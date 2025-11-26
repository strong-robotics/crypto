#!/usr/bin/env python3
"""
Migration script: SQLite → PostgreSQL
Exports data from SQLite and imports into PostgreSQL
"""

import asyncio
import aiosqlite
import asyncpg
import json
from datetime import datetime

# Database paths
SQLITE_DB = "db/tokens.db"
POSTGRES_DSN = "postgresql://localhost/crypto_app"

async def export_from_sqlite():
    """Export all data from SQLite"""
    print("📦 Exporting data from SQLite...")
    
    conn = await aiosqlite.connect(SQLITE_DB)
    
    data = {
        "token_ids": [],
        "trades": [],
        "dexscreener_pairs": [],
        "dexscreener_base_token": [],
        "dexscreener_quote_token": [],
        "dexscreener_txns": [],
        "dexscreener_volume": [],
        "dexscreener_price_change": [],
        "dexscreener_liquidity": []
    }
    
    # Export token_ids
    cursor = await conn.execute("SELECT * FROM token_ids ORDER BY id")
    rows = await cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    for row in rows:
        data["token_ids"].append(dict(zip(columns, row)))
    print(f"  ✅ Exported {len(data['token_ids'])} tokens")
    
    # Export trades
    cursor = await conn.execute("SELECT * FROM trades ORDER BY id")
    rows = await cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    for row in rows:
        data["trades"].append(dict(zip(columns, row)))
    print(f"  ✅ Exported {len(data['trades'])} trades")
    
    # Export DexScreener tables
    dex_tables = [
        "dexscreener_pairs",
        "dexscreener_base_token",
        "dexscreener_quote_token",
        "dexscreener_txns",
        "dexscreener_volume",
        "dexscreener_price_change",
        "dexscreener_liquidity"
    ]
    
    for table in dex_tables:
        try:
            cursor = await conn.execute(f"SELECT * FROM {table}")
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            for row in rows:
                data[table].append(dict(zip(columns, row)))
            print(f"  ✅ Exported {len(data[table])} rows from {table}")
        except Exception as e:
            print(f"  ⚠️ Table {table} not found or empty: {e}")
    
    await conn.close()
    
    # Save to JSON
    with open("db/migration_backup.json", "w") as f:
        json.dump(data, f, indent=2, default=str)
    
    print(f"\n💾 Data saved to db/migration_backup.json")
    return data

async def create_postgres_schema(pool):
    """Create PostgreSQL tables"""
    print("\n🏗️  Creating PostgreSQL schema...")
    
    async with pool.acquire() as conn:
        # Drop existing tables (if any)
        await conn.execute("DROP TABLE IF EXISTS trades CASCADE")
        await conn.execute("DROP TABLE IF EXISTS dexscreener_liquidity CASCADE")
        await conn.execute("DROP TABLE IF EXISTS dexscreener_price_change CASCADE")
        await conn.execute("DROP TABLE IF EXISTS dexscreener_volume CASCADE")
        await conn.execute("DROP TABLE IF EXISTS dexscreener_txns CASCADE")
        await conn.execute("DROP TABLE IF EXISTS dexscreener_quote_token CASCADE")
        await conn.execute("DROP TABLE IF EXISTS dexscreener_base_token CASCADE")
        await conn.execute("DROP TABLE IF EXISTS dexscreener_pairs CASCADE")
        await conn.execute("DROP TABLE IF EXISTS token_ids CASCADE")
        
        # Create token_ids table
        await conn.execute("""
            CREATE TABLE token_ids (
                id SERIAL PRIMARY KEY,
                token_address TEXT UNIQUE NOT NULL,
                token_name TEXT,
                token_symbol TEXT,
                token_pair TEXT,
                timestamp BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                check_dexscreener INTEGER DEFAULT 0,
                history_ready BOOLEAN DEFAULT FALSE
            )
        """)
        print("  ✅ Created table: token_ids")
        
        # Create trades table
        await conn.execute("""
            CREATE TABLE trades (
                id SERIAL PRIMARY KEY,
                token_id INTEGER NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                signature TEXT UNIQUE NOT NULL,
                timestamp BIGINT NOT NULL,
                readable_time TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount_tokens NUMERIC NOT NULL,
                amount_sol TEXT NOT NULL,
                amount_usd TEXT NOT NULL,
                token_price_usd TEXT DEFAULT '0.0000000000',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  ✅ Created table: trades")
        
        # Create DexScreener tables
        await conn.execute("""
            CREATE TABLE dexscreener_pairs (
                id SERIAL PRIMARY KEY,
                token_id INTEGER UNIQUE NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                chain_id TEXT,
                dex_id TEXT,
                url TEXT,
                pair_address TEXT,
                price_native TEXT,
                price_usd TEXT,
                fdv NUMERIC,
                market_cap NUMERIC,
                pair_created_at TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  ✅ Created table: dexscreener_pairs")
        
        await conn.execute("""
            CREATE TABLE dexscreener_base_token (
                id SERIAL PRIMARY KEY,
                token_id INTEGER UNIQUE NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                address TEXT,
                name TEXT,
                symbol TEXT
            )
        """)
        print("  ✅ Created table: dexscreener_base_token")
        
        await conn.execute("""
            CREATE TABLE dexscreener_quote_token (
                id SERIAL PRIMARY KEY,
                token_id INTEGER UNIQUE NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                address TEXT,
                name TEXT,
                symbol TEXT
            )
        """)
        print("  ✅ Created table: dexscreener_quote_token")
        
        await conn.execute("""
            CREATE TABLE dexscreener_txns (
                id SERIAL PRIMARY KEY,
                token_id INTEGER UNIQUE NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                m5_buys INTEGER DEFAULT 0,
                m5_sells INTEGER DEFAULT 0,
                h1_buys INTEGER DEFAULT 0,
                h1_sells INTEGER DEFAULT 0,
                h6_buys INTEGER DEFAULT 0,
                h6_sells INTEGER DEFAULT 0,
                h24_buys INTEGER DEFAULT 0,
                h24_sells INTEGER DEFAULT 0
            )
        """)
        print("  ✅ Created table: dexscreener_txns")
        
        await conn.execute("""
            CREATE TABLE dexscreener_volume (
                id SERIAL PRIMARY KEY,
                token_id INTEGER UNIQUE NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                h24 NUMERIC,
                h6 NUMERIC,
                h1 NUMERIC,
                m5 NUMERIC
            )
        """)
        print("  ✅ Created table: dexscreener_volume")
        
        await conn.execute("""
            CREATE TABLE dexscreener_price_change (
                id SERIAL PRIMARY KEY,
                token_id INTEGER UNIQUE NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                m5 NUMERIC,
                h1 NUMERIC,
                h6 NUMERIC,
                h24 NUMERIC
            )
        """)
        print("  ✅ Created table: dexscreener_price_change")
        
        await conn.execute("""
            CREATE TABLE dexscreener_liquidity (
                id SERIAL PRIMARY KEY,
                token_id INTEGER UNIQUE NOT NULL REFERENCES token_ids(id) ON DELETE CASCADE,
                usd NUMERIC,
                base NUMERIC,
                quote NUMERIC
            )
        """)
        print("  ✅ Created table: dexscreener_liquidity")
        
        # Create indexes for performance
        await conn.execute("CREATE INDEX idx_trades_token_id ON trades(token_id)")
        await conn.execute("CREATE INDEX idx_trades_timestamp ON trades(timestamp)")
        await conn.execute("CREATE INDEX idx_trades_signature ON trades(signature)")
        await conn.execute("CREATE INDEX idx_token_ids_address ON token_ids(token_address)")
        await conn.execute("CREATE INDEX idx_token_ids_pair ON token_ids(token_pair)")
        print("  ✅ Created indexes")

async def import_to_postgres(pool, data):
    """Import data into PostgreSQL"""
    print("\n📥 Importing data to PostgreSQL...")
    
    async with pool.acquire() as conn:
        # Import token_ids
        if data["token_ids"]:
            for token in data["token_ids"]:
                # Convert created_at string to datetime if needed
                created_at = token.get("created_at")
                if isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at)
                    except:
                        created_at = datetime.now()
                
                await conn.execute("""
                    INSERT INTO token_ids (
                        id, token_address, token_name, token_symbol, token_pair,
                        timestamp, created_at, check_dexscreener, history_ready
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """, 
                    token["id"],
                    token["token_address"],
                    token.get("token_name"),
                    token.get("token_symbol"),
                    token.get("token_pair"),
                    token.get("timestamp", 0),  # Default to 0 if missing
                    created_at,
                    token.get("check_dexscreener", 0),
                    bool(token.get("history_ready", 0))  # Convert int to bool
                )
            
            # Reset sequence
            await conn.execute("""
                SELECT setval('token_ids_id_seq', (SELECT MAX(id) FROM token_ids))
            """)
            print(f"  ✅ Imported {len(data['token_ids'])} tokens")
        
        # Import trades
        if data["trades"]:
            for trade in data["trades"]:
                try:
                    # Convert created_at string to datetime if needed
                    created_at = trade.get("created_at")
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at)
                        except:
                            created_at = datetime.now()
                    
                    await conn.execute("""
                        INSERT INTO trades (
                            id, token_id, signature, timestamp, readable_time,
                            direction, amount_tokens, amount_sol, amount_usd, token_price_usd, created_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                        trade["id"],
                        trade["token_id"],
                        trade["signature"],
                        trade["timestamp"],
                        trade["readable_time"],
                        trade["direction"],
                        trade["amount_tokens"],
                        trade["amount_sol"],
                        trade["amount_usd"],
                        trade.get("token_price_usd", "0.0"),
                        created_at
                    )
                except Exception as e:
                    print(f"  ⚠️ Error importing trade {trade['id']}: {e}")
            
            # Reset sequence
            await conn.execute("""
                SELECT setval('trades_id_seq', (SELECT MAX(id) FROM trades))
            """)
            print(f"  ✅ Imported {len(data['trades'])} trades")
        
        # Import DexScreener data
        dex_imports = {
            "dexscreener_pairs": """
                INSERT INTO dexscreener_pairs (
                    token_id, chain_id, dex_id, url, pair_address,
                    price_native, price_usd, fdv, market_cap, pair_created_at, timestamp
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            "dexscreener_base_token": """
                INSERT INTO dexscreener_base_token (token_id, address, name, symbol)
                VALUES ($1, $2, $3, $4)
            """,
            "dexscreener_quote_token": """
                INSERT INTO dexscreener_quote_token (token_id, address, name, symbol)
                VALUES ($1, $2, $3, $4)
            """,
            "dexscreener_txns": """
                INSERT INTO dexscreener_txns (
                    token_id, m5_buys, m5_sells, h1_buys, h1_sells,
                    h6_buys, h6_sells, h24_buys, h24_sells
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            "dexscreener_volume": """
                INSERT INTO dexscreener_volume (token_id, h24, h6, h1, m5)
                VALUES ($1, $2, $3, $4, $5)
            """,
            "dexscreener_price_change": """
                INSERT INTO dexscreener_price_change (token_id, m5, h1, h6, h24)
                VALUES ($1, $2, $3, $4, $5)
            """,
            "dexscreener_liquidity": """
                INSERT INTO dexscreener_liquidity (token_id, usd, base, quote)
                VALUES ($1, $2, $3, $4)
            """
        }
        
        for table, query in dex_imports.items():
            if data[table]:
                for row in data[table]:
                    try:
                        values = []
                        for k in row.keys():
                            if k == 'id':
                                continue
                            val = row.get(k)
                            # Convert timestamp strings to datetime
                            if k == 'timestamp' and isinstance(val, str):
                                try:
                                    val = datetime.fromisoformat(val)
                                except:
                                    val = datetime.now()
                            values.append(val)
                        await conn.execute(query, *values)
                    except Exception as e:
                        print(f"  ⚠️ Error importing {table} row: {e}")
                print(f"  ✅ Imported {len(data[table])} rows to {table}")

async def main():
    """Main migration function"""
    print("🚀 Starting migration: SQLite → PostgreSQL\n")
    
    # Step 1: Export from SQLite
    data = await export_from_sqlite()
    
    # Step 2: Connect to PostgreSQL
    print(f"\n🔌 Connecting to PostgreSQL...")
    pool = await asyncpg.create_pool(
        host='localhost',
        database='crypto_app',
        user='yevhenvasylenko',  # Your Mac username
        min_size=1,
        max_size=10
    )
    print("  ✅ Connected to PostgreSQL")
    
    # Step 3: Create schema
    await create_postgres_schema(pool)
    
    # Step 4: Import data
    await import_to_postgres(pool, data)
    
    # Step 5: Verify
    async with pool.acquire() as conn:
        token_count = await conn.fetchval("SELECT COUNT(*) FROM token_ids")
        trades_count = await conn.fetchval("SELECT COUNT(*) FROM trades")
        print(f"\n✅ Migration complete!")
        print(f"  📊 Tokens: {token_count}")
        print(f"  📊 Trades: {trades_count}")
    
    await pool.close()
    print("\n💾 Backup saved to: db/migration_backup.json")
    print("🎉 Migration successful! You can now update your code to use PostgreSQL")

if __name__ == "__main__":
    asyncio.run(main())

