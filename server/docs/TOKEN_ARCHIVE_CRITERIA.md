# Критерії автоматичного архівування та переміщення токенів

## Загальна інформація

Проект використовує два механізми видалення токенів:
1. **Архівування** (`tokens_history`, `token_metrics_seconds_history`, `trades_history`) - для токенів з достатньою історією
2. **Переміщення в bad таблиці** (`bad_tokens`, `bad_token_metrics`) - для токенів без достатньої історії

**Критичне правило:** Токени з відкритими позиціями (`wallet_id IS NOT NULL` або `exit_iteration IS NULL` в `wallet_history`) **НІКОЛИ** не архівуються та не видаляються автоматично.

---

## 1. Архівування (`archive_token`)

### Критерій рішення: `ARCHIVE_MIN_ITERATIONS`

**Поріг:** `ARCHIVE_MIN_ITERATIONS` = **1000 ітерацій** (з config.py)

**Логіка:**
- Якщо `iteration_count >= 1000` → **архівується** в `tokens_history`
- Якщо `iteration_count < 1000` → **purge** (повне видалення)

**Що робить:**
1. Копіює токен в `tokens_history` з `archived_at = CURRENT_TIMESTAMP`
2. Копіює метрики в `token_metrics_seconds_history`
3. Копіює трейди в `trades_history`
4. Видаляє з `tokens`, `token_metrics_seconds`, `trades`

**Захист:** Перевіряє наявність відкритої позиції перед архівуванням

---

## 2. Переміщення в bad таблиці (`_move_to_bad_tables`)

### Критерій рішення: `ARCHIVE_MIN_ITERATIONS`

**Поріг:** `ARCHIVE_MIN_ITERATIONS` = **1000 ітерацій** (з config.py)

**Логіка:**
- Якщо `iteration_count >= 1000` → **архівується** (не в bad)
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці**

**Що робить:**
1. Копіює токен в `bad_tokens` з `removed_reason` та `removed_at`
2. Копіює метрики в `bad_token_metrics`
3. Видаляє з `tokens`, `token_metrics_seconds`, `trades`

**Виняток:** Для `reason = "no_pair"` або `"transfer_only"` - **пряме видалення без копіювання** в bad таблиці

---

## 3. Критерії виявлення "поганих" токенів

### 3.1. No Pair (без торгової пари)

**Функція:** `_find_no_pair_tokens()`

**Критерії:**
- `token_pair IS NULL` АБО `token_pair = token_address`
- `wallet_id IS NULL` або `wallet_id = 0` (не прив'язаний до кошелька)

**Обробка:**
- **Пряме видалення** без копіювання в bad таблиці (`reason = "no_pair"`)
- Викликається через `_drain_no_pair_tokens()` в `run_cleanup()`

**Конфігурація:** Немає окремих параметрів, обробляється завжди

---

### 3.2. No Swap (без свопів після другого коридору)

**Функція:** `_find_no_swap_tokens()`

**Критерії:**
- `no_swap_after_second_corridor = TRUE` (прапорець встановлюється аналізатором)

**Обробка:**
- Переміщується в bad таблиці з `reason = "no_swap"`
- Викликається через `_drain_no_swap_tokens()` в `run_cleanup()`

**Конфігурація:** Немає окремих параметрів, обробляється завжди

---

### 3.3. No Median (без медіанних об'ємів)

**Функція:** `_find_no_median_tokens()`

**Критерії:**
- `median_amount_usd IS NULL`
- `wallet_id IS NULL` або `wallet_id = 0`
- Кількість метрик `>= 50` (`MEDIAN_MIN_ITERATIONS = 50`)

**Обробка:**
- Якщо `iteration_count >= 1000` → **архівується**
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці** з `reason = "no_median"`
- Викликається через `_drain_no_median_tokens()` в `run_cleanup()`

**Конфігурація:** `MEDIAN_MIN_ITERATIONS = 50` (50 ітерацій мінімум)

---

### 3.4. Zero Tail (нульовий хвіст)

**Функція:** `_find_flagged_tokens(conn, "zero_tail", limit)`

**Критерії виявлення (встановлюються аналізатором):**
- **Варіант 1:** Останні **120 секунд** (`ZERO_TAIL_CONSEC_SEC = 120`) мають `usd_price = 0` або `NULL` та `mcap = 0` або `NULL`
- **Варіант 2:** `fill_ratio <= 0.40` (`ZERO_TAIL_MIN_FILL_RATIO`) при `live_seconds >= 180` (`ZERO_TAIL_MIN_FILL_LIFE_SEC`)

**Прапорець:** `cleaner_flagged = TRUE`, `cleaner_flag_reason = 'zero_tail'`

**Обробка:**
- Якщо `iteration_count >= 1000` → **архівується**
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці** з `reason = "zero_tail"`

**Конфігурація:**
- `ZERO_TAIL_CONSEC_SEC = 120` (120 секунд послідовних нулів)
- `ZERO_TAIL_MIN_FILL_RATIO = 0.40` (40% заповнення метрик)
- `ZERO_TAIL_MIN_FILL_LIFE_SEC = 180` (3 хвилини мінімального віку)

---

### 3.5. Frozen Price (заморожена ціна)

**Функція:** `_find_flagged_tokens(conn, "frozen_price", limit)`

**Критерії виявлення (встановлюються аналізатором):**
- Останні **120 секунд** (`FROZEN_PRICE_CONSEC_SEC = 120`) мають однакову ціну (різниця `<= 1e-10`)

**Прапорець:** `cleaner_flagged = TRUE`, `cleaner_flag_reason = 'frozen_price'`

**Обробка:**
- Якщо `iteration_count >= 1000` → **архівується**
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці** з `reason = "frozen_price"`

**Конфігурація:**
- `FROZEN_PRICE_CONSEC_SEC = 120` (**120 секунд** послідовно однакова ціна)
- `FROZEN_PRICE_EQUAL_EPS = 1e-10` (точність порівняння цін)

---

### 3.6. No Price (без ціни після другого коридору)

**Функція:** `_find_no_price_tokens()`

**Критерії:**
- Вік токена `>= 730 секунд` (`PRICE_CORRIDOR_PRE_END = 730`)
- Немає жодної метрики з `usd_price IS NOT NULL AND usd_price > 0`

**Обробка:**
- Встановлюється прапорець `cleaner_flagged = TRUE`, `cleaner_flag_reason = 'no_price'`
- Обробляється в наступному циклі cleaner як flagged token
- Якщо `iteration_count >= 1000` → **архівується**
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці** з `reason = "no_price"`

**Конфігурація:** `PRICE_CORRIDOR_PRE_END = 730` (730 секунд = ~12 хвилин)

---

### 3.7. Low Holders (мало холдерів)

**Функція:** `_find_low_holder_tokens()`

**Критерії:**
- Кількість метрик `>= 500` (`CLEANER_LOW_HOLDER_ITER_THRESHOLD = 500`)
- `holder_count < 300` (`CLEANER_LOW_HOLDER_MIN_COUNT = 300`)
- `wallet_id IS NULL` (не прив'язаний до кошелька)

**Обробка:**
- Встановлюється прапорець `cleaner_flagged = TRUE`, `cleaner_flag_reason = 'low_holders'`
- Обробляється в наступному циклі cleaner як flagged token
- Якщо `iteration_count >= 1000` → **архівується**
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці** з `reason = "low_holders"`

**Конфігурація:**
- `CLEANER_LOW_HOLDER_ITER_THRESHOLD = 500` (500 ітерацій мінімум)
- `CLEANER_LOW_HOLDER_MIN_COUNT = 300` (мінімум 300 холдерів)

---

### 3.8. Early Trade Type Check (рання перевірка на 20-й секунді)

**Місце:** `_v3_analyzer_jupiter.py` → `save_token_data()`

**Критерії:**
- `iterations_count >= 20`
- `has_real_trading IS NULL` (ще не перевірявся) АБО `has_real_trading = FALSE`
- Немає торгової пари (`token_pair IS NULL`)

**Обробка:**
- Викликається `_archive_or_purge_token()`:
  - Якщо `iteration_count >= ARCHIVE_MIN_ITERATIONS` → **архівується**
  - Якщо `iteration_count < ARCHIVE_MIN_ITERATIONS` → **purge** (повне видалення)

**Конфігурація:** Фіксовано на 20 секундах

---

### 3.9. Bad Pattern (поганий паттерн)

**Місце:** `_v3_analyzer_jupiter.py` → `save_token_data()`

**Критерії:**
- `pattern_code` входить в список bad patterns (`black_hole`, `flatliner`, `rug_prequel`, тощо)
- Немає відкритої позиції (`no_entry.none = TRUE`)
- `iterations >= 14400` (`BAD_PATTERN_HISTORY_READY_ITERS = 14400` = 1 година)

**Обробка:**
- **ЗАХИЩЕНО:** Перевіряє відкриту позицію перед архівуванням
- Якщо немає відкритої позиції → може бути архівований (через cleaner)
- Якщо є відкрита позиція → тільки `history_ready = TRUE`, не архівується
- Якщо `iteration_count >= 1000` → **архівується**
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці** з `reason = "bad_pattern"`

**Конфігурація:** `BAD_PATTERN_HISTORY_READY_ITERS = 14400` (14400 секунд = 4 години)

---

### 3.10. Bad Decision (NOT) - рішення "не входити"

**Місце:** `_v3_analyzer_jupiter.py` → `save_token_data()`

**Критерії:**
- `pattern_segment_decision = "not"`
- Немає відкритої позиції (`no_entry.none = TRUE`)
- `iterations >= 14400` (`BAD_PATTERN_HISTORY_READY_ITERS = 14400` = 1 година)

**Обробка:**
- **ЗАХИЩЕНО:** Перевіряє відкриту позицію перед архівуванням
- Якщо немає відкритої позиції → може бути архівований (через cleaner)
- Якщо є відкрита позиція → тільки `history_ready = TRUE`, не архівується
- Якщо `iteration_count >= 1000` → **архівується**
- Якщо `iteration_count < 1000` → **переміщується в bad таблиці** з `reason = "bad_decision"`

**Конфігурація:** `BAD_PATTERN_HISTORY_READY_ITERS = 14400` (14400 секунд = 4 години)

---

## 4. Процес очистки (`run_cleanup`)

### Порядок обробки:

1. **No Swap** - `_drain_no_swap_tokens()` (завжди першим)
2. **No Median** - `_drain_no_median_tokens()` (якщо `MEDIAN_MIN_ITERATIONS > 0`)
3. **Zero Tail** - `_process_flagged_tokens(..., "zero_tail", ...)`
4. **Frozen Price** - `_process_flagged_tokens(..., "frozen_price", ...)`
5. **No Price** - встановлює прапорець (обробляється в наступному циклі)
6. **Orphan (No Pair)** - `_move_to_bad_tables(..., "orphan")`
7. **Low Holders** - встановлює прапорець (обробляється в наступному циклі)

### Фільтрація токенів з кошельками:

**Функція:** `_filter_tokens_without_wallet()`

**Правило:** Всі критерії автоматично фільтрують токени, які:
- `wallet_id IS NOT NULL` або `wallet_id != 0`
- Мають відкриту позицію в `wallet_history` (`exit_iteration IS NULL`)

**Результат:** Токени з відкритими позиціями **НІКОЛИ** не видаляються автоматично.

---

## 5. Конфігураційні параметри (актуальні значення з config.py)

| Параметр | Поточне значення | Опис |
|----------|------------------|------|
| `ARCHIVE_MIN_ITERATIONS` | **1000** | Мінімальна кількість ітерацій для архівування (замість purge/bad) |
| `MEDIAN_MIN_ITERATIONS` | **50** | Мінімальна кількість ітерацій для перевірки медіани |
| `ZERO_TAIL_CONSEC_SEC` | **120** | Кількість послідовних секунд з нульовою ціною для zero tail |
| `ZERO_TAIL_MIN_FILL_RATIO` | **0.40** | Мінімальний коефіцієнт заповнення метрик (40% від реального віку) |
| `ZERO_TAIL_MIN_FILL_LIFE_SEC` | **180** | Мінімальний вік токена для перевірки fill ratio (3 хвилини) |
| `FROZEN_PRICE_CONSEC_SEC` | **120** | Кількість послідовних секунд з однаковою ціною для frozen price |
| `FROZEN_PRICE_EQUAL_EPS` | **1e-10** | Точність порівняння цін для frozen price (різниця <= 1e-10) |
| `PRICE_CORRIDOR_PRE_END` | **730** | Кінець першого коридору (секунди) - для no_price check |
| `CLEANER_LOW_HOLDER_ITER_THRESHOLD` | **500** | Поріг ітерацій для low holders (токен має прожити >=500 сек) |
| `CLEANER_LOW_HOLDER_MIN_COUNT` | **300** | Мінімальна кількість холдерів для long-lived токена |
| `BAD_PATTERN_HISTORY_READY_ITERS` | **14400** | Поріг ітерацій для bad pattern/decision (1 година = 3600 сек) |

---

## 6. Детальна таблиця: Коли в архів, коли в bad таблиці

### Правило розподілу:

**Якщо `iteration_count >= ARCHIVE_MIN_ITERATIONS` (1000)** → **Архівується** в `tokens_history`  
**Якщо `iteration_count < ARCHIVE_MIN_ITERATIONS` (1000)** → **Переміщується в bad таблиці** (`bad_tokens`)

| Критерій | Умова спрацювання | Поріг для архіву | Поріг для bad | Примітки |
|----------|-------------------|-------------------|---------------|----------|
| **1. No Pair** | `token_pair IS NULL` або `token_pair = token_address` | ❌ Не архівується | ✅ Завжди в bad | Пряме видалення без копіювання в bad (reason="no_pair") |
| **2. No Swap** | `no_swap_after_second_corridor = TRUE` | ❌ Не архівується | ✅ Завжди в bad | reason="no_swap" |
| **3. No Median** | `median_amount_usd IS NULL` + `iterations >= 50` | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → bad | reason="no_median" |
| **4. Zero Tail** | Останні **120 сек** з `usd_price=0` або `fill_ratio <= 0.40` при `age >= 180 сек` | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → bad | reason="zero_tail" |
| **5. Frozen Price** | Останні **120 сек** з однаковою ціною (різниця `<= 1e-10`) | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → bad | reason="frozen_price" |
| **6. No Price** | Вік `>= 730 сек` + немає жодної метрики з `usd_price > 0` | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → bad | reason="no_price" |
| **7. Low Holders** | `iterations >= 500` + `holder_count < 300` | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → bad | reason="low_holders" |
| **8. Early Trade Type** | На **20-й секунді**: `has_real_trading = FALSE` або немає пари | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → purge | Повне видалення (не в bad) |
| **9. Bad Pattern** | Поганий паттерн + `iterations >= 14400` (1 година) | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → bad | Тільки якщо немає відкритої позиції |
| **10. Bad Decision (NOT)** | `pattern_segment_decision = "not"` + `iterations >= 14400` | Якщо `iterations >= 1000` → архів | Якщо `iterations < 1000` → bad | Тільки якщо немає відкритої позиції |

### Приклади:

**Приклад 1: Zero Tail з 500 ітераціями**
- Умова: Останні 120 секунд з нульовою ціною
- `iteration_count = 500` (< 1000)
- **Результат:** Переміщується в `bad_tokens` з `reason = "zero_tail"`

**Приклад 2: Zero Tail з 1500 ітераціями**
- Умова: Останні 120 секунд з нульовою ціною
- `iteration_count = 1500` (>= 1000)
- **Результат:** Архівується в `tokens_history`

**Приклад 3: Frozen Price з 800 ітераціями**
- Умова: Останні 120 секунд з однаковою ціною
- `iteration_count = 800` (< 1000)
- **Результат:** Переміщується в `bad_tokens` з `reason = "frozen_price"`

**Приклад 4: No Pair**
- Умова: `token_pair IS NULL`
- Будь-яка кількість ітерацій
- **Результат:** Пряме видалення (не в bad, не в архів)

**Приклад 5: Early Trade Type на 20-й секунді**
- Умова: На 20-й секунді немає swap-ів
- `iteration_count = 20` (< 1000)
- **Результат:** Повне видалення (purge, не в bad)

---

## 6. Таблиці призначення

### Архівні таблиці (для токенів з історією):
- `tokens_history` - архів токенів
- `token_metrics_seconds_history` - архів метрик
- `trades_history` - архів трейдів

### Bad таблиці (для токенів без достатньої історії):
- `bad_tokens` - токени з причиною видалення (`removed_reason`)
- `bad_token_metrics` - метрики видалених токенів

### Прапорці в таблиці `tokens`:
- `cleaner_flagged` - чи позначений для очистки
- `cleaner_flag_reason` - причина позначення
- `cleaner_flag_iteration` - ітерація позначення
- `cleaner_flagged_at` - час позначення

---

## 7. Важливі зауваження

1. **Захист від втрати коштів:** Всі функції архівування та видалення перевіряють наявність відкритих позицій перед виконанням операцій.

2. **Атомарність:** Всі операції архівування виконуються в транзакціях для забезпечення цілісності даних.

3. **Конкурентність:** Cleaner використовує PostgreSQL advisory locks (`pg_try_advisory_lock`) для запобігання паралельному виконанню.

4. **Dry-run режим:** Cleaner підтримує dry-run режим для тестування без фактичного видалення.

5. **Batch обробка:** Всі операції виконуються батчами для оптимізації продуктивності.

