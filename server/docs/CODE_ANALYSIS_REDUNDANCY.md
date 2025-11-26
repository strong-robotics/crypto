# Аналіз зайвого коду в `_v2_buy_sell.py`

**Файл:** `server/_v2_buy_sell.py`  
**Розмір:** 1406 рядків  
**Дата аналізу:** 2024

---

## 🔍 ЗНАЙДЕНО ЗАЙВОГО КОДУ

### 1. ❌ ДУБЛІКАТ: `import random` (2 місця)

**Проблема:**
- Рядок 9: `import random` (глобальний імпорт)
- Рядок 311: `import random` (всередині `execute_buy()`)

**Рішення:**
- Видалити рядок 311 (локальний імпорт не потрібен)

**Економія:** 1 рядок

---

### 2. ❌ ДУБЛІКАТ: `import aiohttp` (2 місця)

**Проблема:**
- Рядок 12: `import aiohttp` (глобальний імпорт)
- Рядок 896: `import aiohttp` (всередині `sell_real()`)

**Рішення:**
- Видалити рядок 896 (локальний імпорт не потрібен)

**Економія:** 1 рядок

---

### 3. ❌ ДУБЛІКАТ: Sign and Send Transaction (~56 рядків)

**Проблема:**
Ідентичний код в `execute_buy()` та `execute_sell()`:

**`execute_buy()` (рядки 487-542):**
```python
# Sign and send transaction через sender endpoint
tx_bytes = base64.b64decode(swap["swapTransaction"])
vtx = VersionedTransaction.from_bytes(tx_bytes)
vtx = VersionedTransaction(vtx.message, [keypair])

signed_tx = base64.b64encode(bytes(vtx)).decode()
payload = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "sendTransaction",
    "params": [signed_tx, {"encoding": "base64", "preflightCommitment": "confirmed", "skipPreflight": False}]
}

# Use sender endpoint for transaction submission
async with session.post(sender_endpoint, json=payload, timeout=RPC_TIMEOUT) as resp:
    if resp.status != 200:
        text = await resp.text()
        if slippage_bps == slippage_levels[-1]:  # Last attempt
            return {"success": False, "message": f"Transaction HTTP error {resp.status}: {text[:200]}"}
        continue  # Try next slippage level
    try:
        res = await resp.json(content_type=None)
    except Exception as e:
        text = await resp.text()
        if slippage_bps == slippage_levels[-1]:  # Last attempt
            return {"success": False, "message": f"Transaction JSON parse error: {str(e)}, response: {text[:200]}"}
        continue  # Try next slippage level
```

**`execute_sell()` (рядки 706-766):**
- Той самий код (56 рядків)

**Рішення:**
Винести в окрему функцію:
```python
async def _sign_and_send_transaction(
    session: aiohttp.ClientSession,
    swap: dict,
    keypair: Keypair,
    sender_endpoint: str,
    slippage_bps: int,
    slippage_levels: list
) -> Dict:
    """Sign and send transaction with error handling"""
    # ... код ...
```

**Економія:** ~50 рядків

---

### 4. ❌ ДУБЛІКАТ: Slippage Error Detection (~19 рядків)

**Проблема:**
Ідентичний код в `execute_buy()` та `execute_sell()`:

**`execute_buy()` (рядки 515-533):**
```python
if "error" in res:
    error_code = res.get("error", {}).get("data", {}).get("err", {})
    # Check if error is slippage-related (0x1771 = 6001 = slippage tolerance exceeded)
    is_slippage_error = (
        "0x1771" in str(res.get("error", {})) or
        "6001" in str(res.get("error", {})) or
        "slippage" in str(res.get("error", {})).lower() or
        "InstructionError" in str(error_code)
    )
    
    if is_slippage_error and slippage_bps < slippage_levels[-1]:
        # Slippage error and not last attempt - retry with higher slippage
        # Note: Rate limiting already handles delays between requests (1 request per second)
        continue
    elif slippage_bps == slippage_levels[-1]:  # Last attempt
        return {"success": False, "message": f"Transaction error: {res['error']}"}
    else:
        # Non-slippage error - don't retry
        return {"success": False, "message": f"Transaction error: {res['error']}"}
```

**`execute_sell()` (рядки 734-752):**
- Той самий код (19 рядків)

**Рішення:**
Винести в окрему функцію:
```python
def _is_slippage_error(res: dict) -> bool:
    """Check if error is slippage-related"""
    error_code = res.get("error", {}).get("data", {}).get("err", {})
    return (
        "0x1771" in str(res.get("error", {})) or
        "6001" in str(res.get("error", {})) or
        "slippage" in str(res.get("error", {})).lower() or
        "InstructionError" in str(error_code)
    )
```

**Економія:** ~15 рядків

---

### 5. ❌ ДУБЛІКАТ: Завантаження Keypair (~21 рядок)

**Проблема:**
Код завантаження keypair дублюється:

**`sell_real()` (рядки 872-892):**
```python
# 4. Load keypair
try:
    with open(config.WALLET_KEYS_FILE) as f:
        keys = json.load(f)
    wallet_key = None
    for k in keys:
        if k.get("id") == key_id:
            wallet_key = k
            break
    if not wallet_key:
        print(f"[sell_real] ❌ Wallet key_id={key_id} not found in keys.json")
        await _log("failed", f"Wallet key_id={key_id} not found", wallet_id)
        return {"success": False, "message": f"Wallet key_id={key_id} not found"}
    keypair = Keypair.from_bytes(bytes(wallet_key["bits"]))
    print(f"[sell_real] ✅ Keypair loaded: {keypair.pubkey()}")
except Exception as e:
    print(f"[sell_real] ❌ Failed to load keypair: {e}")
    import traceback
    traceback.print_exc()
    await _log("failed", f"Failed to load keypair: {str(e)}", wallet_id)
    return {"success": False, "message": f"Failed to load keypair: {str(e)}"}
```

**Примітка:** `buy_real()` використовує `get_free_wallet()`, який вже повертає keypair, тому тут дублікату немає. Але логіка завантаження keypair може бути винесена в окрему функцію для майбутнього використання.

**Рішення:**
Винести в окрему функцію:
```python
async def _load_keypair_by_id(key_id: int) -> Optional[Keypair]:
    """Load keypair from keys.json by wallet ID"""
    try:
        with open(config.WALLET_KEYS_FILE) as f:
            keys = json.load(f)
        for k in keys:
            if k.get("id") == key_id:
                return Keypair.from_bytes(bytes(k["bits"]))
        return None
    except Exception as e:
        return None
```

**Економія:** ~15 рядків (якщо використовувати в обох місцях)

---

### 6. ❌ ЗАЙВИЙ КОМЕНТАР: Неправильний номер рядка

**Проблема:**
Рядок 1339: `# Note: wallet_id is already set atomically above (line 1570-1577)`

**Проблема:**
- Номери рядків 1570-1577 не існують (файл має 1406 рядків)
- Правильні рядки: 1224-1232 (ATOMIC RESERVATION)

**Рішення:**
Виправити коментар або видалити:
```python
# Note: wallet_id is already set atomically above (lines 1224-1232) to prevent race conditions
```

**Економія:** 0 рядків (тільки виправлення)

---

### 7. ⚠️ ЗАЙВА ЗМІННА: `RPC = HELIUS_RPC`

**Проблема:**
Рядок 34: `RPC = HELIUS_RPC` (backward compatibility)

**Використання:**
- Рядок 93: `get_wallet_balance_sol()` - використовує `RPC`
- Рядок 123: `get_token_balance()` - використовує `RPC`

**Рішення:**
Замінити `RPC` на `HELIUS_RPC` напряму в обох функціях

**Економія:** 1 рядок

---

### 8. ⚠️ БАГАТО PRINT STATEMENTS (40 разів)

**Проблема:**
Файл містить 40 `print()` statements для debug логів

**Приклади:**
- `print(f"[sell_real] 🎯 sell_real called for token {token_id}, source={source}")`
- `print(f"[buy_real] ❌ Force buy failed for token {token_id}: {error_message}")`
- `print(f"[get_free_wallet] ✅ Selected wallet (round-robin): id={check_id}")`

**Рішення:**
Використати `logging` модуль замість `print()`:
```python
import logging
logger = logging.getLogger(__name__)
logger.info(f"[sell_real] 🎯 sell_real called for token {token_id}, source={source}")
```

**Переваги:**
- Можна контролювати рівень логування
- Можна вимкнути debug логи в production
- Краща структура логів

**Економія:** 0 рядків (але краща архітектура)

---

### 9. ❌ ДУБЛІКАТ: HTTP Error Handling (~12 рядків)

**Проблема:**
Ідентичний код обробки HTTP помилок в `execute_buy()` та `execute_sell()`:

**`execute_buy()` (рядки 502-513):**
```python
if resp.status != 200:
    text = await resp.text()
    if slippage_bps == slippage_levels[-1]:  # Last attempt
        return {"success": False, "message": f"Transaction HTTP error {resp.status}: {text[:200]}"}
    continue  # Try next slippage level
try:
    res = await resp.json(content_type=None)
except Exception as e:
    text = await resp.text()
    if slippage_bps == slippage_levels[-1]:  # Last attempt
        return {"success": False, "message": f"Transaction JSON parse error: {str(e)}, response: {text[:200]}"}
    continue  # Try next slippage level
```

**`execute_sell()` (рядки 721-732):**
- Той самий код (12 рядків)

**Рішення:**
Вже включено в `_sign_and_send_transaction()` (пункт 3)

**Економія:** Включено в пункт 3

---

### 10. ⚠️ ЗАЙВІ КОМЕНТАРІ: Детальні описи в docstrings

**Проблема:**
Деякі docstrings дуже детальні і повторюють те, що вже зрозуміло з коду:

**Приклад (рядки 1135-1154):**
```python
"""REAL TRADING: Купити токени з реальним кошельком та блокчейн транзакцією.

Логіка:
1. Отримати вільний реальний кошелек з keys.json
2. Викликати execute_buy, який:
   a. Перевіряє honeypot через симуляцію продажу (test sell simulation)
   b. Якщо honeypot check пройшов - виконує реальну покупку
3. Записати в wallet_history з деталями транзакції
4. Прив'язати кошелек до токена (wallet_id)

NOTE: This function is used by both auto-buy (via analyzer) and force-buy (manual).
It does NOT check patterns - pattern checks are done in analyzer before calling auto-buy.
Force buy bypasses all pattern checks and calls this function directly.

IMPORTANT: Honeypot check is ALWAYS performed (even for force buy) to protect against scams.
Honeypot check simulates a small sell transaction - if it fails, token is blocked.

Returns:
    dict with success, token_id, wallet_id, amount_tokens, price_usd
"""
```

**Рішення:**
Спростити docstrings, залишити тільки ключову інформацію

**Економія:** ~5-10 рядків

---

## 📊 ПІДСУМОК

### Знайдено проблем:

1. ✅ **Дублікат `import random`** - 1 рядок
2. ✅ **Дублікат `import aiohttp`** - 1 рядок
3. ✅ **Дублікат Sign and Send Transaction** - ~50 рядків
4. ✅ **Дублікат Slippage Error Detection** - ~15 рядків
5. ✅ **Дублікат завантаження Keypair** - ~15 рядків (потенційно)
6. ✅ **Неправильний коментар** - виправлення
7. ✅ **Зайва змінна `RPC`** - 1 рядок
8. ⚠️ **40 print statements** - замінити на logging
9. ✅ **Дублікат HTTP Error Handling** - включено в пункт 3
10. ⚠️ **Зайві детальні docstrings** - ~5-10 рядків

### Потенційна економія:

- **Мінімум:** ~83 рядки (видалення дублікатів)
- **Максимум:** ~100 рядків (з рефакторингом)

### Рекомендації:

1. **Пріоритет 1 (швидко):**
   - Видалити дублікати імпортів (рядки 311, 896)
   - Виправити коментар (рядок 1339)
   - Замінити `RPC` на `HELIUS_RPC` (рядки 93, 123)

2. **Пріоритет 2 (рефакторинг):**
   - Винести `_sign_and_send_transaction()` (~50 рядків)
   - Винести `_is_slippage_error()` (~15 рядків)
   - Винести `_load_keypair_by_id()` (~15 рядків)

3. **Пріоритет 3 (опціонально):**
   - Замінити `print()` на `logging`
   - Спростити docstrings

---

## 🎯 ВИСНОВОК

Файл містить **~85-100 рядків зайвого коду** через дублікати між `execute_buy()` та `execute_sell()`. Основна проблема - ідентична логіка підпису та відправки транзакцій, яку можна винести в окремі функції.

**Рекомендація:** Почати з пріоритету 1 (швидкі виправлення), потім перейти до пріоритету 2 (рефакторинг).

