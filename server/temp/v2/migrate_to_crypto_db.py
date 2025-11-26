#!/usr/bin/env python3

import sqlite3
import shutil
from datetime import datetime
import os
from typing import Dict, Any, List, Tuple

class DBMigrator:
    def __init__(self, debug: bool = True):
        self.debug = debug
        self.old_db_path = "db/tokens.db"
        self.new_db_path = "db/crypto.db"
        self.backup_path = f"db/tokens_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        
        # Підключення до БД
        self.old_conn = None
        self.new_conn = None
    
    def log(self, message: str):
        """Логування якщо debug=True"""
        if self.debug:
            print(message)
    
    def create_backup(self):
        """Створює бекап старої БД"""
        self.log(f"\n{'='*80}\n📦 Creating backup of tokens.db...")
        shutil.copy2(self.old_db_path, self.backup_path)
        self.log(f"✅ Backup created: {self.backup_path}")
    
    def connect_dbs(self):
        """Підключається до обох БД"""
        self.log(f"\n{'='*80}\n🔌 Connecting to databases...")
        self.old_conn = sqlite3.connect(self.old_db_path)
        self.old_conn.row_factory = sqlite3.Row
        
        # Створюємо нову БД якщо не існує
        self.new_conn = sqlite3.connect(self.new_db_path)
        self.new_conn.row_factory = sqlite3.Row
        self.log("✅ Connected to both databases")
    
    def create_new_schema(self):
        """Створює схему нової БД"""
        self.log(f"\n{'='*80}\n📝 Creating new database schema...")
        
        # Видаляємо таблиці якщо існують
        self.new_conn.execute("DROP TABLE IF EXISTS trades")
        self.new_conn.execute("DROP TABLE IF EXISTS tokens")
        
        self.new_conn.executescript("""
            -- Основна таблиця токенів
            CREATE TABLE tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT UNIQUE NOT NULL,
                token_pair TEXT,
                name TEXT,
                symbol TEXT,
                icon TEXT,
                decimals INTEGER,
                dev TEXT,
                circ_supply NUMERIC,
                total_supply NUMERIC,
                token_program TEXT,
                holder_count INTEGER,
                usd_price NUMERIC,
                liquidity NUMERIC,
                fdv NUMERIC,
                mcap NUMERIC,
                price_block_id INTEGER,
                organic_score NUMERIC,
                organic_score_label TEXT,
                
                -- Аудит
                mint_authority_disabled BOOLEAN,
                freeze_authority_disabled BOOLEAN,
                top_holders_percentage NUMERIC,
                dev_balance_percentage NUMERIC,
                
                -- Статистика 5m
                price_change_5m NUMERIC,
                holder_change_5m NUMERIC,
                liquidity_change_5m NUMERIC,
                volume_change_5m NUMERIC,
                buy_volume_5m NUMERIC,
                sell_volume_5m NUMERIC,
                buy_organic_volume_5m NUMERIC,
                sell_organic_volume_5m NUMERIC,
                num_buys_5m INTEGER,
                num_sells_5m INTEGER,
                num_traders_5m INTEGER,
                
                -- Статистика 1h
                price_change_1h NUMERIC,
                holder_change_1h NUMERIC,
                liquidity_change_1h NUMERIC,
                volume_change_1h NUMERIC,
                buy_volume_1h NUMERIC,
                sell_volume_1h NUMERIC,
                buy_organic_volume_1h NUMERIC,
                sell_organic_volume_1h NUMERIC,
                num_buys_1h INTEGER,
                num_sells_1h INTEGER,
                num_traders_1h INTEGER,
                
                -- Статистика 6h
                price_change_6h NUMERIC,
                holder_change_6h NUMERIC,
                liquidity_change_6h NUMERIC,
                volume_change_6h NUMERIC,
                buy_volume_6h NUMERIC,
                sell_volume_6h NUMERIC,
                buy_organic_volume_6h NUMERIC,
                sell_organic_volume_6h NUMERIC,
                num_buys_6h INTEGER,
                num_sells_6h INTEGER,
                num_traders_6h INTEGER,
                
                -- Статистика 24h
                price_change_24h NUMERIC,
                holder_change_24h NUMERIC,
                liquidity_change_24h NUMERIC,
                volume_change_24h NUMERIC,
                buy_volume_24h NUMERIC,
                sell_volume_24h NUMERIC,
                buy_organic_volume_24h NUMERIC,
                sell_organic_volume_24h NUMERIC,
                num_buys_24h INTEGER,
                num_sells_24h INTEGER,
                num_traders_24h INTEGER,
                
                -- Системні поля
                check_jupiter INTEGER DEFAULT 0,
                history_ready BOOLEAN DEFAULT 0
            );
            
            -- Торгові операції
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                signature TEXT UNIQUE NOT NULL,
                timestamp INTEGER NOT NULL,
                readable_time TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount_tokens NUMERIC NOT NULL,
                amount_sol TEXT NOT NULL,
                amount_usd TEXT NOT NULL,
                token_price_usd TEXT DEFAULT '0.0000000000',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (token_id) REFERENCES tokens(id)
            );
            
            -- Критичні індекси
            CREATE INDEX idx_tokens_address ON tokens(token_address);
            CREATE INDEX idx_tokens_check_jupiter ON tokens(check_jupiter);
            
            CREATE INDEX idx_trades_token_id ON trades(token_id);
            CREATE INDEX idx_trades_signature ON trades(signature);
            CREATE INDEX idx_trades_timestamp ON trades(timestamp);
        """)
        
        self.log("✅ Created new database schema")
    
    def migrate_tokens(self):
        """Міграція базових даних токенів"""
        self.log(f"\n{'='*80}\n📊 Migrating token data...")
        
        # 1. Базова інформація
        # Спочатку отримуємо дані зі старої БД
        old_data = self.old_conn.execute("""
            SELECT 
                ti.id,
                ti.token_address,
                ti.token_pair,
                t.name,
                t.symbol,
                t.icon,
                t.decimals,
                t.dev,
                t.circ_supply,
                t.total_supply,
                t.token_program,
                t.holder_count,
                t.usd_price,
                t.liquidity,
                t.fdv,
                t.mcap,
                t.price_block_id,
                t.organic_score,
                t.organic_score_label,
                ti.check_jupiter,
                ti.history_ready
            FROM token_ids ti
            LEFT JOIN tokens t ON t.token_id = ti.id
        """).fetchall()
        
        # Потім вставляємо в нову БД
        for row in old_data:
            self.new_conn.execute("""
                INSERT INTO tokens (
                    id, token_address, token_pair, name, symbol, icon, decimals, dev,
                    circ_supply, total_supply, token_program, holder_count,
                    usd_price, liquidity, fdv, mcap, price_block_id,
                    organic_score, organic_score_label,
                    check_jupiter, history_ready
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row['id'], row['token_address'], row['token_pair'], row['name'], 
                row['symbol'], row['icon'], row['decimals'], row['dev'],
                row['circ_supply'], row['total_supply'], row['token_program'], 
                row['holder_count'], row['usd_price'], row['liquidity'], 
                row['fdv'], row['mcap'], row['price_block_id'], 
                row['organic_score'], row['organic_score_label'],
                row['check_jupiter'], row['history_ready']
            ))
        
        # Комітимо зміни
        self.new_conn.commit()
        
        migrated_count = self.new_conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        self.log(f"✅ Migrated {migrated_count} tokens")
        
        # 2. Аудит дані
        self.log("\n📊 Migrating audit data...")
        audit_data = self.old_conn.execute("""
            SELECT token_id, mint_authority_disabled, freeze_authority_disabled, top_holders_percentage
            FROM token_audit
        """).fetchall()
        
        for row in audit_data:
            self.new_conn.execute("""
                UPDATE tokens
                SET 
                    mint_authority_disabled = ?,
                    freeze_authority_disabled = ?,
                    top_holders_percentage = ?,
                    dev_balance_percentage = NULL
                WHERE id = ?
            """, (row['mint_authority_disabled'], row['freeze_authority_disabled'], 
                  row['top_holders_percentage'], row['token_id']))
        
        self.new_conn.commit()
        self.log("✅ Migrated audit data")
        
        # 3. Статистика
        for period in ['5m', '1h', '6h', '24h']:
            self.log(f"\n📊 Migrating {period} stats...")
            table = f"token_stats_{period}"
            period_suffix = period.replace('m', '')  # 5m -> 5, 1h -> 1h
            
            stats_data = self.old_conn.execute(f"""
                SELECT token_id, price_change, liquidity_change, buy_volume, sell_volume, 
                       buy_organic_volume, num_buys, num_sells, num_traders
                FROM {table}
            """).fetchall()
            
            for row in stats_data:
                self.new_conn.execute(f"""
                    UPDATE tokens
                    SET 
                        price_change_{period_suffix} = ?,
                        holder_change_{period_suffix} = NULL,
                        liquidity_change_{period_suffix} = ?,
                        buy_volume_{period_suffix} = ?,
                        sell_volume_{period_suffix} = ?,
                        buy_organic_volume_{period_suffix} = ?,
                        sell_organic_volume_{period_suffix} = NULL,
                        volume_change_{period_suffix} = NULL,
                        num_buys_{period_suffix} = ?,
                        num_sells_{period_suffix} = ?,
                        num_traders_{period_suffix} = ?
                    WHERE id = ?
                """, (row['price_change'], row['liquidity_change'], row['buy_volume'], 
                      row['sell_volume'], row['buy_organic_volume'], row['num_buys'], 
                      row['num_sells'], row['num_traders'], row['token_id']))
            
            self.new_conn.commit()
            self.log(f"✅ Migrated {period} stats")
    
    def migrate_trades(self):
        """Міграція торгових операцій"""
        self.log(f"\n{'='*80}\n💱 Migrating trades...")
        
        trades_data = self.old_conn.execute("""
            SELECT token_id, signature, timestamp, readable_time, direction, 
                   amount_tokens, amount_sol, amount_usd, token_price_usd, created_at
            FROM trades
        """).fetchall()
        
        for row in trades_data:
            self.new_conn.execute("""
                INSERT INTO trades (
                    token_id, signature, timestamp, readable_time, direction, 
                    amount_tokens, amount_sol, amount_usd, token_price_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (row['token_id'], row['signature'], row['timestamp'], row['readable_time'], 
                  row['direction'], row['amount_tokens'], row['amount_sol'], 
                  row['amount_usd'], row['token_price_usd'], row['created_at']))
        
        self.new_conn.commit()
        trades_count = self.new_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        self.log(f"✅ Migrated {trades_count} trades")
    
    def validate_migration(self) -> bool:
        """Перевіряє успішність міграції"""
        self.log(f"\n{'='*80}\n🔍 Validating migration...")
        
        # 1. Перевіряємо кількість токенів
        old_tokens = self.old_conn.execute("SELECT COUNT(*) FROM token_ids").fetchone()[0]
        new_tokens = self.new_conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        self.log(f"\nTokens count:")
        self.log(f"  Old DB: {old_tokens}")
        self.log(f"  New DB: {new_tokens}")
        if old_tokens != new_tokens:
            self.log("❌ Token counts don't match!")
            return False
        
        # 2. Перевіряємо кількість торгів
        old_trades = self.old_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        new_trades = self.new_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        self.log(f"\nTrades count:")
        self.log(f"  Old DB: {old_trades}")
        self.log(f"  New DB: {new_trades}")
        if old_trades != new_trades:
            self.log("❌ Trade counts don't match!")
            return False
        
        # 3. Перевіряємо випадковий токен
        token_id = self.old_conn.execute("SELECT id FROM token_ids LIMIT 1").fetchone()[0]
        
        old_token = dict(self.old_conn.execute("""
            SELECT ti.*, t.* 
            FROM token_ids ti 
            LEFT JOIN tokens t ON t.token_id = ti.id 
            WHERE ti.id = ?
        """, (token_id,)).fetchone())
        
        new_token = dict(self.new_conn.execute("""
            SELECT * FROM tokens WHERE id = ?
        """, (token_id,)).fetchone())
        
        self.log(f"\nRandom token (id={token_id}):")
        self.log(f"  Old DB: {old_token['token_address']}")
        self.log(f"  New DB: {new_token['token_address']}")
        
        if old_token['token_address'] != new_token['token_address']:
            self.log("❌ Random token check failed!")
            return False
        
        self.log("\n✅ Validation passed!")
        return True
    
    def migrate(self):
        """Виконує повну міграцію"""
        try:
            # 1. Створюємо бекап
            self.create_backup()
            
            # 2. Підключаємось до БД
            self.connect_dbs()
            
            # 3. Створюємо нову схему
            self.create_new_schema()
            
            # 4. Мігруємо дані
            self.migrate_tokens()
            self.migrate_trades()
            
            # 5. Валідація
            if not self.validate_migration():
                raise Exception("Migration validation failed!")
            
            self.log(f"\n{'='*80}")
            self.log("🎉 Migration completed successfully!")
            self.log(f"{'='*80}")
            
        except Exception as e:
            self.log(f"\n❌ Migration failed: {e}")
            raise
        
        finally:
            # Закриваємо з'єднання
            if self.old_conn:
                self.old_conn.close()
            if self.new_conn:
                self.new_conn.close()

if __name__ == "__main__":
    migrator = DBMigrator(debug=True)
    migrator.migrate()
