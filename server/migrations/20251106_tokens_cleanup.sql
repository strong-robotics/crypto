-- Tokens cleanup: remove simulation columns and rename plan fields
BEGIN;

-- Rename columns
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='tokens' AND column_name='sim_plan_sell_iteration'
  ) THEN
    EXECUTE 'ALTER TABLE tokens RENAME COLUMN sim_plan_sell_iteration TO plan_sell_iteration';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='tokens' AND column_name='sim_plan_sell_price_usd'
  ) THEN
    EXECUTE 'ALTER TABLE tokens RENAME COLUMN sim_plan_sell_price_usd TO plan_sell_price_usd';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='tokens' AND column_name='sim_cur_income_price_usd'
  ) THEN
    EXECUTE 'ALTER TABLE tokens RENAME COLUMN sim_cur_income_price_usd TO cur_income_price_usd';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='tokens' AND column_name='real_wallet_id'
  ) THEN
    EXECUTE 'ALTER TABLE tokens RENAME COLUMN real_wallet_id TO wallet_id';
  END IF;
END$$;

-- Drop simulation columns if exist
DO $$
DECLARE
  col TEXT;
  cols TEXT[] := ARRAY[
    'sim_buy_token_amount','sim_buy_price_usd','sim_buy_price_sol','sim_buy_iteration',
    'sim_profit_usd','sim_cur_income_price_sol','sim_sell_token_amount','sim_sell_price_usd',
    'sim_sell_price_sol','sim_sell_iteration','sim_wallet_id'
  ];
BEGIN
  FOREACH col IN ARRAY cols LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.columns 
      WHERE table_name='tokens' AND column_name=col
    ) THEN
      EXECUTE format('ALTER TABLE tokens DROP COLUMN %I', col);
    END IF;
  END LOOP;
END$$;

COMMIT;
