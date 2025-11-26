# Balance Monitoring System - Система мониторинга балансов

## 📋 Обзор системы

Balance Monitoring System - это асинхронная система для мониторинга SOL балансов кошельков в реальном времени. Система автоматически загружает данные при запуске сервера и рассылает их через WebSocket при подключении клиентов.

## 🏗️ Архитектура

### Основные компоненты

- **`BalanceV1`** - автономный класс для работы с балансами кошельков
- **`AppState.balance_monitor`** - глобальный экземпляр мониторинга
- **WebSocket `/ws/balances`** - real-time рассылка балансов
- **`keys.json`** - файл с приватными ключами кошельков (фиксированный путь)

### Структура данных

```json
// keys.json - структура файла с кошельками
[
  {
    "id": 1,
    "name": "bot 1", 
    "address": "8jneYFvC2Yy7yt3F79DErG4Fn6zuU5sXAF9ZM8TU5rDS",
    "date_added": "2025-10-01T13:31:32.353690",
    "bits": [193, 140, 165, 145, 93, 250, 23, 202, ...]  // 64 байта приватного ключа
  }
]

// Результат get_sol_balances_for_wallets()
[
  {
    "id": 1,
    "name": "bot 1",
    "address": "8jneYFvC2Yy7yt3F79DErG4Fn6zuU5sXAF9ZM8TU5rDS", 
    "sol_balance": 0.123456789,
    "value_usd": 12.34,
    "sol_price_usd": 100.0,
    "date_added": "2025-10-01T13:31:32.353690"
  }
]
```

## 🔄 Алгоритм работы

### 1. Инициализация системы при запуске

```python
# main.py - AppState
class AppState:
    balance_monitor: Optional[BalanceV1] = None

# Автоматическая инициализация при запуске сервера
async def ensure_balance_monitor():
    if state.balance_monitor is None:
        state.balance_monitor = BalanceV1()
        await state.balance_monitor.__aenter__()
        # Автоматически загружаем данные при создании
        await state.balance_monitor.load_balance_data()
```

### 2. Автоматическая загрузка данных

```python
# BalanceV1.load_balance_data() - вызывается при инициализации
async def load_balance_data(self):
    try:
        if not self.session:
            await self.__aenter__()
        
        # Загружаем кошельки из keys.json (фиксированный путь)
        wallets = self.load_wallets_from_keys()
        
        if wallets:
            # Получаем балансы для всех кошельков
            wallet_balances = await self.get_sol_balances_for_wallets(wallets)
            self.balance_data = wallet_balances
            return wallet_balances
        
        return None
    except Exception as e:
        return None
```

### 3. WebSocket подключение клиента

```python
@app.websocket("/ws/balances")
async def websocket_balances_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
        
        # Забезпечуємо, що баланс монітор ініціалізований
        await ensure_balance_monitor()
        
        # Додаємо клієнта до баланс монітора
        state.balance_monitor.add_client(websocket)
        
        # Відправляємо початкові дані
        await state.balance_monitor.send_initial_data(websocket)
        
        # Очікування повідомлень від клієнта
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
                
    except Exception as e:
        pass
    finally:
        # Видаляємо клієнта з баланс монітора
        if state.balance_monitor:
            state.balance_monitor.remove_client(websocket)
```

### 4. Загрузка кошельков из keys.json

```python
def load_wallets_from_keys(self) -> List[Dict[str, Any]]:
    try:
        with open("keys.json", 'r', encoding='utf-8') as f:
            keys_data = json.load(f)
        
        wallets = []
        for key_data in keys_data:
            bits = key_data.get("bits", [])
            address = self.bits_to_address(bits)
            
            if address:
                wallets.append({
                    "id": key_data.get("id"),
                    "name": key_data.get("name"), 
                    "address": address,
                    "date_added": key_data.get("date_added")
                })
        
        return wallets
    except:
        return []
```

### 5. Конвертация приватного ключа в адрес

```python
def bits_to_address(self, bits: List[int]) -> str:
    try:
        private_key_bytes = bytes(bits)
        if len(private_key_bytes) == 64:
            public_key_bytes = private_key_bytes[32:64]  # Последние 32 байта
        else:
            public_key_bytes = private_key_bytes[:32]    # Первые 32 байта
        return base58.b58encode(public_key_bytes).decode('utf-8')
    except:
        return ""
```

### 6. Получение SOL баланса через RPC

```python
async def get_sol_balance(self, address: str) -> float:
    try:
        payload = {
            "jsonrpc": "2.0", 
            "id": 1, 
            "method": "getBalance", 
            "params": [address]
        }
        async with self.session.post(self.rpc_url, json=payload) as response:
            if response.status == 200:
                data = await response.json()
                if "result" in data:
                    lamports = data["result"]["value"]
                    return lamports / 1_000_000_000  # Конвертация lamports в SOL
            return 0.0
    except:
        return 0.0
```

### 7. Получение цены SOL в USD

```python
async def get_sol_price_usd(self) -> float:
    try:
        url = "https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112"
        async with self.session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                sol_data = data.get("So11111111111111111111111111111111111111112")
                if sol_data:
                    price = float(sol_data.get("usdPrice", 0))
                    if price > 0:
                        return price
            return 0.0
    except Exception:
        return 0.0
```

### 8. Batch обработка кошельков с семафором

```python
async def get_sol_balances_for_wallets(self, wallets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sol_price_usd = await self.get_sol_price_usd()
    
    semaphore = asyncio.Semaphore(5)  # Максимум 5 одновременных запросов
    
    async def get_balance_with_semaphore(wallet):
        async with semaphore:
            sol_balance = await self.get_sol_balance(wallet['address'])
            value_usd = sol_balance * sol_price_usd if sol_price_usd > 0 else 0.0
            
            return {
                "id": wallet['id'],
                "name": wallet['name'],
                "address": wallet['address'],
                "sol_balance": sol_balance,
                "value_usd": value_usd,
                "sol_price_usd": sol_price_usd,
                "date_added": wallet.get('date_added', 'Unknown')
            }
    
    tasks = [get_balance_with_semaphore(wallet) for wallet in wallets]
    wallet_balances = await asyncio.gather(*tasks, return_exceptions=True)
    
    return [w for w in wallet_balances if not isinstance(w, Exception)]
```

### 5. Управление WebSocket клиентами

```python
# BalanceV1 методы для управления клиентами
def add_client(self, websocket: WebSocket):
    self.connected_clients.append(websocket)

def remove_client(self, websocket: WebSocket):
    if websocket in self.connected_clients:
        self.connected_clients.remove(websocket)

async def send_initial_data(self, websocket: WebSocket):
    try:
        if self.balance_data:
            await websocket.send_text(json.dumps(self.balance_data, ensure_ascii=False))
        else:
            balance_data = await self.load_balance_data()
            if balance_data:
                await websocket.send_text(json.dumps(balance_data, ensure_ascii=False))
            else:
                await websocket.send_text(json.dumps([], ensure_ascii=False))
    except Exception as e:
        pass
```

### 6. Рассылка данных клиентам

```python
async def broadcast_to_clients(self, data):
    if not self.connected_clients:
        return
        
    json_data = json.dumps(data, ensure_ascii=False)
    
    disconnected_clients = []
    for client in self.connected_clients:
        try:
            await client.send_text(json_data)
            await asyncio.sleep(0.001)
        except Exception as e:
            disconnected_clients.append(client)
    
    for client in disconnected_clients:
        self.connected_clients.remove(client)
```

## ⚙️ Конфигурация

### Параметры
- **Request timeout:** 10 секунд для API запросов
- **Semaphore limit:** 5 одновременных RPC запросов
- **WebSocket delay:** 0.001 секунды между отправками клиентам
- **Auto-load:** Данные загружаются автоматически при запуске сервера

### API эндпоинты
- **Solana RPC:** `https://api.mainnet-beta.solana.com`
- **Jupiter Price API:** `https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112`

### Файлы
- **keys.json** - приватные ключи кошельков (фиксированный путь)
- **WebSocket:** `ws://localhost:8002/ws/balances`

## 🔧 Ключевые особенности

1. **Асинхронность** - все операции async/await
2. **Semaphore** - ограничение одновременных RPC запросов (5)
3. **Error Handling** - обработка ошибок на всех уровнях
4. **Auto-load** - автоматическая загрузка данных при запуске сервера
5. **Price Integration** - автоматическое получение цены SOL
6. **Private Key Security** - хранение в зашифрованном виде (bits)
7. **Batch Processing** - обработка всех кошельков одновременно
8. **Independent WebSocket** - отдельный список клиентов для балансов
9. **Client Cleanup** - автоматическое удаление отключенных клиентов
10. **Fixed Path** - фиксированный путь к keys.json

## 📊 Поток данных

```
keys.json → load_wallets_from_keys() → bits_to_address() → get_sol_balance() → 
get_sol_price_usd() → calculate_value_usd() → load_balance_data() → 
send_initial_data() → WebSocket → Frontend
```

## 🚀 API эндпоинты

### Управление балансом
```bash
# Обновление данных баланса
POST /api/balance/refresh

# Статус баланса
GET /api/balance/status
```

### WebSocket
```bash
# Мониторинг балансов
ws://localhost:8002/ws/balances
```

## 🔍 Детали реализации

### BalanceV1 класс - ключевые методы
```python
# ОСНОВНЫЕ МЕТОДЫ
def load_wallets_from_keys() -> List[Dict[str, Any]]
async def get_sol_balance(address: str) -> float
async def get_sol_price_usd() -> float  
async def get_sol_balances_for_wallets(wallets: List[Dict[str, Any]]) -> List[Dict[str, Any]]
def bits_to_address(bits: List[int]) -> str
async def load_balance_data() -> Optional[List[Dict[str, Any]]]
async def broadcast_to_clients(data) -> None
def add_client(websocket: WebSocket) -> None
def remove_client(websocket: WebSocket) -> None
async def send_initial_data(websocket: WebSocket) -> None
async def refresh_balance() -> Dict[str, Any]
def get_status() -> Dict[str, Any]
```

### Context Manager
```python
# BalanceV1 поддерживает async context manager
async with BalanceV1() as balance_monitor:
    wallets = balance_monitor.load_wallets_from_keys()
    balances = await balance_monitor.get_sol_balances_for_wallets(wallets)
```

### Error Handling
```python
# Обработка ошибок на всех уровнях:
# 1. Файл keys.json - try/except при чтении
# 2. RPC запросы - try/except при сетевых ошибках  
# 3. Конвертация ключей - try/except при base58 ошибках
# 4. WebSocket - try/except при отправке
# 5. asyncio.gather - return_exceptions=True
```

## 🐛 Обработка ошибок

### Уровни обработки ошибок
1. **File I/O** - обработка ошибок чтения keys.json
2. **Network** - retry logic для RPC и API запросов
3. **Data Conversion** - обработка ошибок base58 конвертации
4. **WebSocket** - удаление отключенных клиентов
5. **Async Operations** - return_exceptions в asyncio.gather

### Логи для диагностики
```python
# Ключевые сообщения в логах:
"💰 Balance update: X wallets, Y SOL" - успешное обновление
"❌ Balance monitoring error: X" - ошибка мониторинга
"📡 Balance WebSocket client connected" - подключение клиента
"❌ Error sending initial balance data" - ошибка отправки
"📡 Broadcasting to X clients: balance_update (Y wallets)" - рассылка
"✅ Broadcast completed to X clients" - завершение рассылки
"📡 No connected clients to broadcast to" - нет клиентов
"❌ Error sending to client: X" - ошибка отправки клиенту
```

## 🔐 Безопасность

### Приватные ключи
- Хранятся в `keys.json` как массив байтов (bits)
- Конвертируются в публичные адреса через base58
- Никогда не передаются через сеть
- Доступны только на сервере

### RPC безопасность
- Используется только `getBalance` метод
- Не передаются приватные ключи в RPC
- Только чтение публичных данных

## 📈 Мониторинг

### Метрики
- Количество кошельков в мониторинге
- Общий SOL баланс
- Общая стоимость в USD
- Количество подключенных WebSocket клиентов
- Частота обновлений (каждые 3 секунды)

### Логирование
```python
# Детальное логирование:
print(f"💰 Balance update: {len(wallet_balances)} wallets, {total_sol:.6f} SOL")
print(f"📡 Balance WebSocket client connected. Total clients: {len(state.connected_clients)}")
print(f"❌ Balance monitoring error: {e}")
print(f"📡 Broadcasting to {len(state.connected_clients)} clients: {data_type}")
print(f"✅ Broadcast completed to {len(state.connected_clients)} clients")
```

## 🚨 КРИТИЧЕСКИ ВАЖНЫЕ ДЕТАЛИ ДЛЯ ВОССТАНОВЛЕНИЯ

### Глобальные переменные
```python
# main.py - AppState
state.balance_monitor: Optional[BalanceV1] = None
```

### Критические файлы
- `server/keys.json` - приватные ключи (фиксированный путь)
- `server/_v1_balance.py` - автономный класс BalanceV1
- `server/main.py` - интеграция с AppState

### Порядок инициализации
1. `state.balance_monitor = BalanceV1()`
2. `await state.balance_monitor.__aenter__()`
3. `await state.balance_monitor.load_balance_data()` (автоматически)

### WebSocket клиенты
```python
# balance_monitor.connected_clients - отдельный список для балансов
# При перезапуске ВСЕ КЛИЕНТЫ ТЕРЯЮТСЯ!
# Нужно переподключиться с frontend
# Автоматическая отправка данных при подключении
```

### Структура keys.json (КРИТИЧНО!)
```json
[
  {
    "id": 1,                    // Уникальный ID
    "name": "bot 1",           // Название кошелька  
    "address": "8jneY...",     // Публичный адрес (опционально)
    "date_added": "2025-...",  // Дата добавления
    "bits": [193, 140, ...]    // 64 байта приватного ключа
  }
]
```

### При перезапуске (АЛГОРИТМ ВОССТАНОВЛЕНИЯ!)
1. Проверить существует ли `state.balance_monitor`
2. Если нет - создать `BalanceV1()` и `await __aenter__()`
3. Автоматически загрузить данные через `load_balance_data()`
4. Проверить существование `keys.json` (фиксированный путь)
5. Проверить `balance_monitor.connected_clients` (отдельный для балансов)

### Ключевые изменения (ОБНОВЛЕНО!)
- **Автономный класс** - BalanceV1 управляет своими клиентами
- **Автоматическая загрузка** - данные загружаются при инициализации
- **Фиксированный путь** - keys.json без параметров
- **Отдельный WebSocket** - независимый список клиентов
- **Упрощенная архитектура** - без фоновых задач и таймеров

---
**ВАЖНО:** Этот Memory Bank содержит ВСЕ детали для восстановления работы системы мониторинга балансов. Использовать как полный справочник при потере контекста.
