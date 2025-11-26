# Jupiter Token Scanner - Алгоритм получения новых токенов

## 📋 Обзор системы

Jupiter Token Scanner - это асинхронная система для получения, сохранения, анализа и рассылки новых токенов с Jupiter API. Система работает в реальном времени через WebSocket, сохраняя данные в SQLite базе данных.

## 🏗️ Архитектура

### Основные компоненты

- **`AsyncJupiterScanner`** - получение токенов с Jupiter API
- **`AsyncTokenDatabase`** - работа с SQLite базой данных  
- **`AsyncTokenAnalyzer`** - анализ безопасности токенов
- **`AppState`** - глобальное состояние приложения
- **WebSocket Server** - real-time коммуникация с frontend

### Структура базы данных

```sql
-- Основные таблицы
token_ids          -- Адреса токенов + метаданные
tokens             -- Детальная информация о токенах
token_stats_24h    -- Статистика за 24 часа
token_audit        -- Результаты аудита безопасности
token_first_pool   -- Информация о первом пуле
token_tags         -- Теги токенов
```

## 🔄 Алгоритм работы

### 1. Инициализация системы

```python
# main.py
state = AppState()
db_instance = AsyncTokenDatabase()

@app.on_event("startup")
async def startup_event():
    await ensure_scanner()
```

### 2. Автоматическое сканирование (каждые 5 секунд)

```python
async def auto_scan():
    while state.is_scanning:
        # Получение токенов с Jupiter API
        result = await state.scanner.get_tokens_from_api(limit=20)
        
        # Добавление в очередь анализа
        token_addresses = [token.get('id') for token in result.get('tokens', [])]
        await add_tokens_for_analysis(token_addresses)
        
        # Рассылка через WebSocket
        await broadcast_to_clients(result)
        
        await asyncio.sleep(state.auto_scan_interval)
```

### 3. Получение токенов с Jupiter API

**URL:** `https://lite-api.jup.ag/tokens/v2/recent`

**Rate Limiting:** 2 секунды между запросами
**Retry Logic:** 3 попытки с экспоненциальной задержкой

```python
async def get_tokens_from_api(self, limit: int = 20):
    data = await self.make_request_with_retry(self.api_url)
    tokens = data[:limit]
    
    # Сохранение в БД
    for token in tokens:
        await self.save_token(token)
    
    return formatted_result
```

### 4. Сохранение в базу данных

```python
async def save_token(self, token_data: Dict[str, Any]):
    # 1. Вставка в token_ids (если не существует)
    INSERT OR IGNORE INTO token_ids (token_address, token_pair)
    
    # 2. Обновление деталей в tokens
    INSERT OR REPLACE INTO tokens (name, symbol, price, liquidity, ...)
    
    # 3. Сохранение статистики 24h
    INSERT OR REPLACE INTO token_stats_24h (...)
    
    # 4. Сохранение аудита
    INSERT OR REPLACE INTO token_audit (...)
    
    # 5. Сохранение тегов
    INSERT INTO token_tags (token_id, tag)
```

### 5. Анализ токенов (каждые 3 секунды)

```python
# Очередь анализа: token_id -> {iterations_left, last_analysis}
async def analyze_tokens():
    for token_address in analysis_queue:
        # Анализ безопасности, honeypot, DEX пары
        # Обновление: token_pair, is_honeypot, security_analyzed_at
```

### 6. WebSocket коммуникация

**Эндпоинты:**
- `/ws/tokens` - получение токенов
- `/ws/balances` - мониторинг балансов

**При подключении:** отправляются все токены из БД
**Real-time:** обновления через `broadcast_to_clients()`

## ⚙️ Конфигурация

### Таймеры
- **Auto scan interval:** 5 секунд
- **Analysis interval:** 3 секунды  
- **Rate limiting:** 2 секунды между API запросами

### Retry настройки
- **Max retries:** 3
- **Base delay:** 5 секунд
- **Exponential backoff:** 2^attempt

### База данных
- **SQLite** с WAL режимом
- **Database locking** для защиты от race conditions
- **Индексы** на ключевых полях

## 🔧 Ключевые особенности

1. **Асинхронность** - вся система работает на async/await
2. **Rate Limiting** - защита от перегрузки API
3. **Retry Logic** - надежность при сбоях сети
4. **Database Locking** - защита от race conditions
5. **Real-time Updates** - WebSocket для мгновенных обновлений
6. **Analysis Queue** - очередь для анализа токенов
7. **Error Handling** - обработка ошибок на всех уровнях

## 📊 Поток данных

```
Jupiter API → Auto Scanner → Database → Analyzer → WebSocket → Frontend
     ↓              ↓           ↓         ↓         ↓
  Rate Limit    Save Token   Analysis   Broadcast  Real-time
  Retry Logic   Queue Add    Security   Clients    Updates
```

## 🚀 API эндпоинты

### Управление сканированием
```bash
# Запуск auto-scan
POST /api/auto-scan/start

# Остановка auto-scan  
POST /api/auto-scan/stop

# Запуск balance monitoring
POST /api/balance-monitor/start

# Остановка balance monitoring
POST /api/balance-monitor/stop

# Статус системы
GET /api/balance-monitor/status
```

### WebSocket эндпоинты
```bash
# Получение токенов
ws://localhost:8002/ws/tokens

# Мониторинг балансов
ws://localhost:8002/ws/balances
```

## 📁 Структура файлов

```
server/
├── main.py                           # Основной FastAPI сервер
├── _v1_new_tokens_jupiter_async.py   # Jupiter API сканер
├── _v1_analyzer_async.py             # Анализатор токенов
├── _v1_balance.py                    # Мониторинг балансов
└── db/
    └── tokens.db                     # SQLite база данных
```

## 🔍 Детали реализации

### Rate Limiting
```python
async def respect_rate_limit(self):
    current_time = time.time()
    time_since_last_request = current_time - self.last_request_time
    
    if time_since_last_request < self.rate_limit_delay:
        sleep_time = self.rate_limit_delay - time_since_last_request
        await asyncio.sleep(sleep_time)
    
    self.last_request_time = time.time()
```

### Retry Logic
```python
async def make_request_with_retry(self, url: str, timeout: int = 10):
    for attempt in range(self.max_retries):
        try:
            await self.respect_rate_limit()
            async with self.session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    wait_time = self.retry_delay * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                    continue
        except Exception as e:
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_delay)
            continue
```

### Database Locking
```python
async def save_token(self, token_data: Dict[str, Any]):
    async with self.db_lock:
        # Безопасные операции с БД
        cursor = await self.conn.execute(...)
        await self.conn.commit()
```

## 🐛 Обработка ошибок

Система имеет многоуровневую обработку ошибок:
- **API уровень** - retry logic с экспоненциальной задержкой
- **Database уровень** - transaction rollback при ошибках
- **WebSocket уровень** - удаление отключенных клиентов
- **Application уровень** - логирование и graceful degradation

## 📈 Мониторинг

Система предоставляет детальное логирование:
- Количество подключенных клиентов
- Статистика сохраненных токенов
- Ошибки API и базы данных
- Статус анализатора и сканера

---

*Документация создана: $(date)*
*Версия системы: 1.0*

## 🚨 КРИТИЧЕСКИ ВАЖНЫЕ ДЕТАЛИ ДЛЯ ВОССТАНОВЛЕНИЯ КОНТЕКСТА

### Глобальные переменные и состояния
```python
# main.py - ГЛОБАЛЬНОЕ СОСТОЯНИЕ СИСТЕМЫ
state = AppState()  # ВСЕГДА существует
db_instance = AsyncTokenDatabase()  # ВСЕГДА существует

# _v1_analyzer_async.py - ГЛОБАЛЬНЫЙ АНАЛИЗАТОР
analyzer_instance: Optional[AsyncTokenAnalyzer] = None  # ГЛОБАЛЬНАЯ ПЕРЕМЕННАЯ
```

### Ключевые функции для восстановления
```python
# ОБЯЗАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ВОССТАНОВЛЕНИЯ:
await ensure_scanner()           # Восстановление сканера
await ensure_analyzer()          # Восстановление анализатора  
await ensure_balance_monitor()   # Восстановление мониторинга балансов
await get_analyzer()             # Получение глобального анализатора
```

### Структура AppState (КРИТИЧНО!)
```python
class AppState:
    scanner: Optional[AsyncJupiterScanner] = None
    analyzer_task: Optional[asyncio.Task] = None
    auto_scan_task: Optional[asyncio.Task] = None
    auto_scan_interval: int = 5
    is_scanning: bool = False
    connected_clients: List[WebSocket] = []
    
    # Balance monitoring
    balance_monitor: Optional[BalanceV1] = None
    balance_task: Optional[asyncio.Task] = None
    is_monitoring_balance: bool = False
    balance_interval: int = 3
```

### Порядок инициализации (НЕ НАРУШАТЬ!)
1. `state = AppState()` - создание состояния
2. `db_instance = AsyncTokenDatabase()` - создание БД
3. `await ensure_scanner()` - инициализация сканера
4. `await ensure_analyzer()` - инициализация анализатора
5. `await ensure_balance_monitor()` - инициализация мониторинга

### WebSocket клиенты (КРИТИЧНО ДЛЯ ВОССТАНОВЛЕНИЯ!)
```python
# state.connected_clients - СПИСОК ВСЕХ ПОДКЛЮЧЕННЫХ КЛИЕНТОВ
# При перезапуске ВСЕ КЛИЕНТЫ ТЕРЯЮТСЯ!
# Нужно переподключиться с frontend
```

### База данных - структура таблиц (ПОЛНАЯ!)
```sql
-- ОСНОВНЫЕ ТАБЛИЦЫ (НЕ ИЗМЕНЯТЬ СТРУКТУРУ!)
CREATE TABLE token_ids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT UNIQUE NOT NULL,
    token_pair TEXT UNIQUE,
    is_honeypot BOOLEAN,
    lp_owner TEXT,
    dev_address TEXT,
    security_analyzed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    pattern TEXT,
    check_dexscreener INTEGER,
    check_jupiter INTEGER,
    check_sol_rpc INTEGER
);

CREATE TABLE tokens (
    token_id INTEGER PRIMARY KEY,
    name TEXT, symbol TEXT, icon TEXT, decimals INTEGER,
    twitter TEXT, dev TEXT, circ_supply NUMERIC, total_supply NUMERIC,
    token_program TEXT, launchpad TEXT, holder_count INTEGER,
    usd_price NUMERIC, liquidity NUMERIC, fdv NUMERIC, mcap NUMERIC,
    bonding_curve NUMERIC, price_block_id INTEGER,
    organic_score NUMERIC, organic_score_label TEXT,
    updated_at TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);

-- + token_stats_5m, token_stats_1h, token_stats_6h, token_stats_24h
-- + token_audit, token_first_pool, token_tags
```

### Анализатор - очередь и логика (КРИТИЧНО!)
```python
# Очередь анализа: token_id -> {iterations_left, last_analysis}
self.analysis_queue: Dict[str, Dict[str, Any]] = {}

# 3 итерации анализа для каждого токена
# Rate limiting: 1 секунда между анализами
# Batch size: до 100 токенов за раз
```

### Balance Monitor - ключевые детали
```python
# Файл keys.json - КРИТИЧНО ДЛЯ РАБОТЫ!
# Структура: [{"id": "1", "name": "Wallet 1", "bits": [1,2,3...], "date_added": "..."}]
# bits_to_address() - конвертация приватного ключа в адрес
# Мониторинг каждые 3 секунды
```

### API эндпоинты (ПОЛНЫЙ СПИСОК!)
```bash
# Управление сканированием
POST /api/auto-scan/start
POST /api/auto-scan/stop

# Управление балансами  
POST /api/balance-monitor/start
POST /api/balance-monitor/stop
GET  /api/balance-monitor/status

# WebSocket
ws://localhost:8002/ws/tokens
ws://localhost:8002/ws/balances
```

### Порядок запуска сервера (НЕ НАРУШАТЬ!)
```bash
cd /Users/yevhenvasylenko/Documents/Projects/Crypto/App/server
python main.py
# ИЛИ
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

### Критические файлы (НЕ УДАЛЯТЬ!)
- `server/main.py` - основной сервер
- `server/_v1_new_tokens_jupiter_async.py` - Jupiter API
- `server/_v1_analyzer_async.py` - анализатор токенов
- `server/_v1_balance.py` - мониторинг балансов
- `server/keys.json` - приватные ключи кошельков
- `server/db/tokens.db` - база данных SQLite

### При перезапуске (АЛГОРИТМ ВОССТАНОВЛЕНИЯ!)
1. Проверить существует ли `state.scanner`
2. Если нет - вызвать `await ensure_scanner()`
3. Проверить существует ли `analyzer_instance`
4. Если нет - вызвать `await get_analyzer()`
5. Проверить существует ли `state.balance_monitor`
6. Если нет - вызвать `await ensure_balance_monitor()`
7. Проверить подключение к БД: `await db_instance.ensure_connection()`

### Логи для диагностики
```python
# Ключевые сообщения в логах:
"📡 Broadcasting to X clients" - WebSocket работает
"💰 Balance update: X wallets, Y SOL" - балансы работают  
"🔍 Adding X tokens for analysis" - анализатор работает
"✅ Analyzer started successfully" - анализатор запущен
"❌ Error" - ошибка, нужно диагностировать
```

### Настройки портов и хостов
```python
# main.py
uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)

# Frontend подключается к:
# ws://localhost:8002/ws/tokens
# ws://localhost:8002/ws/balances
```

### Docker конфигурация
```yaml
# docker-compose.yml существует
# Dockerfile.frontend существует  
# Dockerfile (server) существует
# start.sh / stop.sh - скрипты запуска
```

---
**ВАЖНО:** Этот Memory Bank содержит ВСЕ критические детали для восстановления работы системы после потери контекста. Использовать как справочник при перезапуске или восстановлении.

## 🔧 ТЕХНИЧЕСКИЕ ДЕТАЛИ РЕАЛИЗАЦИИ

### AsyncJupiterScanner - ключевые методы
```python
# ОСНОВНЫЕ МЕТОДЫ (НЕ ИЗМЕНЯТЬ СИГНАТУРЫ!)
async def get_tokens_from_api(limit: int = 20) -> Dict[str, Any]
async def save_token(token: Dict[str, Any]) -> bool  
async def get_all_tokens_from_db(limit: int = 100) -> Dict[str, Any]
async def make_request_with_retry(url: str, timeout: int = 10) -> Optional[Dict[str, Any]]
async def respect_rate_limit()
```

### AsyncTokenDatabase - критические методы
```python
# ОСНОВНЫЕ МЕТОДЫ БД (НЕ ИЗМЕНЯТЬ!)
async def save_token(token_data: Dict[str, Any]) -> bool
async def get_tokens(limit: int = 20) -> Dict[str, Any]
async def get_tokens_needing_analysis(max_checks: int = 3, limit: int = 200) -> List[str]
async def ensure_connection()
async def init_db()  # Создает все таблицы и индексы
```

### AsyncTokenAnalyzer - анализ логика
```python
# КЛЮЧЕВЫЕ МЕТОДЫ АНАЛИЗАТОРА
async def start_analysis_loop()  # Главный цикл анализа
async def add_tokens_to_analysis(token_addresses: List[str])  # Добавить в очередь
async def batch_analyze_tokens(token_addresses: List[str]) -> Dict[str, Any]  # Batch анализ
async def analyze_single_token(token_address: str) -> Dict[str, Any]  # Одиночный анализ
```

### BalanceV1 - мониторинг балансов
```python
# КЛЮЧЕВЫЕ МЕТОДЫ BALANCE
def load_wallets_from_keys(keys_file: str = "keys.json") -> List[Dict[str, Any]]
async def get_sol_balances_for_wallets(wallets: List[Dict[str, Any]]) -> List[Dict[str, Any]]
def bits_to_address(bits: List[int]) -> str  # Конвертация приватного ключа
```

### WebSocket обработка (КРИТИЧНО!)
```python
# ЭНДПОИНТЫ WebSocket
@app.websocket("/ws/tokens")  # Получение токенов
@app.websocket("/ws/balances")  # Мониторинг балансов

# При подключении:
# 1. Отправляются ВСЕ токены из БД (limit=1000)
# 2. Клиент добавляется в state.connected_clients
# 3. Real-time обновления через broadcast_to_clients()
```

### Конфигурация и переменные окружения
```python
# КОНСТАНТЫ (НЕ ИЗМЕНЯТЬ!)
JUPITER_API_URL = "https://lite-api.jup.ag/tokens/v2/recent"
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
RATE_LIMIT_DELAY = 2.0  # секунды между API запросами
MAX_RETRIES = 3
RETRY_DELAY = 5.0
AUTO_SCAN_INTERVAL = 5  # секунды
ANALYSIS_INTERVAL = 3  # секунды
BALANCE_INTERVAL = 3  # секунды
```

### Обработка ошибок (ПОЛНАЯ СИСТЕМА!)
```python
# УРОВНИ ОБРАБОТКИ ОШИБОК:
# 1. API уровень - retry logic с экспоненциальной задержкой
# 2. Database уровень - transaction rollback
# 3. WebSocket уровень - удаление отключенных клиентов  
# 4. Application уровень - логирование и graceful degradation
# 5. Analyzer уровень - пропуск ошибочных токенов
```

### База данных - индексы (КРИТИЧНО ДЛЯ ПРОИЗВОДИТЕЛЬНОСТИ!)
```sql
-- ОСНОВНЫЕ ИНДЕКСЫ (НЕ УДАЛЯТЬ!)
CREATE INDEX idx_token_ids_address ON token_ids(token_address)
CREATE INDEX idx_token_ids_pair ON token_ids(token_pair)  
CREATE INDEX idx_token_ids_created ON token_ids(created_at)
CREATE INDEX idx_token_ids_honeypot ON token_ids(is_honeypot)
CREATE INDEX idx_tokens_price ON tokens(usd_price)
CREATE INDEX idx_tokens_liquidity ON tokens(liquidity)
CREATE INDEX idx_tokens_updated ON tokens(updated_at)
CREATE INDEX idx_tokens_organic_score ON tokens(organic_score)
```

### Файловая структура проекта (ПОЛНАЯ!)
```
/Users/yevhenvasylenko/Documents/Projects/Crypto/App/
├── server/                           # Backend сервер
│   ├── main.py                      # Основной FastAPI сервер
│   ├── _v1_new_tokens_jupiter_async.py  # Jupiter API сканер
│   ├── _v1_analyzer_async.py        # Анализатор токенов
│   ├── _v1_balance.py               # Мониторинг балансов
│   ├── keys.json                    # Приватные ключи кошельков
│   ├── requirements.txt             # Python зависимости
│   ├── Dockerfile                   # Docker для сервера
│   └── db/
│       └── tokens.db                # SQLite база данных
├── src/                             # Frontend React
│   ├── app/
│   │   ├── page.tsx                 # Главная страница
│   │   └── layout.tsx               # Layout компонент
│   └── components/                  # React компоненты
├── docker-compose.yml               # Docker композиция
├── Dockerfile.frontend              # Docker для frontend
└── .cursor/memory-bank/             # Memory Bank документация
    └── jupiter-token-scanner-algorithm.md
```

### Запуск и остановка (ПОЛНЫЙ АЛГОРИТМ!)
```bash
# ЗАПУСК СЕРВЕРА:
cd /Users/yevhenvasylenko/Documents/Projects/Crypto/App/server
python main.py

# ИЛИ через uvicorn:
uvicorn main:app --host 0.0.0.0 --port 8002 --reload

# ИЛИ через Docker:
docker-compose up

# ОСТАНОВКА:
# Ctrl+C или
docker-compose down
```

### Frontend подключение (КРИТИЧНО!)
```javascript
// WebSocket подключение к серверу:
const wsTokens = new WebSocket('ws://localhost:8002/ws/tokens');
const wsBalances = new WebSocket('ws://localhost:8002/ws/balances');

// Обработка сообщений:
wsTokens.onmessage = (event) => {
    const data = JSON.parse(event.data);
    // Обновление UI с токенами
};

wsBalances.onmessage = (event) => {
    const data = JSON.parse(event.data);
    // Обновление UI с балансами
};
```

### Диагностика проблем (АЛГОРИТМ РЕШЕНИЯ!)
```bash
# 1. Проверить запущен ли сервер:
curl http://localhost:8002/api/balance-monitor/status

# 2. Проверить логи сервера:
# Искать сообщения: "✅ Analyzer started successfully"

# 3. Проверить базу данных:
sqlite3 server/db/tokens.db "SELECT COUNT(*) FROM token_ids;"

# 4. Проверить WebSocket:
# Открыть http://localhost:8002/ws/tokens в браузере

# 5. Проверить файл keys.json:
cat server/keys.json | head -5
```

### Критические зависимости (НЕ УДАЛЯТЬ!)
```python
# requirements.txt:
fastapi>=0.115.0
uvicorn>=0.32.0
aiohttp>=3.13.0
aiosqlite>=0.21.0
websockets>=15.0.1
base58>=2.1.1
python-dotenv>=1.0.1
```

---
**ФИНАЛЬНАЯ ВАЖНАЯ ЗАМЕТКА:** 
Этот Memory Bank содержит ВСЕ необходимые детали для полного восстановления работы системы Jupiter Token Scanner. При потере контекста - читать этот файл полностью перед началом работы.
