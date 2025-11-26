#!/usr/bin/env python3
"""
Crypto Analyzer V1 SQLite — Розширена стійка версія з ретраями і фолбеками
Аналіз критичних security параметрів токену:
honeypot, rugpull, liquidity, initial_liquidity, dev_activity
"""

import asyncio
import aiohttp
import time
import sqlite3
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
import os
import math
import random

# --- Конфіг ретраїв ---
RETRY_COUNT = 3
RETRY_BACKOFF_BASE = 0.4  # seconds; exponential backoff factor

class TokenAnalyzerSQL:
    def __init__(self, debug: bool = False):
        self.solana_rpc_url = "https://api.mainnet-beta.solana.com"
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        self.db_path = "db/tokens.db"

    def _debug_print(self, *args):
        if self.debug:
            print("[DEBUG]", *args)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def analyze_token(self, token_address: str) -> Dict[str, Any]:
        start_time = time.time()

        jupiter_data = await self._get_jupiter_data(token_address)
        dexscreener_data = await self._get_dexscreener_data(token_address)
        solana_rpc_data = await self._get_solana_rpc_data(token_address)
        holders_data = await self._get_token_holders(token_address)

        # honeypot primary + fallback
        honeypot_check = await self._honeypot_with_fallback(token_address, dexscreener_data, solana_rpc_data)

        # dev address detection (robust)
        dev_address = self._extract_dev_from_jupiter(jupiter_data)

        dev_activity = await self._get_dev_activity(dev_address) if dev_address else None

        # pair / lp owner if available
        pair_address = self._extract_pair_from_dexscreener(dexscreener_data)
        lp_owner = await self._get_lp_owner(pair_address) if pair_address else None

        analysis_time = time.time() - start_time

        result = {
            "token_address": token_address,
            "timestamp": datetime.now().isoformat(),
            "analysis_time": f"{analysis_time:.2f}s",
            "raw_data": {
                "jupiter": jupiter_data,
                "dexscreener": dexscreener_data,
                "solana_rpc": {
                    **solana_rpc_data,
                    "largest_accounts": holders_data,
                    "dev_activity": dev_activity
                }
            },
            "security": {
                "honeypot_check": honeypot_check,
                "lp_owner": lp_owner,
                "dev_address": dev_address
            }
        }

        # Зберігаємо аналіз в SQLite
        await self.save_analysis(result)

        return result

    async def save_analysis(self, analysis: Dict[str, Any]) -> bool:
        """Збереження аналізу токену в SQLite"""
        try:
            # Створюємо директорію якщо її немає
            import os
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Створюємо таблицю для аналізу, якщо її немає
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS token_analysis (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        token_address TEXT,
                        timestamp TIMESTAMP,
                        analysis_time TEXT,
                        
                        -- Jupiter дані
                        jupiter_data TEXT,
                        
                        -- DexScreener дані
                        dexscreener_data TEXT,
                        
                        -- Solana RPC дані
                        solana_rpc_data TEXT,
                        
                        -- Безпека
                        honeypot_check TEXT,
                        lp_owner TEXT,
                        dev_address TEXT,
                        
                        FOREIGN KEY(token_address) REFERENCES tokens(id)
                    )
                """)

                # Вставляємо дані аналізу
                cursor.execute("""
                    INSERT INTO token_analysis (
                        token_address, timestamp, analysis_time,
                        jupiter_data, dexscreener_data, solana_rpc_data,
                        honeypot_check, lp_owner, dev_address
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    analysis['token_address'],
                    analysis['timestamp'],
                    analysis['analysis_time'],
                    json.dumps(analysis['raw_data']['jupiter']),
                    json.dumps(analysis['raw_data']['dexscreener']),
                    json.dumps(analysis['raw_data']['solana_rpc']),
                    json.dumps(analysis['security']['honeypot_check']),
                    analysis['security']['lp_owner'],
                    analysis['security']['dev_address']
                ))

                conn.commit()
                return True

        except Exception as e:
            if self.debug:
                print(f"Error saving analysis: {e}")
            return False

    # ... (решта методів з оригінального файлу) ...

    async def _fetch_with_retries(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        """Універсальна GET з ретраями. Повертає dict: {'ok': bool, 'status': int, 'json': ..., 'error': str}"""
        last_exc = None
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                self._debug_print(f"fetch try {attempt} -> {url}")
                async with self.session.get(url, **kwargs) as resp:
                    status = resp.status
                    text = await resp.text()
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
                    if 200 <= status < 300:
                        return {"ok": True, "status": status, "json": parsed, "text": text}
                    else:
                        return {"ok": False, "status": status, "json": parsed, "text": text,
                                "error": f"HTTP {status}"}
            except Exception as e:
                last_exc = e
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
                self._debug_print(f"fetch error {e}, backoff {backoff:.2f}s")
                await asyncio.sleep(backoff)
        return {"ok": False, "status": None, "json": None, "text": None, "error": str(last_exc)}

    async def _post_rpc_with_retries(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                self._debug_print("rpc try", attempt, payload.get("method"))
                async with self.session.post(self.solana_rpc_url, json=payload, timeout=10) as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
                    if 200 <= status < 300:
                        return {"ok": True, "status": status, "json": data}
                    else:
                        return {"ok": False, "status": status, "json": data, "error": f"HTTP {status}"}
            except Exception as e:
                last_exc = e
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
                self._debug_print(f"rpc error {e}, backoff {backoff:.2f}s")
                await asyncio.sleep(backoff)
        return {"ok": False, "status": None, "json": None, "error": str(last_exc)}

    async def _get_jupiter_data(self, token_address: str) -> Any:
        try:
            url = f"https://lite-api.jup.ag/tokens/v2/search?query={token_address}"
            res = await self._fetch_with_retries("GET", url, headers={"User-Agent": "Mozilla/5.0"})
            if res["ok"]:
                return res["json"]
            return {"error": res.get("error") or f"HTTP {res.get('status')}"}
        except Exception as e:
            return {"error": str(e)}

    async def _get_dexscreener_data(self, token_address: str) -> Any:
        try:
            url = f"https://api.dexscreener.com/latest/dex/search/?q={token_address}"
            res = await self._fetch_with_retries("GET", url)
            if res["ok"]:
                return res["json"]
            return {"error": res.get("error") or f"HTTP {res.get('status')}"}
        except Exception as e:
            return {"error": str(e)}

    async def _get_solana_rpc_data(self, token_address: str) -> Dict[str, Any]:
        rpc_data: Dict[str, Any] = {}
        # getAccountInfo
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [token_address, {"encoding": "json"}]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_account_info"] = res["json"].get("result") if res["ok"] and res.get("json") else None

        # getTokenSupply
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [token_address]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_supply"] = res["json"].get("result") if res["ok"] and res.get("json") else None

        # getAccountInfo jsonParsed (metadata)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [token_address, {"encoding": "jsonParsed"}]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_metadata"] = res["json"].get("result") if res["ok"] and res.get("json") else None

        # getSignaturesForAddress (recent)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [token_address, {"limit": 12}]}
        res = await self._post_rpc_with_retries(payload)
        signatures = res["json"].get("result") if res["ok"] and res.get("json") else []
        rpc_data["recent_signatures"] = signatures

        # If we have signatures, fetch transactions for a few to inspect sells (light)
        txs = []
        if isinstance(signatures, list):
            # take first up to 6 signatures
            for sig_item in signatures[:6]:
                sig = sig_item.get("signature") if isinstance(sig_item, dict) else sig_item
                if not sig:
                    continue
                payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [sig, {"encoding": "jsonParsed"}]}
                r = await self._post_rpc_with_retries(payload)
                if r["ok"] and r.get("json"):
                    txs.append(r["json"].get("result"))
        rpc_data["recent_transactions_parsed"] = txs
        return rpc_data

    async def _get_token_holders(self, token_address: str) -> Dict[str, Any]:
        """getTokenLargestAccounts -> normalize result.value"""
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [token_address]}
            res = await self._post_rpc_with_retries(payload)
            if res["ok"] and res.get("json"):
                result = res["json"].get("result")
                if isinstance(result, dict):
                    # modern RPC: result.value -> list
                    val = result.get("value")
                    if isinstance(val, list):
                        return {"value": val}
                # sometimes result is list directly
                if isinstance(result, list):
                    return {"value": result}
            return {"error": res.get("error") or "no_result"}
        except Exception as e:
            return {"error": str(e)}

    async def _get_dev_activity(self, dev_address: str) -> Optional[List[Dict[str, Any]]]:
        if not dev_address:
            return None
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [dev_address, {"limit": 10}]}
            res = await self._post_rpc_with_retries(payload)
            if res["ok"] and res.get("json"):
                return res["json"].get("result")
            return None
        except Exception:
            return None

    async def _get_lp_owner(self, pair_address: str) -> Optional[str]:
        if not pair_address:
            return None
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [pair_address, {"encoding": "jsonParsed"}]}
            res = await self._post_rpc_with_retries(payload)
            if res["ok"] and res.get("json"):
                account = res["json"].get("result", {}).get("value")
                if isinstance(account, dict):
                    return account.get("owner")
            return None
        except Exception:
            return None

    # ---------------- Honeypot with fallback ----------------

    async def _honeypot_with_fallback(self, token_address: str, dexscreener_data: Any, solana_rpc_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        1) Спробувати Jupiter Quote API (buy + sell).
        2) Якщо недоступний — fallback:
           - використовувати dexscreener.txns (sells > 0)
           - або парсити RPC recent_transactions_parsed на наявність swap/sell (transfer out to quote token)
        """
        reasons = []
        result = {"checked_by": [], "buy_possible": None, "sell_possible": None, "honeypot": None, "reasons": []}

        # 1) Try Jupiter quote API
        quote_buy_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount=1000000"
        quote_sell_url = f"https://quote-api.jup.ag/v6/quote?inputMint={token_address}&outputMint=So11111111111111111111111111111111111111112&amount=1000000"
        buy_res = await self._fetch_with_retries("GET", quote_buy_url)
        sell_res = await self._fetch_with_retries("GET", quote_sell_url)

        # If both ok and no "error" field -> reliable
        if buy_res["ok"] and sell_res["ok"]:
            buy_err = buy_res["json"] and isinstance(buy_res["json"], dict) and buy_res["json"].get("error")
            sell_err = sell_res["json"] and isinstance(sell_res["json"], dict) and sell_res["json"].get("error")
            buy_ok = buy_res["ok"] and not buy_err
            sell_ok = sell_res["ok"] and not sell_err
            result.update({
                "checked_by": ["jupiter_quote_api"],
                "buy_possible": bool(buy_ok),
                "sell_possible": bool(sell_ok),
                "honeypot": not bool(sell_ok)
            })
            if not sell_ok:
                result["reasons"].append("Jupiter quote sell failed or returned error")
            return result

        # 2) Fallback: DexScreener quick check
        ds_checked = False
        try:
            if isinstance(dexscreener_data, dict):
                pairs = dexscreener_data.get("pairs") or []
                if pairs and isinstance(pairs, list):
                    p0 = pairs[0]
                    txns = p0.get("txns") or {}
                    # check sells in last windows
                    sells = 0
                    for window in ("m5", "h1", "h6", "h24"):
                        sells += (txns.get(window) or {}).get("sells", 0) or 0
                    result["checked_by"].append("dexscreener_txns")
                    ds_checked = True
                    result["buy_possible"] = None  # unknown
                    result["sell_possible"] = sells > 0
                    result["honeypot"] = not (sells > 0)
                    if sells == 0:
                        result["reasons"].append("DexScreener reports zero sells in recent windows")
                    else:
                        result["reasons"].append(f"DexScreener reports sells={sells} in recent windows")
        except Exception as e:
            self._debug_print("dexscreener fallback error:", e)

        # 3) Fallback: Inspect recent parsed RPC txs for sell patterns
        rpc_checked = False
        try:
            parsed_txs = solana_rpc_data.get("recent_transactions_parsed", [])
            sells_found = 0
            for tx in parsed_txs:
                if not isinstance(tx, dict):
                    continue
                # Attempt to detect: presence of token transfer from user to pool and pool->quote (simplified heuristic)
                meta = tx.get("meta") or {}
                post_token_balances = meta.get("postTokenBalances") or []
                pre_token_balances = meta.get("preTokenBalances") or []
                # If token amount decreased from pre->post for some owner (seller) and SOL/wrapped SOL increased in pool -> mark as sell
                # Simplified: compare sum minted/burned values for our token and SOL mint (So1111...)
                # Count any tx that has fewer token amount in post than pre for any account as potential sell
                try:
                    for i_pre in pre_token_balances:
                        for i_post in post_token_balances:
                            if i_pre.get("mint") == i_post.get("mint") == token_address:
                                pre_amount = float(i_pre.get("uiTokenAmount", {}).get("uiAmount") or 0)
                                post_amount = float(i_post.get("uiTokenAmount", {}).get("uiAmount") or 0)
                                if post_amount < pre_amount:
                                    sells_found += 1
                                    break
                except Exception:
                    pass
            rpc_checked = True
            result["checked_by"].append("rpc_recent_txs")
            result["sell_possible"] = result.get("sell_possible") or (sells_found > 0)
            result["honeypot"] = result.get("honeypot") if result.get("honeypot") is not None else not (sells_found > 0)
            result["reasons"].append(f"RPC parsed recent txs sells_found={sells_found}")
        except Exception as e:
            self._debug_print("rpc fallback error:", e)

        # If nothing could check, return error
        if not result["checked_by"]:
            result["checked_by"] = ["none"]
            result["reasons"].append("No reliable method succeeded: network issues or APIs down")
            result["buy_possible"] = None
            result["sell_possible"] = None
            result["honeypot"] = None

        return result

    # ---------------- Utilities ----------------

    def _extract_dev_from_jupiter(self, jupiter_data: Any) -> Optional[str]:
        # Jupiter lite search can return list or dict; try several keys used earlier
        try:
            if isinstance(jupiter_data, list) and jupiter_data:
                first = jupiter_data[0]
                # common fields: "dev", "dev_address", "devAddress"
                return first.get("dev") or first.get("dev_address") or first.get("devAddress")
            if isinstance(jupiter_data, dict):
                # sometimes wrap: token object inside "tokens" or top-level keys
                if "dev_address" in jupiter_data:
                    return jupiter_data.get("dev_address")
                # fallback
                for k in ("dev", "dev_address", "devAddress"):
                    if k in jupiter_data:
                        return jupiter_data.get(k)
            return None
        except Exception:
            return None

    def _extract_pair_from_dexscreener(self, dexscreener_data: Any) -> Optional[str]:
        try:
            if isinstance(dexscreener_data, dict):
                pairs = dexscreener_data.get("pairs") or []
                if isinstance(pairs, list) and pairs:
                    p0 = pairs[0]
                    # dexscreener uses "pairAddress" or "pairAddress"
                    return p0.get("pairAddress") or p0.get("pairAddress".lower())
            return None
        except Exception:
            return None

    # ---------------- Output ----------------

    def display_analysis(self, analysis: Dict[str, Any]):
        print(json.dumps(analysis, indent=2, ensure_ascii=False))

    def export_to_file(self, analysis: Dict[str, Any], filename: Optional[str] = None):
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            token_short = analysis['token_address'][:8]
            filename = f"db/raw_data_{token_short}_{timestamp}.json"
        os.makedirs("db", exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)
        print(f"📁 Raw JSON data saved to: {filename}")

# ---------------- Main entry ----------------
async def main(token_address: str = None, debug: bool = False):
    if not token_address:
        token_address = input("🔍 Введіть адресу токену: ").strip()
        if not token_address:
            print("❌ Адреса токену не може бути пустою!")
            return

    async with TokenAnalyzerSQL(debug=debug) as analyzer:
        analysis = await analyzer.analyze_token(token_address)
        analyzer.display_analysis(analysis)
        analyzer.export_to_file(analysis)

if __name__ == "__main__":
    import sys
    token = sys.argv[1] if len(sys.argv) > 1 else None
    debug_flag = "--debug" in sys.argv
    asyncio.run(main(token_address=token, debug=debug_flag))
