# 📋 ПОЛНИЙ АУДИТ: Потік Покупки/Продажи і Передача Даних на Фронтенд

## 🎯 Резюме Проблеми

**Ти кажеш:** Я беру force_buy, але кошелек не підсвічується на фронтенді!

**Причина:** Потік даних має розривів...

---

## 1️⃣ КУП ПОКУПКА (`force_buy`)

### ✅ ЩО ВІДБУВАЄТЬСЯ В `_v1_buy_sell.py`:

```
force_buy(token_id) → роутер (лінія 1771)
    ↓
    ЯКЩО real_trading = True:
        → force_buy_real(token_id) (лінія 1387)
    ЯКЩО real_trading = False:
        → force_buy_simulation(token_id) (лінія 1482)
```

### 🔴 ПРОБЛЕМА 1: ДВА ВАРІАНТИ `force_buy`

**На лінії 1771** (роутер - правильний):
```python
async def force_buy(token_id: int) -> dict:
    """Router: Choose between REAL or SIMULATION buy based on config."""
    real_trading = getattr(config, 'REAL_TRADING_ENABLED', False)
    
    if real_trading:
        return await force_buy_real(token_id)
    else:
        return await force_buy_simulation(token_id)
```

**На лінії 2183** (ДУБЛІКАТ - старий код):
```python
async def force_buy(token_id: int) -> dict:  # ← КОНФЛІКТ!
    """Force-immediate buy using first free wallet..."""
    # ... сотні строк старого коду ...
```

**❌ СЛІДСТВО:** Python використовує ДРУГИЙ `force_buy` (лінія 2183) - це старий код!
Роутер на лінії 1771 ніколи не виконується!

---

### 🔴 ПРОБЛЕМА 2: `force_buy_simulation` НЕ ЗАПИСУЄ В `sim_wallet_history`

**На лінії 1482** (`force_buy_simulation`):
```python
async def force_buy_simulation(token_id: int) -> dict:
    # ... отримати вільний кошелек ...
    # ... розрахувати кількість токенів ...
    
    # LOG TO HISTORY:
    try:
        await conn.execute(
            """
            INSERT INTO sim_wallet_history(
                wallet_id, token_id,
                entry_amount_usd, entry_token_amount, entry_price_usd, entry_iteration,
                outcome, reason, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,'','manual',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """,
            wid, token_id, entry_base, amount_tokens, price_now, rn
        )
    except Exception:
        pass  # ← МОВЧИТЬ!
```

**❌ СЛІДСТВО:** 
- Якщо `INSERT` не працює (exception), **кошелек НЕ ПОЗНАЧАЄТЬСЯ як активний** у журналі
- Фронтенд читає `sim_wallet_history` щоб показати `token_id` кошельків
- БЕЗ `sim_wallet_history` запису - **фронтенд НЕ видить позицію**

---

### 🔴 ПРОБЛЕМА 3: Деякий `force_buy` запис ВИБИРАЄ ВІЛ.КОШЕЛЕК, АЛЕ НЕ ЗАПИСУЄ

На лінії 2183 (`force_buy` - старий):
```python
# ... ЗНАХОДИТЬ ВІЛЬНИЙ КОШЕЛЕК ...
wid = ...  # <- вивільнено

# ... РОЗРАХОВУЄ, ЗАПИСУЄ В `tokens` ...
await conn.execute(
    "UPDATE tokens SET sim_buy_token_amount=$2, ... WHERE id=$1",
    token_id, amount_tokens, ...
)

# ЗАПИСУЄ В ЖУРНАЛ:
await conn.execute(
    """
    INSERT INTO sim_wallet_history(
        wallet_id, token_id,
        entry_amount_usd, entry_token_amount, entry_price_usd, entry_iteration,
        outcome, reason, created_at, updated_at
    ) VALUES ($1,$2,$3,$4,$5,$6,'', 'manual', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """,
    wid, token_id, entry_base, amount_tokens, price_now, rn
)
```

✅ **ЦЕ ПРАВИЛЬНО**, але це ДУБЛІКАТ функції!

---

## 2️⃣ ЯКИХ КОШЕЛЬКІВ СКОРОЧУЄТЬСЯ НА ФРОНТЕНДІ?

### 📡 Дані, які передаються на фронтенд:

**WebSocket `/ws/balances`** → отримує `balance_data`:

```python
# Для КОЖНОГО кошельку:
{
    "id": 1,              # ← wallet ID
    "name": "Bot 1",
    "token_id": 0,        # ← КІЙ ТОКЕН ЗАРАЗ В ПОЗИЦІЇ?
    "value_usd": 5.5,
    "cash_usd": 3.2,
    "sol_balance": 0.02,
    "sol_price_usd": 193,
    "address": "virtual:1",
    "date_added": "virtual"
}
```

### 🔍 ЗВІДКИ БЕРЕТЬСЯ `token_id` У `balance_data`?

**`_v2_balance.py`, лінія 420** (`_virtual_wallets_refresh`):
```python
open_rec = await conn.fetchrow(
    """
    SELECT token_id, entry_token_amount
    FROM sim_wallet_history
    WHERE wallet_id=$1 AND exit_iteration IS NULL  # ← ВІДКРИТА позиція
    ORDER BY id DESC
    LIMIT 1
    """,
    wid
)
token_id_num = 0
if open_rec:
    token_id_num = int(open_rec['token_id'])
    # ... розраховуємо value_usd ...
```

**✅ ЛОГІКА:**
1. Якщо в `sim_wallet_history` є `exit_iteration IS NULL` запис → це ВІДКРИТА позиція
2. Витягаємо `token_id` з цього запису
3. Відправляємо на фронтенд: `token_id: 123` → фронтенд показує "Wallet 1 торгує токеном 123"

**❌ ПРОБЛЕМА:**
- Якщо запис НЕ потрапив в `sim_wallet_history` → `token_id_num = 0`
- Фронтенд бачить `token_id: 0` → "Кошелек ВІЛЬНИЙ"

---

## 3️⃣ ЯК АВТОПОКУПКА/АВТОПРОДАЖА ЗАПИСУЄ ДАНІ

### ✅ Автопокупка (`sim_buy` на лінії 732):

```python
async def sim_buy(token_id: int, entry_sec: int = 30, amount_usd: float = None) -> bool:
    # ... ЗАПИСУЄ В tokens ...
    await conn.execute(
        """
        UPDATE tokens
        SET sim_buy_token_amount=$2,
            sim_buy_price_usd=$3,
            sim_buy_iteration=$4,
            token_updated_at=CURRENT_TIMESTAMP
        WHERE id=$1
        """,
        token_id, amount_tokens, entry_price, entry_sec
    )
```

**❌ ВАЖЛИВО:** `sim_buy` НЕ записує в `sim_wallet_history`!
- Це тільки встановлює `tokens.sim_buy_token_amount`
- БЕЗ `sim_wallet_history` запису → фронтенд НЕ видить хто КУПИВ!

---

### ✅ Автопродажа (`sim_sell` на лінії 938):

```python
async def sim_sell(token_id: int, target_mult: float = TARGET_MULT) -> Optional[int]:
    # ... ЗАПИСУЄ В tokens ...
    await conn.execute(
        """
        UPDATE tokens
        SET sim_sell_token_amount = COALESCE(sim_sell_token_amount, sim_buy_token_amount),
            sim_sell_price_usd = $2,
            sim_sell_iteration = $3,
            token_updated_at = CURRENT_TIMESTAMP
        WHERE id=$1
        """,
        token_id, exit_price, exit_iter
    )
```

**❌ ТАКОЖ:** `sim_sell` НЕ записує в журнал выходу!

---

## 4️⃣ ПОТІК `force_buy` (РЕАЛЬНА ТОРГОВЛЯ)

### 🔴 `force_buy_real` (лінія 1387):

```
1. Отримати ВІЛЬНИЙ КОШЕЛЕК (real_wallet_id)
2. Виконати HONEYPOT CHECK через execute_buy()
3. Записати в tokens:
   - sim_buy_token_amount
   - sim_buy_price_usd
   - real_wallet_id  ← КРИТИЧНО!

4. ЗАПИСАТИ В ЖУРНАЛ:
   INSERT INTO sim_wallet_history(
       wallet_id, token_id,  ← real_wallet_id
       entry_amount_usd, entry_token_amount, entry_price_usd,
       ...
   )
```

**✅ ЦЕ ПРАВИЛЬНО!**

### 🔴 `force_buy_simulation` (лінія 1482):

```
1. Отримати ВІЛЬНИЙ sim_wallet
2. Розрахувати кількість токенів
3. Записати в tokens:
   - sim_buy_token_amount
   - sim_buy_price_usd
   
4. ЗАПИСАТИ В ЖУРНАЛ:
   INSERT INTO sim_wallet_history(
       wallet_id, token_id,  ← sim_wallet_id
       entry_amount_usd, entry_token_amount, entry_price_usd,
       ...
   )
```

**✅ ЛОГІКА ПРАВИЛЬНА**, але вирізняється від старого `force_buy` на лінії 2183!

---

## 5️⃣ ЧОМ КОШЕЛЕК НЕ ПІДСВІЧУЄТЬСЯ?

### Сценарій:
```
1. Нажимаєш force_buy → виконується ДУБЛІКАТ (лінія 2183, старий код)
2. Старий force_buy:
   - Вибирає вільний кошелек
   - ЗАПИСУЄ в tokens ✅
   - ЗАПИСУЄ в sim_wallet_history ✅
   
3. refresh_balance() читає:
   - SELECT token_id FROM sim_wallet_history WHERE wallet_id=$1 AND exit_iteration IS NULL
   - Дані передаються на фронтенд
```

**✅ ЗДАЄТЬСЯ, ЦЕ ПОВИННО ПРАЦЮВАТИ!**

**❌ АЛЕ...**
- Якщо `sim_wallet_history INSERT` спадає (exception) → МОВЧИТЬ (`except: pass`)
- Мож, проблема в БД? Іноземний ключ? Правонаступництво?
- Мож, `active_token_id` в `sim_wallets` не оновлюється?

---

## 6️⃣ ЩО ПОТРІБНО ПЕРЕВІРИТИ

### ✅ Перевірка 1: Чи ЗАПИСУЄТЬСЯ `sim_wallet_history`?

```sql
SELECT * FROM sim_wallet_history 
WHERE wallet_id = 1 
ORDER BY id DESC LIMIT 1;
```

Повинні БУТИ:
- `entry_amount_usd`, `entry_token_amount`, `entry_price_usd`
- `outcome = ''`, `reason = 'manual'`
- `exit_iteration IS NULL` (відкрита позиція)

### ✅ Перевірка 2: Чи `refresh_balance()` видить це?

```python
# У _v2_balance.py, лінія 420:
SELECT token_id, entry_token_amount
FROM sim_wallet_history
WHERE wallet_id=1 AND exit_iteration IS NULL
```

Повинен повернути ЯКИЙСЬ `token_id`

### ✅ Перевірка 3: Чи КОШЕЛЕК оновлює `sim_wallets.active_token_id`?

```sql
SELECT id, active_token_id FROM sim_wallets WHERE id = 1;
```

**ЦЕ МОЖЕ БУТИ ПРОБЛЕМОЮ!** 

У коді НЕ видно, де оновлюється `sim_wallets.active_token_id`!

### ✅ Перевірка 4: Старий `force_buy` на лінії 2183 - цей ВИКОНУЄТЬСЯ?

Мож, потрібно додати `print()` щоб побачити?

---

## 📊 РЕЗЮМЕ ПОТОКУ

```
РУЧНИЙ FORCE_BUY:
├─ force_buy(token_id) на лінії 1771 (роутер)
│  └─ ⚠️ НЕ ВИКОНУЄТЬСЯ (Python вибирає дублікат на лінії 2183)
│
└─ force_buy(token_id) на лінії 2183 (СТАРИЙ КОД)
   ├─ UPDATE tokens (sim_buy_token_amount) ✅
   └─ INSERT sim_wallet_history ✅ ← КРИТИЧНО
      │
      └─ refresh_balance() читає:
         └─ SELECT token_id FROM sim_wallet_history WHERE exit_iteration IS NULL
            │
            └─ WebSocket /ws/balances
               │
               └─ ФРОНТЕНД видить token_id
```

---

## 🎯 ВИСНОВОК

**ПРОБЛЕМА:** На лінії 2183 є ДУБЛІКАТ `force_buy`, який перекриває ПРАВИЛЬНИЙ роутер на лінії 1771.

**РІШЕННЯ:** Видалити ДУБЛІКАТ на лінії 2183 і всю стару логіку, залишивши ТІЛЬКИ:
- Роутер `force_buy` на лінії 1771
- `force_buy_real` на лінії 1387
- `force_buy_simulation` на лінії 1482

**КРІМ ТОГО:**
- Перевірити, чи `sim_wallet_history INSERT` не спадає
- Перевірити, чи `sim_wallets.active_token_id` оновлюється
- Перевірити, чи `refresh_balance()` викликається після покупки

