-- ai_forecasts: header rows for yellow forecast segments
CREATE TABLE IF NOT EXISTS ai_forecasts (
  id BIGSERIAL PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
  model_id INTEGER NOT NULL REFERENCES ai_models(id),
  origin_ts BIGINT NOT NULL,
  encoder_len_sec INTEGER NOT NULL,
  horizon_sec INTEGER NOT NULL,
  score_up DOUBLE PRECISION,           -- probability of growth
  exp_return DOUBLE PRECISION,         -- expected return over horizon
  y_p50 DOUBLE PRECISION[],            -- forecast curve (median)
  y_p10 DOUBLE PRECISION[],            -- lower quantile (optional)
  y_p90 DOUBLE PRECISION[],            -- upper quantile (optional)
  target_return DOUBLE PRECISION,      -- e.g. 0.2 (=+20%)
  eta_to_target_sec INTEGER,           -- ETA to target_return or NULL
  price_now DOUBLE PRECISION,          -- price at origin_ts
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(token_id, model_id, origin_ts)
);
CREATE INDEX IF NOT EXISTS idx_ai_forecasts_token  ON ai_forecasts(token_id);
CREATE INDEX IF NOT EXISTS idx_ai_forecasts_origin ON ai_forecasts(origin_ts);

