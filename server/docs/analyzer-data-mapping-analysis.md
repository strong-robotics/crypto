# АНАЛІЗ МАППІНГУ ДАНИХ АНАЛІЗАТОРА ТОКЕНІВ

## 🔍 ОГЛЯД ПРОЦЕСУ АНАЛІЗУ

### Як запускається аналіз:

```
1. Frontend/API -> /api/auto-scan/start
2. main.py -> auto_scan() 
3. _v1_analyzer_async.py -> add_tokens_for_analysis()
4. start_analysis_loop() -> run_analysis_cycle() (кожні 3 сек)
5. batch_analyze_tokens() -> 50 токенів за раз
6. save_analysis() -> Збереження в SQLite
7. broadcast_to_clients() -> Відправка на frontend
```

### Потік даних:
```
Jupiter API -> jupiter_data
DexScreener API -> dexscreener_data  
Solana RPC -> solana_rpc_data
    ↓
run_analysis_cycle()
    ↓
save_analysis()
    ↓
_save_dexscreener_data()
_save_solana_rpc_data()
_update_token_data_from_dexscreener()
    ↓
SQLite Database (14 таблиць)
```

---

## ✅ ПЕРЕВІРКА МАППІНГУ DEXSCREENER

### JSON Структура (analyse_dexscreener.json):
```json
{
  "pairs": [{
    "chainId": "solana",
    "dexId": "pumpswap",
    "url": "https://dexscreener.com/solana/...",
    "pairAddress": "4FRUEUD7Z263sy3gtmrRUqMFPcBui2NsC9iJ4c63kMs7",
    "priceNative": "0.000001268",
    "priceUsd": "0.0002995",
    "fdv": 299557,
    "marketCap": 299557,
    "pairCreatedAt": 1759763767000,
    
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
    }
  }]
}
```

### Маппінг в БД (_save_dexscreener_data):

#### ✅ ТАБЛИЦЯ: dexscreener_pairs
| JSON поле | DB поле | Тип | Статус |
|-----------|---------|-----|--------|
| `pairs[0].chainId` | `chain_id` | TEXT | ✅ OK |
| `pairs[0].dexId` | `dex_id` | TEXT | ✅ OK |
| `pairs[0].url` | `url` | TEXT | ✅ OK |
| `pairs[0].pairAddress` | `pair_address` | TEXT | ✅ OK |
| `pairs[0].priceNative` | `price_native` | TEXT | ✅ OK |
| `pairs[0].priceUsd` | `price_usd` | TEXT | ✅ OK |
| `pairs[0].fdv` | `fdv` | NUMERIC | ✅ OK |
| `pairs[0].marketCap` | `market_cap` | NUMERIC | ✅ OK |
| `pairs[0].pairCreatedAt` | `pair_created_at` | TIMESTAMP | ✅ OK (конвертується з мс) |

**Код маппінгу (рядки 465-481):**
```python
await self.conn.execute("""
    INSERT OR REPLACE INTO dexscreener_pairs (
        token_id, chain_id, dex_id, url, pair_address,
        price_native, price_usd, fdv, market_cap, pair_created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    token_id,
    pair.get('chainId'),           # ✅ chainId -> chain_id
    pair.get('dexId'),             # ✅ dexId -> dex_id
    pair.get('url'),               # ✅ url -> url
    pair.get('pairAddress'),       # ✅ pairAddress -> pair_address
    pair.get('priceNative'),       # ✅ priceNative -> price_native
    pair.get('priceUsd'),          # ✅ priceUsd -> price_usd
    pair.get('fdv'),               # ✅ fdv -> fdv
    pair.get('marketCap'),         # ✅ marketCap -> market_cap
    datetime.fromtimestamp(pair.get('pairCreatedAt', 0) / 1000).isoformat() 
))
```

#### ✅ ТАБЛИЦЯ: dexscreener_base_token
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `pairs[0].baseToken.address` | `address` | ✅ OK |
| `pairs[0].baseToken.name` | `name` | ✅ OK |
| `pairs[0].baseToken.symbol` | `symbol` | ✅ OK |

**Код маппінгу (рядки 484-495):**
```python
base_token = pair.get('baseToken', {})
if base_token:
    await self.conn.execute("""
        INSERT OR REPLACE INTO dexscreener_base_token (
            token_id, address, name, symbol
        ) VALUES (?, ?, ?, ?)
    """, (
        token_id,
        base_token.get('address'),    # ✅
        base_token.get('name'),       # ✅
        base_token.get('symbol')      # ✅
    ))
```

#### ✅ ТАБЛИЦЯ: dexscreener_quote_token
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `pairs[0].quoteToken.address` | `address` | ✅ OK |
| `pairs[0].quoteToken.name` | `name` | ✅ OK |
| `pairs[0].quoteToken.symbol` | `symbol` | ✅ OK |

#### ✅ ТАБЛИЦЯ: dexscreener_txns
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `pairs[0].txns.m5.buys` | `m5_buys` | ✅ OK |
| `pairs[0].txns.m5.sells` | `m5_sells` | ✅ OK |
| `pairs[0].txns.h1.buys` | `h1_buys` | ✅ OK |
| `pairs[0].txns.h1.sells` | `h1_sells` | ✅ OK |
| `pairs[0].txns.h6.buys` | `h6_buys` | ✅ OK |
| `pairs[0].txns.h6.sells` | `h6_sells` | ✅ OK |
| `pairs[0].txns.h24.buys` | `h24_buys` | ✅ OK |
| `pairs[0].txns.h24.sells` | `h24_sells` | ✅ OK |

**Код маппінгу (рядки 512-529):**
```python
txns = pair.get('txns', {})
if txns:
    await self.conn.execute("""
        INSERT OR REPLACE INTO dexscreener_txns (
            token_id, m5_buys, m5_sells, h1_buys, h1_sells,
            h6_buys, h6_sells, h24_buys, h24_sells
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        token_id,
        txns.get('m5', {}).get('buys'),    # ✅ m5.buys -> m5_buys
        txns.get('m5', {}).get('sells'),   # ✅ m5.sells -> m5_sells
        txns.get('h1', {}).get('buys'),    # ✅ h1.buys -> h1_buys
        txns.get('h1', {}).get('sells'),   # ✅ h1.sells -> h1_sells
        txns.get('h6', {}).get('buys'),    # ✅ h6.buys -> h6_buys
        txns.get('h6', {}).get('sells'),   # ✅ h6.sells -> h6_sells
        txns.get('h24', {}).get('buys'),   # ✅ h24.buys -> h24_buys
        txns.get('h24', {}).get('sells')   # ✅ h24.sells -> h24_sells
    ))
```

#### ✅ ТАБЛИЦЯ: dexscreener_volume
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `pairs[0].volume.h24` | `h24` | ✅ OK |
| `pairs[0].volume.h6` | `h6` | ✅ OK |
| `pairs[0].volume.h1` | `h1` | ✅ OK |
| `pairs[0].volume.m5` | `m5` | ✅ OK |

#### ✅ ТАБЛИЦЯ: dexscreener_price_change
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `pairs[0].priceChange.m5` | `m5` | ✅ OK |
| `pairs[0].priceChange.h1` | `h1` | ✅ OK |
| `pairs[0].priceChange.h6` | `h6` | ✅ OK |
| `pairs[0].priceChange.h24` | `h24` | ✅ OK |

#### ✅ ТАБЛИЦЯ: dexscreener_liquidity
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `pairs[0].liquidity.usd` | `usd` | ✅ OK |
| `pairs[0].liquidity.base` | `base` | ✅ OK |
| `pairs[0].liquidity.quote` | `quote` | ✅ OK |

---

## ✅ ПЕРЕВІРКА МАППІНГУ SOLANA RPC

### JSON Структура (analyse_solana_rpc.json):

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
      "rentEpoch": 18446744073709551615
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
  
  "largest_accounts": {
    "error": "HTTP 429"
  },
  
  "dev_activity": [
    {
      "blockTime": 1759764488,
      "confirmationStatus": "finalized",
      "err": null,
      "memo": null,
      "signature": "2qghAPPiVJRivhsKsjWEnTqv995BupQekEtoeqKccCpS4c9yoQkAcUiy6jcjLpRmuPU3WpAfXLtuo5VenK7HHzpW",
      "slot": 371598594
    }
  ]
}
```

### Маппінг в БД (_save_solana_rpc_data):

#### ✅ ТАБЛИЦЯ: solana_token_supply
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `token_supply.value.amount` | `amount` | ✅ OK |
| `token_supply.value.decimals` | `decimals` | ✅ OK |
| `token_supply.value.uiAmount` | `ui_amount` | ✅ OK |
| `token_supply.value.uiAmountString` | `ui_amount_string` | ✅ OK |
| `token_supply.context.slot` | `slot` | ✅ OK |
| `token_supply.context.apiVersion` | `api_version` | ✅ OK |

**Код маппінгу (рядки 583-601):**
```python
token_supply = solana_rpc_data.get('token_supply', {})
if token_supply and 'value' in token_supply:
    supply_value = token_supply['value']
    context = token_supply.get('context', {})
    await self.conn.execute("""
        INSERT OR REPLACE INTO solana_token_supply (
            token_id, amount, decimals, ui_amount, ui_amount_string,
            slot, api_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        token_id,
        supply_value.get('amount'),          # ✅ value.amount -> amount
        supply_value.get('decimals'),        # ✅ value.decimals -> decimals
        supply_value.get('uiAmount'),        # ✅ value.uiAmount -> ui_amount
        supply_value.get('uiAmountString'),  # ✅ value.uiAmountString -> ui_amount_string
        context.get('slot'),                 # ✅ context.slot -> slot
        context.get('apiVersion')            # ✅ context.apiVersion -> api_version
    ))
```

#### ✅ ТАБЛИЦЯ: solana_token_metadata
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `token_metadata.value.data.parsed.info.decimals` | `decimals` | ✅ OK |
| `token_metadata.value.data.parsed.info.freezeAuthority` | `freeze_authority` | ✅ OK |
| `token_metadata.value.data.parsed.info.isInitialized` | `is_initialized` | ✅ OK |
| `token_metadata.value.data.parsed.info.mintAuthority` | `mint_authority` | ✅ OK |
| `token_metadata.value.data.parsed.info.supply` | `supply` | ✅ OK |
| `token_metadata.value.data.program` | `program` | ✅ OK |
| `token_metadata.value.space` | `space` | ✅ OK |
| `token_metadata.value.executable` | `executable` | ✅ OK |
| `token_metadata.value.lamports` | `lamports` | ✅ OK |
| `token_metadata.value.owner` | `owner` | ✅ OK |
| `token_metadata.value.rentEpoch` | `rent_epoch` | ✅ OK |
| `token_metadata.context.slot` | `slot` | ✅ OK |
| `token_metadata.context.apiVersion` | `api_version` | ✅ OK |

**Код маппінгу (рядки 604-631):**
```python
token_metadata = solana_rpc_data.get('token_metadata', {})
if token_metadata and 'value' in token_metadata:
    metadata_value = token_metadata['value']
    context = token_metadata.get('context', {})
    parsed_info = metadata_value.get('data', {}).get('parsed', {}).get('info', {})
    
    await self.conn.execute("""
        INSERT OR REPLACE INTO solana_token_metadata (
            token_id, decimals, freeze_authority, is_initialized,
            mint_authority, supply, program, space, executable,
            lamports, owner, rent_epoch, slot, api_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        token_id,
        parsed_info.get('decimals'),        # ✅ info.decimals
        parsed_info.get('freezeAuthority'), # ✅ info.freezeAuthority
        parsed_info.get('isInitialized'),   # ✅ info.isInitialized
        parsed_info.get('mintAuthority'),   # ✅ info.mintAuthority
        parsed_info.get('supply'),          # ✅ info.supply
        metadata_value.get('data', {}).get('program'), # ✅ program
        metadata_value.get('space'),        # ✅ space
        metadata_value.get('executable'),   # ✅ executable
        metadata_value.get('lamports'),     # ✅ lamports
        metadata_value.get('owner'),        # ✅ owner
        metadata_value.get('rentEpoch'),    # ✅ rentEpoch
        context.get('slot'),                # ✅ slot
        context.get('apiVersion')           # ✅ apiVersion
    ))
```

#### ✅ ТАБЛИЦЯ: solana_recent_signatures
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `recent_signatures[].blockTime` | `block_time` | ✅ OK |
| `recent_signatures[].confirmationStatus` | `confirmation_status` | ✅ OK |
| `recent_signatures[].err` | `err` | ✅ OK |
| `recent_signatures[].memo` | `memo` | ✅ OK |
| `recent_signatures[].signature` | `signature` | ✅ OK |
| `recent_signatures[].slot` | `slot` | ✅ OK |

**Код маппінгу (рядки 634-656):**
```python
recent_signatures = solana_rpc_data.get('recent_signatures', [])
if isinstance(recent_signatures, list):
    # Спочатку видаляємо старі записи
    await self.conn.execute("""
        DELETE FROM solana_recent_signatures WHERE token_id = ?
    """, (token_id,))
    
    # Додаємо нові
    for sig in recent_signatures:
        if isinstance(sig, dict):
            await self.conn.execute("""
                INSERT INTO solana_recent_signatures (
                    token_id, block_time, confirmation_status, err, memo, signature, slot
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                token_id,
                sig.get('blockTime'),           # ✅ blockTime -> block_time
                sig.get('confirmationStatus'),  # ✅ confirmationStatus -> confirmation_status
                sig.get('err'),                 # ✅ err -> err
                sig.get('memo'),                # ✅ memo -> memo
                sig.get('signature'),           # ✅ signature -> signature
                sig.get('slot')                 # ✅ slot -> slot
            ))
```

#### ✅ ТАБЛИЦЯ: solana_dev_activity
Аналогічний маппінг як для recent_signatures (рядки 659-681)

#### ✅ ТАБЛИЦЯ: solana_largest_accounts
| JSON поле | DB поле | Статус |
|-----------|---------|--------|
| `largest_accounts.error` | `error_message` | ✅ OK |

**Код маппінгу (рядки 684-693):**
```python
largest_accounts = solana_rpc_data.get('largest_accounts', {})
if isinstance(largest_accounts, dict):
    await self.conn.execute("""
        INSERT OR REPLACE INTO solana_largest_accounts (
            token_id, error_message
        ) VALUES (?, ?)
    """, (
        token_id,
        largest_accounts.get('error')  # ✅ error -> error_message
    ))
```

---

## ✅ ПЕРЕВІРКА МАППІНГУ JUPITER

### JSON Структура (analyse_jupiter.json):
```json
{
  "id": "EK7Ms6Q9u3KZWBp5UeBUiC8Zb7CbGnFgxYmkTSvFSGyG",
  "name": "khole trade",
  "symbol": "KHOLE",
  "decimals": 6,
  "dev": "BrhPVH7T39j3wBdMAwiUqHY3w23ZP6UCxvJDmK46fv71",
  "circSupply": 999998082.690627,
  "totalSupply": 999998082.690627,
  "holderCount": 626,
  "fdv": 302483.4206593829,
  "mcap": 302483.4206593829,
  "usdPrice": 0.00030248400061479245,
  "liquidity": 166381.18433142384
}
```

### ⚠️ ПРОБЛЕМА: Jupiter дані НЕ зберігаються окремо!

Jupiter дані використовуються для:
1. ✅ Оновлення `tokens` таблиці (через `_update_token_data_from_dexscreener`)
2. ✅ Витягування `dev_address` (функція `_extract_dev_from_jupiter`)
3. ❌ **НЕ зберігаються** в окрему таблицю

**Рекомендація:** Створити таблицю `jupiter_token_data` для збереження всіх Jupiter даних:
```sql
CREATE TABLE jupiter_token_data (
    token_id INTEGER PRIMARY KEY,
    dev_address TEXT,
    circ_supply NUMERIC,
    total_supply NUMERIC,
    holder_count INTEGER,
    organic_score NUMERIC,
    organic_score_label TEXT,
    audit_mint_authority_disabled BOOLEAN,
    audit_freeze_authority_disabled BOOLEAN,
    audit_top_holders_percentage NUMERIC,
    stats_5m_price_change NUMERIC,
    stats_5m_holder_change NUMERIC,
    stats_5m_liquidity_change NUMERIC,
    stats_5m_volume_change NUMERIC,
    stats_5m_buy_volume NUMERIC,
    stats_5m_sell_volume NUMERIC,
    stats_5m_num_buys INTEGER,
    stats_5m_num_sells INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

---

## 📊 ПІДСУМОК МАППІНГУ

### ✅ ПРАЦЮЄ ПРАВИЛЬНО:
1. **DexScreener** - 7 таблиць, всі поля маппяться правильно
2. **Solana RPC** - 5 таблиць, всі поля маппяться правильно
3. **Типи даних** - правильна конвертація (timestamp з мс, вкладені об'єкти)

### ⚠️ ПОТРЕБУЄ ПОКРАЩЕННЯ:
1. **Jupiter дані** - не зберігаються в окрему таблицю (втрачаються важливі дані)
2. **Batch аналіз** - працює, але може покращитись логування

### ✅ АРХІТЕКТУРА ПРАВИЛЬНА:
- SQLite з WAL mode
- Правильні FOREIGN KEY
- Індекси для швидкості
- INSERT OR REPLACE для upsert
- Транзакції через db_lock

---

## 🚀 РЕКОМЕНДАЦІЇ

### 1. Додати таблицю для Jupiter даних
Створити `jupiter_token_data` таблицю та функцію `_save_jupiter_data()`

### 2. Покращити логування
Додати детальне логування маппінгу полів для діагностики

### 3. Додати валідацію
Перевіряти, чи всі критичні поля присутні перед збереженням

### 4. Створити тестовий endpoint
Додати в `main.py` endpoint для тестування аналізу з реальними прикладами

### 5. Міграція на MySQL
Якщо потрібна міграція на MySQL:
- Замінити `?` на `%s` в SQL запитах
- Замінити `aiosqlite` на `aiomysql` або `asyncpg`
- Оновити типи даних (TEXT -> VARCHAR, NUMERIC -> DECIMAL)

---

## 🎯 СТАТУС

| Компонент | Статус | Примітки |
|-----------|--------|----------|
| DexScreener маппінг | ✅ OK | Всі 7 таблиць працюють |
| Solana RPC маппінг | ✅ OK | Всі 5 таблиць працюють |
| Jupiter маппінг | ⚠️ PARTIAL | Використовується, але не зберігається |
| Batch аналіз | ✅ OK | 50 токенів за раз |
| Rate limiting | ✅ OK | 1 сек між запитами |
| WebSocket broadcast | ✅ OK | Працює правильно |
| База даних SQLite | ✅ OK | 14 таблиць готові |

**ВИСНОВОК:** Маппінг даних працює правильно для DexScreener та Solana RPC. Jupiter дані потребують окремої таблиці для повного збереження всіх полів.

