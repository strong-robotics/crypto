#!/usr/bin/env python3
"""
DexScreener API Analyzer - тільки запити до DexScreener
"""

import asyncio
import aiohttp
import json
import random
from typing import Dict, Any, Optional

class DexScreenerAnalyzer:
    def __init__(self, debug: bool = False):
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        
    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        if self.session:
            await self.session.close()
    
    async def _fetch_with_retries(self, url: str, **kwargs) -> Dict[str, Any]:
        """HTTP запит з ретраями"""
        last_exc = None
        for attempt in range(1, 4):  # 3 спроби
            try:
                async with self.session.get(url, **kwargs) as resp:
                    status = resp.status
                    text = await resp.text()
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
                    if 200 <= status < 300:
                        return {"ok": True, "status": status, "json": parsed}
                    else:
                        return {"ok": False, "status": status, "json": parsed, "error": f"HTTP {status}"}
            except Exception as e:
                last_exc = e
                backoff = 0.4 * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
                await asyncio.sleep(backoff)
        return {"ok": False, "error": str(last_exc)}
    
    async def get_token_data(self, token_address: str) -> Any:
        """Отримати дані про токен з DexScreener"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/search/?q={token_address}"
            res = await self._fetch_with_retries(url)
            if res["ok"]:
                return res["json"]
            return {"error": res.get("error")}
        except Exception as e:
            return {"error": str(e)}
    
    def extract_pair_address(self, dexscreener_data: Any) -> Optional[str]:
        """Витягти pair address з DexScreener даних"""
        try:
            if isinstance(dexscreener_data, dict):
                pairs = dexscreener_data.get("pairs") or []
                if isinstance(pairs, list) and pairs:
                    return pairs[0].get("pairAddress")
            return None
        except Exception:
            return None

# Глобальний екземпляр
dexscreener_instance: Optional[DexScreenerAnalyzer] = None

async def get_dexscreener_analyzer() -> DexScreenerAnalyzer:
    global dexscreener_instance
    if dexscreener_instance is None:
        dexscreener_instance = DexScreenerAnalyzer(debug=False)
    return dexscreener_instance
