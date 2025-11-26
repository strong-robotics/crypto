-- Migration to add has_real_trading column to tokens table
-- This column stores the result of SWAP vs TRANSFER check through Helius API
-- NULL = not checked yet, TRUE = has real trading (SWAP transactions), FALSE = transfer only

BEGIN;

-- Add column if it doesn't exist
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS has_real_trading BOOLEAN;

-- Add column to tokens_history as well (for archived tokens)
ALTER TABLE tokens_history ADD COLUMN IF NOT EXISTS has_real_trading BOOLEAN;

COMMIT;

