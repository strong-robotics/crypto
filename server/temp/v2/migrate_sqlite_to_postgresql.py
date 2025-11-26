#!/usr/bin/env python3
"""
Міграція даних з SQLite tokens.db в PostgreSQL crypto_db
"""

import asyncio
import sqlite3
import asyncpg
from db_config import POSTGRES_CONFIG
from datetime import datetime

class SQLiteToPostgreSQLMigrator:
    def __init__(self):
        self.sqlite_path = "db/tokens.db"
        self.postgres_config = POSTGRES_CONFIG.copy()
        self.postgres_config['database'] = 'crypto_db'
        self.postgres_config.pop('min_size', None)
        self.postgres_config.pop('max_size', None)
    
    async def migrate(self):
        """Основна функція міграції"""
        print("🚀 Початок міграції з SQLite в PostgreSQL...")
        
        # Підключаємося до SQLite
        sqlite_conn = sqlite3.connect(self.sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row
        
        # Підключаємося до PostgreSQL
        postgres_conn = await asyncpg.connect(**self.postgres_config)
        
        try:
            # 1. Мігруємо токени
            await self.migrate_tokens(sqlite_conn, postgres_conn)
            
            # 2. Мігруємо trades
            await self.migrate_trades(sqlite_conn, postgres_conn)
            
            # 3. Валідація
            await self.validate_migration(sqlite_conn, postgres_conn)
            
            print("✅ Міграція завершена успішно!")
            
        finally:
            sqlite_conn.close()
            await postgres_conn.close()
    
    async def migrate_tokens(self, sqlite_conn, postgres_conn):
        """Мігруємо токени з token_ids та tokens таблиць"""
        print("📦 Мігруємо токени...")
        
        # Отримуємо дані з SQLite
        sqlite_conn.execute("""
            SELECT 
                ti.id, ti.token_address, ti.token_pair, ti.check_jupiter, ti.history_ready,
                t.name, t.symbol, t.icon, t.decimals, t.dev, t.circ_supply, t.total_supply,
                t.token_program, t.holder_count, t.usd_price, t.liquidity, t.fdv, t.mcap,
                t.price_block_id, t.organic_score, t.organic_score_label,
                NULL as mint_authority_disabled, NULL as freeze_authority_disabled,
                NULL as top_holders_percentage, NULL as dev_balance_percentage, ti.created_at
            FROM token_ids ti
            LEFT JOIN tokens t ON ti.id = t.token_id
        """)
        
        rows = sqlite_conn.fetchall()
        print(f"📊 Знайдено {len(rows)} токенів для міграції")
        
        # Вставляємо в PostgreSQL
        for row in rows:
            await postgres_conn.execute("""
                INSERT INTO tokens (
                    id, token_address, token_pair, name, symbol, icon, decimals, dev,
                    circ_supply, total_supply, token_program, holder_count,
                    usd_price, liquidity, fdv, mcap, price_block_id,
                    organic_score, organic_score_label,
                    mint_authority_disabled, freeze_authority_disabled,
                    top_holders_percentage, dev_balance_percentage,
                    check_jupiter, history_ready, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27
                )
                ON CONFLICT (id) DO UPDATE SET
                    token_address = EXCLUDED.token_address,
                    token_pair = EXCLUDED.token_pair,
                    name = EXCLUDED.name,
                    symbol = EXCLUDED.symbol,
                    icon = EXCLUDED.icon,
                    decimals = EXCLUDED.decimals,
                    dev = EXCLUDED.dev,
                    circ_supply = EXCLUDED.circ_supply,
                    total_supply = EXCLUDED.total_supply,
                    token_program = EXCLUDED.token_program,
                    holder_count = EXCLUDED.holder_count,
                    usd_price = EXCLUDED.usd_price,
                    liquidity = EXCLUDED.liquidity,
                    fdv = EXCLUDED.fdv,
                    mcap = EXCLUDED.mcap,
                    price_block_id = EXCLUDED.price_block_id,
                    organic_score = EXCLUDED.organic_score,
                    organic_score_label = EXCLUDED.organic_score_label,
                    mint_authority_disabled = EXCLUDED.mint_authority_disabled,
                    freeze_authority_disabled = EXCLUDED.freeze_authority_disabled,
                    top_holders_percentage = EXCLUDED.top_holders_percentage,
                    dev_balance_percentage = EXCLUDED.dev_balance_percentage,
                    check_jupiter = EXCLUDED.check_jupiter,
                    history_ready = EXCLUDED.history_ready,
                    created_at = EXCLUDED.created_at
            """, 
                row['id'], row['token_address'], row['token_pair'], 
                row['name'], row['symbol'], row['icon'], row['decimals'], row['dev'],
                row['circ_supply'], row['total_supply'], row['token_program'], row['holder_count'],
                row['usd_price'], row['liquidity'], row['fdv'], row['mcap'], row['price_block_id'],
                row['organic_score'], row['organic_score_label'],
                row['mint_authority_disabled'], row['freeze_authority_disabled'],
                row['top_holders_percentage'], row['dev_balance_percentage'],
                row['check_jupiter'], row['history_ready'], row['created_at']
            )
        
        print(f"✅ Мігровано {len(rows)} токенів")
    
    async def migrate_trades(self, sqlite_conn, postgres_conn):
        """Мігруємо trades"""
        print("📈 Мігруємо trades...")
        
        # Отримуємо дані з SQLite
        sqlite_conn.execute("SELECT * FROM trades")
        rows = sqlite_conn.fetchall()
        print(f"📊 Знайдено {len(rows)} trades для міграції")
        
        # Вставляємо в PostgreSQL
        for row in rows:
            await postgres_conn.execute("""
                INSERT INTO trades (
                    id, token_id, timestamp, amount_sol, amount_tokens, amount_usd,
                    token_price_usd, trade_type, signature, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                )
                ON CONFLICT (id) DO UPDATE SET
                    token_id = EXCLUDED.token_id,
                    timestamp = EXCLUDED.timestamp,
                    amount_sol = EXCLUDED.amount_sol,
                    amount_tokens = EXCLUDED.amount_tokens,
                    amount_usd = EXCLUDED.amount_usd,
                    token_price_usd = EXCLUDED.token_price_usd,
                    trade_type = EXCLUDED.trade_type,
                    signature = EXCLUDED.signature,
                    created_at = EXCLUDED.created_at
            """, 
                row['id'], row['token_id'], row['timestamp'], 
                float(row['amount_sol']) if row['amount_sol'] else None, 
                row['amount_tokens'], 
                float(row['amount_usd']) if row['amount_usd'] else None,
                float(row['token_price_usd']) if row['token_price_usd'] else None, 
                row['direction'], row['signature'], row['created_at']
            )
        
        print(f"✅ Мігровано {len(rows)} trades")
    
    async def validate_migration(self, sqlite_conn, postgres_conn):
        """Валідація міграції"""
        print("🔍 Валідація міграції...")
        
        # Перевіряємо кількість токенів
        sqlite_count = sqlite_conn.execute("SELECT COUNT(*) FROM token_ids").fetchone()[0]
        postgres_count = await postgres_conn.fetchval("SELECT COUNT(*) FROM tokens")
        
        print(f"📊 SQLite токенів: {sqlite_count}")
        print(f"📊 PostgreSQL токенів: {postgres_count}")
        
        # Перевіряємо кількість trades
        sqlite_trades = sqlite_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        postgres_trades = await postgres_conn.fetchval("SELECT COUNT(*) FROM trades")
        
        print(f"📈 SQLite trades: {sqlite_trades}")
        print(f"📈 PostgreSQL trades: {postgres_trades}")
        
        if sqlite_count == postgres_count and sqlite_trades == postgres_trades:
            print("✅ Валідація пройшла успішно!")
        else:
            print("❌ Помилка валідації!")

async def main():
    migrator = SQLiteToPostgreSQLMigrator()
    await migrator.migrate()

if __name__ == "__main__":
    asyncio.run(main())
