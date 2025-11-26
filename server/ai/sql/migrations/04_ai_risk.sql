-- ai_risk_assessments: risk scoring for tokens
CREATE TABLE IF NOT EXISTS ai_risk_assessments (
  id SERIAL PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
  model_id INTEGER REFERENCES ai_models(id),
  risk_score DOUBLE PRECISION,
  risk_tier TEXT,
  risk_flags JSONB,
  created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_token ON ai_risk_assessments(token_id);

