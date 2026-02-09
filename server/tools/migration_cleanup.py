import asyncio
import asyncpg
import sys
import os

# Add parent directory to path to import db_config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from db_config import POSTGRES_CONFIG
except ImportError:
    print("❌ Could not import db_config. Make sure you are running from server/tools/")
    sys.exit(1)

async def run_migration():
    config = POSTGRES_CONFIG.copy()
    config['database'] = 'crypto_db'
    config.pop('min_size', None)
    config.pop('max_size', None)
    
    print("🔗 Connecting to database...")
    conn = await asyncpg.connect(**config)
    
    try:
        # 1. Cleanup wallets table
        print("🧹 Cleaning up 'wallets' table...")
        await conn.execute("""
            ALTER TABLE wallets 
            DROP COLUMN IF EXISTS active_token_id,
            DROP COLUMN IF EXISTS total_profit_usd,
            DROP COLUMN IF EXISTS initial_deposit_usd;
        """)
        
        # 2. Cleanup wallet_history table
        print("🧹 Cleaning up 'wallet_history' table...")
        await conn.execute("""
            ALTER TABLE wallet_history
            DROP COLUMN IF EXISTS profit_usd,
            DROP COLUMN IF EXISTS profit_pct,
            DROP COLUMN IF EXISTS entry_expected_amount_usd,
            DROP COLUMN IF EXISTS entry_actual_amount_usd,
            DROP COLUMN IF EXISTS exit_expected_amount_usd,
            DROP COLUMN IF EXISTS exit_actual_amount_usd,
            DROP COLUMN IF EXISTS entry_slippage_bps,
            DROP COLUMN IF EXISTS exit_slippage_bps;
        """)
        
        print("✅ Migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(run_migration())
