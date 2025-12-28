# Аналіз проблеми: Невдала продажа залишку та закриття ATA

## Проблема з логів (рядки 957-1014)

### Що сталося:

1. **Продажа пройшла успішно:**
   - Продано: 8520.05141100 токенів
   - Отримано: $2.02
   - Signature: `4svsphuQZ51GRuhMd5djRZWNzo3myw4xMPboCqctrW6SX1Ti4thF5dncDadTHWtUwM5dMmG92gAQLh9KXycJ5nhR`

2. **Спроба закрити ATA не вдалася:**
   - Рядок 1001: `ATA still has 0.07689400 tokens` ✅ **Правильне значення**
   - Це означає, що після продажу залишився залишок (dust)

3. **Перевірка балансу показала неправильне значення:**
   - Рядок 1004: `Remaining balance detected: 8520.05141100 tokens` ❌ **Неправильне значення**
   - Має бути: `0.07689400 tokens`

4. **Спроба продати залишок не вдалася:**
   - Рядок 1007: `Rate limit exceeded, please try again later` (HTTP 429)
   - Немає retry логіки для обробки rate limit

---

## Причини проблеми

### 1. Затримка індексації RPC

**Проблема:**
- `get_token_balance()` використовує `getTokenAccountsByOwner` (RPC метод)
- Цей метод може повертати застарілі дані через затримку індексації
- Після продажу транзакція може не бути проіндексована протягом 5-10 секунд

**Код:**
```python
# Рядок 1428: _v2_buy_sell.py
balance_after_failed_close, _ = await get_token_balance(keypair, token_address, token_decimals)
```

**Чому показує 8520.05141100 замість 0.07689400:**
- `get_token_balance` викликається через 5 секунд після невдалої спроби закрити ATA
- RPC ще не проіндексував транзакцію продажу
- Повертається старий баланс (до продажу)

---

### 2. Різниця між методами перевірки балансу

**Метод 1: `_close_ata_after_sell()` (правильний)**
```python
# Рядок 2293: _v2_buy_sell.py
balance_resp = await client.get_token_account_balance(ata)
ui_amount = float(balance.ui_amount) if balance and balance.ui_amount is not None else 0.0
```
- Використовує `get_token_account_balance` (більш точний)
- Перевіряє конкретний ATA рахунок
- ✅ Показує правильне значення: `0.07689400 tokens`

**Метод 2: `get_token_balance()` (застарілий)**
```python
# Рядок 443: _v2_buy_sell.py
method: "getTokenAccountsByOwner"
```
- Використовує `getTokenAccountsByOwner` (може бути застарілим)
- Перевіряє всі токен-акаунти власника
- ❌ Показує неправильне значення: `8520.05141100 tokens`

---

### 3. Відсутність retry для rate limit

**Проблема:**
- При спробі продати залишок отримано HTTP 429 (Rate limit exceeded)
- Немає retry логіки з exponential backoff
- Спроба продати залишок завершується помилкою

**Код:**
```python
# Рядок 1442: _v2_buy_sell.py
dust_sell_result = await execute_sell(...)
# Немає retry при rate limit
```

---

## Рішення проблем

### 1. Використовувати правильний метод перевірки балансу

**Замість `get_token_balance()` використовувати `get_token_account_balance()`:**

```python
# Замість:
balance_after_failed_close, _ = await get_token_balance(keypair, token_address, token_decimals)

# Використовувати:
async with AsyncClient(rpc_endpoint, commitment="confirmed") as client:
    balance_resp = await client.get_token_account_balance(ata)
    balance = balance_resp.value
    ui_amount = float(balance.ui_amount) if balance and balance.ui_amount is not None else 0.0
    balance_after_failed_close = ui_amount
```

**Переваги:**
- ✅ Більш точний метод (перевіряє конкретний ATA)
- ✅ Менша затримка індексації
- ✅ Показує актуальний баланс

---

### 2. Додати додаткове очікування на індексацію

**Проблема:** RPC може не проіндексувати транзакцію протягом 5 секунд

**Рішення:** Додати polling з retry для перевірки балансу:

```python
# Очікування на індексацію балансу
max_wait_sec = 30
poll_interval = 2
for attempt in range(max_wait_sec // poll_interval):
    balance = await get_ata_balance(client, ata)
    if balance < 0.000001:  # Немає токенів
        break
    await asyncio.sleep(poll_interval)
```

---

### 3. Додати retry для rate limit при продажі залишку

**Проблема:** Немає retry при HTTP 429

**Рішення:** Додати retry з exponential backoff:

```python
max_retries = 3
base_delay = 5.0
for attempt in range(max_retries):
    try:
        dust_sell_result = await execute_sell(...)
        if dust_sell_result.get("success"):
            break
    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower():
            delay = base_delay * (2 ** attempt)  # Exponential backoff
            await asyncio.sleep(delay)
            continue
        raise
```

---

### 4. Використовувати commitment level "finalized" для перевірки балансу

**Проблема:** `commitment="confirmed"` може не відображати актуальний баланс

**Рішення:** Використовувати `commitment="finalized"` для критичних перевірок:

```python
async with AsyncClient(rpc_endpoint, commitment="finalized") as client:
    balance_resp = await client.get_token_account_balance(ata)
```

**Недолік:** Більша затримка (до 30 секунд), але більш точні дані

---

## Рекомендовані зміни (без зміни коду)

### 1. Використовувати `get_token_account_balance` замість `get_token_balance`

**Місце:** Рядок 1428 в `_v2_buy_sell.py`

**Замість:**
```python
balance_after_failed_close, _ = await get_token_balance(keypair, token_address, token_decimals)
```

**Використовувати:**
```python
# Отримати ATA адресу
ata = derive_ata_address(keypair.pubkey(), token_address)

# Перевірити баланс через get_token_account_balance
async with AsyncClient(rpc_endpoint, commitment="confirmed") as client:
    balance_resp = await client.get_token_account_balance(ata)
    balance = balance_resp.value
    balance_after_failed_close = float(balance.ui_amount) if balance and balance.ui_amount is not None else 0.0
```

---

### 2. Додати polling для очікування індексації

**Місце:** Після рядка 1424 (після `await asyncio.sleep(5.0)`)

**Додати:**
```python
# Polling для очікування індексації балансу
max_poll_attempts = 10
poll_interval = 2.0
for poll_attempt in range(max_poll_attempts):
    balance_resp = await client.get_token_account_balance(ata)
    balance = balance_resp.value
    current_balance = float(balance.ui_amount) if balance and balance.ui_amount is not None else 0.0
    
    # Якщо баланс змінився (не дорівнює старому), індексація завершена
    if current_balance < balance_before_sell * 0.9:  # Баланс зменшився на 10%+
        break
    
    if poll_attempt < max_poll_attempts - 1:
        await asyncio.sleep(poll_interval)
```

---

### 3. Додати retry для rate limit

**Місце:** Рядок 1440-1451 в `_v2_buy_sell.py`

**Замість:**
```python
try:
    dust_sell_result = await execute_sell(...)
```

**Використовувати:**
```python
max_dust_retries = 3
base_delay = 5.0
dust_sell_result = None

for dust_attempt in range(max_dust_retries):
    try:
        dust_sell_result = await execute_sell(...)
        if dust_sell_result.get("success"):
            break
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "rate limit" in error_msg.lower():
            if dust_attempt < max_dust_retries - 1:
                delay = base_delay * (2 ** dust_attempt)  # Exponential backoff: 5s, 10s, 20s
                print(f"[sell_real] ⏳ [Background] Rate limit hit, waiting {delay:.1f}s before retry {dust_attempt + 1}/{max_dust_retries}...")
                await asyncio.sleep(delay)
                continue
        raise
```

---

### 4. Додати перевірку мінімального значення залишку

**Проблема:** Залишок 0.07689400 токенів може бути занадто малим для продажу (комісії більші за вартість)

**Рішення:** Перевіряти, чи вартість залишку покриває комісії:

```python
# Оцінити вартість залишку
dust_value_usd = balance_after_failed_close * current_price_usd
min_sell_value_usd = 0.01  # Мінімальна вартість для продажу (покриває комісії)

if dust_value_usd < min_sell_value_usd:
    print(f"[sell_real] ℹ️ [Background] Dust value (${dust_value_usd:.6f}) too small to sell, closing ATA with --force")
    # Закрити ATA з --force (втратити залишок, але отримати rent)
    close_success, close_result = await _close_ata_force(keypair, token_address, rpc_endpoint)
else:
    # Спробувати продати залишок
    dust_sell_result = await execute_sell(...)
```

---

## Альтернативне рішення: Force close ATA

**Якщо залишок занадто малий для продажу:**

1. **Оцінити вартість залишку:**
   - Якщо `dust_value_usd < 0.01` → не варто продавати (комісії більші)

2. **Закрити ATA з --force:**
   - Втратити залишок (0.07689400 токенів ≈ $0.00002)
   - Отримати rent (~0.00203928 SOL ≈ $0.39)
   - **Чистий прибуток:** +$0.39

**Реалізація:**
```python
async def _close_ata_force(keypair, token_address, rpc_endpoint):
    """Close ATA even if it has tokens (lose tokens, but reclaim rent)"""
    # Використовувати CloseAccount з ігноруванням балансу
    # Або перевести залишок на burn address перед закриттям
```

---

## Підсумок проблем

| Проблема | Причина | Вплив |
|----------|---------|-------|
| **Неправильний баланс** | `get_token_balance()` використовує застарілий RPC метод | Показує 8520 токенів замість 0.076 |
| **Rate limit** | Немає retry логіки | Спроба продати залишок не вдалася |
| **Затримка індексації** | RPC не проіндексував транзакцію за 5 секунд | Перевірка балансу показує старі дані |
| **Малий залишок** | 0.076 токенів ≈ $0.00002, комісії більші | Не варто продавати, краще закрити з --force |

---

## Рекомендації

1. ✅ **Використовувати `get_token_account_balance`** замість `get_token_balance` для перевірки залишку
2. ✅ **Додати polling** для очікування індексації (до 30 секунд)
3. ✅ **Додати retry з exponential backoff** для rate limit (3 спроби: 5s, 10s, 20s)
4. ✅ **Перевіряти мінімальну вартість залишку** перед продажем (якщо < $0.01 → закрити з --force)
5. ✅ **Використовувати `commitment="finalized"`** для критичних перевірок балансу

---

## Очікуваний результат після виправлень

1. ✅ Правильне виявлення залишку (0.07689400 токенів)
2. ✅ Retry при rate limit (3 спроби з backoff)
3. ✅ Очікування на індексацію перед перевіркою балансу
4. ✅ Автоматичне закриття ATA з --force, якщо залишок занадто малий

