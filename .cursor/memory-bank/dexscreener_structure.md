# DexScreener Data Structure Documentation

## Table Structures

### 1. Main DexScreener Pairs Table
```sql
CREATE TABLE dexscreener_pairs (
    token_id INTEGER PRIMARY KEY,
    chain_id TEXT,
    dex_id TEXT,
    url TEXT,
    pair_address TEXT,
    price_native TEXT,
    price_usd TEXT,
    fdv NUMERIC,
    market_cap NUMERIC,
    pair_created_at TIMESTAMP,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 2. Base Token Information
```sql
CREATE TABLE dexscreener_base_token (
    token_id INTEGER PRIMARY KEY,
    address TEXT,
    name TEXT,
    symbol TEXT,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 3. Quote Token Information
```sql
CREATE TABLE dexscreener_quote_token (
    token_id INTEGER PRIMARY KEY,
    address TEXT,
    name TEXT,
    symbol TEXT,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 4. Transaction Data
```sql
CREATE TABLE dexscreener_txns (
    token_id INTEGER PRIMARY KEY,
    m5_buys INTEGER,
    m5_sells INTEGER,
    h1_buys INTEGER,
    h1_sells INTEGER,
    h6_buys INTEGER,
    h6_sells INTEGER,
    h24_buys INTEGER,
    h24_sells INTEGER,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 5. Volume Data
```sql
CREATE TABLE dexscreener_volume (
    token_id INTEGER PRIMARY KEY,
    h24 NUMERIC,
    h6 NUMERIC,
    h1 NUMERIC,
    m5 NUMERIC,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 6. Price Change Data
```sql
CREATE TABLE dexscreener_price_change (
    token_id INTEGER PRIMARY KEY,
    m5 NUMERIC,
    h1 NUMERIC,
    h6 NUMERIC,
    h24 NUMERIC,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 7. Liquidity Data
```sql
CREATE TABLE dexscreener_liquidity (
    token_id INTEGER PRIMARY KEY,
    usd NUMERIC,
    base NUMERIC,
    quote NUMERIC,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

## Process Flow

1. **Initial token creation** (from Jupiter):
   - Create record in `token_ids` with `token_address`
   - Create record in `tokens` with basic info
   - `token_pair` is initially NULL

2. **DexScreener analysis** (second step):
   - Update `token_pair` in `token_ids`
   - Update main data in `tokens` (price, liquidity, etc.)
   - Store detailed DexScreener data in separate tables

## Example Data Population

Given this DexScreener JSON data:
```json
{
  "schemaVersion": "1.0.0",
  "pairs": [
    {
      "chainId": "solana",
      "dexId": "pumpswap",
      "url": "https://dexscreener.com/solana/4frueud7z263sy3gtmrruqmfpcbui2nsc9ij4c63kms7",
      "pairAddress": "4FRUEUD7Z263sy3gtmrRUqMFPcBui2NsC9iJ4c63kMs7",
      "baseToken": {
        "address": "EK7Ms6Q9u3KZWBp5UeBUiC8Zb7CbGnFgxYmkTSvFSGyG",
        "name": "khole trade",
        "symbol": "KHOLE"
      },
      "quoteToken": {
        "address": "So11111111111111111111111111111111111111112",
        "name": "Wrapped SOL",
        "symbol": "SOL"
      },
      "priceNative": "0.000001268",
      "priceUsd": "0.0002995",
      "txns": {
        "m5": { "buys": 889, "sells": 741 },
        "h1": { "buys": 2678, "sells": 2005 },
        "h6": { "buys": 2678, "sells": 2005 },
        "h24": { "buys": 2678, "sells": 2005 }
      },
      "volume": {
        "h24": 5886534.71,
        "h6": 5886534.71,
        "h1": 5886534.71,
        "m5": 2140065.09
      },
      "priceChange": {
        "m5": 31.94,
        "h1": 261,
        "h6": 261,
        "h24": 261
      },
      "liquidity": {
        "usd": 332072.14,
        "base": 555859048,
        "quote": 701.08382
      },
      "fdv": 299557,
      "marketCap": 299557,
      "pairCreatedAt": 1759763767000
    }
  ]
}
```

Here's how it would be populated in our database:

```sql
-- 1. Update ONLY token_pair in main table (using token_id from previous step)
UPDATE token_ids 
SET token_pair = '4FRUEUD7Z263sy3gtmrRUqMFPcBui2NsC9iJ4c63kMs7'
WHERE id = 1;  -- Використовуємо token_id з першого кроку


-- 2. Insert DexScreener pairs data
INSERT OR REPLACE INTO dexscreener_pairs (
    token_id, chain_id, dex_id, url, pair_address,
    price_native, price_usd, fdv, market_cap, pair_created_at
) VALUES (
    1, 'solana', 'pumpswap', 
    'https://dexscreener.com/solana/4frueud7z263sy3gtmrruqmfpcbui2nsc9ij4c63kms7',
    '4FRUEUD7Z263sy3gtmrRUqMFPcBui2NsC9iJ4c63kMs7',
    '0.000001268', '0.0002995', 299557, 299557, 
    datetime(1759763767000, 'unixepoch')
);

-- 3. Insert base token data
INSERT OR REPLACE INTO dexscreener_base_token (token_id, address, name, symbol)
VALUES (1, 'EK7Ms6Q9u3KZWBp5UeBUiC8Zb7CbGnFgxYmkTSvFSGyG', 'khole trade', 'KHOLE');

-- 4. Insert quote token data
INSERT OR REPLACE INTO dexscreener_quote_token (token_id, address, name, symbol)
VALUES (1, 'So11111111111111111111111111111111111111112', 'Wrapped SOL', 'SOL');

-- 5. Insert transaction data
INSERT OR REPLACE INTO dexscreener_txns (
    token_id, m5_buys, m5_sells, h1_buys, h1_sells, 
    h6_buys, h6_sells, h24_buys, h24_sells
) VALUES (1, 889, 741, 2678, 2005, 2678, 2005, 2678, 2005);

-- 6. Insert volume data
INSERT OR REPLACE INTO dexscreener_volume (token_id, h24, h6, h1, m5)
VALUES (1, 5886534.71, 5886534.71, 5886534.71, 2140065.09);

-- 7. Insert price change data
INSERT OR REPLACE INTO dexscreener_price_change (token_id, m5, h1, h6, h24)
VALUES (1, 31.94, 261, 261, 261);

-- 9. Insert liquidity data
INSERT OR REPLACE INTO dexscreener_liquidity (token_id, usd, base, quote)
VALUES (1, 332072.14, 555859048, 701.08382);
```

## Key Fields Mapping

| DexScreener Field | Table | Column | Description |
|------------------|-------|--------|-------------|
| `pairs[0].pairAddress` | `token_ids` | `token_pair` | Trading pair address |
| `pairs[0].priceUsd` | `tokens` | `usd_price` | Current USD price |
| `pairs[0].liquidity.usd` | `tokens` | `liquidity` | Liquidity in USD |
| `pairs[0].fdv` | `tokens` | `fdv` | Fully Diluted Value |
| `pairs[0].marketCap` | `tokens` | `mcap` | Market Cap |
| `pairs[0].baseToken` | `dexscreener_base_token` | - | Base token info |
| `pairs[0].quoteToken` | `dexscreener_quote_token` | - | Quote token info |
| `pairs[0].txns` | `dexscreener_txns` | - | Transaction counts |
| `pairs[0].volume` | `dexscreener_volume` | - | Volume data |
| `pairs[0].priceChange` | `dexscreener_price_change` | - | Price changes |
| `pairs[0].liquidity` | `dexscreener_liquidity` | - | Detailed liquidity |

## Indexes
```sql
CREATE INDEX idx_dexscreener_pairs_timestamp ON dexscreener_pairs(timestamp);
CREATE INDEX idx_dexscreener_pairs_dex ON dexscreener_pairs(dex_id);
CREATE INDEX idx_dexscreener_pairs_chain ON dexscreener_pairs(chain_id);
```
