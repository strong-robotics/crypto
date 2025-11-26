# Список агентов

## Downloader
Файл с правилами: ./ai_team/downloader.md
Назначение: Выкачивает HTML документацию внешних API в `./ai_team/docs/` по списку из `./ai_team/_api_sources.md`.
Запуск (пример):
codex -p gpt-5 "Запусти агента Downloader по инструкции в ./ai_team/downloader.md (режим: refresh)"

## Researcher
Файл с правилами: ./ai_team/researcher.md
Назначение: Читает заметки и локальный кэш документации, формирует analysis.md.
Запуск (пример):
codex -p gpt-5 "Запусти агента Researcher, выполнив шаги, описанные в ./ai_team/researcher.md"

## Planner
Файл с правилами: ./ai_team/planner.md
Назначение: Преобразует analysis.md в конкретный план работ (plan.md) для Developer без расширения scope.
Запуск (пример):
codex -p gpt-5 "Запусти агента Planner, выполнив шаги из ./ai_team/planner.md"

## Developer
Файл с правилами: ./ai_team/developer.md
Назначение: Выполняет задачи из plan.md, вносит изменения в код/доки согласно плану.
Запуск (пример):
codex -p gpt-5 "Запусти агента Developer, выполняй задачи из ./ai_team/plan.md по правилам ./ai_team/developer.md"
