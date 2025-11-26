#!/usr/bin/env python3
"""
Check all database data for a specific token
"""

import asyncio
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_config import POSTGRES_CONFIG
import asyncpg


async def check_token_full_data(token_id: int):
    """Check all database data for token ID"""
    
    config = POSTGRES_CONFIG.copy()
    config['database'] = 'crypto_db'
    config.pop('min_size', None)
    config.pop('max_size', None)
    
    try:
        conn = await asyncpg.connect(**config)
        
        print("=" * 80)
        print(f"🔍 ПОВНА ПЕРЕВІРКА ТОКЕНУ ID {token_id}")
        print("=" * 80)
        print()
        
        # 1. Token basic info
        print("1️⃣ ОСНОВНА ІНФОРМАЦІЯ ПРО ТОКЕН:")
        print("-" * 80)
        token = await conn.fetchrow("""
            SELECT 
                id, token_address, token_pair, name, symbol,
                usd_price, mcap, liquidity, holder_count,
                wallet_id, pattern_code, pattern,
                created_at, token_updated_at
            FROM tokens
            WHERE id = $1
        """, token_id)
        
        if not token:
            print(f"❌ Токен {token_id} не знайдено в БД!")
            await conn.close()
            return
        
        print(f"   ID: {token['id']}")
        print(f"   Address: {token['token_address']}")
        print(f"   Pair: {token['token_pair']}")
        print(f"   Name: {token['name']}")
        print(f"   Symbol: {token['symbol']}")
        print(f"   Price USD: {token['usd_price']}")
        print(f"   Market Cap: {token['mcap']}")
        print(f"   Liquidity: {token['liquidity']}")
        print(f"   Holders: {token['holder_count']}")
        print(f"   Wallet ID: {token['wallet_id']}")
        print(f"   Pattern Code: {token['pattern_code']}")
        print(f"   Pattern: {token['pattern']}")
        print(f"   Created: {token['created_at']}")
        print(f"   Updated: {token['token_updated_at']}")
        print()
        
        # 2. Wallet history (buy/sell operations)
        print("2️⃣ ІСТОРІЯ ПОКУПОК/ПРОДАЖ (wallet_history):")
        print("-" * 80)
        wallet_history = await conn.fetch("""
            SELECT 
                id, wallet_id, token_id,
                entry_amount_usd, entry_token_amount, entry_price_usd, entry_iteration,
                entry_signature, entry_slippage_bps, entry_actual_amount_usd,
                exit_amount_usd, exit_token_amount, exit_price_usd, exit_iteration,
                exit_signature, exit_slippage_bps, exit_actual_amount_usd,
                profit_usd, profit_pct, outcome, reason,
                created_at, updated_at
            FROM wallet_history
            WHERE token_id = $1
            ORDER BY id DESC
        """, token_id)
        
        if wallet_history:
            for i, wh in enumerate(wallet_history, 1):
                print(f"   Запис #{i} (ID: {wh['id']}):")
                print(f"      Wallet ID: {wh['wallet_id']}")
                print(f"      ENTRY:")
                print(f"         Amount USD: {wh['entry_amount_usd']}")
                print(f"         Token Amount: {wh['entry_token_amount']}")
                print(f"         Price USD: {wh['entry_price_usd']}")
                print(f"         Iteration: {wh['entry_iteration']}")
                print(f"         Signature: {wh['entry_signature']}")
                print(f"         Slippage: {wh['entry_slippage_bps']} bps")
                print(f"         Actual Amount: {wh['entry_actual_amount_usd']}")
                print(f"      EXIT:")
                print(f"         Amount USD: {wh['exit_amount_usd']}")
                print(f"         Token Amount: {wh['exit_token_amount']}")
                print(f"         Price USD: {wh['exit_price_usd']}")
                print(f"         Iteration: {wh['exit_iteration']}")
                print(f"         Signature: {wh['exit_signature']}")
                print(f"         Slippage: {wh['exit_slippage_bps']} bps")
                print(f"         Actual Amount: {wh['exit_actual_amount_usd']}")
                print(f"      RESULT:")
                print(f"         Profit USD: {wh['profit_usd']}")
                print(f"         Profit %: {wh['profit_pct']}")
                print(f"         Outcome: {wh['outcome']}")
                print(f"         Reason: {wh['reason']}")
                print(f"      Created: {wh['created_at']}")
                print(f"      Updated: {wh['updated_at']}")
                print()
        else:
            print("   ⚠️  Немає записів в wallet_history")
            print()
        
        # 3. Trade attempts (buy/sell attempts)
        print("3️⃣ СПРОБИ ТОРГІВЛІ (trade_attempts):")
        print("-" * 80)
        trade_attempts = await conn.fetch("""
            SELECT 
                id, token_id, wallet_id, action, status, message, details, created_at
            FROM trade_attempts
            WHERE token_id = $1
            ORDER BY created_at DESC
            LIMIT 20
        """, token_id)
        
        if trade_attempts:
            for i, ta in enumerate(trade_attempts, 1):
                print(f"   Спроба #{i} (ID: {ta['id']}):")
                print(f"      Action: {ta['action']}")
                print(f"      Status: {ta['status']}")
                print(f"      Wallet ID: {ta['wallet_id']}")
                print(f"      Message: {ta['message']}")
                print(f"      Details: {ta['details']}")
                print(f"      Created: {ta['created_at']}")
                print()
        else:
            print("   ⚠️  Немає записів в trade_attempts")
            print()
        
        # 4. Recent trades
        print("4️⃣ ОСТАННІ ТРАНЗАКЦІЇ (trades):")
        print("-" * 80)
        recent_trades = await conn.fetch("""
            SELECT 
                id, signature, timestamp, readable_time, direction,
                amount_tokens, amount_sol, amount_usd, token_price_usd, slot, created_at
            FROM trades
            WHERE token_id = $1
            ORDER BY timestamp DESC
            LIMIT 10
        """, token_id)
        
        if recent_trades:
            print(f"   Всього транзакцій: {await conn.fetchval('SELECT COUNT(*) FROM trades WHERE token_id = $1', token_id)}")
            print()
            for i, trade in enumerate(recent_trades, 1):
                print(f"   Транзакція #{i}:")
                print(f"      Signature: {trade['signature']}")
                print(f"      Time: {trade['readable_time']} (timestamp: {trade['timestamp']})")
                print(f"      Direction: {trade['direction']}")
                print(f"      Amount Tokens: {trade['amount_tokens']}")
                print(f"      Amount SOL: {trade['amount_sol']}")
                print(f"      Amount USD: {trade['amount_usd']}")
                print(f"      Token Price USD: {trade['token_price_usd']}")
                print(f"      Slot: {trade['slot']}")
                print()
        else:
            print("   ⚠️  Немає транзакцій")
            print()
        
        # 5. Metrics count
        print("5️⃣ МЕТРИКИ (token_metrics_seconds):")
        print("-" * 80)
        metrics_count = await conn.fetchval("""
            SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id = $1
        """, token_id)
        print(f"   Всього метрик: {metrics_count}")
        
        if metrics_count > 0:
            latest_metric = await conn.fetchrow("""
                SELECT ts, usd_price, mcap, liquidity, fdv
                FROM token_metrics_seconds
                WHERE token_id = $1
                ORDER BY ts DESC
                LIMIT 1
            """, token_id)
            if latest_metric:
                print(f"   Остання метрика:")
                print(f"      Timestamp: {latest_metric['ts']}")
                print(f"      Price USD: {latest_metric['usd_price']}")
                print(f"      Market Cap: {latest_metric['mcap']}")
                print(f"      Liquidity: {latest_metric['liquidity']}")
                print()
        
        # 6. Wallet info (if wallet_id exists)
        if token['wallet_id']:
            print("6️⃣ ІНФОРМАЦІЯ ПРО ГАМАНЕЦЬ:")
            print("-" * 80)
            wallet = await conn.fetchrow("""
                SELECT 
                    id, name, initial_deposit_usd, cash_usd,
                    entry_amount_usd, active_token_id, total_profit_usd,
                    created_at, updated_at
                FROM wallets
                WHERE id = $1
            """, token['wallet_id'])
            
            if wallet:
                print(f"   Wallet ID: {wallet['id']}")
                print(f"   Name: {wallet['name']}")
                print(f"   Initial Deposit: ${wallet['initial_deposit_usd']}")
                print(f"   Cash USD: ${wallet['cash_usd']}")
                print(f"   Entry Amount USD: ${wallet['entry_amount_usd']}")
                print(f"   Active Token ID: {wallet['active_token_id']}")
                print(f"   Total Profit USD: ${wallet['total_profit_usd']}")
                print(f"   Created: {wallet['created_at']}")
                print(f"   Updated: {wallet['updated_at']}")
                print()
            else:
                print(f"   ⚠️  Гаманець {token['wallet_id']} не знайдено")
                print()
        
        print("=" * 80)
        print("✅ ПЕРЕВІРКА ЗАВЕРШЕНА")
        print("=" * 80)
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Помилка: {e}")
        import traceback
        traceback.print_exc()


async def main():
    if len(sys.argv) < 2:
        print("Використання: python check_token_full_data.py <token_id>")
        sys.exit(1)
    
    token_id = int(sys.argv[1])
    await check_token_full_data(token_id)


if __name__ == "__main__":
    asyncio.run(main())

