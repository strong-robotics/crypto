#!/usr/bin/env python3
"""
Спрощена міграція даних з SQLite tokens.db в PostgreSQL crypto_db
"""

import asyncio
import sqlite3
import asyncpg
from db_config import POSTGRES_CONFIG

async def migrate():
    print("🚀 Початок спрощеної міграції...")
    
    # Підключаємося до SQLite
    sqlite_conn = sqlite3.connect("db/tokens.db")
    sqlite_conn.row_factory = sqlite3.Row
    
    # Підключаємося до PostgreSQL
    postgres_config = POSTGRES_CONFIG.copy()
    postgres_config['database'] = 'crypto_db'
    postgres_config.pop('min_size', None)
    postgres_config.pop('max_size', None)
    
    postgres_conn = await asyncpg.connect(**postgres_config)
    
    try:
        # Очищаємо таблиці
        await postgres_conn.execute('DELETE FROM trades')
        await postgres_conn.execute('DELETE FROM tokens')
        
        # Мігруємо токени
        print("📦 Мігруємо токени...")
        cursor = sqlite_conn.execute("""
            SELECT 
                ti.id, ti.token_address, ti.token_pair, ti.check_jupiter, ti.history_ready,
                t.name, t.symbol, t.icon, t.decimals, t.dev, t.circ_supply, t.total_supply,
                t.token_program, t.holder_count, t.usd_price, t.liquidity, t.fdv, t.mcap,
                t.price_block_id, t.organic_score, t.organic_score_label, ti.created_at
            FROM token_ids ti
            LEFT JOIN tokens t ON ti.id = t.token_id
        """)
        
        rows = cursor.fetchall()
        print(f"📊 Знайдено {len(rows)} токенів")
        
        for row in rows:
            await postgres_conn.execute("""
                INSERT INTO tokens (
                    id, token_address, token_pair, name, symbol, icon, decimals, dev,
                    circ_supply, total_supply, token_program, holder_count,
                    usd_price, liquidity, fdv, mcap, price_block_id,
                    organic_score, organic_score_label,
                    check_jupiter, history_ready, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24
                )
            """, 
                row['id'], row['token_address'], row['token_pair'], 
                row['name'], row['symbol'], row['icon'], row['decimals'], row['dev'],
                row['circ_supply'], row['total_supply'], row['token_program'], row['holder_count'],
                row['usd_price'], row['liquidity'], row['fdv'], row['mcap'], row['price_block_id'],
                row['organic_score'], row['organic_score_label'],
                row['check_jupiter'], bool(row['history_ready']), row['created_at']
            )
        
        print(f"✅ Мігровано {len(rows)} токенів")
        
        # Мігруємо trades
        print("📈 Мігруємо trades...")
        cursor = sqlite_conn.execute("SELECT * FROM trades")
        rows = cursor.fetchall()
        print(f"📊 Знайдено {len(rows)} trades")
        
        for row in rows:
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
                row['amount_sol'], row['amount_usd'], row['token_price_usd'], row['created_at']
            )
        
        print(f"✅ Мігровано {len(rows)} trades")
        
        # Валідація
        sqlite_count = sqlite_conn.execute("SELECT COUNT(*) FROM token_ids").fetchone()[0]
        postgres_count = await postgres_conn.fetchval("SELECT COUNT(*) FROM tokens")
        
        sqlite_trades = sqlite_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        postgres_trades = await postgres_conn.fetchval("SELECT COUNT(*) FROM trades")
        
        print(f"📊 SQLite: {sqlite_count} токенів, {sqlite_trades} trades")
        print(f"📊 PostgreSQL: {postgres_count} токенів, {postgres_trades} trades")
        
        if sqlite_count == postgres_count and sqlite_trades == postgres_trades:
            print("✅ Міграція завершена успішно!")
        else:
            print("❌ Помилка валідації!")
            
    finally:
        sqlite_conn.close()
        await postgres_conn.close()

if __name__ == "__main__":
    asyncio.run(migrate())
