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
);

CREATE INDEX IF NOT EXISTS idx_author_activity_author_wallet
    ON author_activity (author_wallet);

CREATE INDEX IF NOT EXISTS idx_author_activity_target_wallet
    ON author_activity (target_wallet);

CREATE INDEX IF NOT EXISTS idx_author_activity_token_mint
    ON author_activity (token_mint);

CREATE INDEX IF NOT EXISTS idx_author_activity_slot
    ON author_activity (slot);
