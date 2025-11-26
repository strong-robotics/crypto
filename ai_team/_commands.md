cd /Users/yevhenvasylenko/Documents/Projects/Crypto/App

codex -p gpt-5 "
РОЛЬ: Researcher.
Прочитай ./ai_team/notes/*.md и документации API (через mcp:fetch и mcp:filesystem).
Сформируй файл analysis.md по правилам из ./agents/analysis.md."

codex -p gpt-5 "
РОЛЬ: Planner.
Прочитай RESEARCH.md и создай ROADMAP.md по правилам из ./agents/PLANNER.md."