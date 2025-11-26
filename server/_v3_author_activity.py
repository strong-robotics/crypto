"""
Author activity tracker.
Fetches transfers for a given wallet from Helius API and stores them in author_activity table.
"""

from __future__ import annotations

import aiohttp
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from typing import Any, Dict, List, Optional

from config import config
from _v3_db_pool import get_db_pool

LAMPORTS_IN_SOL = Decimal("1000000000")
getcontext().prec = 50


@dataclass
class ActivityRow:
    dedupe_key: str
    author_wallet: str
    direction: str
    source: str
    signature: str
    slot: Optional[int]
    block_time: Optional[datetime]
    transfer_type: str
    token_mint: Optional[str]
    token_account: Optional[str]
    target_wallet: Optional[str]
    amount_raw: Optional[Decimal]
    amount_ui: Optional[Decimal]
    amount_decimals: Optional[int]


class AuthorActivityRecorder:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = config.HELIUS_API_BASE.rstrip("/")
        self._api_key = config.HELIUS_API_KEY

    async def ensure_session(self):
        if not self._session:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def fetch_transactions(
        self,
        author_wallet: str,
        limit: int = 100,
        before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not self._api_key:
            raise RuntimeError("HELIUS_API_KEY is not configured")

        await self.ensure_session()

        params = {
            "api-key": self._api_key,
            "limit": max(1, min(limit, config.HISTORY_HELIUS_LIMIT)),
        }
        if before:
            params["before"] = before

        url = f"{self._base_url}/v0/addresses/{author_wallet}/transactions"
        if self.debug:
            print(f"📡 Helius request: {url} params={params}")

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Helius HTTP {resp.status}: {text}")
            data = await resp.json()
            if self.debug:
                print(f"✅ Helius response: {len(data)} transactions")
            return data

    async def fetch_and_store(
        self,
        author_wallet: str,
        limit: int = 100,
        before: Optional[str] = None,
        include_incoming: bool = False,
    ) -> Dict[str, Any]:
        transactions = await self.fetch_transactions(author_wallet, limit=limit, before=before)
        rows = self._parse_transactions(
            author_wallet=author_wallet,
            transactions=transactions,
            include_incoming=include_incoming,
        )
        saved = await self._store_rows(rows)
        return {
            "success": True,
            "fetched": len(transactions),
            "parsed": len(rows),
            "inserted": saved["inserted"],
            "updated": saved["updated"],
        }

    def _parse_transactions(
        self,
        author_wallet: str,
        transactions: List[Dict[str, Any]],
        include_incoming: bool = False,
    ) -> List[ActivityRow]:
        rows: List[ActivityRow] = []
        for tx in transactions:
            signature = tx.get("signature") or ""
            slot = tx.get("slot")
            ts_val = tx.get("timestamp")
            block_time = (
                datetime.fromtimestamp(ts_val, tz=timezone.utc).replace(tzinfo=None)
                if isinstance(ts_val, (int, float))
                else None
            )

            token_transfers = tx.get("tokenTransfers") or []
            for transfer in token_transfers:
                row = self._parse_token_transfer(
                    author_wallet=author_wallet,
                    transfer=transfer,
                    signature=signature,
                    slot=slot,
                    block_time=block_time,
                    include_incoming=include_incoming,
                )
                if row:
                    rows.append(row)

            native_transfers = tx.get("nativeTransfers") or []
            for transfer in native_transfers:
                row = self._parse_native_transfer(
                    author_wallet=author_wallet,
                    transfer=transfer,
                    signature=signature,
                    slot=slot,
                    block_time=block_time,
                    include_incoming=include_incoming,
                )
                if row:
                    rows.append(row)

        return rows

    def _parse_token_transfer(
        self,
        author_wallet: str,
        transfer: Dict[str, Any],
        signature: str,
        slot: Optional[int],
        block_time: Optional[datetime],
        include_incoming: bool,
    ) -> Optional[ActivityRow]:
        direction, target = self._resolve_direction(
            author_wallet,
            transfer.get("fromUserAccount"),
            transfer.get("toUserAccount"),
            include_incoming=include_incoming,
        )
        if not direction:
            return None

        raw_info = transfer.get("rawTokenAmount") or {}
        mint = transfer.get("mint")

        decimals = raw_info.get("decimals")
        raw_amount = raw_info.get("tokenAmount")

        amount_ui: Optional[Decimal] = None
        amount_raw: Optional[Decimal] = None

        if raw_amount is not None:
            amount_raw = Decimal(str(raw_amount))
        token_amount_field = transfer.get("tokenAmount")
        if token_amount_field is not None:
            amount_ui = Decimal(str(token_amount_field))

        if amount_raw is None and amount_ui is not None:
            decimals = decimals or 0
            amount_raw = (amount_ui * (Decimal(10) ** Decimal(decimals))).quantize(Decimal("1"))
        elif amount_raw is not None and amount_ui is None:
            decimals = decimals or 0
            denominator = Decimal(10) ** Decimal(decimals)
            amount_ui = amount_raw / denominator if denominator != 0 else Decimal(0)

        if amount_raw is None and amount_ui is None:
            return None

        token_account = (
            transfer.get("toTokenAccount") if direction == "outgoing" else transfer.get("fromTokenAccount")
        )

        dedupe_key = self._dedupe_key(
            signature=signature,
            transfer_type="token",
            direction=direction,
            token_account=token_account,
            target_wallet=target,
            token_mint=mint,
        )

        return ActivityRow(
            dedupe_key=dedupe_key,
            author_wallet=author_wallet,
            direction=direction,
            source="helius",
            signature=signature,
            slot=slot,
            block_time=block_time,
            transfer_type="token",
            token_mint=mint,
            token_account=token_account,
            target_wallet=target,
            amount_raw=amount_raw,
            amount_ui=amount_ui,
            amount_decimals=decimals,
        )

    def _parse_native_transfer(
        self,
        author_wallet: str,
        transfer: Dict[str, Any],
        signature: str,
        slot: Optional[int],
        block_time: Optional[datetime],
        include_incoming: bool,
    ) -> Optional[ActivityRow]:
        direction, target = self._resolve_direction(
            author_wallet,
            transfer.get("fromUserAccount"),
            transfer.get("toUserAccount"),
            include_incoming=include_incoming,
        )
        if not direction:
            return None

        lamports = transfer.get("amount")
        if lamports is None:
            return None

        amount_raw = Decimal(str(lamports))
        amount_ui = amount_raw / LAMPORTS_IN_SOL

        dedupe_key = self._dedupe_key(
            signature=signature,
            transfer_type="native",
            direction=direction,
            token_account=None,
            target_wallet=target,
            token_mint=None,
        )

        return ActivityRow(
            dedupe_key=dedupe_key,
            author_wallet=author_wallet,
            direction=direction,
            source="helius",
            signature=signature,
            slot=slot,
            block_time=block_time,
            transfer_type="native",
            token_mint=None,
            token_account=None,
            target_wallet=target,
            amount_raw=amount_raw,
            amount_ui=amount_ui,
            amount_decimals=9,
        )

    def _resolve_direction(
        self,
        author_wallet: str,
        from_user: Optional[str],
        to_user: Optional[str],
        include_incoming: bool,
    ) -> (Optional[str], Optional[str]):
        if from_user == author_wallet:
            return "outgoing", to_user
        if include_incoming and to_user == author_wallet:
            return "incoming", from_user
        return None, None

    def _dedupe_key(
        self,
        signature: str,
        transfer_type: str,
        direction: str,
        token_account: Optional[str],
        target_wallet: Optional[str],
        token_mint: Optional[str],
    ) -> str:
        parts = [
            signature or "",
            transfer_type or "",
            direction or "",
            token_account or "",
            target_wallet or "",
            token_mint or "",
        ]
        return "|".join(parts)

    async def _store_rows(self, rows: List[ActivityRow]) -> Dict[str, int]:
        if not rows:
            return {"inserted": 0, "updated": 0}

        pool = await get_db_pool()
        insert_sql = """
            INSERT INTO author_activity (
                dedupe_key,
                author_wallet,
                direction,
                source,
                signature,
                slot,
                block_time,
                transfer_type,
                token_mint,
                token_account,
                target_wallet,
                amount_raw,
                amount_ui,
                amount_decimals
            )
            VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14
            )
            ON CONFLICT (dedupe_key) DO UPDATE SET
                slot = EXCLUDED.slot,
                block_time = EXCLUDED.block_time,
                amount_raw = EXCLUDED.amount_raw,
                amount_ui = EXCLUDED.amount_ui,
                amount_decimals = EXCLUDED.amount_decimals,
                token_account = COALESCE(EXCLUDED.token_account, author_activity.token_account),
                target_wallet = COALESCE(EXCLUDED.target_wallet, author_activity.target_wallet),
                direction = EXCLUDED.direction
            RETURNING (xmax = 0) AS inserted_flag
        """

        inserted = 0
        updated = 0

        async with pool.acquire() as conn:
            async with conn.transaction():
                for row in rows:
                    record = await conn.fetchrow(
                        insert_sql,
                        row.dedupe_key,
                        row.author_wallet,
                        row.direction,
                        row.source,
                        row.signature,
                        row.slot,
                        row.block_time,
                        row.transfer_type,
                        row.token_mint,
                        row.token_account,
                        row.target_wallet,
                        row.amount_raw,
                        row.amount_ui,
                        row.amount_decimals,
                    )
                    if record and record["inserted_flag"]:
                        inserted += 1
                    else:
                        updated += 1

        if self.debug:
            print(f"💾 Stored author activity: {inserted} inserted, {updated} updated")

        return {"inserted": inserted, "updated": updated}

    async def get_watch_candidates(
        self,
        author_wallet: Optional[str] = None,
        limit: int = 20,
        only_without_mint: bool = True,
    ) -> List[Dict[str, Any]]:
        pool = await get_db_pool()
        params: List[Any] = []
        where_clauses = ["direction = 'outgoing'"]
        if author_wallet:
            params.append(author_wallet)
            where_clauses.append(f"author_wallet = ${len(params)}")
        if only_without_mint:
            where_clauses.append("token_mint IS NULL")

        params.append(limit)
        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
        sql = f"""
            SELECT id, author_wallet, target_wallet, token_account, token_mint,
                   transfer_type, amount_ui, amount_raw, slot, signature, block_time
            FROM author_activity
            WHERE {where_sql}
            ORDER BY slot DESC NULLS LAST, id DESC
            LIMIT ${len(params)}
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [
            {
                "id": row["id"],
                "author_wallet": row["author_wallet"],
                "target_wallet": row["target_wallet"],
                "token_account": row["token_account"],
                "token_mint": row["token_mint"],
                "transfer_type": row["transfer_type"],
                "amount_ui": float(row["amount_ui"]) if row["amount_ui"] is not None else None,
                "amount_raw": str(row["amount_raw"]) if row["amount_raw"] is not None else None,
                "slot": row["slot"],
                "signature": row["signature"],
                "block_time": row["block_time"].isoformat() if row["block_time"] else None,
            }
            for row in rows
        ]


async def main_example():
    recorder = AuthorActivityRecorder(debug=True)
    try:
        res = await recorder.fetch_and_store(author_wallet="AoX3EMzVXCNBdCNvboc7yGM4gsr3wcKd7hGsZ4yXcydU", limit=25)
        print(res)
    finally:
        await recorder.close()


if __name__ == "__main__":
    asyncio.run(main_example())
