#!/usr/bin/env python3

import asyncio
import random
from typing import Optional, Dict, Any, List

from config import config
from _v3_analyzer_jupiter import get_analyzer
from _v3_new_tokens import get_scanner
from _v3_slot_synchronizer import sync_all_slots


class JupiterScheduler:

    def __init__(self) -> None:
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._tick = 0
        self._cleaner_task: Optional[asyncio.Task] = None
        self._last_request_ts: float = 0.0
        self._min_gap: float = float(getattr(config, 'JUPITER_MIN_INTERVAL_SEC', 1.15))
        self._jitter_min: float = float(getattr(config, 'JUPITER_JITTER_MIN_SEC', 0.12))
        self._jitter_max: float = float(getattr(config, 'JUPITER_JITTER_MAX_SEC', 0.20))
        self._backoff_until: float = 0.0
        self._yield_every: int = int(getattr(config, 'JUPITER_YIELD_EVERY', 10))
        self._skip_scanner: bool = False
        self._scanner_paused_by_wallets: bool = False
        self._manual_skip_scanner: bool = False

    async def _acquire_slot(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._backoff_until:
            await asyncio.sleep(self._backoff_until - now)
            now = loop.time()
        wait = (self._last_request_ts + self._min_gap) - now
        if wait > 0:
            await asyncio.sleep(wait)
        jitter = random.uniform(self._jitter_min, self._jitter_max)
        if jitter > 0:
            await asyncio.sleep(jitter)

    async def _analyzer_tick(self) -> Dict[str, Any]:
        try:
            analyzer = await get_analyzer()
            await self._acquire_slot()
            tokens = await analyzer.get_tokens_batch()
            if not tokens:
                return {"success": True, "updated": 0, "tokens": 0}
            
            # print(f"📡 Jupiter API request: analyzer batch ({len(tokens)} tokens)")
            data = await analyzer.get_jupiter_data(tokens)
            self._last_request_ts = asyncio.get_running_loop().time()

            if isinstance(data, dict) and data.get("error"):
                if str(data.get("error")).strip().endswith("429"):
                    now = asyncio.get_running_loop().time()
                    retry_after = data.get("retry_after")
                    if isinstance(retry_after, (int, float)) and retry_after > 0:
                        self._backoff_until = max(self._backoff_until, now + float(retry_after))
                        # print(f"⚠️ Jupiter API 429: Rate limit, retry after {retry_after}s")
                    else:
                        self._backoff_until = max(self._backoff_until, now + float(getattr(config, 'JUPITER_BACKOFF_SEC', 2.0)))
                        # print(f"⚠️ Jupiter API 429: Rate limit, backoff {getattr(config, 'JUPITER_BACKOFF_SEC', 2.0)}s")
                return {"success": False, "error": data.get("error"), "tokens": len(tokens)}

            updated = 0

            for idx, item in enumerate(data):
                addr = item.get("id")
                for t in tokens:
                    if t["token_address"] == addr:
                        ok = await analyzer.save_token_data(t["token_id"], item)
                        if ok:
                            updated += 1
                        break
                if self._yield_every > 0 and (idx + 1) % self._yield_every == 0:
                    await asyncio.sleep(0)
            return {"success": True, "updated": updated, "tokens": len(tokens)}
            
        except Exception as e:
            return {"success": False, "error": str(e), "tokens": 0}

    async def _scanner_tick(self) -> Dict[str, Any]:
        scanner = await get_scanner()
        await scanner.ensure_session()
        await self._acquire_slot()
        # print(f"📡 Jupiter API request: scanner (new tokens)")
        res = await scanner.get_tokens_from_api(limit=20)
        self._last_request_ts = asyncio.get_running_loop().time()
        if not res or not res.get("success"):
            err = res.get("error") if isinstance(res, dict) else "unknown"
            if isinstance(err, str) and err.strip().endswith("429"):
                backoff = float(getattr(config, 'JUPITER_BACKOFF_SEC', 1.8))
                self._backoff_until = max(self._backoff_until, asyncio.get_running_loop().time() + backoff)
                # print(f"⚠️ Jupiter API 429: Rate limit, backoff {backoff}s")
            return {"success": False, "error": res.get("error") if isinstance(res, dict) else "unknown"}
        saved = int(res.get("saved_count", 0))
        new_count = int(res.get("new_count", 0))
        updated = max(0, saved - new_count)
        return {"success": True, "new": new_count, "updated": updated}

    async def _loop(self) -> None:
        if getattr(config, 'CLEANER_ENABLED', True) and not self._cleaner_task:
            self._cleaner_task = asyncio.create_task(self._cleaner_loop())
        while self.is_running:
            self._tick += 1
            
            # Scanner tick interval from config (default: every 6 ticks)
            scanner_interval = int(getattr(config, 'JUPITER_SCANNER_TICK_INTERVAL', 6))
            on_scanner_tick = (self._tick % scanner_interval == 0)
            if on_scanner_tick and not self._skip_scanner:
                try:
                    await self._scanner_tick()
                except Exception:
                    pass
            else:
                # When scanner is paused, analyzer still runs every tick (including scanner slots)
                try:
                    await self._analyzer_tick()
                except Exception:
                    pass
            
            # Slot sync tick interval from config (default: every 10 ticks)
            slot_sync_interval = int(getattr(config, 'JUPITER_SLOT_SYNC_TICK_INTERVAL', 10))
            if self._tick % slot_sync_interval == 0:
                try:
                    await sync_all_slots(limit=50)
                except Exception:
                    pass
            
            await asyncio.sleep(max(0.0, float(getattr(config, 'JUPITER_ANALYZER_INTERVAL', 1))))

            # Auto-skip logic: pause scanner when any token is bound to a wallet
            if getattr(config, 'PAUSE_SCANNER_WHEN_WALLET_BOUND', False):
                try:
                    await self._update_scanner_skip_from_wallet_bindings()
                except Exception:
                    pass

    async def _cleaner_loop(self) -> None:
        interval = float(getattr(config, 'CLEANER_INTERVAL_SEC', 15))
        older = int(getattr(config, 'CLEANER_OLDER_SEC', 15))
        limit = int(getattr(config, 'CLEANER_BATCH_LIMIT', 200))
        from _v3_cleaner import run_cleanup  # local import
        while self.is_running:
            try:
                # Respect keep-alive for tokens with a valid pair: age and iterations
                res = await run_cleanup(
                    dry_run=False,
                    older_than_sec=older,
                    limit=limit,
                    no_entry_age_sec=int(getattr(config, 'CLEANER_NO_ENTRY_AGE_SEC', 7200)),
                    no_entry_iters=int(getattr(config, 'CLEANER_NO_ENTRY_ITERS', 7200)),
                )
                # Cleaner runs silently
                pass
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def start(self) -> Dict[str, Any]:
        if self.is_running:
            return {"success": False, "message": "Scheduler already running"}
        self.is_running = True
        self._tick = 0
        self._backoff_until = 0.0
        self._last_request_ts = 0.0
        # preserve manual skip state across restarts
        self._scanner_paused_by_wallets = False
        self._apply_skip_state()
        self._task = asyncio.create_task(self._loop())
        return {"success": True, "message": "Scheduler started"}

    async def stop(self) -> Dict[str, Any]:
        if not self.is_running:
            return {"success": False, "message": "Scheduler not running"}
        self.is_running = False
        if self._cleaner_task:
            self._cleaner_task.cancel()
            try:
                await self._cleaner_task
            except asyncio.CancelledError:
                pass
            self._cleaner_task = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        return {"success": True, "message": "Scheduler stopped"}

    def status(self) -> Dict[str, Any]:
        return {"is_running": self.is_running, "tick": self._tick, "cleaner": bool(self._cleaner_task)}

    async def _update_scanner_skip_from_wallet_bindings(self) -> None:
        """Pause/resume scanner based on wallet bindings."""
        from _v3_db_pool import get_db_pool
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            bound = await conn.fetchval("SELECT COUNT(*) FROM tokens WHERE wallet_id IS NOT NULL") or 0
        should_skip = bound > 0
        if should_skip != self._scanner_paused_by_wallets:
            self._scanner_paused_by_wallets = should_skip
            self._apply_skip_state()
            # state = "paused" if should_skip else "resumed"
            # print(f"[Scheduler] Scanner {state} (wallet bindings: {bound})")

    def _apply_skip_state(self) -> None:
        self._skip_scanner = self._manual_skip_scanner or self._scanner_paused_by_wallets

    def set_manual_scanner_skip(self, skip: bool) -> None:
        self._manual_skip_scanner = skip
        self._apply_skip_state()

    def get_scanner_state(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "scanner_paused": self._skip_scanner,
            "manual_skip": self._manual_skip_scanner,
            "auto_skip": self._scanner_paused_by_wallets,
        }

    def is_scanner_active(self) -> bool:
        return self.is_running and not self._skip_scanner


_scheduler: Optional[JupiterScheduler] = None

async def get_scheduler() -> JupiterScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = JupiterScheduler()
    return _scheduler
