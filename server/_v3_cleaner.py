#!/usr/bin/env python3
"""
V3 Cleaner – safely purge orphan tokens without a valid trading pair.

Rules:
- Candidate if:
    * token_pair IS NULL OR token_pair = token_address
    * history_ready IS NOT TRUE
    * created_at < now() - older_than_sec
    * no rows in token_metrics_seconds and no rows in trades for this token_id
- Full purge: delete from token_metrics_seconds, trades, then tokens.

Concurrency:
- Uses pg_try_advisory_lock to avoid parallel cleaners.

Usage examples:
  python3 -m server._v3_cleaner --dry-run
  python3 -m server._v3_cleaner --older 15 --limit 200
  python3 -m server._v3_cleaner --no-dry-run --older 20 --limit 500 --loop
"""

import argparse
import asyncio
from typing import List, Tuple, Dict

from datetime import timedelta

from config import config
from _v3_db_pool import get_db_pool
from _v3_token_archiver import archive_token
FLAG_COLUMNS = [
    ("cleaner_flagged", "BOOLEAN DEFAULT FALSE"),
    ("cleaner_flag_reason", "TEXT"),
    ("cleaner_flag_iteration", "INTEGER"),
    ("cleaner_flagged_at", "TIMESTAMPTZ"),
]

_FLAG_COLUMNS_ENSURED = False
_BAD_TABLES_ENSURED = False


ADVISORY_LOCK_KEY = 0x76635F636C65616E  # arbitrary "v3_clean" key
SECOND_CORRIDOR_SEC = int(getattr(config, "PRICE_CORRIDOR_PRE_END", 85))


async def _acquire_lock(conn) -> bool:
    row = await conn.fetchval("SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY)
    return bool(row)


async def _release_lock(conn) -> None:
    try:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
    except Exception:
        pass


async def _find_candidates(conn, older_than_sec: int, limit: int) -> List[int]:
    """Find orphan tokens without a valid pair using per-second metric timestamps.

    Кандидат =
      - token_pair IS NULL OR token_pair = token_address
      - по таблице token_metrics_seconds есть хотя бы N=older_than_sec "итераций" (секунд)
        и разница между max(ts) и min(ts) по этому токену >= older_than_sec-1.

    Это эквивалентно правилу: "токен прожил N секунд по локальной метрике и
    так и не получил пару".
    
    Note: Archived tokens are in tokens_history table, so this function only queries tokens table (live tokens).
    """
    rows = await conn.fetch(
        """
        WITH m AS (
          SELECT token_id,
                 MIN(ts) AS min_ts,
                 MAX(ts) AS max_ts,
                 COUNT(*) AS cnt
          FROM token_metrics_seconds
          GROUP BY token_id
        )
        SELECT t.id
        FROM tokens t
        JOIN m ON m.token_id = t.id
        WHERE (t.token_pair IS NULL OR t.token_pair = t.token_address)
          AND m.cnt >= $2
          AND (m.max_ts - m.min_ts) >= ($3 - 1)
        ORDER BY m.min_ts ASC
        LIMIT $1
        """,
        limit,
        older_than_sec,            # минимум – по параметру (например, 4 итераций)
        older_than_sec,
    )
    return [int(r["id"]) for r in rows]


async def _find_no_entry_candidates(conn, max_age_sec: int, limit: int) -> List[int]:
    """Disabled: previously purged tokens with pair but no entry; keep alive now."""
    return []


async def _find_no_entry_iterations(conn, min_iterations: int, limit: int) -> List[int]:
    """Disabled: previously purged tokens with pair and many iterations but no entry; keep alive now."""
    return []


async def _find_low_holder_tokens(
    conn,
    min_iterations: int,
    min_holders: int,
    limit: int,
) -> List[int]:
    """Find tokens that живут очень долго, но не набрали нужного количества холдеров."""
    if limit <= 0 or min_iterations <= 0 or min_holders <= 0:
        return []
    rows = await conn.fetch(
        """
        WITH metric_counts AS (
            SELECT token_id, COUNT(*) AS cnt
            FROM token_metrics_seconds
            GROUP BY token_id
        )
        SELECT t.id
        FROM tokens t
        JOIN metric_counts mc ON mc.token_id = t.id
        WHERE mc.cnt >= $2
          AND COALESCE(t.holder_count, 0) < $3
          AND t.wallet_id IS NULL
        ORDER BY mc.cnt DESC
        LIMIT $1
        """,
        limit,
        min_iterations,
        min_holders,
    )
    return [int(r["id"]) for r in rows]


async def _find_no_swap_tokens(conn, limit: int) -> List[int]:
    """Find tokens flagged как no_swap_after_second_corridor."""
    if limit <= 0:
        return []
    rows = await conn.fetch(
        """
        SELECT id
        FROM tokens
        WHERE no_swap_after_second_corridor = TRUE
        ORDER BY updated_at ASC
        LIMIT $1
        """,
        limit,
    )
    return [int(r["id"]) for r in rows]


async def _find_no_pair_tokens(conn, older_than_sec: int, limit: int) -> List[int]:
    """Catch tokens that давно без валидной пары (token_pair NULL/==address)."""
    if limit <= 0:
        return []
    rows = await conn.fetch(
        """
        SELECT id
        FROM tokens
        WHERE (token_pair IS NULL OR token_pair = token_address)
          AND EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(created_at, CURRENT_TIMESTAMP))) >= $2
        ORDER BY created_at ASC
        LIMIT $1
        """,
        limit,
        older_than_sec,
    )
    return [int(r["id"]) for r in rows]


async def _find_no_price_tokens(conn, limit: int) -> List[int]:
    """Catch tokens that достигли второго коридора, но так и не получили usd_price."""
    if limit <= 0:
        return []
    rows = await conn.fetch(
        """
        SELECT t.id
        FROM tokens t
        WHERE EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(t.created_at, CURRENT_TIMESTAMP))) >= $2
          AND NOT EXISTS (
              SELECT 1
              FROM token_metrics_seconds ms
              WHERE ms.token_id = t.id
                AND ms.usd_price IS NOT NULL
                AND ms.usd_price > 0
          )
        ORDER BY t.created_at ASC
        LIMIT $1
        """,
        limit,
        SECOND_CORRIDOR_SEC,
    )
    return [int(r["id"]) for r in rows]


async def _purge_batch(conn, ids: List[int]) -> Tuple[int, int, int]:
    if not ids:
        return (0, 0, 0)
    # Delete metrics → trades → tokens
    m = await conn.execute("DELETE FROM token_metrics_seconds WHERE token_id = ANY($1)", ids)
    t = await conn.execute("DELETE FROM trades WHERE token_id = ANY($1)", ids)
    x = await conn.execute("DELETE FROM tokens WHERE id = ANY($1)", ids)
    # Convert results like 'DELETE 5' → 5
    def _n(s: str) -> int:
        try:
            return int((s or '').split()[-1])
        except Exception:
            return 0
    return (_n(m), _n(t), _n(x))


async def _ensure_flag_columns(conn) -> None:
    global _FLAG_COLUMNS_ENSURED
    if _FLAG_COLUMNS_ENSURED:
        return
    for column, definition in FLAG_COLUMNS:
        await conn.execute(
            f"ALTER TABLE tokens ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
    _FLAG_COLUMNS_ENSURED = True


async def _flag_tokens(conn, ids: List[int], reason: str) -> int:
    if not ids:
        return 0
    rows = await conn.fetch(
        """
        SELECT token_id, COUNT(*) AS cnt
        FROM token_metrics_seconds
        WHERE token_id = ANY($1)
        GROUP BY token_id
        """,
        ids,
    )
    counts: Dict[int, int] = {int(r["token_id"]): int(r["cnt"] or 0) for r in rows}
    total = 0
    for tid in ids:
        iter_count = counts.get(tid, 0)
        await conn.execute(
            """
            UPDATE tokens
            SET cleaner_flagged = TRUE,
                cleaner_flag_reason = $2,
                cleaner_flag_iteration = $3,
                cleaner_flagged_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            tid,
            reason,
            iter_count,
        )
        total += 1
    return total


async def _get_iteration_counts(conn, ids: List[int]) -> Dict[int, int]:
    if not ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT token_id, COUNT(*) AS cnt
        FROM token_metrics_seconds
        WHERE token_id = ANY($1)
        GROUP BY token_id
        """,
        ids,
    )
    return {int(r["token_id"]): int(r["cnt"] or 0) for r in rows}


async def _process_flagged_tokens(conn, ids: List[int], reason: str, archive_threshold: int) -> Tuple[int, int]:
    if not ids:
        return (0, 0)
    counts = await _get_iteration_counts(conn, ids)
    archive_ids: List[int] = []
    bad_ids: List[int] = []
    for tid in ids:
        iter_count = counts.get(tid, 0)
        if archive_threshold > 0 and iter_count >= archive_threshold:
            archive_ids.append(tid)
        else:
            bad_ids.append(tid)
    archived = 0
    if archive_ids:
        for tid in archive_ids:
            try:
                res = await archive_token(tid, conn=conn)
                if res.get("success"):
                    archived += 1
            except Exception:
                pass
    removed = 0
    if bad_ids:
        removed = await _move_to_bad_tables(conn, bad_ids, reason)
    return archived, removed


async def _find_flagged_tokens(conn, reason: str, limit: int) -> List[int]:
    if limit <= 0:
        return []
    rows = await conn.fetch(
        """
        SELECT id
        FROM tokens
        WHERE cleaner_flagged = TRUE
          AND cleaner_flag_reason = $1
        ORDER BY cleaner_flagged_at ASC NULLS LAST
        LIMIT $2
        """,
        reason,
        limit,
    )
    return [int(r["id"]) for r in rows]


async def _ensure_bad_tables(conn) -> None:
    global _BAD_TABLES_ENSURED
    if _BAD_TABLES_ENSURED:
        return
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bad_tokens (
            LIKE tokens INCLUDING ALL,
            removed_reason TEXT,
            removed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bad_token_metrics (
            LIKE token_metrics_seconds INCLUDING ALL
        )
        """
    )
    _BAD_TABLES_ENSURED = True


async def _move_to_bad_tables(conn, ids: List[int], reason: str) -> int:
    if not ids:
        return 0
    await conn.execute(
        """
        INSERT INTO bad_tokens
        SELECT t.*, $2 AS removed_reason, CURRENT_TIMESTAMP AS removed_at
        FROM tokens t
        WHERE t.id = ANY($1)
        """,
        ids,
        reason,
    )
    await conn.execute(
        """
        INSERT INTO bad_token_metrics
        SELECT *
        FROM token_metrics_seconds
        WHERE token_id = ANY($1)
        """,
        ids,
    )
    await _purge_batch(conn, ids)
    return len(ids)


async def run_cleanup(dry_run: bool = True, older_than_sec: int = 15, limit: int = 200,
                      no_entry_age_sec: int = 0, no_entry_iters: int = 80) -> dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await _ensure_flag_columns(conn)
        await _ensure_bad_tables(conn)
        locked = await _acquire_lock(conn)
        if not locked:
            return {"success": False, "message": "another cleaner is running"}
        try:
            holder_iter_threshold = int(getattr(config, "CLEANER_LOW_HOLDER_ITER_THRESHOLD", 0) or 0)
            holder_min_count = int(getattr(config, "CLEANER_LOW_HOLDER_MIN_COUNT", 0) or 0)
            holder_archive_ids: List[int] = []
            if holder_iter_threshold > 0 and holder_min_count > 0:
                holder_archive_ids = await _find_low_holder_tokens(
                    conn, holder_iter_threshold, holder_min_count, limit
                )

            ids: List[int] = []
            remaining = limit
            zero_tail_candidates: List[int] = []
            bad_removed: Dict[str, int] = {}
            archived_summary: Dict[str, int] = {}
            archive_threshold = int(getattr(config, "ARCHIVE_MIN_ITERATIONS", 0) or 0)

            flagged_ids = await _find_no_swap_tokens(conn, remaining)
            if flagged_ids:
                if dry_run:
                    ids.extend(flagged_ids)
                else:
                    await _purge_batch(conn, flagged_ids)
                    bad_removed["no_swap"] = bad_removed.get("no_swap", 0) + len(flagged_ids)
                remaining = max(0, remaining - len(flagged_ids))

            no_pair_ids = await _find_no_pair_tokens(conn, older_than_sec, remaining)
            if no_pair_ids:
                if dry_run:
                    ids.extend(no_pair_ids)
                else:
                    await _purge_batch(conn, no_pair_ids)
                    bad_removed["no_pair"] = bad_removed.get("no_pair", 0) + len(no_pair_ids)
                remaining = max(0, remaining - len(no_pair_ids))

            zero_tail_candidates = await _find_flagged_tokens(conn, "zero_tail", remaining)
            if zero_tail_candidates:
                if dry_run:
                    ids.extend(zero_tail_candidates)
                else:
                    archived, removed = await _process_flagged_tokens(conn, zero_tail_candidates, "zero_tail", archive_threshold)
                    if archived:
                        archived_summary["zero_tail"] = archived_summary.get("zero_tail", 0) + archived
                    if removed:
                        bad_removed["zero_tail"] = bad_removed.get("zero_tail", 0) + removed
                remaining = max(0, remaining - len(zero_tail_candidates))

            frozen_candidates = await _find_flagged_tokens(conn, "frozen_price", remaining)
            if frozen_candidates:
                if dry_run:
                    ids.extend(frozen_candidates)
                else:
                    archived, removed = await _process_flagged_tokens(conn, frozen_candidates, "frozen_price", archive_threshold)
                    if archived:
                        archived_summary["frozen_price"] = archived_summary.get("frozen_price", 0) + archived
                    if removed:
                        bad_removed["frozen_price"] = bad_removed.get("frozen_price", 0) + removed
                remaining = max(0, remaining - len(frozen_candidates))

            no_price_ids = await _find_no_price_tokens(conn, remaining)
            ids.extend(no_price_ids)
            remaining = max(0, limit - len(ids))

            orphan_ids = await _find_candidates(conn, older_than_sec, remaining)
            ids.extend(orphan_ids)

            ids = list(dict.fromkeys(ids))
            extra_processed = 0 if dry_run else (len(zero_tail_candidates) + len(frozen_candidates))
            total_candidates = len(holder_archive_ids) + len(ids) + extra_processed
            if total_candidates == 0:
                return {"success": True, "found": 0, "flagged": 0, "removed_to_bad": {}, "archived": {}}
            if dry_run:
                return {
                    "success": True,
                    "found": total_candidates,
                    "flagged": 0,
                    "ids": ids[:10],
                    "removed_to_bad": {},
                    "archived": {},
                }
            summary: Dict[str, int] = {}
            flagged_total = 0
            if no_price_ids:
                count = await _flag_tokens(conn, no_price_ids, "no_price")
                summary["no_price"] = count
                flagged_total += count
            if orphan_ids:
                count = await _flag_tokens(conn, orphan_ids, "orphan")
                summary["orphan"] = count
                flagged_total += count
            if holder_archive_ids:
                count = await _flag_tokens(conn, holder_archive_ids, "low_holders")
                summary["low_holders"] = count
                flagged_total += count
            return {
                "success": True,
                "found": total_candidates,
                "flagged": flagged_total,
                "summary": summary,
                "removed_to_bad": bad_removed,
                "archived": archived_summary,
            }
        finally:
            await _release_lock(conn)


async def run_until_empty(dry_run: bool, older_than_sec: int, limit: int,
                          no_entry_age_sec: int = 0, no_entry_iters: int = 80) -> dict:
    total_found = 0
    total_flagged = 0
    removed_summary: Dict[str, int] = {}
    archived_summary: Dict[str, int] = {}
    while True:
        res = await run_cleanup(dry_run=dry_run, older_than_sec=older_than_sec, limit=limit,
                                no_entry_age_sec=no_entry_age_sec, no_entry_iters=no_entry_iters)
        if not res.get("success"):
            return res
        total_found += res.get("found", 0)
        total_flagged += res.get("flagged", 0)
        removed = res.get("removed_to_bad") or {}
        for key, value in removed.items():
            removed_summary[key] = removed_summary.get(key, 0) + value
        archived = res.get("archived") or {}
        for key, value in archived.items():
            archived_summary[key] = archived_summary.get(key, 0) + value
        if res.get("found", 0) == 0 or dry_run:
            break
    return {
        "success": True,
        "found": total_found,
        "flagged": total_flagged,
        "removed_to_bad": removed_summary,
        "archived": archived_summary,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Purge orphan tokens without valid pair")
    p.add_argument("--older", type=int, default=15, help="Age in seconds to consider orphan (default: 15)")
    p.add_argument("--limit", type=int, default=200, help="Max tokens to purge per batch (default: 200)")
    p.add_argument("--no-dry-run", action="store_true", help="Actually delete (default: dry-run)")
    p.add_argument("--loop", action="store_true", help="Repeat batches until no candidates left (ignored in dry-run)")
    return p.parse_args()


def main():
    args = _parse_args()
    dry = not args.no_dry_run
    if args.loop and not dry:
        res = asyncio.run(run_until_empty(dry_run=dry, older_than_sec=args.older, limit=args.limit))
    else:
        res = asyncio.run(run_cleanup(dry_run=dry, older_than_sec=args.older, limit=args.limit))
    import json
    # print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
