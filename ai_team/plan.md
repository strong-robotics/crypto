status: ready

Scope Summary
- [NOTE] Каждые 5 секунд получать новые токены из Jupiter (tokens v2 recent) и обновлять таблицы согласно приходящим данным (ai_team/analysis.md: Notes Summary).

Backlog
- Code
  - [T1] Проверить/зафиксировать интервал сканирования 5с — `server/config.py` [NOTE][CODE]
    - Done: `JUPITER_SCANNER_INTERVAL = 5` присутствует и используется в рантайме.
  - [T2] Убедиться, что сохранение полей recent → БД соответствует контракту — `server/_v3_new_tokens.py` [NOTE][CODE]
    - Done: INSERT/UPDATE в `save_jupiter_data()` покрывает основные поля (id/name/symbol/decimals/icon/mcap/liquidity/usdPrice/firstPool.id→token_pair/audit/stats*), без падений на None.
  - [T3] Логи и метрики цикла — `server/_v3_new_tokens.py` [CODE]
    - Done: в `_auto_scan_loop()` логируются fetched/saved/new; при желании добавить счетчики в ответ `/api/scanner/status`.

- API
  - [T4] Контрольные эндпоинты управления авто‑сканом — `server/main.py` [CODE]
    - Done: POST `/api/auto-scan/start|stop`, GET `/api/scanner/status` работают и возвращают корректный статус сканера.

- Docs
  - [T5] Зафиксировать mapping recent→tokens — `server/docs/recent_mapping.md` [NOTE]
    - Done: документ с перечислением полей recent и соответствующих колонок таблицы tokens.

Test Plan
- Конфиг: убедиться `JUPITER_SCANNER_INTERVAL=5` (server/config.py).
- Запуск: POST `/api/auto-scan/start`; дождаться ≥10 секунд; проверить логи “Jupiter API: … fetched, … saved, … NEW”.
- БД: выборка из таблицы `tokens` — появились/обновились строки с актуальным `created_at`/значениями.
- Стоп: POST `/api/auto-scan/stop`; статус `/api/scanner/status` отражает остановку.

Risks & Assumptions
- Публичный Jupiter endpoint может временно замедляться; предусмотрены ретраи в сканере.
- Формат recent может эволюционировать; INSERT/UPDATE должны быть устойчивыми к отсутствию полей.

Options
- (Опционально) Отдавать счетчики saved/new через `/api/scanner/status`.

Sources
- ai_team/analysis.md (Notes Summary, API Overview, Integration Notes, Recommendations)

