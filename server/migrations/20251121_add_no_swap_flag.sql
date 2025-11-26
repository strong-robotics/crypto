ALTER TABLE tokens
    ADD COLUMN IF NOT EXISTS no_swap_after_second_corridor BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE tokens_history
    ADD COLUMN IF NOT EXISTS no_swap_after_second_corridor BOOLEAN NOT NULL DEFAULT FALSE;
