"""
Centralized Configuration for Crypto Token Scanner
All constants in one place. Edit values directly in this file.
"""
import os


class Config:
    """Main configuration class with all application constants"""
    
    # ============================================================================
    # API ENDPOINTS (public URLs - not secrets)
    # ============================================================================
    
    # Project paths
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    # Jupiter API
    JUPITER_RECENT_API = "https://lite-api.jup.ag/tokens/v2/recent"
    JUPITER_SEARCH_API = "https://lite-api.jup.ag/tokens/v2/search"
    JUPITER_PRICE_API = "https://lite-api.jup.ag/price/v3"
    
    # DexScreener API
    DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/search/"
    # DexScreener rate limits (token-pairs API): up to 300 req/min.
    # We keep a safer headroom: 240 RPM (≈4 rps)
    DEXSCREENER_MAX_RPM = 240
    # Retry window for unresolved mints (seconds) — aim to react in first seconds
    DEXSCREENER_RETRY_TTL_SEC = 5
    
    # Helius API
    HELIUS_API_BASE = "https://api.helius.xyz"
    
    # Solana RPC
    SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
    
    # ============================================================================
    # SCANNER TIMING CONFIGURATION (not secrets)
    # ============================================================================
    
    # Jupiter Scanner (new tokens discovery)
    JUPITER_SCANNER_INTERVAL = 5  # seconds between scans
    # How often to run scanner in scheduler (every N-th tick)
    JUPITER_SCANNER_TICK_INTERVAL = 6  # run scanner every 6 ticks
    
    # Jupiter Analyzer (extended data enrichment for existing tokens)
    # Каждый цикл формируем до 50 токенов (только живые токены из таблицы tokens)
    JUPITER_ANALYZER_INTERVAL = 1   # seconds between batches
    JUPITER_ANALYZER_BATCH_SIZE = 50
    # How often to run slot synchronization in scheduler (every N-th tick)
    JUPITER_SLOT_SYNC_TICK_INTERVAL = 10  # run slot sync every 10 ticks
    JUPITER_MIN_INTERVAL_SEC = 1.2  # hard minimum gap between requests (>=1 RPS)
    JUPITER_JITTER_MIN_SEC = 0.10   # small random jitter
    JUPITER_JITTER_MAX_SEC = 0.30
    JUPITER_BACKOFF_SEC = 5.0       # default backoff when 429 and no Retry-After
    JUPITER_MAX_URL_LEN = 8000      # safe URL length budget for Jupiter GET
    JUPITER_MIN_REQUEST_SIZE = 20    # do not send less than 20 when possible

    # Warm-up: skip first N tokens from the first scanner response (in-memory only)
    NEW_TOKENS_WARMUP_SKIP_ENABLED = True
    NEW_TOKENS_WARMUP_SKIP = 5
    # Hard age filter for new tokens: skip tokens older than N seconds
    NEW_TOKENS_MAX_AGE_SEC = 40
    
    # DexScreener Analyzer (trading pairs enrichment)
    DEXSCREENER_ANALYZER_INTERVAL = 1  # seconds between batches
    DEXSCREENER_ANALYZER_BATCH_SIZE = 5  # tokens per batch (DexScreener rate limit: 5 req/sec)
    
    # Frontend Data Readers
    TOKENS_REFRESH_INTERVAL = 1  # seconds (real-time updates for token list)
    CHART_REFRESH_INTERVAL = 1  # seconds (real-time updates for charts)
    # Chart data mode for WS chart_data series:
    #   'usd_second' - current behavior (avg per second of token_price_usd)
    #   'sol_minute' - SOL-denominated minute bars with robust filters (returns series of VWAPs)
    #   'dex_usd'   - Dex-like USD/second from token_metrics_seconds + trades median
    #   'mcap_series' - Market cap series from token_metrics_seconds (X=time, Y=market cap)
    CHART_DATA_MODE = 'mcap_series'
    
    # SOL Price Monitor
    SOL_PRICE_UPDATE_INTERVAL = 1  # seconds (SOL/USD price updates)
    SOL_PRICE_FALLBACK = 193.0  # USD, fallback when monitor not ready

    # SOL-minute bars settings (applied when CHART_DATA_MODE='sol_minute')
    CHART_SOL_WINDOW_SECONDS = 86400  # last 24h
    CHART_SOL_VWAP_WEIGHT_BY = 'tokens'  # 'tokens' | 'sol'
    CHART_SOL_DROP_PERCENTILE = 2.0  # drop lowest p% by volume per minute
    CHART_SOL_IQR_K = 1.5  # IQR k for price filtering in minute
    CHART_SOL_FORWARD_FILL = False  # forward-fill empty minutes with last close
    CHART_SOL_SERIES_VALUE = 'vwap'  # 'vwap' | 'close' for output series value per minute
    CHART_BAR_VERSION = 1

    # Token list behavior on start (frontend WS): show history tokens by default
    TOKENS_SHOW_HISTORY = False
    # When enabled together with TOKENS_SHOW_HISTORY, disable sorting and show
    # only historical tokens strictly in insertion order (created_at ASC)
    TOKENS_DISABLE_SORT = False

    # ============================================================================
    # AI PATTERN CLASSIFIER
    # ============================================================================
    # OLD: Use full-series (from token birth) features for pattern classification
    # NEW: Using pattern segments (0-35, 35-85, 85-125) calculated by JupiterAnalyzerV3
    # Pattern segments are stored in tokens.pattern_segment_1/2/3 with decision "buy"/"not"
    CLASSIFY_USE_FULL_SERIES = False  # Disabled - using pattern segments instead
    # Optional: disable momentum veto when using full-series logic
    DISABLE_MOMENTUM_VETO = True
    # Compare patterns on a comparable time horizon: use only the first N seconds
    # of a token's life when computing full-series features (prevents mismatch of
    # 40s tokens vs. multi-minute references). Set to 0 to use full length.
    PATTERN_REF_HORIZON_SEC = 20
    # Minimum age (seconds) for pattern classification to run
    # Pattern should be visible on chart at this age (90 seconds)
    PATTERN_CLASSIFY_MIN_SEC = 20
    # Preview/final checkpoints for pattern evaluation
    PATTERN_PREVIEW_SEC = 20    # first checkpoint (aligned with AI preview/entry forecast)
    PATTERN_FINAL_SEC = 120     # final checkpoint to decide good/bad pattern fate

    # Additional anti-trap gates
    MIN_SELL_COUNT = 3  # require at least N sells in recent window

    # ============================================================================
    # AUTO-BUY / ENTRY CONFIGURATION
    # ============================================================================
    # Global switch to enable/disable авто-покупку (быстрое тестовое отключение)
    AUTO_BUY_ENABLED = True
    # Seconds before auto-buy considers entry (token age threshold)
    # IMPORTANT: Tokens that survive past 2 minutes (120s) are more likely to be legitimate.
    # Tokens that get rug-pulled before 75-120s are scams - we avoid them by waiting.
    # 
    # Entry point for both preview forecast and auto-buy
    # Tokens must survive at least this many seconds before entry is allowed
    # Set to 150 seconds (2.5 minutes) - allows time for pattern segments calculation and liquidity check
    AUTO_BUY_ENTRY_SEC = 950  # seconds before auto-buy triggers (extended lifespan)
    AI_PREVIEW_ENTRY_SEC = 950  # seconds to assume for preview forecast (same as auto-buy entry)
    
    # Preview forecast (simulation) - shows potential profit before real entry
    # Set to False to disable preview forecast and only use real trading
    AI_PREVIEW_FORECAST_ENABLED = False  # Disabled for real trading only

    # Corridor guard to detect brutal dumps around entry/final checkpoints
    PRICE_CORRIDOR_GUARD_ENABLED = True
    PRICE_CORRIDOR_PRE_ENABLED = True
    PRICE_CORRIDOR_PRE_START = 670
    PRICE_CORRIDOR_PRE_END = 730
    PRICE_CORRIDOR_PRE_DROP_THRESHOLD = 0.18  # 18% drop within the window
    PRICE_CORRIDOR_PRE_RECOVERY_MIN = 0.50    # require at least 50% recovery
    PRICE_CORRIDOR_FINAL_ENABLED = True
    PRICE_CORRIDOR_FINAL_START = 940
    PRICE_CORRIDOR_FINAL_END = 1000  # Extended to detect post-entry drops closer to 1000s
    PRICE_CORRIDOR_FINAL_DROP_THRESHOLD = 0.20  # 20% drop
    PRICE_CORRIDOR_FINAL_RECOVERY_MIN = 0.40    # 40% recovery
    PRICE_CORRIDOR_PATTERN_PREFIX = "corridor_drop"
    ARCHIVE_MIN_ITERATIONS = 1000  # Archive only if token lived >=1000 iterations; otherwise purge

    # Post-entry drop detection: if price drops significantly after entry point (155-170s)
    # This prevents buying tokens that look good at entry but crash immediately after
    POST_ENTRY_DROP_THRESHOLD = 0.15  # 15% drop after entry point (155s) triggers "not" decision
    
    # New AI segments
    SEGMENTS_ENABLED = True
    SEGMENT_MODEL_PATH = os.path.join(BASE_DIR, "models", "pattern_segments.pkl")
    SEGMENT_UPDATE_INTERVAL = 3  # seconds

    # Liquidity withdrawal (flat price) detection
    # Uses AUTO_BUY_ENTRY_SEC as check iteration (155s)
    # Window: check last N iterations for flat/zero mcap/price (should be small, e.g., 5-10)
    LIQUIDITY_WITHDRAW_WINDOW = 8  # Check last 5 iterations for flat mcap/price
    LIQUIDITY_WITHDRAW_EQUAL_EPS = 1e-6
    
    # Default entry amount in USD (fallback when wallet.entry_amount_usd is not set)
    DEFAULT_ENTRY_AMOUNT_USD = 5.0

    # Slippage ladders for Jupiter swap (basis points)
    BUY_SLIPPAGE_LEVELS = [250, 270, 290, 310, 330, 350, 370, 390, 410, 430, 450, 470, 490, 510, 530, 550, 570, 590, 600]   # 2.5% → 6.0% шаг 0.2
    SELL_SLIPPAGE_LEVELS = [250, 270, 290, 310, 330, 350, 370, 390, 410, 430, 450, 470, 490, 510, 530, 550, 570, 590, 600]

    # RPC sender: temporarily use public/Jupiter-friendly RPC instead of Helius (when Helius quota is exhausted)
    USE_JUPITER_RPC = False  # True -> use SOLANA_RPC_URL for simulate/send; False -> use HELIUS_RPC
    
    # Trading mode: 'real' (actual wallets and transactions)
    # Legacy: 'simulation' mode removed - only real trading supported
    TRADING_MODE = 'real'  # 'real' only
    REAL_TRADING_ENABLED = True  # Always enabled (simulation removed)
    
    # ============================================================================
    # TEMPORARY: TESTING MODE (simulate transactions instead of sending to blockchain)
    # ============================================================================
    # Set to True to simulate buy/sell transactions (for testing without real money)
    # Transactions will be simulated but still saved to wallet_history
    # Set to False for real trading (actual blockchain transactions)
    SIMULATE_TRANSACTIONS = False  # TEMPORARY: Set to False for real trading
    
    # ============================================================================
    # LEGACY / DEPRECATED CONSTANTS (kept for backward compatibility, not used)
    # ============================================================================
    # Background simulation loop (legacy - disabled, simulation removed)
    SIM_BACKGROUND_BUY_ENABLED = False
    # Virtual wallets simulation (legacy - not used, simulation removed)
    VIRTUAL_WALLETS_ENABLED = False
    VIRTUAL_WALLETS_COUNT = 5  # Legacy
    VIRTUAL_WALLET_INITIAL_USD = 5.5  # Legacy
    VIRTUAL_WALLETS_RESET_ON_START = True  # Legacy
    # Legacy alias for backward compatibility
    SIM_ENTRY_ITERATION = AUTO_BUY_ENTRY_SEC
    VIRTUAL_WALLET_DEPOSIT_USD = DEFAULT_ENTRY_AMOUNT_USD

    # ============================================================================
    # TRADING / AI CONSTANTS (centralized)
    # ============================================================================
    # Target profit for simulated exits (e.g., +20%)
    TARGET_RETURN = 0.2

    # JUNO (ETA) parameters
    # Comma-separated bins in seconds, e.g. "30,40,60,90,120,180,240"
    ETA_BINS = [30, 40, 60, 90, 120, 180, 240]
    ETA_REL_CAP = 15
    ETA_MAX_CAP = 40
    ETA_P_THRESHOLD = 0.6
    ETA_MODEL_PATH = os.path.join('models', 'eta_tcn.pt')
    ETA_MAX_TOKEN_AGE_SEC = 1000

    # Token candidate filters (live)
    MIN_TX_COUNT = 100  # minimal total transactions in window (auto-entry gate)
    MIN_SELL_SHARE = 0.20  # minimal sell share (alt to ratio)
    MIN_TX_CHECK_ITER = 100  # iteration when to start checking MIN_TX_COUNT/MIN_SELL_SHARE (early validation before AUTO_BUY_ENTRY_SEC)
    MAX_BUY_TO_SELL_RATIO = 8.0  # alternative criterion

    # Minimum iteration before real auto-buy can execute (separate from holder check)
    AUTO_BUY_TRIGGER_ITER = 510

    # Holder momentum filter: require strong holder growth before auto-entry
    HOLDER_MOMENTUM_CHECK_ITER = 500          # iteration to evaluate holder growth (seconds)
    HOLDER_MOMENTUM_MIN_AT_CHECK = 500        # minimal holders at CHECK_ITER
    HOLDER_MOMENTUM_MIN_AT_400 = 350          # minimal holders at 400th iteration
    HOLDER_MOMENTUM_MIN_DELTA100 = 120        # minimal growth within last 100 iterations (e.g., 400→500)
    HOLDER_MOMENTUM_MIN_RATE = 0.8            # minimal average growth (holders per second) within evaluation window
    HOLDER_MOMENTUM_RATE_LOOKBACK = 200       # lookback window for average growth (e.g., from 300→500)

    # Entry confirmation & gap/stall guard (anti "gap then flat" entry)
    ENTRY_CONFIRM_SEC = 15  # seconds after entry point to confirm momentum
    GAP_DROP_PCT = 0.30       # 30% drop from early avg to late avg -> bad
    POST_STALL_VOL_PCT = 0.02  # late std/mean < 2% => stall
    # Rug / drained-liquidity guard: if last N consecutive seconds have usd_price NULL/0,
    # consider token dead. If no entry happened yet → hard delete token (+metrics/trades).
    # If entry happened → finalize position at price 0 and archive token.
    ZERO_TAIL_CONSEC_SEC = 120  # нужно 120 последовательных пустых итераций, чтобы признать токен «мертвым»
    # Do NOT trigger zero-liquidity guard until token прожил минимум столько секунд.
    CLEANER_LOW_HOLDER_ITER_THRESHOLD = 500  # минимальное количество итераций для low-holder архивации
    CLEANER_LOW_HOLDER_MIN_COUNT = 300       # минимальное число холдеров для long-lived токена
    # Frozen price detector: if последние N итераций цена не менялась → считаем токен замороженным
    FROZEN_PRICE_CONSEC_SEC = 120  # количество последних итераций, где цена должна быть одинаковой
    FROZEN_PRICE_EQUAL_EPS = 1e-10  # допуск при сравнении цены
    # Bad pattern guard: archive tokens with bad patterns (black_hole, flatliner, etc.)
    # after this many iterations if no entry. Saves Jupiter API requests on clearly worthless tokens.
    # Keep 100 iterations for future ML training data.
    # Set to same value as CLEANER_NO_ENTRY_ITERS (1 hour) to allow viewing patterns without entry
    BAD_PATTERN_HISTORY_READY_ITERS = 14400  # 1 hour - same as CLEANER_NO_ENTRY_ITERS
    # Minimal pattern score to allow entering a token (0..100). Only tokens classified
    # with score >= this threshold are eligible for simulated buy.
    PATTERN_MIN_SCORE = 80

    # ============================================================================
    # LEGACY: SIMULATED FEES (deprecated - simulation removed)
    # ============================================================================
    # These constants are kept for backward compatibility but not used in real trading
    # Real trading uses actual Jupiter API fees and blockchain transaction fees
    SIM_FEES_SLIPPAGE_BPS = 250  # Legacy - not used
    SIM_FEES_JUPITER_BPS = 0      # Legacy - not used
    SIM_FEES_SOL_PER_TX = 0.000005  # Legacy - not used

    # ============================================================================
    # HONEYPOT HEURISTICS (pre-buy safety check)
    # ============================================================================
    # Window (seconds) for buy/sell counts aggregation
    HONEYPOT_WINDOW_SEC = 120
    # If there are at least this many buys and sells are at or below HONEYPOT_MAX_SELLS → flag
    HONEYPOT_MIN_BUYS = 30
    HONEYPOT_MAX_SELLS = 0
    # Alternative share-based rule: sells/(buys+sells) must be at least this share
    HONEYPOT_MIN_SELL_SHARE = 0.05
    # Manual override: allow force-buy even if honeypot check triggers
    HONEYPOT_BLOCK_FORCE_BUY = False
    # If freeze authority is present (freeze_authority_disabled = false), consider risky
    HONEYPOT_FLAG_FREEZE_AUTH = True
    # Do not block force-buy by default; only annotate
    HONEYPOT_BLOCK_BUY = False

    # Force-buy helpers
    FORCE_BUY_PRICE_LOOKBACK_SEC = 15

    # Live Trades (Helius recent transactions)
    LIVE_TRADES_RATE_LIMIT_PER_SEC = 7  # requests per second (~0.143s per request)
    LIVE_TRADES_BATCH_SIZE = 10  # tokens per tick (parallel processing with rate limiting)
    LIVE_TRADES_API_LIMIT = 100  # transactions per token request
    LIVE_TRADES_LOOP_INTERVAL = 1.0  # seconds between batches
    # Incremental fetch tuning (API economy)
    LIVE_TRADES_PAGE_LIMIT_SMALL = 20  # small page for incremental reads
    LIVE_TRADES_MAX_PAGES_PER_TICK = 5  # max additional pages per token per tick
    # Slot matching window for synchronizer (Jupiter vs Helius slots)
    LIVE_TRADES_SLOT_MATCH_WINDOW = 10  # slots ±N to search around jupiter_slot
    # Archive token after ~30s without new trades.
    # With 7 rps and parallel processing of 10 tokens per loop, one loop ≈ 1s → 30 ticks ≈ 30s.
    # Empty-tick streak to consider token inactive (only if no entry yet)
    LIVE_TRADES_EMPTY_STREAK_THRESHOLD = 50  # more conservative: ~50s without trades
    LIVE_TRADES_MAX_RETRIES = 3  # retries for 429/temporary errors
    LIVE_TRADES_RETRY_BASE_DELAY = 0.5  # seconds (exponential backoff)
    # Enable marking by empty-streak (set to False to activate 30s rule above)
    LIVE_TRADES_DISABLE_EMPTY_STREAK = False

    # Scanner pause: when True, сканер новых токенов ставится на паузу, если есть токены,
    # привязанные к кошелькам (wallet_id IS NOT NULL). Возобновляется автоматически,
    # когда привязок не остаётся.
    PAUSE_SCANNER_WHEN_WALLET_BOUND = True

    # ============================================================================
    # ORPHAN TOKENS CLEANER (no valid pair) – background, tied to Scheduler
    # ============================================================================
    CLEANER_ENABLED = True
    CLEANER_INTERVAL_SEC = 4    # run every 4s
    CLEANER_OLDER_SEC = 2       # consider orphan if 4 iterations (seconds) without valid pair
    CLEANER_BATCH_LIMIT = 200   # max tokens to purge per pass
    # Do NOT remove tokens with a valid pair until they live at least this many seconds without entry
    # Environment override removed – use explicit config constants
    CLEANER_NO_ENTRY_AGE_SEC = 14400  # 1 hour - allow viewing patterns without entry
    # And do not remove just by iteration count unless explicitly set (>0)
    CLEANER_NO_ENTRY_ITERS = 14400  # 1 hour - allow viewing patterns without entry

    # ============================================================================
    # NEW TOKENS INSERT CAP (temporary)
    # ============================================================================
    # When enabled, scanner inserts at most N tokens into DB. Further incoming
    # tokens are only updated if they already exist — new ones are ignored.
    NEW_TOKENS_INSERT_CAP_ENABLED = False  # Disabled to allow unlimited new tokens
    NEW_TOKENS_INSERT_CAP = 1000  # Increased cap if re-enabled
    # Prefer newest tokens in LiveTrades batch selection (test mode without using test_latest_n API)
    LIVE_TRADES_PREFER_LATEST = False

    # ============================================================================
    # METRICS SNAPSHOTS (seconds) - store per-second Jupiter metrics for ML
    # ============================================================================
    METRICS_SECONDS_ENABLED = True
    METRICS_SECONDS_UPSERT = True  # upsert on (token_id, ts)
    # Metrics ticker was removed

    # Trades History (Helius pagination)
    HISTORY_HELIUS_LIMIT = 100  # max tx per request
    HISTORY_PAGINATION_DELAY = 0.25  # seconds between pagination requests
    
    # ============================================================================
    # DATABASE CONFIGURATION - PostgreSQL (not secrets except password)
    # ============================================================================
    
    DB_HOST = "localhost"
    DB_NAME = "crypto_db"
    DB_USER = "postgres"
    DB_PORT = 5433
    DB_PASSWORD = ""  # No password for local PostgreSQL
    DB_MIN_POOL_SIZE = 10  # Minimum connections in pool
    DB_MAX_POOL_SIZE = 50  # Maximum connections in pool
    
    # ============================================================================
    # SERVER CONFIGURATION (not secrets)
    # ============================================================================
    
    PORT = 8002  # Backend server port
    HOST = "0.0.0.0"  # Listen on all interfaces
    DEBUG = True  # Debug mode
    
    # AI identity
    AI_NAME = 'JUNO'
    
    # CORS - allowed origins
    ALLOWED_ORIGINS = [
        "http://localhost:8001",  # Next.js frontend (local)
        "http://127.0.0.1:8001",  # Next.js frontend (local IP)
    ]
    
    # ============================================================================
    # API KEYS (SECRETS - set directly here or leave empty if not needed)
    # ============================================================================
    
    HELIUS_API_KEY = '51aa6a7b-3fb6-42b9-840d-01271b7d9d91'  # Set your Helius API key here
    _HELIUS_RPC_FROM_ENV = os.environ.get("HELIUS_RPC_URL", "").strip()
    if _HELIUS_RPC_FROM_ENV:
        HELIUS_RPC_URL = _HELIUS_RPC_FROM_ENV
    elif HELIUS_API_KEY:
        HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    else:
        # Fall back to public Solana RPC if no Helius key is provided
        HELIUS_RPC_URL = SOLANA_RPC_URL
    
    # Optional API keys (if needed in future)
    BIRDEYE_API_KEY = ''  # Set your Birdeye API key here if needed
    SOLSCAN_API_KEY = ''  # Set your Solscan API key here if needed
    
    # ============================================================================
    # FILE PATHS (not secrets)
    # ============================================================================
    
    WALLET_KEYS_FILE = "keys.json"  # Wallet keys for balance monitoring
    
    # ============================================================================
    # BACKWARD COMPATIBILITY
    # ============================================================================
    
    @property
    def rpc_url(self):
        """Get Solana RPC URL (backward compatibility)"""
        return self.SOLANA_RPC_URL


# Create global config instance
config = Config()

# Backward compatibility exports
PORT = config.PORT
DEBUG = config.DEBUG
HOST = config.HOST
ALLOWED_ORIGINS = config.ALLOWED_ORIGINS
