"""
FastAPI application for Jupiter token scanning with WebSocket support
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from _v1_new_tokens_jupiter_SQLite import JupiterScannerSQL

# Initialize FastAPI app
app = FastAPI(title="Jupiter Token Scanner")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшені треба обмежити
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Global state
class AppState:
    def __init__(self):
        self.scanner: Optional[JupiterScannerSQL] = None
        self.auto_scan_task: Optional[asyncio.Task] = None
        self.last_scan_time: Optional[datetime] = None
        self.auto_scan_interval: int = 5  # seconds
        self.is_scanning: bool = False

state = AppState()

async def ensure_scanner():
    """Ensure scanner is initialized"""
    if state.scanner is None:
        state.scanner = JupiterScannerSQL(debug=True)
        await state.scanner.ensure_session()

async def cleanup_scanner():
    """Cleanup scanner resources"""
    if state.scanner:
        await state.scanner.close()
        state.scanner = None

async def auto_scan():
    """Background task for automatic scanning"""
    while state.is_scanning:
        try:
            await ensure_scanner()
            result = await state.scanner.get_tokens(limit=20)
            
            if result["success"]:
                state.last_scan_time = datetime.now()
                
                # Broadcast to all connected clients
                await state.scanner.broadcast_tokens(result)
                print(f"✅ Auto-scan complete: {result['total_found']} tokens found")
            else:
                print(f"❌ Auto-scan failed: {result.get('error', 'Unknown error')}")
                
        except Exception as e:
            print(f"❌ Auto-scan error: {str(e)}")
            
        await asyncio.sleep(state.auto_scan_interval)

@app.websocket("/ws/tokens")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time token updates"""
    print("👋 New WebSocket connection request...")
    try:
        # Встановлюємо необхідні заголовки для WebSocket
        websocket.scope["headers"].extend([
            (b"Access-Control-Allow-Origin", b"*"),
            (b"Access-Control-Allow-Methods", b"GET, POST, OPTIONS"),
            (b"Access-Control-Allow-Headers", b"*"),
        ])
        
        await ensure_scanner()
        if not state.scanner:
            print("❌ Scanner not initialized during WebSocket connection")
            return
            
        print("🔌 Accepting WebSocket connection...")
        await state.scanner.connect_client(websocket)
        print("✅ WebSocket client connected")
        
        # Відправляємо початкові дані, якщо вони є
        if state.last_scan_time:
            try:
                result = await state.scanner.get_tokens(limit=20)
                if result["success"]:
                    print("📤 Sending initial data to new client...")
                    await websocket.send_json(result)
            except Exception as e:
                print(f"⚠️ Failed to send initial data: {str(e)}")
        
        while True:
            try:
                # Wait for client messages
                data = await websocket.receive_text()
                print(f"📥 Received message from client: {data}")
                
                # Обробка команд від клієнта
                if data == "scan":
                    result = await state.scanner.get_tokens(limit=20)
                    await websocket.send_json(result)
                elif data == "stop":
                    print("🛑 Client requested to stop scanning")
                    state.is_scanning = False
                    if state.auto_scan_task:
                        state.auto_scan_task.cancel()
                        try:
                            await state.auto_scan_task
                        except asyncio.CancelledError:
                            pass
                    await websocket.send_json({
                        "success": True,
                        "message": "Scanning stopped",
                        "stopped": True
                    })
                    
            except WebSocketDisconnect:
                print("🔌 Client disconnected normally")
                break
            except Exception as e:
                print(f"❌ WebSocket error: {str(e)}")
                break
                
    except Exception as e:
        print(f"❌ WebSocket connection error: {str(e)}")
    finally:
        print("👋 Cleaning up WebSocket connection...")
        await state.scanner.disconnect_client(websocket)

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    await ensure_scanner()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    state.is_scanning = False
    if state.auto_scan_task:
        state.auto_scan_task.cancel()
        try:
            await state.auto_scan_task
        except asyncio.CancelledError:
            pass
    await cleanup_scanner()

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "status": "running",
        "auto_scan": state.is_scanning,
        "last_scan": state.last_scan_time.isoformat() if state.last_scan_time else None
    }

@app.post("/api/scan")
async def scan_tokens() -> Dict[str, Any]:
    """Manual scan for new tokens"""
    try:
        print("🔍 Starting manual scan...")
        await ensure_scanner()
        
        if not state.scanner:
            print("❌ Scanner not initialized")
            return {
                "success": False,
                "error": "Scanner not initialized"
            }
            
        print("📡 Fetching tokens...")
        result = await state.scanner.get_tokens(limit=20)
        print(f"📊 Scan result: {result}")
        
        if result["success"]:
            state.last_scan_time = datetime.now()
            print("📢 Broadcasting results...")
            await state.scanner.broadcast_tokens(result)
            print("✅ Broadcast complete")
            
        return result
        
    except Exception as e:
        print(f"❌ Scan error: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/api/auto-scan/start")
async def start_auto_scan():
    """Start automatic scanning"""
    if not state.is_scanning:
        state.is_scanning = True
        state.auto_scan_task = asyncio.create_task(auto_scan())
        return {"success": True, "message": "Auto-scan started"}
    return {"success": False, "message": "Auto-scan already running"}

@app.post("/api/auto-scan/stop")
async def stop_auto_scan():
    """Stop automatic scanning"""
    if state.is_scanning:
        state.is_scanning = False
        if state.auto_scan_task:
            state.auto_scan_task.cancel()
            try:
                await state.auto_scan_task
            except asyncio.CancelledError:
                pass
            state.auto_scan_task = None
        return {"success": True, "message": "Auto-scan stopped"}
    return {"success": False, "message": "Auto-scan not running"}

@app.get("/api/status")
async def get_status():
    """Get current scanner status"""
    return {
        "auto_scan": state.is_scanning,
        "last_scan": state.last_scan_time.isoformat() if state.last_scan_time else None,
        "scan_interval": state.auto_scan_interval,
        "connected_clients": len(state.scanner.active_connections) if state.scanner else 0
    }

if __name__ == "__main__":
    print("🚀 Server starting...")
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)