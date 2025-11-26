-- token_trades_agg_seconds: per-second aggregates of trades for modeling/analytics
CREATE TABLE IF NOT EXISTS token_trades_agg_seconds (
  id BIGSERIAL PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
  ts BIGINT NOT NULL,
  buys INTEGER DEFAULT 0,
  sells INTEGER DEFAULT 0,
  trades_total INTEGER DEFAULT 0,
  buy_usd DOUBLE PRECISION DEFAULT 0,
  sell_usd DOUBLE PRECISION DEFAULT 0,
  avg_price_usd DOUBLE PRECISION,
  med_price_usd DOUBLE PRECISION,
  net_usd DOUBLE PRECISION,
  imbalance DOUBLE PRECISION,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(token_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_trades_agg_token ON token_trades_agg_seconds(token_id);
CREATE INDEX IF NOT EXISTS idx_trades_agg_ts    ON token_trades_agg_seconds(ts);

