# Crypto DB V3 Structure

## Overview
New database structure optimized for Jupiter API data and Helius trades history. The main goal is to simplify the structure by keeping all token data in a single table while maintaining trade history in a separate table.

## Tables

### 1. `tokens`
Main table containing all token information including stats and audit data.

#### Basic Token Info
```sql
id                  INTEGER PRIMARY KEY AUTOINCREMENT
token_address       TEXT UNIQUE NOT NULL     -- Jupiter: id
token_pair          TEXT                     -- Jupiter: firstPool.id
name                TEXT                     -- Jupiter: name
symbol              TEXT                     -- Jupiter: symbol
icon                TEXT                     -- Jupiter: icon
decimals            INTEGER                  -- Jupiter: decimals
dev                 TEXT                     -- Jupiter: dev
circ_supply         NUMERIC                  -- Jupiter: circSupply
total_supply        NUMERIC                  -- Jupiter: totalSupply
token_program       TEXT                     -- Jupiter: tokenProgram
holder_count        INTEGER                  -- Jupiter: holderCount
usd_price           NUMERIC                  -- Jupiter: usdPrice
liquidity          NUMERIC                  -- Jupiter: liquidity
fdv                NUMERIC                  -- Jupiter: fdv
mcap               NUMERIC                  -- Jupiter: mcap
price_block_id     INTEGER                  -- Jupiter: priceBlockId
organic_score      NUMERIC                  -- Jupiter: organicScore
organic_score_label TEXT                    -- Jupiter: organicScoreLabel
```

#### Audit Data
```sql
mint_authority_disabled   BOOLEAN    -- Jupiter: audit.mintAuthorityDisabled
freeze_authority_disabled BOOLEAN    -- Jupiter: audit.freezeAuthorityDisabled
top_holders_percentage   NUMERIC    -- Jupiter: audit.topHoldersPercentage
dev_balance_percentage   NUMERIC    -- Jupiter: audit.devBalancePercentage
```

#### Stats Data
Each period (5m, 1h, 6h, 24h) has its own set of columns:
```sql
-- Example for 5m period (repeated for 1h, 6h, 24h)
price_change_5m         NUMERIC    -- Jupiter: stats5m.priceChange
holder_change_5m       NUMERIC    -- Jupiter: stats5m.holderChange
liquidity_change_5m    NUMERIC    -- Jupiter: stats5m.liquidityChange
volume_change_5m       NUMERIC    -- Jupiter: stats5m.volumeChange
buy_volume_5m          NUMERIC    -- Jupiter: stats5m.buyVolume
sell_volume_5m         NUMERIC    -- Jupiter: stats5m.sellVolume
buy_organic_volume_5m  NUMERIC    -- Jupiter: stats5m.buyOrganicVolume
sell_organic_volume_5m NUMERIC    -- Jupiter: stats5m.sellOrganicVolume
num_buys_5m           INTEGER    -- Jupiter: stats5m.numBuys
num_sells_5m          INTEGER    -- Jupiter: stats5m.numSells
num_traders_5m        INTEGER    -- Jupiter: stats5m.numTraders
```

#### System Fields
```sql
check_jupiter  INTEGER DEFAULT 0     -- Counter for Jupiter API checks
history_ready  BOOLEAN DEFAULT 0     -- Whether Helius trade history is collected
```

### 2. `trades`
Trade history from Helius API.

```sql
id              INTEGER PRIMARY KEY AUTOINCREMENT
token_id        INTEGER NOT NULL
signature       TEXT UNIQUE NOT NULL
timestamp       INTEGER NOT NULL
readable_time   TEXT NOT NULL
direction       TEXT NOT NULL
amount_tokens   NUMERIC NOT NULL
amount_sol      TEXT NOT NULL
amount_usd      TEXT NOT NULL
token_price_usd TEXT DEFAULT '0.0000000000'
created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

## Indexes

### tokens
```sql
idx_tokens_address       ON tokens(token_address)
idx_tokens_pair         ON tokens(token_pair)
idx_tokens_check_jupiter ON tokens(check_jupiter)
idx_tokens_history_ready ON tokens(history_ready)
idx_tokens_price        ON tokens(usd_price)
idx_tokens_liquidity    ON tokens(liquidity)
idx_tokens_organic_score ON tokens(organic_score)
```

### trades
```sql
idx_trades_token_id    ON trades(token_id)
idx_trades_signature   ON trades(signature)
idx_trades_timestamp   ON trades(timestamp)
idx_trades_direction   ON trades(direction)
```

## Data Flow

1. **New Token Discovery**
   - Jupiter Scanner (V3) finds new tokens
   - Creates record in `tokens` table with basic info
   - Sets `check_jupiter = 0`
   - Sets `history_ready = false`

2. **Token Data Updates**
   - Jupiter Analyzer (V3) updates token data every 3 seconds
   - Updates all fields in `tokens` table
   - Increments `check_jupiter` (max 3)

3. **Trade History**
   - Helius API collects trade history
   - Saves to `trades` table
   - Sets `history_ready = true` when done

## Example Jupiter Data
```json
{
  "id": "8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR",
  "name": "Eureka",
  "symbol": "ERK",
  "decimals": 9,
  "dev": "EJFCRaCHXZHxWgfNzdaxzf53MYrtbCeWi1LmfTGC2Ev",
  "circSupply": 998265707.4637209,
  "totalSupply": 998265707.4637209,
  "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
  "firstPool": {
    "id": "Go6wx5drZ2Ekz44BoQ1QNCTonc1RwNxVCoGnwqFb1zb9"
  },
  "holderCount": 99,
  "audit": {
    "mintAuthorityDisabled": true,
    "freezeAuthorityDisabled": true,
    "topHoldersPercentage": 31.94,
    "devBalancePercentage": 6.87
  },
  "organicScore": 0,
  "organicScoreLabel": "low",
  "fdv": 8146.36,
  "mcap": 8146.36,
  "usdPrice": 0.0000081605,
  "priceBlockId": 373617998,
  "liquidity": 3718.83,
  "stats5m": {
    "priceChange": 0.50,
    "holderChange": 2.06,
    "liquidityChange": -0.36,
    "buyVolume": 0.89,
    "sellVolume": 0.90,
    "buyOrganicVolume": 0.89,
    "sellOrganicVolume": 0.90,
    "numBuys": 1,
    "numSells": 1,
    "numTraders": 1
  }
  // ... stats1h, stats6h, stats24h
}
```

## Example Trades Data
```json
[
  {
    "timestamp": 1759438522,
    "readable_time": "2025-10-02 22:55:22",
    "direction": "sell",
    "amount_tokens": 102510.25471584,
    "amount_sol": "0.00432753",
    "amount_usd": "0.98",
    "signature": "tGBdRCkCABaxR4FUcA19Ma26vJeMMZzvd8ppXPvnZpYq87Zqg3dqKKCrYxbHVqpiZ5YJ5RjhkRvp8sAeTNSkbbV"
  },
  {
    "timestamp": 1759415658,
    "readable_time": "2025-10-02 16:34:18",
    "direction": "buy",
    "amount_tokens": 103385.984670267,
    "amount_sol": "0.00438639",
    "amount_usd": "0.99",
    "signature": "23AwF4HWhiDBzqbo92cfT2wJTEbSenKuuwbvPqQsBcnkdwyGDkkaZgvB9DaME7qUVSKEunpp3TKd7SoLHW6vtKgA"
  },
  {
    "timestamp": 1759415429,
    "readable_time": "2025-10-02 16:30:29",
    "direction": "sell",
    "amount_tokens": 125205.621513331,
    "amount_sol": "0.00528587",
    "amount_usd": "1.20",
    "signature": "2aka8DHwHU56aSfr1kzx6Qf6XT6RRmyvPHSdcMPxEifLxPjMPhsXao5N96w5uWfMkfnqjenZGHTc2EkTiYxfnLwH"
  }
]
```