#!/usr/bin/env python3

import asyncio
import aiohttp
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

class JupiterScanner:
    """Scanner for Jupiter DEX new tokens"""
    
    def __init__(self, debug: bool = False):
        """Initialize scanner with optional debug mode"""
        self.api_url = "https://lite-api.jup.ag/tokens/v2/recent"
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        self.last_scan_time: Optional[datetime] = None
    
    async def ensure_session(self) -> None:
        """Ensure aiohttp session is created and active"""
        if not self.session or self.session.closed:
            if self.debug:
                print("📡 Creating new aiohttp session...")
            self.session = aiohttp.ClientSession()
    
    async def get_tokens(self, limit: int = 20) -> Dict[str, Any]:
        """Get recent tokens from Jupiter API"""
        try:
            await self.ensure_session()
            
            if self.debug:
                print(f"🔍 Fetching {limit} recent tokens from Jupiter...")
            
            async with self.session.get(self.api_url, timeout=10) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return {
                        "success": False,
                        "error": f"API returned status {response.status}: {error_text}"
                    }
                
                data = await response.json()
                tokens = data[:limit]
                
                self.last_scan_time = datetime.now()
                
                # Зберігаємо повні дані
                save_result = await self.save_tokens(tokens)
                if self.debug and save_result["success"]:
                    print(f"💾 Saved {save_result['new_added']} new tokens")
                
                # Форматуємо дані для відображення
                formatted_tokens = []
                for token in tokens:
                    formatted_tokens.append({
                        "id": token.get("id", ""),
                        "name": token.get("name", "Unknown"),
                        "mcap": token.get("mcap", 0),  # Ринкова капіталізація
                        "symbol": token.get("symbol", ""),  # Додатково символ токена
                        "price": token.get("usdPrice", 0)  # Додатково ціна
                    })
                
                return {
                    "success": True,
                    "tokens": formatted_tokens,
                    "total_found": len(formatted_tokens),
                    "scan_time": self.last_scan_time.isoformat()
                }
                
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "Request timeout"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def save_tokens(self, new_tokens: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Save new tokens to local storage and return stats"""
        try:
            try:
                with open("/Users/yevhenvasylenko/Documents/Projects/Crypto/App/server/db/tokens.json", 'r', encoding='utf-8') as f:
                    existing_tokens = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                existing_tokens = []
            
            existing_ids = {token['id'] for token in existing_tokens}
            unique_new = [token for token in new_tokens if token['id'] not in existing_ids]
            
            if unique_new:
                all_tokens = existing_tokens + unique_new
                with open("/Users/yevhenvasylenko/Documents/Projects/Crypto/App/server/db/tokens.json", 'w', encoding='utf-8') as f:
                    json.dump(all_tokens, f, indent=2, ensure_ascii=False)
            
            return {
                "success": True,
                "total_existing": len(existing_tokens),
                "new_added": len(unique_new)
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to save tokens: {str(e)}"
            }
    
    async def close(self) -> None:
        """Close the aiohttp session"""
        if self.session and not self.session.closed:
            if self.debug:
                print("🔌 Closing aiohttp session...")
            await self.session.close()
            
    async def __aenter__(self):
        """Async context manager entry"""
        await self.ensure_session()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()

async def main():
    """Test function"""
    async with JupiterScanner(debug=True) as scanner:
        result = await scanner.get_tokens(20)
        if result["success"]:
            save_result = await scanner.save_tokens(result["tokens"])
            print(f"Save result: {save_result}")
        else:
            print(f"Error: {result['error']}")

if __name__ == "__main__":
    asyncio.run(main())