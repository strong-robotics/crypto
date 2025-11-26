#!/usr/bin/env python3
"""Async manager to run forecast loop inside FastAPI server.

Used by server/main.py via start/stop/status endpoints.
"""

import asyncio
from typing import Optional

try:
    # ETA online loop that writes plan_sell_* fields (AI plan only)
    from ai.infer.eta_online import loop_once
except Exception:
    # Fallback to legacy forecast loop
    from ai.infer.forecast_loop import loop_once


class ForecastRunner:
    def __init__(self) -> None:
        self.is_running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._interval: float = 1.0

    async def _loop(self) -> None:
        while self.is_running:
            try:
                await loop_once()
            except Exception as e:
                # keep alive even if single iteration failed
                try:
                    import traceback
                    print("[JUNO] loop_once error; continue")
                    print(traceback.format_exc())
                except Exception:
                    print(f"[JUNO] loop_once error: {e}")
            await asyncio.sleep(self._interval)

    async def start(self) -> dict:
        if self.is_running:
            return {"success": True, "message": "forecast already running"}
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        # Simulation timers removed (real trading only)
        print("🚀 JUNO started (ETA loop, 1 Hz)")
        return {"success": True, "message": "forecast started"}

    async def stop(self) -> dict:
        if not self.is_running:
            return {"success": True, "message": "forecast already stopped"}
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Simulation timers removed (real trading only)
        print("🛑 JUNO stopped")
        return {"success": True, "message": "forecast stopped"}

    def get_status(self) -> dict:
        return {"is_running": self.is_running}


_instance: Optional[ForecastRunner] = None


async def get_forecast_runner() -> ForecastRunner:
    global _instance
    if _instance is None:
        _instance = ForecastRunner()
    return _instance
