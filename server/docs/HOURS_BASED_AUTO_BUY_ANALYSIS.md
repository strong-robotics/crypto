# Аналіз: Перехід на часові мірки для автопокупки та AI моделей

## Поточна ситуація

### Як працює зараз:

**Автопокупка:**
- Використовує `iterations_count` (кількість секунд з `usd_price > 0`)
- `AUTO_BUY_ENTRY_SEC = 950` секунд
- Перевірка: `iterations >= 950`
- **Проблема:** Якщо токен має пропуски в даних, на реальній 1000-й секунді може бути тільки 600 ітерацій → автопокупка не спрацює

**AI моделі:**
- Pattern segments: перевірка `iterations_count >= segment_end` (0-35, 35-85, 85-125)
- ETA модель: використовує `iterations` для віку токена
- Моделі навчені на повних даних (кожна секунда має ціну)

**live_seconds:**
- Вже розраховується в `save_token_data()` (рядок 845-847)
- Використовується для zero tail detection з fill ratio
- **НЕ використовується** для автопокупки

---

## Можливість переходу на часові мірки

### ✅ Можна зробити:

#### 1. **Автопокупка за реальним часом (1 година 20 хвилин = 4800 секунд)**

**Поточна логіка:**
```python
# _v3_analyzer_jupiter.py:1304
iterations = await get_token_iterations_count(conn, token_id)
entry_gate_iter = self.entry_sec  # 950
if iterations >= entry_gate_iter:
    # Перевірка AI та автопокупка
```

**Нова логіка (за реальним часом):**
```python
# Використовувати live_seconds замість iterations
live_seconds = calculate_live_seconds(token_id)  # Вже розраховується!
entry_gate_sec = 4800  # 1 година 20 хвилин

if live_seconds >= entry_gate_sec:
    # Перевірка достатності даних для AI
    iterations = await get_token_iterations_count(conn, token_id)
    min_data_ratio = 0.7  # 70% даних мінімум
    min_data_points = int(entry_gate_sec * min_data_ratio)  # 3360 ітерацій
    
    if iterations >= min_data_points:
        # AI може зробити прогноз
        decision = await ai_predict(token_id)
        if decision == "buy":
            await buy_real(token_id)
    else:
        # Недостатньо даних - пропускаємо цей цикл
        pass
```

**Переваги:**
- ✅ Автопокупка спрацює точно через 1 годину 20 хвилин життя токена
- ✅ Незалежно від якості даних (пропуски не впливають)
- ✅ Більш передбачувана поведінка

**Недоліки:**
- ⚠️ Потрібна перевірка достатності даних для AI (70% мінімум)
- ⚠️ Токени з великими пропусками (< 50% даних) не отримають AI прогноз

---

#### 2. **AI моделі - переобучення на часові мірки**

**Поточна система:**
- Pattern segments: сегменти 0-35, 35-85, 85-125 (ітерації)
- Модель навчена на повних даних (кожна секунда має ціну)

**Нова система (з переобученням):**
- Pattern segments: сегменти 0-35, 35-85, 85-125 (секунди реального часу)
- Модель навчена на даних з пропусками (forward-fill або interpolation)

**Варіанти переобучення:**

**Варіант A: Forward-fill (заповнення пропусків останнім значенням)**
```python
# Приклад: якщо на секунді 5 немає ціни, використовуємо ціну з секунди 4
prices = [0.001, 0.002, None, 0.003, None]
# Forward-fill: [0.001, 0.002, 0.002, 0.003, 0.003]
```

**Варіант B: Interpolation (лінійна інтерполяція)**
```python
# Приклад: якщо на секунді 5 немає ціни, інтерполюємо між секундами 4 та 6
prices = [0.001, 0.002, None, 0.003, None]
# Interpolation: [0.001, 0.002, 0.0025, 0.003, 0.003]
```

**Варіант C: Зберігати ітерації для AI, але використовувати live_seconds для віку**
```python
# Гібридний підхід:
live_seconds = calculate_live_seconds(token_id)  # Для перевірки віку
iterations = await get_token_iterations_count(conn, token_id)  # Для AI

# Перевірка віку за реальним часом
if live_seconds >= 4800:  # 1 година 20 хвилин
    # Перевірка достатності даних
    if iterations >= int(live_seconds * 0.7):  # 70% даних
        # AI прогноз на основі ітерацій (як зараз)
        decision = await ai_predict(token_id, iterations)
```

**Рекомендація:** Варіант C (гібридний підхід) - найбезпечніший, не потребує переобучення моделей.

---

## Конкретні зміни в коді

### 1. Автопокупка (рядок 1304 в `_v3_analyzer_jupiter.py`)

**Замість:**
```python
iterations = await get_token_iterations_count(conn, token_id)
entry_gate_iter = self.entry_sec  # 950
if iterations >= entry_gate_iter:
```

**Використовувати:**
```python
# Використовувати live_seconds для перевірки віку
live_seconds = live_seconds_value  # Вже розраховано вище (рядок 847)
entry_gate_sec = 4800  # 1 година 20 хвилин (замість 950 ітерацій)

if live_seconds >= entry_gate_sec:
    # Перевірка достатності даних для AI
    iterations = await get_token_iterations_count(conn, token_id)
    min_data_ratio = 0.7  # 70% даних мінімум
    min_data_points = int(entry_gate_sec * min_data_ratio)  # 3360 ітерацій
    
    if iterations >= min_data_points:
        # AI може зробити прогноз (використовуємо ітерації як зараз)
        # Далі логіка залишається такою ж
```

---

### 2. Pattern Segments (рядок 296 в `_v3_analyzer_jupiter.py`)

**Поточна логіка:**
```python
segment_end = SEGMENT_BOUNDS[idx][1]  # 35, 85, 125
if iterations_count is None or iterations_count < segment_end:
    predicted.append("unknown")
```

**Можна залишити як є** (використовувати ітерації для AI), або:

**Нова логіка (якщо переобучуємо модель):**
```python
# Використовувати live_seconds для перевірки сегментів
live_seconds = live_seconds_value  # Вже розраховано
segment_end = SEGMENT_BOUNDS[idx][1]  # 35, 85, 125 секунд

if live_seconds is None or live_seconds < segment_end:
    predicted.append("unknown")
else:
    # Перевірка достатності даних в сегменті
    segment_data_points = count_data_points_in_segment(token_id, segment_start, segment_end)
    min_segment_ratio = 0.6  # 60% даних в сегменті мінімум
    min_segment_points = int((segment_end - segment_start) * min_segment_ratio)
    
    if segment_data_points >= min_segment_points:
        # AI може зробити прогноз для сегменту
        # (з forward-fill або interpolation)
    else:
        predicted.append("unknown")
```

**Рекомендація:** Залишити ітерації для AI (не переобучувати), але додати перевірку достатності даних.

---

### 3. ETA модель (`ai/infer/eta_online.py`)

**Поточна логіка:**
```python
async def _age_now(conn, token_id: int) -> int:
    """Current age in iterations (≈ seconds of life with price>0)."""
    c = await conn.fetchval(
        "SELECT COUNT(*) FROM token_metrics_seconds "
        "WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0",
        token_id,
    )
    return int(c or 0)
```

**Можна залишити як є** (ETA модель використовує ітерації), або:

**Нова логіка (якщо переобучуємо):**
```python
async def _age_now(conn, token_id: int) -> int:
    """Current age in real-time seconds."""
    row = await conn.fetchrow(
        "SELECT first_pool_created_at, created_at FROM tokens WHERE id=$1",
        token_id
    )
    origin_ts = row['first_pool_created_at'] or row['created_at']
    if origin_ts:
        return int(time.time()) - int(origin_ts.timestamp())
    return 0
```

**Рекомендація:** Залишити ітерації для ETA моделі (не переобучувати).

---

## Вплив на точність прогнозів

### Сценарій 1: Токен з повними даними (100% секунд з ціною)

**Ітерації:** `iterations = live_seconds`  
**Вплив:** ✅ Немає змін - все працює як раніше

---

### Сценарій 2: Токен з пропусками (50% секунд з ціною)

**Ітерації:** `iterations = live_seconds / 2`  
**Поточна система:**
- На реальній 1000-й секунді: `iterations = 500`
- Автопокупка НЕ спрацює (потрібно 950 ітерацій)
- AI не може зробити прогноз (недостатньо даних)

**Нова система (з live_seconds):**
- На реальній 1000-й секунді: `live_seconds = 1000`, `iterations = 500`
- Автопокупка НЕ спрацює (потрібно 4800 секунд = 1 година 20 хвилин)
- AI не може зробити прогноз (500 < 3360 мінімум)

**Висновок:** Для автопокупки за 1 годину 20 хвилин - спрацює точно в потрібний момент.

---

### Сценарій 3: Токен з великими пропусками (20% секунд з ціною)

**Ітерації:** `iterations = live_seconds * 0.2`  
**Поточна система:**
- На реальній 5000-й секунді: `iterations = 1000`
- Автопокупка спрацює (1000 >= 950)
- AI може зробити прогноз

**Нова система (з live_seconds):**
- На реальній 5000-й секунді: `live_seconds = 5000`, `iterations = 1000`
- Автопокупка спрацює (5000 >= 4800)
- AI може зробити прогноз (1000 >= 3360? НІ - недостатньо даних)

**Висновок:** Токени з великими пропусками не отримають AI прогноз, але автопокупка спрацює точно за часом.

---

## Рекомендації

### ✅ Рекомендований підхід: Гібридний

1. **Використовувати `live_seconds` для:**
   - Перевірки віку токена (`live_seconds >= 4800` для автопокупки)
   - Архівування (`live_seconds >= 3600`)
   - Zero tail / Frozen price detection (останні N секунд)

2. **Використовувати `iterations_count` для:**
   - AI прогнозів (Pattern segments, ETA модель)
   - З мінімальною перевіркою: `iterations >= int(live_seconds * 0.7)`

3. **Переваги:**
   - ✅ Автопокупка спрацює точно через 1 годину 20 хвилин
   - ✅ Не потрібно переобучувати AI моделі
   - ✅ Більш передбачувана поведінка
   - ✅ Захист від токенів з великими пропусками (не робимо AI прогноз)

4. **Недоліки:**
   - ⚠️ Токени з великими пропусками (< 70% даних) не отримають AI прогноз
   - ⚠️ Потрібна додаткова перевірка достатності даних

---

## Конкретні значення для реалізації

### Константи:

```python
# config.py
AUTO_BUY_ENTRY_SEC = 4800  # 1 година 20 хвилин (замість 950)
AUTO_BUY_MIN_DATA_RATIO = 0.7  # 70% даних мінімум для AI прогнозу
AUTO_BUY_MIN_DATA_POINTS = int(AUTO_BUY_ENTRY_SEC * AUTO_BUY_MIN_DATA_RATIO)  # 3360

# Для архівування
ARCHIVE_MIN_LIVE_SECONDS = 3600  # 1 година (замість ARCHIVE_MIN_ITERATIONS = 1000)
```

### Логіка перевірки:

```python
# Перевірка віку за реальним часом
if live_seconds >= AUTO_BUY_ENTRY_SEC:  # 4800 секунд
    # Перевірка достатності даних для AI
    iterations = await get_token_iterations_count(conn, token_id)
    if iterations >= AUTO_BUY_MIN_DATA_POINTS:  # 3360 ітерацій
        # AI може зробити прогноз
        decision = await ai_predict(token_id)
        if decision == "buy":
            await buy_real(token_id)
    else:
        # Недостатньо даних - пропускаємо
        # Можна логувати: "Token {token_id} has {iterations} iterations but needs {AUTO_BUY_MIN_DATA_POINTS} for AI prediction"
        pass
```

---

## Висновок

**Можна зробити перехід на часові мірки для автопокупки:**

1. ✅ Використовувати `live_seconds` для перевірки віку токена (1 година 20 хвилин)
2. ✅ Додати перевірку достатності даних для AI (`iterations >= 70% від live_seconds`)
3. ✅ Залишити `iterations_count` для AI моделей (не переобучувати)
4. ✅ Автопокупка спрацює точно через 1 годину 20 хвилин життя токена

**Переваги:**
- Точність автопокупки (незалежно від якості даних)
- Не потрібно переобучувати AI моделі
- Більш передбачувана поведінка

**Недоліки:**
- Токени з великими пропусками (< 70% даних) не отримають AI прогноз
- Потрібна додаткова перевірка достатності даних

