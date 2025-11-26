"""
V3 main.py - Тестова версія для crypto.db (SQLite)
Використовує V3 компоненти для роботи з новою структурою БД
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Body
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from _v3_tokens_reader import TokensReaderV3
from _v3_chart_data_reader import ChartDataReaderV3
from _v2_balance import BalanceV1
from _v2_sol_price import get_sol_price_monitor
from _v3_new_tokens import get_scanner as get_jupiter_scanner
from _v3_analyzer_jupiter import get_analyzer as get_jupiter_analyzer
from _v3_jupiter_scheduler import get_scheduler
from _v2_buy_sell import force_sell as bs_force_sell, force_buy as bs_force_buy
# from _v1_buy_sell import sync_wallet_positions  # TODO: Function needs to be restored
from _v3_live_trades import (
    get_live_trades_reader,
    set_live_trades_rate_limit,
)
from config import config

from _v3_db_pool import get_db_pool, close_db_pool
from ai.infer.service import get_forecast_runner
from config import config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STOP_SCRIPT_PATH = PROJECT_ROOT / "stop.sh"


# 
app = FastAPI(title="Crypto App V3 - SQLite Version")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


class AppStateV3:
    def __init__(self):
        self.tokens_reader: Optional[TokensReaderV3] = None
        self.chart_data_reader: Optional[ChartDataReaderV3] = None
        self.balance_monitor: Optional[BalanceV1] = None
        self.scanner = None
        self.jupiter_analyzer = None
        self.live_trades = None
        self.ai_forecast = None
        self.metrics_ticker = None


state = AppStateV3()


def history_mode_enabled() -> bool:
    env_value = os.getenv("TOKENS_SHOW_HISTORY")
    fallback = getattr(config, 'TOKENS_SHOW_HISTORY', False)
    flag = env_value if env_value is not None else fallback
    if isinstance(flag, bool):
        return flag
    return str(flag).lower() not in ("0", "false", "none", "")


# ============================================================================
# INITIALIZATION HELPERS
# ============================================================================

async def ensure_tokens_reader():
    if state.tokens_reader is None:
        state.tokens_reader = TokensReaderV3(debug=True)
        await state.tokens_reader.ensure_connection()


async def ensure_chart_data_reader():
    if state.chart_data_reader is None:
        state.chart_data_reader = ChartDataReaderV3(debug=True)
        await state.chart_data_reader.ensure_connection()


async def ensure_balance_monitor():
    if state.balance_monitor is None:
        state.balance_monitor = BalanceV1()
        await state.balance_monitor.__aenter__()
        await state.balance_monitor.load_balance_data()


async def ensure_scanner():
    if state.scanner is None:
        state.scanner = await get_jupiter_scanner()


async def ensure_jupiter_analyzer():
    if state.jupiter_analyzer is None:
        state.jupiter_analyzer = await get_jupiter_analyzer()


async def ensure_live_trades():
    if state.live_trades is None:
        state.live_trades = await get_live_trades_reader()


async def ensure_ai_forecast():
    if state.ai_forecast is None:
        state.ai_forecast = await get_forecast_runner()

async def ensure_metrics_ticker():
    return None




async def cleanup():
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
    state.metrics_ticker = None


def schedule_full_shutdown(delay_seconds: float = 0.5) -> bool:
    """
    Запускає ./stop.sh у фоні для повної зупинки всіх сервісів.
    Використовується, щоб кнопка STOP працювала так само, як shell-скрипт.
    """
    if not STOP_SCRIPT_PATH.exists():
        if config.DEBUG:
            print(f"[System] stop.sh not found at {STOP_SCRIPT_PATH}")
        return False
    
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    def _launch_stop_script():
        try:
            subprocess.Popen(
                ["/bin/bash", str(STOP_SCRIPT_PATH)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if config.DEBUG:
                print("[System] stop.sh launched")
        except Exception as exc:
            if config.DEBUG:
                print(f"[System] Failed to launch stop.sh: {exc}")
    
    if loop:
        loop.call_later(delay_seconds, _launch_stop_script)
    else:
        _launch_stop_script()
    
    return True


# ============================================================================
# WEBSOCKETS (3 endpoints)
# ============================================================================

@app.websocket("/ws/tokens")
async def websocket_tokens(websocket: WebSocket):
    try:
        await websocket.accept()
        
        await ensure_tokens_reader()
        await state.tokens_reader.add_client(websocket)
        
        try:
            result = await state.tokens_reader.get_tokens_from_db(limit=1000)

            if result["success"]:
                token_count = len(result.get('tokens', []))
                
                if token_count > 0:
                    await websocket.send_text(json.dumps(result, ensure_ascii=False))
                else:
                    empty_result = {
                        "success": True,
                        "tokens": [],
                        "total_found": 0,
                        "total_count": 0
                    }
                    await websocket.send_text(json.dumps(empty_result, ensure_ascii=False))
            else:
                error_result = {
                    "success": False,
                    "error": result.get('error', 'Unknown error'),
                    "tokens": []
                }
                await websocket.send_text(json.dumps(error_result, ensure_ascii=False))
                
        except Exception as e:
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
        print(f"❌ WebSocket tokens V3 error: {e}")
    finally:
        if state.tokens_reader:
            state.tokens_reader.remove_client(websocket)


@app.websocket("/ws/chart-data")
async def websocket_chart_data(websocket: WebSocket):
    try:
        await websocket.accept()
        await ensure_chart_data_reader()
        await state.chart_data_reader.add_client(websocket)
        
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                break
                
    except Exception as e:
        print(f"❌ WebSocket chart-data V3 error: {e}")
    finally:
        if state.chart_data_reader:
            await state.chart_data_reader.remove_client(websocket)


@app.websocket("/ws/balances")
async def websocket_balances(websocket: WebSocket):
    try:
        await websocket.accept()
        await ensure_balance_monitor()
        
        state.balance_monitor.add_client(websocket)
        await state.balance_monitor.send_initial_data(websocket)
        
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
# API ENDPOINTS
# ============================================================================

@app.post("/api/system/timers/start")
async def api_start_all_timers():
    if history_mode_enabled():
        return {"success": False, "error": "history_mode_active"}
    results = {}
    # Balance
    await ensure_balance_monitor()
    if state.balance_monitor:
        results['balance'] = await state.balance_monitor.start_auto_refresh()
    else:
        results['balance'] = {"success": False, "error": "balance monitor not initialized"}

    # Tokens
    await ensure_tokens_reader()
    if state.tokens_reader:
        await state.tokens_reader.start_auto_refresh()
        results['tokens'] = {"success": True, "message": "tokens auto-refresh started"}
    else:
        results['tokens'] = {"success": False, "error": "tokens reader not initialized"}

    # Charts
    await ensure_chart_data_reader()
    if state.chart_data_reader:
        await state.chart_data_reader.start_auto_refresh()
        results['charts'] = {"success": True, "message": "charts auto-refresh started"}
    else:
        results['charts'] = {"success": False, "error": "chart reader not initialized"}

    return {"success": True, "results": results}


@app.post("/api/system/timers/stop")
async def api_stop_all_timers():
    if history_mode_enabled():
        return {"success": False, "error": "history_mode_active"}
    results = {}
    # WS timers
    if state.balance_monitor:
        results['balance'] = await state.balance_monitor.stop_auto_refresh()
    else:
        results['balance'] = {"success": False, "error": "balance monitor not initialized"}

    if state.tokens_reader:
        await state.tokens_reader.stop_auto_refresh()
        results['tokens'] = {"success": True, "message": "tokens auto-refresh stopped"}
    else:
        results['tokens'] = {"success": False, "error": "tokens reader not initialized"}

    if state.chart_data_reader:
        await state.chart_data_reader.stop_auto_refresh()
        results['charts'] = {"success": True, "message": "charts auto-refresh stopped"}
    else:
        results['charts'] = {"success": False, "error": "chart reader not initialized"}

    # Force stop New Tokens scanner
    try:
        await ensure_scanner()
        if state.scanner and getattr(state.scanner, 'is_scanning', False):
            results['new_tokens_scanner'] = await state.scanner.stop_auto_scan()
        else:
            results['new_tokens_scanner'] = {"success": True, "message": "scanner already stopped"}
    except Exception as e:
        results['new_tokens_scanner'] = {"success": False, "error": str(e)}

    # Force stop Analyzer (legacy) / unified Scheduler
    try:
        from _v3_jupiter_scheduler import get_scheduler

        sched = await get_scheduler()

        if getattr(sched, 'is_running', False):
            results['scheduler'] = await sched.stop()
        else:
            results['scheduler'] = {"success": True, "message": "scheduler already stopped"}
        
        await ensure_jupiter_analyzer()

        if state.jupiter_analyzer and getattr(state.jupiter_analyzer, 'is_scanning', False):
            results['analyzer'] = await state.jupiter_analyzer.stop()
        else:
            results['analyzer'] = {"success": True, "message": "analyzer already stopped"}
    except Exception as e:
        results['analyzer'] = {"success": False, "error": str(e)}

    # Stop LiveTrades reader (Helius)
    try:
        await ensure_live_trades()
        if state.live_trades and getattr(state.live_trades, 'is_running', False):
            results['live_trades'] = await state.live_trades.stop()
        else:
            results['live_trades'] = {"success": True, "message": "live trades already stopped"}
    except Exception as e:
        results['live_trades'] = {"success": False, "error": str(e)}

    shutdown_scheduled = schedule_full_shutdown()
    
    return {
        "success": True,
        "results": results,
        "shutdown_scheduled": shutdown_scheduled
    }


@app.get("/api/system/timers/status")
async def api_timers_status():
    await ensure_balance_monitor()
    await ensure_tokens_reader()
    await ensure_chart_data_reader()
    await ensure_scanner()
    await ensure_jupiter_analyzer()
    await ensure_live_trades()
    await ensure_metrics_ticker()
    await ensure_ai_forecast()

    status = {
        "balance": state.balance_monitor.get_status() if state.balance_monitor else {"is_running": False},
        "tokens": {
            "auto_refresh_running": state.tokens_reader.auto_refresh_task is not None if state.tokens_reader else False,
            "token_count": state.tokens_reader.last_token_count if state.tokens_reader else 0,
            "total_token_count": state.tokens_reader.total_token_count if state.tokens_reader else 0
        },
        "charts": {
            "is_running": state.chart_data_reader.is_running if state.chart_data_reader else False,
            "connected_clients": len(state.chart_data_reader.connected_clients) if state.chart_data_reader else 0
        },
        "new_tokens_scanner": state.scanner.get_status() if state.scanner else {"is_scanning": False},
        "analyzer": {"is_scanning": state.jupiter_analyzer.is_scanning if state.jupiter_analyzer else False},
        "live_trades": {"is_running": state.live_trades.is_running if state.live_trades else False},
        "ai_forecast": {**(state.ai_forecast.get_status() if state.ai_forecast else {"is_running": False}), "name": config.AI_NAME},
        "metrics_ticker": {"is_running": False}
    }
    return {"success": True, "status": status}




@app.post("/api/analyzer/start")
async def api_analyzer_start():
    sched = await get_scheduler()
    res = await sched.start()
    # Simulation timers removed
    return res


@app.post("/api/analyzer/stop")
async def api_analyzer_stop():
    sched = await get_scheduler()
    res = await sched.stop()
    # Simulation timers removed
    return res


@app.get("/api/analyzer/status")
async def api_analyzer_status():
    sched = await get_scheduler()
    s = sched.status()
    return {"success": True, **s}



@app.post("/api/ai-forecast/start")
async def api_ai_forecast_start():
    await ensure_ai_forecast()
    return await state.ai_forecast.start()


@app.post("/api/ai-forecast/stop")
async def api_ai_forecast_stop():
    await ensure_ai_forecast()
    return await state.ai_forecast.stop()


@app.get("/api/ai-forecast/status")
async def api_ai_forecast_status():
    await ensure_ai_forecast()
    status = state.ai_forecast.get_status()
    status.update({"name": config.AI_NAME})
    return {"success": True, **status}




@app.post("/api/live-trades/start")
async def api_live_trades_start():
    await ensure_live_trades()
    if state.live_trades is None:
        return {"success": False, "message": "LiveTrades not initialized"}
    return await state.live_trades.start()


@app.post("/api/live-trades/stop")
async def api_live_trades_stop():
    if state.live_trades is None:
        return {"success": False, "message": "LiveTrades not initialized"}
    return await state.live_trades.stop()


@app.get("/api/live-trades/status")
async def api_live_trades_status():
    await ensure_live_trades()
    if state.live_trades is None:
        return {"success": True, "is_running": False}
    return {"success": True, **state.live_trades.get_status()}


@app.post("/api/live-trades/set-rate-limit")
async def api_live_trades_set_rate_limit(rps: int = 1):
    res = await set_live_trades_rate_limit(rps)
    return res


@app.post("/api/sell/force")
async def api_force_sell(token_id: int):
    print(f"[API] 📞 Force sell API called for token_id={token_id}")
    try:
        res = await bs_force_sell(token_id)
        print(f"[API] 📤 Force sell API response for token_id={token_id}: success={res.get('success')}, message={res.get('message', 'N/A')}")
        # Push balance update immediately so UI reflects wallet changes
        try:
            await ensure_balance_monitor()
            if state.balance_monitor:
                await state.balance_monitor.refresh_balance()
            # Also push tokens immediately so entry/exit lines update without delay
            # Small delay to ensure DB transaction is committed
            await asyncio.sleep(0.1)
            await ensure_tokens_reader()
            if state.tokens_reader:
                print(f"[API] 📤 Pushing token list update to clients after force sell for token {token_id}")
                await state.tokens_reader.push_now()
                print(f"[API] ✅ Token list update pushed successfully")
            else:
                print(f"[API] ⚠️ tokens_reader is None, cannot push update")
        except Exception as e:
            print(f"[API] ❌ Error pushing token list update: {e}")
            import traceback
            traceback.print_exc()
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/buy/force")
async def api_force_buy(token_id: int):
    try:
        res = await bs_force_buy(token_id)
        # Push balance update immediately
        try:
            await ensure_balance_monitor()
            if state.balance_monitor:
                await state.balance_monitor.refresh_balance()
            # Also push tokens immediately so entry line appears instantly
            await ensure_tokens_reader()
            if state.tokens_reader:
                await state.tokens_reader.push_now()
        except Exception:
            pass
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.put("/api/wallet/{wallet_id}/entry-amount")
async def api_update_wallet_entry_amount(wallet_id: int, request: Request):
    """Update entry amount for a wallet (user-configured amount for next buy)."""
    try:
        body = await request.json()
        entry_amount_usd = body.get("entry_amount_usd")
        
        if entry_amount_usd is None:
            return {"success": False, "error": "entry_amount_usd is required"}
        
        try:
            entry_amount_num = float(entry_amount_usd)
            # Allow 0 (wallet disabled) but block negative numbers
            if entry_amount_num < 0:
                return {"success": False, "error": "entry_amount_usd cannot be negative"}
        except (ValueError, TypeError):
            return {"success": False, "error": "entry_amount_usd must be a valid number"}
        
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Update entry_amount_usd in wallets table
            result = await conn.execute(
                """
                UPDATE wallets
                SET entry_amount_usd = $1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $2
                """,
                entry_amount_num,
                wallet_id
            )
            
            # Check if wallet exists
            if "UPDATE 0" in str(result):
                return {"success": False, "error": f"Wallet {wallet_id} not found"}
            
            return {"success": True, "wallet_id": wallet_id, "entry_amount_usd": entry_amount_num}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/new-tokens/stop")
async def api_stop_new_tokens():
    """Stop new tokens scanner while keeping analyzer running"""
    try:
        sched = await get_scheduler()
        if not sched.is_running:
            return {"success": False, "message": "Scheduler not running"}
        
        sched.set_manual_scanner_skip(True)
        return {"success": True, "message": "New tokens scanner stopped (analyzer continues)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/new-tokens/start")
async def api_start_new_tokens():
    """Resume new tokens scanner after manual stop"""
    try:
        sched = await get_scheduler()
        if not sched.is_running:
            return {"success": False, "message": "Scheduler not running"}
        sched.set_manual_scanner_skip(False)
        return {"success": True, "message": "New tokens scanner resumed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/new-tokens/status")
async def api_new_tokens_status():
    try:
        sched = await get_scheduler()
        state = sched.get_scanner_state()
        state["scanner_active"] = sched.is_scanner_active()
        return {"success": True, **state}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/wallet/check-positions")
async def api_check_open_positions():
    """Перевірити відкриті позиції без закриття (лише wallet_history)."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Find all open positions in history
            open_positions = await conn.fetch(
                """
                SELECT 
                    h.id AS history_id,
                    h.wallet_id,
                    h.token_id,
                    t.name AS token_name,
                    t.token_address,
                    h.entry_amount_usd,
                    h.entry_token_amount,
                    h.entry_price_usd,
                    h.entry_iteration,
                    h.entry_signature,
                    h.created_at AS entry_time,
                    t.wallet_id
                FROM wallet_history h
                JOIN tokens t ON t.id = h.token_id
                WHERE h.exit_iteration IS NULL
                ORDER BY h.created_at DESC
                """
            )
            
            positions = []
            for pos in open_positions:
                positions.append({
                    "history_id": pos["history_id"],
                    "wallet_id": pos["wallet_id"],
                    "wallet_name": None,
                    "token_id": pos["token_id"],
                    "token_name": pos["token_name"],
                    "token_address": pos["token_address"],
                    "entry_amount_usd": float(pos["entry_amount_usd"] or 0),
                    "entry_token_amount": float(pos["entry_token_amount"] or 0),
                    "entry_price_usd": float(pos["entry_price_usd"] or 0),
                    "entry_iteration": pos["entry_iteration"],
                    "entry_signature": pos["entry_signature"],
                    "entry_time": str(pos["entry_time"]) if pos["entry_time"] else None,
                    "wallet_id_bound": pos["wallet_id"]
                })
            
            return {"success": True, "total": len(positions), "positions": positions}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def sync_wallet_positions() -> dict:
    """Синхронізувати стан реальних кошельків з blockchain.
    
    Перевіряє всі токени з прив'язаними кошельками (tokens.wallet_id IS NOT NULL)
    і очищає прив'язку, якщо позиція вже закрита (exit_iteration IS NOT NULL в wallet_history).
    
    Returns:
        dict with sync statistics: {success, synced, errors, total}
    """
    pool = await get_db_pool()
    synced = 0
    errors = 0
    
    async with pool.acquire() as conn:
        try:
            # Find all tokens with wallet_id set
            tokens_with_wallet = await conn.fetch(
                """
                SELECT t.id AS token_id, t.wallet_id, t.name AS token_name
                FROM tokens t
                WHERE t.wallet_id IS NOT NULL
                """
            )
            
            if not tokens_with_wallet:
                return {"success": True, "synced": 0, "errors": 0, "total": 0}
            
            total = len(tokens_with_wallet)
            
            # Check each token: if position is closed in wallet_history, clear wallet_id
            for token in tokens_with_wallet:
                token_id = token["token_id"]
                wallet_id = token["wallet_id"]
                
                try:
                    # Check if there's an open position (exit_iteration IS NULL)
                    open_position = await conn.fetchrow(
                        """
                        SELECT id FROM wallet_history
                        WHERE token_id=$1 
                          AND wallet_id=$2
                          AND exit_iteration IS NULL
                        LIMIT 1
                        """,
                        token_id, wallet_id
                    )
                    
                    if not open_position:
                        # No open position - clear wallet_id (position was closed)
                        await conn.execute(
                            """
                            UPDATE tokens
                            SET wallet_id = NULL,
                                token_updated_at = CURRENT_TIMESTAMP
                            WHERE id=$1
                            """,
                            token_id
                        )
                        synced += 1
                        print(f"[sync_wallet_positions] ✅ Cleared wallet_id for token {token_id} (token_name={token.get('token_name', 'N/A')}, wallet_id={wallet_id}) - position was closed")
                except Exception as e:
                    errors += 1
                    print(f"[sync_wallet_positions] ❌ Error syncing token {token_id}: {e}")
            
            return {
                "success": True,
                "synced": synced,
                "errors": errors,
                "total": total
            }
        except Exception as e:
            return {"success": False, "error": str(e), "synced": synced, "errors": errors, "total": 0}


@app.post("/api/wallet/sync-positions")
async def api_sync_wallet_positions():
    """Синхронізувати стан реальних кошельків з blockchain.
    
    Перевіряє всі активні позиції і закриває ті, які вже продано через зовнішні кошельки (наприклад, Phantom).
    Викликається автоматично при старті сервера, але можна викликати вручну через цей endpoint.
    
    Returns:
        dict with sync statistics: {success, synced, errors, total}
    """
    try:
        from config import config
        if not getattr(config, 'REAL_TRADING_ENABLED', False):
            return {"success": False, "message": "Real trading is not enabled"}
        
        sync_result = await sync_wallet_positions()
        return sync_result
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _repair_sequences_if_needed():
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            for table in ("tokens", "trades"):
                seq = await conn.fetchval("SELECT pg_get_serial_sequence($1, 'id')", table)
                if not seq:
                    continue
                max_id = await conn.fetchval(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
                await conn.execute("SELECT setval($1, $2, $3)", seq, int(max_id) + 1, True)
    except Exception:
        pass


# ============================================================================
# LIFECYCLE EVENTS
# ============================================================================

@app.on_event("startup")
async def startup_event():
    await get_db_pool()
    await _repair_sequences_if_needed()

    history_mode = history_mode_enabled()

    if not history_mode:
        # Start SOL price monitor FIRST (before balance monitor needs it)
        await get_sol_price_monitor(update_interval=1, debug=True)
        # Wait a bit for initial price fetch
        import asyncio
        await asyncio.sleep(0.5)
        # Initialize balance monitor so that wallet rows exist and broadcast initial state
        try:
            await ensure_balance_monitor()
            if state.balance_monitor:
                await state.balance_monitor.load_balance_data()
                await state.balance_monitor.refresh_balance()
                await state.balance_monitor.start_auto_refresh()
        except Exception as e:
            print(f"[startup] Error initializing balance monitor: {e}")
        
        # Sync real wallet positions with blockchain state
        try:
            if getattr(config, 'REAL_TRADING_ENABLED', False):
                print("[startup] Syncing real wallet positions with blockchain...")
                sync_result = await sync_wallet_positions()
                print(f"[startup] Wallet sync complete: {sync_result}")
        except Exception as e:
            print(f"[startup] Error syncing wallet positions: {e}")
    else:
        print("[startup] History mode detected – disabling realtime scanners/monitors")
        await ensure_balance_monitor()

    await ensure_tokens_reader()
    await ensure_chart_data_reader()

    if not history_mode:
        await ensure_scanner()
        await ensure_jupiter_analyzer()
        await ensure_live_trades()
        await ensure_metrics_ticker()
        # Start AI forecast service for pattern classification (runs every 1 second)
        try:
            await ensure_ai_forecast()
            if state.ai_forecast:
                await state.ai_forecast.start()
                print("[startup] ✅ AI forecast service started (pattern classification)")
        except Exception as e:
            print(f"[startup] ⚠️ Error starting AI forecast service: {e}")
    else:
        print("[startup] History mode: skipping scanner/analyzer/live trades/AI forecast")


@app.on_event("shutdown")
async def shutdown_event():
    # Stop background scanners/analyzers if they expose scanning flags
    try:
        if state.scanner and getattr(state.scanner, 'is_scanning', False):
            await state.scanner.stop_auto_scan()
    except Exception:
        pass

    try:
        if state.jupiter_analyzer and getattr(state.jupiter_analyzer, 'is_scanning', False):
            await state.jupiter_analyzer.stop_auto_scan()
    except Exception:
        pass

    # Gracefully close scanner session if supported
    try:
        if state.scanner and hasattr(state.scanner, 'close'):
            await state.scanner.close()
    except Exception:
        pass

    await cleanup()
    await close_db_pool()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=config.DEBUG)
