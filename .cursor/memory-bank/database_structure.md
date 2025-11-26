# Database Structure Documentation

## Table Structures

### 1. Primary Reference Table (token_ids)
```sql
CREATE TABLE token_ids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    token_address TEXT UNIQUE NOT NULL,
    token_pair TEXT UNIQUE NOT NULL,

    is_honeypot BOOLEAN,
    pattern TEXT,
    "mintAuthorityDisabled": BOOLEAN DEFAULT 0,    "freezeAuthorityDisabled": BOOLEAN DEFAULT 0,

    "topHoldersPercentage": 31.9480856914939, float
    "devBalancePercentage": 6.8763644264622465 float
    "holderCount": 99, int 

    "fdv": 8176.158771211337,  float
    "mcap": 8176.158771211337,float
    "usdPrice": 0.0000081903632570775, float
    "priceBlockId": 373605546, float
    "liquidity": 3732.424047481944, float
    
    history_ready BOOLEAN DEFAULT 0,
    check_jupiter INTEGER DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Pattern Field Description:**
- `pattern`: AI-assigned classification based on token behavior analysis
  - Values: `pattern_1`, `pattern_2`, `pattern_3`, etc.
  - Determined after analyzing 300+ tokens by AI model
  - Used for:
    - Identifying similar scam tokens (e.g., tokens that dump within 1 hour)
    - Determining entry amount strategies
    - Predicting token lifespan
    - Risk assessment based on historical patterns

### 2. Main Token Information (tokens)
```sql
CREATE TABLE tokens (
    token_id INTEGER PRIMARY KEY,
    name TEXT,
    symbol TEXT,
    icon TEXT,
    decimals INTEGER,
    twitter TEXT,
    dev TEXT,
    circ_supply NUMERIC,
    total_supply NUMERIC,
    token_program TEXT,
    launchpad TEXT,
    holder_count INTEGER,
    usd_price NUMERIC,
    liquidity NUMERIC,
    fdv NUMERIC,
    mcap NUMERIC,
    bonding_curve NUMERIC,
    price_block_id INTEGER,
    organic_score NUMERIC,
    organic_score_label TEXT,
    updated_at TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 3. Statistics Tables

```sql
-- 5-minute statistics
CREATE TABLE token_stats_5m (
    token_id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    price_change NUMERIC,
    liquidity_change NUMERIC,
    buy_volume NUMERIC,
    sell_volume NUMERIC,
    buy_organic_volume NUMERIC,
    num_buys INTEGER,
    num_sells INTEGER,
    num_traders INTEGER,
    num_net_buyers INTEGER,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);

-- 1-hour statistics (identical structure)
CREATE TABLE token_stats_1h (
    token_id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    price_change NUMERIC,
    liquidity_change NUMERIC,
    buy_volume NUMERIC,
    sell_volume NUMERIC,
    buy_organic_volume NUMERIC,
    num_buys INTEGER,
    num_sells INTEGER,
    num_traders INTEGER,
    num_net_buyers INTEGER,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);

-- 6-hour statistics (identical structure)
CREATE TABLE token_stats_6h (
    token_id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    price_change NUMERIC,
    liquidity_change NUMERIC,
    buy_volume NUMERIC,
    sell_volume NUMERIC,
    buy_organic_volume NUMERIC,
    num_buys INTEGER,
    num_sells INTEGER,
    num_traders INTEGER,
    num_net_buyers INTEGER,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);

-- 24-hour statistics (identical structure)
CREATE TABLE token_stats_24h (
    token_id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    price_change NUMERIC,
    liquidity_change NUMERIC,
    buy_volume NUMERIC,
    sell_volume NUMERIC,
    buy_organic_volume NUMERIC,
    num_buys INTEGER,
    num_sells INTEGER,
    num_traders INTEGER,
    num_net_buyers INTEGER,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 4. Audit Information
```sql
CREATE TABLE token_audit (
    token_id INTEGER PRIMARY KEY,
    mint_authority_disabled BOOLEAN,
    freeze_authority_disabled BOOLEAN,
    top_holders_percentage NUMERIC,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 5. First Pool Information
```sql
CREATE TABLE token_first_pool (
    token_id INTEGER PRIMARY KEY,
    pool_id TEXT,
    created_at TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 6. Token Tags
```sql
CREATE TABLE token_tags (
    token_id INTEGER,
    tag TEXT,
    PRIMARY KEY (token_id, tag),
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 7. Trades (Transaction History)
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL,
    signature TEXT UNIQUE NOT NULL,
    timestamp INTEGER NOT NULL,
    readable_time TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount_tokens NUMERIC NOT NULL,
    amount_sol TEXT NOT NULL,
    amount_usd TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);

CREATE INDEX idx_trades_token_id ON trades(token_id);
CREATE INDEX idx_trades_signature ON trades(signature);
CREATE INDEX idx_trades_timestamp ON trades(timestamp);
CREATE INDEX idx_trades_direction ON trades(direction);
```

**Field Descriptions:**
- `signature`: Unique transaction hash (prevents duplicates)
- `token_id`: Reference to token_ids table
- `timestamp`: Unix timestamp for sorting
- `readable_time`: Human-readable datetime
- `direction`: Transaction type (buy/sell/withdraw)
- `amount_tokens`: Number of tokens traded
- `amount_sol`: SOL amount as TEXT (formatted string like "0.00432753")
- `amount_usd`: USD amount as TEXT (formatted string like "0.98")

## Data Population Process

### Step 1: Initial Token Creation (Jupiter)
1. Create record in `token_ids` with `token_address`
2. Create record in `tokens` with basic info
3. Get `token_id` (e.g., id = 1) for future updates

### Step 2: Analysis Updates (DexScreener, Jupiter, Solana RPC)
- Use `token_id` from Step 1 (no need to search by `token_address`)
- Update `token_pair` in `token_ids`
- Update main data in `tokens` (Jupiter analysis)
- Store detailed data in separate tables (DexScreener, Solana RPC)

### Step 3: Security Analysis
- Use data from all previous steps
- Update Security results in `token_ids` (is_honeypot, lp_owner, dev_address)

## Example Data Population

Given this JSON data:
```json
{
    "id": "99TKEGzrCypdx484ciCqw38PmAtKBcAZQuMcq3fRBodr",
    "name": "AI Bubble",
    "symbol": "AIBubble",
    "icon": "https://axiomtrading.sfo3.cdn.digitaloceanspaces.com/4iH9QpugJvfzTJKJcDqMHrzfKbgaRjxgkxxKwXuVpump.webp",
    "decimals": 6,
    "twitter": "https://twitter.com/zerohedge/status/1975310131656065408",
    "dev": "3QEvxoxv68zcCQV26CengvzC56qXf4PVHsFAMBhxtVfZ",
    "circSupply": 1000000000,
    "totalSupply": 1000000000,
    "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "launchpad": "pump.fun",
    "firstPool": {
      "id": "99TKEGzrCypdx484ciCqw38PmAtKBcAZQuMcq3fRBodr",
      "createdAt": "2025-10-06T21:20:59Z"
    },
    "holderCount": 2,
    "audit": {
      "mintAuthorityDisabled": true,
      "freezeAuthorityDisabled": true,
      "topHoldersPercentage": 8.4577647058824
    },
    "organicScore": 0,
    "organicScoreLabel": "low",
    "tags": [
      "unknown"
    ],
    "fdv": 6565.744293276485,
    "mcap": 6565.744293276485,
    "usdPrice": 6.565744293276485e-06,
    "priceBlockId": 371652060,
    "liquidity": 7045.384046079858,
    "stats5m": {
      "priceChange": -11.49923976004795,
      "liquidityChange": -11.498749762899445,
      "buyVolume": 960.0049909347171,
      "sellVolume": 936.2080144407125,
      "buyOrganicVolume": 23.483474378456034,
      "numBuys": 2,
      "numSells": 2,
      "numTraders": 2,
      "numNetBuyers": 1
    },
    "stats1h": {
      "priceChange": -11.49923976004795,
      "liquidityChange": -11.498749762899445,
      "buyVolume": 960.0049909347171,
      "sellVolume": 936.2080144407125,
      "buyOrganicVolume": 23.483474378456034,
      "numBuys": 2,
      "numSells": 2,
      "numTraders": 2,
      "numNetBuyers": 1
    },
    "stats6h": {
      "priceChange": -11.49923976004795,
      "liquidityChange": -11.498749762899445,
      "buyVolume": 960.0049909347171,
      "sellVolume": 936.2080144407125,
      "buyOrganicVolume": 23.483474378456034,
      "numBuys": 2,
      "numSells": 2,
      "numTraders": 2,
      "numNetBuyers": 1
    },
    "stats24h": {
      "priceChange": -11.49923976004795,
      "liquidityChange": -11.498749762899445,
      "buyVolume": 960.0049909347171,
      "sellVolume": 936.2080144407125,
      "buyOrganicVolume": 23.483474378456034,
      "numBuys": 2,
      "numSells": 2,
      "numTraders": 2,
      "numNetBuyers": 1
    },
    "bondingCurve": 0.4509728911864852,
    "updatedAt": "2025-10-06T21:21:21.133830023Z"
}
```

Here's how it would be populated in our database:

```sql
-- 1. Create token_id entry (token_pair буде заповнено пізніше іншим аналізатором)
INSERT INTO token_ids (token_address, token_pair) 
VALUES (
    '99TKEGzrCypdx484ciCqw38PmAtKBcAZQuMcq3fRBodr',
    NULL  -- Буде оновлено пізніше через UPDATE
) RETURNING id;  -- Let's say this returns id = 1

-- Пізніше, коли аналізатор знайде пару (використовуємо token_id):
UPDATE token_ids 
SET token_pair = 'PAIR_ADDRESS_HERE' 
WHERE id = 1;  -- Використовуємо token_id з першого кроку

-- Пізніше, коли Security аналіз завершиться (використовуємо token_id):
UPDATE token_ids 
SET 
    is_honeypot = false,
    lp_owner = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
    dev_address = 'BrhPVH7T39j3wBdMAwiUqHY3w23ZP6UCxvJDmK46fv71',
    security_analyzed_at = CURRENT_TIMESTAMP
WHERE id = 1;  -- Використовуємо token_id з першого кроку

-- 2. Insert main token information
INSERT INTO tokens (
    token_id, name, symbol, icon, decimals, twitter, dev,
    circ_supply, total_supply, token_program, launchpad,
    holder_count, usd_price, liquidity, fdv, mcap,
    organic_score, organic_score_label, price_block_id, updated_at
) VALUES (
    1,
    'AI Bubble',
    'AIBubble',
    'https://axiomtrading.sfo3.cdn.digitaloceanspaces.com/4iH9QpugJvfzTJKJcDqMHrzfKbgaRjxgkxxKwXuVpump.webp',
    6,
    'https://twitter.com/zerohedge/status/1975310131656065408',
    '3QEvxoxv68zcCQV26CengvzC56qXf4PVHsFAMBhxtVfZ',
    1000000000,
    1000000000,
    'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
    'pump.fun',
    2,
    6.565744293276485e-06,
    7045.384046079858,
    6565.744293276485,
    6565.744293276485,
    0,
    'low',
    371652060,
    CURRENT_TIMESTAMP
);

-- 3. Insert 24h statistics
INSERT INTO token_stats_24h (
    token_id, timestamp,
    price_change, liquidity_change,
    buy_volume, sell_volume, buy_organic_volume,
    num_buys, num_sells, num_traders, num_net_buyers
) VALUES (
    1,
    CURRENT_TIMESTAMP,
    -11.49923976004795,
    -11.498749762899445,
    960.0049909347171,
    936.2080144407125,
    23.483474378456034,
    2,
    2,
    2,
    1
);

-- 4. Insert audit information
INSERT INTO token_audit (
    token_id,
    mint_authority_disabled,
    freeze_authority_disabled,
    top_holders_percentage
) VALUES (
    1,
    true,
    true,
    8.4577647058824
);

-- 5. Insert tags
INSERT INTO token_tags (token_id, tag) 
VALUES (1, 'unknown');
```

## Querying Example
```sql
-- Get complete token information
SELECT 
    ti.token_address,
    ti.token_pair,
    ti.is_honeypot,
    ti.lp_owner,
    ti.dev_address,
    ti.security_analyzed_at,
    t.name,
    t.symbol,
    t.usd_price,
    t.liquidity,
    t.fdv,
    t.mcap,
    t.organic_score,
    t.organic_score_label,
    s24.price_change as price_change_24h,
    s24.buy_volume as volume_24h,
    a.top_holders_percentage,
    GROUP_CONCAT(tt.tag) as tags
FROM token_ids ti
JOIN tokens t ON t.token_id = ti.id
JOIN token_stats_24h s24 ON s24.token_id = ti.id
JOIN token_audit a ON a.token_id = ti.id
LEFT JOIN token_tags tt ON tt.token_id = ti.id
WHERE ti.token_address = '99TKEGzrCypdx484ciCqw38PmAtKBcAZQuMcq3fRBodr'
GROUP BY ti.id;
```

## Indexes
```sql
-- Primary reference indexes
CREATE INDEX idx_token_ids_address ON token_ids(token_address);
CREATE INDEX idx_token_ids_pair ON token_ids(token_pair);
CREATE INDEX idx_token_ids_created ON token_ids(created_at);
CREATE INDEX idx_token_ids_honeypot ON token_ids(is_honeypot);
CREATE INDEX idx_token_ids_pattern ON token_ids(pattern);
CREATE INDEX idx_token_ids_security_analyzed ON token_ids(security_analyzed_at);

-- Performance indexes
CREATE INDEX idx_tokens_price ON tokens(usd_price);
CREATE INDEX idx_tokens_liquidity ON tokens(liquidity);
CREATE INDEX idx_tokens_updated ON tokens(updated_at);
CREATE INDEX idx_tokens_organic_score ON tokens(organic_score);

-- Statistics indexes
CREATE INDEX idx_stats_5m_timestamp ON token_stats_5m(timestamp);
CREATE INDEX idx_stats_1h_timestamp ON token_stats_1h(timestamp);
CREATE INDEX idx_stats_6h_timestamp ON token_stats_6h(timestamp);
CREATE INDEX idx_stats_24h_timestamp ON token_stats_24h(timestamp);
```
