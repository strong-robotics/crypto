#!/usr/bin/env python3
"""
Solana RPC Analyzer - тільки запити до Solana RPC
"""

import asyncio
import aiohttp
import json
import random
from typing import Dict, Any, Optional

class SolanaRPCAnalyzer:
    def __init__(self, debug: bool = False):
        self.solana_rpc_url = "https://api.mainnet-beta.solana.com"
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        
    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        if self.session:
            await self.session.close()
    
    async def _post_rpc_with_retries(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """RPC POST з ретраями"""
        last_exc = None
        for attempt in range(1, 4):  # 3 спроби
            try:
                async with self.session.post(self.solana_rpc_url, json=payload, timeout=10) as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
                    if 200 <= status < 300:
                        return {"ok": True, "status": status, "json": data}
                    else:
                        return {"ok": False, "status": status, "json": data, "error": f"HTTP {status}"}
            except Exception as e:
                last_exc = e
                backoff = 0.4 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)
        return {"ok": False, "error": str(last_exc)}
    
    async def get_token_supply(self, token_address: str) -> Any:
        """Отримати supply токена"""
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [token_address]}
            res = await self._post_rpc_with_retries(payload)
            return res["json"].get("result") if res["ok"] and res.get("json") else None
        except Exception as e:
            return {"error": str(e)}
    
    async def get_token_metadata(self, token_address: str) -> Any:
        """Отримати метадані токена"""
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [token_address, {"encoding": "jsonParsed"}]}
            res = await self._post_rpc_with_retries(payload)
            return res["json"].get("result") if res["ok"] and res.get("json") else None
        except Exception as e:
            return {"error": str(e)}
    
    async def get_basic_data(self, token_address: str) -> Dict[str, Any]:
        """Отримати базові дані токена (supply + metadata)"""
        rpc_data: Dict[str, Any] = {}
        
        # 1. getTokenSupply
        token_supply = await self.get_token_supply(token_address)
        rpc_data["token_supply"] = token_supply

        # 2. getAccountInfo (metadata)
        token_metadata = await self.get_token_metadata(token_address)
        rpc_data["token_metadata"] = token_metadata
        
        return rpc_data

# Глобальний екземпляр
solana_rpc_instance: Optional[SolanaRPCAnalyzer] = None

async def get_solana_rpc_analyzer() -> SolanaRPCAnalyzer:
    global solana_rpc_instance
    if solana_rpc_instance is None:
        solana_rpc_instance = SolanaRPCAnalyzer(debug=False)
    return solana_rpc_instance
