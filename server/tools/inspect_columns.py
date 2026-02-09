import asyncio
import asyncpg
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_config import POSTGRES_CONFIG

async def inspect_db():
    config = POSTGRES_CONFIG.copy()
    config['database'] = 'crypto_db'
    config.pop('min_size', None)
    config.pop('max_size', None)
    
    conn = await asyncpg.connect(**config)
    
    for table in ['wallets', 'wallet_history']:
        print(f"\n📊 Columns in '{table}':")
        columns = await conn.fetch(f"""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = '{table}'
            ORDER BY ordinal_position;
        """)
        for col in columns:
            print(f"  - {col['column_name']} ({col['data_type']})")
            
    await conn.close()

if __name__ == "__main__":
    asyncio.run(inspect_db())
