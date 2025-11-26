# Аналіз дублікатів в модулях

**Дата аналізу:** 2024  
**Модулі:** `_v3_cleaner.py`, `_v3_analyzer_jupiter.py`, `_v2_trades_history.py`, `_v3_new_tokens.py`, `_v3_jupiter_scheduler.py`

---

## 📊 СТАТИСТИКА МОДУЛІВ

| Модуль | Рядків | Функцій/класів | SQL запитів | get_db_pool() |
|--------|--------|-----------------|-------------|---------------|
| `_v3_cleaner.py` | 224 | 10 | 8 | 1 |
| `_v3_analyzer_jupiter.py` | 1688 | 4 | 50 | 5 |
| `_v2_trades_history.py` | 588 | 3 | 6 | 5 |
| `_v3_new_tokens.py` | 400 | 2 | 13 | 1 |
| `_v3_jupiter_scheduler.py` | 195 | 2 | 0 | 0 |
| **РАЗОМ** | **3095** | **21** | **77** | **12** |

---

## 🔍 ЗНАЙДЕНО ДУБЛІКАТІВ ТА СХОЖОГО КОДУ

### ⚠️ ВАЖЛИВО: Розрізнення типів

- **✅ ПОВНИЙ ДУБЛІКАТ**: Ідентичний код (можна винести без змін)
- **⚠️ ЧАСТКОВИЙ ДУБЛІКАТ**: Схожий код з невеликими відмінностями (можна винести з параметрами)
- **🔄 СХОЖИЙ КОД**: Схожа структура, але різна логіка (потрібна стандартизація)
- **❌ НЕ ДУБЛІКАТ**: Схожий код, але різні призначення

---

### 1. ⚠️ ЧАСТКОВИЙ ДУБЛІКАТ: `INSERT INTO token_metrics_seconds` (~14 рядків)

**Проблема:**
Ідентичний код в `_v3_analyzer_jupiter.py` та `_v3_new_tokens.py`:

**`_v3_analyzer_jupiter.py` (рядки 802-817):**
```python
await conn.execute(
    """
    INSERT INTO token_metrics_seconds (
        token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot, holder_count
    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
    ON CONFLICT (token_id, ts) DO UPDATE SET
        usd_price = EXCLUDED.usd_price,
        liquidity = EXCLUDED.liquidity,
        fdv = EXCLUDED.fdv,
        mcap = EXCLUDED.mcap,
        price_block_id = EXCLUDED.price_block_id,
        jupiter_slot = EXCLUDED.jupiter_slot,
        holder_count = EXCLUDED.holder_count
    """,
    token_id, ts, usd_p, liq, fdv, mcap, pblk, pblk, holders
)
```

**`_v3_new_tokens.py` (рядки 276-290):**
```python
await conn.execute(
    """
    INSERT INTO token_metrics_seconds (
        token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot
    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
    ON CONFLICT (token_id, ts) DO UPDATE SET
        usd_price = EXCLUDED.usd_price,
        liquidity = EXCLUDED.liquidity,
        fdv = EXCLUDED.fdv,
        mcap = EXCLUDED.mcap,
        price_block_id = EXCLUDED.price_block_id,
        jupiter_slot = EXCLUDED.jupiter_slot
    """,
    token_id, ts, usd_p, liq, fdv, mcap, pblk, pblk
)
```

**Відмінності:**
- `analyzer`: додає `holder_count` (9 параметрів)
- `new_tokens`: без `holder_count` (8 параметрів)

**Класифікація:** ⚠️ **ЧАСТКОВИЙ ДУБЛІКАТ** - відрізняється тільки одним полем

**Рішення:**
Винести в окрему функцію з опціональним параметром:
```python
async def save_token_metrics(conn, token_id: int, data: dict, include_holder_count: bool = False):
    """Save token metrics to token_metrics_seconds table"""
    # Якщо include_holder_count=True → додаємо holder_count
    # Якщо False → без holder_count
```

**Економія:** ~14 рядків (після стандартизації)

---

### 2. 🔄 СХОЖИЙ КОД: `UPDATE tokens SET stats` (5m, 1h, 6h, 24h) (~32 рядки)

**Проблема:**
Ідентичний цикл в `_v3_analyzer_jupiter.py` та `_v3_new_tokens.py`:

**`_v3_analyzer_jupiter.py` (рядки 685-716):**
```python
for period in ['5m', '1h', '6h', '24h']:
    stats = data.get(f'stats{period}', {})
    if stats:
        period_suffix = f"_{period}"
        await conn.execute(f"""
            UPDATE tokens SET
                price_change{period_suffix} = $2,
                holder_change{period_suffix} = $3,
                liquidity_change{period_suffix} = $4,
                volume_change{period_suffix} = $5,
                buy_volume{period_suffix} = $6,
                sell_volume{period_suffix} = $7,
                buy_organic_volume{period_suffix} = $8,
                sell_organic_volume{period_suffix} = $9,
                num_buys{period_suffix} = $10,
                num_sells{period_suffix} = $11,
                num_traders{period_suffix} = $12
            WHERE id = $1
        """, 
            token_id,
            safe_numeric(stats.get('priceChange')),
            safe_numeric(stats.get('holderChange')),
            # ... інші поля
        )
```

**`_v3_new_tokens.py` (рядки 233-264):**
```python
for period in ['5m', '1h', '6h', '24h']:
    stats = token_data.get(f'stats{period}', {})
    if stats:
        suffix = f'_{period}'
        await conn.execute(f"""
            UPDATE tokens SET
                price_change{suffix} = $2,
                holder_change{suffix} = $3,
                liquidity_change{suffix} = $4,
                volume_change{suffix} = $5,
                buy_volume{suffix} = $6,
                sell_volume{suffix} = $7,
                buy_organic_volume{suffix} = $8,
                sell_organic_volume{suffix} = $9,
                num_buys{suffix} = $10,
                num_sells{suffix} = $11,
                num_traders{suffix} = $12
            WHERE id = $1
        """,
            token_id,
            float(stats.get('priceChange', 0)) if stats.get('priceChange') is not None else None,
            # ... інші поля (інша логіка конвертації)
        )
```

**Відмінності:**
- `analyzer`: використовує `safe_numeric()` для конвертації
- `new_tokens`: використовує `float(...) if ... is not None else None`

**Класифікація:** 🔄 **СХОЖИЙ КОД** - схожа структура, але **РІЗНА логіка конвертації**

**Проблема:**
- `safe_numeric()` обмежує значення до `max_val=999999.9999`
- `float(...) if ... is not None else None` не обмежує значення
- Це може призвести до різних результатів!

**Рішення:**
1. **Стандартизувати логіку конвертації** (вибрати один підхід)
2. Винести в окрему функцію:
```python
async def update_token_stats(conn, token_id: int, data: dict, convert_func):
    """Update token stats for all periods (5m, 1h, 6h, 24h)"""
    # Використовує convert_func для конвертації (safe_numeric або float)
```

**Економія:** ~32 рядки (після стандартизації логіки)

---

### 3. 🔄 СХОЖИЙ КОД: `UPDATE tokens SET audit fields` (~18 рядків)

**Проблема:**
Ідентичний код в `_v3_analyzer_jupiter.py` та `_v3_new_tokens.py`:

**`_v3_analyzer_jupiter.py` (рядки 718-735):**
```python
audit = data.get('audit', {})
if audit:
    await conn.execute("""
        UPDATE tokens SET
            mint_authority_disabled = $2,
            freeze_authority_disabled = $3,
            top_holders_percentage = $4,
            dev_balance_percentage = $5,
            blockaid_rugpull = $6
        WHERE id = $1
    """, 
        token_id,
        audit.get('mintAuthorityDisabled'),
        audit.get('freezeAuthorityDisabled'),
        safe_numeric(audit.get('topHoldersPercentage')),
        safe_numeric(audit.get('devBalancePercentage')),
        audit.get('blockaidRugpull')
    )
```

**`_v3_new_tokens.py` (рядки 214-231):**
```python
audit = token_data.get('audit', {})
if audit:
    await conn.execute("""
        UPDATE tokens SET
            mint_authority_disabled = $2,
            freeze_authority_disabled = $3,
            top_holders_percentage = $4,
            dev_balance_percentage = $5,
            blockaid_rugpull = $6
        WHERE id = $1
    """,
        token_id,
        audit.get('mintAuthorityDisabled'),
        audit.get('freezeAuthorityDisabled'),
        float(audit.get('topHoldersPercentage', 0)) if audit.get('topHoldersPercentage') is not None else None,
        float(audit.get('devBalancePercentage', 0)) if audit.get('devBalancePercentage') is not None else None,
        audit.get('blockaidRugpull')
    )
```

**Відмінності:**
- `analyzer`: використовує `safe_numeric()` для числових полів
- `new_tokens`: використовує `float(...) if ... is not None else None`

**Класифікація:** 🔄 **СХОЖИЙ КОД** - схожа структура, але **РІЗНА логіка конвертації**

**Проблема:**
- Та сама проблема, що і з UPDATE stats - різна логіка конвертації
- `safe_numeric()` обмежує значення, `float(...)` - ні

**Рішення:**
1. **Стандартизувати логіку конвертації** (вибрати один підхід)
2. Винести в окрему функцію:
```python
async def update_token_audit(conn, token_id: int, audit: dict, convert_func):
    """Update token audit fields"""
    # Використовує convert_func для конвертації
```

**Економія:** ~18 рядків (після стандартизації логіки)

---

### 4. 🔄 СХОЖИЙ КОД: Логіка `pair_resolve_attempts` (~19 рядків)

**Проблема:**
Схожа логіка в `_v3_analyzer_jupiter.py` та `_v3_new_tokens.py`:

**`_v3_analyzer_jupiter.py` (рядки 765-783):**
```python
# Логіка підрахунку спроб отримання валідної пари
if not updated_pair and (
    not current_pair or 
    current_pair == token_addr or 
    not candidate_pair or 
    candidate_pair == token_addr
):
    # Пара не валідна - збільшуємо лічильник спроб
    await conn.execute(
        "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", 
        token_id
    )
else:
    # Пара валідна - скидаємо лічильник
    if current_pair and current_pair != token_addr:
        await conn.execute(
            "UPDATE tokens SET pair_resolve_attempts = 0 WHERE id = $1", 
            token_id
        )
```

**`_v3_new_tokens.py` (рядки 201-212):**
```python
else:
    # Пара не валідна - збільшуємо лічильник спроб
    await conn.execute(
        "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", 
        token_id
    )
else:
    # Немає first_pool - збільшуємо лічильник спроб
    await conn.execute(
        "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", 
        token_id
    )
```

**Відмінності:**
- `analyzer`: складніша логіка (перевірка `updated_pair`, `current_pair`, `candidate_pair`, fallback через `resolve_and_update_pair`)
- `new_tokens`: простіша логіка (тільки перевірка `first_pool`, оновлює `first_pool_created_at`)

**Класифікація:** 🔄 **СХОЖИЙ КОД** - схожа ідея, але **РІЗНА логіка визначення валідності пари**

**Проблема:**
- Різна логіка визначення, чи пара валідна
- `analyzer` має fallback механізм, `new_tokens` - ні
- `new_tokens` оновлює `first_pool_created_at`, `analyzer` - ні

**Рішення:**
Винести базову частину (інкремент/скидання лічильника) в окрему функцію:
```python
async def update_pair_resolve_attempts(conn, token_id: int, is_valid_pair: bool):
    """Update pair_resolve_attempts counter"""
    if is_valid_pair:
        await conn.execute("UPDATE tokens SET pair_resolve_attempts = 0 WHERE id = $1", token_id)
    else:
        await conn.execute("UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", token_id)
```

**Економія:** ~5-10 рядків (тільки базова частина, логіка визначення залишається різною)

---

### 5. ❌ НЕ ДУБЛІКАТ: `safe_numeric` vs `safe_get` (схожа логіка)

**Проблема:**
Дві функції зі схожою логікою:

**`_v3_analyzer_jupiter.py` (рядки 644-653):**
```python
def safe_numeric(value, max_val=999999.9999):
    try:
        v = float(value) if value is not None else None
        if v is None:
            return None
        if abs(v) > max_val:
            return max_val if v > 0 else -max_val
        return v
    except (ValueError, TypeError):
        return None
```

**`_v3_new_tokens.py` (рядки 87-98):**
```python
def safe_get(key: str, default=None, field_type=str):
    value = token_data.get(key, default)
    if value is None or value == '':
        if field_type == int:
            return 0
        elif field_type == float:
            return 0.0
        elif field_type == bool:
            return False
        else:
            return default or 'Unknown'
    return value
```

**Відмінності:**
- `safe_numeric`: конвертує в `float`, обмежує максимальне значення (`max_val=999999.9999`)
- `safe_get`: отримує значення з dict, конвертує в різні типи (`int`, `float`, `bool`, `str`), має default

**Класифікація:** ❌ **НЕ ДУБЛІКАТ** - різні призначення:
- `safe_numeric`: для обмеження числових значень (захист від переповнення)
- `safe_get`: для безпечного отримання значень з dict з конвертацією типів

**Рішення:**
**НЕ об'єднувати** - це різні функції з різними призначеннями. Можна залишити як є.

**Економія:** 0 рядків (не дублікат)

---

### 6. ✅ ПОВНИЙ ДУБЛІКАТ: Перевірка open position (SELECT FROM wallet_history)

**Проблема:**
Однаковий SQL запит повторюється в багатьох місцях:

**Використання:**
- `_v3_analyzer_jupiter.py`: **6 разів** (рядки 470-477, 934-942, 1188-1195, 1248-1255, 1396-1403, 1440-1448)
- `_v2_trades_history.py`: 1 раз (рядок 332)
- `_v2_buy_sell.py`: 1 раз (рядок 1182)

**Варіанти запиту:**
1. `SELECT 1 FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1` (3 рази)
2. `SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1` (3 рази)
3. `SELECT id, wallet_id, entry_token_amount FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1` (1 раз - для отримання даних)

**Приклад:**
```python
open_pos_check = await conn.fetchrow(
    """
    SELECT id FROM wallet_history
    WHERE token_id=$1 AND exit_iteration IS NULL
    LIMIT 1
    """,
    token_id
)
```

**Класифікація:** ✅ **ПОВНИЙ ДУБЛІКАТ** - ідентичний SQL запит в усіх місцях

**Рішення:**
Винести в окрему функцію (можна використати без змін):
```python
async def has_open_position(conn, token_id: int) -> bool:
    """Check if token has open position in wallet_history"""
    row = await conn.fetchrow(
        "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
        token_id
    )
    return row is not None
```

**Економія:** ~3-5 рядків на використання (всього ~20-30 рядків)

---

### 7. ❌ ДУБЛІКАТ: `UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP`

**Проблема:**
Однаковий SQL запит повторюється в багатьох місцях:

**Використання:**
- `_v3_analyzer_jupiter.py`: 1 раз (рядок 785)
- `_v3_new_tokens.py`: 1 раз (рядок 294)
- `_v2_buy_sell.py`: 2 рази (рядки 1291, 1309)

**Приклад:**
```python
await conn.execute("UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP WHERE id = $1", token_id)
```

**Рішення:**
Винести в окрему функцію (або залишити як є - це короткий запит)

**Економія:** ~1 рядок на використання (всього ~3 рядки)

---

### 8. ⚠️ ДУБЛІКАТ: `SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0`

**Проблема:**
Однаковий SQL запит повторюється в багатьох місцях:

**Використання:**
- `_v3_analyzer_jupiter.py`: 4+ разів (рядки 390, 1056, 1164, 1224, 1281)
- `_v2_buy_sell.py`: 3 рази (рядки 991, 1056, 1340)

**Приклад:**
```python
iterations_count = int(
    await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM token_metrics_seconds
        WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
        """,
        token_id,
    )
    or 0
)
```

**Рішення:**
Винести в окрему функцію:
```python
async def get_token_iterations_count(conn, token_id: int) -> int:
    """Get count of iterations (records with valid price) for token"""
    return int(
        await conn.fetchval(
            "SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0",
            token_id
        ) or 0
    )
```

**Економія:** ~3-4 рядки на використання (всього ~20-30 рядків)

---

### 9. ⚠️ ДУБЛІКАТ: Логіка оновлення `token_pair`

**Проблема:**
Схожа логіка в `_v3_analyzer_jupiter.py` та `_v3_new_tokens.py`:

**`_v3_analyzer_jupiter.py` (рядки 737-783):**
- Перевіряє `first_pool.get('id')`
- Порівнює з `current_pair`
- Оновлює `token_pair` якщо змінився
- Викликає `resolve_and_update_pair` як fallback
- Оновлює `pair_resolve_attempts`

**`_v3_new_tokens.py` (рядки 180-212):**
- Перевіряє `first_pool.get('id')`
- Порівнює з `existing_pair`
- Оновлює `token_pair` якщо змінився
- Оновлює `first_pool_created_at`
- Оновлює `pair_resolve_attempts`

**Відмінності:**
- `analyzer`: має fallback через `resolve_and_update_pair`
- `new_tokens`: оновлює `first_pool_created_at`

**Рішення:**
Винести в окрему функцію (або залишити як є - різні контексти)

**Економія:** ~20-30 рядків (якщо об'єднати)

---

### 10. ⚠️ ДУБЛІКАТ: `pool = await get_db_pool()` + `async with pool.acquire() as conn:`

**Проблема:**
Однаковий патерн повторюється в багатьох місцях:

**Використання:**
- `_v3_analyzer_jupiter.py`: 5 разів
- `_v2_trades_history.py`: 5 разів
- `_v3_new_tokens.py`: 1 раз
- `_v3_cleaner.py`: 1 раз

**Приклад:**
```python
pool = await get_db_pool()
async with pool.acquire() as conn:
    # ... код ...
```

**Рішення:**
Можна використати декоратор або context manager, але це може бути over-engineering

**Економія:** ~2 рядки на використання (всього ~24 рядки)

---

## 📊 ПІДСУМОК ДУБЛІКАТІВ

| # | Тип | Дублікат | Модулі | Рядків | Економія |
|---|-----|----------|--------|--------|----------|
| 1 | ⚠️ | INSERT INTO token_metrics_seconds | analyzer, new_tokens | ~14 | ~14 |
| 2 | 🔄 | UPDATE tokens SET stats (5m, 1h, 6h, 24h) | analyzer, new_tokens | ~32 | ~32* |
| 3 | 🔄 | UPDATE tokens SET audit fields | analyzer, new_tokens | ~18 | ~18* |
| 4 | 🔄 | Логіка pair_resolve_attempts | analyzer, new_tokens | ~19 | ~5-10 |
| 5 | ❌ | safe_numeric vs safe_get | analyzer, new_tokens | ~10 | 0 |
| 6 | ✅ | Перевірка open position | analyzer (6x), trades_history, buy_sell | ~30 | ~20-30 |
| 7 | ✅ | UPDATE token_updated_at | analyzer, new_tokens, buy_sell | ~3 | ~3 |
| 8 | ✅ | SELECT COUNT(*) FROM token_metrics_seconds | analyzer, buy_sell | ~30 | ~20-30 |
| 9 | 🔄 | Логіка оновлення token_pair | analyzer, new_tokens | ~50 | ~10-20 |
| 10 | ✅ | pool = await get_db_pool() | всі модулі | ~24 | ~24 |

**Легенда:**
- ✅ **ПОВНИЙ ДУБЛІКАТ** - можна винести без змін
- ⚠️ **ЧАСТКОВИЙ ДУБЛІКАТ** - можна винести з параметрами
- 🔄 **СХОЖИЙ КОД** - потрібна стандартизація перед винесенням
- ❌ **НЕ ДУБЛІКАТ** - різні призначення

**\* Потрібна стандартизація логіки конвертації перед винесенням**

**ЗАГАЛЬНА ЕКОНОМІЯ:** ~120-160 рядків (після стандартизації логіки)

---

## 🎯 РЕКОМЕНДАЦІЇ

### Пріоритет 1 (повні дублікати - можна винести без змін):
1. ✅ Винести перевірку open position в окрему функцію `has_open_position()`
2. ✅ Винести `SELECT COUNT(*) FROM token_metrics_seconds` в окрему функцію `get_token_iterations_count()`
3. ✅ Винести `UPDATE token_updated_at` в окрему функцію (опціонально)

### Пріоритет 2 (часткові дублікати - можна винести з параметрами):
4. ⚠️ Винести `INSERT INTO token_metrics_seconds` в окрему функцію з параметром `include_holder_count`

### Пріоритет 3 (схожий код - потрібна стандартизація):
5. 🔄 **Спочатку стандартизувати логіку конвертації** (`safe_numeric()` vs `float(...)`)
6. 🔄 Потім винести `UPDATE tokens SET stats` в окрему функцію
7. 🔄 Потім винести `UPDATE tokens SET audit` в окрему функцію
8. 🔄 Винести базову частину логіки `pair_resolve_attempts` (інкремент/скидання)

### Пріоритет 4 (не дублікати):
9. ❌ `safe_numeric` vs `safe_get` - **НЕ об'єднувати** (різні призначення)
10. 🔄 Логіка оновлення `token_pair` - залишити як є (різні контексти)

### Пріоритет 5 (опціонально):
11. ⚠️ Винести `pool = await get_db_pool()` (можливо over-engineering)

---

## 📝 ВИСНОВОК

Знайдено **~160-200 рядків дублікатів** між модулями. Основні проблеми:

1. **Дублювання логіки збереження метрик** (`token_metrics_seconds`)
2. **Дублювання логіки оновлення статистики** (stats для 5m, 1h, 6h, 24h)
3. **Дублювання логіки оновлення audit полів**
4. **Повторювані перевірки open position**
5. **Повторювані SQL запити** (COUNT, UPDATE token_updated_at)

**Рекомендація:** Почати з пріоритету 1 - це дасть найбільшу економію та покращить підтримуваність коду.

