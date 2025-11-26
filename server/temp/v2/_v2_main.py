"""
main.py - Мінімальна версія головного скрипта

Включає:
- WebSockets: tokens, chart-data, balances (real-time дані)
- Trade History (збір історичних trades для токена)
- Scanner (автоматичне сканування нових токенів з Jupiter API)
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import json
from typing import Optional, List, Dict
from _v2_tokens_reader import TokensReaderV2
from _v2_chart_data_reader import ChartDataReader
from _v2_balance import BalanceV1
from _v2_trades_history import TradesHistory
from _v2_sol_price import get_sol_price_monitor
from _v3_new_tokens import get_scanner as get_jupiter_scanner
from _v3_analyzer_jupiter import get_analyzer as get_jupiter_analyzer
from config import config
# PostgreSQL pool management
from _v2_db_pool import get_db_pool, close_db_pool

app = FastAPI(title="Crypto App - Clean Version")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


class AppState:
    """Стан додатку"""
    def __init__(self):
        self.tokens_reader: Optional[TokensReaderV2] = None
        self.chart_data_reader: Optional[ChartDataReader] = None
        self.balance_monitor: Optional[BalanceV1] = None
        self.scanner = None
        self.jupiter_analyzer = None


state = AppState()


# ============================================================================
# INITIALIZATION HELPERS
# ============================================================================

async def ensure_tokens_reader():
    """Ініціалізувати Tokens Reader"""
    if state.tokens_reader is None:
        state.tokens_reader = TokensReaderV2(debug=True)
        await state.tokens_reader.ensure_connection()


async def ensure_chart_data_reader():
    """Ініціалізувати Chart Data Reader"""
    if state.chart_data_reader is None:
        state.chart_data_reader = ChartDataReader(debug=True)
        await state.chart_data_reader.ensure_connection()


async def ensure_balance_monitor():
    """Ініціалізувати Balance Monitor"""
    if state.balance_monitor is None:
        state.balance_monitor = BalanceV1()
        await state.balance_monitor.__aenter__()
        await state.balance_monitor.load_balance_data()


async def ensure_scanner():
    """Ініціалізувати Scanner"""
    if state.scanner is None:
        state.scanner = await get_jupiter_scanner()


async def ensure_jupiter_analyzer():
    """Ініціалізувати Jupiter Analyzer"""
    if state.jupiter_analyzer is None:
        state.jupiter_analyzer = await get_jupiter_analyzer()


async def cleanup():
    """Очистити ресурси при зупинці"""
    if state.tokens_reader:
        await state.tokens_reader.close()
        state.tokens_reader = None
    
    if state.chart_data_reader:
        await state.chart_data_reader.close()
        state.chart_data_reader = None
    
    if state.balance_monitor:
        await state.balance_monitor.__aexit__(None, None, None)
        state.balance_monitor = None

    if state.scanner:
        await state.scanner.close()
        state.scanner = None
    
    if state.jupiter_analyzer:
        await state.jupiter_analyzer.close()
        state.jupiter_analyzer = None


# ============================================================================
# WEBSOCKETS (3 endpoints)
# ============================================================================

@app.websocket("/ws/tokens")
async def websocket_tokens(websocket: WebSocket):
    """
    WebSocket для отримання списку токенів з БД
    Відправляє ВСІ токени при підключенні
    """
    try:
        # print("🔌 WebSocket /ws/tokens: Client connecting...")
        await websocket.accept()
        # print("✅ WebSocket /ws/tokens: Connection accepted")
        
        await ensure_tokens_reader()
        await state.tokens_reader.add_client(websocket)
        # print(f"👥 WebSocket /ws/tokens: Client added (total clients: {len(state.tokens_reader.connected_clients)})")
        
        # Відправляємо всі токени з БД при підключенні
        try:
            # print("📊 WebSocket /ws/tokens: Fetching tokens from DB...")
            result = await state.tokens_reader.get_tokens_from_db(limit=1000)
            if result["success"]:
                token_count = len(result.get('tokens', []))
                # print(f"📡 WebSocket /ws/tokens: Sending {token_count} tokens to client")
                
                # DEBUG: Виводимо перші 2 токени
                if token_count > 0:
                    # print(f"🔍 DEBUG: First token data:")
                    # first_token = result['tokens'][0]
                    # print(f"   - id: {first_token.get('id', 'MISSING')}")
                    # print(f"   - name: {first_token.get('name', 'MISSING')}")
                    # print(f"   - symbol: {first_token.get('symbol', 'MISSING')}")
                    # print(f"   - pair: {first_token.get('pair', 'MISSING')}")
                    # print(f"   - price: {first_token.get('price', 'MISSING')}")
                    
                    await websocket.send_text(json.dumps(result, ensure_ascii=False))
                else:
                    # Порожній результат
                    empty_result = {
                        "success": True,
                        "tokens": [],
                        "total_found": 0,
                        "total_count": 0
                    }
                    await websocket.send_text(json.dumps(empty_result, ensure_ascii=False))
            else:
                print(f"❌ No tokens in database: {result.get('error', 'Unknown error')}")
                error_result = {
                    "success": False,
                    "error": result.get('error', 'Unknown error'),
                    "tokens": []
                }
                await websocket.send_text(json.dumps(error_result, ensure_ascii=False))
        except Exception as e:
            import traceback
            print(f"❌ Error loading tokens: {e}")
            print(f"❌ Traceback: {traceback.format_exc()}")
            error_result = {
                "success": False,
                "error": str(e),
                "tokens": []
            }
            await websocket.send_text(json.dumps(error_result, ensure_ascii=False))
        
        # Слухаємо WebSocket
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
            
        # except Exception as e:
        # print(f"❌ WebSocket tokens error: {e}")
    finally:
        if state.tokens_reader:
            state.tokens_reader.remove_client(websocket)


@app.websocket("/ws/chart-data")
async def websocket_chart_data(websocket: WebSocket):
    """
    WebSocket для отримання chart_data (графіки trades)
    Відправляє дані кожну секунду через auto-refresh
    """
    try:
        await websocket.accept()
        await ensure_chart_data_reader()
        await state.chart_data_reader.add_client(websocket)
        
        # print(f"📊 Chart data client connected")
        
        # Слухаємо WebSocket
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
                
    except Exception as e:
        print(f"❌ WebSocket chart-data error: {e}")
    finally:
        if state.chart_data_reader:
            await state.chart_data_reader.remove_client(websocket)
            # print(f"📊 Chart data client disconnected")


@app.websocket("/ws/balances")
async def websocket_balances(websocket: WebSocket):
    """
    WebSocket для отримання балансів гаманців
    Відправляє дані при підключенні та при оновленні
    """
    try:
        await websocket.accept()
        await ensure_balance_monitor()
        
        state.balance_monitor.add_client(websocket)
        await state.balance_monitor.send_initial_data(websocket)
        
        # Слухаємо WebSocket
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
                
    except Exception as e:
        print(f"❌ WebSocket balances error: {e}")
    finally:
        if state.balance_monitor:
            state.balance_monitor.remove_client(websocket)


# ============================================================================
# SCANNER ENDPOINTS (3 endpoints)
# ============================================================================

@app.post("/api/auto-scan/start")
async def start_scanner():
    """
    🚀 Запустити автоматичне сканування: Jupiter Scanner + Jupiter Analyzer
    
    Jupiter Scanner (кожні 5 секунд):
    - Отримує 20 нових токенів з Jupiter API
    - Фільтрує дублікати (по timestamp)
    - Зберігає в БД з check_jupiter = 0
    
    Jupiter Analyzer (кожні 3 секунди):
    - Обробляє 100 токенів з check_jupiter < 3 (batch API)
    - Оновлює stats, audit, firstPool, tags
    - Збільшує check_jupiter + 1
    - Безкінечний цикл (навіть якщо немає токенів для обробки)
    """
    try:
        results = {}
        
        # 1️⃣ Запускаємо Jupiter Scanner
        await ensure_scanner()
        if state.scanner:
            jupiter_result = await state.scanner.start_auto_scan()
            results['jupiter_scanner'] = jupiter_result
        else:
            results['jupiter_scanner'] = {"success": False, "error": "Jupiter scanner not initialized"}
        
        # 2️⃣ Запускаємо Jupiter Analyzer
        await ensure_jupiter_analyzer()
        if state.jupiter_analyzer:
            jupiter_analyzer_result = await state.jupiter_analyzer.start_auto_scan()
            results['jupiter_analyzer'] = jupiter_analyzer_result
        else:
            results['jupiter_analyzer'] = {"success": False, "error": "Jupiter analyzer not initialized"}
        
        # 3️⃣ Загальний результат
        overall_success = (
            results['jupiter_scanner'].get('success', False) and 
            results['jupiter_analyzer'].get('success', False)
        )
        
        return {
            "success": overall_success,
            "message": "All scanners started" if overall_success else "Some scanners failed to start",
            "details": results
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/auto-scan/stop")
async def stop_scanner():
    """
    🛑 Зупинити автоматичне сканування: Jupiter Scanner + Jupiter Analyzer
    """
    try:
        results = {}
        
        # 1️⃣ Зупиняємо Jupiter Scanner
        if state.scanner:
            jupiter_result = await state.scanner.stop_auto_scan()
            results['jupiter_scanner'] = jupiter_result
        else:
            results['jupiter_scanner'] = {"success": False, "error": "Jupiter scanner not initialized"}
        
        # 2️⃣ Зупиняємо Jupiter Analyzer
        if state.jupiter_analyzer:
            jupiter_analyzer_result = await state.jupiter_analyzer.stop_auto_scan()
            results['jupiter_analyzer'] = jupiter_analyzer_result
        else:
            results['jupiter_analyzer'] = {"success": False, "error": "Jupiter analyzer not initialized"}
        
        return {
            "success": True,
            "message": "All scanners stopped",
            "details": results
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/scanner/status")
async def get_scanner_status():
    """
    📊 Отримати статус всіх сканерів: Jupiter Scanner + Jupiter Analyzer
    
    Повертає:
    {
        "is_scanning": bool (загальний статус),
        "details": {
            "jupiter_scanner": {
                "is_scanning": bool,
                "scan_interval": 5,
                "connected_clients": int,
                "api_url": str
            },
            "jupiter_analyzer": {
                "is_scanning": bool,
                "scan_interval": 3,
                "batch_size": 100
            }
        }
    }
    """
    try:
        status = {}
        
        # Jupiter Scanner
        await ensure_scanner()
        if state.scanner:
            status['jupiter_scanner'] = state.scanner.get_status()
        else:
            status['jupiter_scanner'] = {"is_scanning": False, "error": "Not initialized"}
        
        # Jupiter Analyzer
        await ensure_jupiter_analyzer()
        if state.jupiter_analyzer:
            status['jupiter_analyzer'] = state.jupiter_analyzer.get_status()
        else:
            status['jupiter_analyzer'] = {"is_scanning": False, "error": "Not initialized"}
        
        # Загальний статус (хоча б один працює)
        overall_scanning = (
            status['jupiter_scanner'].get('is_scanning', False) or 
            status['jupiter_analyzer'].get('is_scanning', False)
        )
        
        return {
            "is_scanning": overall_scanning,
            "details": status
        }
        
    except Exception as e:
        return {"is_scanning": False, "error": str(e)}


# ============================================================================
# TRADE HISTORY ENDPOINT (1 endpoint)
# ============================================================================

@app.get("/api/trades/get-history")
async def get_trades_history(token_pair: str = None):
    """
    📚 Отримати історичні trades для токенів
    
    Query param (опціонально):
    - token_pair: адреса торгової пари
    
    Логіка:
    1. З token_pair → збирає ВСІ trades для ОДНОГО токена (з pagination, до 5000 транзакцій)
    2. Без token_pair → збирає trades для ВСІХ токенів з БД (батчі по 10, 50 транзакцій на токен)
    
    Приклади:
    - GET /api/trades/get-history?token_pair=8En9ZeLoMwKaHJY68TjMGmqFmoBPSD1xZaQ1VS6dm2R5
    - GET /api/trades/get-history
    """
    try:
        if token_pair:
            # Для ОДНОГО токена з pagination (PostgreSQL)
            manager = TradesHistory(config.HELIUS_API_KEY, debug=True)
            try:
                # Знаходимо token_info в БД
                token_info = await manager.get_token_info_by_pair(token_pair)
                if not token_info:
                    return {
                        "success": False,
                        "message": f"Token pair {token_pair} not found in database"
                    }
                
                # Збираємо ВСІ trades з pagination (до 50 запитів = 5000 транзакцій)
                saved_count = await manager.fetch_all_trades_for_token_with_pagination(
                    token_pair, 
                    token_info['token_address'], 
                    token_info['id'], 
                    max_requests=50
                )
                
                return {
                    "success": True,
                    "message": f"Saved {saved_count} trades for token {token_info['token_address'][:8]}...",
                    "trades_count": saved_count
                }
            finally:
                await manager.close()
        else:
            # Для ВСІХ токенів (батчі по 10)
            from _v2_trades_history import fetch_all_historical_trades
            result = await fetch_all_historical_trades(debug=True)
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }


# ============================================================================
# LIFECYCLE EVENTS
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """
    Ініціалізація при запуску сервера (PostgreSQL)
    """
    # print("🚀 Starting Crypto App - PostgreSQL Version")
    
    # 🔌 Ініціалізуємо PostgreSQL connection pool
    await get_db_pool()
    # print("✅ PostgreSQL pool initialized")
    
    # Запускаємо SOL price monitor (для внутрішніх розрахунків)
    await get_sol_price_monitor(update_interval=1, debug=True)
    
    # Ініціалізуємо основні компоненти
    await ensure_tokens_reader()
    await ensure_chart_data_reader()
    await ensure_balance_monitor()
    await ensure_scanner()  # Тільки ініціалізація, БЕЗ автозапуску
    await ensure_jupiter_analyzer()  # Тільки ініціалізація, БЕЗ автозапуску
    
    # ❌ Chart Data Reader НЕ запускається автоматично
    # Запуск ТІЛЬКИ при підключенні першого WebSocket клієнта → /ws/chart-data
    # (Це економить ресурси коли немає підключених клієнтів)
    
    # ❌ Scanners НЕ запускаються автоматично
    # Запуск ТІЛЬКИ через кнопку "Start" на frontend → POST /api/auto-scan/start
    # - Jupiter Scanner V3: нові токени з API (кожні 5 сек) + збереження всіх даних
    # - Jupiter Analyzer V3: оновлення даних для токенів (кожні 3 сек, 100 токенів/батч)
    
    # print("✅ Server started successfully (PostgreSQL)")


@app.on_event("shutdown")
async def shutdown_event():
    """
    Очищення при зупинці сервера (PostgreSQL)
    """
    # print("🛑 Stopping Crypto App - PostgreSQL Version")
    
    # Зупиняємо Jupiter Scanner якщо він працює
    if state.scanner and state.scanner.is_scanning:
        await state.scanner.stop_auto_scan()
        # print("🛑 Jupiter Scanner stopped")
    
    # Зупиняємо Jupiter Analyzer якщо він працює
    if state.jupiter_analyzer and state.jupiter_analyzer.is_scanning:
        await state.jupiter_analyzer.stop_auto_scan()
        # print("🛑 Jupiter Analyzer stopped")
    
    await cleanup()
    
    # 🔌 Закриваємо PostgreSQL connection pool
    await close_db_pool()
    
    # print("✅ Server stopped successfully (PostgreSQL)")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=config.DEBUG)

