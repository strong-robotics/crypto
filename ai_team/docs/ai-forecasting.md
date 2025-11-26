# AI‑Forecasting (V3): прогноз цены по секундам

Документ описывает целевую архитектуру и этапы внедрения модуля ИИ‑прогнозов для токенов в рамках текущей БД `crypto_db` и сервисов V3. Текст ориентирован на прод‑использование на macOS (Apple Silicon M3, 24 ГБ RAM).

## 1. Задача и сценарий

- По первым K секунд (по умолчанию K=15) жизни токена построить прогноз траектории цены на горизонте L секунд (например, L=30/60/180). 
- Результат нужен для оперативного решения: когда «зайти на $5» и когда «выйти на $6» до ухода ликвидности (часто скам‑токены живут 45–180 секунд).
- Прогноз дорисовывается на графике (“жёлтая” линия) поверх реального ряда (“синяя”).

## 2. Источники данных

Используем текущую схему `crypto_db` (см. `ai_team/docs/db-schema.md`). Ключевые таблицы:
- `token_metrics_seconds` — per‑second: `usd_price`, `liquidity`, `mcap`, `fdv`.
- `trades` — сделки: направление, объёмы, per‑trade цена.
- `tokens` — статика токена: supply, аудит‑флаги, holders, organic_score и т.д.

Подготовительный шаг: заполняем секунды цен из сделок для свежих токенов через `_v3_price_resolver.py`, чтобы убирать нули в метриках.

## 3. Новые таблицы ИИ (хранилище прогнозов)

Рекомендуемые сущности (DDL добавить отдельной миграцией). Обновлено с учётом практичного базлайна и потенциального расширения:
- `ai_models` — реестр обученных моделей (имя, версия, тип, фреймворк, гиперпараметры, окно обучения/горизонты, путь к артефактам, метрики, дата обучения).
- `ai_forecasts` — «шапка» прогноза для токена: `token_id`, `model_id`, `origin_ts` (момент старта прогноза), `encoder_len_sec`, `horizon_sec`, вероятность роста, ожидаемая доходность, массивы `y_p50/y_p10/y_p90`, а также сервисные поля (целевая доходность, ETA до цели, текущая цена). `UNIQUE(token_id, model_id, origin_ts)`.
- (Опционально) `ai_forecast_points` — нормализованное хранение точек прогноза по шагам.

См. также «Typical Queries» в `db-schema.md` для выборки последнего прогноза на фронт.

## 4. Пайплайн (этапы внедрения)

Этап 0. Бэкофилл цен
- `_v3_price_resolver.py` агрегирует сделки → цена/сек, дозаполняет `token_metrics_seconds` и производные (mcap/fdv/liquidity при нулях).

Этап 1. Фичи (ETL)
- Формируем признаки по окну K секунд до `origin_ts` (без утечек в будущее):
  - из `token_metrics_seconds`: цена, доходности/лог‑доходности, волатильность, дельты `mcap/liquidity/fdv`;
  - из `trades`: объёмы (SOL/USD/токены), дисбаланс покупок/продаж, интенсивность (count/sec), межсделочные интервалы;
  - из `tokens` (статика): `circ_supply/total_supply`, `holder_count`, аудит‑флаги, `organic_score` и др.
- Нормализация: лог‑преобразования для масштабных величин; GroupNormalizer по `token_id` или стандартизация на train‑сплите.

Этап 2. Обучение моделей
- Базовый baseline (быстро и надёжно в проде):
  - Градиентный бустинг (CatBoost/LightGBM) по агрегатам первых K секунд + статике.
  - Задачи: классификация «рост ≥ X% за L секунд», регрессия доходности/макс‑доходности.
- Последовательная модель по секундам (улучшение качества):
  - TCN/1D‑CNN (PyTorch): вход — матрица K×F; выход — траектория на L секунд (или суммарная доходность), дополнительно «вероятность пампа».
- Альтернатива/исследование: Temporal Fusion Transformer (TFT)/PatchTST для многогоризонтного прогноза с квантилями.

Идеальная середина (Practical + Research):
- Baseline (прод‑готовый сейчас): CatBoost (классификация «pump / no pump» + регрессия ожидаемой доходности). Быстро, CPU/M3‑дружно, легко обслуживать.
- Advanced: TCN/1D‑CNN по окну K×F для «жёлтой» траектории с квантилями; при необходимости — TFT/PatchTST.

Этап 3. Пакетирование и реестр
- Сохраняем артефакты (веса, нормировщики) на диск/в объектное хранилище; в `ai_models` — запись версии/параметров.

Этап 4. Инференс (онлайн)
- По мере накопления K секунд для токена: 
  1) собираем признаки, 
  2) вызываем модель, 
  3) сохраняем в `ai_forecasts` (массивы `y_p50/y_p10/y_p90`, `score_up`).
- Повторяем каждые N секунд скользящим окном, пока токен «жив». Держим последние M прогнозов на токен.

Этап 5. Фронтенд
- В `Chart WS` отдаём `{ id, token_pair, forecast: { origin_ts, horizon_sec, p50[], p10[], p90[] } }`.
- Рисуем «жёлтую» линию (p50) и полосу уверенности (p10..p90).

## 5. Постановка задач обучения

- Окно истории: `encoder_length = K` (15 по умолчанию, можно 30/60 для устойчивости).
- Горизонты: `prediction_length = L` (30/60/180). Возможен мульти‑горизонт (несколько L).
- Таргет: 
  - предпочтительно лог‑цена или лог‑доходности (устранение масштаба между токенами),
  - для классификации — событие «достигнуть +X% раньше, чем −Y% в пределах L».
- Сплиты/утечки: разбиение по времени и по токенам; фичи считать только из прошлых секунд относительно `origin_ts`.
- Качество/метрики: MAE/MAPE для регрессии траекторий, ROC‑AUC/PR‑AUC для «рост/не рост», и оффлайн‑PnL топ‑K.

Замечание по онлайновому дообучению: для продакшена держим оффлайн‑тренинг по расписанию (почасово/сутки). Онлайн — только инференс; это стабильно и дешево по ресурсам.

## 6. Рекомендуемые библиотеки и конфигурация (macOS M3, 24 ГБ)

- PyTorch 2.x с поддержкой Apple Metal (MPS): ускорение на M‑чипах.
  - Инициализация в коде: `device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')`.
  - В Lightning: `Trainer(accelerator='mps'|'cpu', devices=1)`.
- PyTorch Lightning — структурирование обучения/валидации/логирования.
- CatBoost / LightGBM / XGBoost — бустинги для табличных фичей.
- pytorch‑forecasting — TFT/модели рядов (опционально), TorchMetrics — метрики, scikit‑learn — препроцесс.
- Pandas/NumPy/SQLAlchemy — загрузка/ETL; joblib — сериализация скейлеров.
- Для работы с БД: asyncpg/psycopg и существующий пул `_v3_db_pool`.

На M3 обычно хватает CPU/MPS для наших размеров (секундные окна, сотни токенов). Для больших батчей — уменьшать `batch_size`, использовать градиентное накопление.

## 7. План структуры модулей (предложение)

```
server/ai/
  feature_builder.py    # SQL/ETL фичей, без утечек
  train_runner.py       # CLI-тренинг (CatBoost/TCN/TFT) + валидация + сохранение артефактов
  model_registry.py     # запись/чтение из ai_models, версионирование
  infer_service.py      # инференс, запись в ai_forecasts, простые правила сигналов
```

Интеграция эндпоинтов FastAPI:
- `POST /api/ai/forecast?token_id=...` — построить прогноз сейчас, вернуть JSON и записать в БД.
- `GET  /api/ai/forecast/last?token_id=...` — отдать последний прогноз.

## 8. Примеры политик и правил

- «Целевой рост»: +20% за ≤60 секунд; сигнал публиковать, если `score_up ≥ 0.7` и `time_to_target ≤ 60`.
- Отслеживать `withdraw`: после него прогнозы не строить/снимать с витрины.
- Хранение: для каждого токена — последние M=5 прогнозов, чистка по `origin_ts`.

## 9. Мини‑чек‑лист качества данных

- Только валидные пары (`token_pair IS NOT NULL AND token_pair <> token_address`).
- Исключать сегменты после `withdraw`.
- Цена > 0 в обучении; внутри encoder допускается лёгкий forward‑fill, но не в таргете.
- Контроль смещений по supply: использовать лог‑трансформации и/или нормализацию по токену.

## 10. Следующие шаги

1) Подготовить миграцию для `ai_models` и `ai_forecasts` (и, при необходимости, `ai_forecast_points`).
2) Реализовать `feature_builder.py` с корректным окном K=15 и наборами горизонтов L.
3) Собрать baseline CatBoost (классификация «рост/не рост») и TCN (траектория), сравнить оффлайн‑метрики и оффлайн‑PnL.
4) Добавить `infer_service.py` + FastAPI эндпоинт, запись прогнозов в БД, публикация в WS.
5) Настроить расписание переобучения (почасово/ежедневно), логирование качества и мониторинг дрейфа.

---

## 11. Паттерны поведения и риск‑оценка (manual → AI)

Идея: помимо прогноза цены фиксировать визуально‑поведенческие паттерны токена (тип траектории в первые секунды) и агрегированную риск‑оценку. Сначала метки задаются вручную, далее — дообучаем AI для автодетекции.

### 11.1. Таксономия паттернов
- 3 уровня риска (пример): Top/Middle/Bottom Tier.
- 20 базовых паттернов (напр. The Best One, Rising Phoenix, Wave Rider, … Black Hole). 
- Для каждого паттерна задаём: `code`, `name`, `tier`, `description`, `score` (0..100 где 100 — лучший).

### 11.2. Таблицы для паттернов

```sql
-- Справочник паттернов
CREATE TABLE IF NOT EXISTS ai_patterns (
  id SERIAL PRIMARY KEY,
  code TEXT UNIQUE,            -- 'best_one', 'wave_rider', ...
  name TEXT NOT NULL,
  tier TEXT,                   -- 'top' | 'middle' | 'bottom'
  risk_level TEXT,             -- текстовая категория риска
  score INTEGER,               -- 0..100
  description TEXT,
  created_at TIMESTAMP DEFAULT now()
);

-- Метки для токенов (manual / model)
CREATE TABLE IF NOT EXISTS ai_token_patterns (
  id SERIAL PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
  pattern_id INTEGER NOT NULL REFERENCES ai_patterns(id),
  source TEXT NOT NULL,            -- 'manual' | 'model'
  confidence DOUBLE PRECISION,     -- 0..1 (для model)
  notes TEXT,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(token_id, pattern_id, source)
);
```

Интеграция с текущим API/фронтом:
- Поле `pattern` в выдаче списка токенов можно формировать как `JOIN` последней записи из `ai_token_patterns` (или напрямую хранить `tokens.pattern_id` для быстрого доступа).
- Для начала допустимо держать поле `tokens.pattern`/`tokens.pattern_id` (как вы предлагали) и периодически синхронизировать из `ai_token_patterns`.

### 11.3. Риск‑оценка (risk scoring)

Задача: дать числовой `risk_score` (0..1) и набор `risk_flags` исходя из on‑chain/аудит‑признаков и поведения цены/ликвидности.

Источники признаков (уже есть в БД):
- `blockaid_rugpull`, `mint_authority_disabled`, `freeze_authority_disabled`.
- `top_holders_percentage`, `dev_balance_percentage`.
- стабильность/скачки ликвидности (`token_metrics_seconds.liquidity`), ранние `withdraw` в `trades`.
- концентрация объёмов, перекос buys/sells, скорость роста/падения, «одноволновые» пики.

Предлагаемое хранилище оценок:

```sql
CREATE TABLE IF NOT EXISTS ai_risk_assessments (
  id SERIAL PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
  model_id INTEGER REFERENCES ai_models(id),    -- если считали ML‑моделью
  risk_score DOUBLE PRECISION,                  -- 0..1 (1 — высокий риск)
  risk_tier TEXT,                               -- 'low' | 'mid' | 'high'
  risk_flags JSONB,                             -- {"top_holders":true, "rug_signal":true, ...}
  created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_token ON ai_risk_assessments(token_id);
```

Онлайн‑логика: после инференса прогноза записываем/обновляем оценку риска. На фронте применяем «гейтинг»: не показываем «зелёный» сигнал входа при `risk_tier='high'` или наличии критических `risk_flags`.

### 11.4. Импорт ручных меток

На старте вы можете присылать файл/список вида `token_address → pattern_code`. Импортёр:
- находит `token_id` по адресу,
- создаёт недостающий `ai_patterns.code`,
- пишет запись в `ai_token_patterns` со `source='manual'`.

В дальнейшем эти метки используем как целевые для обучения авто‑детектора паттернов (классификатор по K×F окну).

## Приложение D. Каталог паттернов v1 (константы)

Ниже — согласованный список из 20 паттернов с кодами (их используем в БД и в коде), читаемыми именами и уровнем риска. Эти коды можно импортировать в `ai_patterns` как справочник.

Top Tier (входим охотно)
- code: `best_one`,          name: "The Best One",     tier: `top`      — плавный старт → накопление → резкий памп и стабилизация.
- code: `rising_phoenix`,    name: "Rising Phoenix",   tier: `top`      — ранний спад, затем быстрое восстановление и рост.
- code: `wave_rider`,        name: "Wave Rider",       tier: `top`      — волнообразный рост с последовательно выше пиками.
- code: `clean_launch`,      name: "Clean Launch",     tier: `top`      — линейный стабильный рост без шума.
- code: `calm_storm`,        name: "Calm Storm",       tier: `top`      — 5–10с спокойствия, затем импульс без глубоких откатов.
- code: `gravity_breaker`,   name: "Gravity Breaker",  tier: `top`      — медленное накопление объёма → резкий отрыв цены.
- code: `golden_curve`,      name: "Golden Curve",     tier: `top`      — параболический рост с уменьшением волатильности.

Middle Tier (спорные, смотреть внимательно)
- code: `bait_switch`,       name: "Bait & Switch",    tier: `middle`   — быстрый памп в первые 5с, резкий дроп, затем лёгкий подъём.
- code: `echo_wave`,         name: "Echo Wave",        tier: `middle`   — повторяет форму предыдущего пампа, но слабее по амплитуде.
- code: `flash_bloom`,       name: "Flash Bloom",      tier: `middle`   — мгновенный всплеск и откат; живёт, если объём поддерживается.
- code: `tug_of_war`,        name: "Tug of War",       tier: `middle`   — 3–4 чередующихся скачка вверх/вниз, борьба покупателей и продавцов.
- code: `drunken_sailor`,    name: "Drunken Sailor",   tier: `middle`   — хаотичные движения без структуры, ближе к нейтрали.
- code: `ice_melt`,          name: "Ice Melt",         tier: `middle`   — постепенный спад после стабильного старта, возможен реапмп.

Bottom Tier (избегаем)
- code: `rug_prequel`,       name: "Rug Prequel",      tier: `bottom`   — 5–10с стабильности → резкий обвал и исчезающая ликвидность.
- code: `death_spike`,       name: "Death Spike",      tier: `bottom`   — одиночный высокий пик и мгновенное падение (honeypot/pump‑trap).
- code: `flatliner`,         name: "Flatliner",        tier: `bottom`   — плоская линия, ликвидность мертва ("труп").
- code: `smoke_bomb`,        name: "Smoke Bomb",       tier: `bottom`   — шумный рост на ботовых объёмах, "дымовой" график.
- code: `mirage_rise`,       name: "Mirage Rise",      tier: `bottom`   — плавный подъём при фейковых объёмах, обманная структура.
- code: `panic_sink`,        name: "Panic Sink",       tier: `bottom`   — резкая сброска объёма без причин, признак бегства.
- code: `black_hole`,        name: "Black Hole",       tier: `bottom`   — исчезновение цены и ликвидности одновременно.

Пример Python‑перечисления (для бэкенда)
```python
from enum import StrEnum

class PatternCode(StrEnum):
    BEST_ONE = "best_one"
    RISING_PHOENIX = "rising_phoenix"
    WAVE_RIDER = "wave_rider"
    CLEAN_LAUNCH = "clean_launch"
    CALM_STORM = "calm_storm"
    GRAVITY_BREAKER = "gravity_breaker"
    GOLDEN_CURVE = "golden_curve"
    BAIT_SWITCH = "bait_switch"
    ECHO_WAVE = "echo_wave"
    FLASH_BLOOM = "flash_bloom"
    TUG_OF_WAR = "tug_of_war"
    DRUNKEN_SAILOR = "drunken_sailor"
    ICE_MELT = "ice_melt"
    RUG_PREQUEL = "rug_prequel"
    DEATH_SPIKE = "death_spike"
    FLATLINER = "flatliner"
    SMOKE_BOMB = "smoke_bomb"
    MIRAGE_RISE = "mirage_rise"
    PANIC_SINK = "panic_sink"
    BLACK_HOLE = "black_hole"
```

SQL‑seed для `ai_patterns`
```sql
INSERT INTO ai_patterns(code,name,tier,score) VALUES
 ('best_one','The Best One','top',100),
 ('rising_phoenix','Rising Phoenix','top',95),
 ('wave_rider','Wave Rider','top',92),
 ('clean_launch','Clean Launch','top',90),
 ('calm_storm','Calm Storm','top',88),
 ('gravity_breaker','Gravity Breaker','top',86),
 ('golden_curve','Golden Curve','top',85),
 ('bait_switch','Bait & Switch','middle',60),
 ('echo_wave','Echo Wave','middle',58),
 ('flash_bloom','Flash Bloom','middle',55),
 ('tug_of_war','Tug of War','middle',52),
 ('drunken_sailor','Drunken Sailor','middle',50),
 ('ice_melt','Ice Melt','middle',48),
 ('rug_prequel','Rug Prequel','bottom',20),
 ('death_spike','Death Spike','bottom',15),
 ('flatliner','Flatliner','bottom',10),
 ('smoke_bomb','Smoke Bomb','bottom',10),
 ('mirage_rise','Mirage Rise','bottom',8),
 ('panic_sink','Panic Sink','bottom',5),
 ('black_hole','Black Hole','bottom',1)
ON CONFLICT (code) DO NOTHING;
```

## Приложение E. Структура AI‑модулей (подробно)

Дерево директорий и ключевые файлы для части ИИ (бетон скриптов):

```
server/ai/
  __init__.py
  config.py                  # общие константы: ENCODER_SEC, HORIZONS, TARGET_RETURN, пути, флаги MPS/CPU
  sql/
    features.sql             # оконные выборки 15s/30s/60s без утечек
    migrations/
      ai_models.sql
      ai_forecasts.sql
      ai_patterns.sql
      ai_risk.sql
    seeds/
      ai_patterns_seed.sql   # 20 паттернов (из Приложения D)

  feature_builder.py         # сбор фичей из PG (asyncpg), K×F + агрегаты, нормализация
  datasets.py                # преобразование в pandas/torch Dataset, сплиты по времени/токенам

  models/
    base.py                  # интерфейс fit/predict/save/load/info
    catboost_cls.py          # классификатор pump/no‑pump (CatBoost)
    catboost_reg.py          # регрессор ожидаемой доходности (CatBoost)
    tcn.py                   # TCN/1D‑CNN (PyTorch) для «жёлтой» траектории
    tft.py                   # (опц.) TFT/patchTST
    registry.py              # связь с таблицей ai_models (CRUD артефактов/метрик)

  training/
    train_catboost.py        # обучение класификатора/регрессора + запись метрик в ai_models
    train_tcn.py             # обучение PyTorch‑модели (Lightning), сохранение .pth и нормировщиков
    evaluation.py            # ROC/PR, PnL@K, MAE/MAPE, калибровка

  infer/
    forecast_loop.py         # циклический инференс 1 Гц: выбор окон, предикт, запись в ai_forecasts
    endpoints.py             # FastAPI: /api/ai/forecast, /api/ai/forecast/last
    loader.py                # загрузка лучшей модели из ai_models/каталога моделей

  risk/
    risk_scoring.py          # расчёт risk_score/tier/flags, запись в ai_risk_assessments
    save.py                  # вспомогательные функции записи

  patterns/
    catalog.py               # PatternCode (20 констант), описания, уровни риска
    importer.py              # импорт ручных меток token_address → pattern_code в ai_token_patterns
    detector.py              # (опц.) авто‑классификатор паттернов по окну K×F

  tools/
    migrate.py               # применение SQL‑миграций
    seed_patterns.py         # заливка справочника паттернов в ai_patterns
    backfill_forecasts.py    # ретро‑пересчёт прогнозов/рисков

  logs/                      # (git‑ignored) логи тренинга/инференса
  models_store/              # (git‑ignored) артефакты .cbm/.pth/.pkl (локально)

requirements-ai.txt          # зависимости: catboost, torch+mps, lightning, sklearn, pandas, numpy, asyncpg, (опц.) pytorch‑forecasting
```

Интеграция в `server/cli.py` (предлагаемые подкоманды):
- `ai migrate` — применить миграции из `server/ai/sql/migrations/`.
- `ai seed-patterns` — вставить 20 паттернов в `ai_patterns`.
- `ai train --model catboost|tcn --encoder 15 --horizons 30,60,180` — оффлайн‑обучение и запись в `ai_models`.
- `ai forecast --token-id N|--all --once|--loop` — инференс и запись в `ai_forecasts`.
- `ai import-patterns --file patterns.csv` — импорт ручных меток токенов.

## Приложение A. DDL‑скетчи для новых таблиц

```sql
CREATE TABLE IF NOT EXISTS ai_models (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  model_type TEXT NOT NULL,       -- catboost_cls / catboost_reg / tcn / tft
  framework TEXT,                 -- catboost / pytorch / lightgbm
  hyperparams JSONB,
  train_window_sec INTEGER,
  predict_horizons_sec INTEGER[],
  trained_on TIMESTAMP,
  path TEXT,                      -- путь к артефактам (.cbm, .pth)
  metrics JSONB,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS ai_forecasts (
  id BIGSERIAL PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
  model_id INTEGER NOT NULL REFERENCES ai_models(id),
  origin_ts BIGINT NOT NULL,
  encoder_len_sec INTEGER NOT NULL,
  horizon_sec INTEGER NOT NULL,
  score_up DOUBLE PRECISION,           -- вероятность роста
  exp_return DOUBLE PRECISION,         -- ожидаемая доходность за горизонт
  y_p50 DOUBLE PRECISION[],            -- прогнозная кривая (медиана)
  y_p10 DOUBLE PRECISION[],            -- нижний квантиль (опционально)
  y_p90 DOUBLE PRECISION[],            -- верхний квантиль (опционально)
  target_return DOUBLE PRECISION,      -- целевая доходность, напр. 0.2 (=+20%)
  eta_to_target_sec INTEGER,           -- ETA до достижения target_return
  price_now DOUBLE PRECISION,          -- текущая цена в момент origin_ts
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(token_id, model_id, origin_ts)
);

CREATE INDEX IF NOT EXISTS idx_forecasts_token  ON ai_forecasts(token_id);
CREATE INDEX IF NOT EXISTS idx_forecasts_origin ON ai_forecasts(origin_ts);
```

Рекомендации по полю `ai_models.metrics` (JSONB)
- Для A/B‑сравнения моделей фиксируйте хотя бы:
  - `roc_auc`, `pr_auc` — качество классификации «pump / no pump»
  - `pnl_at_k` — оффлайн‑PnL для топ‑K сигналов (например, K=10/20)
  - `mae`, `mape`, `rmse` — ошибка регрессии траектории/доходности
  - `calibration` — калибровка вероятностей (Brier/Expected Calibration Error)
- Пример:
  ```json
  {
    "roc_auc": 0.81,
    "pnl_at_k": {"k10": 0.24, "k20": 0.19},
    "mae": 0.012,
    "mape": 6.4,
    "rmse": 0.021
  }
  ```

## Приложение B. Минимальный рабочий цикл инференса (CatBoost‑базлайн)

Идея: каждый тик (раз в 1 сек) отбирать токены с готовым окном K=15, собирать лёгкие признаки, получать два предсказания — вероятность пампа (классификация) и ожидаемую доходность (регрессия), — формировать «жёлтую» кривую `y_p50` и писать в `ai_forecasts`.

```python
#!/usr/bin/env python3
import asyncio, numpy as np
from catboost import CatBoostClassifier, CatBoostRegressor
from _v3_db_pool import get_db_pool

MODEL_VERSION = "cb_v1_15s"
TARGET_RETURN = 0.20
ENCODER_SEC = 15
HORIZONS = [30, 60, 180]

cls = CatBoostClassifier(thread_count=-1); cls.load_model("models/catboost_cls.cbm")
reg = CatBoostRegressor(thread_count=-1);  reg.load_model("models/catboost_reg.cbm")

async def fetch_candidates(conn):
    q = """
      WITH latest AS (
        SELECT token_id, MAX(ts) AS t0 FROM token_metrics_seconds GROUP BY token_id
      )
      SELECT l.token_id, l.t0
      FROM latest l
      JOIN token_metrics_seconds m ON m.token_id=l.token_id
      WHERE m.ts BETWEEN l.t0-($1-1) AND l.t0
      GROUP BY l.token_id, l.t0 HAVING COUNT(*) >= $1
      LIMIT 32;
    """
    return await conn.fetch(q, ENCODER_SEC)

def make_feats(rows):
    prices = [r["usd_price"] for r in rows]
    p0 = float(prices[-1])
    avg_p = float(np.mean(prices))
    std_p = float(np.std(prices))
    slope = (p0 - float(prices[0])) / max(1.0, ENCODER_SEC) / max(1e-9, avg_p)
    # простой вектор признаков; в проде расширяем фичи
    return np.array([[avg_p, std_p, slope]]), p0, slope

async def insert_forecast(conn, token_id, t0, horizon, prob, exp_ret, price0, slope):
    gamma = 0.9 if slope > 0 else 1.2
    y = [price0 * (1 + max(exp_ret, 0.0) * (t / horizon) ** gamma) for t in range(1, horizon + 1)]
    eta = next((i for i, p in enumerate(y, 1) if p >= price0 * (1 + TARGET_RETURN)), None)
    await conn.execute(
        """
        INSERT INTO ai_forecasts(
          token_id, model_id, origin_ts, encoder_len_sec, horizon_sec,
          score_up, exp_return, y_p50, target_return, eta_to_target_sec, price_now
        ) VALUES ($1, 1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (token_id, model_id, origin_ts) DO UPDATE SET
          score_up = EXCLUDED.score_up,
          exp_return = EXCLUDED.exp_return,
          y_p50 = EXCLUDED.y_p50,
          eta_to_target_sec = EXCLUDED.eta_to_target_sec
        """,
        token_id, t0, ENCODER_SEC, horizon, prob, exp_ret, y, TARGET_RETURN, eta, price0
    )

async def loop_once():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await fetch_candidates(conn)
        for r in rows:
            win = await conn.fetch(
                "SELECT usd_price FROM token_metrics_seconds WHERE token_id=$1 AND ts BETWEEN $2-($3-1) AND $2 ORDER BY ts",
                r["token_id"], r["t0"], ENCODER_SEC
            )
            if not win:
                continue
            x, p0, slope = make_feats(win)
            prob = float(cls.predict_proba(x)[0, 1])
            exp_ret = float(reg.predict(x))
            for H in HORIZONS:
                await insert_forecast(conn, r["token_id"], r["t0"], H, prob, exp_ret, p0, slope)

async def main_loop():
    while True:
        try:
            await loop_once()
        except Exception:
            pass
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main_loop())
```

## Приложение C. План действий (быстрый старт)

1) Создать таблицы `ai_models`, `ai_forecasts` (DDL выше).
2) Зарегистрировать модель в `ai_models` (baseline CatBoost) и положить артефакты в `models/`.
3) Собрать и сохранить `catboost_cls.cbm` и `catboost_reg.cbm` (обучение оффлайн на истории).
4) Запустить цикл инференса (скрипт выше) — каждые 1 сек заполняются записи в `ai_forecasts`.
5) Фронт читает `y_p50` для «жёлтой» линии, по желанию отрисовывает коридор `y_p10..y_p90`.
