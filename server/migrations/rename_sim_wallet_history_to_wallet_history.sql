-- Migration: Rename sim_wallet_history to wallet_history
-- Execute this if you have existing data in sim_wallet_history table

-- Step 1: Rename the table
ALTER TABLE IF EXISTS sim_wallet_history RENAME TO wallet_history;

-- Step 2: Rename indexes
ALTER INDEX IF EXISTS idx_swh_wallet RENAME TO idx_wh_wallet;
ALTER INDEX IF EXISTS idx_swh_token RENAME TO idx_wh_token;

-- Step 3: Verify (optional - can run manually)
-- SELECT COUNT(*) FROM wallet_history;

