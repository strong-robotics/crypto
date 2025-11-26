# План розгортання рефакторингу (видалення simulation)

**ВАЖЛИВО:** Цей план описує безпечне застосування міграцій для видалення simulation-функціональності та переходу на нову схему БД.

---

## 📋 Передумови

- PostgreSQL база даних з поточною схемою (включає `sim_*` поля та `sim_wallets`)
- Доступ до продакшн-сервера з правами на виконання SQL
- Можливість створити бекап БД
- Можливість зупинити сервіси на час міграції

---

## 🔄 Крок 1: Підготовка

### 1.1. Створення бекапу БД

```bash
# Створити повний бекап PostgreSQL
pg_dump -h localhost -U your_user -d crypto_db -F c -f backup_before_refactor_$(date +%Y%m%d_%H%M%S).dump

# Або SQL dump
pg_dump -h localhost -U your_user -d crypto_db -f backup_before_refactor_$(date +%Y%m%d_%H%M%S).sql
```

**Перевірка бекапу:**
```bash
# Перевірити розмір файлу
ls -lh backup_before_refactor_*.dump

# Перевірити цілісність (опціонально)
pg_restore --list backup_before_refactor_*.dump | head -20
```

### 1.2. Перевірка поточного стану БД

```sql
-- Перевірити кількість токенів з sim_* даними
SELECT 
    COUNT(*) FILTER (WHERE sim_buy_iteration IS NOT NULL) AS tokens_with_sim_buy,
    COUNT(*) FILTER (WHERE sim_sell_iteration IS NOT NULL) AS tokens_with_sim_sell,
    COUNT(*) FILTER (WHERE sim_wallet_id IS NOT NULL) AS tokens_with_sim_wallet,
    COUNT(*) AS total_tokens
FROM tokens;

-- Перевірити кількість записів у sim_wallets
SELECT COUNT(*) FROM sim_wallets;

-- Перевірити кількість записів у wallet_history
SELECT COUNT(*) FROM wallet_history;
```

### 1.3. Зупинка сервісів

```bash
# Зупинити Python сервер
./start.sh --stop

# Або вручну
pkill -f "python.*main.py"
pkill -f "uvicorn"

# Перевірити, що процеси зупинені
ps aux | grep -E "python|uvicorn" | grep -v grep
```

---

## 🔄 Крок 2: Data Migration (опціонально)

**ВАЖЛИВО:** Виконайте цей крок ТІЛЬКИ якщо у вас є дані в `sim_*` полях, які потрібно зберегти в `wallet_history`.

### 2.1. Перевірка потреб у міграції

```sql
-- Перевірити, чи є відкриті позиції в sim_*, які не в wallet_history
SELECT COUNT(*) 
FROM tokens t
WHERE t.sim_buy_iteration IS NOT NULL
  AND t.sim_sell_iteration IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM wallet_history wh 
      WHERE wh.token_id = t.id AND wh.exit_iteration IS NULL
  );
```

### 2.2. Виконання data migration (якщо потрібно)

```bash
# Підключитися до БД
psql -h localhost -U your_user -d crypto_db

# Виконати міграцію
\i server/migrations/20251106_data_migration.sql
```

**Або вручну:**
```bash
psql -h localhost -U your_user -d crypto_db -f server/migrations/20251106_data_migration.sql
```

**ПРИМІТКА:** За замовчуванням `20251106_data_migration.sql` не виконує жодних операцій (всі команди закоментовані). Розкоментуйте потрібні блоки, якщо у вас є дані для міграції.

---

## 🔄 Крок 3: Schema Migration

### 3.1. Перейменування таблиці (якщо ще не виконано)

```bash
psql -h localhost -U your_user -d crypto_db -f server/migrations/rename_sim_wallet_history_to_wallet_history.sql
```

### 3.2. Очищення tokens (видалення sim_* полів)

```bash
psql -h localhost -U your_user -d crypto_db -f server/migrations/20251106_tokens_cleanup.sql
```

**Перевірка:**
```sql
-- Перевірити, що sim_* поля видалені
SELECT column_name 
FROM information_schema.columns 
WHERE table_name = 'tokens' 
  AND column_name LIKE 'sim_%';
-- Має повернути 0 рядків

-- Перевірити, що нові поля існують
SELECT column_name 
FROM information_schema.columns 
WHERE table_name = 'tokens' 
  AND column_name IN ('plan_sell_iteration', 'plan_sell_price_usd', 'wallet_id');
-- Має повернути 3 рядки
```

### 3.3. Видалення таблиці sim_wallets

```bash
psql -h localhost -U your_user -d crypto_db -f server/migrations/20251106_drop_sim_wallets.sql
```

**Перевірка:**
```sql
-- Перевірити, що sim_wallets видалена
SELECT table_name 
FROM information_schema.tables 
WHERE table_name = 'sim_wallets';
-- Має повернути 0 рядків

-- Перевірити, що wallets існує
SELECT table_name 
FROM information_schema.tables 
WHERE table_name = 'wallets';
-- Має повернути 1 рядок
```

---

## 🔄 Крок 4: Валідація

### 4.1. Перевірка структури БД

```sql
-- Перевірити структуру tokens
\d tokens

-- Перевірити структуру wallet_history
\d wallet_history

-- Перевірити структуру wallets
\d wallets
```

### 4.2. Перевірка даних

```sql
-- Перевірити, що wallet_history містить дані (якщо очікується)
SELECT COUNT(*) FROM wallet_history;

-- Перевірити, що wallets містить дані
SELECT COUNT(*) FROM wallets;

-- Перевірити, що tokens.wallet_id правильно заповнений (якщо очікується)
SELECT COUNT(*) FROM tokens WHERE wallet_id IS NOT NULL;
```

### 4.3. Запуск smoke-тестів

```bash
# Запустити smoke-тести
cd server
python3 -m pytest tests/test_smoke_refactor.py -v

# Або вручну перевірити основні функції
python3 -c "
import asyncio
from _v3_db_pool import get_db_pool

async def check():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Перевірка структури
        sim_fields = await conn.fetch('SELECT column_name FROM information_schema.columns WHERE table_name = \\'tokens\\' AND column_name LIKE \\'sim_%\\'')
        print(f'Sim fields: {len(sim_fields)} (має бути 0)')
        
        plan_fields = await conn.fetchrow('SELECT column_name FROM information_schema.columns WHERE table_name = \\'tokens\\' AND column_name IN (\\'plan_sell_iteration\\', \\'plan_sell_price_usd\\', \\'wallet_id\\')')
        print(f'Plan fields exist: {plan_fields is not None}')

asyncio.run(check())
"
```

---

## 🔄 Крок 5: Запуск сервісів

### 5.1. Запуск Python сервера

```bash
# Запустити сервер
./start.sh

# Або вручну
cd server
python3 main.py
```

### 5.2. Перевірка логів

```bash
# Перевірити, що сервер запустився без помилок
tail -f logs/server.log

# Або перевірити через API
curl http://localhost:8002/api/analyzer/status
```

### 5.3. Перевірка функціональності

```bash
# Перевірити основні ендпоїнти
curl http://localhost:8002/api/tokens/list | jq '.tokens | length'

# Перевірити баланси
curl http://localhost:8002/api/wallet/check-positions | jq '.'
```

---

## 🔄 Крок 6: Моніторинг

### 6.1. Перші хвилини після запуску

- Перевірити логи на наявність помилок
- Перевірити, що аналізатор працює
- Перевірити, що баланси оновлюються
- Перевірити, що WebSocket з'єднання працюють

### 6.2. Перші години після запуску

- Перевірити, що токени правильно обробляються
- Перевірити, що wallet_history правильно заповнюється
- Перевірити, що AI правильно пише plan_sell_* в tokens
- Перевірити, що buy/sell операції працюють коректно

---

## ⚠️ Rollback Plan (якщо щось пішло не так)

### Відкат міграцій

```bash
# Відновити БД з бекапу
pg_restore -h localhost -U your_user -d crypto_db -c backup_before_refactor_*.dump

# Або з SQL dump
psql -h localhost -U your_user -d crypto_db < backup_before_refactor_*.sql
```

### Відкат коду

```bash
# Повернутися до попередньої версії коду
git checkout <previous_commit_hash>

# Перезапустити сервер
./start.sh
```

---

## 📝 Чеклист розгортання

- [ ] Створено бекап БД
- [ ] Перевірено поточний стан БД
- [ ] Зупинено сервіси
- [ ] Виконано data migration (якщо потрібно)
- [ ] Виконано schema migration (rename, cleanup, drop)
- [ ] Валідовано структуру БД
- [ ] Валідовано дані
- [ ] Запущено smoke-тести
- [ ] Запущено сервіси
- [ ] Перевірено логи
- [ ] Перевірено функціональність
- [ ] Налаштовано моніторинг

---

## 📞 Контакти для підтримки

Якщо виникли проблеми під час розгортання:
1. Перевірте логи сервера
2. Перевірте логи БД
3. Перевірте бекап БД
4. При необхідності виконайте rollback

---

**Дата створення:** 2025-11-06  
**Версія:** 1.0

