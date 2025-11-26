-- Data Migration: Перенести дані з sim_* полів у wallet_history (якщо такі є)
-- АБО автоматично мігрувати дані, якщо вони існують

-- ВАЖЛИВО: Цей скрипт потрібно виконати ПЕРЕД застосуванням 20251106_tokens_cleanup.sql
-- Оскільки після видалення sim_* полів ми втратимо доступ до цих даних

BEGIN;

-- 1. Міграція відкритих позицій з tokens.sim_* у wallet_history
-- Автоматично виконується, якщо є дані
DO $$
DECLARE
    open_count INTEGER;
BEGIN
    -- Перевірка, чи є відкриті позиції
    SELECT COUNT(*) INTO open_count
    FROM tokens t
    WHERE t.sim_buy_iteration IS NOT NULL
      AND t.sim_sell_iteration IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM wallet_history wh 
          WHERE wh.token_id = t.id 
            AND wh.exit_iteration IS NULL
      );
    
    IF open_count > 0 THEN
        RAISE NOTICE 'Мігруємо % відкритих позицій з tokens.sim_* у wallet_history', open_count;
        
        INSERT INTO wallet_history (
            wallet_id, token_id, entry_token_amount, entry_price_usd, 
            entry_amount_usd, entry_iteration, entry_signature, 
            created_at, updated_at
        )
        SELECT 
            t.sim_wallet_id AS wallet_id,
            t.id AS token_id,
            t.sim_buy_token_amount AS entry_token_amount,
            t.sim_buy_price_usd AS entry_price_usd,
            COALESCE(t.sim_buy_token_amount * t.sim_buy_price_usd, 0.0) AS entry_amount_usd,
            t.sim_buy_iteration AS entry_iteration,
            NULL AS entry_signature,
            CURRENT_TIMESTAMP AS created_at,
            CURRENT_TIMESTAMP AS updated_at
        FROM tokens t
        WHERE t.sim_buy_iteration IS NOT NULL
          AND t.sim_sell_iteration IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM wallet_history wh 
              WHERE wh.token_id = t.id 
                AND wh.exit_iteration IS NULL
          );
    ELSE
        RAISE NOTICE 'Відкритих позицій для міграції не знайдено';
    END IF;
END$$;

-- 2. Міграція закритих позицій з tokens.sim_* у wallet_history
-- Автоматично виконується, якщо є дані
DO $$
DECLARE
    closed_count INTEGER;
BEGIN
    -- Перевірка, чи є закриті позиції
    SELECT COUNT(*) INTO closed_count
    FROM tokens t
    WHERE t.sim_buy_iteration IS NOT NULL
      AND t.sim_sell_iteration IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM wallet_history wh 
          WHERE wh.token_id = t.id 
            AND wh.wallet_id = t.sim_wallet_id
      );
    
    IF closed_count > 0 THEN
        RAISE NOTICE 'Мігруємо % закритих позицій з tokens.sim_* у wallet_history', closed_count;
        
        INSERT INTO wallet_history (
            wallet_id, token_id, entry_token_amount, entry_price_usd, 
            entry_amount_usd, entry_iteration, entry_signature,
            exit_token_amount, exit_price_usd, exit_amount_usd,
            exit_iteration, outcome, reason,
            created_at, updated_at
        )
        SELECT 
            t.sim_wallet_id AS wallet_id,
            t.id AS token_id,
            t.sim_buy_token_amount AS entry_token_amount,
            t.sim_buy_price_usd AS entry_price_usd,
            COALESCE(t.sim_buy_token_amount * t.sim_buy_price_usd, 0.0) AS entry_amount_usd,
            t.sim_buy_iteration AS entry_iteration,
            NULL AS entry_signature,
            t.sim_sell_token_amount AS exit_token_amount,
            t.sim_sell_price_usd AS exit_price_usd,
            COALESCE(t.sim_sell_token_amount * t.sim_sell_price_usd, 0.0) AS exit_amount_usd,
            t.sim_sell_iteration AS exit_iteration,
            'closed' AS outcome,
            'migrated' AS reason,
            CURRENT_TIMESTAMP AS created_at,
            CURRENT_TIMESTAMP AS updated_at
        FROM tokens t
        WHERE t.sim_buy_iteration IS NOT NULL
          AND t.sim_sell_iteration IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM wallet_history wh 
              WHERE wh.token_id = t.id 
                AND wh.wallet_id = t.sim_wallet_id
          );
    ELSE
        RAISE NOTICE 'Закритих позицій для міграції не знайдено';
    END IF;
END$$;

-- 3. Оновлення tokens.wallet_id з sim_wallet_id (автоматично)
UPDATE tokens t
SET wallet_id = t.sim_wallet_id
WHERE t.sim_wallet_id IS NOT NULL
  AND (t.wallet_id IS NULL OR t.wallet_id != t.sim_wallet_id);

-- 4. Оновлення tokens.plan_sell_* з sim_plan_sell_* (автоматично, якщо поля існують)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='tokens' AND column_name='sim_plan_sell_iteration'
    ) THEN
        UPDATE tokens t
        SET plan_sell_iteration = t.sim_plan_sell_iteration,
            plan_sell_price_usd = t.sim_plan_sell_price_usd
        WHERE (t.sim_plan_sell_iteration IS NOT NULL OR t.sim_plan_sell_price_usd IS NOT NULL)
          AND (t.plan_sell_iteration IS NULL OR t.plan_sell_price_usd IS NULL);
    END IF;
END$$;

-- 5. Оновлення tokens.cur_income_price_usd з sim_cur_income_price_usd (автоматично, якщо поле існує)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='tokens' AND column_name='sim_cur_income_price_usd'
    ) THEN
        UPDATE tokens t
        SET cur_income_price_usd = t.sim_cur_income_price_usd
        WHERE t.sim_cur_income_price_usd IS NOT NULL
          AND t.cur_income_price_usd IS NULL;
    END IF;
END$$;

-- 6. Міграція даних з sim_wallets у wallets (якщо потрібно)
DO $$
DECLARE
    sim_wallets_count INTEGER;
BEGIN
    -- Перевірка, чи існує таблиця sim_wallets
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'sim_wallets'
    ) THEN
        SELECT COUNT(*) INTO sim_wallets_count FROM sim_wallets;
        
        IF sim_wallets_count > 0 THEN
            RAISE NOTICE 'Мігруємо % записів з sim_wallets у wallets', sim_wallets_count;
            
            INSERT INTO wallets (
                id, name, initial_deposit_usd, cash_usd, 
                entry_amount_usd, active_token_id, total_profit_usd,
                created_at, updated_at
            )
            SELECT 
                sw.id,
                sw.name,
                COALESCE(sw.initial_deposit_usd, sw.cash_usd, 5.5),
                COALESCE(sw.cash_usd, 5.5),
                sw.entry_amount_usd,
                sw.active_token_id,
                COALESCE(sw.total_profit_usd, 0.0),
                COALESCE(sw.created_at, CURRENT_TIMESTAMP),
                COALESCE(sw.updated_at, CURRENT_TIMESTAMP)
            FROM sim_wallets sw
            WHERE NOT EXISTS (
                SELECT 1 FROM wallets w WHERE w.id = sw.id
            )
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                cash_usd = EXCLUDED.cash_usd,
                entry_amount_usd = EXCLUDED.entry_amount_usd,
                active_token_id = EXCLUDED.active_token_id,
                total_profit_usd = EXCLUDED.total_profit_usd,
                updated_at = CURRENT_TIMESTAMP;
        ELSE
            RAISE NOTICE 'Записів у sim_wallets не знайдено';
        END IF;
    ELSE
        RAISE NOTICE 'Таблиця sim_wallets не існує';
    END IF;
END$$;

COMMIT;

