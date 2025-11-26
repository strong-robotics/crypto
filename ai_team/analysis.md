status: ok

Notes Summary

- [NOTE] ai_team/notes/new_tokens.md: «надо получать с джупитер каждую 5 секунд новые токены обновлять таблицы исходя данных которые приходят»

API Overview

- Цель из заметок: «каждые 5 секунд получать новые токены из Jupiter и обновлять таблицы согласно приходящим данным» (ai_team/notes/new_tokens.md).
- Используемый внешний API для этой задачи: только Jupiter Tokens v2 (recent).

Endpoints & Parameters (по задаче new tokens)

- Jupiter Tokens v2 — Recent
  - GET https://lite-api.jup.ag/tokens/v2/recent
  - Параметры: отсутствуют (публичный эндпоинт).
  - Ответ: массив токенов с полями (id, name, symbol, decimals, icon, mcap, liquidity, usdPrice, firstPool.id, audit, stats5m/1h/6h/24h и т.д.).

 

Integration Notes (текущее состояние кода)

- [CODE] server/_v3_new_tokens.py: реализован JupiterScannerV3 — периодический опрос recent и сохранение в БД.
  - Интервал сканирования: [CODE] server/config.py:52 `JUPITER_SCANNER_INTERVAL = 5` (секунды).
  - [CODE] get_tokens_from_api(): тянет recent, сохраняет базовые поля токена, при наличии firstPool.id → `token_pair`, audit и stats.
  - [CODE] start_auto_scan()/ _auto_scan_loop(): цикл с period = `scan_interval`.
- Управление сканированием из API:
  - [CODE] server/main.py:365 POST `/api/auto-scan/start`, 371 POST `/api/auto-scan/stop`, 378 GET `/api/scanner/status` (legacy совместимость).
  - [CODE] server/main.py:412 POST `/api/analyzer/start`, 419 POST `/api/analyzer/stop` — включает unified scheduler, в котором каждая 6‑я итерация — новые токены.

- Схема БД (PostgreSQL, через пул): server/_v3_db_pool.py и миграции в коде
  - Таблица `tokens` принимает поля из recent (см. INSERT/UPDATE в `_v3_new_tokens.save_jupiter_data`).
  - При необходимости расширения полей — добавлять idempotent ALTER в инициализации.

- Конфигурация:
  - `config.JUPITER_RECENT_API` = https://lite-api.jup.ag/tokens/v2/recent
  - `config.JUPITER_SCANNER_INTERVAL` = 5 (сек) — соответствует заметке.

Recommendations (с учётом узкого объёма заметки)

- Зафиксировать поведение «каждые 5 секунд» тестом или health‑метрикой:
  - GET `/api/system/timers/status` уже возвращает состояния; можно добавить поле про активность auto‑scan.
- Явно документировать контракт сохранения полей recent → tokens (mapping) в `server/docs/`.
- Добавить метрику сохранений: количество новых/обновлённых токенов за цикл — уже логируется, можно отдавать через `/api/analyzer/status` или отдельный `/api/new-tokens/status` (если потребуется).
- При всплесках — добавить ограничение по limit в `get_tokens_from_api` и очередь на обработку.

Sources

- Notes:
  - ai_team/notes/new_tokens.md
- Docs cache:
  - ai_team/docs/https___dev.jup.ag_docs_tokens_v2.html
- Code:
  - server/_v3_new_tokens.py
  - server/config.py
  - server/main.py
