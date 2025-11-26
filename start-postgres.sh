#!/bin/bash

# Кольори для виводу
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🐘 Starting local PostgreSQL...${NC}"

# Перевіряємо, чи не запущений вже Postgres
if pgrep -f "postgres.*server/db/pgdata" > /dev/null; then
    echo -e "${GREEN}✅ PostgreSQL is already running${NC}"
    exit 0
fi

# Запускаємо Postgres
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D server/db/pgdata -o "-c config_file=server/db/postgresql.conf -c hba_file=server/db/pg_hba.conf" -l server/db/postgres.log start

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ PostgreSQL started successfully on port 5433${NC}"
    echo -e "${BLUE}📊 Database: crypto_db${NC}"
    echo -e "${BLUE}👤 User: postgres${NC}"
    echo -e "${BLUE}🔗 Connection: localhost:5433${NC}"
else
    echo -e "${RED}❌ Failed to start PostgreSQL${NC}"
    exit 1
fi
