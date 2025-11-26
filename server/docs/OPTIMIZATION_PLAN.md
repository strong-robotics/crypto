# План оптимізації модулів на основі аналізу дублікатів

**Дата:** 2024  
**Модулі для оптимізації:** `_v3_analyzer_jupiter.py`, `_v2_trades_history.py`, `_v3_new_tokens.py`, `_v2_buy_sell.py`

---

## 🎯 СТВОРЕНО НОВИЙ МОДУЛЬ: `_v3_db_utils.py`

Модуль містить універсальні функції для роботи з БД, які використовуються в кількох місцях.

### Функції в модулі:

1. ✅ `has_open_position(conn, token_id)` - перевірка відкритої позиції
2. ✅ `get_open_position(conn, token_id)` - отримання деталей відкритої позиції
3. ✅ `get_token_iterations_count(conn, token_id)` - підрахунок ітерацій
4. ✅ `update_token_updated_at(conn, token_id)` - оновлення timestamp
5. ✅ `save_token_metrics(...)` - збереження метрик (з опціональним `holder_count`)
6. ✅ `safe_numeric(value, max_val)` - конвертація з обмеженням
7. ✅ `update_token_stats(conn, token_id, stats, period, convert_func)` - оновлення статистики
8. ✅ `update_token_audit(conn, token_id, audit, convert_func)` - оновлення audit полів
9. ✅ `update_pair_resolve_attempts(conn, token_id, is_valid_pair)` - оновлення лічильника спроб

---

## 📋 ПЛАН ОПТИМІЗАЦІЇ ПО ПРІОРИТЕТАХ

### ✅ ПРІОРИТЕТ 1: Повні дублікати (можна винести без змін)

#### 1.1. Перевірка open position
**Модулі для оновлення:**
- `_v3_analyzer_jupiter.py` (6 місць)
- `_v2_trades_history.py` (1 місце)
- `_v2_buy_sell.py` (1 місце)

**Замінити:**
```python
# БУЛО:
open_pos_check = await conn.fetchrow(
    "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
    token_id
)
if open_pos_check:
    # ...

# СТАЛО:
from _v3_db_utils import has_open_position
if await has_open_position(conn, token_id):
    # ...
```

**Або для отримання деталей:**
```python
# БУЛО:
open_position = await conn.fetchrow(
    """
    SELECT id, wallet_id, entry_token_amount
    FROM wallet_history
    WHERE token_id=$1 AND exit_iteration IS NULL
    LIMIT 1
    """,
    token_id
)

# СТАЛО:
from _v3_db_utils import get_open_position
open_position = await get_open_position(conn, token_id)
```

**Економія:** ~20-30 рядків

---

#### 1.2. SELECT COUNT(*) FROM token_metrics_seconds
**Модулі для оновлення:**
- `_v3_analyzer_jupiter.py` (4+ місць)
- `_v2_buy_sell.py` (3 місця)

**Замінити:**
```python
# БУЛО:
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

# СТАЛО:
from _v3_db_utils import get_token_iterations_count
iterations_count = await get_token_iterations_count(conn, token_id)
```

**Економія:** ~20-30 рядків

---

#### 1.3. UPDATE token_updated_at
**Модулі для оновлення:**
- `_v3_analyzer_jupiter.py` (1 місце)
- `_v3_new_tokens.py` (1 місце)
- `_v2_buy_sell.py` (2 місця)

**Замінити:**
```python
# БУЛО:
await conn.execute("UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP WHERE id = $1", token_id)

# СТАЛО:
from _v3_db_utils import update_token_updated_at
await update_token_updated_at(conn, token_id)
```

**Економія:** ~3 рядки

---

### ⚠️ ПРІОРИТЕТ 2: Часткові дублікати (можна винести з параметрами)

#### 2.1. INSERT INTO token_metrics_seconds
**Модулі для оновлення:**
- `_v3_analyzer_jupiter.py` (рядки 802-817)
- `_v3_new_tokens.py` (рядки 276-290)

**Замінити:**
```python
# БУЛО (_v3_analyzer_jupiter.py):
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

# СТАЛО:
from _v3_db_utils import save_token_metrics
await save_token_metrics(
    conn, token_id, ts, usd_p, liq, fdv, mcap, pblk, 
    jupiter_slot=pblk, holder_count=holders
)
```

```python
# БУЛО (_v3_new_tokens.py):
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

# СТАЛО:
from _v3_db_utils import save_token_metrics
await save_token_metrics(
    conn, token_id, ts, usd_p, liq, fdv, mcap, pblk, 
    jupiter_slot=pblk
    # holder_count не передаємо (буде None)
)
```

**Економія:** ~14 рядків

---

### 🔄 ПРІОРИТЕТ 3: Схожий код (потрібна стандартизація)

#### 3.1. UPDATE tokens SET stats (5m, 1h, 6h, 24h)
**Модулі для оновлення:**
- `_v3_analyzer_jupiter.py` (рядки 685-716)
- `_v3_new_tokens.py` (рядки 233-264)

**⚠️ ВАЖЛИВО:** Спочатку потрібно стандартизувати логіку конвертації!

**Проблема:**
- `analyzer` використовує `safe_numeric()` (обмежує значення)
- `new_tokens` використовує `float(...) if ... is not None else None` (не обмежує)

**Рішення:**
1. **Стандартизувати:** Використовувати `safe_numeric()` в обох місцях
2. **Винести в функцію:**

```python
# БУЛО (_v3_analyzer_jupiter.py):
for period in ['5m', '1h', '6h', '24h']:
    stats = data.get(f'stats{period}', {})
    if stats:
        period_suffix = f"_{period}"
        await conn.execute(f"""
            UPDATE tokens SET
                price_change{period_suffix} = $2,
                holder_change{period_suffix} = $3,
                # ... інші поля
            WHERE id = $1
        """, 
            token_id,
            safe_numeric(stats.get('priceChange')),
            # ... інші поля
        )

# СТАЛО:
from _v3_db_utils import update_token_stats, safe_numeric
for period in ['5m', '1h', '6h', '24h']:
    stats = data.get(f'stats{period}', {})
    if stats:
        await update_token_stats(conn, token_id, stats, period, convert_func=safe_numeric)
```

```python
# БУЛО (_v3_new_tokens.py):
for period in ['5m', '1h', '6h', '24h']:
    stats = token_data.get(f'stats{period}', {})
    if stats:
        suffix = f'_{period}'
        await conn.execute(f"""
            UPDATE tokens SET
                price_change{suffix} = $2,
                # ... інші поля
            WHERE id = $1
        """,
            token_id,
            float(stats.get('priceChange', 0)) if stats.get('priceChange') is not None else None,
            # ... інші поля
        )

# СТАЛО:
from _v3_db_utils import update_token_stats, safe_numeric
for period in ['5m', '1h', '6h', '24h']:
    stats = token_data.get(f'stats{period}', {})
    if stats:
        await update_token_stats(conn, token_id, stats, period, convert_func=safe_numeric)
```

**Економія:** ~32 рядки (після стандартизації)

---

#### 3.2. UPDATE tokens SET audit fields
**Модулі для оновлення:**
- `_v3_analyzer_jupiter.py` (рядки 718-735)
- `_v3_new_tokens.py` (рядки 214-231)

**⚠️ ВАЖЛИВО:** Спочатку потрібно стандартизувати логіку конвертації!

**Замінити:**
```python
# БУЛО (_v3_analyzer_jupiter.py):
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

# СТАЛО:
from _v3_db_utils import update_token_audit, safe_numeric
audit = data.get('audit', {})
if audit:
    await update_token_audit(conn, token_id, audit, convert_func=safe_numeric)
```

```python
# БУЛО (_v3_new_tokens.py):
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

# СТАЛО:
from _v3_db_utils import update_token_audit, safe_numeric
audit = token_data.get('audit', {})
if audit:
    await update_token_audit(conn, token_id, audit, convert_func=safe_numeric)
```

**Економія:** ~18 рядків (після стандартизації)

---

#### 3.3. Логіка pair_resolve_attempts
**Модулі для оновлення:**
- `_v3_analyzer_jupiter.py` (рядки 765-783)
- `_v3_new_tokens.py` (рядки 201-212)

**Замінити базову частину (інкремент/скидання):**
```python
# БУЛО (_v3_analyzer_jupiter.py):
if not updated_pair and (...):
    await conn.execute(
        "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", 
        token_id
    )
else:
    if current_pair and current_pair != token_addr:
        await conn.execute(
            "UPDATE tokens SET pair_resolve_attempts = 0 WHERE id = $1", 
            token_id
        )

# СТАЛО:
from _v3_db_utils import update_pair_resolve_attempts
is_valid = (updated_pair is not None) or (current_pair and current_pair != token_addr)
await update_pair_resolve_attempts(conn, token_id, is_valid)
```

**Економія:** ~5-10 рядків (тільки базова частина)

---

## 📊 ОЧІКУВАНА ЕКОНОМІЯ

| Пріоритет | Оптимізація | Економія рядків |
|-----------|-------------|-----------------|
| 1.1 | Перевірка open position | ~20-30 |
| 1.2 | SELECT COUNT(*) FROM token_metrics_seconds | ~20-30 |
| 1.3 | UPDATE token_updated_at | ~3 |
| 2.1 | INSERT INTO token_metrics_seconds | ~14 |
| 3.1 | UPDATE tokens SET stats | ~32 |
| 3.2 | UPDATE tokens SET audit | ~18 |
| 3.3 | pair_resolve_attempts | ~5-10 |
| **РАЗОМ** | | **~112-145 рядків** |

---

## ⚠️ ВАЖЛИВІ ЗАУВАЖЕННЯ

### 1. Стандартизація логіки конвертації
**Проблема:** `safe_numeric()` vs `float(...) if ... is not None else None`

**Рішення:**
- Використовувати `safe_numeric()` в усіх місцях (захист від переповнення)
- Якщо потрібно без обмеження - створити окрему функцію `safe_float()` без обмеження

### 2. Порядок застосування
1. **Спочатку** створити `_v3_db_utils.py` з усіма функціями
2. **Потім** застосувати пріоритет 1 (повні дублікати)
3. **Потім** стандартизувати логіку конвертації
4. **Потім** застосувати пріоритет 3 (схожий код)

### 3. Тестування
Після кожної зміни перевіряти:
- Компіляція коду
- Функціональність (покупка/продаж)
- Архівація токенів
- Оновлення метрик

---

## 🚀 НАСТУПНІ КРОКИ

1. ✅ Створити `_v3_db_utils.py` (виконано)
2. ⏳ Застосувати пріоритет 1 (повні дублікати)
3. ⏳ Стандартизувати логіку конвертації
4. ⏳ Застосувати пріоритет 2-3 (часткові дублікати та схожий код)
5. ⏳ Тестування

