#!/usr/bin/env python3
"""
Проста міграція: SQLite -> пам'ять -> PostgreSQL
"""

import asyncio
import sqlite3
import asyncpg
from datetime import datetime
from db_config import POSTGRES_CONFIG

async def migrate_data():
    print("🚀 Початок міграції даних...")
    
    # 1. Вигружаємо дані з SQLite в пам'ять
    print("📥 Вигружаємо дані з SQLite...")
    sqlite_conn = sqlite3.connect("db/tokens.db")
    sqlite_conn.row_factory = sqlite3.Row
    
    # Отримуємо токени
    cursor = sqlite_conn.execute("""
        SELECT 
            ti.id, ti.token_address, ti.token_pair, ti.check_jupiter, ti.history_ready,
            t.name, t.symbol, t.icon, t.decimals, t.dev, t.circ_supply, t.total_supply,
            t.token_program, t.holder_count, t.usd_price, t.liquidity, t.fdv, t.mcap,
            t.price_block_id, t.organic_score, t.organic_score_label, ti.created_at
        FROM token_ids ti
        LEFT JOIN tokens t ON ti.id = t.token_id
    """)
    tokens_data = cursor.fetchall()
    print(f"📊 Завантажено {len(tokens_data)} токенів")
    
    # Отримуємо trades
    cursor = sqlite_conn.execute("SELECT * FROM trades")
    trades_data = cursor.fetchall()
    print(f"📈 Завантажено {len(trades_data)} trades")
    
    sqlite_conn.close()
    
    # 2. Підключаємося до PostgreSQL
    print("🔌 Підключаємося до PostgreSQL...")
    postgres_config = POSTGRES_CONFIG.copy()
    postgres_config['database'] = 'crypto_db'
    postgres_config.pop('min_size', None)
    postgres_config.pop('max_size', None)
    
    postgres_conn = await asyncpg.connect(**postgres_config)
    
    try:
        # Очищаємо таблиці
        await postgres_conn.execute('DELETE FROM trades')
        await postgres_conn.execute('DELETE FROM tokens')
        print("🧹 Очищено таблиці")
        
        # 3. Вставляємо токени
        print("📦 Вставляємо токени...")
        for row in tokens_data:
            await postgres_conn.execute("""
                INSERT INTO tokens (
                    id, token_address, token_pair, name, symbol, icon, decimals, dev,
                    circ_supply, total_supply, token_program, holder_count,
                    usd_price, liquidity, fdv, mcap, price_block_id,
                    organic_score, organic_score_label,
                    check_jupiter, history_ready, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
                )
            """, 
                row['id'], row['token_address'], row['token_pair'], 
                row['name'], row['symbol'], row['icon'], row['decimals'], row['dev'],
                row['circ_supply'], row['total_supply'], row['token_program'], row['holder_count'],
                row['usd_price'], row['liquidity'], row['fdv'], row['mcap'], row['price_block_id'],
                row['organic_score'], row['organic_score_label'],
                row['check_jupiter'], bool(row['history_ready']), 
                datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')) if row['created_at'] else None
            )
        
        print(f"✅ Вставлено {len(tokens_data)} токенів")
        
        # 4. Вставляємо trades
        print("📈 Вставляємо trades...")
        for row in trades_data:
            await postgres_conn.execute("""
                INSERT INTO trades (
                    id, token_id, signature, timestamp, readable_time, direction,
                    amount_tokens, amount_sol, amount_usd, token_price_usd, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
                )
            """, 
                row['id'], row['token_id'], row['signature'], row['timestamp'], 
                row['readable_time'], row['direction'], row['amount_tokens'], 
                row['amount_sol'], row['amount_usd'], row['token_price_usd'], 
                datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')) if row['created_at'] else None
            )
        
        print(f"✅ Вставлено {len(trades_data)} trades")
        
        # 5. Валідація
        print("🔍 Валідація...")
        postgres_tokens = await postgres_conn.fetchval("SELECT COUNT(*) FROM tokens")
        postgres_trades = await postgres_conn.fetchval("SELECT COUNT(*) FROM trades")
        
        print(f"📊 PostgreSQL: {postgres_tokens} токенів, {postgres_trades} trades")
        
        if postgres_tokens == len(tokens_data) and postgres_trades == len(trades_data):
            print("✅ Міграція завершена успішно!")
        else:
            print("❌ Помилка валідації!")
            
    finally:
        await postgres_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_data())
