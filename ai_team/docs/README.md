Папка ./ai_team/docs — локальный кэш HTML‑документации

Назначение
- Хранить сохранённые через mcp:fetch копии страниц документации (Jupiter/Helius/Solana и др.).

Именование файлов
- Формат: URL со слешами, заменёнными на подчёркивания, с префиксом протокола, оканчивается на .html.
  Примеры:
  - https___dev.jup.ag_docs_tokens_v2.html
  - https___lite-api.jup.ag_price_v3.html
  - https___www.helius.dev_docs_api-reference_enhanced-transactions_gettransactionsbyaddress.html

Обновление
- Если требуется обновление — перезаписываем файл новой версией.
- Для критичных изменений (лимиты, авторизация, структуры ответов) добавляем краткую заметку в ./ai_team/notes/ с датой.

Источник ссылок
- См. ./ai_team/DOC_SOURCES.md — поддерживаем там список URL.
