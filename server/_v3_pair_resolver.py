#!/usr/bin/env python3

import aiohttp
import asyncio
from typing import Optional, Dict, Any, List, Set
from _v3_db_pool import get_db_pool
from config import config

# Global RPM limiter (DexScreener: 300 req/min)
class _RpmLimiter:
    def __init__(self, max_rpm: int = 300, window_sec: float = 60.0) -> None:
        self.max_rpm = int(max_rpm)
        self.window = float(window_sec)
        self._hits: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            # discard old hits
            cutoff = now - self.window
            self._hits = [t for t in self._hits if t >= cutoff]
            if len(self._hits) < self.max_rpm:
                self._hits.append(now)
                return
            # need to wait until earliest hit leaves window
            wait = self.window - (now - self._hits[0]) + 0.01
            await asyncio.sleep(max(0.01, wait))
            # recurse once to re-check after sleep
            await self.acquire()


_limiter = _RpmLimiter(max_rpm=getattr(config, 'DEXSCREENER_MAX_RPM', 240), window_sec=60.0)

# Caches to avoid repeated lookups within process lifetime
_resolved_mints: Set[str] = set()
_miss_until: Dict[str, float] = {}  # mint -> unix monotonic when next allowed


async def _fetch_json(url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    try:
        # rate-limit before external call
        await _limiter.acquire()
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
    except Exception:
        return None


async def resolve_pair_via_dexscreener(mint: str) -> Optional[str]:
    """Resolve pool address (pair) for a given mint using DexScreener token-pairs API.

    Endpoint: https://api.dexscreener.com/token-pairs/v1/solana/<mint>
    RPM limit: 300 — enforced by module-level limiter.
    """
    # Prefer token-pairs API (explicit by chain + mint)
    url = f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}"
    data = await _fetch_json(url)
    if not data:
        # Fallback to search if token-pairs is unavailable
        base = getattr(config, 'DEXSCREENER_API', 'https://api.dexscreener.com/latest/dex/search/')
        if not base.endswith('/'):
            base += '/'
        url = f"{base}{mint}"
        data = await _fetch_json(url)
        if not data:
            return None
        pairs = data.get('pairs') if isinstance(data, dict) else (data if isinstance(data, list) else None)
    else:
        # token-pairs returns {'pairs': [...]}
        pairs = data.get('pairs') if isinstance(data, dict) else None
    if not pairs:
        return None

    best_pair = None
    best_liq = -1.0
    for p in pairs:
        try:
            # token-pairs already filtered by chain, but keep guards for search fallback
            if (p.get('chainId') or '').lower() not in ('solana', ''):
                continue
            base_token = (p.get('baseToken') or {}).get('address') or (p.get('baseTokenAddress') or None)
            quote_token = (p.get('quoteToken') or {}).get('address') or (p.get('quoteTokenAddress') or None)
            if base_token != mint and quote_token != mint:
                continue
            pair_addr = p.get('pairAddress') or p.get('address')
            if not pair_addr:
                continue
            liq = float((p.get('liquidity', {}) or {}).get('usd', 0) or p.get('liquidityUsd') or 0)
            if liq > best_liq:
                best_liq = liq
                best_pair = pair_addr
        except Exception:
            continue
    return best_pair


async def resolve_and_update_pair(token_id: int, token_address: str) -> Optional[str]:
    """Resolve pair via DexScreener and update DB if valid and different from mint.

    Returns the pair if updated, else None.
    """
    # If we already resolved in this process, skip network
    if token_address in _resolved_mints:
        return None
    # Skip if still in miss TTL window
    now = asyncio.get_running_loop().time()
    ttl = float(getattr(config, 'DEXSCREENER_RETRY_TTL_SEC', 600))
    until = _miss_until.get(token_address, 0.0)
    if until and now < until:
        return None

    # Quick DB check: if pair already exists, cache and return
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchrow("SELECT token_pair FROM tokens WHERE id = $1", token_id)
        if current and current['token_pair'] and current['token_pair'] != token_address:
            _resolved_mints.add(token_address)
            return current['token_pair']

    pair = await resolve_pair_via_dexscreener(token_address)
    if not pair or pair == token_address:
        # Remember miss window to avoid hammering
        _miss_until[token_address] = now + ttl
        return None
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval("SELECT token_pair FROM tokens WHERE id = $1", token_id)
        if current == pair:
            _resolved_mints.add(token_address)
            return pair
        await conn.execute(
            "UPDATE tokens SET token_pair = $2, token_updated_at = CURRENT_TIMESTAMP WHERE id = $1",
            token_id,
            pair,
        )
    _resolved_mints.add(token_address)
    return pair
