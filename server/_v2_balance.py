#!/usr/bin/env python3

import asyncio
import aiohttp
import json
import base58
from typing import Dict, List, Any, Optional, Tuple
from fastapi import WebSocket
from _v2_sol_price import get_current_sol_price
from config import config
from _v3_db_pool import get_db_pool

class BalanceV1:
    def __init__(self):
        self.rpc_url = config.SOLANA_RPC_URL
        self.session = None
        self.balance_data: Optional[List[Dict[str, Any]]] = None
        self.connected_clients: List[WebSocket] = []
        # Auto-refresh loop controls
        self.is_running: bool = False
        self.refresh_task: Optional[asyncio.Task] = None
        self.refresh_interval: float = 1.0  # Update every second
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    def _bits_to_address(self, bits: List[int]) -> str:
        try:
            private_key_bytes = bytes(bits)
            if len(private_key_bytes) == 64:
                public_key_bytes = private_key_bytes[32:64]
            else:
                public_key_bytes = private_key_bytes[:32]
            return base58.b58encode(public_key_bytes).decode('utf-8')
        except:
            return ""
    
    def load_wallets_from_keys(self) -> List[Dict[str, Any]]:
        try:
            # Try server/keys.json first, then keys.json
            keys_file = config.WALLET_KEYS_FILE
            if not keys_file.startswith('/') and not keys_file.startswith('server/'):
                keys_file = f"server/{keys_file}"
            
            try:
                with open(keys_file, 'r', encoding='utf-8') as f:
                    keys_data = json.load(f)
            except FileNotFoundError:
                # Fallback to keys.json in current directory
                with open('keys.json', 'r', encoding='utf-8') as f:
                    keys_data = json.load(f)
            
            wallets = []
            for key_data in keys_data:
                bits = key_data.get("bits", [])
                if not bits:
                    continue
                address = self._bits_to_address(bits)
                
                if address:
                    wallets.append({
                        "id": key_data.get("id"),
                        "name": key_data.get("name"),
                        "address": address,
                        "date_added": key_data.get("date_added")
                    })
            
            return wallets
        except Exception as e:
            print(f"[load_wallets_from_keys] Error: {e}")
            return []
    
    async def _get_sol_balance(self, address: str) -> Optional[float]:
        """Get SOL balance from RPC. Returns None if RPC fails (to use last known value from DB)."""
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]}
            async with self.session.post(self.rpc_url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if "result" in data:
                        lamports = data["result"]["value"]
                        return lamports / 1_000_000_000
            return None  # RPC failed - use last known value from DB
        except:
            return None  # RPC failed - use last known value from DB
    
    async def _get_sol_price_usd(self) -> float:
        """Get SOL price from monitor, with fallback to config."""
        price = get_current_sol_price()
        if price <= 0:
            # Fallback to config fallback price
            price = float(getattr(config, 'SOL_PRICE_FALLBACK', 193.0))
        return price
    
    async def get_sol_balances_for_wallets(self, wallets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sol_price_usd = await self._get_sol_price_usd()
        
        semaphore = asyncio.Semaphore(5)
        
        async def get_balance_with_semaphore(wallet):
            async with semaphore:
                sol_balance = await self._get_sol_balance(wallet['address'])
                # If RPC failed, sol_balance is None - set to 0.0 for display
                sol_balance_display = sol_balance if sol_balance is not None else 0.0
                value_usd = sol_balance_display * sol_price_usd if sol_price_usd > 0 and sol_balance is not None else 0.0
                
                return {
                    "id": wallet['id'],
                    "name": wallet['name'],
                    "address": wallet['address'],
                    "sol_balance": sol_balance_display,
                    "value_usd": value_usd,
                    "sol_price_usd": sol_price_usd,
                    "date_added": wallet.get('date_added', 'Unknown')
                }
        
        tasks = [get_balance_with_semaphore(wallet) for wallet in wallets]
        wallet_balances = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [w for w in wallet_balances if not isinstance(w, Exception)]
    
    async def _wallets_refresh(self, keys_wallets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Refresh real wallets from keys.json - update balances from actual SOL balances."""
        if not keys_wallets:
            return []
        
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Get real wallet IDs from keys.json
            real_wallet_ids = {int(w.get('id')) for w in keys_wallets}
            
            # Ensure real wallets exist and update their balances from actual SOL
            sol_price = await self._get_sol_price_usd()
            if sol_price <= 0:
                print(f"[_wallets_refresh] Warning: SOL price is {sol_price}, using fallback")
                sol_price = float(getattr(config, 'SOL_PRICE_FALLBACK', 193.0))
            
            for w in keys_wallets:
                wid = int(w.get('id'))
                name = w.get('name') or f"Wallet {wid}"
                address = w.get('address', '')
                
                if not address:
                    print(f"[_wallets_refresh] Warning: No address for wallet {wid}")
                    continue
                
                # Get real SOL balance from RPC
                sol_balance = await self._get_sol_balance(address)
                
                try:
                    existing = await conn.fetchrow("SELECT id, cash_usd FROM wallets WHERE id=$1", wid)
                    if not existing:
                        # New real wallet - insert only if RPC returned balance
                        if sol_balance is not None:
                            balance_usd = sol_balance * sol_price if sol_price > 0 else 0.0
                            await conn.execute(
                                """
                                INSERT INTO wallets(id, name, initial_deposit_usd, cash_usd)
                                VALUES($1, $2, $3, $3)
                                """,
                                wid, name, balance_usd
                            )
                            print(f"[_wallets_refresh] Created real wallet {wid}: {sol_balance} SOL * ${sol_price} = ${balance_usd}")
                        else:
                            # RPC failed for new wallet - skip creation
                            print(f"[_wallets_refresh] Warning: RPC failed for new wallet {wid}, skipping creation")
                    else:
                        # Existing real wallet
                        if sol_balance is not None:
                            # RPC succeeded - update cash from actual SOL balance
                            balance_usd = sol_balance * sol_price if sol_price > 0 else 0.0
                            await conn.execute(
                                """
                                UPDATE wallets 
                                SET cash_usd = $2,
                                    name = $3
                                WHERE id = $1
                                """,
                                wid, balance_usd, name
                            )
                            # Debug log
                            # print(f"[_wallets_refresh] Updated real wallet {wid}: {sol_balance} SOL * ${sol_price} = ${balance_usd}")
                        else:
                            # RPC failed - keep last known value from DB (cash_usd), only update name
                            await conn.execute(
                                """
                                UPDATE wallets 
                                SET name = $2
                                WHERE id = $1
                                """,
                                wid, name
                            )
                            # cash_usd remains unchanged (last known value)
                except Exception as e:
                    print(f"[_wallets_refresh] Error for wallet {wid}: {e}")
            
            # Fetch real wallets with current balances (including entry_amount_usd for UI)
            if not real_wallet_ids:
                return []
            
            wrows = await conn.fetch(
                "SELECT id, name, cash_usd, entry_amount_usd FROM wallets WHERE id = ANY($1) ORDER BY id ASC",
                list(real_wallet_ids)
            )
            wallets = [dict(r) for r in wrows]
            
            # Build mapping from keys.json
            keys_by_id = {int(w['id']): w for w in keys_wallets}
            
            # Compose UI payload for real wallets
            out: List[Dict[str, Any]] = []
            for w in wallets:
                wid = int(w['id'])
                cash = float(w.get('cash_usd') or 0.0)
                
                # For real wallets, balance_val is always cash (actual SOL balance converted to USD)
                # Real wallets don't use wallet_history for tracking positions
                # The cash_usd field already reflects the actual SOL balance from blockchain
                token_id_num = 0
                balance_val = cash  # Real wallet balance = actual SOL balance
                
                # Check if wallet has active position (for display purposes only)
                # But don't use it for balance calculation - real wallets use actual SOL balance
                open_rec = await conn.fetchrow(
                    """
                    SELECT token_id, entry_token_amount
                    FROM wallet_history
                    WHERE wallet_id=$1 AND exit_iteration IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    wid
                )
                if open_rec:
                    token_id_num = int(open_rec['token_id'])
                    # Note: balance_val stays as cash (real SOL balance), not calculated from position
                
                meta = keys_by_id.get(wid) or {}
                address = meta.get('address', '')
                # Try to get current SOL balance for display
                # If RPC fails, calculate from cash_usd and sol_price (last known values)
                sol_balance = 0.0
                if address:
                    try:
                        rpc_balance = await self._get_sol_balance(address)
                        if rpc_balance is not None:
                            sol_balance = rpc_balance
                        else:
                            # RPC failed - calculate from last known cash_usd
                            if sol_price > 0:
                                sol_balance = cash / sol_price
                    except Exception:
                        # Fallback: calculate from last known cash_usd
                        if sol_price > 0:
                            sol_balance = cash / sol_price
                
                # Get entry_amount_usd (user-configured entry amount for this wallet)
                # Allow 0 (wallet disabled) - check for None explicitly, not falsy check
                entry_amount_usd_val = w.get('entry_amount_usd')
                if entry_amount_usd_val is not None:
                    entry_amount = float(entry_amount_usd_val)
                else:
                    entry_amount = 5.0  # Default fallback when not set
                
                out.append({
                    "id": wid,
                    "name": meta.get('name') or (w.get('name') or f"Wallet {wid}"),
                    "address": address or f"real:{wid}",
                    "sol_balance": sol_balance,
                    "value_usd": balance_val,
                    "cash_usd": cash,
                    "sol_price_usd": sol_price,
                    "entry_amount_usd": entry_amount,  # User-configured entry amount
                    "date_added": meta.get('date_added') or "real",
                    "token_id": token_id_num,
                })
            
            return out

    async def load_balance_data(self) -> List[Dict[str, Any]]:
        """Load real wallets from keys.json."""
        try:
            wallets = self.load_wallets_from_keys()
            if not wallets:
                print("[load_balance_data] No wallets found in keys.json")
                return []
            
            real_data = await self._wallets_refresh(wallets)
            self.balance_data = real_data
            return real_data
        except Exception as e:
            print(f"[load_balance_data] Error: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    async def _broadcast_to_clients(self, data):
        if not self.connected_clients:
            return
            
        json_data = json.dumps(data, ensure_ascii=False)
        
        disconnected_clients = []
        for client in self.connected_clients:
            try:
                await client.send_text(json_data)
                await asyncio.sleep(0.001)
            except Exception as e:
                disconnected_clients.append(client)
        
        for client in disconnected_clients:
            self.connected_clients.remove(client)
    
    def add_client(self, websocket: WebSocket):
        self.connected_clients.append(websocket)
    
    def remove_client(self, websocket: WebSocket):
        if websocket in self.connected_clients:
            self.connected_clients.remove(websocket)
    
    async def send_initial_data(self, websocket: WebSocket):
        try:
            if self.balance_data:
                await websocket.send_text(json.dumps(self.balance_data, ensure_ascii=False))
            else:
                balance_data = await self.load_balance_data()
                if balance_data:
                    await websocket.send_text(json.dumps(balance_data, ensure_ascii=False))
                else:
                    await websocket.send_text(json.dumps([], ensure_ascii=False))
        except Exception as e:
            pass
    
    async def refresh_balance(self):
        try:
            balance_data = await self.load_balance_data()
            if balance_data:
                await self._broadcast_to_clients(balance_data)
                return {"success": True, "message": "Balance data refreshed", "wallets_count": len(balance_data)}
            else:
                return {"success": False, "message": "No balance data available"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _auto_refresh_loop(self):
        """Background loop to periodically refresh and broadcast balances."""
        try:
            while self.is_running:
                try:
                    await self.refresh_balance()
                except Exception:
                    pass
                await asyncio.sleep(self.refresh_interval)
        except asyncio.CancelledError:
            pass

    async def start_auto_refresh(self):
        """Start periodic balance refresh if not already running."""
        if not self.is_running:
            self.is_running = True
            self.refresh_task = asyncio.create_task(self._auto_refresh_loop())
            return {"success": True, "message": "Balance auto-refresh started"}
        return {"success": False, "message": "Balance auto-refresh already running"}

    async def stop_auto_refresh(self):
        """Stop periodic balance refresh if running."""
        if self.is_running:
            self.is_running = False
            if self.refresh_task:
                self.refresh_task.cancel()
                try:
                    await self.refresh_task
                except asyncio.CancelledError:
                    pass
                self.refresh_task = None
            return {"success": True, "message": "Balance auto-refresh stopped"}
        return {"success": False, "message": "Balance auto-refresh not running"}
    
    def get_status(self):
        return {
            "has_data": self.balance_data is not None,
            "wallets_count": len(self.balance_data) if self.balance_data else 0,
            "connected_clients": len(self.connected_clients),
            "is_running": self.is_running,
            "refresh_interval": self.refresh_interval
        }

if __name__ == "__main__":
    pass
