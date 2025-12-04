#!/usr/bin/env python3

import asyncio
import aiohttp
from typing import Optional
from datetime import datetime
from config import config

class SolPriceMonitor:
    def __init__(self, update_interval: int = 1, debug: bool = False):
        self.update_interval = update_interval
        self.debug = debug
        self.session: Optional[aiohttp.ClientSession] = None
        self.current_price: float = 0.0
        self.last_update: Optional[datetime] = None
        self.is_running = False
        self.monitor_task: Optional[asyncio.Task] = None
        
    async def ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        self.is_running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None
        
        if self.session:
            await self.session.close()
            self.session = None
    
    async def _fetch_sol_price(self) -> float:
        """Fetch SOL/USD price using public DexScreener API.
        Returns last known price if API fails.
        """
        await self.ensure_session()
        # 1) DexScreener public API
        try:
            ds_url = (
                "https://api.dexscreener.com/tokens/v1/solana/"
                "So11111111111111111111111111111111111111112"
            )
            async with self.session.get(ds_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if self.debug:
                    # print(f"📡 SOL price API response: HTTP {resp.status}")
                    pass
                
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and data:
                        price = float(data[0].get("priceUsd", 0) or 0)
                        if price > 0:
                            return price
                elif resp.status == 429:
                    # print(f"⚠️ SOL price API: Rate limit hit (429)")
                    pass
                else:
                    # print(f"⚠️ SOL price API: HTTP {resp.status} error")
                    pass
        except Exception as e:
            # print(f"❌ DexScreener SOL price error: {e}")
            pass
        
        # Если API не работает, возвращаем последнее известное значение
        return self.current_price if self.current_price > 0 else 0.0
    
    async def _monitor_loop(self):
        while self.is_running:
            try:
                price = await self._fetch_sol_price()
                
                # Обновляем цену только если получили новое валидное значение
                if price > 0 and price != self.current_price:
                    self.current_price = price
                    self.last_update = datetime.now()
                    if self.debug:
                        # print(f"💰 SOL price updated: ${price:.2f}")
                        pass
            
            except Exception as e:
                if self.debug:
                    print(f"❌ Monitor loop error: {e}")
            
            await asyncio.sleep(self.update_interval)
    
    async def start(self):
        if self.is_running:
            return {"success": False, "message": "SOL price monitor already running"}
        
        price = await self._fetch_sol_price()
        if price > 0:
            self.current_price = price
            self.last_update = datetime.now()
        
        self.is_running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        
        # if self.debug:
            # print(f"✅ SOL price monitor started (update every {self.update_interval}s)")
        
        return {"success": True, "message": "SOL price monitor started", "initial_price": self.current_price}
    
    async def stop(self):
        if not self.is_running:
            return {"success": False, "message": "SOL price monitor not running"}
        
        await self.close()
        
        # if self.debug:
            # print("🛑 SOL price monitor stopped")
        
        return {"success": True, "message": "SOL price monitor stopped"}
    
    def get_price(self) -> float:
        return self.current_price
    
    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "current_price": self.current_price,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "update_interval": self.update_interval
        }

_sol_price_monitor_instance: Optional[SolPriceMonitor] = None

async def get_sol_price_monitor(update_interval: int = 1, debug: bool = False) -> SolPriceMonitor:
    global _sol_price_monitor_instance
    if _sol_price_monitor_instance is None:
        _sol_price_monitor_instance = SolPriceMonitor(update_interval=update_interval, debug=debug)
        await _sol_price_monitor_instance.start()
    return _sol_price_monitor_instance

def get_current_sol_price() -> float:
    """Получить текущую цену SOL. Возвращает последнее известное значение или 0.0"""
    global _sol_price_monitor_instance
    fallback = float(getattr(config, "SOL_PRICE_FALLBACK", 0.0) or 0.0)
    if _sol_price_monitor_instance:
        price = _sol_price_monitor_instance.get_price()
        return price if price > 0 else fallback
    return fallback
