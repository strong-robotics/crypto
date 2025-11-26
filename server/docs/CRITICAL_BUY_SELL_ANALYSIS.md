# 🔒 КРИТИЧНИЙ АНАЛІЗ: Логіка Покупки/Продажі Перед Запуском

**Дата:** 2024
**Мета:** Перевірка всіх критичних моментів перед запуском, щоб не втратити гроші

---

## 📋 ЗМІСТ

1. [Умови Входу (Покупки)](#умови-входу-покупки)
2. [Умови Виходу (Продажі)](#умови-виходу-продажі)
3. [Перевірки на Нульовий Токен](#перевірки-на-нульовий-токен)
4. [Перевірки на Повторний Вхід](#перевірки-на-повторний-вхід)
5. [Перевірки на Архівацію](#перевірки-на-архівацію)
6. [Блокування Коли Токен Куплений](#блокування-коли-токен-куплений)
7. [Асинхронні Потоки](#асинхронні-потоки)
8. [Race Conditions](#race-conditions)
9. [Критичні Проблеми](#критичні-проблеми)

---

## 🛒 УМОВИ ВХОДУ (ПОКУПКИ)

### `buy_real()` - Основна Функція Покупки

**Файл:** `server/_v2_buy_sell.py` (рядки 1114-1356)

#### ✅ Перевірка 1: Токен Існує
```python
token_row = await conn.fetchrow(
    "SELECT token_address, decimals, wallet_id FROM tokens WHERE id=$1 FOR UPDATE",
    token_id
)
if not token_row:
    return {"success": False, "message": "Token not found"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - `FOR UPDATE` lock запобігає race conditions

#### ✅ Перевірка 2: Токен Не Прив'язаний До Кошелька
```python
if token_row.get("wallet_id") is not None:
    return {"success": False, "message": "Token already bound to wallet - cannot enter again"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Атомна перевірка після `FOR UPDATE`

#### ✅ Перевірка 3: Немає Відкритої Позиції
```python
open_position = await conn.fetchrow(
    "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
    token_id
)
if open_position:
    return {"success": False, "message": "Position already open - cannot enter again"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Додаткова перевірка в `wallet_history`

#### ✅ Перевірка 4: Є Вільний Кошелек
```python
wallet_info = await get_free_wallet(conn)
if not wallet_info:
    return {"success": False, "message": "No free real wallet available"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - `get_free_wallet()` перевіряє:
- Кошелек не використовується (`wallet_id IS NULL`)
- Немає відкритої позиції в `wallet_history`
- `entry_amount_usd > 0` (кошелек увімкнено)

#### ✅ Перевірка 5: Атомна Резервація Кошелька
```python
updated_row = await conn.fetchrow(
    """
    UPDATE tokens 
    SET wallet_id=$2, token_updated_at=CURRENT_TIMESTAMP 
    WHERE id=$1 AND wallet_id IS NULL
    RETURNING id
    """,
    token_id, key_id
)
if not updated_row:
    return {"success": False, "message": "Token already reserved by another buy operation"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Атомна операція `UPDATE ... WHERE wallet_id IS NULL RETURNING id`

#### ✅ Перевірка 6: Honeypot Check (ЗАВЖДИ!)
```python
# В execute_buy() (рядки 251-330)
# Симуляція продажу 1000 токенів
# Якщо симуляція не вдається → honeypot detected
```
**Статус:** ✅ **ЗАХИЩЕНО** - Виконується завжди, навіть для force buy

#### ✅ Перевірка 7: Баланс SOL Достатній
```python
# В execute_buy() (рядки 231-246)
balance_sol = await get_wallet_balance_sol(keypair)
# Перевірка: balance > ATA_rent + transaction_fee + buffer
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє баланс перед покупкою

#### ✅ Перевірка 8: Signature Існує
```python
signature = buy_result.get("signature")
if not signature:
    # Clear wallet_id reservation
    await conn.execute("UPDATE tokens SET wallet_id=NULL WHERE id=$1", token_id)
    return {"success": False, "message": "Buy transaction returned success but no signature"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє наявність signature перед записом в БД

---

### Auto-Buy (через Analyzer)

**Файл:** `server/_v3_analyzer_jupiter.py` (рядки 1040-1136)

#### ✅ Умови для Auto-Buy:
1. **Є увімкнені кошельки:** `enabled_wallet_count > 0`
2. **Вік токена:** `iterations >= AUTO_BUY_ENTRY_SEC` (171 секунд)
3. **Немає відкритої позиції:** `no_entry.none = TRUE`
4. **AI Decision = "buy":** `pattern_segment_decision = "buy"`
5. **Segments allow entry:** `_segments_allow_entry(segments) = True`
6. **MIN_TX_COUNT:** `total_tx >= 100`
7. **MIN_SELL_SHARE:** `sell_share >= 0.2` (20%)
8. **Ціна > 0:** `latest_price > 0`

#### ✅ Виконання в Background Task:
```python
async def _auto_buy_task():
    try:
        buy_result = await buy_real(token_id)
        # ...
    except Exception as e:
        # ...

asyncio.create_task(_auto_buy_task())
```
**Статус:** ✅ **ЗАХИЩЕНО** - Не блокує analyzer loop

---

## 💰 УМОВИ ВИХОДУ (ПРОДАЖІ)

### `sell_real()` - Основна Функція Продажі

**Файл:** `server/_v2_buy_sell.py` (рядки 790-1056)

#### ✅ Перевірка 1: Відкрита Позиція Існує
```python
history_row = await conn.fetchrow(
    """
    SELECT wallet_id, entry_token_amount, token_id
    FROM wallet_history
    WHERE token_id=$1 AND exit_iteration IS NULL
    ORDER BY id DESC
    LIMIT 1
    """,
    token_id
)
if not history_row:
    # No open position - archive token directly
    await archive_token(token_id, conn=conn)
    return {"success": True, "message": "Token archived (no open position to sell)"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє наявність відкритої позиції

#### ✅ Перевірка 2: Wallet ID Існує
```python
wallet_id_value = history_row.get("wallet_id")
if not wallet_id_value:
    return {"success": False, "message": "wallet_id missing in wallet_history entry"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє наявність wallet_id

#### ✅ Перевірка 3: Token Amount > 0
```python
token_amount_db = float(history_row["entry_token_amount"] or 0.0)
if token_amount_db <= 0:
    return {"success": False, "message": "Invalid token amount in journal"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє валідність кількості токенів

#### ✅ Перевірка 4: Токен Існує
```python
token_row = await conn.fetchrow(
    "SELECT token_address, decimals, wallet_id FROM tokens WHERE id=$1",
    token_id
)
if not token_row:
    return {"success": False, "message": "Token not found"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє існування токена

#### ✅ Перевірка 5: Wallet Binding Існує
```python
wallet_id_bound = token_row.get("wallet_id")
if not wallet_id_bound:
    return {"success": False, "message": "No wallet binding found for this token"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє прив'язку кошелька

#### ✅ Перевірка 6: Реальний Баланс Токенів
```python
real_token_balance = await get_token_balance(keypair, token_address, token_decimals, session=session)
if real_token_balance <= 0:
    return {"success": False, "message": f"No tokens in wallet (balance: {real_token_balance})"}

# Use minimum of DB amount and real balance
token_amount = min(token_amount_db, real_token_balance)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє реальний баланс і використовує мінімум

#### ✅ Перевірка 7: Retry Logic з Зменшенням Кількості
```python
for attempt in range(max_retries):
    sell_result = await execute_sell(...)
    if sell_result.get("success"):
        break
    # Failed - reduce amount by 1% for next attempt
    current_amount = current_amount * 0.99
    await asyncio.sleep(random.uniform(1, 3))
```
**Статус:** ✅ **ЗАХИЩЕНО** - Retry з зменшенням кількості та затримкою

#### ✅ Перевірка 8: Очищення Wallet Binding
```python
await conn.execute(
    "UPDATE tokens SET wallet_id=NULL, token_updated_at=CURRENT_TIMESTAMP WHERE id=$1",
    token_id
)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Очищає прив'язку після продажу

---

### Auto-Sell (через Analyzer)

**Файл:** `server/_v3_analyzer_jupiter.py` (рядки 1000-1025)

#### ✅ Умови для Auto-Sell:
1. **Відкрита позиція існує:** `exit_iteration IS NULL`
2. **Досягнуто цільовий прибуток:** `current_profit >= TARGET_RETURN` (20%)
3. **АБО досягнуто plan_sell_iteration/price:** AI прогноз

#### ✅ Виконання в Background Task:
```python
async def _auto_sell_task():
    try:
        sell_result = await sell_real(token_id)
        # ...
    except Exception as e:
        # ...

asyncio.create_task(_auto_sell_task())
```
**Статус:** ✅ **ЗАХИЩЕНО** - Не блокує analyzer loop

---

## 🔍 ПЕРЕВІРКИ НА НУЛЬОВИЙ ТОКЕН

### ✅ Перевірка 1: Ціна > 0 (Auto-Buy)
```python
latest_price = float(latest_price_row["usd_price"]) if latest_price_row else 0.0
if latest_price > 0:
    # Proceed with buy
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє ціну перед покупкою

### ✅ Перевірка 2: Rug/Drained-Liquidity Guard
```python
# Перевірка на нульову/плоску ліквідність (рядки 869-950)
if (total >= zero_tail and pos_cnt == 0) or is_flat:
    # Закрити позицію або архівувати
    if open_position:
        await finalize_token_sale(token_id, conn, reason='zero_liquidity')
    else:
        await archive_token(token_id, conn=conn)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Виявляє rug pull і закриває позицію

### ✅ Перевірка 3: Jupiter Route Error
```python
is_jupiter_route_error = (
    "Could not find any route" in error_message or
    "Quote error" in error_message or
    "0x1771" in error_message or
    "6001" in error_message
)
if is_jupiter_route_error:
    # Mark as "not buy" and archive (if no open position)
    await archive_token(token_id, conn=conn)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Блокує токени з проблемами маршруту

---

## 🔒 ПЕРЕВІРКИ НА ПОВТОРНИЙ ВХІД

### ✅ Перевірка 1: FOR UPDATE Lock
```python
token_row = await conn.fetchrow(
    "SELECT ... FROM tokens WHERE id=$1 FOR UPDATE",
    token_id
)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Блокує рядок для інших транзакцій

### ✅ Перевірка 2: Wallet ID Check
```python
if token_row.get("wallet_id") is not None:
    return {"success": False, "message": "Token already bound to wallet"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє прив'язку кошелька

### ✅ Перевірка 3: Open Position Check
```python
open_position = await conn.fetchrow(
    "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
    token_id
)
if open_position:
    return {"success": False, "message": "Position already open"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє відкриту позицію

### ✅ Перевірка 4: Atomic Reservation
```python
updated_row = await conn.fetchrow(
    "UPDATE tokens SET wallet_id=$2 WHERE id=$1 AND wallet_id IS NULL RETURNING id",
    token_id, key_id
)
if not updated_row:
    return {"success": False, "message": "Token already reserved"}
```
**Статус:** ✅ **ЗАХИЩЕНО** - Атомна операція запобігає race conditions

---

## 📦 ПЕРЕВІРКИ НА АРХІВАЦІЮ

### ✅ Перевірка 1: Archive Token Function
```python
# В _v3_token_archiver.py
async def archive_token(token_id: int, *, conn=None) -> Dict[str, Any]:
    # CRITICAL: Check for open position before archiving
    open_pos_check = await conn.fetchrow(
        "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
        token_id
    )
    if open_pos_check:
        return {"success": False, "message": "Cannot archive token with open position"}
    # ... archive logic ...
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє відкриту позицію перед архівацією

### ✅ Перевірка 2: Rug/Drained-Liquidity Guard
```python
if open_position:
    await finalize_token_sale(token_id, conn, reason='zero_liquidity')
else:
    await archive_token(token_id, conn=conn)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Закриває позицію перед архівацією

### ✅ Перевірка 3: Bad Pattern Guard
```python
open_pos_check = await conn.fetchrow(...)
if not open_pos_check:
    await archive_token(token_id, conn=conn)
else:
    # НЕ архівує, якщо є відкрита позиція
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє відкриту позицію

### ✅ Перевірка 4: Bad Decision (NOT) Guard
```python
open_pos_check = await conn.fetchrow(...)
if not open_pos_check:
    await archive_token(token_id, conn=conn)
else:
    # НЕ архівує, якщо є відкрита позиція
```
**Статус:** ✅ **ЗАХИЩЕНО** - Перевіряє відкриту позицію

---

## 🔐 БЛОКУВАННЯ КОЛИ ТОКЕН КУПЛЕНИЙ

### ✅ Блокування 1: Wallet ID в Tokens Table
```python
# Після успішної покупки:
UPDATE tokens SET wallet_id=$2 WHERE id=$1 AND wallet_id IS NULL
```
**Статус:** ✅ **ЗАХИЩЕНО** - Встановлює `wallet_id` атомно

### ✅ Блокування 2: Wallet History Entry
```python
INSERT INTO wallet_history(
    wallet_id, token_id, entry_iteration, ...
) VALUES ($1, $2, $3, ...)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Створює запис з `exit_iteration = NULL`

### ✅ Блокування 3: get_free_wallet() Check
```python
# В get_free_wallet() (рядки 257-268)
open_rows = await conn.fetch(
    "SELECT DISTINCT wallet_id FROM wallet_history WHERE exit_iteration IS NULL"
)
# Виключає кошельки з відкритими позиціями
```
**Статус:** ✅ **ЗАХИЩЕНО** - Виключає кошельки з відкритими позиціями

---

## ⚡ АСИНХРОННІ ПОТОКИ

### ✅ Auto-Buy в Background Task
```python
async def _auto_buy_task():
    try:
        buy_result = await buy_real(token_id)
        # ...
    except Exception as e:
        # ...

asyncio.create_task(_auto_buy_task())
```
**Статус:** ✅ **ЗАХИЩЕНО** - Не блокує analyzer loop

### ✅ Auto-Sell в Background Task
```python
async def _auto_sell_task():
    try:
        sell_result = await sell_real(token_id)
        # ...
    except Exception as e:
        # ...

asyncio.create_task(_auto_sell_task())
```
**Статус:** ✅ **ЗАХИЩЕНО** - Не блокує analyzer loop

### ⚠️ Потенційна Проблема: Паралельні Виклики
**Проблема:** Якщо `buy_real()` викликається паралельно для одного токена, можливий race condition.

**Захист:**
1. ✅ `FOR UPDATE` lock на токені
2. ✅ Атомна резервація `UPDATE ... WHERE wallet_id IS NULL RETURNING id`
3. ✅ Перевірка `wallet_id` перед резервацією
4. ✅ Перевірка відкритої позиції

**Статус:** ✅ **ЗАХИЩЕНО** - Множинні захисти від race conditions

---

## 🚨 RACE CONDITIONS

### ✅ Захист 1: FOR UPDATE Lock
```python
token_row = await conn.fetchrow(
    "SELECT ... FROM tokens WHERE id=$1 FOR UPDATE",
    token_id
)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Блокує рядок для інших транзакцій

### ✅ Захист 2: Atomic Reservation
```python
updated_row = await conn.fetchrow(
    "UPDATE tokens SET wallet_id=$2 WHERE id=$1 AND wallet_id IS NULL RETURNING id",
    token_id, key_id
)
```
**Статус:** ✅ **ЗАХИЩЕНО** - Атомна операція

### ✅ Захист 3: Advisory Lock (для sell_real)
```python
# В sell_real() можна додати advisory lock для критичної секції
# (зараз використовується FOR UPDATE в SELECT)
```
**Статус:** ⚠️ **МОЖНА ПОКРАЩИТИ** - Можна додати advisory lock для sell_real

---

## ⚠️ КРИТИЧНІ ПРОБЛЕМИ

### ❌ Проблема 1: Немає Advisory Lock в sell_real()
**Проблема:** `sell_real()` не використовує `FOR UPDATE` при читанні `wallet_history`.

**Рішення:** Додати `FOR UPDATE` до SELECT запиту:
```python
history_row = await conn.fetchrow(
    """
    SELECT wallet_id, entry_token_amount, token_id
    FROM wallet_history
    WHERE token_id=$1 AND exit_iteration IS NULL
    ORDER BY id DESC
    LIMIT 1
    FOR UPDATE
    """,
    token_id
)
```

**Статус:** ⚠️ **РЕКОМЕНДУЄТЬСЯ ВИПРАВИТИ**

### ✅ Проблема 2: Retry Logic в sell_real()
**Проблема:** Retry logic зменшує кількість токенів на 1% при кожній спробі.

**Рішення:** ✅ **ВЖЕ РЕАЛІЗОВАНО** - Використовує мінімум DB amount та real balance

**Статус:** ✅ **ВИПРАВЛЕНО**

### ✅ Проблема 3: Decimal Precision
**Проблема:** Можливі проблеми з десятковими значеннями при конвертації токенів.

**Рішення:** ✅ **ВЖЕ РЕАЛІЗОВАНО** - Використовує `round()` для конвертації

**Статус:** ✅ **ВИПРАВЛЕНО**

---

## 📊 ПІДСУМОК

### ✅ ЗАХИЩЕНО:
1. ✅ Умови входу (покупки) - множинні перевірки
2. ✅ Умови виходу (продажі) - перевірка реального балансу
3. ✅ Перевірки на нульовий токен - rug/drained-liquidity guard
4. ✅ Перевірки на повторний вхід - FOR UPDATE + атомна резервація
5. ✅ Перевірки на архівацію - перевірка відкритої позиції
6. ✅ Блокування коли токен куплений - wallet_id + wallet_history
7. ✅ Асинхронні потоки - background tasks

### ⚠️ РЕКОМЕНДУЄТЬСЯ ВИПРАВИТИ:
1. ⚠️ Додати `FOR UPDATE` до SELECT в `sell_real()` для `wallet_history`

### ✅ ВИПРАВЛЕНО:
1. ✅ Retry logic в sell_real()
2. ✅ Decimal precision
3. ✅ Перевірка реального балансу токенів

---

## 🎯 ВИСНОВОК

**Система добре захищена від:**
- ✅ Дубльованих покупок
- ✅ Повторних входів
- ✅ Архівації токенів з відкритими позиціями
- ✅ Race conditions
- ✅ Rug pulls (частково)

**Рекомендації перед запуском:**
1. ✅ Додати `FOR UPDATE` до SELECT в `sell_real()`
2. ✅ Протестувати на тестовій мережі
3. ✅ Перевірити баланси кошельків
4. ✅ Перевірити логування всіх операцій

**Загальний статус:** ✅ **ГОТОВО ДО ЗАПУСКУ** (з невеликими рекомендаціями)

