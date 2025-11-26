# Refactor Roadmap (safe, incremental)

**ВАЖЛИВО: Симуляція повністю видаляється. Тільки реальна торгівля.**

Мета: акуратно перевести проект на нову схему з DB_SCHEMA.txt без поломок: прибрати застарілі sim_* з `tokens`, уніфікувати `wallet_id`, залишити у `tokens` лише план (AI) і службові поля, а всі входи/виходи фіксувати тільки у `wallet_history`. **Видалити всі функції симуляції (sell_simulation, force_buy_simulation, force_sell_simulation).**

---

## 0) Реліз‑процес і safety‑net
- Backup БД (pg_dump).
- Створити SQL‑міграції (rename/drop/alter) як окремі файли, застосовувати в транзакції.
- Флаг/константа MAINTENANCE=false → під час міграцій зупинити таймери/аналізатор.
- Після міграцій — smoke‑перевірки, далі відновити потоки.

---

## 1) База даних (схема)

Що змінюємо в `tokens`:
- Видалити: `sim_buy_token_amount`, `sim_buy_price_usd`, `sim_buy_iteration`, `sim_sell_token_amount`, `sim_sell_price_usd`, `sim_sell_iteration`, `sim_profit_usd`, `sim_wallet_id`.
- Перейменувати:
  - `sim_plan_sell_iteration` → `plan_sell_iteration` (AI пише план виходу, так)
  - `sim_plan_sell_price_usd` → `plan_sell_price_usd` (AI пише план виходу, так)
  - `sim_cur_income_price_usd` → `cur_income_price_usd`
  - `real_wallet_id` → `wallet_id` (уніфікуємо прив’язку)
- Залишаємо: службові (`history_ready`, `pair_resolve_attempts`, `pattern_code`, ціни/ліквідність/FDV/MCap/…)

Інші таблиці:
- `token_metrics_seconds`: вже містить потрібні поля (включно з `median_amount_tokens`).
- `wallet_history`: уже повний журнал вход/вихід. Без змін у схемі.
- `sim_wallets` → **ВИДАЛИТИ** (симуляція більше не використовується). Всі операції тільки з реальними гаманцями через `wallet_history`.

Результат:
- Створити міграцію `migrations/20251106_tokens_cleanup.sql` з ALTER TABLE (DROP/RENAME COLUMN).
- Створити міграцію `migrations/20251106_drop_sim_wallets.sql` для видалення таблиці `sim_wallets` (DROP TABLE IF EXISTS sim_wallets CASCADE).
- Оновити `_v3_db_init.py` (create/alter) під нові назви, видалити CREATE TABLE для `sim_wallets`.

---

## 2) Аналізатор `_v3_analyzer_jupiter.py`

Завдання: відв’язати від sim_* у `tokens`, лишити тільки:
- Запис даних з Jupiter у `tokens`.
- Запис секундних метрик у `token_metrics_seconds`.
- Читання/оновлення AI‑плану: `plan_sell_iteration`, `plan_sell_price_usd` (тільки читання/refresh; писати їх буде AI).
- Auto‑вихід тригериться так само (ETA/zero-tail) через встановлення умов і виклик `finalize_token_sale` з `_v2_buy_sell` (залишається без змін).

Конкретні правки:
- Прибрати будь‑які читання/записи до видалених з `tokens` полів sim_*.
- Перевірка «чи є відкрита позиція» робиться через `wallet_history (exit_iteration IS NULL)`, а не через `tokens.sim_buy_*`.
- Zero‑tail:
  - якщо відкритої позиції нема → `tokens.history_ready=TRUE`.
  - якщо позиція відкрита → фіналізуємо через `_v2_buy_sell.finalize_token_sale()` (без записів у sim_*).
- Імпорт `finalize_token_sale` залишити з `_v2_buy_sell`.

Вплив:
- Аналізатор стає чистим «writer/guard»: збір даних + тригер фіналізації. Логіка торгів — у buy/sell.

---

## 3) Модуль купівлі/продажу `_v2_buy_sell.py`

Цілі:
- Єдине джерело правди по угодах — `wallet_history`.
- Прив’язка гаманця до токена — `tokens.wallet_id` (тільки реальні гаманці).
- `finalize_token_sale(token_id, conn, reason)` — вже перенесена сюди. Переконатися, що вона не покладається на sim_* у `tokens` і не працює з `sim_wallets`.

Конкретні правки і перевірки:
- **ВИДАЛИТИ** `sell_simulation(token_id)` — функція повністю видаляється (рядки 248-358).
- `sell_real(token_id)`:
  - Шукає відкриту позицію у `wallet_history` (amount/ціна входу вже там).
  - Продає рівно `entry_token_amount` через Jupiter API (реальна транзакція).
  - Оновлює `wallet_history` (exit_*), встановлює `tokens.history_ready=TRUE`, очищає `tokens.wallet_id`.
- `finalize_token_sale(...)`:
  - **ВИДАЛИТИ** всю логіку для simulation (перевірки `sim_wallet_id`, оновлення `sim_wallets`).
  - Якщо є відкритий запис у `wallet_history` — закриває його нульовою/фактичною ціною; виставляє `history_ready=TRUE`.
  - Працює тільки з реальними гаманцями (немає віртуальних балансів для оновлення).

Вплив:
- Всі операції buy/sell і надалі логуються тільки у `wallet_history`.
- Немає жодних операцій з `sim_wallets`.

---

## 4) AI (ETA/JUNO) — `ai/infer/*`

Завдання: AI не пише нічого у «entry/exit» у `tokens`. Тільки план виходу (plan_*) у `tokens`. **Тільки реальна торгівля.**

Конкретні правки:
- Запис плану: `plan_sell_iteration`, `plan_sell_price_usd` у `tokens` — залишити.
- Коли спрацював факт «ціль досягнута» — НЕ записувати в `tokens` sim_sell_* (полів більше немає);
  - Замість цього:
    - або викликати сервісну функцію автопродажу з `_v2_buy_sell` (тільки `sell_real`),
    - або (якщо за поточною архітектурою тригер робить аналізатор) виставляти ознаку для аналізатора (бажано у `wallet_history`/іншій службовій таблиці).
- Узгодити місце тригера: найпростіше — лишити як є: AI сигналізує, а аналізатор завершує через `finalize_token_sale` (тільки реальна торгівля).

Вплив:
- Позбавляємось залежності від sim_* у `tokens`.
- AI працює тільки з реальною торгівлею.

---

## 5) Баланси/вебсокети `_v2_balance.py`, читачі даних

Завдання:
- Баланси рахувати за реальними гаманцями та відкритими позиціями з `wallet_history`.
- Планові значення/графіки — з `tokens` та `token_metrics_seconds`.
- **ВИДАЛИТИ** всю логіку роботи з `sim_wallets`.

Конкретні правки:
- **ВИДАЛИТИ** всі функції/блоки, що працюють з `sim_wallets` (ініціалізація, оновлення балансів, перевірка `active_token_id`).
- Прибрати будь‑які звернення до `tokens.sim_*`.
- Для відкритої позиції: визначати її через `wallet_history` (exit_iteration IS NULL).
- Поточну ринкову вартість відкритої позиції — із `token_metrics_seconds` (останній price) × `entry_token_amount`.
- Баланси реальних гаманців — тільки через `wallet_history` (сума entry/exit операцій) + реальні баланси з blockchain (якщо є інтеграція).

Вплив:
- Модуль працює тільки з реальними даними, без віртуальних балансів.

---

## 6) Ініціалізація/міграції `_v3_db_init.py`

- Оновити блоки ALTER/CREATE під нові поля/відсутність sim_* у `tokens`.
- **ВИДАЛИТИ** створення таблиці `sim_wallets` (якщо вона є в `_v3_db_init.py`).
- Залишити індекси, які використовуються UI/API.
- Додати індекси для `wallet_history (wallet_id, token_id, exit_iteration)` якщо відсутні.

---

## 7) main.py / маршрути

- Перевірити імпорти:
  - аналізатор — не імпортує нічого з `_v1_buy_sell`;
  - торгові ендпоїнти використовують `_v2_buy_sell` (тільки `sell_real`, без `sell_simulation`).
- **ВИДАЛИТИ** всі виклики `sell_simulation`, `force_buy_simulation`, `force_sell_simulation`.
- **ВИДАЛИТИ** всі ендпоїнти/блоки, що працюють з `sim_wallets` (перевірка `active_token_id`, оновлення балансів).
- Логіка «post‑trade updates» (push/broadcast) — лишається без змін; джерела даних більше не покладаються на sim_*.

---

## 8) Пошук/заміна у коді

- Глобально прибрати використання:
  - `sim_buy_token_amount`, `sim_buy_price_usd`, `sim_buy_iteration`,
  - `sim_sell_token_amount`, `sim_sell_price_usd`, `sim_sell_iteration`,
  - `sim_profit_usd`, `sim_wallet_id`, `real_wallet_id` (заміна на `wallet_id`),
  - записів до цих полів.
- **ВИДАЛИТИ** всі функції:
  - `sell_simulation()` з `_v2_buy_sell.py`
  - `force_buy_simulation()` з `_v1_buy_sell.py`
  - `force_sell_simulation()` з `_v1_buy_sell.py`
- **ВИДАЛИТИ** всі звернення до таблиці `sim_wallets` (SELECT, UPDATE, INSERT, DELETE).
- Заміни методів «чи є відкрита позиція?» на перевірку в `wallet_history`.
- **ВИДАЛИТИ** router-функції, що викликають simulation-версії (залишити тільки real).

---

## 9) Тестування (smoke)

Сценарії (тільки реальна торгівля):
- Manual Buy (real) → Manual Sell (real) (journal запис, history_ready=TRUE, оновлення реального кошелька).
- AI Plan → факт хіта → фіналізація через аналізатор (тільки реальна продажа).
- Zero‑tail (ліквідність 0): без відкритої позиції → history_ready=TRUE; з відкритою позицією → finalize з нульовою ціною (реальна позиція).
- Вебсокети/читачі — вартість портфеля/чарти працюють без sim_* і без `sim_wallets`.
- Перевірка, що немає викликів simulation-функцій.

---

## 10) Розгортання

- Бекап.
- Зупинка таймерів.
- Застосування міграцій.
- Перезапуск, smoke‑прогін.
- Моніторинг логів/дешбордів.

---

## Перелік точкових правок по файлах (без коду, опис):

- `server/_v3_db_init.py`:
  - Оновити create/alter для `tokens` (прибрати sim_*; перейменувати планові поля; `wallet_id`).
- `server/_v3_analyzer_jupiter.py`:
  - Прибрати будь‑які звернення до видалених полів у `tokens`.
  - Перевірку відкритої позиції робити тільки через `wallet_history`.
  - Тригер фіналізації залишити з `_v2_buy_sell.finalize_token_sale`.
- `server/_v2_buy_sell.py`:
  - **ВИДАЛИТИ** функцію `sell_simulation()` (рядки 248-358).
  - Переконатися, що `sell_real`, `finalize_token_sale` працюють виключно з `wallet_history` і `tokens.wallet_id`; не використовують sim_* і не працюють з `sim_wallets`.
- `server/_v1_buy_sell.py`:
  - **ВИДАЛИТИ** функції `force_buy_simulation()` (рядки 1482-1595) та `force_sell_simulation()` (рядки 1695+).
  - Оновити router-функції `force_buy`, `force_sell` — прибрати виклики simulation-версій, залишити тільки real.
- `server/_v2_balance.py`:
  - **ВИДАЛИТИ** всю логіку роботи з `sim_wallets` (ініціалізація, оновлення балансів, перевірка `active_token_id`).
  - Розрахунок значень з `wallet_history` + `token_metrics_seconds`; прибрати залежності від sim_*.
- `ai/infer/*`:
  - Писати лише `plan_sell_*` у `tokens`; більш не встановлювати entry/exit у `tokens`.
- `server/main.py`:
  - Імпорти/зв’язки перевірити, використання `_v2_buy_sell` для торгових дій (тільки `sell_real`).
  - **ВИДАЛИТИ** всі ендпоїнти/блоки, що працюють з `sim_wallets` (перевірка `active_token_id`, оновлення балансів, ініціалізація).
- `server/_v3_db_init.py`:
  - **ВИДАЛИТИ** створення таблиці `sim_wallets` (якщо є CREATE TABLE для неї).

---

Цей план не змінює код одразу. Після підтвердження — згенерую файли міграцій і зроблю правки малими порціями (з перевіркою компіляції після кожного блоку).
