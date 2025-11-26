from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import json
import aiosqlite
from datetime import datetime
from typing import Optional, Dict, Any, List
from _v1_new_tokens_jupiter_async import AsyncJupiterScanner, AsyncTokenDatabase
from _v2_new_tokens import JupiterScannerV2
from _v2_tokens_reader import TokensReaderV2
from _v1_analyzer_async_v2 import get_analyzer  # V2 - оптимізована версія
from _v2_balance import BalanceV1
from _v2_analyzer_dexscreener import get_dexscreener_analyzer
from _v2_sol_price import get_sol_price_monitor, get_current_sol_price
from _v2_live_trades import HeliusTradesReporter
from _v2_trades_history import fetch_all_historical_trades, fetch_trades_for_single_token
from _v2_chart_data_reader import ChartDataReader
from config import config

app = FastAPI(title="Jupiter Token Scanner")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

class AppState:
    def __init__(self):
        self.scanner: Optional[AsyncJupiterScanner] = None
        self.scanner_v2: Optional[JupiterScannerV2] = None  # Новий сканер
        self.tokens_reader: Optional[TokensReaderV2] = None  # Новий читач
        self.analyzer_task: Optional[asyncio.Task] = None
        self.auto_scan_task: Optional[asyncio.Task] = None
        self.auto_scan_interval: int = 5
        self.is_scanning: bool = False
        self.connected_clients: List[WebSocket] = []
        
        # Balance monitoring - тепер в окремому класі
        self.balance_monitor: Optional[BalanceV1] = None
        
        # History Scanner - збір історичних trades
        self.history_scanner_task: Optional[asyncio.Task] = None
        self.is_history_scanning: bool = False
        self.history_batch_offset: int = 0  # Offset для циклічного проходу по токенах
        
        # Chart Data Reader - читає trades з БД та генерує графіки
        self.chart_data_reader: Optional[ChartDataReader] = None

db_instance = AsyncTokenDatabase()
state = AppState()

async def ensure_scanner():
    if state.scanner is None:
        state.scanner = AsyncJupiterScanner(db_instance, debug=True)
        await state.scanner.ensure_session()

async def ensure_scanner_v2():
    if state.scanner_v2 is None:
        state.scanner_v2 = JupiterScannerV2(debug=True)
        await state.scanner_v2.ensure_connection()

async def ensure_tokens_reader():
    if state.tokens_reader is None:
        state.tokens_reader = TokensReaderV2(debug=True)
        await state.tokens_reader.ensure_connection()

async def ensure_chart_data_reader():
    if state.chart_data_reader is None:
        state.chart_data_reader = ChartDataReader(debug=True)
        await state.chart_data_reader.ensure_connection()

async def cleanup_scanner():
    if state.scanner:
        await state.scanner.close()
        state.scanner = None
    
    if state.scanner_v2:
        await state.scanner_v2.close()
        state.scanner_v2 = None
    
    if state.tokens_reader:
        await state.tokens_reader.close()
        state.tokens_reader = None

# V2: analyzer не потребує окремого task

async def ensure_balance_monitor():
    if state.balance_monitor is None:
        state.balance_monitor = BalanceV1()
        await state.balance_monitor.__aenter__()
        # Автоматично завантажуємо дані при створенні
        await state.balance_monitor.load_balance_data()

async def cleanup_balance_monitor():
    if state.balance_monitor:
        await state.balance_monitor.__aexit__(None, None, None)
        state.balance_monitor = None

async def history_scanner_loop():
    """
    History Scanner - збирає історичні trades для всіх токенів з БД.
    Обробляє по 10 токенів кожну секунду циклічно.
    """
    helius_reporter = HeliusTradesReporter(helius_api_key=config.HELIUS_API_KEY, debug=True)
    await helius_reporter.ensure_session()
    await helius_reporter.ensure_connection()
    
    print("🕐 History Scanner started")
    
    try:
        while state.is_history_scanning:
            try:
                # 1. Отримати всі токени з БД
                conn = await aiosqlite.connect("db/tokens.db")
                cursor = await conn.execute("SELECT COUNT(*) FROM token_ids")
                total_count = (await cursor.fetchone())[0]
                
                if total_count == 0:
                    print("⚠️ No tokens in database")
                    await conn.close()
                    await asyncio.sleep(1)
                    continue
                
                # 2. Отримати batch (10 токенів) з поточного offset
                batch_size = 10
                cursor = await conn.execute("""
                    SELECT id, token_address, token_pair 
                    FROM token_ids 
                    LIMIT ? OFFSET ?
                """, (batch_size, state.history_batch_offset))
                tokens_batch = await cursor.fetchall()
                await conn.close()
                
                if not tokens_batch:
                    # Досягнуто кінця, починаємо знову
                    state.history_batch_offset = 0
                    print(f"🔄 Reached end of tokens, restarting from beginning")
                    await asyncio.sleep(1)
                    continue
                
                print(f"📊 History Scanner: Processing {len(tokens_batch)} tokens (offset: {state.history_batch_offset}/{total_count})")
                
                # 3. Обробити кожен токен з batch
                for token_row in tokens_batch:
                    token_id, token_address, token_pair = token_row
                    
                    # ВАЖЛИВО: Helius працює ТІЛЬКИ з token_pair (trading pair), не з token_address (Token Mint)
                    if not token_pair:
                        print(f"⚠️ History: Skipping token {token_address[:8]}... - no trading pair")
                        continue
                    
                    try:
                        # Fetch trades з Helius використовуючи token_pair
                        await helius_reporter.get_trades(token_pair)
                        print(f"✅ History: Processed token {token_address[:8]}... (pair: {token_pair[:8]}...)")
                    except Exception as e:
                        print(f"❌ History: Error processing token {token_address[:8]}... (pair: {token_pair[:8]}...): {e}")
                
                # 4. Збільшити offset для наступного циклу
                state.history_batch_offset += batch_size
                
                # 5. Якщо offset досяг кінця, почати знову
                if state.history_batch_offset >= total_count:
                    state.history_batch_offset = 0
                    print(f"🔄 Completed full cycle, restarting from beginning")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ History Scanner error: {e}")
            
            await asyncio.sleep(1)  # Кожну секунду
    
    finally:
        if helius_reporter.session:
            await helius_reporter.session.close()
        if helius_reporter.conn:
            await helius_reporter.conn.close()
        print("🛑 History Scanner stopped")

async def start_history_scanner():
    """Запуск History Scanner"""
    if state.is_history_scanning:
        return {"success": False, "message": "History scanner already running"}
    
    state.is_history_scanning = True
    state.history_batch_offset = 0  # Reset offset
    state.history_scanner_task = asyncio.create_task(history_scanner_loop())
    
    return {"success": True, "message": "History scanner started"}

async def stop_history_scanner():
    """Зупинка History Scanner"""
    if not state.is_history_scanning:
        return {"success": False, "message": "History scanner not running"}
    
    state.is_history_scanning = False
    
    if state.history_scanner_task:
        state.history_scanner_task.cancel()
        try:
            await state.history_scanner_task
        except asyncio.CancelledError:
            pass
        state.history_scanner_task = None
    
    return {"success": True, "message": "History scanner stopped"}

def get_history_scanner_status():
    """Отримати статус History Scanner"""
    return {
        "is_scanning": state.is_history_scanning,
        "current_offset": state.history_batch_offset,
        "batch_size": 10
    }

async def broadcast_to_clients(data):
    if not state.connected_clients:
        print(f"📡 No connected clients to broadcast to")
        return
        
    json_data = json.dumps(data, ensure_ascii=False)
    
    data_type = "unknown"
    if isinstance(data, list):
        data_type = f"tokens_update ({len(data)} tokens)"
    elif isinstance(data, dict):
        data_type = data.get('type', 'unknown')
    
    print(f"📡 Broadcasting to {len(state.connected_clients)} clients: {data_type}")
    
    disconnected_clients = []
    for client in state.connected_clients:
        try:
            await client.send_text(json_data)
            await asyncio.sleep(0.001)
        except Exception as e:
            print(f"❌ Error sending to client: {e}")
            disconnected_clients.append(client)
    
    for client in disconnected_clients:
        state.connected_clients.remove(client)
    
    print(f"✅ Broadcast completed to {len(state.connected_clients)} clients")

async def auto_scan():
    while state.is_scanning:
        try:
            await ensure_scanner()
            # V2: аналізатор викликається окремо через API
            
            if not state.scanner:
                await asyncio.sleep(state.auto_scan_interval)
                continue
                
            result = await state.scanner.get_tokens_from_api(limit=20)
            
            if result["success"]:
                await broadcast_to_clients(result)
                
        except Exception as e:
            pass
            
        await asyncio.sleep(state.auto_scan_interval)


@app.websocket("/ws/tokens")
async def websocket_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
        
        # Забезпечуємо, що читач ініціалізований
        await ensure_tokens_reader()
        
        # Додаємо клієнта до читача
        await state.tokens_reader.add_client(websocket)
        
        # Відправляємо ВСІ токени з БД при підключенні
        try:
            result = await state.tokens_reader.get_tokens_from_db(limit=1000)
            if result["success"]:
                token_count = len(result.get('tokens', []))
                print(f"📡 Sending {token_count} tokens from DB to client")
                if token_count > 0:
                    await websocket.send_text(json.dumps(result, ensure_ascii=False))
                else:
                    # Якщо токенів немає, відправляємо порожній результат
                    empty_result = {
                        "success": True,
                        "tokens": [],
                        "total_found": 0,
                        "total_count": 0,
                        "scan_time": datetime.now().isoformat()
                    }
                    await websocket.send_text(json.dumps(empty_result, ensure_ascii=False))
            else:
                print(f"❌ No tokens found in database: {result.get('error', 'Unknown error')}")
                # Відправляємо помилку клієнту
                error_result = {
                    "success": False,
                    "error": result.get('error', 'Unknown error'),
                    "tokens": []
                }
                await websocket.send_text(json.dumps(error_result, ensure_ascii=False))
        except Exception as e:
            print(f"❌ Error loading tokens from DB: {e}")
            # Відправляємо помилку клієнту
            error_result = {
                "success": False,
                "error": str(e),
                "tokens": []
            }
            await websocket.send_text(json.dumps(error_result, ensure_ascii=False))
        
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
                
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
    finally:
        # Видаляємо клієнта з читача
        if state.tokens_reader:
            state.tokens_reader.remove_client(websocket)

@app.websocket("/ws/chart-data")
async def chart_data_websocket(websocket: WebSocket):
    """WebSocket для chart_data - графіки trades"""
    try:
        await websocket.accept()
        
        await ensure_chart_data_reader()
        
        await state.chart_data_reader.add_client(websocket)
        
        print(f"📊 Chart data client connected")
        
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
    
    except Exception as e:
        print(f"❌ Chart WebSocket error: {e}")
    finally:
        if state.chart_data_reader:
            await state.chart_data_reader.remove_client(websocket)
            print(f"📊 Chart data client disconnected")

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
        
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
                
    except Exception as e:
        print(f"❌ Balance WebSocket error: {e}")
    finally:
        # Видаляємо клієнта з баланс монітора
        if state.balance_monitor:
            state.balance_monitor.remove_client(websocket)

@app.on_event("startup")
async def startup_event():
    # Запускаємо SOL price monitor (кожну секунду)
    await get_sol_price_monitor(update_interval=1, debug=True)
    
    await ensure_scanner()
    # Завантажуємо дані балансу при запуску (тепер автоматично в ensure_balance_monitor)
    await ensure_balance_monitor()
    # Ініціалізуємо читач токенів
    await ensure_tokens_reader()

@app.on_event("shutdown")
async def shutdown_event():
    state.is_scanning = False
    
    if state.auto_scan_task:
        state.auto_scan_task.cancel()
        try:
            await state.auto_scan_task
        except asyncio.CancelledError:
            pass
    
    # Зупинити History Scanner
    if state.is_history_scanning:
        await stop_history_scanner()
    
    await cleanup_scanner()
    await cleanup_balance_monitor()

@app.post("/api/auto-scan/start")
async def start_auto_scan():
    try:
        await ensure_scanner_v2()
        if state.scanner_v2:
            return await state.scanner_v2.start_auto_scan()
        else:
            return {"success": False, "error": "Scanner V2 not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/auto-scan/stop")
async def stop_auto_scan():
    try:
        if state.scanner_v2:
            return await state.scanner_v2.stop_auto_scan()
        else:
            return {"success": False, "error": "Scanner V2 not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/scanner/status")
async def get_scanner_status():
    try:
        await ensure_scanner_v2()
        if state.scanner_v2:
            return state.scanner_v2.get_status()
        else:
            return {"is_scanning": False, "error": "Scanner V2 not initialized"}
    except Exception as e:
        return {"is_scanning": False, "error": str(e)}

@app.get("/api/tokens")
async def get_tokens(limit: int = 100, offset: int = 0):
    """Отримує токени з БД"""
    try:
        await ensure_tokens_reader()
        if state.tokens_reader:
            return await state.tokens_reader.get_tokens_from_db(limit=limit, offset=offset)
        else:
            return {"success": False, "error": "Tokens reader not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/tokens/{token_address}")
async def get_token_by_address(token_address: str):
    """Отримує конкретний токен за адресою"""
    try:
        await ensure_tokens_reader()
        if state.tokens_reader:
            return await state.tokens_reader.get_token_by_address(token_address)
        else:
            return {"success": False, "error": "Tokens reader not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/tokens/search/{query}")
async def search_tokens(query: str, limit: int = 50):
    """Пошук токенів за назвою або символом"""
    try:
        await ensure_tokens_reader()
        if state.tokens_reader:
            return await state.tokens_reader.search_tokens(query, limit=limit)
        else:
            return {"success": False, "error": "Tokens reader not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/balance/refresh")
async def refresh_balance():
    """Оновити дані балансу вручну"""
    try:
        await ensure_balance_monitor()
        if state.balance_monitor:
            return await state.balance_monitor.refresh_balance()
        else:
            return {"success": False, "error": "Balance monitor not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/history-scanner/start")
async def api_start_history_scanner():
    """Запустити History Scanner для збору історичних trades"""
    try:
        result = await start_history_scanner()
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/history-scanner/stop")
async def api_stop_history_scanner():
    """Зупинити History Scanner"""
    try:
        result = await stop_history_scanner()
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/history-scanner/status")
async def api_history_scanner_status():
    """Отримати статус History Scanner"""
    try:
        return get_history_scanner_status()
    except Exception as e:
        return {"is_scanning": False, "error": str(e)}

@app.get("/api/balance/status")
async def get_balance_status():
    try:
        await ensure_balance_monitor()
        if state.balance_monitor:
            return state.balance_monitor.get_status()
        else:
            return {"has_data": False, "wallets_count": 0, "connected_clients": 0}
    except Exception as e:
        return {"has_data": False, "wallets_count": 0, "connected_clients": 0, "error": str(e)}

@app.post("/api/analyzer/test-single")
async def test_analyzer_single_token(request: dict):
    """Тестування аналізатора з одним токеном"""
    try:
        token_address = request.get("token_address")
        if not token_address:
            return {"success": False, "error": "token_address is required"}
        
        print(f"🔍 Testing analyzer with token: {token_address}")
        
        # Отримуємо аналізатор
        analyzer = await get_analyzer()
        await analyzer.ensure_connection()
        
        # Додаємо токен до черги аналізу
        await analyzer.add_tokens_to_analysis([token_address])
        
        # Запускаємо один цикл аналізу
        await analyzer.run_analysis_cycle()
        
        # Отримуємо результат з бази даних
        token_id = await analyzer._get_token_id_by_address(token_address)
        if token_id:
            updated_token = await analyzer._get_updated_token_data(token_id)
            return {
                "success": True,
                "message": f"Analysis completed for {token_address}",
                "token_id": token_id,
                "token_data": updated_token
            }
        else:
            return {
                "success": False,
                "error": f"Token {token_address} not found in database"
            }
            
    except Exception as e:
        print(f"❌ Error in test_analyzer_single_token: {e}")
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/api/analyzer/test-detailed")
async def test_analyzer_detailed(request: dict):
    """Детальне тестування аналізатора з повним виводом всіх даних"""
    try:
        token_address = request.get("token_address")
        if not token_address:
            return {"success": False, "error": "token_address is required"}
        
        print(f"\n{'='*80}")
        print(f"🔍 ДЕТАЛЬНЕ ТЕСТУВАННЯ АНАЛІЗАТОРА")
        print(f"Token: {token_address}")
        print(f"{'='*80}\n")
        
        # Отримуємо аналізатор
        analyzer = await get_analyzer()
        await analyzer.ensure_connection()
        await analyzer.ensure_session()
        
        # Крок 1: Перевіряємо, чи токен є в базі даних
        print("📊 Крок 1: Перевірка наявності токена в БД...")
        token_id = await analyzer._get_token_id_by_address(token_address)
        if not token_id:
            print(f"⚠️ Token {token_address} не знайдено в БД, створюємо...")
            # Створюємо токен в БД
            await db_instance.ensure_connection()
            async with db_instance.db_lock:
                await db_instance.conn.execute("""
                    INSERT OR IGNORE INTO token_ids (token_address)
                    VALUES (?)
                """, (token_address,))
                await db_instance.conn.commit()
            token_id = await analyzer._get_token_id_by_address(token_address)
            print(f"✅ Token створено з ID: {token_id}")
        else:
            print(f"✅ Token знайдено з ID: {token_id}")
        
        # Крок 2: Отримуємо Jupiter дані
        print(f"\n📊 Крок 2: Отримання Jupiter даних...")
        jupiter_data = await analyzer._get_jupiter_data(token_address)
        print(f"Jupiter data keys: {list(jupiter_data.keys()) if isinstance(jupiter_data, dict) else type(jupiter_data)}")
        if isinstance(jupiter_data, list) and jupiter_data:
            print(f"✅ Jupiter повернув {len(jupiter_data)} токенів")
            print(f"   Name: {jupiter_data[0].get('name', 'N/A')}")
            print(f"   Symbol: {jupiter_data[0].get('symbol', 'N/A')}")
            print(f"   Dev: {jupiter_data[0].get('dev', 'N/A')}")
        
        # Крок 3: Отримуємо DexScreener дані
        print(f"\n📊 Крок 3: Отримання DexScreener даних...")
        dexscreener_data = await analyzer._get_dexscreener_data(token_address)
        print(f"DexScreener data keys: {list(dexscreener_data.keys()) if isinstance(dexscreener_data, dict) else type(dexscreener_data)}")
        if isinstance(dexscreener_data, dict) and 'pairs' in dexscreener_data:
            pairs = dexscreener_data.get('pairs', [])
            print(f"✅ DexScreener повернув {len(pairs)} пар")
            if pairs:
                pair = pairs[0]
                print(f"   DexId: {pair.get('dexId', 'N/A')}")
                print(f"   PairAddress: {pair.get('pairAddress', 'N/A')}")
                print(f"   Price USD: {pair.get('priceUsd', 'N/A')}")
                print(f"   Liquidity: {pair.get('liquidity', {}).get('usd', 'N/A')}")
        
        # Крок 4: Отримуємо Solana RPC дані
        print(f"\n📊 Крок 4: Отримання Solana RPC даних...")
        solana_rpc_data = await analyzer._get_solana_rpc_data(token_address)
        print(f"Solana RPC data keys: {list(solana_rpc_data.keys()) if isinstance(solana_rpc_data, dict) else type(solana_rpc_data)}")
        if isinstance(solana_rpc_data, dict):
            if 'token_supply' in solana_rpc_data and solana_rpc_data['token_supply']:
                supply = solana_rpc_data['token_supply'].get('value', {})
                print(f"✅ Token Supply: {supply.get('uiAmountString', 'N/A')}")
            if 'token_metadata' in solana_rpc_data and solana_rpc_data['token_metadata']:
                metadata = solana_rpc_data['token_metadata'].get('value', {})
                parsed = metadata.get('data', {}).get('parsed', {}).get('info', {})
                print(f"✅ Decimals: {parsed.get('decimals', 'N/A')}")
                print(f"   Mint Authority: {parsed.get('mintAuthority', 'N/A')}")
        
        # Крок 5: Honeypot check
        print(f"\n📊 Крок 5: Honeypot перевірка...")
        honeypot_check = await analyzer._honeypot_with_fallback(token_address, dexscreener_data, solana_rpc_data)
        print(f"Honeypot check result:")
        print(f"   Checked by: {honeypot_check.get('checked_by', [])}")
        print(f"   Buy possible: {honeypot_check.get('buy_possible')}")
        print(f"   Sell possible: {honeypot_check.get('sell_possible')}")
        print(f"   Is Honeypot: {honeypot_check.get('honeypot')}")
        print(f"   Reasons: {honeypot_check.get('reasons', [])}")
        
        # Крок 6: Збереження аналізу
        print(f"\n📊 Крок 6: Збереження аналізу в БД...")
        analysis = {
            'token_address': token_address,
            'timestamp': datetime.now().isoformat(),
            'analysis_time': '0.00s',
            'iteration': 1,
            'raw_data': {
                'jupiter': jupiter_data,
                'dexscreener': dexscreener_data,
                'solana_rpc': solana_rpc_data
            },
            'security': {
                'honeypot_check': honeypot_check,
                'lp_owner': None,
                'dev_address': analyzer._extract_dev_from_jupiter(jupiter_data)
            }
        }
        
        save_result = await analyzer.save_analysis(analysis)
        print(f"{'✅' if save_result else '❌'} Збереження: {'успішне' if save_result else 'помилка'}")
        
        # Крок 7: Отримання збережених даних
        print(f"\n📊 Крок 7: Отримання збережених даних з БД...")
        updated_token = await analyzer._get_updated_token_data(token_id)
        
        print(f"\n{'='*80}")
        print(f"✅ ТЕСТУВАННЯ ЗАВЕРШЕНО")
        print(f"{'='*80}\n")
        
        return {
            "success": True,
            "message": f"Detailed analysis completed for {token_address}",
            "token_id": token_id,
            "steps": {
                "1_token_found": bool(token_id),
                "2_jupiter_data": bool(jupiter_data),
                "3_dexscreener_data": bool(dexscreener_data and dexscreener_data.get('pairs')),
                "4_solana_rpc_data": bool(solana_rpc_data),
                "5_honeypot_check": honeypot_check,
                "6_save_result": save_result,
                "7_updated_token": updated_token
            },
            "raw_data": {
                "jupiter": jupiter_data,
                "dexscreener": dexscreener_data,
                "solana_rpc": solana_rpc_data
            }
        }
        
    except Exception as e:
        import traceback
        print(f"❌ Error in test_analyzer_detailed: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }

@app.get("/api/analyzer/db-stats")
async def get_analyzer_db_stats():
    """Отримати статистику бази даних аналізатора"""
    try:
        analyzer = await get_analyzer()
        await analyzer.ensure_connection()
        
        stats = {}
        
        # Підраховуємо кількість записів в кожній таблиці
        tables = [
            'token_ids',
            'tokens',
            'dexscreener_pairs',
            'dexscreener_base_token',
            'dexscreener_quote_token',
            'dexscreener_txns',
            'dexscreener_volume',
            'dexscreener_price_change',
            'dexscreener_liquidity',
            'solana_token_supply',
            'solana_token_metadata',
            'solana_recent_signatures',
            'solana_dev_activity',
            'solana_largest_accounts'
        ]
        
        for table in tables:
            try:
                cursor = await analyzer.conn.execute(f"SELECT COUNT(*) FROM {table}")
                count = await cursor.fetchone()
                stats[table] = count[0] if count else 0
            except Exception as e:
                stats[table] = f"Error: {str(e)}"
        
        # Отримуємо токени, які потребують аналізу
        cursor = await analyzer.conn.execute("""
            SELECT COUNT(*) FROM token_ids 
            WHERE token_pair IS NULL OR token_pair = 'Analyzing...'
        """)
        needs_analysis = await cursor.fetchone()
        stats['tokens_needing_analysis'] = needs_analysis[0] if needs_analysis else 0
        
        # Отримуємо токени з повним аналізом
        cursor = await analyzer.conn.execute("""
            SELECT COUNT(*) FROM token_ids 
            WHERE token_pair IS NOT NULL AND token_pair != 'Analyzing...'
        """)
        analyzed = await cursor.fetchone()
        stats['tokens_analyzed'] = analyzed[0] if analyzed else 0
        
        return {
            "success": True,
            "stats": stats,
            "queue_size": len(analyzer.analysis_queue)
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/api/analyzer/token/{token_address}")
async def get_token_data(token_address: str):
    """GET: Тільки ЧИТАННЯ даних токена з БД (без аналізу)"""
    try:
        analyzer = await get_analyzer()
        await analyzer.ensure_connection()
        
        # Отримуємо token_id
        token_id = await analyzer._get_token_id_by_address(token_address)
        if not token_id:
            return {
                "success": False,
                "error": f"Token {token_address} not found in database"
            }
        
        # Отримуємо збережені дані
        updated_token = await analyzer._get_updated_token_data(token_id)
        
        # Отримуємо додаткові дані з таблиць
        cursor = await analyzer.conn.execute("""
            SELECT 
                dp.dex_id, dp.pair_address, dp.price_usd, dp.fdv, dp.market_cap,
                dt.m5_buys, dt.m5_sells, dt.h24_buys, dt.h24_sells,
                dv.h24 as volume_24h,
                dl.usd as liquidity_usd
            FROM token_ids ti
            LEFT JOIN dexscreener_pairs dp ON dp.token_id = ti.id
            LEFT JOIN dexscreener_txns dt ON dt.token_id = ti.id
            LEFT JOIN dexscreener_volume dv ON dv.token_id = ti.id
            LEFT JOIN dexscreener_liquidity dl ON dl.token_id = ti.id
            WHERE ti.id = ?
        """, (token_id,))
        
        row = await cursor.fetchone()
        
        if row:
            detailed_data = {
                "dex_id": row[0],
                "pair_address": row[1],
                "price_usd": row[2],
                "fdv": row[3],
                "market_cap": row[4],
                "txns_5m": {"buys": row[5], "sells": row[6]},
                "txns_24h": {"buys": row[7], "sells": row[8]},
                "volume_24h": row[9],
                "liquidity_usd": row[10]
            }
        else:
            detailed_data = None
        
        return {
            "success": True,
            "token_id": token_id,
            "token_data": updated_token,
            "detailed_data": detailed_data
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/api/analyzer/check-honeypot")
async def check_honeypot(request: dict):
    """
    🚨 ШВИДКА ПЕРЕВІРКА HONEYPOT (без повного аналізу)
    
    Використовується для швидкої перевірки токена перед купівлею.
    Перевіряє тільки критичні параметри:
    - Honeypot check (Jupiter Quote API + RPC fallback)
    - Вік токена
    - Рівень ризику
    
    Приклад:
    ```
    POST /api/analyzer/check-honeypot
    {
        "token_address": "8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR"
    }
    ```
    
    Відповідь:
    ```json
    {
        "success": true,
        "token_address": "...",
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
            "is_very_new": false
        }
    }
    ```
    """
    try:
        token_address = request.get("token_address")
        if not token_address:
            return {"success": False, "error": "token_address is required"}
        
        print(f"\n{'='*80}")
        print(f"🚨 HONEYPOT CHECK REQUEST")
        print(f"Token: {token_address}")
        print(f"{'='*80}\n")
        
        # Отримуємо аналізатор
        analyzer = await get_analyzer()
        await analyzer.ensure_connection()
        await analyzer.ensure_session()
        
        # Швидкий аналіз ризиків
        result = await analyzer.analyze_risk_quick(token_address)
        
        if result.get("success"):
            print(f"\n✅ HONEYPOT CHECK COMPLETE")
            print(f"   Risk level: {result.get('risk_level')}")
            print(f"   Honeypot: {result['risk_analysis']['honeypot_check'].get('honeypot')}")
            print(f"   Time: {result.get('analysis_time')}")
        else:
            print(f"\n❌ HONEYPOT CHECK FAILED")
            print(f"   Error: {result.get('error')}")
        
        return result
        
    except Exception as e:
        import traceback
        print(f"❌ Error in check_honeypot: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }

@app.get("/api/analyzer/check-honeypot/{token_address}")
async def check_honeypot_get(token_address: str):
    """
    🚨 ШВИДКА ПЕРЕВІРКА HONEYPOT (GET метод)
    
    Альтернатива POST методу для зручності тестування в браузері
    
    Приклад:
    ```
    GET /api/analyzer/check-honeypot/8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR
    ```
    """
    return await check_honeypot({"token_address": token_address})

@app.post("/api/analyzer/analyze-full")
async def analyze_full(request: dict):
    """
    📊 ПОВНИЙ АНАЛІЗ ТОКЕНА (оптимізована версія)
    
    Послідовність з early exit:
    1️⃣ Jupiter Honeypot Check (2 запити) → якщо TRUE → СТОП ⛔
    2️⃣ Jupiter Token Info (1 запит) → name, symbol, dev address
    3️⃣ DexScreener (1 запит) → торгова пара, ліквідність, транзакції
    4️⃣ Solana RPC (2 запити) → supply, metadata
    
    Загалом: 6 запитів (~0.5-0.8s) якщо НЕ honeypot
             2 запити (~0.2s) якщо honeypot
    
    Приклад:
    ```
    POST /api/analyzer/analyze-full
    {
        "token_address": "8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR"
    }
    ```
    """
    try:
        token_address = request.get("token_address")
        if not token_address:
            return {"success": False, "error": "token_address is required"}
        
        print(f"\n{'='*80}")
        print(f"📊 FULL ANALYSIS REQUEST")
        print(f"Token: {token_address}")
        print(f"{'='*80}\n")
        
        # Отримуємо аналізатор
        analyzer = await get_analyzer()
        await analyzer.ensure_connection()
        await analyzer.ensure_session()
        
        # Повний аналіз (з збереженням в БД)
        result = await analyzer.analyze_token_full(token_address, save_to_db=True)
        
        if result.get("success"):
            print(f"\n✅ FULL ANALYSIS COMPLETE")
            print(f"   Risk level: {result.get('risk_level')}")
            print(f"   Honeypot: {result.get('security', {}).get('honeypot_check', {}).get('honeypot')}")
            if result.get('stopped_at'):
                print(f"   ⛔ Stopped at: {result.get('stopped_at')}")
            print(f"   Time: {result.get('analysis_time')}")
        else:
            print(f"\n❌ FULL ANALYSIS FAILED")
            print(f"   Error: {result.get('error')}")
        
        return result
        
    except Exception as e:
        import traceback
        print(f"❌ Error in analyze_full: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }

@app.get("/api/analyzer/analyze-full/{token_address}")
async def analyze_full_get(token_address: str):
    """
    📊 ПОВНИЙ АНАЛІЗ ТОКЕНА (GET метод)
    
    Альтернатива POST методу для зручності тестування в браузері
    
    Приклад:
    ```
    GET /api/analyzer/analyze-full/8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR
    ```
    """
    return await analyze_full({"token_address": token_address})

@app.post("/api/dexscreener/start")
async def start_dexscreener_scanner():
    """Запускає DexScreener сканер"""
    try:
        analyzer = await get_dexscreener_analyzer()
        return await analyzer.start_auto_scan()
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/dexscreener/stop")
async def stop_dexscreener_scanner():
    """Зупиняє DexScreener сканер"""
    try:
        analyzer = await get_dexscreener_analyzer()
        return await analyzer.stop_auto_scan()
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/dexscreener/status")
async def get_dexscreener_status():
    """Отримує статус DexScreener сканера"""
    try:
        analyzer = await get_dexscreener_analyzer()
        return analyzer.get_status()
    except Exception as e:
        return {"is_scanning": False, "error": str(e)}

@app.get("/api/sol-price")
async def get_sol_price_endpoint():
    """Отримує поточну ціну SOL"""
    try:
        monitor = await get_sol_price_monitor()
        return monitor.get_status()
    except Exception as e:
        return {"current_price": 0.0, "error": str(e)}

@app.post("/api/trades/get-for-token")
async def get_trades_for_token_endpoint(request: Dict[str, str]):
    """
    🔍 Окремий пошук trades для конкретного токена
    
    Body: {"token_pair": "trading_pair_address"}
    
    Приклад:
    ```
    POST /api/trades/get-for-token
    {"token_pair": "8en9zelomwkahjy68tjmgmqfmobpsd1xzaq1vs6dm2r5"}
    ```
    """
    try:
        token_pair = request.get("token_pair")
        if not token_pair:
            return {
                "success": False,
                "message": "token_pair is required"
            }
        
        # Імпортуємо функцію
        from _v2_live_trades import get_trades_for_token
        
        # Викликаємо функцію
        result = await get_trades_for_token(token_pair, debug=True)
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

@app.get("/api/trades/get-history")
async def get_trades_history_endpoint(token_pair: str = None):
    """
    📚 Отримання історичних trades для trading pair з pagination
    
    Query parameter: ?token_pair=trading_pair_address або без параметра для всіх токенів
    
    Приклад:
    ```
    GET /api/trades/get-history?token_pair=8En9ZeLoMwKaHJY68TjMGmqFmoBPSD1xZaQ1VS6dm2R5
    ```
    
    Або для всіх токенів:
    ```
    GET /api/trades/get-history
    ```
    """
    try:
        from _v2_trades_history import TradesHistory
        
        if token_pair:
            # Отримуємо ВСІ trades для одного токена з pagination
            manager = TradesHistory(config.HELIUS_API_KEY, "db/tokens.db", debug=True)
            try:
                # Знаходимо token_info
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
            # Отримуємо trades для всіх токенів
            result = await fetch_all_historical_trades(debug=True)
            return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

# Видалено проблемний endpoint - використовуйте run_trade_history.py

@app.post("/api/trades/get-by-token-address")
async def get_trades_by_token_address_endpoint(request: Dict[str, str]):
    """
    🔍 Отримання trades для токена по token_address (з Frontend)
    
    Body: {"token_address": "token_mint_address"}
    
    Приклад:
    ```
    POST /api/trades/get-by-token-address
    {"token_address": "8En9ZeLoMwKaHJY68TjMGmqFmoBPSD1xZaQ1VS6dm2R5"}
    ```
    
    Функція:
    1. Шукає token_pair в БД по token_address
    2. Використовує token_pair для Helius API
    3. Зберігає trades в БД з правильним token_id
    """
    try:
        token_address = request.get("token_address")
        if not token_address:
            return {
                "success": False,
                "message": "token_address is required"
            }
        
        # Імпортуємо функцію
        from _v2_live_trades import get_trades_for_token_by_address
        
        # Викликаємо функцію
        result = await get_trades_for_token_by_address(token_address, debug=True)
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

@app.on_event("startup")
async def startup_event():
    """Ініціалізація при запуску сервера"""
    await ensure_tokens_reader()
    await ensure_chart_data_reader()
    print("✅ Server started with Chart Data Reader")

@app.on_event("shutdown")
async def shutdown_event():
    """Очищення при зупинці сервера"""
    await cleanup_scanner()
    await cleanup_balance_monitor()
    print("✅ Server stopped")

@app.get("/api/chart-reader/status")
async def get_chart_reader_status():
    """Отримати статус Chart Data Reader"""
    if state.chart_data_reader:
        return state.chart_data_reader.get_status()
    return {"status": "not_initialized"}

@app.post("/api/chart-reader/start")
async def start_chart_reader():
    """Запустити Chart Data Reader"""
    if state.chart_data_reader:
        await state.chart_data_reader.start_auto_refresh()
        return {"success": True, "message": "Chart Data Reader started"}
    return {"success": False, "message": "Chart Data Reader not initialized"}

@app.post("/api/chart-reader/stop")
async def stop_chart_reader():
    """Зупинити Chart Data Reader"""
    if state.chart_data_reader:
        await state.chart_data_reader.stop_auto_refresh()
        return {"success": True, "message": "Chart Data Reader stopped"}
    return {"success": False, "message": "Chart Data Reader not initialized"}

@app.websocket("/ws/chart-data")
async def chart_data_websocket(websocket: WebSocket):
    await websocket.accept()
    await state.chart_data_reader.add_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await state.chart_data_reader.remove_client(websocket)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)