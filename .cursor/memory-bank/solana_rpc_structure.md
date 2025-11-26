# Solana RPC Data Structure Documentation

## Table Structures

### 1. Solana Token Supply
```sql
CREATE TABLE solana_token_supply (
    token_id INTEGER PRIMARY KEY,
    amount TEXT,
    decimals INTEGER,
    ui_amount NUMERIC,
    ui_amount_string TEXT,
    slot INTEGER,
    api_version TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 2. Solana Token Metadata
```sql
CREATE TABLE solana_token_metadata (
    token_id INTEGER PRIMARY KEY,
    decimals INTEGER,
    freeze_authority TEXT,
    is_initialized BOOLEAN,
    mint_authority TEXT,
    supply TEXT,
    program TEXT,
    space INTEGER,
    executable BOOLEAN,
    lamports INTEGER,
    owner TEXT,
    rent_epoch TEXT,
    slot INTEGER,
    api_version TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 3. Solana Recent Signatures
```sql
CREATE TABLE solana_recent_signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER,
    block_time INTEGER,
    confirmation_status TEXT,
    err TEXT,
    memo TEXT,
    signature TEXT,
    slot INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 4. Solana Dev Activity
```sql
CREATE TABLE solana_dev_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER,
    block_time INTEGER,
    confirmation_status TEXT,
    err TEXT,
    memo TEXT,
    signature TEXT,
    slot INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### 5. Solana Largest Accounts
```sql
CREATE TABLE solana_largest_accounts (
    token_id INTEGER PRIMARY KEY,
    error_message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

## Process Flow

1. **Initial token creation** (from Jupiter):
   - Create record in `token_ids` with `token_address`
   - Create record in `tokens` with basic info

2. **Solana RPC analysis** (second step):
   - Use token_id from previous step (no need to search by token_address)
   - Store detailed Solana RPC data in separate tables
   - Do NOT update main tables (`tokens`, `token_audit`, etc.)
   - Data will be used later for Security analysis

## Example Data Population

Given this Solana RPC JSON data:
```json
{
  "token_supply": {
    "context": {
      "apiVersion": "2.3.6",
      "slot": 371598587
    },
    "value": {
      "amount": "999998268315339",
      "decimals": 6,
      "uiAmount": 999998268.315339,
      "uiAmountString": "999998268.315339"
    }
  },
  "token_metadata": {
    "context": {
      "apiVersion": "2.3.6",
      "slot": 371598587
    },
    "value": {
      "data": {
        "parsed": {
          "info": {
            "decimals": 6,
            "freezeAuthority": null,
            "isInitialized": true,
            "mintAuthority": null,
            "supply": "999998268315339"
          },
          "type": "mint"
        },
        "program": "spl-token",
        "space": 82
      },
      "executable": false,
      "lamports": 1461600,
      "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
      "rentEpoch": 18446744073709551615,
      "space": 82
    }
  },
  "recent_signatures": [
    {
      "blockTime": 1759764486,
      "confirmationStatus": "finalized",
      "err": null,
      "memo": null,
      "signature": "4ZnS3UMVQp1tGPXE1P2hnkYGUDr8XkfHmkto7aADG6ktoqmQ55j9yVFakVEN5w9vxMCk7dbsHBzqhSJGYy2v5Xjx",
      "slot": 371598587
    }
  ],
  "dev_activity": [
    {
      "blockTime": 1759764488,
      "confirmationStatus": "finalized",
      "err": null,
      "memo": null,
      "signature": "2qghAPPiVJRivhsKsjWEnTqv995BupQekEtoeqKccCpS4c9yoQkAcUiy6jcjLpRmuPU3WpAfXLtuo5VenK7HHzpW",
      "slot": 371598594
    }
  ],
  "largest_accounts": {
    "error": "HTTP 429"
  }
}
```

Here's how it would be populated in our database:

```sql
-- 1. Insert Token Supply data
INSERT OR REPLACE INTO solana_token_supply (
    token_id, amount, decimals, ui_amount, ui_amount_string, slot, api_version
) VALUES (
    1, '999998268315339', 6, 999998268.315339, '999998268.315339', 
    371598587, '2.3.6'
);

-- 2. Insert Token Metadata data
INSERT OR REPLACE INTO solana_token_metadata (
    token_id, decimals, freeze_authority, is_initialized, mint_authority,
    supply, program, space, executable, lamports, owner, rent_epoch, slot, api_version
) VALUES (
    1, 6, NULL, true, NULL, '999998268315339', 'spl-token', 82,
    false, 1461600, 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
    '18446744073709551615', 371598587, '2.3.6'
);

-- 3. Insert Recent Signatures (for each signature)
INSERT INTO solana_recent_signatures (
    token_id, block_time, confirmation_status, err, memo, signature, slot
) VALUES 
(1, 1759764486, 'finalized', NULL, NULL, '4ZnS3UMVQp1tGPXE1P2hnkYGUDr8XkfHmkto7aADG6ktoqmQ55j9yVFakVEN5w9vxMCk7dbsHBzqhSJGYy2v5Xjx', 371598587),
(1, 1759764486, 'finalized', NULL, NULL, '6556ciaPT5kKGp8UpBTX6rTVNaPwbTAYsFyuoVYWc6RN7yXkAGDVYMPjJKg4Utm1r8EUBNBEidBz7MbkynDVQER', 371598587),
(1, 1759764485, 'finalized', NULL, NULL, 'DhAkynorzgiKJP6BJoVobVv3kd31DGdwMVraoxQfkztJvhpaVBL8heZ2QBzBsrMz7EEpjLiHisdgdNXwk1uzZH8', 371598586);
-- ... continue for all signatures

-- 4. Insert Dev Activity (for each activity)
INSERT INTO solana_dev_activity (
    token_id, block_time, confirmation_status, err, memo, signature, slot
) VALUES 
(1, 1759764488, 'finalized', NULL, NULL, '2qghAPPiVJRivhsKsjWEnTqv995BupQekEtoeqKccCpS4c9yoQkAcUiy6jcjLpRmuPU3WpAfXLtuo5VenK7HHzpW', 371598594),
(1, 1759764488, 'finalized', NULL, NULL, '3aLXeWZoDSwBVX6WkjwYHKBFA9eLbL9MYZw1V4BLtrqXoSHir6qSVJu12NZzdsSdTUevyov37ywrFcwARuNi3nxw', 371598592);
-- ... continue for all dev activities

-- 5. Insert Largest Accounts error
INSERT OR REPLACE INTO solana_largest_accounts (token_id, error_message)
VALUES (1, 'HTTP 429');
```

## Key Fields Mapping

| Solana RPC Field | Table | Column | Description |
|------------------|-------|--------|-------------|
| `token_supply.value.amount` | `solana_token_supply` | `amount` | Total token supply |
| `token_supply.value.decimals` | `solana_token_supply` | `decimals` | Token decimals |
| `token_metadata.value.data.parsed.info.mintAuthority` | `solana_token_metadata` | `mint_authority` | Mint authority (NULL = good) |
| `token_metadata.value.data.parsed.info.freezeAuthority` | `solana_token_metadata` | `freeze_authority` | Freeze authority (NULL = good) |
| `token_metadata.value.data.parsed.info.isInitialized` | `solana_token_metadata` | `is_initialized` | Token initialization status |
| `recent_signatures[]` | `solana_recent_signatures` | - | Recent transaction signatures |
| `dev_activity[]` | `solana_dev_activity` | - | Developer activity signatures |
| `largest_accounts.error` | `solana_largest_accounts` | `error_message` | Error message for largest accounts |

## Security Analysis Usage

These tables will be used later for:
- **Honeypot detection**: Check `mint_authority` and `freeze_authority`
- **Activity analysis**: Count recent signatures and dev activity
- **Error rate calculation**: Analyze error rates in transactions
- **Supply verification**: Compare with other sources

## Indexes
```sql
CREATE INDEX idx_solana_supply_timestamp ON solana_token_supply(timestamp);
CREATE INDEX idx_solana_metadata_timestamp ON solana_token_metadata(timestamp);
CREATE INDEX idx_solana_signatures_timestamp ON solana_recent_signatures(timestamp);
CREATE INDEX idx_solana_signatures_block_time ON solana_recent_signatures(block_time);
CREATE INDEX idx_solana_dev_activity_timestamp ON solana_dev_activity(timestamp);
CREATE INDEX idx_solana_dev_activity_block_time ON solana_dev_activity(block_time);
```
