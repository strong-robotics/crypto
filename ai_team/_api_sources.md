Jupiter / Helius / Solana — Внешние API, которые мы используем

Jupiter (Lite API)
- Документация: https://dev.jup.ag/api-reference
- Base URLs:
  - Recent tokens: https://lite-api.jup.ag/tokens/v2/recent
  - Batch search:  https://lite-api.jup.ag/tokens/v2/search?query={addr1,addr2,...}
  - Price v3:     https://lite-api.jup.ag/price/v3?ids={mint1,mint2,...}
- Использование в проекте:
  - Recent → загрузка новых токенов, первичное сохранение в `tokens` (см. server/_v3_new_tokens.py)
  - Search → батч-обновление метрик, `firstPool.id` → `token_pair`, инкремент `check_jupiter` (см. server/_v3_analyzer_jupiter.py)
  - Price → монитор цены SOL для расчетов USD (см. server/_v2_sol_price.py)
- Примеры:
  - curl "https://lite-api.jup.ag/tokens/v2/recent"
  - curl "https://lite-api.jup.ag/tokens/v2/search?query=8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR"
  - curl "https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112"

Helius (Enhanced Transactions)
- Документация: https://www.helius.dev/docs/api-reference/enhanced-transactions/gettransactionsbyaddress
- Base URL: https://api.helius.xyz
- Эндпоинт: /v0/addresses/{address}/transactions
- Параметры: `api-key` (query), `limit`, `before` (пагинация)
- Использование в проекте:
  - История и live-трейды по пулу (`token_pair`) с парсингом buy/sell и расчетом USD (см. server/_v2_trades_history.py, server/_v3_live_trades.py)
- Примеры:
  - curl "https://api.helius.xyz/v0/addresses/{PAIR}/transactions?api-key=$HELIUS_API_KEY&limit=100"
  - curl "https://api.helius.xyz/v0/addresses/{PAIR}/transactions?api-key=$HELIUS_API_KEY&limit=100&before={last_signature}"

Solana JSON-RPC
- Документация: https://solana.com/docs/rpc
- Public RPC URL: https://api.mainnet-beta.solana.com
- Методы, которые используем:
  - getBalance — баланс кошелька для дашборда (см. server/_v2_balance.py)
  - (опционально) getHealth — для health-check внешних сервисов
- Примеры:
  - curl -X POST https://api.mainnet-beta.solana.com \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"getBalance","params":["<WALLET>"]}'

Дополнительно
- DexScreener: в конфиге указан base (https://api.dexscreener.com/latest/dex/search/), но в текущем V3-потоке не вызывается; присутствует в старых/временных файлах.

Правило для агента
- Перед изменениями интеграций или эндпоинтов подтягивать актуальные страницы документации через MCP fetch (см. ~/.codex/config.toml) и опираться на них.
