# TCN‑Forecasting (PyTorch): краткое руководство

Этот документ описывает, как устроена и как запускать новую модель прогнозирования на Temporal Convolutional Networks (TCN) в папке `server/ai/`.

## Что делает модель
- Вход: последние `K` секунд (по умолчанию K=15), теперь с расширенным набором признаков (10 каналов):
  - Метрики: цена, ликвидность, mcap, |∆mcap|.
  - Торговля: buy_count, sell_count, total_trades, log1p(buy_usd), log1p(sell_usd), signed_log1p(net_usd), trade_imbalance, avg_trade_price_usd, median_trade_price_usd.
- Доп. вход: статические признаки токена — `holder_count`, `organic_score`, возраст (секунды), отношение `circ_supply/total_supply`, `top_holders_percentage`, `dev_balance_percentage`, флаги (`blockaid_rugpull`, `mint_authority_disabled`, `freeze_authority_disabled`).
- Выход: 
  - траектория относительной доходности на 300 секунд вперёд (массив длины 300),
  - вероятность «пампа»,
  - ожидаемая доходность.

## Основные файлы
- Модель: `server/ai/models/tcn.py:1`
- Датасет/загрузка данных (+агрегация сделок по секундам): `server/ai/datasets.py:1`
- Тренировка: `server/ai/training/train_tcn.py:1`
- Инференс (скрипт): `server/ai/infer/tcn_forecast.py:1`
- Конфиг AI: `server/ai/config.py:1`
- Регистрация моделей в БД: `server/ai/models/registry.py:1`

## Зависимости и окружение
- Python 3.10+ (локально: 3.13 тоже ок)
- PyTorch (CPU или MPS на Mac, Apple Silicon). Пример: `pip install torch torchvision torchaudio`.
- PostgreSQL с БД `crypto_db` (локальная по умолчанию на `localhost:5433`).

## Подготовка БД
1) Инициализация базовой схемы создаётся автоматически при первом подключении (`server/_v3_db_init.py:1`).
2) Примените AI-миграции (таблицы `ai_models`, `ai_forecasts`, …):
   - Команда: `python server/cli.py ai migrate`

## Где сохраняются веса модели
- Путь теперь стабильный: `server/ai/config.py:1` задаёт `MODELS_DIR` как абсолютный путь к `server/ai/models`.
- Артефакты сохраняются туда как файлы `tcn_best_YYYYmmdd_HHMMSS.pth`.

## Тренировка
- Скрипт: `python server/ai/training/train_tcn.py`
- Что делает:
  - Загружает обучающие окна из БД: `server/ai/datasets.py:47`.
  - Создаёт DataLoader: `server/ai/datasets.py:180`.
  - Создаёт модель: `server/ai/models/tcn.py:197`.
  - Обучает и сохраняет лучший чекпойнт: `server/ai/training/train_tcn.py:163` и `server/ai/training/train_tcn.py:231`.
  - Регистрирует модель в `ai_models`: `server/ai/models/registry.py:16`.

Замечания:
- По умолчанию данные берутся из `token_metrics_seconds` и `tokens`. Убедитесь, что они заполняются вашими процессами (`_v3_price_resolver.py`, `_v3_analyzer_jupiter.py`).
- На macOS с M3 скрипт автоматически использует `mps`, если доступно.

## Инференс (разовый тест)
- Скрипт: `python server/ai/infer/tcn_forecast.py`
- Логика загрузки модели (надёжная):
  1) Если передали `model_path`, загрузит оттуда.
  2) Иначе попытается взять последний `tcn` из таблицы `ai_models`.
  3) Если БД/запись недоступны — подберёт самый свежий локальный `tcn_best_*.pth` из `MODELS_DIR`.

См. реализацию: `server/ai/infer/tcn_forecast.py:29`.

## Использование в коде
Пример (асинхронно):
```python
from ai.infer.tcn_forecast import TCNForecaster

forecaster = TCNForecaster()  # можно также передать model_path=...
await forecaster.load_model()
res = await forecaster.predict(token_id=123, origin_ts=ts)
# res = { 'price_trajectory': np.array(shape=(300,)), 'pump_probability': float, 'expected_return': float }
```

Для визуализации: `TCNForecaster.generate_forecast_data(...)` вернёт точки для графика.

Признаки на инференсе формируются идентично train‑пайплайну: добавлены агрегаты по сделкам за последние K секунд в окне [t0−K+1, t0],
вычисляются count/объёмы по buy/sell, лог‑преобразования и дисбаланс. Если чекпойнт ожидает меньше каналов, вход автоматически
сужается или дополняется нулями до нужного размера (обратная совместимость).

## Точки расширения и качество
- Архитектура TCN: блоки `TemporalBlock` с дилатациями 1/2/4, выход объединяется со статическими фичами и расходится в три головы (траектория/класс/регрессия).
- Баланс loss: сейчас равные веса MSE+BCE+MSE, смотрите `train_tcn.py:35`.
- Признаки: базовый набор (цена/ликвидность/mcap/|∆mcap|). Рекомендуется дополнять торговыми агрегатами (buy/sell volume, count/sec, и т.д.).

## Частые проблемы
- Нет `torch`: установите через pip (см. выше).
- Нет таблицы `ai_models`: выполните `python server/cli.py ai migrate`.
- Путь к модели: теперь фиксированный `server/ai/models`; если запись в БД хранит относительный путь — код подхватит локальный артефакт как фолбэк.

## Мини‑чеклист «всё работает»
- `python server/cli.py ai migrate` — миграции применены, таблицы есть.
- `python server/ai/models/tcn.py` — быстрый self‑test forward pass (потребует torch).
- `python server/ai/training/train_tcn.py` — проходит хотя бы 1–2 эпохи, сохраняет `tcn_best_*.pth`, регистрирует модель.
- `python server/ai/infer/tcn_forecast.py` — загружает чекпойнт, печатает предсказания по нескольким токенам.
