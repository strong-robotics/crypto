#!/usr/bin/env python3
"""
Міграція даних з SQLite tokens.db в PostgreSQL crypto_db
Згідно з crypto-db-v3-structure.md
"""

import asyncio
import sqlite3
import asyncpg
from db_config import POSTGRES_CONFIG
from datetime import datetime

class SQLiteToPostgreSQLMigratorV3:
    def __init__(self):
        self.sqlite_path = "db/tokens.db"
        self.postgres_config = POSTGRES_CONFIG.copy()
        self.postgres_config['database'] = 'crypto_db'
        self.postgres_config.pop('min_size', None)
        self.postgres_config.pop('max_size', None)
    
    async def migrate(self):
        """Основна функція міграції"""
        print("🚀 Початок міграції з SQLite в PostgreSQL (V3 структура)...")
        
        # Підключаємося до SQLite
        sqlite_conn = sqlite3.connect(self.sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row
        
        # Підключаємося до PostgreSQL
        postgres_conn = await asyncpg.connect(**self.postgres_config)
        
        try:
            # 1. Створюємо таблиці з правильною структурою
            await self.create_tables(postgres_conn)
            
            # 2. Мігруємо токени
            await self.migrate_tokens(sqlite_conn, postgres_conn)
            
            # 3. Мігруємо trades
            await self.migrate_trades(sqlite_conn, postgres_conn)
            
            # 4. Валідація
            await self.validate_migration(sqlite_conn, postgres_conn)
            
            print("✅ Міграція завершена успішно!")
            
        finally:
            sqlite_conn.close()
            await postgres_conn.close()
    
    async def create_tables(self, postgres_conn):
        """Створюємо таблиці згідно з V3 структурою"""
        print("🏗️ Створюємо таблиці...")
        
        # Видаляємо існуючі таблиці
        await postgres_conn.execute('DROP TABLE IF EXISTS trades CASCADE')
        await postgres_conn.execute('DROP TABLE IF EXISTS tokens CASCADE')
        
        # Створюємо таблицю tokens з повною структурою
        await postgres_conn.execute('''
            CREATE TABLE tokens (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(44) UNIQUE NOT NULL,
                token_pair VARCHAR(44),
                name VARCHAR(255),
                symbol VARCHAR(50),
                icon TEXT,
                decimals INTEGER,
                dev VARCHAR(44),
                circ_supply DECIMAL(20,8),
                total_supply DECIMAL(20,8),
                token_program VARCHAR(44),
                holder_count INTEGER,
                usd_price DECIMAL(20,8),
                liquidity DECIMAL(20,8),
                fdv DECIMAL(20,8),
                mcap DECIMAL(20,8),
                price_block_id BIGINT,
                organic_score DECIMAL(10,4),
                organic_score_label VARCHAR(50),
                blockaid_rugpull BOOLEAN,
                mint_authority_disabled BOOLEAN,
                freeze_authority_disabled BOOLEAN,
                top_holders_percentage DECIMAL(5,2),
                dev_balance_percentage DECIMAL(5,2),
                -- Stats 5m
                price_change_5m DECIMAL(10,4),
                holder_change_5m DECIMAL(10,4),
                liquidity_change_5m DECIMAL(10,4),
                volume_change_5m DECIMAL(10,4),
                buy_volume_5m DECIMAL(20,8),
                sell_volume_5m DECIMAL(20,8),
                buy_organic_volume_5m DECIMAL(20,8),
                sell_organic_volume_5m DECIMAL(20,8),
                num_buys_5m INTEGER,
                num_sells_5m INTEGER,
                num_traders_5m INTEGER,
                -- Stats 1h
                price_change_1h DECIMAL(10,4),
                holder_change_1h DECIMAL(10,4),
                liquidity_change_1h DECIMAL(10,4),
                volume_change_1h DECIMAL(10,4),
                buy_volume_1h DECIMAL(20,8),
                sell_volume_1h DECIMAL(20,8),
                buy_organic_volume_1h DECIMAL(20,8),
                sell_organic_volume_1h DECIMAL(20,8),
                num_buys_1h INTEGER,
                num_sells_1h INTEGER,
                num_traders_1h INTEGER,
                -- Stats 6h
                price_change_6h DECIMAL(10,4),
                holder_change_6h DECIMAL(10,4),
                liquidity_change_6h DECIMAL(10,4),
                volume_change_6h DECIMAL(10,4),
                buy_volume_6h DECIMAL(20,8),
                sell_volume_6h DECIMAL(20,8),
                buy_organic_volume_6h DECIMAL(20,8),
                sell_organic_volume_6h DECIMAL(20,8),
                num_buys_6h INTEGER,
                num_sells_6h INTEGER,
                num_traders_6h INTEGER,
                -- Stats 24h
                price_change_24h DECIMAL(10,4),
                holder_change_24h DECIMAL(10,4),
                liquidity_change_24h DECIMAL(10,4),
                volume_change_24h DECIMAL(10,4),
                buy_volume_24h DECIMAL(20,8),
                sell_volume_24h DECIMAL(20,8),
                buy_organic_volume_24h DECIMAL(20,8),
                sell_organic_volume_24h DECIMAL(20,8),
                num_buys_24h INTEGER,
                num_sells_24h INTEGER,
                num_traders_24h INTEGER,
                -- System fields
                check_jupiter INTEGER DEFAULT 0,
                history_ready BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Створюємо таблицю trades
        await postgres_conn.execute('''
            CREATE TABLE trades (
                id SERIAL PRIMARY KEY,
                token_id INTEGER NOT NULL,
                signature VARCHAR(88) UNIQUE NOT NULL,
                timestamp BIGINT NOT NULL,
                readable_time TEXT NOT NULL,
                direction VARCHAR(10) NOT NULL,
                amount_tokens DECIMAL(20,8) NOT NULL,
                amount_sol TEXT NOT NULL,
                amount_usd TEXT NOT NULL,
                token_price_usd TEXT DEFAULT '0.0000000000',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Створюємо індекси
        await postgres_conn.execute('CREATE INDEX idx_tokens_address ON tokens(token_address)')
        await postgres_conn.execute('CREATE INDEX idx_tokens_pair ON tokens(token_pair)')
        await postgres_conn.execute('CREATE INDEX idx_tokens_check_jupiter ON tokens(check_jupiter)')
        await postgres_conn.execute('CREATE INDEX idx_tokens_history_ready ON tokens(history_ready)')
        await postgres_conn.execute('CREATE INDEX idx_tokens_price ON tokens(usd_price)')
        await postgres_conn.execute('CREATE INDEX idx_tokens_liquidity ON tokens(liquidity)')
        await postgres_conn.execute('CREATE INDEX idx_tokens_organic_score ON tokens(organic_score)')
        
        await postgres_conn.execute('CREATE INDEX idx_trades_token_id ON trades(token_id)')
        await postgres_conn.execute('CREATE INDEX idx_trades_signature ON trades(signature)')
        await postgres_conn.execute('CREATE INDEX idx_trades_timestamp ON trades(timestamp)')
        await postgres_conn.execute('CREATE INDEX idx_trades_direction ON trades(direction)')
        
        print("✅ Таблиці створено згідно з V3 структурою")
    
    async def migrate_tokens(self, sqlite_conn, postgres_conn):
        """Мігруємо токени з token_ids та tokens таблиць"""
        print("📦 Мігруємо токени...")
        
        # Отримуємо дані з SQLite
        cursor = sqlite_conn.execute("""
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
        
        rows = cursor.fetchall()
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
            """, 
                row['id'], row['token_address'], row['token_pair'], 
                row['name'], row['symbol'], row['icon'], row['decimals'], row['dev'],
                row['circ_supply'], row['total_supply'], row['token_program'], row['holder_count'],
                row['usd_price'], row['liquidity'], row['fdv'], row['mcap'], row['price_block_id'],
                row['organic_score'], row['organic_score_label'],
                row['mint_authority_disabled'], row['freeze_authority_disabled'],
                row['top_holders_percentage'], row['dev_balance_percentage'],
                row['check_jupiter'], bool(row['history_ready']), row['created_at']
            )
        
        print(f"✅ Мігровано {len(rows)} токенів")
    
    async def migrate_trades(self, sqlite_conn, postgres_conn):
        """Мігруємо trades"""
        print("📈 Мігруємо trades...")
        
        # Отримуємо дані з SQLite
        cursor = sqlite_conn.execute("SELECT * FROM trades")
        rows = cursor.fetchall()
        print(f"📊 Знайдено {len(rows)} trades для міграції")
        
        # Вставляємо в PostgreSQL
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
    migrator = SQLiteToPostgreSQLMigratorV3()
    await migrator.migrate()

if __name__ == "__main__":
    asyncio.run(main())
