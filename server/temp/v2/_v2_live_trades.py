#!/usr/bin/env python3
"""
🚀 Helius Trades Simple - простий модуль для отримання trades
"""

import aiohttp
import asyncio
from typing import List, Dict, Optional
from config import config

class HeliusTradesReporter:
    """Простий репортер для отримання trades з Helius API"""
    
    def __init__(self, helius_api_key: str, db_path: str, debug: bool = False):
        self.helius_api_key = helius_api_key
        self.db_path = db_path
        self.debug = debug
        self.session = None
        self.base_url = config.HELIUS_API_BASE
    
    async def ensure_session(self):
        """Створити aiohttp сесію якщо потрібно"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def get_trades(self, token_pair: str) -> List[Dict]:
        """Отримати trades для trading pair"""
        try:
            await self.ensure_session()
            
            url = f"{self.base_url}/v0/addresses/{token_pair}/transactions"
            params = {
                "api-key": self.helius_api_key,
                "limit": 50
            }
            
            if self.debug:
                print(f"🔍 Fetching trades for pair {token_pair[:8]}...")
            
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    if self.debug:
                        print(f"❌ Helius API error: {resp.status}")
                    return []
                
                data = await resp.json()
                if not data:
                    if self.debug:
                        print(f"⚠️ No data returned for {token_pair[:8]}...")
                    return []
                
                if self.debug:
                    print(f"✅ Got {len(data)} raw transactions for {token_pair[:8]}...")
                
                return data
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting trades: {e}")
            return []
    
    async def close(self):
        """Закрити aiohttp сесію"""
        if self.session and not self.session.closed:
            await self.session.close()

# Прості функції для використання в main.py
async def fetch_trades_for_single_token(token_pair: str, debug: bool = False) -> Dict:
    """Отримати trades для одного токена"""
    try:
        reporter = HeliusTradesReporter(config.HELIUS_API_KEY, "db/tokens.db", debug=debug)
        try:
            trades = await reporter.get_trades(token_pair)
            return {
                "success": True,
                "message": f"Got {len(trades)} trades for {token_pair[:8]}...",
                "trades_count": len(trades)
            }
        finally:
            await reporter.close()
    except Exception as e:
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

async def fetch_all_historical_trades(debug: bool = False) -> Dict:
    """Отримати trades для всіх токенів"""
    return {
        "success": True,
        "message": "Use run_trade_history.py script for full history collection",
        "trades_count": 0
    }
