-- ai_models: registry of trained models
CREATE TABLE IF NOT EXISTS ai_models (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  model_type TEXT NOT NULL,       -- catboost_cls / catboost_reg / tcn / tft
  framework TEXT,                 -- catboost / pytorch / lightgbm
  hyperparams JSONB,
  train_window_sec INTEGER,
  predict_horizons_sec INTEGER[],
  trained_on TIMESTAMP,
  path TEXT,                      -- path to artifacts (.cbm, .pth)
  metrics JSONB,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(name, version)
);

