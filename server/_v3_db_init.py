#!/usr/bin/env python3
"""
Database initialization for V3
Automatically creates tables on first run
"""

import asyncio
import asyncpg
from db_config import POSTGRES_CONFIG

async def init_database():
    try:
        config = POSTGRES_CONFIG.copy()
        config['database'] = 'crypto_db'
        config.pop('min_size', None)
        config.pop('max_size', None)
        
        conn = await asyncpg.connect(**config)
        
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'tokens'
            )
        """)
        
        if not table_exists:
            await create_tables(conn)
        else:
            try:
                exists = await conn.fetchval(
                    """
                    SELECT 1 FROM pg_constraint 
                    WHERE conrelid = 'public.tokens'::regclass 
                      AND conname = 'chk_token_pair_not_mint'
                    """
                )
                if not exists:
                    await conn.execute(
                        "ALTER TABLE tokens ADD CONSTRAINT chk_token_pair_not_mint CHECK (token_pair IS NULL OR token_pair <> token_address)"
                    )
            except Exception:
                pass
            try:
                await conn.execute(
                    "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS token_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                )
            except Exception:
                pass
            try:
                await conn.execute(
                    "UPDATE tokens SET token_pair = NULL WHERE token_pair = token_address"
                )
            except Exception:
                pass
        # Миграция для token_metrics_seconds
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS jupiter_slot BIGINT"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS median_amount_sol TEXT"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS median_amount_usd TEXT"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS median_amount_tokens TEXT"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS median_token_price TEXT"
            )
        except Exception:
            pass
        # Aggregated trade flow per second (optional, for early-entry ML)
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS buy_count INTEGER"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS sell_count INTEGER"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS buy_usd DOUBLE PRECISION"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS sell_usd DOUBLE PRECISION"
            )
        except Exception:
            pass
        # Holders snapshot per second (optional)
        try:
            await conn.execute(
                "ALTER TABLE token_metrics_seconds ADD COLUMN IF NOT EXISTS holder_count INTEGER"
            )
        except Exception:
            pass
        
        # Note: history_ready and history_ready_iteration columns are deprecated.
        # Archived tokens are now in tokens_history table.
        # If these columns exist in old databases, they will be removed by migration 20250116_remove_history_ready.sql

        # Миграция для tokens - добавляем pattern_code с дефолтом 'unknown'
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS pattern_code VARCHAR"
            )
        except Exception:
            pass
        try:
            # установить default и проставить unknown там, где NULL
            await conn.execute(
                "ALTER TABLE tokens ALTER COLUMN pattern_code SET DEFAULT 'unknown'"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "UPDATE tokens SET pattern_code='unknown' WHERE pattern_code IS NULL"
            )
        except Exception:
            pass
        
        # Миграция для tokens - добавляем pair_resolve_attempts
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS pair_resolve_attempts INTEGER DEFAULT 0"
            )
        except Exception:
            pass
        
        # Миграция для trades - добавляем поле slot
        try:
            await conn.execute(
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS slot BIGINT"
            )
        except Exception:
            pass
        
        # Tokens: ensure real-trading plan fields and wallet binding
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS plan_sell_iteration INTEGER"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS plan_sell_price_usd NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS cur_income_price_usd NUMERIC(20,8)"
            )
        except Exception:
            pass
        
        # Миграция для tokens - добавляем поле pattern
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS pattern VARCHAR(255)"
            )
        except Exception:
            pass

        # Wallet binding (real trading) - stores key_id from keys.json
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS wallet_id INTEGER"
            )
        except Exception:
            pass
        
        # Real trading check (SWAP vs TRANSFER only)
        # NULL = not checked yet, TRUE = has real trading (SWAP), FALSE = transfer only
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS has_real_trading BOOLEAN"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS has_real_trading BOOLEAN"
            )
        except Exception:
            pass
        for column in ("swap_count", "transfer_count", "withdraw_count"):
            try:
                await conn.execute(
                    f"ALTER TABLE tokens ADD COLUMN IF NOT EXISTS {column} INTEGER DEFAULT 0"
                )
            except Exception:
                pass
            try:
                await conn.execute(
                    f"ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS {column} INTEGER DEFAULT 0"
                )
            except Exception:
                pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens ADD COLUMN IF NOT EXISTS median_amount_usd NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens ADD COLUMN IF NOT EXISTS median_amount_sol NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens ADD COLUMN IF NOT EXISTS median_amount_tokens NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens ADD COLUMN IF NOT EXISTS median_token_price NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS median_amount_usd NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS median_amount_sol NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS median_amount_tokens NUMERIC(20,8)"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                f"ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS median_token_price NUMERIC(20,8)"
            )
        except Exception:
            pass
        
        # Honeypot markers on tokens (lightweight flags)
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS is_honeypot BOOLEAN"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS honeypot_checked_at TIMESTAMP"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS honeypot_reason TEXT"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE tokens ADD COLUMN IF NOT EXISTS zero_tail_detected_iter INTEGER"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS zero_tail_detected_iter INTEGER"
            )
        except Exception:
            pass

        # History of trades per wallet
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_history (
                    id SERIAL PRIMARY KEY,
                    wallet_id INTEGER NOT NULL,
                    token_id INTEGER NOT NULL,
                    entry_amount_usd NUMERIC(20,8),
                    entry_token_amount NUMERIC(20,8),
                    entry_price_usd NUMERIC(20,8),
                    entry_iteration INTEGER,
                    -- Entry transaction details (real trading)
                    entry_slippage_bps INTEGER,              -- Slippage in basis points (e.g., 70 = 0.70%)
                    entry_slippage_pct NUMERIC(10,4),        -- Slippage in percentage
                    entry_price_impact_pct NUMERIC(10,4),    -- Price impact percentage
                    entry_transaction_fee_sol NUMERIC(20,8), -- Transaction fee in SOL
                    entry_transaction_fee_usd NUMERIC(20,8), -- Transaction fee in USD
                    entry_expected_amount_usd NUMERIC(20,8), -- Expected amount (before slippage)
                    entry_actual_amount_usd NUMERIC(20,8),   -- Actual amount (after slippage)
                    entry_signature TEXT,                    -- Transaction signature
                    exit_amount_usd NUMERIC(20,8),
                    exit_token_amount NUMERIC(20,8),
                    exit_price_usd NUMERIC(20,8),
                    exit_iteration INTEGER,
                    -- Exit transaction details (real trading)
                    exit_slippage_bps INTEGER,               -- Slippage in basis points
                    exit_slippage_pct NUMERIC(10,4),         -- Slippage in percentage
                    exit_price_impact_pct NUMERIC(10,4),     -- Price impact percentage
                    exit_transaction_fee_sol NUMERIC(20,8),  -- Transaction fee in SOL
                    exit_transaction_fee_usd NUMERIC(20,8),  -- Transaction fee in USD
                    exit_expected_amount_usd NUMERIC(20,8),  -- Expected amount (before slippage)
                    exit_actual_amount_usd NUMERIC(20,8),    -- Actual amount (after slippage)
                    exit_signature TEXT,                     -- Transaction signature
                    profit_usd NUMERIC(20,8),
                    profit_pct NUMERIC(10,4),
                    outcome VARCHAR(16),           -- good | medium | bad | frozen
                    reason VARCHAR(32),            -- manual | target | frozen | zero_tail | auto | honeypot
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_wallet ON wallet_history(wallet_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_token ON wallet_history(token_id)")
        except Exception:
            pass

        # Trade attempts log (manual + auto buy/sell attempts with reasons)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_attempts (
                    id SERIAL PRIMARY KEY,
                    token_id INTEGER,
                    wallet_id INTEGER,
                    action VARCHAR(32) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    message TEXT,
                    details JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_attempts_token ON trade_attempts(token_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_attempts_action ON trade_attempts(action)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_attempts_created_at ON trade_attempts(created_at DESC)")
        except Exception:
            pass
        
        # Wallets table (renamed from sim_wallets)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wallets (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(64),
                    initial_deposit_usd NUMERIC(20,8) NOT NULL DEFAULT 5.5,
                    cash_usd NUMERIC(20,8) NOT NULL DEFAULT 5.5,
                    entry_amount_usd NUMERIC(20,8) NULL,
                    active_token_id INTEGER NULL,
                    total_profit_usd NUMERIC(20,8) NOT NULL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        except Exception:
            pass

        # History tables (store archived tokens/metrics/trades outside hot path)
        try:
            await conn.execute("CREATE TABLE IF NOT EXISTS tokens_history (LIKE tokens INCLUDING ALL)")
            await conn.execute("ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            await conn.execute("CREATE TABLE IF NOT EXISTS token_metrics_seconds_history (LIKE token_metrics_seconds INCLUDING ALL)")
            await conn.execute("ALTER TABLE token_metrics_seconds_history ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            await conn.execute("ALTER TABLE token_metrics_seconds_history DROP CONSTRAINT IF EXISTS token_metrics_seconds_history_token_id_fkey")
            await conn.execute("CREATE TABLE IF NOT EXISTS trades_history (LIKE trades INCLUDING ALL)")
            await conn.execute("ALTER TABLE trades_history ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            await conn.execute("ALTER TABLE trades_history DROP CONSTRAINT IF EXISTS trades_history_token_id_fkey")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_history_created_at ON tokens_history(created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_history_archived_at ON tokens_history(archived_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_history_token_id ON token_metrics_seconds_history(token_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_history_token_id ON trades_history(token_id)")
        except Exception:
            pass
        
        await conn.close()
        return True
        
    except Exception:
        return False

async def create_tables(conn):
    await conn.execute('''
        CREATE TABLE tokens (
            id SERIAL PRIMARY KEY,
            token_address VARCHAR(44) UNIQUE NOT NULL,
            token_pair VARCHAR(44),
            CONSTRAINT chk_token_pair_not_mint CHECK (token_pair IS NULL OR token_pair <> token_address),
            name VARCHAR(255),
            symbol VARCHAR(50),
            icon TEXT,
            decimals INTEGER,
            dev VARCHAR(44),
            circ_supply NUMERIC(20,8),
            total_supply NUMERIC(20,8),
            token_program VARCHAR(44),
            holder_count INTEGER,
            usd_price NUMERIC(20,8),
            liquidity NUMERIC(20,8),
            fdv NUMERIC(20,8),
            mcap NUMERIC(20,8),
            price_block_id BIGINT,
            organic_score NUMERIC(10,4),
            organic_score_label VARCHAR(50),
            blockaid_rugpull BOOLEAN DEFAULT FALSE,
            mint_authority_disabled BOOLEAN,
            freeze_authority_disabled BOOLEAN,
            top_holders_percentage NUMERIC(5,2),
            dev_balance_percentage NUMERIC(5,2),
            -- Stats 5m
            price_change_5m NUMERIC(10,4),
            holder_change_5m NUMERIC(10,4),
            liquidity_change_5m NUMERIC(10,4),
            volume_change_5m NUMERIC(10,4),
            buy_volume_5m NUMERIC(20,8) DEFAULT 0,
            sell_volume_5m NUMERIC(20,8) DEFAULT 0,
            buy_organic_volume_5m NUMERIC(20,8) DEFAULT 0,
            sell_organic_volume_5m NUMERIC(20,8) DEFAULT 0,
            num_buys_5m INTEGER DEFAULT 0,
            num_sells_5m INTEGER DEFAULT 0,
            num_traders_5m INTEGER DEFAULT 0,
            -- Stats 1h
            price_change_1h NUMERIC(10,4),
            holder_change_1h NUMERIC(10,4),
            liquidity_change_1h NUMERIC(10,4),
            volume_change_1h NUMERIC(10,4),
            buy_volume_1h NUMERIC(20,8) DEFAULT 0,
            sell_volume_1h NUMERIC(20,8) DEFAULT 0,
            buy_organic_volume_1h NUMERIC(20,8) DEFAULT 0,
            sell_organic_volume_1h NUMERIC(20,8) DEFAULT 0,
            num_buys_1h INTEGER DEFAULT 0,
            num_sells_1h INTEGER DEFAULT 0,
            num_traders_1h INTEGER DEFAULT 0,
            -- Stats 6h
            price_change_6h NUMERIC(10,4),
            holder_change_6h NUMERIC(10,4),
            liquidity_change_6h NUMERIC(10,4),
            volume_change_6h NUMERIC(10,4),
            buy_volume_6h NUMERIC(20,8) DEFAULT 0,
            sell_volume_6h NUMERIC(20,8) DEFAULT 0,
            buy_organic_volume_6h NUMERIC(20,8) DEFAULT 0,
            sell_organic_volume_6h NUMERIC(20,8) DEFAULT 0,
            num_buys_6h INTEGER DEFAULT 0,
            num_sells_6h INTEGER DEFAULT 0,
            num_traders_6h INTEGER DEFAULT 0,
            -- Stats 24h
            price_change_24h NUMERIC(10,4),
            holder_change_24h NUMERIC(10,4),
            liquidity_change_24h NUMERIC(10,4),
            volume_change_24h NUMERIC(10,4),
            buy_volume_24h NUMERIC(20,8) DEFAULT 0,
            sell_volume_24h NUMERIC(20,8) DEFAULT 0,
            buy_organic_volume_24h NUMERIC(20,8) DEFAULT 0,
            sell_organic_volume_24h NUMERIC(20,8) DEFAULT 0,
            num_buys_24h INTEGER DEFAULT 0,
            num_sells_24h INTEGER DEFAULT 0,
            num_traders_24h INTEGER DEFAULT 0,
            median_amount_usd NUMERIC(20,8),
            median_amount_sol NUMERIC(20,8),
            median_amount_tokens NUMERIC(20,8),
            median_token_price NUMERIC(20,8),
            -- System fields
            check_dexscreener BOOLEAN DEFAULT FALSE,
            check_security BOOLEAN DEFAULT FALSE,
            check_solana_rpc BOOLEAN DEFAULT FALSE,
            has_real_trading BOOLEAN,
            swap_count INTEGER DEFAULT 0,
            transfer_count INTEGER DEFAULT 0,
            withdraw_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- Additional fields for compatibility
            token_price_usd NUMERIC(20,8),
            token_price_sol NUMERIC(20,8),
            token_market_cap NUMERIC(20,2),
            token_volume_24h NUMERIC(20,2),
            token_supply NUMERIC(20,2),
            token_decimals INTEGER,
            token_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            token_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE trades (
            id SERIAL PRIMARY KEY,
            token_id INTEGER REFERENCES tokens(id),
            signature VARCHAR(88) UNIQUE NOT NULL,
            timestamp BIGINT NOT NULL,
            readable_time TEXT,
            direction VARCHAR(10),
            amount_tokens NUMERIC(20,8),
            amount_sol TEXT,
            amount_usd TEXT,
            token_price_usd TEXT,
            slot BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE token_metrics_seconds (
            id SERIAL PRIMARY KEY,
            token_id INTEGER REFERENCES tokens(id),
            ts BIGINT NOT NULL,
            usd_price DOUBLE PRECISION,
            liquidity DOUBLE PRECISION,
            fdv DOUBLE PRECISION,
            mcap DOUBLE PRECISION,
            price_block_id BIGINT,
            jupiter_slot BIGINT,
            median_amount_sol TEXT,
            median_amount_usd TEXT,
            median_amount_tokens TEXT,
            median_token_price TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(token_id, ts)
        )
    ''')

    await conn.execute('''
        CREATE TABLE wallet_history (
            id SERIAL PRIMARY KEY,
            wallet_id INTEGER NOT NULL,
            token_id INTEGER NOT NULL,
            entry_amount_usd NUMERIC(20,8),
            entry_token_amount NUMERIC(20,8),
            entry_price_usd NUMERIC(20,8),
            entry_iteration INTEGER,
            entry_slippage_bps INTEGER,
            entry_slippage_pct NUMERIC(10,4),
            entry_price_impact_pct NUMERIC(10,4),
            entry_transaction_fee_sol NUMERIC(20,8),
            entry_transaction_fee_usd NUMERIC(20,8),
            entry_expected_amount_usd NUMERIC(20,8),
            entry_actual_amount_usd NUMERIC(20,8),
            entry_signature TEXT,
            exit_amount_usd NUMERIC(20,8),
            exit_token_amount NUMERIC(20,8),
            exit_price_usd NUMERIC(20,8),
            exit_iteration INTEGER,
            exit_slippage_bps INTEGER,
            exit_slippage_pct NUMERIC(10,4),
            exit_price_impact_pct NUMERIC(10,4),
            exit_transaction_fee_sol NUMERIC(20,8),
            exit_transaction_fee_usd NUMERIC(20,8),
            exit_expected_amount_usd NUMERIC(20,8),
            exit_actual_amount_usd NUMERIC(20,8),
            exit_signature TEXT,
            profit_usd NUMERIC(20,8),
            profit_pct NUMERIC(10,4),
            outcome VARCHAR(16),
            reason VARCHAR(32),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('CREATE INDEX idx_wh_wallet ON wallet_history(wallet_id)')
    await conn.execute('CREATE INDEX idx_wh_token ON wallet_history(token_id)')

    await conn.execute('''
        CREATE TABLE trade_attempts (
            id SERIAL PRIMARY KEY,
            token_id INTEGER,
            wallet_id INTEGER,
            action VARCHAR(32) NOT NULL,
            status VARCHAR(16) NOT NULL,
            message TEXT,
            details JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('CREATE INDEX idx_trade_attempts_token ON trade_attempts(token_id)')
    await conn.execute('CREATE INDEX idx_trade_attempts_action ON trade_attempts(action)')
    await conn.execute('CREATE INDEX idx_trade_attempts_created_at ON trade_attempts(created_at)')

    await conn.execute('''
        CREATE TABLE IF NOT EXISTS author_activity (
            id SERIAL PRIMARY KEY,
            dedupe_key VARCHAR(256) UNIQUE NOT NULL,
            author_wallet VARCHAR(64) NOT NULL,
            direction VARCHAR(16) NOT NULL DEFAULT 'outgoing',
            source VARCHAR(32) NOT NULL DEFAULT 'helius',
            signature VARCHAR(128) NOT NULL,
            slot BIGINT,
            block_time TIMESTAMP,
            transfer_type VARCHAR(16) NOT NULL,
            token_mint VARCHAR(64),
            token_account VARCHAR(64),
            target_wallet VARCHAR(64),
            amount_raw NUMERIC(40,0),
            amount_ui NUMERIC(30,12),
            amount_decimals INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('CREATE INDEX IF NOT EXISTS idx_author_activity_author_wallet ON author_activity(author_wallet)')
    await conn.execute('CREATE INDEX IF NOT EXISTS idx_author_activity_target_wallet ON author_activity(target_wallet)')
    await conn.execute('CREATE INDEX IF NOT EXISTS idx_author_activity_token_mint ON author_activity(token_mint)')
    await conn.execute('CREATE INDEX IF NOT EXISTS idx_author_activity_slot ON author_activity(slot)')

    await conn.execute('CREATE INDEX idx_tokens_address ON tokens(token_address)')
    await conn.execute('CREATE INDEX idx_tokens_symbol ON tokens(symbol)')
    await conn.execute('CREATE INDEX idx_tokens_usd_price ON tokens(usd_price)')
    await conn.execute('CREATE INDEX idx_tokens_liquidity ON tokens(liquidity)')
    await conn.execute('CREATE INDEX idx_tokens_organic_score ON tokens(organic_score)')
    
    await conn.execute('CREATE INDEX idx_trades_token_id ON trades(token_id)')
    await conn.execute('CREATE INDEX idx_trades_signature ON trades(signature)')
    await conn.execute('CREATE INDEX idx_trades_timestamp ON trades(timestamp)')
    await conn.execute('CREATE INDEX idx_trades_direction ON trades(direction)')
    
    await conn.execute('CREATE INDEX idx_metrics_token_id ON token_metrics_seconds(token_id)')
    await conn.execute('CREATE INDEX idx_metrics_ts ON token_metrics_seconds(ts)')

    # History tables (archived tokens/metrics/trades)
    await conn.execute('CREATE TABLE tokens_history (LIKE tokens INCLUDING ALL)')
    await conn.execute('ALTER TABLE tokens_history ADD COLUMN archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    await conn.execute('CREATE TABLE token_metrics_seconds_history (LIKE token_metrics_seconds INCLUDING ALL)')
    await conn.execute('ALTER TABLE token_metrics_seconds_history ADD COLUMN archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    await conn.execute('ALTER TABLE token_metrics_seconds_history DROP CONSTRAINT IF EXISTS token_metrics_seconds_history_token_id_fkey')
    await conn.execute('CREATE TABLE trades_history (LIKE trades INCLUDING ALL)')
    await conn.execute('ALTER TABLE trades_history ADD COLUMN archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    await conn.execute('ALTER TABLE trades_history DROP CONSTRAINT IF EXISTS trades_history_token_id_fkey')
    await conn.execute('CREATE INDEX idx_tokens_history_created_at ON tokens_history(created_at)')
    await conn.execute('CREATE INDEX idx_tokens_history_archived_at ON tokens_history(archived_at)')
    await conn.execute('CREATE INDEX idx_metrics_history_token_id ON token_metrics_seconds_history(token_id)')
    await conn.execute('CREATE INDEX idx_metrics_history_ts ON token_metrics_seconds_history(ts)')
    await conn.execute('CREATE INDEX idx_trades_history_token_id ON trades_history(token_id)')
    await conn.execute('CREATE INDEX idx_trades_history_created_at ON trades_history(created_at)')

async def test_connection():
    success = await init_database()
    return success

if __name__ == "__main__":
    asyncio.run(test_connection())
