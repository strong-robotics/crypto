#!/usr/bin/env python3
"""
Migration: Merge tokens → token_ids
Об'єднання таблиць tokens та token_ids в одну головну таблицю
"""

import asyncio
import aiosqlite
import asyncpg
from datetime import datetime

SQLITE_DB = "db/tokens.db"
POSTGRES_DSN = "postgresql://yevhenvasylenko@localhost/crypto_app"

async def export_merged_data():
    """Експорт об'єднаних даних з SQLite"""
    print("📦 Exporting merged data from SQLite (token_ids + tokens)...")
    
    conn = await aiosqlite.connect(SQLITE_DB)
    
    # Отримуємо об'єднані дані через LEFT JOIN
    cursor = await conn.execute("""
        SELECT 
            ti.id,
            ti.token_address,
            ti.token_pair,
            ti.is_honeypot,
            ti.lp_owner,
            ti.pattern,
            ti.dev_address,
            ti.security_analyzed_at,
            ti.check_dexscreener,
            ti.check_jupiter,
            ti.check_sol_rpc,
            ti.history_ready,
            ti.created_at,
            t.name,
            t.symbol,
            t.icon,
            t.decimals,
            t.twitter,
            t.dev,
            t.circ_supply,
            t.total_supply,
            t.token_program,
            t.launchpad,
            t.holder_count,
            t.usd_price,
            t.liquidity,
            t.fdv,
            t.mcap,
            t.bonding_curve,
            t.price_block_id,
            t.organic_score,
            t.organic_score_label,
            t.updated_at
        FROM token_ids ti
        LEFT JOIN tokens t ON t.token_id = ti.id
        ORDER BY ti.id
    """)
    
    rows = await cursor.fetchall()
    columns = [
        'id', 'token_address', 'token_pair', 'is_honeypot', 'lp_owner', 'pattern',
        'dev_address', 'security_analyzed_at', 'check_dexscreener', 'check_jupiter',
        'check_sol_rpc', 'history_ready', 'created_at',
        'name', 'symbol', 'icon', 'decimals', 'twitter', 'dev',
        'circ_supply', 'total_supply', 'token_program', 'launchpad',
        'holder_count', 'usd_price', 'liquidity', 'fdv', 'mcap',
        'bonding_curve', 'price_block_id', 'organic_score', 'organic_score_label',
        'updated_at'
    ]
    
    merged_data = []
    for row in rows:
        merged_data.append(dict(zip(columns, row)))
    
    await conn.close()
    
    print(f"  ✅ Exported {len(merged_data)} merged tokens")
    return merged_data

async def create_merged_schema(pool):
    """Створення нової структури token_ids в PostgreSQL"""
    print("\n🏗️  Creating merged token_ids schema in PostgreSQL...")
    
    async with pool.acquire() as conn:
        # Видаляємо стару структуру
        await conn.execute("DROP TABLE IF EXISTS token_ids CASCADE")
        print("  🗑️  Dropped old token_ids table")
        
        # Створюємо нову об'єднану структуру
        await conn.execute("""
            CREATE TABLE token_ids (
                -- === ОСНОВНІ ПОЛЯ (з token_ids) ===
                id SERIAL PRIMARY KEY,
                token_address TEXT UNIQUE NOT NULL,
                token_pair TEXT UNIQUE,
                is_honeypot BOOLEAN,
                lp_owner TEXT,
                pattern TEXT DEFAULT '',
                dev_address TEXT,
                security_analyzed_at TIMESTAMP,
                check_dexscreener INTEGER DEFAULT 0,
                check_jupiter INTEGER DEFAULT 0,
                check_sol_rpc INTEGER DEFAULT 0,
                history_ready BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                -- === ДЕТАЛЬНА ІНФОРМАЦІЯ (з tokens) ===
                name TEXT,
                symbol TEXT,
                icon TEXT,
                decimals INTEGER,
                twitter TEXT,
                dev TEXT,
                circ_supply NUMERIC,
                total_supply NUMERIC,
                token_program TEXT,
                launchpad TEXT,
                holder_count INTEGER,
                usd_price NUMERIC,
                liquidity NUMERIC,
                fdv NUMERIC,
                mcap NUMERIC,
                bonding_curve NUMERIC,
                price_block_id INTEGER,
                organic_score NUMERIC,
                organic_score_label TEXT,
                updated_at TIMESTAMP
            )
        """)
        print("  ✅ Created merged token_ids table")
        
        # Індекси
        await conn.execute("CREATE INDEX idx_token_ids_address ON token_ids(token_address)")
        await conn.execute("CREATE INDEX idx_token_ids_pair ON token_ids(token_pair)")
        await conn.execute("CREATE INDEX idx_token_ids_created ON token_ids(created_at)")
        await conn.execute("CREATE INDEX idx_token_ids_price ON token_ids(usd_price)")
        await conn.execute("CREATE INDEX idx_token_ids_liquidity ON token_ids(liquidity)")
        await conn.execute("CREATE INDEX idx_token_ids_updated ON token_ids(updated_at)")
        print("  ✅ Created indexes")

async def import_merged_data(pool, merged_data):
    """Імпорт об'єднаних даних в PostgreSQL"""
    print(f"\n📥 Importing {len(merged_data)} merged tokens to PostgreSQL...")
    
    imported = 0
    async with pool.acquire() as conn:
        for token in merged_data:
            try:
                # Конвертуємо datetime fields
                security_analyzed_at = None
                if token.get('security_analyzed_at'):
                    try:
                        security_analyzed_at = datetime.fromisoformat(token['security_analyzed_at'])
                    except:
                        security_analyzed_at = None
                
                created_at = None
                if token.get('created_at'):
                    try:
                        created_at = datetime.fromisoformat(token['created_at'])
                    except:
                        created_at = datetime.now()
                else:
                    created_at = datetime.now()
                
                updated_at = None
                if token.get('updated_at'):
                    try:
                        # Remove timezone info for PostgreSQL
                        dt = datetime.fromisoformat(token['updated_at'])
                        updated_at = dt.replace(tzinfo=None) if hasattr(dt, 'tzinfo') else dt
                    except:
                        updated_at = None
                
                # Конвертуємо boolean
                is_honeypot = bool(token.get('is_honeypot', 0)) if token.get('is_honeypot') is not None else None
                history_ready = bool(token.get('history_ready', 0)) if token.get('history_ready') is not None else False
                
                await conn.execute("""
                    INSERT INTO token_ids (
                        token_address, token_pair, is_honeypot, lp_owner, pattern,
                        dev_address, security_analyzed_at, check_dexscreener, check_jupiter,
                        check_sol_rpc, history_ready, created_at,
                        name, symbol, icon, decimals, twitter, dev,
                        circ_supply, total_supply, token_program, launchpad,
                        holder_count, usd_price, liquidity, fdv, mcap,
                        bonding_curve, price_block_id, organic_score, organic_score_label,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13, $14, $15, $16, $17, $18, $19, $20, $21, $22,
                        $23, $24, $25, $26, $27, $28, $29, $30, $31, $32
                    )
                    ON CONFLICT (token_address) DO UPDATE SET
                        token_pair = EXCLUDED.token_pair,
                        is_honeypot = EXCLUDED.is_honeypot,
                        lp_owner = EXCLUDED.lp_owner,
                        pattern = EXCLUDED.pattern,
                        dev_address = EXCLUDED.dev_address,
                        security_analyzed_at = EXCLUDED.security_analyzed_at,
                        check_dexscreener = EXCLUDED.check_dexscreener,
                        check_jupiter = EXCLUDED.check_jupiter,
                        check_sol_rpc = EXCLUDED.check_sol_rpc,
                        history_ready = EXCLUDED.history_ready,
                        name = EXCLUDED.name,
                        symbol = EXCLUDED.symbol,
                        icon = EXCLUDED.icon,
                        decimals = EXCLUDED.decimals,
                        twitter = EXCLUDED.twitter,
                        dev = EXCLUDED.dev,
                        circ_supply = EXCLUDED.circ_supply,
                        total_supply = EXCLUDED.total_supply,
                        token_program = EXCLUDED.token_program,
                        launchpad = EXCLUDED.launchpad,
                        holder_count = EXCLUDED.holder_count,
                        usd_price = EXCLUDED.usd_price,
                        liquidity = EXCLUDED.liquidity,
                        fdv = EXCLUDED.fdv,
                        mcap = EXCLUDED.mcap,
                        bonding_curve = EXCLUDED.bonding_curve,
                        price_block_id = EXCLUDED.price_block_id,
                        organic_score = EXCLUDED.organic_score,
                        organic_score_label = EXCLUDED.organic_score_label,
                        updated_at = EXCLUDED.updated_at
                """,
                    token.get('token_address'),
                    token.get('token_pair'),
                    is_honeypot,
                    token.get('lp_owner'),
                    token.get('pattern', ''),
                    token.get('dev_address'),
                    security_analyzed_at,
                    token.get('check_dexscreener', 0),
                    token.get('check_jupiter', 0),
                    token.get('check_sol_rpc', 0),
                    history_ready,
                    created_at,
                    token.get('name'),
                    token.get('symbol'),
                    token.get('icon'),
                    token.get('decimals'),
                    token.get('twitter'),
                    token.get('dev'),
                    token.get('circ_supply'),
                    token.get('total_supply'),
                    token.get('token_program'),
                    token.get('launchpad'),
                    token.get('holder_count'),
                    token.get('usd_price'),
                    token.get('liquidity'),
                    token.get('fdv'),
                    token.get('mcap'),
                    token.get('bonding_curve'),
                    token.get('price_block_id'),
                    token.get('organic_score'),
                    token.get('organic_score_label'),
                    updated_at
                )
                
                imported += 1
                if imported % 10 == 0:
                    print(f"  📊 Imported {imported}/{len(merged_data)} tokens...")
                    
            except Exception as e:
                print(f"  ❌ Error importing token {token.get('token_address', '?')[:20]}...: {e}")
    
    print(f"  ✅ Successfully imported {imported}/{len(merged_data)} tokens")

async def main():
    print("="*80)
    print("🔄 MIGRATION: Merging tokens → token_ids")
    print("="*80)
    
    # 1. Експорт об'єднаних даних
    merged_data = await export_merged_data()
    
    # 2. Підключення до PostgreSQL
    pool = await asyncpg.create_pool(POSTGRES_DSN)
    
    # 3. Створення нової структури
    await create_merged_schema(pool)
    
    # 4. Імпорт даних
    await import_merged_data(pool, merged_data)
    
    # 5. Закриття
    await pool.close()
    
    print("\n" + "="*80)
    print("✅ MIGRATION COMPLETED!")
    print("="*80)
    print(f"📊 Total merged tokens: {len(merged_data)}")
    print("🎉 token_ids now contains all fields from both tables!")

if __name__ == "__main__":
    asyncio.run(main())

