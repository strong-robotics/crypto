#!/bin/bash

# Кольори для виводу
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🛑 Stopping Jupiter Token Scanner...${NC}\n"

# Функція для зупинки процесів на конкретному порту
stop_port() {
    local port=$1
    local service_name=$2
    if lsof -i ":$port" >/dev/null 2>&1; then
        echo -e "${GREEN}👉 Stopping $service_name on port $port...${NC}"
        lsof -ti ":$port" | xargs kill -9 2>/dev/null
        echo -e "${GREEN}✅ $service_name stopped${NC}"
    else
        echo -e "${BLUE}ℹ️  No service running on port $port${NC}"
    fi
}

# Зупиняємо Next.js (порт 8001)
stop_port 8001 "Frontend (Next.js)"

# Зупиняємо Python backend (порт 8002)
stop_port 8002 "Backend (Python)"

echo -e "\n${GREEN}✅ All services stopped successfully${NC}"
