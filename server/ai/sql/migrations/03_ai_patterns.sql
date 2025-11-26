-- ai_patterns: dictionary of behavior patterns
CREATE TABLE IF NOT EXISTS ai_patterns (
  id SERIAL PRIMARY KEY,
  code TEXT UNIQUE,
  name TEXT NOT NULL,
  tier TEXT,
  risk_level TEXT,
  score INTEGER,
  description TEXT,
  created_at TIMESTAMP DEFAULT now()
);

-- ai_token_patterns: labels for tokens (manual or model)
CREATE TABLE IF NOT EXISTS ai_token_patterns (
  id SERIAL PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
  pattern_id INTEGER NOT NULL REFERENCES ai_patterns(id),
  source TEXT NOT NULL,            -- 'manual' | 'model'
  confidence DOUBLE PRECISION,
  notes TEXT,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(token_id, pattern_id, source)
);

