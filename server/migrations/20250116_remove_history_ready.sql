-- Migration: Remove history_ready and history_ready_iteration columns
-- Date: 2025-01-16
-- Description: These columns are no longer used. Archived tokens are now in tokens_history table.

-- Step 1: Drop index on history_ready (if exists)
DROP INDEX IF EXISTS idx_tokens_history_ready;

-- Step 2: Remove history_ready column from tokens table (if exists)
ALTER TABLE tokens DROP COLUMN IF EXISTS history_ready;

-- Step 3: Remove history_ready_iteration column from tokens table (if exists)
ALTER TABLE tokens DROP COLUMN IF EXISTS history_ready_iteration;

