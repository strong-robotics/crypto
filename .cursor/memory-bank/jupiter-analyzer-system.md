# Jupiter Analyzer System - Technical Documentation

## Overview

**File**: `server/_v2_analyzer_jupiter.py`  
**Purpose**: Automated batch scanner that enriches existing tokens with extended Jupiter data (stats, audit, first pool, tags)  
**Database**: PostgreSQL  
**Type**: Background async service with cursor-based pagination

## Architecture

### Core Components

1. **JupiterAnalyzer Class**
   - Batch processing using Jupiter Search API (100 tokens per request)
   - Cursor-based pagination for continuous database iteration
   - Stores extended token data across 4 PostgreSQL tables
   - Implements check counter (`check_jupiter`) to limit attempts

2. **Database Tables**
   - `token_ids` - main tokens table (updates `check_jupiter`)
   - `token_stats` - 5m/1h/6h/24h price changes, volumes, trader counts
   - `token_audit` - security info (mint authority, freeze authority, holder distribution)
   - `token_first_pool` - first liquidity pool creation info
   - `token_tags` - flexible tagging system (multiple tags per token)

3. **Scanning Mode**
   - **Auto-Scan Only**: Continuous background scanning (no manual mode)
   - Runs indefinitely until stopped via API

## Key Features

### 1. Cursor-Based Pagination

**Why Cursor-Based?**
- Ensures **full database coverage** (no tokens skipped)
- Avoids **re-processing** same initial tokens in each cycle
- Fills batches **completely** (100 tokens, not 26 + 15 separate batches)
- **Efficient** for large datasets (no OFFSET overhead)

**How It Works:**
```python
class JupiterAnalyzer:
    def __init__(self):
        self.last_processed_id = 0  # Cursor position
        self.batch_size = 100
        self.scan_interval = 3  # seconds
```

**Pagination Logic:**
```python
async def get_tokens_to_scan(self, limit: int = 100) -> List[Dict]:
    """
    Get EXACTLY 100 tokens for batch processing
    
    Process:
    1. Start from self.last_processed_id (where we left off)
    2. Iterate through ALL tokens (ORDER BY id ASC)
    3. Skip tokens where check_jupiter >= 3
    4. Collect EXACTLY 100 tokens (not more, not less)
    5. Save ID of last token in self.last_processed_id
    6. If reached end of DB → reset cursor to 0 (start from beginning)
    
    Example (254 tokens):
    Cycle 1: Token 1-150 → found 100 with check<3 → last_id=150
    Cycle 2: Token 151-200 → found 50 with check<3 → continue
             Token 201-254 → found 50 more → total 100 → last_id=254
    Cycle 3: Reached end (254) → RESET cursor=0 → start from Token 1
    """
    tokens = []
    current_id = self.last_processed_id
    
    # Get max ID in database
    max_id = await conn.fetchval("SELECT MAX(id) FROM token_ids")
    
    # Reset cursor if we're past the end
    if current_id >= max_id:
        self.last_processed_id = 0
        current_id = 0
    
    # Collect EXACTLY 100 tokens
    while len(tokens) < limit:
        # Fetch next chunk (200 at a time for speed)
        rows = await conn.fetch("""
            SELECT id, token_address, check_jupiter
            FROM token_ids
            WHERE id > $1 AND check_jupiter < 3
            ORDER BY id ASC
            LIMIT 200
        """, current_id)
        
        if not rows:
            # Reached end - try wrapping around to start
            if current_id > 0:
                rows = await conn.fetch("""
                    SELECT id, token_address, check_jupiter
                    FROM token_ids
                    WHERE id <= $1 AND check_jupiter < 3
                    ORDER BY id ASC
                    LIMIT 200
                """, self.last_processed_id)
                
                if not rows:
                    break  # No more tokens to process
                
                current_id = 0  # Reset for next cycle
            else:
                break  # Already tried from start
        
        # Add tokens to batch
        for row in rows:
            if len(tokens) >= limit:
                break
            tokens.append({
                "token_id": row['id'],
                "token_address": row['token_address'],
                "check_jupiter": row['check_jupiter']
            })
            current_id = row['id']
    
    # Save cursor position for next cycle
    if tokens:
        self.last_processed_id = tokens[-1]["token_id"]
    
    return tokens
```

**Example Execution (147 tokens in DB):**
```
Cycle 1:
  - Start: last_processed_id = 0
  - Query: id > 0 AND check_jupiter < 3 LIMIT 200
  - Found: 100 tokens (ids 1-105, some skipped)
  - Update: last_processed_id = 105
  - API: 1 batch request (100 tokens)

Cycle 2:
  - Start: last_processed_id = 105
  - Query: id > 105 AND check_jupiter < 3 LIMIT 200
  - Found: 47 tokens (ids 106-147)
  - Update: last_processed_id = 147
  - API: 1 batch request (47 tokens)

Cycle 3:
  - Start: last_processed_id = 147
  - Query: id > 147 → no results (end of DB)
  - Reset: last_processed_id = 0
  - Query: id <= 147 AND check_jupiter < 3
  - Found: 0 tokens (all have check_jupiter >= 3 now)
  - No API request (nothing to process)

Cycle 4-∞:
  - Continuously checks DB every 3 seconds
  - Waits for new tokens from Jupiter Scanner
  - When new token appears → processes immediately
```

### 2. Batch API Integration

**Jupiter Search API**:
```
GET https://lite-api.jup.ag/tokens/v2/search?query=ADDRESS1,ADDRESS2,...,ADDRESS100
```

**Request Format**:
```python
async def get_tokens_data_batch(self, token_addresses: List[str]) -> List[Any]:
    """
    Fetch data for UP TO 100 tokens in a SINGLE API request
    
    Args:
        token_addresses: List of mint addresses (comma-separated in URL)
    
    Returns:
        List of token data objects (stats, audit, firstPool, tags)
    """
    # Join addresses with comma
    query = ",".join(token_addresses)
    url = f"https://lite-api.jup.ag/tokens/v2/search?query={query}"
    
    # Single API call for entire batch
    response = await self._fetch_with_retries(url)
    
    if response["ok"]:
        data = response["json"]
        return data.get("result", {}).get("data", [])
    
    return []
```

**Benefits**:
- **1 API call** for 100 tokens (vs. 100 calls for individual tokens)
- **Much faster** processing (~0.5s for 100 tokens vs. ~50s for 100 individual calls)
- **Lower API rate limit usage**

### 3. Auto-Scan Loop

```python
async def _auto_scan_loop(self):
    """
    Infinite loop that processes tokens in batches of 100
    
    IMPORTANT: Does NOT stop when no tokens found!
    Continues checking every 3 seconds for new tokens.
    """
    while self.is_scanning:
        try:
            # 1. Get batch of tokens (up to 100) where check_jupiter < 3
            tokens = await self.get_tokens_to_scan(limit=self.batch_size)
            
            # 2. If no tokens → DON'T STOP, just wait and retry
            if not tokens:
                if self.debug:
                    print(f"ℹ️  Немає токенів для сканування (всі check_jupiter >= 3)")
                await asyncio.sleep(self.scan_interval)
                continue  # ← Continue loop, don't exit
            
            # 3. Extract mint addresses for batch API request
            token_addresses = [t["token_address"] for t in tokens]
            
            # 4. Single API call for all tokens in batch
            jupiter_data_list = await self.get_tokens_data_batch(token_addresses)
            
            # 5. Map API results to tokens (by mint address)
            address_to_data = {
                item.get("id"): item 
                for item in jupiter_data_list 
                if item.get("id")
            }
            
            # 6. Save data for each token
            success_count = 0
            for token in tokens:
                jupiter_data = address_to_data.get(token["token_address"])
                
                if jupiter_data:
                    success = await self.save_jupiter_extended_data(
                        token["token_id"],
                        token["token_address"],
                        jupiter_data
                    )
                    if success:
                        success_count += 1
            
            # 7. Bulk increment check_jupiter for ALL tokens in batch
            await self._increment_check_jupiter_bulk(tokens)
            
            if self.debug:
                print(f"✅ Batch: {success_count}/{len(tokens)} успішно збережено")
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error in auto-scan loop: {e}")
        
        # 8. Wait 3 seconds before next batch
        await asyncio.sleep(self.scan_interval)
```

**Configuration**:
- `scan_interval = 3` seconds (time between batches)
- `batch_size = 100` tokens per batch
- **Rate limit**: 1 API call every 3 seconds (very conservative)

**Why 3 seconds?**
- Jupiter API has no official rate limit for Search endpoint
- Conservative approach to avoid potential throttling
- Can be reduced if needed

### 4. Data Saving Logic

```python
async def save_jupiter_extended_data(
    self, 
    token_id: int, 
    token_address: str, 
    jupiter_data: Any
) -> bool:
    """
    Save Jupiter extended data to 4 tables:
    1. token_stats (price changes, volumes, trader counts)
    2. token_audit (security info)
    3. token_first_pool (first pool creation)
    4. token_tags (multiple tags per token)
    
    IMPORTANT: Does NOT increment check_jupiter here!
    That's done in bulk for entire batch in _auto_scan_loop.
    """
    try:
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            # 1. Save token_stats (5m, 1h, 6h, 24h metrics)
            stats_5m = jupiter_data.get("stats", {}).get("5m", {})
            stats_1h = jupiter_data.get("stats", {}).get("1h", {})
            stats_6h = jupiter_data.get("stats", {}).get("6h", {})
            stats_24h = jupiter_data.get("stats", {}).get("24h", {})
            
            await conn.execute("""
                INSERT INTO token_stats (
                    token_id,
                    stats_5m_price_change, stats_5m_buy_volume, stats_5m_sell_volume,
                    stats_5m_num_buys, stats_5m_num_sells, stats_5m_num_traders,
                    stats_1h_price_change, ..., stats_24h_num_net_buyers
                ) VALUES ($1, $2, $3, ...)
                ON CONFLICT (token_id) DO UPDATE SET
                    stats_5m_price_change = EXCLUDED.stats_5m_price_change,
                    ...
            """, token_id, ...)
            
            # 2. Save token_audit (security info)
            audit = jupiter_data.get("audit", {})
            await conn.execute("""
                INSERT INTO token_audit (
                    token_id, mint_authority_disabled, freeze_authority_disabled,
                    top_holders_percentage, dev_balance_percentage, dev_migrations
                ) VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (token_id) DO UPDATE SET ...
            """, token_id, ...)
            
            # 3. Save token_first_pool (first pool creation)
            first_pool = jupiter_data.get("firstPool", {})
            if first_pool:
                await conn.execute("""
                    INSERT INTO token_first_pool (token_id, pool_id, created_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (token_id) DO UPDATE SET ...
                """, token_id, ...)
            
            # 4. Save token_tags (multiple tags per token)
            tags = jupiter_data.get("tags", [])
            if tags:
                # Delete old tags first
                await conn.execute("DELETE FROM token_tags WHERE token_id = $1", token_id)
                
                # Insert new tags
                for tag in tags:
                    await conn.execute("""
                        INSERT INTO token_tags (token_id, tag)
                        VALUES ($1, $2)
                        ON CONFLICT (token_id, tag) DO NOTHING
                    """, token_id, tag)
            
            return True
            
    except Exception as e:
        if self.debug:
            print(f"❌ Error saving Jupiter data for {token_address}: {e}")
        return False
```

### 5. Bulk Counter Increment

```python
async def _increment_check_jupiter_bulk(self, tokens: List[Dict[str, Any]]):
    """
    Increment check_jupiter for entire batch in a SINGLE query
    
    Why bulk?
    - Much faster than 100 individual UPDATE queries
    - Atomic operation (all or nothing)
    - Less database load
    """
    try:
        pool = await get_db_pool()
        token_ids = [t["token_id"] for t in tokens]
        
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE token_ids
                SET check_jupiter = check_jupiter + 1
                WHERE id = ANY($1)
            """, token_ids)
            
    except Exception as e:
        if self.debug:
            print(f"❌ Error incrementing check_jupiter: {e}")
```

**Benefits**:
- **1 query** instead of 100 queries
- **10-100x faster** than individual updates
- **Atomic**: all tokens updated or none

## Database Schema

### token_ids (updates)

```sql
-- New field:
check_jupiter INTEGER DEFAULT 0  -- Attempt counter (0-3)
```

### token_stats

```sql
CREATE TABLE token_stats (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    
    -- 5-minute stats
    stats_5m_price_change NUMERIC,
    stats_5m_buy_volume NUMERIC,
    stats_5m_sell_volume NUMERIC,
    stats_5m_num_buys INTEGER,
    stats_5m_num_sells INTEGER,
    stats_5m_num_traders INTEGER,
    stats_5m_num_net_buyers INTEGER,
    
    -- 1-hour stats (same fields)
    stats_1h_price_change NUMERIC,
    stats_1h_buy_volume NUMERIC,
    ... (7 fields)
    
    -- 6-hour stats (same fields)
    stats_6h_price_change NUMERIC,
    stats_6h_buy_volume NUMERIC,
    ... (7 fields)
    
    -- 24-hour stats (same fields)
    stats_24h_price_change NUMERIC,
    stats_24h_buy_volume NUMERIC,
    ... (7 fields)
    
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

**Total fields**: 28 metrics (4 time periods × 7 metrics each)

### token_audit

```sql
CREATE TABLE token_audit (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    mint_authority_disabled BOOLEAN,          -- Can't mint more tokens?
    freeze_authority_disabled BOOLEAN,        -- Can't freeze accounts?
    top_holders_percentage NUMERIC,           -- % held by top wallets
    dev_balance_percentage NUMERIC,           -- % held by dev
    dev_migrations INTEGER,                   -- Times dev moved tokens
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

**Security metrics**:
- ✅ Good: `mint_authority_disabled = true` (supply fixed)
- ✅ Good: `freeze_authority_disabled = true` (can't freeze users)
- ⚠️ Warning: `top_holders_percentage > 50%` (centralized)
- ⚠️ Warning: `dev_balance_percentage > 20%` (rug pull risk)
- ⚠️ Warning: `dev_migrations > 5` (suspicious activity)

### token_first_pool

```sql
CREATE TABLE token_first_pool (
    token_id INTEGER PRIMARY KEY REFERENCES token_ids(id) ON DELETE CASCADE,
    pool_id TEXT,                             -- First liquidity pool ID
    created_at TIMESTAMP WITHOUT TIME ZONE,   -- When pool was created
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

**Use case**: Determine token age and initial liquidity

### token_tags

```sql
CREATE TABLE token_tags (
    id SERIAL PRIMARY KEY,
    token_id INTEGER REFERENCES token_ids(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    UNIQUE (token_id, tag)  -- No duplicate tags per token
);
```

**Example tags**:
- `"verified"` - Verified by Jupiter
- `"trending"` - Currently trending
- `"community"` - Community token
- `"meme"` - Meme coin

**Multiple tags per token**:
```sql
-- Token can have many tags
INSERT INTO token_tags VALUES (1, 123, 'verified');
INSERT INTO token_tags VALUES (2, 123, 'trending');
INSERT INTO token_tags VALUES (3, 123, 'community');
```

## Integration with Other Systems

### 1. Jupiter Scanner Integration

**Flow**:
```
Jupiter Scanner (new tokens)
    ↓ (sets check_jupiter = 0)
token_ids table
    ↓ (check_jupiter < 3)
Jupiter Analyzer
    ↓ (updates 4 tables: stats, audit, first pool, tags)
Frontend display (future)
```

**Timing**:
- Jupiter Scanner: every 5 seconds (20 new tokens)
- Jupiter Analyzer: every 3 seconds (100 tokens processed)
- Processing lag: ~3-6 seconds for batch of 100 tokens

### 2. UPSERT Logic in Scanner

**Jupiter Scanner** (`_v2_new_tokens.py`) also populates Jupiter tables:

```python
# In save_jupiter_data():
# 1. UPSERT into token_ids (basic info)
# 2. UPSERT into token_stats (if stats available)
# 3. UPSERT into token_audit (if audit available)
# 4. UPSERT into token_first_pool (if firstPool available)
# 5. UPSERT into token_tags (if tags available)
```

**Why both Scanner and Analyzer?**
- **Scanner**: Saves data for NEW tokens (20 every 5 seconds)
- **Analyzer**: Backfills data for OLD tokens (100 every 3 seconds)
- **Result**: All tokens eventually have complete Jupiter data

### 3. Parallel Processing

```
┌─────────────────────────────────────────┐
│ Jupiter Scanner (new tokens)            │
│ → Adds 20 tokens/5s with check=0        │
│ → Saves basic + extended Jupiter data   │
└─────────────────────────────────────────┘
              ↓ (new tokens)
┌─────────────────────────────────────────┐
│ token_ids table                         │
│ → Mix of old (check<3) and new (check=0)│
└─────────────────────────────────────────┘
              ↓ (tokens to enrich)
┌─────────────────────────────────────────┐
│ Jupiter Analyzer (backfill old tokens)  │
│ → Processes 100 tokens/3s (check<3)     │
│ → Updates stats/audit/pool/tags          │
│ → Increments check_jupiter              │
└─────────────────────────────────────────┘
```

**No conflicts** because:
- Scanner handles NEW tokens (just added)
- Analyzer handles OLD tokens (added earlier, check<3)
- UPSERT ensures no duplicate data

## API Endpoints Integration

### 1. Start Auto-Scan

**Endpoint**: `POST /api/auto-scan/start`

**Process**:
```python
# In main.py
await ensure_jupiter_analyzer()
result = await state.jupiter_analyzer.start_auto_scan()

# Returns:
{
    "success": True,
    "message": "All scanners started",
    "details": {
        "jupiter_scanner": {...},
        "dexscreener": {...},
        "jupiter_analyzer": {
            "success": True,
            "message": "Jupiter Analyzer started"
        }
    }
}
```

**What happens**:
1. Sets `is_scanning = True`
2. Creates background task: `asyncio.create_task(_auto_scan_loop())`
3. Loop runs continuously (even if no tokens to process)
4. Waits for new tokens from Jupiter Scanner

### 2. Stop Auto-Scan

**Endpoint**: `POST /api/auto-scan/stop`

**Process**:
```python
result = await state.jupiter_analyzer.stop_auto_scan()

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
        "jupiter_scanner": {...},
        "dexscreener": {...},
        "jupiter_analyzer": {
            "is_scanning": true,
            "scan_interval": 3,
            "batch_size": 100,
            "last_processed_id": 147
        }
    }
}
```

## Cursor-Based Pagination Example

### Scenario: 254 tokens in database

**Initial State**:
- 254 tokens total
- 254 tokens with `check_jupiter = 0` (never scanned)
- `last_processed_id = 0` (start from beginning)

**Cycle 1** (0 seconds):
```
Query: id > 0 AND check_jupiter < 3 LIMIT 200
Found: 200 tokens (ids 1-200)
Select: First 100 tokens (ids 1-100)
API: Batch request for 100 tokens
Save: Update stats/audit/pool/tags for 100 tokens
Increment: check_jupiter = 1 for ids 1-100
Update: last_processed_id = 100
Wait: 3 seconds
```

**Cycle 2** (3 seconds):
```
Query: id > 100 AND check_jupiter < 3 LIMIT 200
Found: 154 tokens (ids 101-254)
Select: First 100 tokens (ids 101-200)
API: Batch request for 100 tokens
Save: Update for 100 tokens
Increment: check_jupiter = 1 for ids 101-200
Update: last_processed_id = 200
Wait: 3 seconds
```

**Cycle 3** (6 seconds):
```
Query: id > 200 AND check_jupiter < 3 LIMIT 200
Found: 54 tokens (ids 201-254)
Select: All 54 tokens
API: Batch request for 54 tokens
Save: Update for 54 tokens
Increment: check_jupiter = 1 for ids 201-254
Update: last_processed_id = 254
Wait: 3 seconds
```

**Cycle 4** (9 seconds):
```
Query: id > 254 → no results (reached end)
Reset: last_processed_id = 0
Query: id <= 254 AND check_jupiter < 3
Found: 254 tokens (all have check=1 now)
Select: First 100 tokens (ids 1-100)
API: Batch request for 100 tokens
Save: Update for 100 tokens
Increment: check_jupiter = 2 for ids 1-100
Update: last_processed_id = 100
Wait: 3 seconds
```

**Result**:
- All 254 tokens scanned once in **3 cycles** (9 seconds)
- Started second pass immediately (Cycle 4)
- Each token will be scanned 3 times total
- After 3 passes, all tokens have `check_jupiter = 3` (done)

## Performance Optimization

### 1. Batch API Requests

**Before** (hypothetical individual requests):
- 100 tokens × 1 API call each = 100 API calls
- Time: ~50 seconds (with rate limiting)

**After** (batch requests):
- 100 tokens ÷ 1 batch API call = 1 API call
- Time: ~0.5 seconds

**Improvement**: **100x faster** 🚀

### 2. Cursor-Based Pagination

**Before** (OFFSET-based):
```sql
-- Cycle 1
SELECT * FROM token_ids LIMIT 100 OFFSET 0;   -- Fast (scan 0-100)

-- Cycle 2
SELECT * FROM token_ids LIMIT 100 OFFSET 100; -- Slower (scan 0-200, skip 0-100)

-- Cycle 10
SELECT * FROM token_ids LIMIT 100 OFFSET 900; -- Very slow (scan 0-1000, skip 0-900)
```

**After** (cursor-based):
```sql
-- Cycle 1
SELECT * FROM token_ids WHERE id > 0 LIMIT 100;    -- Fast (scan 0-100)

-- Cycle 2
SELECT * FROM token_ids WHERE id > 100 LIMIT 100;  -- Fast (scan 100-200)

-- Cycle 10
SELECT * FROM token_ids WHERE id > 900 LIMIT 100;  -- Fast (scan 900-1000)
```

**Improvement**: **Constant O(1) time** vs. **Linear O(n) time**

### 3. Bulk Database Updates

**Before** (individual updates):
```python
for token_id in token_ids:
    await conn.execute("UPDATE token_ids SET check_jupiter = check_jupiter + 1 WHERE id = $1", token_id)
# 100 queries → ~500ms
```

**After** (bulk update):
```python
await conn.execute("UPDATE token_ids SET check_jupiter = check_jupiter + 1 WHERE id = ANY($1)", token_ids)
# 1 query → ~5ms
```

**Improvement**: **100x faster** 🚀

## Error Handling

### 1. API Errors

```python
# Retry logic with exponential backoff
async def _fetch_with_retries(self, url: str, **kwargs):
    for attempt in range(1, 4):  # 3 attempts
        try:
            async with self.session.get(url, **kwargs) as resp:
                if 200 <= resp.status < 300:
                    return {"ok": True, "json": await resp.json()}
        except Exception as e:
            backoff = 0.4 * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)
    
    return {"ok": False, "error": "Max retries exceeded"}
```

### 2. Missing Data

```python
# Handle missing fields gracefully
stats_5m = jupiter_data.get("stats", {}).get("5m", {})

# If stats don't exist → save NULL values
price_change = stats_5m.get("priceChange")  # Can be None
buy_volume = stats_5m.get("buyVolume")      # Can be None
```

### 3. Check Counter

```python
# Always increment check_jupiter, even on error
# This prevents infinite loops for tokens with missing data

# After processing batch (success or failure):
await self._increment_check_jupiter_bulk(tokens)

# After 3 attempts → token will have check_jupiter = 3
# System will stop trying and move on to other tokens
```

## Monitoring and Debugging

### Debug Logs

**Enable**:
```python
analyzer = JupiterAnalyzer(debug=True)
```

**Output**:
```
📍 Cursor position: last_id=147, collected=100 tokens
🔄 Processing batch of 100 tokens...
✅ Batch: 95/100 successfully saved

ℹ️  Немає токенів для сканування (всі check_jupiter >= 3)
ℹ️  Немає токенів для сканування (всі check_jupiter >= 3)
```

### Status Monitoring

**Check if running**:
```python
status = analyzer.get_status()
# {
#     "is_scanning": True,
#     "scan_interval": 3,
#     "batch_size": 100,
#     "last_processed_id": 147
# }
```

### Database Queries

**Check progress**:
```sql
-- Tokens waiting for scan
SELECT COUNT(*) FROM token_ids WHERE check_jupiter < 3;

-- Tokens with stats data
SELECT COUNT(*) FROM token_stats;

-- Tokens with audit data
SELECT COUNT(*) FROM token_audit;

-- Tokens that completed all scans
SELECT COUNT(*) FROM token_ids WHERE check_jupiter >= 3;
```

## Lifecycle Management

### Startup (main.py)

```python
@app.on_event("startup")
async def startup_event():
    # Initialize but DON'T start scanning
    await ensure_jupiter_analyzer()
    # Scanning starts only when user clicks START button
```

### Shutdown (main.py)

```python
@app.on_event("shutdown")
async def shutdown_event():
    # Stop analyzer if running
    if state.jupiter_analyzer and state.jupiter_analyzer.is_scanning:
        await state.jupiter_analyzer.stop_auto_scan()
    
    # Cleanup resources
    await cleanup()
```

### Singleton Pattern

```python
jupiter_analyzer_instance: Optional[JupiterAnalyzer] = None

async def get_jupiter_analyzer() -> JupiterAnalyzer:
    global jupiter_analyzer_instance
    if jupiter_analyzer_instance is None:
        jupiter_analyzer_instance = JupiterAnalyzer(debug=True)
        await jupiter_analyzer_instance.ensure_connection()
        await jupiter_analyzer_instance.ensure_session()
    return jupiter_analyzer_instance
```

## Common Use Cases

### 1. First-Time Setup

**Scenario**: New database with 200 tokens, none have Jupiter extended data

**Steps**:
1. Start auto-scan via API: `POST /api/auto-scan/start`
2. Analyzer processes 100 tokens/3s
3. Expected time: ~6 seconds for first pass (2 batches)
4. After 3 passes: ~18 seconds total (all tokens checked 3 times)

### 2. Continuous Operation

**Scenario**: Production mode with Jupiter Scanner adding new tokens

**Flow**:
1. Jupiter Scanner adds token (check_jupiter = 0, with basic data)
2. Jupiter Analyzer picks it up in next batch
3. Enriches with stats/audit/pool/tags (up to 3 attempts)
4. Process repeats for new tokens

### 3. Backfill Historical Data

**Scenario**: 1000 old tokens without extended Jupiter data

**Process**:
1. Reset check_jupiter: `UPDATE token_ids SET check_jupiter = 0`
2. Start analyzer
3. Processing time: ~30 seconds for first pass (10 batches × 3s)
4. Total time: ~90 seconds for 3 passes

## Best Practices

### 1. Cursor Management

✅ **Do**:
- Reset cursor when reaching end of DB
- Save cursor position after each batch
- Use `id` (auto-increment) for cursor

❌ **Don't**:
- Use timestamps for cursor (not unique, can change)
- Skip cursor reset (causes incomplete scans)
- Hardcode cursor values

### 2. Batch Processing

✅ **Do**:
- Process exactly 100 tokens per batch (API limit)
- Use bulk UPDATE for check_jupiter
- Handle partial batches (e.g., 47 tokens)

❌ **Don't**:
- Send > 100 tokens per API request (will fail)
- Update check_jupiter individually
- Skip batches with < 100 tokens

### 3. Error Handling

✅ **Do**:
- Always increment check_jupiter (even on error)
- Log errors for debugging
- Continue loop on errors

❌ **Don't**:
- Skip incrementing counter (causes infinite loops)
- Re-raise exceptions (breaks auto-scan loop)
- Stop scanning on single token error

## Future Improvements

1. **Adaptive Batching**
   - Adjust batch_size based on DB size
   - Smaller batches for small DBs (< 100 tokens)
   - Larger batches for big DBs (> 1000 tokens)

2. **Priority Queue**
   - Scan tokens with recent trades first
   - Skip tokens with very low activity
   - Re-scan tokens with significant price changes

3. **Data Validation**
   - Verify stats make sense (no negative volumes)
   - Check audit flags for consistency
   - Alert on suspicious patterns

4. **Performance Metrics**
   - Track API response times
   - Monitor batch processing speed
   - Alert on slow queries

5. **Smart Re-scanning**
   - Re-scan tokens after 24 hours
   - Update stats for active tokens
   - Skip dead tokens (no trades, no liquidity)

## Summary

The Jupiter Analyzer is a **critical component** for enriching token data:

- **Efficient**: 100 tokens/batch (1 API call vs. 100 calls)
- **Smart**: Cursor-based pagination (no tokens skipped, no duplicates)
- **Reliable**: Continuous scanning (runs even with no tokens)
- **Scalable**: Bulk updates, parallel processing
- **Observable**: Debug logs, status endpoint

It works seamlessly with:
- **Jupiter Scanner** (provides new tokens)
- **TokensReaderV2** (will send enriched data to frontend)
- **DexScreener Analyzer** (complementary data source)

**Key Metrics**:
- Processing speed: 100 tokens/3 seconds
- Max attempts: 3 per token
- API calls: ~20/minute (very conservative)
- Tables updated: 4 (stats, audit, first pool, tags)

