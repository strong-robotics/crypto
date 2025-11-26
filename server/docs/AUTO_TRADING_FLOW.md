# 📊 Документація: Авто Покупка/Продажа та Force Buy/Sell

Цей документ описує повну логіку автоматичної та ручної торгівлі в системі.

---

## 🎯 Загальна Архітектура

### Два Режими Торгівлі:

1. **Auto-Buy/Auto-Sell** - автоматична торгівля на основі правил
2. **Force Buy/Force Sell** - ручна торгівля (bypass всіх перевірок)

### Основні Файли:

- `_v3_analyzer_jupiter.py` - логіка auto-buy/auto-sell
- `_v2_buy_sell.py` - виконання реальних транзакцій (buy_real, sell_real, force_buy, force_sell)
- `main.py` - HTTP endpoints (`/api/buy/force`, `/api/sell/force`)

---

## 🤖 AUTO-BUY (Автоматична Покупка)

### 📍 Де Виконується:

**Файл:** `_v3_analyzer_jupiter.py`  
**Метод:** `save_token_data()` (рядки 811-906)  
**Цикл:** Кожну секунду через `_scan_loop()`

### ✅ Умови для Auto-Buy:

1. **Вік токена:**
   - `iterations >= AUTO_BUY_ENTRY_SEC` (150 секунд)
   - Перевірка: `mc.cnt >= self.entry_sec` (рядок 855)

2. **Немає відкритої позиції:**
   - `NOT EXISTS (SELECT 1 FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL)`
   - Перевірка: `no_entry.none = TRUE` (рядок 854)

3. **Pattern Segments Decision:**
   - `pattern_segment_decision = "buy"` (рядок 889)
   - Перевірка через `_segments_allow_entry()` (рядок 890)

4. **Мінімальна кількість транзакцій:**
   - `total_tx >= MIN_TX_COUNT` (100 транзакцій) (рядок 891)

5. **Мінімальна частка продажів:**
   - `sell_share >= MIN_SELL_SHARE` (0.20 = 20%) (рядок 892)
   - Anti-honeypot перевірка

6. **Ціна > 0:**
   - `latest_price > 0` (рядок 893)

7. **Є вільний кошелек:**
   - `enabled_wallet_count > 0` (рядок 819)
   - Перевірка: `SELECT COUNT(*) FROM wallets WHERE entry_amount_usd > 0`

### 🔄 Процес Auto-Buy:

```python
# 1. Перевірка умов (рядки 823-894)
if (iterations >= 150 
    and no_entry 
    and decision == "buy"
    and segments_allow_entry
    and total_tx >= 100
    and sell_share >= 0.20
    and price > 0):
    
    # 2. Виклик buy_real() (рядок 896)
    buy_result = await buy_real(token_id, source='auto_buy')
    
    # 3. Логування результату (рядки 898-905)
    if buy_result.get("success"):
        print(f"✅ Auto-buy executed: token {token_id}")
    else:
        print(f"⚠️ Auto-buy failed: {buy_result.get('message')}")
```

### 📝 Що Робить `buy_real()`:

1. **Перевірка токена:**
   - Токен існує
   - Немає відкритої позиції

2. **Отримання вільного кошелька:**
   - `get_free_wallet()` - round-robin логіка
   - Перевірка: `wallet_id IS NULL` для всіх токенів з цим key_id
   - Перевірка: немає відкритої позиції в `wallet_history`
   - Перевірка: `entry_amount_usd > 0`

3. **Honeypot Check:**
   - `execute_buy()` виконує симуляцію продажу (1000 токенів)
   - Якщо симуляція не проходить → honeypot detected → блокування
   - Якщо проходить → продовжуємо з покупкою

4. **Реальна Покупка:**
   - Jupiter API quote для покупки
   - Build swap transaction
   - Sign transaction з keypair
   - Send transaction до блокчейну
   - Отримуємо signature

5. **Запис в БД:**
   - `wallet_history` - запис про покупку:
     - `entry_amount_usd` - сума в USD
     - `entry_token_amount` - кількість токенів
     - `entry_price_usd` - ціна покупки
     - `entry_iteration` - ітерація входу (реальна секунда)
     - `entry_signature` - signature транзакції
   - `tokens.wallet_id` - прив'язка кошелька до токена

---

## 💰 AUTO-SELL (Автоматична Продажа)

### 📍 Де Виконується:

**Файл:** `_v3_analyzer_jupiter.py`  
**Метод:** `save_token_data()` (рядки 735-797)  
**Цикл:** Кожну секунду через `_scan_loop()`

### ✅ Умови для Auto-Sell:

1. **Є відкрита позиція:**
   - `EXISTS (SELECT 1 FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL)`
   - Перевірка: `open_position` (рядок 737)

2. **Поточна вартість >= Цільова:**
   - `cur_value >= entry_amount_usd * (1 + TARGET_RETURN)`
   - `TARGET_RETURN = 0.2` (20% прибуток)
   - Перевірка: `cur_value >= target_value` (рядок 778)

3. **АБО досягнуто plan_sell_iteration:**
   - `current_iteration >= plan_sell_iteration` (рядок 781)
   - `plan_sell_iteration` встановлюється AI моделлю (`eta_online.py`)

4. **АБО досягнуто plan_sell_price_usd:**
   - `current_price >= plan_sell_price_usd` (рядок 784)
   - `plan_sell_price_usd` встановлюється AI моделлю

### 🔄 Процес Auto-Sell:

```python
# 1. Перевірка умов (рядки 737-785)
if (open_position 
    and (cur_value >= target_value 
         or current_iteration >= plan_sell_iteration
         or current_price >= plan_sell_price_usd)):
    
    # 2. Виклик sell_real() (рядок 787)
    sell_result = await sell_real(token_id, source='auto_sell')
    
    # 3. Логування результату (рядки 789-797)
    if sell_result.get("success"):
        print(f"✅ Auto-sell executed: token {token_id}")
    else:
        print(f"⚠️ Auto-sell failed: {sell_result.get('message')}")
```

### 📝 Що Робить `sell_real()`:

1. **Знайти відкриту позицію:**
   - `SELECT * FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL`
   - Отримуємо `entry_token_amount` - кількість токенів для продажу

2. **Отримання інформації:**
   - Token address, decimals
   - Wallet keypair з `keys.json`

3. **Реальна Продажа:**
   - `execute_sell()` - Jupiter API swap
   - Retry logic: якщо помилка → зменшуємо amount на 1% (до 10 спроб)
   - Sign transaction
   - Send transaction до блокчейну

4. **Запис в БД:**
   - `wallet_history` - оновлення запису:
     - `exit_token_amount` - кількість проданих токенів
     - `exit_price_usd` - ціна продажу
     - `exit_amount_usd` - сума отримана в USD
     - `exit_iteration` - ітерація виходу (реальна секунда)
     - `exit_signature` - signature транзакції
     - `outcome = 'closed'`
   - `tokens.history_ready = TRUE` - токен архівований
   - `tokens.wallet_id = NULL` - кошелек звільнений

---

## 🚀 FORCE BUY (Ручна Покупка)

### 📍 Де Виконується:

**Файл:** `_v2_buy_sell.py`  
**Метод:** `force_buy()` (рядки 949-968)  
**HTTP Endpoint:** `POST /api/buy/force` (main.py, рядок 471)

### ⚠️ ВАЖЛИВО: Force Buy Bypass Всіх Перевірок!

**Force buy НЕ перевіряє:**
- ❌ Pattern code (good/bad patterns)
- ❌ Pattern at AI_PREVIEW_ENTRY_SEC
- ❌ Bad pattern history
- ❌ AUTO_BUY_ENTRY_SEC threshold (150 секунд)
- ❌ Pattern score
- ❌ Pattern segments decision

**Force buy перевіряє ТІЛЬКИ:**
- ✅ Токен існує
- ✅ Немає відкритої позиції
- ✅ Є вільний кошелек
- ✅ Достатній баланс SOL
- ✅ **Honeypot check** (завжди виконується для безпеки!)

### 🔄 Процес Force Buy:

```python
# 1. HTTP Request (main.py, рядок 471)
POST /api/buy/force?token_id=123

# 2. Router (main.py, рядок 474)
res = await bs_force_buy(token_id)

# 3. Force Buy Router (_v2_buy_sell.py, рядок 949)
async def force_buy(token_id: int) -> dict:
    return await buy_real(token_id, source='force_buy')

# 4. buy_real() виконує покупку (рядки 795-946)
#    - Honeypot check (завжди!)
#    - Real buy transaction
#    - Log to wallet_history
#    - Bind wallet to token
```

### 📝 Що Робить `force_buy()`:

1. **Викликає `buy_real()`** з `source='force_buy'`
2. **`buy_real()` виконує:**
   - Перевірка токена
   - Отримання вільного кошелька
   - **Honeypot check** (симуляція продажу)
   - Реальна покупка через Jupiter
   - Запис в `wallet_history`
   - Прив'язка кошелька до токена

3. **Після успіху:**
   - Оновлення балансу (`balance_monitor.refresh_balance()`)
   - Push токенів на фронтенд (`tokens_reader.push_now()`)

---

## 🛑 FORCE SELL (Ручна Продажа)

### 📍 Де Виконується:

**Файл:** `_v2_buy_sell.py`  
**Метод:** `force_sell()` (рядки 779-792)  
**HTTP Endpoint:** `POST /api/sell/force` (main.py, рядок 451)

### ⚠️ ВАЖЛИВО: Force Sell Продає ВСЕ Одразу!

**Force sell НЕ перевіряє:**
- ❌ Target return (TARGET_RETURN)
- ❌ Current portfolio value vs target
- ❌ Plan sell iteration/price (plan_sell_*)
- ❌ Будь-які auto-sell умови

**Force sell:**
- ✅ Продає **ВСЮ** кількість токенів (`entry_token_amount`)
- ✅ Виконується **одразу** (не в паралельному потоці)
- ✅ Не чекає досягнення цільової ціни

### 🔄 Процес Force Sell:

```python
# 1. HTTP Request (main.py, рядок 451)
POST /api/sell/force?token_id=123

# 2. Router (main.py, рядок 454)
res = await bs_force_sell(token_id)

# 3. Force Sell Router (_v2_buy_sell.py, рядок 779)
async def force_sell(token_id: int) -> dict:
    return await sell_real(token_id, source='force_sell')

# 4. sell_real() виконує продажу (рядки 497-709)
#    - Знаходить відкриту позицію
#    - Отримує entry_token_amount
#    - Real sell transaction (з retry logic)
#    - Update wallet_history
#    - Free wallet
```

### 📝 Що Робить `force_sell()`:

1. **Викликає `sell_real()`** з `source='force_sell'`
2. **`sell_real()` виконує:**
   - Знаходить відкриту позицію в `wallet_history`
   - Отримує `entry_token_amount` - вся кількість токенів
   - Реальна продажа через Jupiter (з retry logic)
   - Оновлення `wallet_history` (exit_* поля)
   - `tokens.history_ready = TRUE`
   - `tokens.wallet_id = NULL` (звільнення кошелька)

3. **Після успіху:**
   - Оновлення балансу
   - Push токенів на фронтенд

---

## 🔐 HONEYPOT CHECK (Перевірка Honeypot)

### 📍 Де Виконується:

**Файл:** `_v2_buy_sell.py`  
**Метод:** `execute_buy()` (рядки 201-393)  
**Виконується:** Завжди, навіть для force buy!

### 🔄 Процес Honeypot Check:

```python
# 1. Симуляція продажу (рядки 232-321)
test_sell_amount = 1000 * (10**token_decimals)  # 1000 токенів

# 2. Отримання quote для продажу (рядки 240-250)
GET /quote?inputMint=TOKEN&outputMint=SOL&amount=1000

# 3. Build swap transaction (рядки 259-268)
POST /swap (з dummy pubkey для симуляції)

# 4. Simulate transaction (рядки 287-311)
POST RPC simulateTransaction

# 5. Результат:
#    - Якщо simulation fails → Honeypot detected → блокування
#    - Якщо simulation passes → продовжуємо з покупкою
```

### ⚠️ ВАЖЛИВО:

- **Honeypot check виконується ЗАВЖДИ**, навіть для force buy
- Це захист від скам токенів, де неможливо продати
- Якщо honeypot detected → покупка блокується, навіть для force buy

---

## 📊 Порівняння Auto vs Force

| Критерій | Auto-Buy | Force Buy |
|----------|----------|-----------|
| **Pattern checks** | ✅ Перевіряє | ❌ Bypass |
| **AUTO_BUY_ENTRY_SEC** | ✅ Перевіряє (150s) | ❌ Bypass |
| **Pattern segments** | ✅ Перевіряє | ❌ Bypass |
| **MIN_TX_COUNT** | ✅ Перевіряє (100) | ❌ Bypass |
| **MIN_SELL_SHARE** | ✅ Перевіряє (20%) | ❌ Bypass |
| **Honeypot check** | ✅ Завжди | ✅ Завжди |
| **Вільний кошелек** | ✅ Перевіряє | ✅ Перевіряє |
| **Баланс SOL** | ✅ Перевіряє | ✅ Перевіряє |

| Критерій | Auto-Sell | Force Sell |
|----------|-----------|------------|
| **TARGET_RETURN** | ✅ Перевіряє (20%) | ❌ Bypass |
| **plan_sell_iteration** | ✅ Перевіряє | ❌ Bypass |
| **plan_sell_price_usd** | ✅ Перевіряє | ❌ Bypass |
| **Відкрита позиція** | ✅ Перевіряє | ✅ Перевіряє |
| **Кількість токенів** | Вся кількість | Вся кількість |

---

## 🔄 Повний Потік Auto-Buy:

```
1. Analyzer Loop (_scan_loop)
   ↓
2. get_tokens_batch() - отримує токени для обробки
   ↓
3. get_jupiter_data() - отримує дані з Jupiter API
   ↓
4. save_token_data() - зберігає дані та перевіряє умови
   ↓
5. Перевірка умов для auto-buy:
   - iterations >= 150
   - no_entry
   - decision == "buy"
   - segments_allow_entry
   - total_tx >= 100
   - sell_share >= 0.20
   - price > 0
   ↓
6. buy_real() - виконання покупки
   ↓
7. execute_buy() - honeypot check + реальна покупка
   ↓
8. Запис в wallet_history
   ↓
9. Прив'язка кошелька до токена
```

---

## 🔄 Повний Потік Auto-Sell:

```
1. Analyzer Loop (_scan_loop)
   ↓
2. save_token_data() - перевірка умов для auto-sell
   ↓
3. Перевірка умов:
   - open_position exists
   - cur_value >= target_value (20% прибуток)
   - АБО current_iteration >= plan_sell_iteration
   - АБО current_price >= plan_sell_price_usd
   ↓
4. sell_real() - виконання продажу
   ↓
5. execute_sell() - реальна продажа через Jupiter
   ↓
6. Оновлення wallet_history (exit_* поля)
   ↓
7. tokens.history_ready = TRUE
   ↓
8. tokens.wallet_id = NULL (звільнення кошелька)
```

---

## 📝 Важливі Деталі:

### 1. **Iteration vs Seconds:**
- `iteration` = кількість записів в `token_metrics_seconds` з `usd_price > 0`
- Кожен запис = 1 секунда життя токена з валідною ціною
- `entry_iteration` = реальна секунда входу (не hardcoded!)

### 2. **Round-Robin Wallet Selection:**
- `get_free_wallet()` використовує round-robin логіку
- Бере наступний вільний кошелек після останнього використаного
- Якщо всі вільні - бере найменший ID

### 3. **Retry Logic для Продажу:**
- Якщо продажа не вдалася → зменшуємо amount на 1%
- До 10 спроб
- Це допомагає обійти проблеми з ліквідністю

### 4. **Honeypot Check:**
- Завжди виконується, навіть для force buy
- Симулює продажу 1000 токенів
- Якщо симуляція не проходить → honeypot detected → блокування

### 5. **Wallet History:**
- `wallet_history` - журнал всіх покупок/продаж
- `exit_iteration IS NULL` = відкрита позиція
- `exit_iteration IS NOT NULL` = закрита позиція

---

## 🎯 Ключові Константи:

- `AUTO_BUY_ENTRY_SEC = 150` - мінімальний вік для auto-buy
- `AI_PREVIEW_ENTRY_SEC = 150` - точка входу для preview forecast
- `TARGET_RETURN = 0.2` - цільовий прибуток (20%)
- `MIN_TX_COUNT = 100` - мінімальна кількість транзакцій
- `MIN_SELL_SHARE = 0.20` - мінімальна частка продажів (20%)
- `DEFAULT_ENTRY_AMOUNT_USD = 5.0` - сума входу за замовчуванням

---

## 🔍 Діагностика:

### Перевірка чому токен не купився:
```bash
cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_token_entry.py <token_id>
```

### Перевірка статусу кошельків:
```bash
cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_wallets_status.py
```

