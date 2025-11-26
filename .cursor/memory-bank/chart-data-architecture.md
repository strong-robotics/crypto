# Chart Data Architecture - Writer/Reader Pattern

## **Концепція**

Backend і Frontend працюють **НЕЗАЛЕЖНО** через базу даних:
- **Backend Writer** збирає trades з Helius API → пише в БД
- **Frontend Reader** читає trades з БД → генерує графіки → WebSocket
- Два паралельних цикли по 1 секунді кожен
- НЕ чекають один одного, працюють незалежно

---

## **1. Backend Writer: `_v2_helius_trades_scanner.py`**

### **Призначення**
Збирати trades з Helius API для ВСІХ токенів і зберігати в БД.

### **Логіка роботи**
- **Цикл:** Кожну 1 секунду
- **Джерело:** Читає ВСІ токени з `token_ids` (молоді токени можуть "вибухнути" через 20 секунд)
- **Дія:** Для кожного токена викликає `helius_reporter.get_trades(token_address)`
- **Збереження:** `INSERT OR IGNORE` в БД (дублікати по `signature` пропускаються)
- **Незалежність:** НЕ чекає Frontend, НЕ відправляє дані, просто пише в БД

### **Ключові методи**
```python
class HeliusTradesScanner:
    def __init__(self, helius_api_key: str, db_path: str, debug: bool = False)
    
    async def get_all_tokens_for_scanning(self) -> List[Dict]
        # Читає всі токени з token_ids
    
    async def scan_token_trades(self, token_address: str)
        # Викликає helius_reporter.get_trades()
        # Зберігає в БД через helius_reporter.save_trades_to_db()
    
    async def _auto_scan_loop(self)
        # Головний цикл (кожну 1 сек)
        # Для кожного токена → scan_token_trades()
    
    def start_scanning(self)
    def stop_scanning(self)
    def get_status(self) -> Dict
```

### **Особливості**
- Збирає **ВСЮ** історичну торговлю для майбутньої AI моделі
- Helius API може повертати 50 trades, з яких 42 дублікати → збережуться тільки 8 нових
- Працює швидко, не блокується Frontend
- Якщо Frontend не працює → Backend все одно збирає дані

---

## **2. Frontend Reader: `_v2_chart_data_reader.py`**

### **Призначення**
Читати trades з БД, генерувати `chart_data` і відправляти на Frontend через WebSocket.

### **Логіка роботи**
- **Цикл:** Кожну 1 секунду (паралельно з Writer)
- **Джерело:** Читає ВСІ токени з `token_ids`
- **Для кожного токена:**
  1. Читає trades з БД (останні 450 секунд)
  2. Групує по секундах (`timestamp` → секунда)
  3. Вираховує середню ціну (`amount_usd`) за кожну секунду
  4. Якщо в секунду немає trades → повторює попередню ціну
  5. Генерує `chart_data: number[]` (450 точок)
  6. Broadcast через WebSocket `/ws/chart-data` → Frontend

### **Ключові методи**
```python
class ChartDataReader:
    def __init__(self, db_path: str, debug: bool = False)
    
    async def get_all_tokens(self) -> List[Dict]
        # Читає всі токени з token_ids
    
    async def get_trades_from_db(self, token_id: int, start_time: int, end_time: int) -> List[Dict]
        # SELECT * FROM trades WHERE token_id = ? AND timestamp BETWEEN ? AND ?
    
    async def generate_chart_data(self, token_address: str, last_seconds: int = 450) -> List[float]
        # Головна логіка генерації chart_data
    
    async def broadcast_to_clients(self, data: Dict)
        # Відправляє через WebSocket
    
    async def _auto_refresh_loop(self)
        # Головний цикл (кожну 1 сек)
    
    async def add_client(self, websocket: WebSocket)
    async def remove_client(self, websocket: WebSocket)
    
    def start_auto_refresh(self)
    def stop_auto_refresh(self)
    def get_status(self) -> Dict
```

### **Алгоритм `generate_chart_data(token_address, last_seconds=450)`**

```python
1. Отримати token_id з token_address:
   SELECT id FROM token_ids WHERE token_address = ?

2. Читати trades з БД (останні 450 секунд):
   now = int(time.time())
   start_time = now - 450
   trades = SELECT * FROM trades WHERE token_id = ? AND timestamp >= ?

3. Групувати по секундах:
   trades_by_second = {}
   for trade in trades:
       second = trade['timestamp']
       if second not in trades_by_second:
           trades_by_second[second] = []
       trades_by_second[second].append(float(trade['amount_usd']))

4. Генерувати chart_data (450 ітерацій):
   chart_data = []
   prev_price = None
   
   for second in range(start_time, now + 1):
       if second in trades_by_second:
           # Є trades → середня ціна
           avg_price = sum(trades_by_second[second]) / len(trades_by_second[second])
           chart_data.append(round(avg_price, 2))
           prev_price = avg_price
       else:
           # Немає trades → попередня ціна
           if prev_price is not None:
               chart_data.append(prev_price)
           else:
               chart_data.append(None)  # Або 0

5. Повернути chart_data:
   return [45.5, 45.5, 46.0, 46.0, 47.2, ...]  # 450 елементів
```

---

## **3. Database (trades table)**

### **Структура таблиці**
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL,
    signature TEXT UNIQUE NOT NULL,       -- Унікальний хеш транзакції
    timestamp INTEGER NOT NULL,           -- Unix timestamp
    readable_time TEXT NOT NULL,          -- "2025-10-10 12:34:56"
    direction TEXT NOT NULL,              -- "buy" | "sell" | "withdraw"
    amount_tokens NUMERIC NOT NULL,       -- Кількість токенів
    amount_sol TEXT NOT NULL,             -- Формат: "0.00432753"
    amount_usd TEXT NOT NULL,             -- Формат: "0.98"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_id) REFERENCES token_ids(id)
);
```

### **Індекси**
```sql
CREATE INDEX idx_trades_token_id ON trades(token_id);
CREATE INDEX idx_trades_signature ON trades(signature);
CREATE INDEX idx_trades_timestamp ON trades(timestamp);
CREATE INDEX idx_trades_direction ON trades(direction);
```

### **Захист від дублікатів**
- **SQL constraint:** `signature TEXT UNIQUE`
- **Insert strategy:** `INSERT OR IGNORE INTO trades ...`
- **Логіка:** Якщо `signature` вже є в БД → пропускає транзакцію

### **Приклад даних**
```
| id | token_id | signature | timestamp  | amount_usd | direction |
|----|----------|-----------|------------|------------|-----------|
| 1  | 42       | sig1...   | 1728560400 | "45.20"    | "buy"     |
| 2  | 42       | sig2...   | 1728560400 | "45.80"    | "buy"     |
| 3  | 42       | sig3...   | 1728560401 | "46.10"    | "sell"    |
```

---

## **4. Frontend Integration (page.tsx)**

### **WebSocket підключення**
```typescript
const wsChartRef = useRef<WebSocket | null>(null);

const connectChartWebSocket = () => {
  wsChartRef.current = new WebSocket(`ws://localhost:8002/ws/chart-data`);
  
  wsChartRef.current.onopen = () => {
    console.log("🔗 Chart WebSocket connected");
    setWsChartConnected(true);
  };
  
  wsChartRef.current.onmessage = (event) => {
    const data = JSON.parse(event.data);
    // data = { token_id: "ABC...", chart_data: [45.2, 46.1, ...] }
    
    setTokens(prevTokens => 
      prevTokens.map(token => 
        token.tokenId === data.token_id 
          ? { ...token, chartData: data.chart_data }
          : token
      )
    );
  };
  
  wsChartRef.current.onclose = () => {
    console.log("🔌 Chart WebSocket disconnected");
    setWsChartConnected(false);
  };
};

useEffect(() => {
  connectChartWebSocket();
  return () => {
    if (wsChartRef.current) {
      wsChartRef.current.close();
    }
  };
}, []);
```

### **Формат даних `chart_data`**
```typescript
// Формат отримуваних даних
{
  "token_id": "ABC123...",
  "chart_data": [45.2, 45.2, 46.1, 46.1, 46.1, 47.3, ...]  // 450 чисел
}

// tokens state
const [tokens, setTokens] = useState([
  {
    tokenId: "ABC123...",
    chartData: [45.2, 45.2, 46.1, ...],  // Масив цін по секундах
    // ... інші поля
  }
]);
```

### **Area Chart інтеграція**
```typescript
// В TokenCell компоненті
<AreaChartComponent
  timer={100000}
  width={500}
  height={110}
  chartData={token.chartData}  // [45.2, 46.1, 46.1, ...]
/>
```

**Як працює графік:**
- **Вісь X:** `time: index` (0, 1, 2, ..., 449)
- **Вісь Y:** `value: price` (в USD)
- **Ширина:** `externalChartData.length` пікселів (1 секунда = 1 піксель)
- **Y-axis domain:** Автоматично: `[minValue - 10%, maxValue + 10%]`

---

## **5. Main.py Integration**

### **Ініціалізація модулів**
```python
from _v2_helius_trades_scanner import HeliusTradesScanner
from _v2_chart_data_reader import ChartDataReader
from config import config

# Global instances
helius_scanner: Optional[HeliusTradesScanner] = None
chart_reader: Optional[ChartDataReader] = None

def get_helius_scanner():
    global helius_scanner
    if helius_scanner is None:
        helius_scanner = HeliusTradesScanner(
            helius_api_key=config.HELIUS_API_KEY,
            db_path="db/tokens.db",
            debug=True
        )
    return helius_scanner

def get_chart_reader():
    global chart_reader
    if chart_reader is None:
        chart_reader = ChartDataReader(
            db_path="db/tokens.db",
            debug=True
        )
    return chart_reader
```

### **Startup event**
```python
@app.on_event("startup")
async def startup_event():
    # Запускаємо обидва модулі
    scanner = get_helius_scanner()
    scanner.start_scanning()
    
    reader = get_chart_reader()
    reader.start_auto_refresh()
    
    print("✅ Helius Scanner started")
    print("✅ Chart Data Reader started")
```

### **WebSocket endpoint**
```python
@app.websocket("/ws/chart-data")
async def chart_data_websocket(websocket: WebSocket):
    await websocket.accept()
    reader = get_chart_reader()
    await reader.add_client(websocket)
    
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        await reader.remove_client(websocket)
```

### **API Endpoints**

#### **Helius Scanner (Writer)**
```python
@app.post("/api/helius-scanner/start")
async def start_helius_scanner():
    scanner = get_helius_scanner()
    scanner.start_scanning()
    return {"success": True, "message": "Helius scanner started"}

@app.post("/api/helius-scanner/stop")
async def stop_helius_scanner():
    scanner = get_helius_scanner()
    scanner.stop_scanning()
    return {"success": True, "message": "Helius scanner stopped"}

@app.get("/api/helius-scanner/status")
async def helius_scanner_status():
    scanner = get_helius_scanner()
    return scanner.get_status()
```

#### **Chart Reader**
```python
@app.post("/api/chart-reader/start")
async def start_chart_reader():
    reader = get_chart_reader()
    reader.start_auto_refresh()
    return {"success": True, "message": "Chart reader started"}

@app.post("/api/chart-reader/stop")
async def stop_chart_reader():
    reader = get_chart_reader()
    reader.stop_auto_refresh()
    return {"success": True, "message": "Chart reader stopped"}

@app.get("/api/chart-reader/status")
async def chart_reader_status():
    reader = get_chart_reader()
    return reader.get_status()
```

---

## **6. Переваги архітектури Writer/Reader**

### **Незалежність**
- ✅ Backend Writer не чекає Frontend
- ✅ Frontend Reader не чекає Backend
- ✅ Якщо Frontend лагає → Backend все одно збирає дані
- ✅ Якщо Backend тимчасово недоступний → Frontend показує останні дані з БД

### **Паралельність**
- ✅ Два цикли по 1 секунді працюють одночасно
- ✅ Не блокують один одного
- ✅ Можна масштабувати окремо (Writer на сервері, Reader локально)

### **AI-готовність**
- ✅ Backend може збирати дані навіть без Frontend
- ✅ ВСЯ історія зберігається в БД для ML моделі
- ✅ AI модель може працювати на Backend паралельно

### **Масштабованість**
- ✅ Backend може обробляти 1000+ токенів
- ✅ Frontend показує тільки потрібні токени
- ✅ Reader може відправляти дані тільки для видимих токенів (оптимізація)

### **Продуктивність**
- ✅ БД як кеш між Writer і Reader
- ✅ Індекси забезпечують швидкі запити
- ✅ `INSERT OR IGNORE` швидко фільтрує дублікати

---

## **7. Приклад роботи системи**

### **Секунда 0**
```
Writer (Backend):
  ├─ Fetch Helius API для токена ABC123
  ├─ Отримано 50 trades
  └─ Save to DB: INSERT OR IGNORE → 50 нових записів

Reader (Frontend):
  ├─ Read DB: SELECT trades WHERE token_id = ABC123 AND timestamp >= now-450
  ├─ Found 50 trades
  ├─ Group by second: {1728560350: [45.2, 45.8], 1728560351: [46.1], ...}
  ├─ Generate chart_data: [45.5, 46.1, 46.1, ...]
  └─ Broadcast to Frontend: {"token_id": "ABC123", "chart_data": [...]}

Frontend:
  └─ Update UI: tokens[0].chartData = [45.5, 46.1, ...]
```

### **Секунда 1**
```
Writer (Backend):
  ├─ Fetch Helius API для токена ABC123
  ├─ Отримано 48 trades (42 дублікати + 6 нових)
  └─ Save to DB: INSERT OR IGNORE → 6 нових записів (42 пропущено)

Reader (Frontend):
  ├─ Read DB: 56 trades total (50 + 6 нових)
  ├─ Generate chart_data: [45.5, 46.1, 46.1, 46.8, ...]
  └─ Broadcast to Frontend: {"token_id": "ABC123", "chart_data": [...]}

Frontend:
  └─ Update UI: tokens[0].chartData = [45.5, 46.1, 46.1, 46.8, ...]
```

### **Секунда 2**
```
Writer (Backend):
  ├─ Fetch Helius API для токена ABC123
  ├─ Отримано 55 trades (50 дублікатів + 5 нових)
  └─ Save to DB: 5 нових записів → Total: 61 trades

Reader (Frontend):
  ├─ Read DB: 61 trades
  ├─ Generate chart_data: [45.5, 46.1, 46.1, 46.8, 47.2, ...]
  └─ Broadcast: chart_data (450 точок)

Frontend:
  └─ Update UI: график оновлено
```

### **Результат на Frontend**
- Графік оновлюється кожну секунду
- Відображає останні 450 секунд торговлі
- Середня ціна по секундах (5 trades в секунду → 1 avg price на графіку)
- Плавна лінія без пропусків (missing seconds = prev_price)

---

## **8. Обробка edge cases**

### **Немає trades в секунду**
```python
# Якщо в секунду 5 немає trades:
chart_data = [45.5, 46.1, 46.8, 47.2, 47.2, 47.2, 48.0, ...]
#                                       ↑     ↑
#                           Секунди 4-6: повторюємо 47.2
```

### **Перший trade токена**
```python
# Якщо токен новий і ще немає історії:
chart_data = [None, None, None, ..., 45.5, 45.5, 46.1, ...]
#             ↑ 440 секунд без даних     ↑ Перший trade
```

### **Більше 1 trade в секунду**
```python
# Секунда 10: trades = [45.2, 45.8, 46.1, 44.9]
avg_price = (45.2 + 45.8 + 46.1 + 44.9) / 4 = 45.5
chart_data[10] = 45.5
```

### **Дублікати від Helius**
```python
# Helius повернув:
trades = [
  {"signature": "sig1", ...},  # Новий
  {"signature": "sig2", ...},  # Дублікат (вже в БД)
  {"signature": "sig3", ...},  # Новий
]

# INSERT OR IGNORE:
✅ sig1 → inserted
⏭️  sig2 → ignored (duplicate)
✅ sig3 → inserted
```

---

## **9. Майбутнє розширення (AI Model)**

### **Збір даних для ML**
```python
# Backend Writer збирає ВСЮ історію:
- Timestamp кожного trade
- Buy/Sell direction
- Volume (amount_tokens)
- Price (amount_usd)
- Token age (created_at з token_ids)
```

### **Features для AI моделі**
```python
1. Volume per second: sum(amount_tokens) за секунду
2. Price volatility: std_dev(prices) за хвилину
3. Buy/Sell ratio: count(buy) / count(sell)
4. Price momentum: (current_price - price_5min_ago) / price_5min_ago
5. Time since token creation: now - token.created_at
```

### **AI модель на Backend**
```python
# Паралельно з Writer і Reader:
class AIPredictor:
    async def predict_token_success(self, token_id):
        # Читає trades з БД
        # Вираховує features
        # ML model prediction
        # Return: probability of success
```

### **Frontend отримує AI predictions**
```typescript
// Додатковий WebSocket endpoint
wsAIRef.current.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // data = { token_id: "ABC", success_probability: 0.85 }
  
  setTokens(prev => prev.map(token => 
    token.tokenId === data.token_id 
      ? { ...token, aiScore: data.success_probability }
      : token
  ));
};
```

---

## **10. Оптимізації (майбутнє)**

### **Batch processing**
```python
# Замість:
for token in tokens:
    await scan_token_trades(token)

# Використати:
await asyncio.gather(*[scan_token_trades(t) for t in tokens])
```

### **Кешування chart_data**
```python
# Якщо trades не змінилися → не генерувати знову
last_trade_count = {}

if last_trade_count[token_id] == current_trade_count:
    # Використати кеш
    chart_data = cache[token_id]
else:
    # Регенерувати
    chart_data = generate_chart_data(token_id)
```

### **WebSocket оптимізація**
```python
# Відправляти тільки зміни (delta)
{
  "token_id": "ABC",
  "new_point": 47.5,  # Тільки нова точка
  "timestamp": 1728560450
}

# Frontend додає до масиву:
chartData = [...prevData, 47.5].slice(-450)
```

---

## **Підсумок**

### **Архітектура**
- **2 модулі:** Writer (Helius Scanner) + Reader (Chart Data)
- **1 таблиця:** `trades` з індексами та UNIQUE constraint
- **2 цикли:** По 1 секунді кожен, паралельно
- **450 секунд:** Історія для графіків
- **∞ історії:** Вся торговля для AI

### **Переваги**
- ✅ Незалежність Backend ↔ Frontend
- ✅ Паралельність Writer ↔ Reader
- ✅ Швидкість (БД як кеш)
- ✅ Масштабованість (1000+ токенів)
- ✅ AI-готовність (історичні дані)

### **Файли для створення**
1. `server/_v2_helius_trades_scanner.py` - Writer
2. `server/_v2_chart_data_reader.py` - Reader
3. `server/main.py` - Integration (WebSocket + API)
4. `src/app/page.tsx` - Frontend WebSocket connection

**Готово до імплементації!** 🚀

