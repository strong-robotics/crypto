Database: crypto_db (PostgreSQL)

Overview

- Единая БД для всех компонентов V3: сканер новых токенов, анализатор Jupiter, читатели графиков и лент, live‑trades.
- Основные сущности: tokens, trades, token_metrics_seconds.
- Потоки данных:
  - _v3_analyzer_jupiter.py — заполняет/обновляет tokens и делает секундные снэпшоты метрик (usd_price, liquidity, fdv, mcap).
  - _v3_live_trades.py — пишет сделки (trades) с Helius, считает per‑trade цену.
  - _v3_price_resolver.py — строит цену по секундам из trades и дозаполняет token_metrics_seconds (и производные mcap/fdv/liq при нулях).
  - _v3_tokens_reader.py — отдаёт список токенов для фронта, подставляя “последнюю НЕ нулевую” метрику из token_metrics_seconds, если в tokens цена/кап/ликвидность равны 0.
  - _v3_cleaner.py — чистит “сироты” без валидной пары и данных.

Tables

- tokens
  - id: integer, PK
  - token_address: varchar(44), unique, not null
  - token_pair: varchar(44), nullable (CHECK token_pair IS NULL OR token_pair <> token_address)
  - name: varchar(255)
  - symbol: varchar(50)
  - icon: text
  - decimals: integer
  - dev: varchar(44)
  - circ_supply: numeric(20,8)
  - total_supply: numeric(20,8)
  - token_program: varchar(44)
  - holder_count: integer
  - usd_price: numeric(20,8)
  - liquidity: numeric(20,8)
  - fdv: numeric(20,8)
  - mcap: numeric(20,8)
  - price_block_id: bigint
  - organic_score: numeric(10,4)
  - organic_score_label: varchar(50)
  - blockaid_rugpull: boolean default false
  - mint_authority_disabled: boolean
  - freeze_authority_disabled: boolean
  - top_holders_percentage: numeric(5,2)
  - dev_balance_percentage: numeric(5,2)
  - price_change_5m: numeric(10,4)
  - holder_change_5m: numeric(10,4)
  - liquidity_change_5m: numeric(10,4)
  - volume_change_5m: numeric(10,4)
  - buy_volume_5m: numeric(20,8) default 0
  - sell_volume_5m: numeric(20,8) default 0
  - buy_organic_volume_5m: numeric(20,8) default 0
  - sell_organic_volume_5m: numeric(20,8) default 0
  - num_buys_5m: integer default 0
  - num_sells_5m: integer default 0
  - num_traders_5m: integer default 0
  - price_change_1h: numeric(10,4)
  - holder_change_1h: numeric(10,4)
  - liquidity_change_1h: numeric(10,4)
  - volume_change_1h: numeric(10,4)
  - buy_volume_1h: numeric(20,8) default 0
  - sell_volume_1h: numeric(20,8) default 0
  - buy_organic_volume_1h: numeric(20,8) default 0
  - sell_organic_volume_1h: numeric(20,8) default 0
  - num_buys_1h: integer default 0
  - num_sells_1h: integer default 0
  - num_traders_1h: integer default 0
  - price_change_6h: numeric(10,4)
  - holder_change_6h: numeric(10,4)
  - liquidity_change_6h: numeric(10,4)
  - volume_change_6h: numeric(10,4)
  - buy_volume_6h: numeric(20,8) default 0
  - sell_volume_6h: numeric(20,8) default 0
  - buy_organic_volume_6h: numeric(20,8) default 0
  - sell_organic_volume_6h: numeric(20,8) default 0
  - num_buys_6h: integer default 0
  - num_sells_6h: integer default 0
  - num_traders_6h: integer default 0
  - price_change_24h: numeric(10,4)
  - holder_change_24h: numeric(10,4)
  - liquidity_change_24h: numeric(10,4)
  - volume_change_24h: numeric(10,4)
  - buy_volume_24h: numeric(20,8) default 0
  - sell_volume_24h: numeric(20,8) default 0
  - buy_organic_volume_24h: numeric(20,8) default 0
  - sell_organic_volume_24h: numeric(20,8) default 0
  - num_buys_24h: integer default 0
  - num_sells_24h: integer default 0
  - num_traders_24h: integer default 0
  - check_dexscreener: boolean default false
  - check_security: boolean default false
  - check_solana_rpc: boolean default false
  - history_ready: boolean default false
  - created_at: timestamp default now()
  - token_price_usd: numeric(20,8)         // compatibility (legacy)
  - token_price_sol: numeric(20,8)         // compatibility (legacy)
  - token_market_cap: numeric(20,2)        // compatibility (legacy)
  - token_volume_24h: numeric(20,2)        // compatibility (legacy)
  - token_supply: numeric(20,2)            // compatibility (legacy)
  - token_decimals: integer                // compatibility (legacy)
  - token_created_at: timestamp default now()
  - token_updated_at: timestamp default now() // also used to signal frontend updates

- trades
  - id: integer, PK
  - token_id: integer, FK → tokens(id)
  - signature: varchar(88), unique, not null
  - timestamp: bigint (epoch seconds), not null
  - readable_time: text
  - direction: varchar(10)  // buy | sell | withdraw
  - amount_tokens: numeric(20,8)
  - amount_sol: text        // stored as string for precision/compat
  - amount_usd: text        // stored as string for precision/compat
  - token_price_usd: text   // per-trade USD price
  - created_at: timestamp

- token_metrics_seconds
  - id: integer, PK
  - token_id: integer, FK → tokens(id)
  - ts: bigint (epoch seconds), not null
  - usd_price: double precision
  - liquidity: double precision
  - fdv: double precision
  - mcap: double precision
  - price_block_id: bigint
  - created_at: timestamp
  - UNIQUE(token_id, ts)

Constraints & Indexes

- tokens
  - UNIQUE(token_address)
  - CHECK(token_pair IS NULL OR token_pair <> token_address)
  - Indexes: 
    - idx_tokens_address(token_address)
    - idx_tokens_symbol(symbol)
    - idx_tokens_history_ready(history_ready)
    - idx_tokens_usd_price(usd_price)
    - idx_tokens_liquidity(liquidity)
    - idx_tokens_organic_score(organic_score)

- trades
  - UNIQUE(signature)
  - Indexes:
    - idx_trades_token_id(token_id)
    - idx_trades_signature(signature)
    - idx_trades_timestamp(timestamp)
    - idx_trades_direction(direction)

- token_metrics_seconds
  - UNIQUE(token_id, ts)
  - Indexes:
    - idx_metrics_token_id(token_id)
    - idx_metrics_ts(ts)

Data Flow Notes

- Analyzer (Jupiter): кладёт снапшоты метрик каждую секунду (если METRICS_SECONDS_ENABLED) c UPSERT по (token_id, ts).
- LiveTrades: сохраняет сделки, вычисляет per‑trade USD цену через текущую цену SOL, direction (buy/sell/withdraw).
- PriceResolver: агрегирует сделки → медианная цена в секунду → upsert в token_metrics_seconds; если mcap/fdv/liquidity в метриках нули —
  рассчитывает: mcap = price × circ_supply (фолбэк: token_supply/total_supply), fdv = price × total_supply (фолбэк: circ/token_supply),
  liquidity = tokens.liquidity (если > 0).
- TokensReader (для фронта): если в таблице tokens значения price/mcap/liquidity = 0, подставляет последнюю НЕ нулевую метрику из token_metrics_seconds (по каждому полю отдельно).
- Cleaner: удаляет “сироты” без валидной пары (token_pair IS NULL или = mint), старше N секунд, и без записей в trades/metrics.

Typical Queries

- Валидные пары (исключая ошибки, когда pair==mint):
  SELECT COUNT(*) FROM tokens WHERE token_pair IS NOT NULL AND token_pair <> '' AND token_pair <> token_address;

- Токены с полностью нулевыми секундными метриками:
  SELECT COUNT(DISTINCT token_id)
  FROM (
    SELECT token_id,
           MAX(COALESCE(usd_price,0)) max_usd,
           MAX(COALESCE(mcap,0))      max_mcap,
           MAX(COALESCE(liquidity,0)) max_liq
    FROM token_metrics_seconds GROUP BY token_id
  ) s
  WHERE max_usd=0 AND max_mcap=0 AND max_liq=0;

- Последняя не нулевая цена/кап по токену для списка:
  WITH lm_usd AS (
    SELECT DISTINCT ON (token_id) token_id, usd_price
    FROM token_metrics_seconds
    WHERE token_id=$1 AND usd_price>0 ORDER BY token_id, ts DESC
  ), lm_mcap AS (
    SELECT DISTINCT ON (token_id) token_id, mcap
    FROM token_metrics_seconds
    WHERE token_id=$1 AND mcap>0 ORDER BY token_id, ts DESC
  )
  SELECT COALESCE(NULLIF(t.usd_price,0), u.usd_price) price,
         COALESCE(NULLIF(t.mcap,0), m.mcap) mcap
  FROM tokens t LEFT JOIN lm_usd u ON u.token_id=t.id LEFT JOIN lm_mcap m ON m.token_id=t.id
  WHERE t.id=$1;

