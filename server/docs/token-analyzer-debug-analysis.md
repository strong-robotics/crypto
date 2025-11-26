# ДЕТАЛЬНИЙ АНАЛІЗ ПРОБЛЕМ АНАЛІЗАТОРА ТОКЕНІВ

## 🔍 ВИЯВЛЕНІ КРИТИЧНІ ПРОБЛЕМИ

### 1. **JUPITER API BATCH ЗАПИТ - ПРАВИЛЬНИЙ** ✅

**Локація:** `batch_analyze_tokens()` (рядки 55-107)

**Статус:** ✅ ПРАВИЛЬНИЙ
```python
# ✅ ПРАВИЛЬНИЙ URL для batch запиту
url = f"https://lite-api.jup.ag/tokens/v2/search?query={query_string}"
```

**Підтвердження з документації Jupiter:**
- URL: https://dev.jup.ag/docs/token-api/v2
- Endpoint: `https://lite-api.jup.ag/tokens/v2/search?query=`
- Підтримує: Comma-separate to search for multiple
- Ліміт: 100 mint addresses in query
- Приклад: `https://lite-api.jup.ag/tokens/v2/search?query=So11111111111111111111111111111111111111112`

**Висновок:** Batch аналіз Jupiter API працює правильно!

### 2. **ПРОБЛЕМА З ТИПАМИ ДАНИХ У BROADCAST** ❌

**Локація:** `_broadcast_token_update()` (рядки 736-819)

**Проблема:**
```python
# ❌ НЕПРАВИЛЬНИЙ ТИП ПАРАМЕТРА
async def _broadcast_token_update(self, token_id: int):
    # Але в run_analysis_cycle() передається string
    await self._broadcast_token_update(token_id)  # token_id = string
```

**Аналіз:**
- Функція очікує `int`, але отримує `str`
- Це призводить до помилок в SQL запитах
- Broadcast не працює

### 3. **ПРОБЛЕМА З ANALYSIS_TIME РОЗРАХУНКОМ** ❌

**Локація:** `run_analysis_cycle()` (рядок 875)

**Проблема:**
```python
# ❌ НЕПРАВИЛЬНИЙ РОЗРАХУНОК ЧАСУ
'analysis_time': f"{time.time() - time.time():.2f}s",  # Завжди 0.00s
```

**Аналіз:**
- `time.time() - time.time()` завжди дорівнює 0
- Потрібно зберігати `start_time` перед аналізом

### 4. **ПРОБЛЕМА З HONEYPOT CHECK** ❌

**Локація:** `run_analysis_cycle()` (рядок 883)

**Проблема:**
```python
# ❌ ВИКЛИК ПРОСТОГО МЕТОДУ ЗАМІСТЬ ДЕТАЛЬНОГО
'honeypot_check': self._check_honeypot(jupiter_data),
```

**Аналіз:**
- Метод `_check_honeypot()` існує, але він простий
- Не використовується більш детальний `_honeypot_with_fallback()`
- Втрачаються важливі дані безпеки

### 5. **ПРОБЛЕМА З LP_OWNER ТА DEV_ADDRESS** ❌

**Локація:** `run_analysis_cycle()` (рядки 884-885)

**Проблема:**
```python
# ❌ НЕПРАВИЛЬНІ ПАРАМЕТРИ
'lp_owner': self._get_lp_owner(solana_rpc_data),
'dev_address': self._get_dev_address(jupiter_data)
```

**Аналіз:**
- `_get_lp_owner()` очікує `pair_address`, а не `solana_rpc_data`
- `_get_dev_address()` правильний
- LP owner не визначається правильно

## 🗄️ СТРУКТУРА БАЗИ ДАНИХ АНАЛІЗАТОРА

### **ОСНОВНІ ТАБЛИЦІ (з _v1_new_tokens_jupiter_async.py)**

#### 1. **token_ids** (основна таблиця)
```sql
CREATE TABLE token_ids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT UNIQUE NOT NULL,
    token_pair TEXT,
    is_honeypot BOOLEAN DEFAULT FALSE,
    lp_owner TEXT,
    dev_address TEXT,
    security_analyzed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

#### 2. **tokens** (метадані токенів)
```sql
CREATE TABLE tokens (
    token_id INTEGER PRIMARY KEY,
    name TEXT,
    symbol TEXT,
    usd_price NUMERIC,
    liquidity NUMERIC,
    fdv NUMERIC,
    mcap NUMERIC,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

### **DEXSCREENER ТАБЛИЦІ (аналізатор)**

#### 3. **dexscreener_pairs** (основна інформація про пари)
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
)
```

#### 4. **dexscreener_base_token** (базовий токен пари)
```sql
CREATE TABLE dexscreener_base_token (
    token_id INTEGER PRIMARY KEY,
    address TEXT,
    name TEXT,
    symbol TEXT,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 5. **dexscreener_quote_token** (квотний токен пари)
```sql
CREATE TABLE dexscreener_quote_token (
    token_id INTEGER PRIMARY KEY,
    address TEXT,
    name TEXT,
    symbol TEXT,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 6. **dexscreener_txns** (транзакції по часових вікнах)
```sql
CREATE TABLE dexscreener_txns (
    token_id INTEGER PRIMARY KEY,
    m5_buys INTEGER,      -- 5 хвилин
    m5_sells INTEGER,
    h1_buys INTEGER,      -- 1 година
    h1_sells INTEGER,
    h6_buys INTEGER,      -- 6 годин
    h6_sells INTEGER,
    h24_buys INTEGER,     -- 24 години
    h24_sells INTEGER,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 7. **dexscreener_volume** (об'єми торгів)
```sql
CREATE TABLE dexscreener_volume (
    token_id INTEGER PRIMARY KEY,
    h24 NUMERIC,          -- 24 години
    h6 NUMERIC,           -- 6 годин
    h1 NUMERIC,           -- 1 година
    m5 NUMERIC,           -- 5 хвилин
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 8. **dexscreener_price_change** (зміни цін)
```sql
CREATE TABLE dexscreener_price_change (
    token_id INTEGER PRIMARY KEY,
    m5 NUMERIC,           -- 5 хвилин
    h1 NUMERIC,           -- 1 година
    h6 NUMERIC,           -- 6 годин
    h24 NUMERIC,          -- 24 години
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 9. **dexscreener_liquidity** (ліквідність)
```sql
CREATE TABLE dexscreener_liquidity (
    token_id INTEGER PRIMARY KEY,
    usd NUMERIC,          -- USD ліквідність
    base NUMERIC,         -- Базовий токен
    quote NUMERIC,        -- Квотний токен
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

### **SOLANA RPC ТАБЛИЦІ (аналізатор)**

#### 10. **solana_token_supply** (постачання токенів)
```sql
CREATE TABLE solana_token_supply (
    token_id INTEGER PRIMARY KEY,
    amount TEXT,              -- Загальна кількість
    decimals INTEGER,         -- Кількість десяткових знаків
    ui_amount NUMERIC,        -- UI кількість
    ui_amount_string TEXT,    -- UI кількість як рядок
    slot INTEGER,             -- Solana slot
    api_version TEXT,         -- Версія API
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 11. **solana_token_metadata** (метадані токена)
```sql
CREATE TABLE solana_token_metadata (
    token_id INTEGER PRIMARY KEY,
    decimals INTEGER,         -- Десяткові знаки
    freeze_authority TEXT,    -- Адреса freeze authority
    is_initialized BOOLEAN,   -- Чи ініціалізований
    mint_authority TEXT,      -- Адреса mint authority
    supply TEXT,              -- Постачання
    program TEXT,             -- Програма
    space INTEGER,            -- Простір
    executable BOOLEAN,       -- Виконуваний
    lamports INTEGER,         -- Lamports
    owner TEXT,               -- Власник
    rent_epoch TEXT,          -- Rent epoch
    slot INTEGER,             -- Solana slot
    api_version TEXT,         -- Версія API
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 12. **solana_recent_signatures** (останні підписи)
```sql
CREATE TABLE solana_recent_signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER,
    block_time INTEGER,       -- Час блоку
    confirmation_status TEXT, -- Статус підтвердження
    err TEXT,                 -- Помилка
    memo TEXT,                -- Мемо
    signature TEXT,           -- Підпис
    slot INTEGER,             -- Solana slot
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 13. **solana_dev_activity** (активність розробника)
```sql
CREATE TABLE solana_dev_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER,
    block_time INTEGER,       -- Час блоку
    confirmation_status TEXT, -- Статус підтвердження
    err TEXT,                 -- Помилка
    memo TEXT,                -- Мемо
    signature TEXT,           -- Підпис
    slot INTEGER,             -- Solana slot
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

#### 14. **solana_largest_accounts** (найбільші аккаунти)
```sql
CREATE TABLE solana_largest_accounts (
    token_id INTEGER PRIMARY KEY,
    error_message TEXT,       -- Повідомлення про помилку
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
)
```

### **ІНДЕКСИ ДЛЯ ШВИДКОСТІ**
```sql
-- Індекси для швидкого пошуку
CREATE INDEX idx_dexscreener_pairs_timestamp ON dexscreener_pairs(timestamp)
CREATE INDEX idx_solana_supply_timestamp ON solana_token_supply(timestamp)
CREATE INDEX idx_solana_signatures_timestamp ON solana_recent_signatures(timestamp)
```

## 🔧 РІШЕННЯ ПРОБЛЕМ

### 1. **ВИПРАВИТИ ТИПИ ДАНИХ**

```python
# ✅ ПРАВИЛЬНИЙ ТИП ПАРАМЕТРА
async def _broadcast_token_update(self, token_address: str):
    # Отримуємо token_id з бази даних
    token_id = await self._get_token_id_by_address(token_address)
    if not token_id:
        return
    # Далі працюємо з token_id...
```

### 2. **ВИПРАВИТИ ANALYSIS_TIME**

```python
# ✅ ПРАВИЛЬНИЙ РОЗРАХУНОК ЧАСУ
start_time = time.time()
# ... аналіз ...
analysis_time = time.time() - start_time
'analysis_time': f"{analysis_time:.2f}s",
```

### 3. **ВИПРАВИТИ HONEYPOT CHECK**

```python
# ✅ ВИКОРИСТОВУВАТИ ДЕТАЛЬНИЙ HONEYPOT CHECK
'honeypot_check': await self._honeypot_with_fallback(
    token_id, dexscreener_data, solana_rpc_data
),
```

### 4. **ВИПРАВИТИ LP_OWNER**

```python
# ✅ ПРАВИЛЬНІ ПАРАМЕТРИ
pair_address = self._extract_pair_from_dexscreener(dexscreener_data)
'lp_owner': await self._get_lp_owner(pair_address) if pair_address else None,
```

## 📊 СТАТИСТИКА ПРОБЛЕМ

- **Критичні проблеми:** 1 (типи даних)
- **Важливі проблеми:** 3 (analysis_time, honeypot, lp_owner)
- **Другорядні проблеми:** 0

## 🎯 ПРІОРИТЕТИ ВИПРАВЛЕННЯ

1. **ВИСОКИЙ:** Виправити типи даних у broadcast
2. **СЕРЕДНІЙ:** Виправити honeypot check
3. **СЕРЕДНІЙ:** Виправити analysis_time
4. **СЕРЕДНІЙ:** Виправити LP owner detection

## 🔍 ДОДАТКОВІ СПОСТЕРЕЖЕННЯ

- ✅ Jupiter API batch запит працює правильно
- ✅ Database структура відповідає документації
- ✅ Rate limiting працює правильно
- ✅ WebSocket broadcast механізм продуманий
- ❌ Потрібно виправити 4 проблеми

## 🚨 КРИТИЧНІ МОМЕНТИ ДЛЯ ВИПРАВЛЕННЯ

### A. Broadcast Function Signature
```python
# ПОТОЧНИЙ КОД (НЕПРАВИЛЬНИЙ):
async def _broadcast_token_update(self, token_id: int):

# ПРАВИЛЬНИЙ КОД:
async def _broadcast_token_update(self, token_address: str):
    token_id = await self._get_token_id_by_address(token_address)
```

### B. Analysis Time Calculation
```python
# ПОТОЧНИЙ КОД (НЕПРАВИЛЬНИЙ):
'analysis_time': f"{time.time() - time.time():.2f}s",

# ПРАВИЛЬНИЙ КОД:
start_time = time.time()
# ... аналіз ...
analysis_time = time.time() - start_time
'analysis_time': f"{analysis_time:.2f}s",
```

### C. Honeypot Check
```python
# ПОТОЧНИЙ КОД (ПРОСТИЙ):
'honeypot_check': self._check_honeypot(jupiter_data),

# ПРАВИЛЬНИЙ КОД (ДЕТАЛЬНИЙ):
'honeypot_check': await self._honeypot_with_fallback(
    token_id, dexscreener_data, solana_rpc_data
),
```

### D. LP Owner Detection
```python
# ПОТОЧНИЙ КОД (НЕПРАВИЛЬНИЙ):
'lp_owner': self._get_lp_owner(solana_rpc_data),

# ПРАВИЛЬНИЙ КОД:
pair_address = self._extract_pair_from_dexscreener(dexscreener_data)
'lp_owner': await self._get_lp_owner(pair_address) if pair_address else None,
```

## 📈 ОЧІКУВАНІ РЕЗУЛЬТАТИ ПІСЛЯ ВИПРАВЛЕННЯ

1. **Broadcast працюватиме** - frontend отримуватиме оновлення
2. **Honeypot detection покращиться** - більш точна перевірка безпеки
3. **LP owner визначатиметься** - важлива інформація для аналізу
4. **Analysis time буде точним** - корисна діагностична інформація

## 🔄 ПЛАН ВИПРАВЛЕННЯ

1. **Крок 1:** Виправити типи даних у broadcast
2. **Крок 2:** Виправити analysis_time розрахунок
3. **Крок 3:** Покращити honeypot check
4. **Крок 4:** Виправити LP owner detection
5. **Крок 5:** Тестування та валідація

## 📋 ПОВНА СТРУКТУРА БАЗИ ДАНИХ

**Всього таблиць:** 14
- **Основні:** 2 (token_ids, tokens)
- **DexScreener:** 7 (pairs, base_token, quote_token, txns, volume, price_change, liquidity)
- **Solana RPC:** 5 (token_supply, token_metadata, recent_signatures, dev_activity, largest_accounts)

**Всього полів:** 60+
**Індекси:** 3 (для швидкого пошуку по timestamp)

