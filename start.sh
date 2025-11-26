#!/bin/bash

# Кольори для виводу
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Starting Jupiter Token Scanner...${NC}\n"

# Parse flags
SHOW_HISTORY=0
HISTORY_NOSORT=0
CHECK_POSITIONS=0
CLOSE_POSITIONS=0
for arg in "$@"; do
  case "$arg" in
    --history)
      SHOW_HISTORY=1
      ;;
    --history-nosort)
      SHOW_HISTORY=1
      HISTORY_NOSORT=1
      ;;
    --check-positions)
      CHECK_POSITIONS=1
      ;;
    --close-positions)
      CLOSE_POSITIONS=1
      ;;
  esac
done

# Запускаємо локальний Postgres
echo -e "${BLUE}🐘 Starting local PostgreSQL...${NC}"
./start-postgres.sh

# Функція для зупинки всіх процесів при виході
cleanup() {
    echo -e "\n${RED}🛑 Stopping all services...${NC}"
    kill $(jobs -p) 2>/dev/null
    exit
}

# Встановлюємо обробник для SIGINT (Ctrl+C)
trap cleanup SIGINT

# Перевіряємо, чи не зайняті порти
check_port() {
    if lsof -i ":$1" >/dev/null 2>&1; then
        echo -e "${RED}⚠️  Port $1 is already in use${NC}"
        echo -e "${BLUE}👉 Attempting to free port $1...${NC}"
        lsof -ti ":$1" | xargs kill -9 2>/dev/null
        sleep 1
    fi
}

# Перевіряємо порти
check_port 8001
check_port 8002

# Функція для перевірки доступності сервісу
wait_for_service() {
    local port=$1
    local service=$2
    local max_attempts=30
    local attempt=1

    echo -e "${BLUE}⏳ Waiting for $service to start...${NC}"
    
    while ! nc -z localhost $port >/dev/null 2>&1; do
        if [ $attempt -ge $max_attempts ]; then
            echo -e "${RED}❌ Failed to start $service after $max_attempts attempts${NC}"
            cleanup
            exit 1
        fi
        sleep 0.1
        attempt=$((attempt + 1))
    done
    
    echo -e "${GREEN}✅ $service is ready!${NC}"
}

# Запускаємо бекенд
echo -e "${GREEN}📡 Starting Python backend server...${NC}"
cd server
if [ "$SHOW_HISTORY" = "1" ]; then
  echo -e "${BLUE}ℹ️  Showing history tokens by default (TOKENS_SHOW_HISTORY=1)${NC}"
  export TOKENS_SHOW_HISTORY=1
fi
if [ "$HISTORY_NOSORT" = "1" ]; then
  echo -e "${BLUE}ℹ️  History view without sorting (insertion order)${NC}"
  export TOKENS_DISABLE_SORT=1
fi
source venv/bin/activate && python main.py &
cd ..

# Перевіряємо готовність бекенду
wait_for_service 8002 "Backend server"

# Додаткове очікування, щоб сервер точно готовий обробляти запити
sleep 2

# Функція для перевірки відкритих позицій
check_open_positions() {
    echo -e "\n${BLUE}🔍 Checking open positions...${NC}"
    max_retries=5
    retry=0
    while [ $retry -lt $max_retries ]; do
        response=$(curl -s -w "\n%{http_code}" -X GET "http://localhost:8002/api/wallet/check-positions" 2>/dev/null)
        http_code=$(echo "$response" | tail -n1)
        body=$(echo "$response" | sed '$d')
        
        if [ "$http_code" = "200" ]; then
            echo -e "${GREEN}✅ Open positions check:${NC}"
            echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
            return 0
        else
            retry=$((retry + 1))
            if [ $retry -lt $max_retries ]; then
                echo -e "${BLUE}⏳ Retrying... ($retry/$max_retries)${NC}"
                sleep 1
            fi
        fi
    done
    echo -e "${RED}❌ Failed to check open positions (HTTP $http_code)${NC}"
    echo "Response: $body"
    return 1
}

# Функція для закриття позицій (синхронізація з blockchain)
close_positions() {
    echo -e "\n${BLUE}🔄 Closing positions (syncing with blockchain)...${NC}"
    max_retries=5
    retry=0
    while [ $retry -lt $max_retries ]; do
        response=$(curl -s -w "\n%{http_code}" -X POST "http://localhost:8002/api/wallet/sync-positions" 2>/dev/null)
        http_code=$(echo "$response" | tail -n1)
        body=$(echo "$response" | sed '$d')
        
        if [ "$http_code" = "200" ]; then
            echo -e "${GREEN}✅ Positions sync result:${NC}"
            echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
            return 0
        else
            retry=$((retry + 1))
            if [ $retry -lt $max_retries ]; then
                echo -e "${BLUE}⏳ Retrying... ($retry/$max_retries)${NC}"
                sleep 1
            fi
        fi
    done
    echo -e "${RED}❌ Failed to sync positions (HTTP $http_code)${NC}"
    echo "Response: $body"
    return 1
}

# Якщо вказані флаги для перевірки/закриття позицій, виконуємо ЇХ и виходимо
if [ "$CHECK_POSITIONS" = "1" ] || [ "$CLOSE_POSITIONS" = "1" ]; then
    if [ "$CHECK_POSITIONS" = "1" ]; then
        check_open_positions
        echo ""
    fi
    
    if [ "$CLOSE_POSITIONS" = "1" ]; then
        close_positions
        echo ""
    fi
    
    echo -e "${BLUE}✅ Operation completed. Exiting...${NC}"
    exit 0
fi

# АВТОМАТИЧНО: Синхронізація позицій при кожному старті (закриває позиції, продані через Phantom)
# Це виконується автоматично, щоб очистити невідповідності між БД і blockchain
close_positions
echo ""

# Запускаємо фронтенд
echo -e "${GREEN}🌐 Starting Next.js frontend...${NC}"
npm run dev &

# Перевіряємо готовність фронтенду
wait_for_service 8001 "Frontend server"

# Виводимо інформацію про доступ
echo -e "\n${BLUE}🌍 Access the application:${NC}"
echo -e "Frontend: ${GREEN}http://localhost:8001${NC}"
echo -e "Backend:  ${GREEN}http://localhost:8002${NC}\n"
echo -e "${BLUE}📝 Press Ctrl+C to stop all services${NC}\n"

# Чекаємо, поки всі фонові процеси працюють
wait
