# DexScreener Analyzer System - Technical Documentation

## Overview

**File**: `server/_v2_analyzer_dexscreener.py`  
**Purpose**: Automated scanner that enriches token data with trading pair information from DexScreener API  
**Database**: PostgreSQL (migrated from SQLite)  
**Type**: Background async service with manual and auto-scan modes

## Architecture

### Core Components

1. **DexScreenerAnalyzer Class**
   - Manages DexScreener API integration
   - Handles batch processing of tokens (5 tokens/second - rate limit)
   - Stores trading pair data across 7 PostgreSQL tables
   - Implements retry logic with exponential backoff

2. **Database Tables**
   - `token_ids` - main tokens table (updates `token_pair`, `check_dexscreener`)
   - `dexscreener_pairs` - pair metadata (price, FDV, market cap, timestamps)
   - `dexscreener_base_token` - base token info (address, name, symbol)
   - `dexscreener_quote_token` - quote token info (address, name, symbol)
   - `dexscreener_txns` - transaction counts (5m, 1h, 6h, 24h buys/sells)
   - `dexscreener_volume` - trading volumes (5m, 1h, 6h, 24h)
   - `dexscreener_price_change` - price changes (5m, 1h, 6h, 24h)
   - `dexscreener_liquidity` - liquidity info (USD, base, quote)

3. **Scanning Modes**
   - **Auto-Scan**: Continuous background scanning (triggered by START button)
   - **Manual Scan**: One-time batch processing via console function

## Key Features

### 1. Auto-Scan Loop

```python
async def _auto_scan_loop(self):
    while self.is_scanning:
        # 1. Get tokens where check_dexscreener < 3
        tokens = await self.get_tokens_to_scan(limit=self.batch_size)
        
        # 2. If no tokens → wait 1 second and retry
        if not tokens:
            await asyncio.sleep(self.scan_interval)
            continue
        
        # 3. Scan tokens in parallel (batch of 5)
        tasks = [self.scan_token(token) for token in tokens]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 4. Wait 1 second before next batch (rate limit)
        await asyncio.sleep(self.scan_interval)
```

**Configuration**:
- `scan_interval = 1` second (time between batches)
- `batch_size = 5` tokens per batch
- Rate limit: **5 requests/second** (DexScreener API limit)

### 2. Token Selection Logic

**Query**:
```sql
SELECT id, token_address, check_dexscreener
FROM token_ids
WHERE check_dexscreener < 3
ORDER BY created_at ASC
LIMIT 5
```

**Logic**:
1. Processes oldest tokens first (`created_at ASC`)
2. Only tokens with `check_dexscreener < 3` (max 3 attempts)
3. Increments `check_dexscreener` after each attempt (success or failure)
4. Stops trying after 3 failed attempts (`check_dexscreener >= 3`)

### 3. Data Processing Flow

```
Token from DB (check_dexscreener < 3)
    ↓
Fetch DexScreener API
    ↓
    ├─ Success (pairs found)
    │   ├─ Save to 8 tables (token_ids + 7 DexScreener tables)
    │   ├─ Increment check_dexscreener + 1
    │   └─ Return True
    │
    ├─ API Error (HTTP error, timeout, etc.)
    │   ├─ Increment check_dexscreener + 1
    │   └─ Return False
    │
    └─ No Pairs Found (empty response)
        ├─ Increment check_dexscreener + 1
        └─ Return False
```

**Important**: `check_dexscreener` is **ALWAYS** incremented, regardless of success/failure. This ensures:
- No infinite loops for tokens without pairs
- Maximum 3 attempts per token
- System can move on to new tokens

### 4. Data Type Conversions (PostgreSQL)

**Decimal → float**:
```python
# Numeric fields stored as NUMERIC in PostgreSQL
float(pair.get("fdv", 0))
float(pair.get("marketCap", 0))
float(volume.get("h24", 0))
float(liquidity.get("usd", 0))
```

**datetime → string**:
```python
# pair_created_at stored as TEXT (ISO format)
datetime.fromtimestamp(pair.get("pairCreatedAt", 0) / 1000).isoformat()
```

**Scientific notation → string**:
```python
# priceNative/priceUsd stored as TEXT (can be very small numbers)
str(pair.get("priceNative", ""))
str(pair.get("priceUsd", ""))
```

### 5. Retry Logic

```python
async def _fetch_with_retries(self, url: str, **kwargs) -> Dict[str, Any]:
    for attempt in range(1, 4):  # 3 attempts
        try:
            async with self.session.get(url, **kwargs) as resp:
                # Return response if successful
                if 200 <= status < 300:
                    return {"ok": True, "status": status, "json": parsed}
        except Exception as e:
            # Exponential backoff: 0.4s, 0.8s, 1.6s (+ random jitter)
            backoff = 0.4 * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
            await asyncio.sleep(backoff)
    
    return {"ok": False, "error": str(last_exc)}
```

**Backoff timings**:
- Attempt 1 fails: wait ~0.4s
- Attempt 2 fails: wait ~0.8s
- Attempt 3 fails: return error

## API Endpoints Integration

### 1. Start Auto-Scan

**Endpoint**: `POST /api/auto-scan/start`

**Process**:
```python
# In main.py
await ensure_dexscreener_analyzer()
result = await state.dexscreener_analyzer.start_auto_scan()

# Returns:
{
    "success": True,
    "message": "DexScreener scanner started",
    "details": {
        "dexscreener": {
            "success": True,
            "message": "DexScreener scanner started"
        }
    }
}
```

**Lifecycle**:
1. Sets `is_scanning = True`
2. Creates background task: `asyncio.create_task(_auto_scan_loop())`
3. Loop runs continuously until `stop_auto_scan()` is called

### 2. Stop Auto-Scan

**Endpoint**: `POST /api/auto-scan/stop`

**Process**:
```python
result = await state.dexscreener_analyzer.stop_auto_scan()

# Process:
# 1. Sets is_scanning = False
# 2. Cancels scan_task
# 3. Waits for task to finish
# 4. Cleans up resources
```

### 3. Get Status

**Endpoint**: `GET /api/scanner/status`

**Response**:
```json
{
    "is_scanning": true,
    "details": {
        "dexscreener": {
            "is_scanning": true,
            "scan_interval": 1,
            "batch_size": 5
        }
    }
}
```

## Manual Scan Function

### refresh_missing_token_pairs()

**Purpose**: One-time batch processing for tokens without trading pairs

**Function signature**:
```python
async def refresh_missing_token_pairs(
    debug: bool = True,
    delay_seconds: float = 1.0,
    batch_size: int = 5,
    max_tokens: int = None,
    force_rescan: bool = False
) -> Dict
```

**Parameters**:
- `debug`: Show detailed logs
- `delay_seconds`: Delay between batches (default 1.0s for rate limit)
- `batch_size`: Tokens per batch (default 5)
- `max_tokens`: Limit processing (None = all tokens). For testing.
- `force_rescan`: Scan even if `check_dexscreener >= 3`

**Query Logic**:
```python
# Normal mode (force_rescan=False):
SELECT id, token_address, check_dexscreener
FROM token_ids
WHERE (token_pair IS NULL OR token_pair = '')
  AND check_dexscreener < 3
ORDER BY created_at ASC

# Force mode (force_rescan=True):
SELECT id, token_address, check_dexscreener
FROM token_ids
WHERE token_pair IS NULL OR token_pair = ''
ORDER BY created_at ASC
```

**Usage Examples**:

```bash
# 1. Scan all tokens without token_pair (skip if check >= 3)
python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs())"

# 2. Test mode - only 10 tokens
python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs(max_tokens=10))"

# 3. Force rescan (even if check >= 3)
python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs(force_rescan=True))"
```

**Output Example**:
```
================================================================================
🚀 ЗАПОВНЕННЯ TOKEN_PAIR ДЛЯ ТОКЕНІВ БЕЗ ТОРГОВОЇ ПАРИ
================================================================================
📊 Знайдено токенів БЕЗ token_pair: 45
⏭️  Пропускаємо токени з check_dexscreener >= 3
⏱️  Затримка між батчами: 1.0s
📦 Розмір батчу: 5 токенів/сек (rate limit)
================================================================================

────────────────────────────────────────────────────────────────────────────────
📦 Батч 1/9 (5 токенів)
────────────────────────────────────────────────────────────────────────────────
✅ Token 8Tg6NK4nVe3uCz... success
❌ Token 9Xf2Lm8kPq9rTs... no pair found
✅ Token 3Hn5Qw7yRt2uVx... success
⚠️  Token 7Zp4Nm3cXt8wQr... API error
✅ Token 5Yn2Km9bZp6sTq... success
📊 Батч результат: 3/5 успішно
⏳ Затримка 1.0s перед наступним батчем...

================================================================================
🎉 ЗАПОВНЕННЯ ЗАВЕРШЕНО
================================================================================
✅ Оброблено токенів: 45/45
✅ Успішно заповнено: 32
❌ Помилок/не знайдено: 13
================================================================================
```

**Return Value**:
```python
{
    "success": True,
    "total_tokens": 45,
    "processed_tokens": 45,
    "success_count": 32,
    "failed_count": 13
}
```

## Database Schema

### token_ids (updates)

```sql
-- Updated fields:
token_pair TEXT                  -- Pair address from DexScreener
check_dexscreener INTEGER        -- Attempt counter (0-3)
```

### dexscreener_pairs

```sql
CREATE TABLE dexscreener_pairs (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    chain_id TEXT,
    dex_id TEXT,
    url TEXT,
    pair_address TEXT,
    price_native TEXT,           -- Scientific notation as string
    price_usd TEXT,              -- Scientific notation as string
    fdv NUMERIC,
    market_cap NUMERIC,
    pair_created_at TEXT,        -- ISO datetime string
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

### dexscreener_base_token

```sql
CREATE TABLE dexscreener_base_token (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    address TEXT,
    name TEXT,
    symbol TEXT
);
```

### dexscreener_quote_token

```sql
CREATE TABLE dexscreener_quote_token (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    address TEXT,
    name TEXT,
    symbol TEXT
);
```

### dexscreener_txns

```sql
CREATE TABLE dexscreener_txns (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    m5_buys INTEGER,
    m5_sells INTEGER,
    h1_buys INTEGER,
    h1_sells INTEGER,
    h6_buys INTEGER,
    h6_sells INTEGER,
    h24_buys INTEGER,
    h24_sells INTEGER
);
```

### dexscreener_volume

```sql
CREATE TABLE dexscreener_volume (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    h24 NUMERIC,
    h6 NUMERIC,
    h1 NUMERIC,
    m5 NUMERIC
);
```

### dexscreener_price_change

```sql
CREATE TABLE dexscreener_price_change (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    m5 NUMERIC,
    h1 NUMERIC,
    h6 NUMERIC,
    h24 NUMERIC
);
```

### dexscreener_liquidity

```sql
CREATE TABLE dexscreener_liquidity (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    usd NUMERIC,
    base NUMERIC,
    quote NUMERIC
);
```

## Integration with Other Systems

### 1. Jupiter Scanner Integration

**Flow**:
```
Jupiter Scanner (new tokens)
    ↓ (sets check_dexscreener = 0)
token_ids table
    ↓ (check_dexscreener < 3)
DexScreener Analyzer
    ↓ (updates token_pair + 7 tables)
Frontend display
```

**Timing**:
- Jupiter Scanner: every 5 seconds (20 new tokens)
- DexScreener Analyzer: every 1 second (5 tokens processed)
- Processing lag: ~4-5 seconds per batch of 5 tokens

### 2. Frontend Updates

**Data Flow**:
```
DexScreener Analyzer saves data
    ↓
Triggers TokensReaderV2._auto_refresh_loop
    ↓ (detects MAX(updated_at) change)
WebSocket /ws/tokens sends update
    ↓
Frontend re-renders with new token_pair
```

**Updated Fields**:
- `token_pair`: Trading pair address (displayed in UI)
- `check_dexscreener`: Scan attempt counter (not displayed, internal)

## Error Handling

### 1. API Errors

```python
# HTTP errors, timeouts, network issues
if "error" in dexscreener_data:
    # Increment check_dexscreener to avoid infinite retry
    await conn.execute("""
        UPDATE token_ids 
        SET check_dexscreener = check_dexscreener + 1 
        WHERE id = $1
    """, token_id)
    return False
```

### 2. No Pairs Found

```python
# DexScreener has no data for this token
pairs = dexscreener_data.get("pairs", [])
if not pairs:
    # Increment check_dexscreener
    # This prevents endless scanning of tokens without pairs
    return False
```

### 3. Save Failures

```python
# Database errors, data validation issues
try:
    await self.save_dexscreener_data(...)
except Exception as e:
    # Log error and increment check_dexscreener
    # Prevents blocking the queue
    return False
```

## Performance Optimization

### 1. Batch Processing

- **Before**: 1 token/second (sequential)
- **After**: 5 tokens/second (parallel batch)
- **Improvement**: 5x faster processing

### 2. Rate Limit Compliance

```python
# DexScreener API limit: 300 requests/minute = 5 requests/second
batch_size = 5          # Process 5 tokens in parallel
scan_interval = 1       # Wait 1 second between batches
# Result: Exactly 5 requests/second (max allowed)
```

### 3. Database Optimization

**UPSERT instead of INSERT + UPDATE**:
```python
# Single query instead of SELECT + (INSERT or UPDATE)
INSERT INTO dexscreener_pairs (...) VALUES (...)
ON CONFLICT (token_id) DO UPDATE SET ...
```

**Benefits**:
- Atomic operation
- No race conditions
- Faster execution

## Monitoring and Debugging

### Debug Logs

**Enable**:
```python
analyzer = DexScreenerAnalyzer(debug=True)
```

**Output**:
```
🔍 Scanning token_id=123, address=8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR, check=1
✅ Saved DexScreener data for token_id=123, address=8Tg6NK...
📊 Scanning 5 tokens...
✅ Scanned 4/5 tokens successfully
```

### Status Monitoring

**Check if running**:
```python
status = analyzer.get_status()
# {
#     "is_scanning": True,
#     "scan_interval": 1,
#     "batch_size": 5
# }
```

### Database Queries

**Check progress**:
```sql
-- Tokens waiting for scan
SELECT COUNT(*) FROM token_ids WHERE check_dexscreener < 3;

-- Tokens with trading pairs
SELECT COUNT(*) FROM token_ids WHERE token_pair IS NOT NULL;

-- Tokens that failed all attempts
SELECT COUNT(*) FROM token_ids WHERE check_dexscreener >= 3 AND token_pair IS NULL;
```

## Lifecycle Management

### Startup (main.py)

```python
@app.on_event("startup")
async def startup_event():
    # Initialize but DON'T start scanning
    await ensure_dexscreener_analyzer()
    # Scanning starts only when user clicks START button
```

### Shutdown (main.py)

```python
@app.on_event("shutdown")
async def shutdown_event():
    # Stop scanner if running
    if state.dexscreener_analyzer and state.dexscreener_analyzer.is_scanning:
        await state.dexscreener_analyzer.stop_auto_scan()
    
    # Cleanup resources
    await cleanup()
```

### Singleton Pattern

```python
dexscreener_instance: Optional[DexScreenerAnalyzer] = None

async def get_dexscreener_analyzer() -> DexScreenerAnalyzer:
    global dexscreener_instance
    if dexscreener_instance is None:
        dexscreener_instance = DexScreenerAnalyzer(debug=True)
        await dexscreener_instance.ensure_connection()
        await dexscreener_instance.ensure_session()
    return dexscreener_instance
```

**Why Singleton**:
- Only one scanner instance per application
- Shared aiohttp session (connection pooling)
- Prevents duplicate scanning of same tokens

## Common Use Cases

### 1. First-Time Setup

**Scenario**: New database with 200 tokens, none have trading pairs

**Steps**:
1. Start auto-scan via API: `POST /api/auto-scan/start`
2. Scanner processes 5 tokens/second
3. Expected time: ~40 seconds for 200 tokens
4. Tokens without pairs will be tried 3 times, then skipped

### 2. Continuous Operation

**Scenario**: Production mode with Jupiter Scanner adding new tokens

**Flow**:
1. Jupiter Scanner adds token (check_dexscreener = 0)
2. DexScreener Analyzer picks it up in next batch
3. Tries to find trading pair (up to 3 attempts)
4. Updates frontend via WebSocket
5. Process repeats for new tokens

### 3. Manual Backfill

**Scenario**: System was offline, need to fill missing pairs for 50 tokens

**Command**:
```bash
python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs())"
```

**Process**:
- Scans only tokens without `token_pair`
- Skips tokens with `check_dexscreener >= 3`
- Processes in batches of 5 (rate limit compliant)
- Shows progress and results

### 4. Force Re-scan

**Scenario**: DexScreener added new pairs, need to re-scan old tokens

**Command**:
```bash
python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs(force_rescan=True))"
```

**Process**:
- Ignores `check_dexscreener` counter
- Re-scans ALL tokens without trading pairs
- Use with caution (can be many API requests)

## Best Practices

### 1. Rate Limit Compliance

✅ **Do**:
- Keep `batch_size = 5` and `scan_interval = 1`
- Use exponential backoff for retries
- Monitor API response times

❌ **Don't**:
- Increase batch_size beyond 5
- Decrease scan_interval below 1 second
- Remove retry delays

### 2. Error Handling

✅ **Do**:
- Always increment `check_dexscreener` on errors
- Log errors for debugging
- Return False to mark as failed

❌ **Don't**:
- Skip incrementing counter (causes infinite loops)
- Re-raise exceptions (breaks auto-scan loop)
- Ignore API errors

### 3. Database Updates

✅ **Do**:
- Use UPSERT for idempotency
- Convert data types before INSERT
- Handle NULL values gracefully

❌ **Don't**:
- Use separate SELECT + INSERT queries
- Insert raw API data without conversion
- Assume all fields exist

## Migration Notes (SQLite → PostgreSQL)

### Changed Syntax

**SQLite** → **PostgreSQL**:
- `?` placeholders → `$1, $2, $3` placeholders
- `aiosqlite.connect()` → `await get_db_pool()`
- `async with self.conn.execute()` → `async with pool.acquire() as conn`

### Data Type Changes

**Decimal Handling**:
```python
# SQLite: stores as TEXT
INSERT INTO table VALUES (123.456)

# PostgreSQL: stores as NUMERIC, returns Decimal
# Must convert to float for JSON serialization
INSERT INTO table VALUES ($1)  # float(123.456)
```

**DateTime Handling**:
```python
# SQLite: stores as TEXT (ISO format)
INSERT INTO table VALUES ('2025-10-15T12:00:00')

# PostgreSQL: stores as TIMESTAMP
# Must parse string to datetime object
INSERT INTO table VALUES ($1)  # datetime.fromisoformat(...)
```

## Troubleshooting

### Issue: Scanner not processing tokens

**Check**:
1. Is scanner running? `GET /api/scanner/status`
2. Are there tokens to scan? `SELECT COUNT(*) FROM token_ids WHERE check_dexscreener < 3`
3. Check logs for errors

### Issue: All tokens have check_dexscreener = 3

**Reason**: All tokens were attempted 3 times without success

**Solutions**:
1. Check DexScreener API status
2. Verify token addresses are correct
3. Use `force_rescan=True` to retry
4. Reset counter manually: `UPDATE token_ids SET check_dexscreener = 0 WHERE ...`

### Issue: Frontend not showing token_pair

**Check**:
1. Is token_pair actually saved? `SELECT token_pair FROM token_ids WHERE id = X`
2. Is WebSocket connected? Check browser console
3. Is TokensReaderV2 detecting changes? Check auto-refresh logic

### Issue: Rate limit exceeded (HTTP 429)

**Reason**: Sending > 5 requests/second

**Solutions**:
1. Check `batch_size` (should be 5)
2. Check `scan_interval` (should be 1)
3. Verify no other processes are calling DexScreener API
4. Wait for rate limit reset (~1 minute)

## Future Improvements

1. **Adaptive Rate Limiting**
   - Monitor API response times
   - Automatically adjust batch_size/scan_interval
   - Handle HTTP 429 gracefully

2. **Priority Queue**
   - Scan tokens with recent trades first
   - Skip tokens with very low liquidity
   - Re-scan tokens with outdated data

3. **Caching**
   - Cache API responses (5-minute TTL)
   - Reduce redundant API calls
   - Faster re-scans

4. **Metrics**
   - Track success/failure rates
   - Monitor API response times
   - Alert on high failure rates

5. **Multi-DEX Support**
   - Raydium, Orca, Jupiter pairs
   - Store multiple pairs per token
   - Select best pair by liquidity

## Summary

The DexScreener Analyzer is a **critical component** for enriching token data:

- **Automatic**: Runs in background, processes tokens continuously
- **Efficient**: 5 tokens/second (max DexScreener rate limit)
- **Reliable**: Retry logic, error handling, auto-increment counter
- **Scalable**: Parallel batch processing, UPSERT operations
- **Observable**: Debug logs, status endpoint, manual scan function

It works seamlessly with:
- **Jupiter Scanner** (provides new tokens)
- **TokensReaderV2** (sends updates to frontend)
- **Frontend** (displays trading pairs in UI)

**Key Metrics**:
- Processing speed: 5 tokens/second
- Max attempts: 3 per token
- API calls: ~300/minute (max allowed)
- Tables updated: 8 (1 main + 7 DexScreener)

