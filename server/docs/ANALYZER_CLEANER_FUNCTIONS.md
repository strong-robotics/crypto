# Analyzer & Cleaner Functions Documentation

## `server/_v3_analyzer_jupiter.py`

### Class: `JupiterAnalyzerV3`

#### `__init__(self)`
**Призначення:** Ініціалізація аналізатора, завантаження моделі сегментів, налаштування параметрів.
**Викликається:** При створенні екземпляра через `get_analyzer()`.
**Використання:** ✅ Активно використовується.

#### `ensure_session(self)`
**Призначення:** Створює HTTP сесію для запитів до Jupiter API, створює таблицю `token_metrics_seconds` якщо не існує.
**Викликається:** В `get_jupiter_data()`, `_scan_loop()`.
**Використання:** ✅ Активно використовується.

#### `_load_segment_model(self)`
**Призначення:** Завантажує ML модель для прогнозування паттернів сегментів з файлу `models/pattern_segments.pkl`.
**Викликається:** В `__init__()`.
**Використання:** ✅ Активно використовується.

#### `_normalize_segment_label(value: Optional[str]) -> str`
**Призначення:** Нормалізує мітки сегментів ("super" → "best", перевіряє на "unknown").
**Викликається:** В `_update_segment_predictions()`, `_segments_allow_entry()`.
**Використання:** ✅ Активно використовується.

#### `_detect_post_entry_drop(prices, entry_sec, post_entry_end, drop_threshold) -> bool`
**Призначення:** Виявляє значне падіння ціни після точки входу (155-170s), щоб уникнути покупки токенів, які падають одразу після входу.
**Викликається:** В `_update_segment_predictions()` (рядок 442).
**Використання:** ✅ Активно використовується.

#### `_detect_liquidity_withdraw(total_points, recent_rows) -> Optional[int]`
**Призначення:** Виявляє виведення ліквідності (плоска/нульова ціна) на основі останніх записів метрик.
**Викликається:** В `_update_segment_predictions()` (рядок 399).
**Використання:** ✅ Активно використовується.

#### `_segments_allow_entry(labels: List[str]) -> bool`
**Призначення:** Перевіряє, чи дозволено вхід на основі прогнозованих міток сегментів. Блокує вхід якщо є 2+ "middle" сегменти або є "bad/risk/flat/unknown".
**Викликається:** В `_update_segment_predictions()` (рядок 303), `save_token_data()` (рядок 1305).
**Використання:** ✅ Активно використовується.

#### `_update_segment_predictions(conn, token_id) -> Optional[List[str]]`
**Призначення:** Головна функція прогнозування: читає метрики, обчислює фічі для 3 сегментів, прогнозує мітки через ML модель, перевіряє swap'и на checkpoint'ах (250s, 700s, 1000s), виявляє виведення ліквідності та падіння після входу.
**Викликається:** В `save_token_data()` (рядок 996) після запису метрик.
**Використання:** ✅ Активно використовується.

#### `close(self)`
**Призначення:** Закриває HTTP сесію.
**Викликається:** При зупинці аналізатора (не використовується в основному циклі).
**Використання:** ⚠️ Можливо не використовується.

#### `get_tokens_batch() -> List[Dict[str, Any]]`
**Призначення:** Отримує батч токенів для обробки з БД, використовує round-robin з offset для рівномірного розподілу.
**Викликається:** В `_scan_loop()` (рядок 1753), `_analyzer_tick()` в scheduler (рядок 47).
**Використання:** ✅ Активно використовується.

#### `get_jupiter_data(tokens) -> Dict[str, Any]`
**Призначення:** Робить запит до Jupiter Search API для отримання даних про токени (ціна, ліквідність, статистика).
**Викликається:** В `_scan_loop()` (рядок 1760), `_analyzer_tick()` в scheduler (рядок 52), `refresh_missing_jupiter_data()`.
**Використання:** ✅ Активно використовується.

#### `save_token_data(token_id, data) -> bool`
**Призначення:** Головна функція збереження: оновлює дані токена в БД, записує метрики в `token_metrics_seconds`, перевіряє swap'и на 20-й секунді (видаляє токени без swap'ів), викликає `_update_segment_predictions()`, обробляє auto-buy/auto-sell логіку, виявляє corridor drops.
**Викликається:** В `_scan_loop()` (рядок 1773), `_analyzer_tick()` в scheduler (рядок 73), `refresh_missing_jupiter_data()` (рядок 1836).
**Використання:** ✅ Активно використовується.

#### `_set_final_decision(conn, token_id, is_buy) -> None`
**Призначення:** Зберігає фінальне рішення (buy/not) в `tokens.pattern_segment_decision`.
**Викликається:** В `save_token_data()` (рядок 1350).
**Використання:** ✅ Активно використовується.

#### `_get_corridor_windows() -> List[Dict[str, Any]]`
**Призначення:** Повертає конфігурацію вікон для виявлення corridor drops (pre/final коридори).
**Викликається:** В `_apply_price_corridor_guard()` (рядок 1682).
**Використання:** ✅ Активно використовується.

#### `_calc_window_drop_recovery(prices, start_iter, end_iter) -> Optional[List[float]]`
**Призначення:** Обчислює відсоток падіння та відновлення в заданому вікні цін.
**Викликається:** В `_apply_price_corridor_guard()` (рядок 1734).
**Використання:** ✅ Активно використовується.

#### `_flag_corridor_drop(conn, token_id, label, stage, drop_pct, recovery_pct) -> None`
**Призначення:** Позначає токен як `corridor_drop` в `tokens.pattern_code` при виявленні значного падіння в коридорі.
**Викликається:** В `_apply_price_corridor_guard()` (рядок 1740).
**Використання:** ✅ Активно використовується.

#### `_apply_price_corridor_guard(conn, token_id) -> bool`
**Призначення:** Виявляє значні падіння ціни в коридорах (pre/final) і архівує токени без відкритих позицій.
**Викликається:** В `save_token_data()` (рядок 1203).
**Використання:** ✅ Активно використовується.

#### `_scan_loop(self)`
**Призначення:** Головний цикл аналізатора: отримує батч токенів, запитує Jupiter API, зберігає дані.
**Викликається:** В `start()` через `asyncio.create_task()` (рядок 1789).
**Використання:** ✅ Активно використовується.

#### `start(self) -> Dict[str, Any]`
**Призначення:** Запускає аналізатор (створює task для `_scan_loop()`).
**Викликається:** В `main.py` через `get_analyzer()`.
**Використання:** ✅ Активно використовується.

#### `stop(self) -> Dict[str, Any]`
**Призначення:** Зупиняє аналізатор.
**Викликається:** При зупинці сервера (не використовується в основному циклі).
**Використання:** ⚠️ Можливо не використовується.

### Module-level functions

#### `get_analyzer() -> JupiterAnalyzerV3`
**Призначення:** Singleton для отримання екземпляра аналізатора.
**Викликається:** В `main.py`, `_v3_jupiter_scheduler.py`, `cli.py`.
**Використання:** ✅ Активно використовується.

#### `refresh_missing_jupiter_data(batch_size, delay_seconds, force_rescan) -> Dict`
**Призначення:** Утиліта для оновлення даних токенів без торгової пари або всіх токенів (якщо `force_rescan=True`).
**Викликається:** В `cli.py` через команду `refresh-jupiter`.
**Використання:** ✅ Активно використовується.

#### `refresh_until_three(debug, batch_size, delay_seconds, max_rounds) -> Dict`
**Призначення:** Оновлює дані токенів поки не залишиться менше 3 токенів без пари.
**Викликається:** В `cli.py` через команду `refresh-jupiter --until-three`.
**Використання:** ✅ Активно використовується.

---

## `server/_v3_cleaner.py`

### Lock functions

#### `_acquire_lock(conn) -> bool`
**Призначення:** Намагається отримати advisory lock для запобігання паралельним виконанням cleaner'а.
**Викликається:** В `run_cleanup()` (рядок 474).
**Використання:** ✅ Активно використовується.

#### `_release_lock(conn) -> None`
**Призначення:** Звільняє advisory lock.
**Викликається:** В `run_cleanup()` в блоці `finally` (рядок 593).
**Використання:** ✅ Активно використовується.

### Filter functions

#### `_filter_tokens_without_wallet(conn, ids) -> List[int]`
**Призначення:** Фільтрує токени, які не прив'язані до кошельків (`wallet_id IS NULL OR wallet_id = 0`), щоб не видаляти токени з активними позиціями.
**Викликається:** В `_process_flagged_tokens()` (рядок 357), `run_cleanup()` для `zero_tail_candidates` та `frozen_candidates` (рядки 517, 532).
**Використання:** ✅ Активно використовується.

### Find functions

#### `_find_candidates(conn, older_than_sec, limit) -> List[int]`
**Призначення:** Знаходить токени без торгової пари, які прожили мінімум `older_than_sec` секунд за метриками.
**Викликається:** В `run_cleanup()` (не використовується зараз, замінено на `_find_no_pair_tokens`).
**Використання:** ⚠️ Не використовується (залишено для сумісності).

#### `_find_no_entry_candidates(conn, max_age_sec, limit) -> List[int]`
**Призначення:** Disabled функція (повертає порожній список).
**Викликається:** Не викликається.
**Використання:** ❌ Не використовується (мусор).

#### `_find_no_entry_iterations(conn, min_iterations, limit) -> List[int]`
**Призначення:** Disabled функція (повертає порожній список).
**Викликається:** Не викликається.
**Використання:** ❌ Не використовується (мусор).

#### `_find_low_holder_tokens(conn, min_iterations, min_holders, limit) -> List[int]`
**Призначення:** Знаходить токени, які прожили довго (`>= min_iterations`), але не набрали достатньо холдерів (`< min_holders`).
**Викликається:** В `run_cleanup()` (рядок 483).
**Використання:** ✅ Активно використовується (якщо налаштовано в config).

#### `_find_no_swap_tokens(conn, limit) -> List[int]`
**Призначення:** Знаходить токени, позначені як `no_swap_after_second_corridor = TRUE` (токени без swap'ів після другого коридору).
**Викликається:** В `run_cleanup()` (рядок 495), `_drain_no_swap_tokens()` (рядок 219).
**Використання:** ✅ Активно використовується.

#### `_find_no_pair_tokens(conn, min_iterations, limit) -> List[int]`
**Призначення:** Знаходить токени без торгової пари (`token_pair IS NULL OR token_pair = token_address`).
**Викликається:** В `_drain_no_pair_tokens()` (рядок 241), `run_cleanup()` (рядок 511).
**Використання:** ✅ Активно використовується.

#### `_find_no_price_tokens(conn, limit) -> List[int]`
**Призначення:** Знаходить токени, які досягли другого коридору, але не отримали `usd_price`.
**Викликається:** Не викликається.
**Використання:** ❌ Не використовується (мусор).

#### `_find_flagged_tokens(conn, reason, limit) -> List[int]`
**Призначення:** Знаходить токени, позначені cleaner'ом з конкретною причиною (`cleaner_flag_reason`).
**Викликається:** В `run_cleanup()` для `zero_tail` та `frozen_price` (рядки 516, 531).
**Використання:** ✅ Активно використовується.

### Count functions

#### `_count_no_pair_tokens(conn) -> int`
**Призначення:** Рахує кількість токенів без торгової пари.
**Викликається:** Не викликається (закоментовано в `_drain_no_pair_tokens`).
**Використання:** ⚠️ Не використовується (залишено для логування).

#### `_count_no_swap_tokens(conn) -> int`
**Призначення:** Рахує кількість токенів з `no_swap_after_second_corridor = TRUE`.
**Викликається:** В `_drain_no_swap_tokens()` (рядки 217, 227).
**Використання:** ✅ Активно використовується.

### Drain functions

#### `_drain_no_swap_tokens(conn, batch_size) -> int`
**Призначення:** Видаляє всі токени з `no_swap_after_second_corridor = TRUE` батчами.
**Викликається:** В `run_cleanup()` (рядок 500).
**Використання:** ✅ Активно використовується.

#### `_drain_no_pair_tokens(conn, min_iterations, batch_size) -> int`
**Призначення:** Видаляє всі токени без торгової пари батчами.
**Викликається:** В `run_cleanup()` (рядок 511).
**Використання:** ✅ Активно використовується.

### Purge functions

#### `_purge_batch(conn, ids) -> Tuple[int, int, int]`
**Призначення:** Видаляє токени та їх метрики/трейди з БД (повне видалення без архівування).
**Викликається:** В `_move_to_bad_tables()` (рядок 464).
**Використання:** ✅ Активно використовується.

### Flag functions

#### `_ensure_flag_columns(conn) -> None`
**Призначення:** Створює колонки для флагів cleaner'а в таблиці `tokens` якщо не існують.
**Викликається:** В `run_cleanup()` (рядок 472).
**Використання:** ✅ Активно використовується.

#### `_flag_tokens(conn, ids, reason) -> int`
**Призначення:** Позначає токени флагами cleaner'а (`cleaner_flagged`, `cleaner_flag_reason`, `cleaner_flag_iteration`).
**Викликається:** Не викликається безпосередньо (використовується в аналізаторі для zero_tail/frozen).
**Використання:** ⚠️ Не використовується в cleaner'і (використовується в аналізаторі).

#### `_get_iteration_counts(conn, ids) -> Dict[int, int]`
**Призначення:** Отримує кількість ітерацій (метрик) для кожного токена.
**Викликається:** В `_process_flagged_tokens()` (рядок 360).
**Використання:** ✅ Активно використовується.

#### `_process_flagged_tokens(conn, ids, reason, archive_threshold) -> Tuple[int, int]`
**Призначення:** Обробляє позначені токени: фільтрує без кошельків, архівує старі (`>= archive_threshold` ітерацій), видаляє молоді.
**Викликається:** В `run_cleanup()` для `zero_tail` та `frozen_price` (рядки 522, 537).
**Використання:** ✅ Активно використовується.

### Bad tables functions

#### `_ensure_bad_tables(conn) -> None`
**Призначення:** Створює таблиці `bad_tokens` та `bad_token_metrics` для архівування видалених токенів.
**Викликається:** В `run_cleanup()` (рядок 473).
**Використання:** ✅ Активно використовується.

#### `_move_to_bad_tables(conn, ids, reason) -> int`
**Призначення:** Переміщує токени в `bad_tokens` та метрики в `bad_token_metrics` перед видаленням (крім `no_pair` та `transfer_only`, які видаляються одразу).
**Викликається:** В `_process_flagged_tokens()` (рядок 380), `_drain_no_swap_tokens()` (рядок 222), `_drain_no_pair_tokens()` (рядок 244).
**Використання:** ✅ Активно використовується.

### Main functions

#### `run_cleanup(dry_run, older_than_sec, limit, no_entry_age_sec, no_entry_iters) -> dict`
**Призначення:** Головна функція cleaner'а: видаляє токени без swap'ів, без пар, з zero_tail, frozen_price, low_holder. Використовує advisory lock для запобігання паралельним виконанням.
**Викликається:** В `_v3_jupiter_scheduler._cleaner_loop()` (рядок 149), `run_until_empty()`, CLI `main()`.
**Використання:** ✅ Активно використовується.

#### `run_until_empty(dry_run, older_than_sec, limit, no_entry_age_sec, no_entry_iters) -> dict`
**Призначення:** Запускає `run_cleanup()` в циклі поки не залишиться токенів для видалення.
**Викликається:** В CLI `main()` з флагом `--loop`.
**Використання:** ✅ Активно використовується.

#### `_parse_args() -> argparse.Namespace`
**Призначення:** Парсить аргументи командного рядка для CLI.
**Викликається:** В `main()`.
**Використання:** ✅ Активно використовується.

#### `main()`
**Призначення:** Точка входу для CLI запуску cleaner'а.
**Викликається:** При запуску `python3 -m server._v3_cleaner`.
**Використання:** ✅ Активно використовується.

---

## Висновки

### Невикористані функції (мусор):
1. `_find_no_entry_candidates` - повертає порожній список
2. `_find_no_entry_iterations` - повертає порожній список
3. `_find_no_price_tokens` - не викликається
4. `_count_no_pair_tokens` - не викликається (закоментовано)
5. `_flag_tokens` - не викликається в cleaner'і (використовується в аналізаторі)
6. `_find_candidates` - замінено на `_find_no_pair_tokens`

### Можливо не використовуються:
1. `close()` в аналізаторі - не викликається при нормальній роботі
2. `stop()` в аналізаторі - не викликається при нормальній роботі

### Всі інші функції активно використовуються в основному циклі роботи системи.

