#!/usr/bin/env python3
"""
🚀 Trade History Runner - збирає ВСІ історичні trades з pagination
"""

import asyncio
import aiohttp
from config import config

async def get_all_trades_with_pagination(token_pair: str, max_requests: int = 50):
    """
    Отримати ВСІ trades з pagination
    
    Args:
        token_pair: Trading pair address
        max_requests: Максимум запитів (50 = 5000 транзакцій)
    """
    base_url = "https://api.helius.xyz/v0/addresses"
    all_transactions = []
    before = None
    request_count = 0
    
    print(f"🔄 Starting pagination for {token_pair[:8]}... (max requests: {max_requests})")
    
    async with aiohttp.ClientSession() as session:
        while request_count < max_requests:
            url = f"{base_url}/{token_pair}/transactions"
            params = {
                "api-key": config.HELIUS_API_KEY,
                "limit": 100  # Максимальний ліміт за запит
            }
            
            if before:
                params["before"] = before
            
            print(f"📡 Request {request_count + 1}: fetching with before={before[:8] if before else 'None'}...")
            
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        print(f"❌ Helius API error: {resp.status}")
                        break
                    
                    data = await resp.json()
                    if not data:
                        print(f"⚠️ No more data returned")
                        break
                    
                    all_transactions.extend(data)
                    
                    print(f"✅ Got {len(data)} transactions (total: {len(all_transactions)})")
                    
                    # Для pagination - беремо signature останньої транзакції
                    last_sig = data[-1].get("signature")
                    if not last_sig:
                        print(f"⚠️ No signature in last transaction")
                        break
                    
                    before = last_sig
                    request_count += 1
                    
                    # Якщо 0 транзакцій - значить, більше немає
                    if len(data) == 0:
                        print(f"✅ Reached end of data (got 0 transactions)")
                        break
                    
                    # Затримка 0.25 секунди між запитами
                    print(f"⏳ Waiting 0.25 seconds...")
                    await asyncio.sleep(0.25)
                    
            except Exception as e:
                print(f"❌ Error in request {request_count + 1}: {e}")
                break
    
    print(f"🎉 Pagination complete: {len(all_transactions)} total transactions in {request_count} requests")
    return all_transactions

async def main():
    """Головна функція"""
    # Токен з ID = 9
    token_pair = "8En9ZeLoMwKaHJY68TjMGmqFmoBPSD1xZaQ1VS6dm2R5"
    
    print(f"🚀 Starting Trade History collection for {token_pair[:8]}...")
    
    # Збираємо ВСІ trades (до 50 запитів = 5000 транзакцій)
    all_transactions = await get_all_trades_with_pagination(token_pair, max_requests=50)
    
    print(f"📊 Final result: {len(all_transactions)} transactions collected")
    
    # Показуємо перші 3 транзакції для перевірки
    if all_transactions:
        print(f"\n🔍 First 3 transactions:")
        for i, tx in enumerate(all_transactions[:3]):
            print(f"  {i+1}. Signature: {tx.get('signature', 'N/A')[:16]}...")
            print(f"     Timestamp: {tx.get('timestamp', 'N/A')}")
            print(f"     Type: {tx.get('type', 'N/A')}")
            print(f"     Source: {tx.get('source', 'N/A')}")
            print()

if __name__ == "__main__":
    asyncio.run(main())
