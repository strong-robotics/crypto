# 🚨 HONEYPOT CHECK - ІНСТРУКЦІЯ КОРИСТУВАЧА

## 📋 ЩО НОВОГО

### ✅ Оптимізований Honeypot Check
- **Метод 1:** Jupiter Quote API (основний, найточніший)
- **Метод 2:** Solana RPC transactions (fallback для молодих токенів)
- **Видалено:** DexScreener (не потрібен для молодих токенів < 5 хвилин)

### ✅ Новий API Endpoint
- **POST** `/api/analyzer/check-honeypot` - Швидка перевірка honeypot
- **GET** `/api/analyzer/check-honeypot/{token}` - Альтернатива для браузера

### ✅ Risk Level System
- `CRITICAL` - Honeypot підтверджено ⛔
- `HIGH` - Дуже молодий токен (< 1 хв) ⚠️
- `MEDIUM` - Молодий токен (< 5 хв) ⚡
- `LOW` - Безпечний ✅
- `UNKNOWN` - Недостатньо даних ❓

---

## 🚀 ВИКОРИСТАННЯ

### 1️⃣ Швидка перевірка через консоль (curl)

```bash
# GET метод (простіший)
curl http://localhost:8002/api/analyzer/check-honeypot/YOUR_TOKEN_ADDRESS | jq '.'

# POST метод (детальніший)
curl -X POST http://localhost:8002/api/analyzer/check-honeypot \
  -H "Content-Type: application/json" \
  -d '{"token_address": "YOUR_TOKEN_ADDRESS"}' | jq '.'
```

### 2️⃣ Швидка перевірка в браузері

Просто відкрийте URL:
```
http://localhost:8002/api/analyzer/check-honeypot/YOUR_TOKEN_ADDRESS
```

### 3️⃣ З коду Python

```python
import requests

def check_honeypot(token_address: str) -> dict:
    url = f"http://localhost:8002/api/analyzer/check-honeypot/{token_address}"
    response = requests.get(url)
    return response.json()

# Використання
result = check_honeypot("8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR")

if result["success"]:
    risk_level = result["risk_level"]
    is_honeypot = result["risk_analysis"]["honeypot_check"]["honeypot"]
    
    if risk_level == "CRITICAL":
        print("⛔ НЕ КУПУЙТЕ! Honeypot підтверджено!")
    elif risk_level == "HIGH":
        print("⚠️ ОБЕРЕЖНО! Дуже молодий токен, немає історії!")
    elif risk_level == "MEDIUM":
        print("⚡ РИЗИК! Молодий токен, мало даних")
    elif risk_level == "LOW":
        print("✅ OK! Токен безпечний")
```

### 4️⃣ З коду JavaScript/TypeScript

```typescript
async function checkHoneypot(tokenAddress: string) {
    const response = await fetch(
        `http://localhost:8002/api/analyzer/check-honeypot/${tokenAddress}`
    );
    const result = await response.json();
    
    if (result.success) {
        console.log(`Risk Level: ${result.risk_level}`);
        console.log(`Is Honeypot: ${result.risk_analysis.honeypot_check.honeypot}`);
        console.log(`Token Age: ${result.risk_analysis.token_age_seconds}s`);
        
        return {
            safe: result.risk_level === "LOW",
            riskLevel: result.risk_level,
            isHoneypot: result.risk_analysis.honeypot_check.honeypot
        };
    }
}

// Використання
const check = await checkHoneypot("8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR");
if (!check.safe) {
    alert("⚠️ Цей токен небезпечний!");
}
```

---

## 📊 ПРИКЛАДИ ВІДПОВІДЕЙ

### ✅ Приклад 1: Безпечний токен (LOW)

```json
{
  "success": true,
  "token_address": "...",
  "analysis_time": "3.45s",
  "risk_level": "LOW",
  "risk_analysis": {
    "honeypot_check": {
      "checked_by": ["jupiter_quote_api"],
      "buy_possible": true,
      "sell_possible": true,
      "honeypot": false,
      "reasons": ["✅ Jupiter: can BUY and SELL - NOT honeypot"]
    },
    "token_age_seconds": 3600,
    "token_created_at": "2025-10-08T23:00:00",
    "is_very_new": false
  }
}
```

**Інтерпретація:**
- ✅ Jupiter підтвердив: можна купувати і продавати
- ✅ Токену 1 година (достатньо історії)
- ✅ **БЕЗПЕЧНО для покупки**

### ⚠️ Приклад 2: Дуже молодий токен (HIGH)

```json
{
  "success": true,
  "token_address": "...",
  "analysis_time": "5.23s",
  "risk_level": "HIGH",
  "risk_analysis": {
    "honeypot_check": {
      "checked_by": ["rpc_recent_txs"],
      "buy_possible": true,
      "sell_possible": false,
      "honeypot": true,
      "reasons": ["⚠️ RPC: found 0 sells, 2 buys - possibly honeypot or very new token"]
    },
    "token_age_seconds": 45,
    "token_created_at": "2025-10-09T01:19:15",
    "is_very_new": true
  }
}
```

**Інтерпретація:**
- ⚠️ Jupiter не встиг проіндексувати (fallback на RPC)
- ⚠️ Токену тільки 45 секунд (дуже молодий)
- ⚠️ Є тільки купівлі, немає продажів
- ⚠️ **ВИСОКИЙ РИЗИК** - може бути honeypot або просто дуже новий

### ⛔ Приклад 3: Honeypot (CRITICAL)

```json
{
  "success": true,
  "token_address": "...",
  "analysis_time": "4.12s",
  "risk_level": "CRITICAL",
  "risk_analysis": {
    "honeypot_check": {
      "checked_by": ["jupiter_quote_api"],
      "buy_possible": true,
      "sell_possible": false,
      "honeypot": true,
      "reasons": ["⚠️ Jupiter: can BUY but CANNOT SELL - HONEYPOT!"]
    },
    "token_age_seconds": 7200,
    "token_created_at": "2025-10-08T23:00:00",
    "is_very_new": false
  }
}
```

**Інтерпретація:**
- ⛔ Jupiter підтвердив: можна купити, але НЕ МОЖНА продати
- ⛔ Токену 2 години (достатньо часу для перевірки)
- ⛔ **HONEYPOT! НЕ КУПУЙТЕ!**

---

## 🔍 ЯК ПРАЦЮЄ ПЕРЕВІРКА

### Метод 1: Jupiter Quote API (основний)

```
1. Намагаємось отримати quote для покупки:
   SOL → Token (amount: 0.01 SOL)

2. Намагаємось отримати quote для продажу:
   Token → SOL (amount: 10,000,000 tokens)

3. Перевіряємо результат:
   ✅ Обидва працюють → NOT honeypot
   ⚠️ Купівля працює, продаж ні → HONEYPOT
   ❌ Обидва не працюють → Немає ліквідності
```

### Метод 2: Solana RPC Transactions (fallback)

```
1. Отримуємо останні 12 транзакцій токена

2. Аналізуємо зміни балансів:
   pre_balance > post_balance → SELL
   post_balance > pre_balance → BUY

3. Підраховуємо:
   ✅ Є продажі → NOT honeypot
   ⚠️ Немає продажів → Можливо honeypot або дуже новий
```

---

## ⚡ ШВИДКІСТЬ РОБОТИ

| Сценарій | Час виконання |
|----------|---------------|
| Jupiter працює | 3-5 секунд |
| Jupiter не працює (fallback RPC) | 6-8 секунд |
| Помилка (все не працює) | 10-12 секунд |

**Чому це швидко:**
- Не робимо повний аналіз (DexScreener, holders, тощо)
- Тільки критичні перевірки
- Мінімум API запитів

---

## 📝 РЕКОМЕНДАЦІЇ

### ✅ БЕЗПЕЧНІ токени (LOW):
- ✅ Jupiter підтвердив купівлю і продаж
- ✅ Токену більше 5 хвилин
- ✅ Є історія транзакцій
- **МОЖНА купувати з обережністю**

### ⚡ РИЗИКОВАНІ токени (MEDIUM):
- ⚠️ Токену 1-5 хвилин
- ⚠️ Мало транзакцій
- **ОЧІКАЙТЕ 5-10 хвилин перед покупкою**

### ⚠️ ДУЖЕ ризиковані (HIGH):
- ⚠️ Токену менше 1 хвилини
- ⚠️ Немає історії продажів
- **ЗАЧЕКАЙТЕ 10-15 хвилин, подивіться чи хтось продасть**

### ⛔ НЕБЕЗПЕЧНІ (CRITICAL):
- ⛔ Підтверджений honeypot
- ⛔ Неможливо продати
- **НЕ КУПУЙТЕ НІ В ЯКОМУ РАЗІ!**

---

## 🧪 ТЕСТУВАННЯ

### Тест 1: Відомий безпечний токен
```bash
# USDC (стейблкоїн, точно безпечний)
curl http://localhost:8002/api/analyzer/check-honeypot/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v | jq '.risk_level'
# Очікуємо: "LOW"
```

### Тест 2: Новий токен (вставте свій)
```bash
curl http://localhost:8002/api/analyzer/check-honeypot/YOUR_NEW_TOKEN | jq '.'
```

### Тест 3: POST метод
```bash
curl -X POST http://localhost:8002/api/analyzer/check-honeypot \
  -H "Content-Type: application/json" \
  -d '{"token_address": "YOUR_TOKEN"}' | jq '.'
```

---

## 🎯 ПОРІВНЯННЯ З ПОВНИМ АНАЛІЗОМ

| Параметр | Honeypot Check | Повний аналіз |
|----------|---------------|---------------|
| **Час виконання** | 3-8 сек | 15-30 сек |
| **API запитів** | 2-3 | 10-15 |
| **Що перевіряє** | Honeypot + вік | Все (DEX, RPC, Jupiter, Security) |
| **Збереження в БД** | ❌ Ні | ✅ Так |
| **Коли використовувати** | Перед купівлею | Для детального аналізу |

---

## 🚀 ENDPOINTS В ПРОЕКТІ

```
POST /api/analyzer/check-honeypot          # Новий! Швидка перевірка
GET  /api/analyzer/check-honeypot/{token}  # Новий! GET альтернатива

POST /api/analyzer/test-single             # Простий повний аналіз
POST /api/analyzer/test-detailed           # Детальний повний аналіз
GET  /api/analyzer/token/{token}           # Читання з БД
GET  /api/analyzer/db-stats                # Статистика БД
```

---

## 💡 ПОРАДИ

1. **Для нових токенів (< 5 хв):**
   - Спочатку `/check-honeypot` (швидко)
   - Зачекайте 5-10 хвилин
   - Потім `/test-detailed` (повний аналіз)

2. **Для старих токенів (> 1 година):**
   - Одразу `/test-detailed` (повний аналіз)
   - Honeypot check включений в повний аналіз

3. **Для trading бота:**
   - Перевіряйте ВСІ токени через `/check-honeypot`
   - Блокуйте CRITICAL та HIGH
   - MEDIUM - чекайте 5 хвилин
   - LOW - можна торгувати

---

## 📞 ПІДТРИМКА

Якщо є питання або проблеми:
1. Перевірте логи сервера (консоль де запущений `python main.py`)
2. Перевірте чи працює Jupiter API: https://quote-api.jup.ag/v6/quote
3. Спробуйте з іншим токеном для порівняння

**Автор:** AI Assistant  
**Дата:** 2025-10-09  
**Версія:** 1.0

