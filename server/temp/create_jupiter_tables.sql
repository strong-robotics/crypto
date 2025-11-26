-- Jupiter додаткові таблиці для PostgreSQL
-- Виконати: psql -U yevhenvasylenko -d crypto_app -f create_jupiter_tables.sql

-- 1. Token Stats (5m, 1h, 6h, 24h)
CREATE TABLE IF NOT EXISTS token_stats (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    
    -- Stats 5m
    stats_5m_price_change NUMERIC,
    stats_5m_buy_volume NUMERIC,
    stats_5m_sell_volume NUMERIC,
    stats_5m_num_buys INTEGER,
    stats_5m_num_sells INTEGER,
    stats_5m_num_traders INTEGER,
    stats_5m_num_net_buyers INTEGER,
    
    -- Stats 1h
    stats_1h_price_change NUMERIC,
    stats_1h_buy_volume NUMERIC,
    stats_1h_sell_volume NUMERIC,
    stats_1h_num_buys INTEGER,
    stats_1h_num_sells INTEGER,
    stats_1h_num_traders INTEGER,
    stats_1h_num_net_buyers INTEGER,
    
    -- Stats 6h
    stats_6h_price_change NUMERIC,
    stats_6h_buy_volume NUMERIC,
    stats_6h_sell_volume NUMERIC,
    stats_6h_num_buys INTEGER,
    stats_6h_num_sells INTEGER,
    stats_6h_num_traders INTEGER,
    stats_6h_num_net_buyers INTEGER,
    
    -- Stats 24h
    stats_24h_price_change NUMERIC,
    stats_24h_buy_volume NUMERIC,
    stats_24h_sell_volume NUMERIC,
    stats_24h_num_buys INTEGER,
    stats_24h_num_sells INTEGER,
    stats_24h_num_traders INTEGER,
    stats_24h_num_net_buyers INTEGER,
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Token Audit (безпека)
CREATE TABLE IF NOT EXISTS token_audit (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    
    mint_authority_disabled BOOLEAN,
    freeze_authority_disabled BOOLEAN,
    top_holders_percentage NUMERIC,
    dev_balance_percentage NUMERIC,
    dev_migrations INTEGER,
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Token First Pool
CREATE TABLE IF NOT EXISTS token_first_pool (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    
    pool_id TEXT,
    created_at TIMESTAMP,
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Token Tags
CREATE TABLE IF NOT EXISTS token_tags (
    id SERIAL PRIMARY KEY,
    token_id INTEGER REFERENCES token_ids(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    
    UNIQUE(token_id, tag)
);

-- 5. Додаємо website в token_ids (якщо ще немає)
ALTER TABLE token_ids 
ADD COLUMN IF NOT EXISTS website TEXT;

-- Індекси для швидкого пошуку
CREATE INDEX IF NOT EXISTS idx_token_stats_token_id ON token_stats(token_id);
CREATE INDEX IF NOT EXISTS idx_token_audit_token_id ON token_audit(token_id);
CREATE INDEX IF NOT EXISTS idx_token_first_pool_token_id ON token_first_pool(token_id);
CREATE INDEX IF NOT EXISTS idx_token_tags_token_id ON token_tags(token_id);

-- Коментарі
COMMENT ON TABLE token_stats IS 'Jupiter trading statistics (5m, 1h, 6h, 24h)';
COMMENT ON TABLE token_audit IS 'Jupiter security audit data';
COMMENT ON TABLE token_first_pool IS 'Jupiter first pool creation info';
COMMENT ON TABLE token_tags IS 'Jupiter token tags';

