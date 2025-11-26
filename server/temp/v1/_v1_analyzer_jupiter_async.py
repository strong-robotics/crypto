#!/usr/bin/env python3
"""
Jupiter API Analyzer - only Jupiter API requests
"""

import asyncio
import aiohttp
import json
import random
from typing import Dict, Any, Optional

class JupiterAnalyzer:
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
        last_exc = None
        for attempt in range(1, 4):
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
    
    async def get_token_info(self, token_address: str) -> Any:
        try:
            url = f"https://lite-api.jup.ag/tokens/v2/search?query={token_address}"
            res = await self._fetch_with_retries(url, headers={"User-Agent": "Mozilla/5.0"})
            if res["ok"]:
                return res["json"]
            return {"error": res.get("error")}
        except Exception as e:
            return {"error": str(e)}
    
    async def check_honeypot(self, token_address: str) -> Dict[str, Any]:
        result = {
            "checked_by": [],
            "buy_possible": None,
            "sell_possible": None,
            "honeypot": None,
            "reasons": []
        }

        try:
            quote_buy_url = f"https://lite-api.jup.ag/swap/v1/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount=100000000&slippageBps=50&restrictIntermediateTokens=true"
            quote_sell_url = f"https://lite-api.jup.ag/swap/v1/quote?inputMint={token_address}&outputMint=So11111111111111111111111111111111111111112&amount=100000000&slippageBps=50&restrictIntermediateTokens=true"
            
            buy_res = await self._fetch_with_retries(quote_buy_url)
            sell_res = await self._fetch_with_retries(quote_sell_url)
            
            # Перевіряємо чи є outAmount
            can_buy = False
            can_sell = False
            
            if buy_res["ok"] and buy_res.get("json"):
                buy_data = buy_res["json"]
                can_buy = bool(buy_data.get('outAmount')) and not buy_data.get('error')
                
            if sell_res["ok"] and sell_res.get("json"):
                sell_data = sell_res["json"]
                can_sell = bool(sell_data.get('outAmount')) and not sell_data.get('error')
            
            if buy_res["ok"] or sell_res["ok"]:
                result["checked_by"] = ["jupiter_quote_api"]
                result["buy_possible"] = can_buy
                result["sell_possible"] = can_sell
                result["honeypot"] = not can_sell
                
                if can_buy and can_sell:
                    result["reasons"].append("Jupiter: can BUY and SELL - NOT honeypot")
                elif can_buy and not can_sell:
                    result["reasons"].append("Jupiter: can BUY but CANNOT SELL - HONEYPOT!")
                else:
                    result["reasons"].append("Jupiter: check liquidity")
                
                return result
                
        except Exception as e:
            result["reasons"].append(f"Jupiter API error: {str(e)}")

        if not result["checked_by"]:
            result["checked_by"] = ["failed"]
            result["honeypot"] = None

        return result
    
    def extract_dev_address(self, jupiter_data: Any) -> Optional[str]:
        try:
            if isinstance(jupiter_data, list) and jupiter_data:
                return jupiter_data[0].get("dev")
            return None
        except Exception:
            return None

jupiter_instance: Optional[JupiterAnalyzer] = None

async def get_jupiter_analyzer() -> JupiterAnalyzer:
    global jupiter_instance
    if jupiter_instance is None:
        jupiter_instance = JupiterAnalyzer(debug=False)
    return jupiter_instance
