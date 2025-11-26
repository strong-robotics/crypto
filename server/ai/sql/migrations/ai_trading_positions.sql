-- Таблиця для відстеження торгових позицій
CREATE TABLE IF NOT EXISTS ai_trading_positions (
    id BIGSERIAL PRIMARY KEY,
    token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION,
    invest_usd DOUBLE PRECISION NOT NULL,
    target_profit_pct DOUBLE PRECISION NOT NULL,
    profit_pct DOUBLE PRECISION,
    max_hold_seconds INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', -- 'active', 'closed', 'cancelled'
    exit_reason TEXT,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

-- Індекси для швидкого пошуку
CREATE INDEX IF NOT EXISTS idx_trading_positions_token ON ai_trading_positions(token_id);
CREATE INDEX IF NOT EXISTS idx_trading_positions_status ON ai_trading_positions(status);
CREATE INDEX IF NOT EXISTS idx_trading_positions_entry_time ON ai_trading_positions(entry_time);

-- Тригер для оновлення updated_at
CREATE OR REPLACE FUNCTION update_trading_positions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_trading_positions_updated_at
    BEFORE UPDATE ON ai_trading_positions
    FOR EACH ROW
    EXECUTE FUNCTION update_trading_positions_updated_at();
