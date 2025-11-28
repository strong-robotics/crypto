#!/usr/bin/env python3

import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import os
from ai.patterns.catalog import PATTERN_SEED
from fastapi import WebSocket
from _v3_db_pool import get_db_pool
from config import config

class TokensReaderV3:
    
    def __init__(self, debug: bool = False):
        self.debug = debug
        
        self.connected_clients: List[WebSocket] = []
        
        self.auto_refresh_task: Optional[asyncio.Task] = None
        self.refresh_interval: int = 1  # seconds
        self.last_token_count: int = 0
        self.total_token_count: int = 0
        self.last_updated_at: Optional[datetime] = None
        
        self.last_updated_at_sum: str = ""
        # Show historical tokens (archived tokens from tokens_history) when enabled in config/env
        self.history_mode: bool = bool(str(os.getenv("TOKENS_SHOW_HISTORY", getattr(config, 'TOKENS_SHOW_HISTORY', False))).lower() not in ("0", "false", "none", ""))
        # Disable sorting and show only historical tokens in insertion order
        self.disable_sort: bool = bool(str(os.getenv("TOKENS_DISABLE_SORT", getattr(config, 'TOKENS_DISABLE_SORT', False))).lower() not in ("0", "false", "none", ""))

    def _use_history_source(self) -> bool:
        """Return True when tokens history tables should be used."""
        return bool(self.history_mode or self.disable_sort)

    def set_history_mode(self, enabled: bool):
        self.history_mode = bool(enabled)
    
    async def ensure_connection(self):
        pass
    
    async def close(self):
        pass
    
    async def get_tokens_from_db(self, limit: int = 1000, offset: int = 0) -> Dict[str, Any]:
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                use_history = self._use_history_source()
                tokens_table = "tokens_history" if use_history else "tokens"
                metrics_table = "token_metrics_seconds_history" if use_history else "token_metrics_seconds"
                
                total_count = await conn.fetchval(f"SELECT COUNT(*) FROM {tokens_table}") or 0
                
                if use_history:
                    where_history = ""
                elif self.disable_sort:
                    # For disable_sort, we show tokens from tokens_history table (archived tokens)
                    # Since archived tokens are not in tokens table, this filter is not needed
                    where_history = ""
                else:
                    # Archived tokens are not in tokens table, so no filter needed
                    where_history = ""

                if use_history:
                    order_clause = "ORDER BY COALESCE(t.archived_at, t.token_updated_at, t.created_at) DESC"
                    archived_at_select = "t.archived_at"
                else:
                    order_clause = "ORDER BY t.created_at DESC"
                    archived_at_select = "NULL::timestamp AS archived_at"
                if self.disable_sort:
                    if use_history:
                        order_clause = "ORDER BY COALESCE(t.archived_at, t.token_updated_at, t.created_at) ASC"
                    else:
                        order_clause = "ORDER BY t.created_at ASC"

                sql = f"""
                    WITH 
                    lm_usd AS (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            usd_price
                        FROM {metrics_table}
                        WHERE usd_price IS NOT NULL AND usd_price > 0
                        ORDER BY token_id, ts DESC
                    ),
                    lm_liq AS (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            liquidity
                        FROM {metrics_table}
                        WHERE liquidity IS NOT NULL AND liquidity > 0
                        ORDER BY token_id, ts DESC
                    ),
                    lm_fdv AS (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            fdv
                        FROM {metrics_table}
                        WHERE fdv IS NOT NULL AND fdv > 0
                        ORDER BY token_id, ts DESC
                    ),
                    lm_mcap AS (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            mcap
                        FROM {metrics_table}
                        WHERE mcap IS NOT NULL AND mcap > 0
                        ORDER BY token_id, ts DESC
                    ),
                    lm_median AS (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            median_amount_usd
                        FROM {metrics_table}
                        WHERE median_amount_usd IS NOT NULL AND median_amount_usd <> ''
                        ORDER BY token_id, ts DESC
                    ),
                    median_trade_amount AS (
                        SELECT 
                            token_id,
                            percentile_cont(0.5) WITHIN GROUP (ORDER BY NULLIF(amount_usd, '')::numeric) AS median_amount_usd
                        FROM trades
                        WHERE amount_usd IS NOT NULL
                          AND amount_usd <> ''
                        GROUP BY token_id
                    ),
                    wallet_trade_amount AS (
                        WITH wallet_union AS (
                            SELECT token_id, entry_amount_usd AS amount
                            FROM wallet_history
                            WHERE entry_amount_usd IS NOT NULL AND entry_amount_usd > 0
                            UNION ALL
                            SELECT token_id, exit_amount_usd AS amount
                            FROM wallet_history
                            WHERE exit_amount_usd IS NOT NULL AND exit_amount_usd > 0
                        )
                        SELECT 
                            token_id,
                            percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) AS median_amount_usd
                        FROM wallet_union
                        GROUP BY token_id
                    ),
                    metrics_count AS (
                        SELECT 
                            token_id,
                            COUNT(*) AS iteration_count
                        FROM {metrics_table}
                        GROUP BY token_id
                    ),
                    open_positions AS (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            entry_token_amount,
                            entry_price_usd,
                            entry_iteration
                        FROM wallet_history
                        WHERE exit_iteration IS NULL
                        ORDER BY token_id, id DESC
                    ),
                    closed_positions AS (
                        SELECT DISTINCT ON (wh.token_id)
                            wh.token_id,
                            wh.exit_token_amount,
                            wh.exit_price_usd,
                            wh.exit_iteration,
                            wh.profit_usd
                        FROM wallet_history wh
                        INNER JOIN tokens t_closed ON t_closed.id = wh.token_id 
                            AND t_closed.wallet_id IS NOT NULL
                            AND t_closed.wallet_id = wh.wallet_id
                        WHERE wh.exit_iteration IS NOT NULL
                            AND NOT EXISTS (
                                SELECT 1
                                FROM wallet_history wh_open
                                WHERE wh_open.token_id = wh.token_id
                                    AND wh_open.wallet_id = wh.wallet_id
                                    AND wh_open.exit_iteration IS NULL
                            )
                        ORDER BY wh.token_id, wh.id DESC
                    )
                    SELECT 
                        t.id,
                        t.token_address,
                        t.token_pair,
                        t.name,
                        t.symbol,
                        t.icon,
                        t.decimals,
                        t.dev,
                        t.circ_supply,
                        t.total_supply,
                        t.token_program,
                        t.holder_count,
                        t.pattern_code AS pattern,
                        t.pattern_segment_1,
                        t.pattern_segment_2,
                        t.pattern_segment_3,
                        t.pattern_segment_decision,
                        t.has_real_trading,
                        t.swap_count,
                        t.transfer_count,
                        t.withdraw_count,
                        COALESCE(NULLIF(t.usd_price, 0), u.usd_price) AS usd_price,
                        COALESCE(NULLIF(t.liquidity, 0), l.liquidity) AS liquidity,
                        COALESCE(NULLIF(t.fdv, 0), f.fdv) AS fdv,
                        COALESCE(NULLIF(t.mcap, 0), m.mcap) AS mcap,
                        t.price_block_id,
                        t.organic_score,
                        t.organic_score_label,
                        t.blockaid_rugpull,
                        t.mint_authority_disabled,
                        t.freeze_authority_disabled, 
                        t.top_holders_percentage,
                        t.dev_balance_percentage,
                        t.price_change_5m,
                        t.holder_change_5m,
                        t.liquidity_change_5m,
                        t.volume_change_5m,
                        t.buy_volume_5m,
                        t.sell_volume_5m,
                        t.buy_organic_volume_5m,
                        t.sell_organic_volume_5m,
                        t.num_buys_5m,
                        t.num_sells_5m,
                        t.num_traders_5m,
                        t.price_change_1h,
                        t.holder_change_1h,
                        t.liquidity_change_1h,
                        t.volume_change_1h,
                        t.buy_volume_1h,
                        t.sell_volume_1h,
                        t.buy_organic_volume_1h,
                        t.sell_organic_volume_1h,
                        t.num_buys_1h,
                        t.num_sells_1h,
                        t.num_traders_1h,
                        t.price_change_6h,
                        t.holder_change_6h,
                        t.liquidity_change_6h,
                        t.volume_change_6h,
                        t.buy_volume_6h,
                        t.sell_volume_6h,
                        t.buy_organic_volume_6h,
                        t.sell_organic_volume_6h,
                        t.num_buys_6h,
                        t.num_sells_6h,
                        t.num_traders_6h,
                        t.price_change_24h,
                        t.holder_change_24h,
                        t.liquidity_change_24h,
                        t.volume_change_24h,
                        t.buy_volume_24h,
                        t.sell_volume_24h,
                        t.buy_organic_volume_24h,
                        t.sell_organic_volume_24h,
                        t.num_buys_24h,
                        t.num_sells_24h,
                        t.num_traders_24h,
                        t.is_honeypot,
                        t.honeypot_reason,
                        {archived_at_select},
                        t.created_at,
                        t.first_pool_created_at,
                        t.plan_sell_iteration,
                        t.plan_sell_price_usd,
                        t.cur_income_price_usd,
                        t.wallet_id,
                        t.pattern_code AS t_pattern_code,
                        p.code AS p_code,
                        p.name AS p_name,
                        COALESCE(mc.iteration_count, 0) AS iteration_count,
                        op.entry_token_amount,
                        op.entry_price_usd,
                        op.entry_iteration,
                        cp.exit_token_amount,
                        cp.exit_price_usd,
                        cp.exit_iteration,
                        cp.profit_usd,
                        COALESCE(
                            t.median_amount_usd,
                            NULLIF(med.median_amount_usd, '')::numeric,
                            mta.median_amount_usd,
                            wta.median_amount_usd,
                            0
                        ) AS median_amount_usd
                    FROM {tokens_table} t
                    LEFT JOIN lm_usd  u ON u.token_id = t.id
                    LEFT JOIN lm_liq  l ON l.token_id = t.id
                    LEFT JOIN lm_fdv  f ON f.token_id = t.id
                    LEFT JOIN lm_mcap m ON m.token_id = t.id
                    LEFT JOIN lm_median med ON med.token_id = t.id
                    LEFT JOIN median_trade_amount mta ON mta.token_id = t.id
                    LEFT JOIN wallet_trade_amount wta ON wta.token_id = t.id
                    LEFT JOIN metrics_count mc ON mc.token_id = t.id
                    LEFT JOIN open_positions op ON op.token_id = t.id
                    LEFT JOIN closed_positions cp ON cp.token_id = t.id
                    LEFT JOIN wallets w ON w.id = t.wallet_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            pattern_id,
                            confidence
                        FROM ai_token_patterns
                        ORDER BY token_id, confidence DESC, source ASC
                    ) atp ON atp.token_id = t.id
                    LEFT JOIN ai_patterns p ON p.id = atp.pattern_id
                    WHERE t.token_pair IS NOT NULL AND t.token_pair <> '' AND t.token_pair <> t.token_address{where_history}
                    {order_clause}
                    LIMIT $1 OFFSET $2
                """
                # For no-sort history view, ignore client-provided offset and fetch a large page
                if self.disable_sort:
                    limit = max(limit, 100000)
                    offset = 0
                rows = await conn.fetch(sql, limit, offset)
                
                # if self.debug:
                #     print(f"[TokensReader] SQL query returned {len(rows)} rows")
                #     if len(rows) > 0:
                #         print(f"[TokensReader] First token: id={rows[0].get('id')}, name={rows[0].get('name')}, token_pair={rows[0].get('token_pair')}")
                
                formatted_tokens = []

                # Build code->name and code->score maps from catalog once
                code_to_name = {}
                code_to_score = {}
                for item in PATTERN_SEED:
                    code = item.get("code")
                    name = item.get("name")
                    score = item.get("score", 0)
                    if code is None or name is None:
                        continue
                    code_str = getattr(code, "value", str(code))
                    code_to_name[code_str] = name
                    code_to_score[code_str] = score

                for row in rows:
                    token_id = row['id']
                    token_address = row['token_address']
                    token_pair = row['token_pair']
                    name = row['name']
                    symbol = row['symbol']
                    icon = row['icon']
                    decimals = row['decimals']
                    dev = row['dev']
                    circ_supply = float(row['circ_supply']) if row['circ_supply'] else 0
                    total_supply = float(row['total_supply']) if row['total_supply'] else 0
                    token_program = row['token_program']
                    holder_count = row['holder_count']
                    usd_price = float(row['usd_price']) if row['usd_price'] else 0
                    liquidity = float(row['liquidity']) if row['liquidity'] else 0
                    fdv = float(row['fdv']) if row['fdv'] else 0
                    mcap = float(row['mcap']) if row['mcap'] else 0
                    median_amount_usd_value = row.get('median_amount_usd')
                    if median_amount_usd_value is None:
                        median_amount_usd = 0.0
                    else:
                        try:
                            median_amount_usd = float(median_amount_usd_value)
                        except Exception:
                            median_amount_usd = 0.0
                    organic_score = float(row['organic_score']) if row['organic_score'] else 0
                    organic_score_label = row['organic_score_label']
                    blockaid_rugpull = row['blockaid_rugpull']
                    
                    mint_authority_disabled = row['mint_authority_disabled']
                    freeze_authority_disabled = row['freeze_authority_disabled']
                    top_holders_percentage = float(row['top_holders_percentage']) if row['top_holders_percentage'] else None
                    dev_balance_percentage = float(row['dev_balance_percentage']) if row['dev_balance_percentage'] else None
                    
                    # Resolve pattern using catalog (code -> pretty name)
                    t_pattern_code = row.get('t_pattern_code')
                    p_code = row.get('p_code')
                    p_name = row.get('p_name')
                    pattern_code = t_pattern_code or p_code
                    pattern_display = code_to_name.get(pattern_code) or p_name or (pattern_code.replace('_', ' ').title() if pattern_code else "")
                    
                    # Get pattern score for sorting (higher score = better pattern)
                    pattern_score = code_to_score.get(pattern_code, 0)
                    
                    # Check if token is archived (archived tokens are in tokens_history table)
                    is_archived = tokens_table == "tokens_history"
                    created_at = row['created_at']
                    iteration_count = int(row['iteration_count']) if row['iteration_count'] else 0
                    
                    price_change_5m = float(row['price_change_5m']) if row['price_change_5m'] else None
                    holder_change_5m = float(row['holder_change_5m']) if row['holder_change_5m'] else None
                    liquidity_change_5m = float(row['liquidity_change_5m']) if row['liquidity_change_5m'] else None
                    volume_change_5m = float(row['volume_change_5m']) if row['volume_change_5m'] else None
                    buy_volume_5m = float(row['buy_volume_5m']) if row['buy_volume_5m'] else None
                    sell_volume_5m = float(row['sell_volume_5m']) if row['sell_volume_5m'] else None
                    buy_organic_volume_5m = float(row['buy_organic_volume_5m']) if row['buy_organic_volume_5m'] else None
                    sell_organic_volume_5m = float(row['sell_organic_volume_5m']) if row['sell_organic_volume_5m'] else None
                    num_buys_5m = row['num_buys_5m']
                    num_sells_5m = row['num_sells_5m']
                    num_traders_5m = row['num_traders_5m']
                    
                    price_change_1h = float(row['price_change_1h']) if row['price_change_1h'] else None
                    holder_change_1h = float(row['holder_change_1h']) if row['holder_change_1h'] else None
                    liquidity_change_1h = float(row['liquidity_change_1h']) if row['liquidity_change_1h'] else None
                    volume_change_1h = float(row['volume_change_1h']) if row['volume_change_1h'] else None
                    buy_volume_1h = float(row['buy_volume_1h']) if row['buy_volume_1h'] else None
                    sell_volume_1h = float(row['sell_volume_1h']) if row['sell_volume_1h'] else None
                    buy_organic_volume_1h = float(row['buy_organic_volume_1h']) if row['buy_organic_volume_1h'] else None
                    sell_organic_volume_1h = float(row['sell_organic_volume_1h']) if row['sell_organic_volume_1h'] else None
                    num_buys_1h = row['num_buys_1h']
                    num_sells_1h = row['num_sells_1h']
                    num_traders_1h = row['num_traders_1h']
                    
                    price_change_6h = float(row['price_change_6h']) if row['price_change_6h'] else None
                    holder_change_6h = float(row['holder_change_6h']) if row['holder_change_6h'] else None
                    liquidity_change_6h = float(row['liquidity_change_6h']) if row['liquidity_change_6h'] else None
                    volume_change_6h = float(row['volume_change_6h']) if row['volume_change_6h'] else None
                    buy_volume_6h = float(row['buy_volume_6h']) if row['buy_volume_6h'] else None
                    sell_volume_6h = float(row['sell_volume_6h']) if row['sell_volume_6h'] else None
                    buy_organic_volume_6h = float(row['buy_organic_volume_6h']) if row['buy_organic_volume_6h'] else None
                    sell_organic_volume_6h = float(row['sell_organic_volume_6h']) if row['sell_organic_volume_6h'] else None
                    num_buys_6h = row['num_buys_6h']
                    num_sells_6h = row['num_sells_6h']
                    num_traders_6h = row['num_traders_6h']
                    
                    price_change_24h = float(row['price_change_24h']) if row['price_change_24h'] else None
                    holder_change_24h = float(row['holder_change_24h']) if row['holder_change_24h'] else None
                    liquidity_change_24h = float(row['liquidity_change_24h']) if row['liquidity_change_24h'] else None
                    volume_change_24h = float(row['volume_change_24h']) if row['volume_change_24h'] else None
                    buy_volume_24h = float(row['buy_volume_24h']) if row['buy_volume_24h'] else None
                    sell_volume_24h = float(row['sell_volume_24h']) if row['sell_volume_24h'] else None
                    buy_organic_volume_24h = float(row['buy_organic_volume_24h']) if row['buy_organic_volume_24h'] else None
                    sell_organic_volume_24h = float(row['sell_organic_volume_24h']) if row['sell_organic_volume_24h'] else None
                    num_buys_24h = row['num_buys_24h']
                    num_sells_24h = row['num_sells_24h']
                    num_traders_24h = row['num_traders_24h']
                    
                    # Real trading data (from tokens table and wallet_history)
                    wallet_id = row.get('wallet_id')  # ID гаманця, який тримає токен
                    plan_sell_iteration = row.get('plan_sell_iteration')
                    plan_sell_price_usd = float(row['plan_sell_price_usd']) if row.get('plan_sell_price_usd') else None
                    cur_income_price_usd = float(row['cur_income_price_usd']) if row.get('cur_income_price_usd') else None
                    
                    # Entry data from wallet_history (open position)
                    entry_token_amount = float(row['entry_token_amount']) if row.get('entry_token_amount') else None
                    entry_price_usd = float(row['entry_price_usd']) if row.get('entry_price_usd') else None
                    entry_iteration = row.get('entry_iteration')
                    
                    # PREVIEW FORECAST: If no real position but plan_sell_* exists, calculate preview entry data
                    # This allows frontend to show "Bought" section with preview entry at AI_PREVIEW_ENTRY_SEC
                    # Only show preview if AI_PREVIEW_FORECAST_ENABLED = True
                    AI_PREVIEW_FORECAST_ENABLED = getattr(config, 'AI_PREVIEW_FORECAST_ENABLED', True)
                    if entry_iteration is None and plan_sell_iteration is not None and plan_sell_price_usd is not None and AI_PREVIEW_FORECAST_ENABLED:
                        try:
                            AI_PREVIEW_ENTRY_SEC = int(getattr(config, 'AI_PREVIEW_ENTRY_SEC', 60))
                            DEFAULT_ENTRY_AMOUNT_USD = float(getattr(config, 'DEFAULT_ENTRY_AMOUNT_USD', 5.0))
                            
                            # Get entry price at AI_PREVIEW_ENTRY_SEC (60s) from token_metrics_seconds
                            preview_entry_row = await conn.fetchrow(
                                """
                                SELECT usd_price
                                FROM token_metrics_seconds
                                WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
                                ORDER BY ts ASC
                                OFFSET $2 LIMIT 1
                                """,
                                token_id, max(0, AI_PREVIEW_ENTRY_SEC - 1)
                            )
                            
                            if preview_entry_row and preview_entry_row.get('usd_price'):
                                preview_entry_price = float(preview_entry_row['usd_price'])
                                # Calculate preview entry data
                                entry_price_usd = preview_entry_price
                                entry_iteration = AI_PREVIEW_ENTRY_SEC
                                entry_token_amount = DEFAULT_ENTRY_AMOUNT_USD / preview_entry_price if preview_entry_price > 0 else None
                                # if self.debug:
                                #     print(f\"[TokensReader] token {token_id}: PREVIEW entry calculated - iter={entry_iteration}, price=${entry_price_usd:.6f}, amount={entry_token_amount:.6f}\")
                        except Exception:
                            # if self.debug:
                            #     print(f\"[TokensReader] token {token_id}: preview entry calculation failed: {e}\")
                            pass
                    
                    # Exit data from wallet_history (closed position)
                    exit_token_amount = float(row['exit_token_amount']) if row.get('exit_token_amount') else None
                    exit_price_usd = float(row['exit_price_usd']) if row.get('exit_price_usd') else None
                    exit_iteration = row.get('exit_iteration')
                    profit_usd = float(row['profit_usd']) if row.get('profit_usd') else None
                    pattern_segment_1 = (row.get('pattern_segment_1') or 'unknown')
                    pattern_segment_2 = (row.get('pattern_segment_2') or 'unknown')
                    pattern_segment_3 = (row.get('pattern_segment_3') or 'unknown')
                    pattern_segment_decision = (row.get('pattern_segment_decision') or 'not')
                    pattern_segments = [pattern_segment_1, pattern_segment_2, pattern_segment_3]
                    has_real_trading = row.get('has_real_trading')  # NULL, TRUE, or FALSE
                    swap_count = int(row.get('swap_count') or 0)
                    transfer_count = int(row.get('transfer_count') or 0)
                    withdraw_count = int(row.get('withdraw_count') or 0)

                    formatted_tokens.append({
                        "id": token_id,
                        "token_address": token_address,
                        "name": name or "Unknown",
                        "symbol": symbol or "UNKNOWN",
                        "icon": icon or "",
                        "decimals": decimals or 0,
                        "dev": dev or "",
                        "circ_supply": circ_supply,
                        "total_supply": total_supply,
                        "token_program": token_program or "",
                        "holders": holder_count or 0,
                        "price": usd_price,
                        "liquidity": liquidity,
                        "fdv": fdv,
                        "mcap": mcap,
                        "organic_score": organic_score,
                        "organic_score_label": organic_score_label or "",
                        "median_amount_usd": median_amount_usd,
                        "blockaid_rugpull": bool(blockaid_rugpull) if blockaid_rugpull is not None else None,
                        "dex": "Jupiter",
                        
                        "pair": (token_pair if (token_pair and token_pair != token_address) else None),
                        
                        "pattern": pattern_display,
                        "pattern_code": pattern_code,
                        "pattern_score": pattern_score,
                        "pattern_segments": pattern_segments,
                        "pattern_segment_1": pattern_segment_1,  # Individual fields for frontend
                        "pattern_segment_2": pattern_segment_2,
                        "pattern_segment_3": pattern_segment_3,
                        "pattern_segment_decision": pattern_segment_decision,
                        "check_sol_rpc": 0,
                        
                        "mint_authority_disabled": mint_authority_disabled,
                        "freeze_authority_disabled": freeze_authority_disabled,
                        "top_holders_percentage": top_holders_percentage,
                        "dev_balance_percentage": dev_balance_percentage,
                        
                        "price_change_5m": price_change_5m,
                        "holder_change_5m": holder_change_5m,
                        "liquidity_change_5m": liquidity_change_5m,
                        "volume_change_5m": volume_change_5m,
                        "buy_volume_5m": buy_volume_5m,
                        "sell_volume_5m": sell_volume_5m,
                        "buy_organic_volume_5m": buy_organic_volume_5m,
                        "sell_organic_volume_5m": sell_organic_volume_5m,
                        "num_buys_5m": num_buys_5m,
                        "num_sells_5m": num_sells_5m,
                        "num_traders_5m": num_traders_5m,
                        
                        "price_change_1h": price_change_1h,
                        "holder_change_1h": holder_change_1h,
                        "liquidity_change_1h": liquidity_change_1h,
                        "volume_change_1h": volume_change_1h,
                        "buy_volume_1h": buy_volume_1h,
                        "sell_volume_1h": sell_volume_1h,
                        "buy_organic_volume_1h": buy_organic_volume_1h,
                        "sell_organic_volume_1h": sell_organic_volume_1h,
                        "num_buys_1h": num_buys_1h,
                        "num_sells_1h": num_sells_1h,
                        "num_traders_1h": num_traders_1h,
                        
                        "price_change_6h": price_change_6h,
                        "holder_change_6h": holder_change_6h,
                        "liquidity_change_6h": liquidity_change_6h,
                        "volume_change_6h": volume_change_6h,
                        "buy_volume_6h": buy_volume_6h,
                        "sell_volume_6h": sell_volume_6h,
                        "buy_organic_volume_6h": buy_organic_volume_6h,
                        "sell_organic_volume_6h": sell_organic_volume_6h,
                        "num_buys_6h": num_buys_6h,
                        "num_sells_6h": num_sells_6h,
                        "num_traders_6h": num_traders_6h,
                        
                        "price_change_24h": price_change_24h,
                        "holder_change_24h": holder_change_24h,
                        "liquidity_change_24h": liquidity_change_24h,
                        "volume_change_24h": volume_change_24h,
                        "buy_volume_24h": buy_volume_24h,
                        "sell_volume_24h": sell_volume_24h,
                        "buy_organic_volume_24h": buy_organic_volume_24h,
                        "sell_organic_volume_24h": sell_organic_volume_24h,
                        "num_buys_24h": num_buys_24h,
                        "num_sells_24h": num_sells_24h,
                        "num_traders_24h": num_traders_24h,
                        "security_analyzed_at": None,
                        "updated_at": None,
                        "created_at": created_at.isoformat() if created_at else None,
                    "live_time": self._calculate_live_time(iteration_count, is_archived),
                        "wallet_id": int(wallet_id) if wallet_id is not None else None,
                        "entry_token_amount": entry_token_amount,
                        "entry_price_usd": entry_price_usd,
                        "entry_iteration": entry_iteration,
                        "exit_token_amount": exit_token_amount,
                        "exit_price_usd": exit_price_usd,
                        "exit_iteration": exit_iteration,
                        "profit_usd": profit_usd,
                        "plan_sell_iteration": plan_sell_iteration,
                        "plan_sell_price_usd": plan_sell_price_usd,
                        "cur_income_price_usd": cur_income_price_usd,
                        "has_real_trading": has_real_trading,  # NULL, TRUE, or FALSE
                        "swap_count": swap_count,
                        "transfer_count": transfer_count,
                        "withdraw_count": withdraw_count
                    })
                
                def sort_key(token: Dict[str, Any]):
                    entry_iter = token.get("entry_iteration")
                    exit_iter = token.get("exit_iteration")
                    pattern_code_token = (token.get("pattern_code") or "").strip().lower()
                    has_buy = entry_iter is not None
                    has_sell = exit_iter is not None
                    if has_buy and not has_sell:
                        priority = 0  # активные сделки (вошли и ещё держим)
                    elif not has_buy and pattern_code_token in ("", "unknown"):
                        priority = 1  # пока без входа и без паттерна — проверяем первыми
                    elif has_buy and has_sell:
                        priority = 2  # уже вышли
                    elif not has_buy:
                        priority = 3  # нет входа, но паттерн уже определён
                    else:
                        priority = 4
                    pattern_score = token.get("pattern_score", 0) or 0
                    created = token.get("created_at")
                    created_ord = 0.0
                    if created:
                        try:
                            created_ord = datetime.fromisoformat(created).timestamp()
                        except Exception:
                            created_ord = 0.0
                    return (
                        priority,
                        -pattern_score,
                        -created_ord
                    )

                formatted_tokens.sort(key=sort_key)
                
                # if self.debug:
                #     print(f"[TokensReader] Formatted {len(formatted_tokens)} tokens, total_count={total_count}")
                
                result = {
                    "success": True,
                    "tokens": formatted_tokens,
                    "total_found": len(formatted_tokens),
                    "total_count": total_count,
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + limit) < total_count,
                    "scan_time": datetime.now().isoformat()
                }
                
                # Update counts for next time
                self.last_token_count = len(formatted_tokens)
                self.total_token_count = total_count
                
                return result
                
        except Exception as e:
            # if self.debug:
            #     import traceback
            #     print(f"[TokensReader] ❌ ERROR in get_tokens_from_db: {e}")
            #     print(f"[TokensReader] Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": str(e),
                "tokens": [],
                "total_count": 0
            }
    
    async def get_token_by_address(self, token_address: str) -> Dict[str, Any]:
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT 
                        t.id, t.token_address, t.token_pair, t.name, t.symbol, t.icon, t.decimals, t.dev,
                        t.circ_supply, t.total_supply, t.token_program, t.holder_count,
                        t.usd_price, t.liquidity, t.fdv, t.mcap, t.price_block_id,
                        t.organic_score, t.organic_score_label,
                        t.mint_authority_disabled, t.freeze_authority_disabled, 
                        t.top_holders_percentage, t.dev_balance_percentage,
                        t.price_change_5m, t.holder_change_5m, t.liquidity_change_5m, t.volume_change_5m,
                        t.buy_volume_5m, t.sell_volume_5m, t.buy_organic_volume_5m, t.sell_organic_volume_5m,
                        t.num_buys_5m, t.num_sells_5m, t.num_traders_5m,
                        t.price_change_1h, t.holder_change_1h, t.liquidity_change_1h, t.volume_change_1h,
                        t.buy_volume_1h, t.sell_volume_1h, t.buy_organic_volume_1h, t.sell_organic_volume_1h,
                        t.num_buys_1h, t.num_sells_1h, t.num_traders_1h,
                        t.price_change_6h, t.holder_change_6h, t.liquidity_change_6h, t.volume_change_6h,
                        t.buy_volume_6h, t.sell_volume_6h, t.buy_organic_volume_6h, t.sell_organic_volume_6h,
                        t.num_buys_6h, t.num_sells_6h, t.num_traders_6h,
                        t.price_change_24h, t.holder_change_24h, t.liquidity_change_24h, t.volume_change_24h,
                        t.buy_volume_24h, t.sell_volume_24h, t.buy_organic_volume_24h, t.sell_organic_volume_24h,
                        t.num_buys_24h, t.num_sells_24h, t.num_traders_24h,
                        t.archived_at, t.created_at,
                        t.wallet_id,
                        t.plan_sell_iteration, t.plan_sell_price_usd,
                        t.cur_income_price_usd,
                        COALESCE(NULLIF(p.name, ''), NULLIF(t.pattern_code, ''), NULLIF(p.code, ''), 'Unknown') AS pattern,
                        COALESCE((SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id = t.id AND mcap IS NOT NULL AND mcap > 0), 0) AS iteration_count,
                        op.entry_token_amount,
                        op.entry_price_usd,
                        op.entry_iteration,
                        cp.exit_token_amount,
                        cp.exit_price_usd,
                        cp.exit_iteration,
                        cp.profit_usd
                    FROM tokens t
                    LEFT JOIN (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            entry_token_amount,
                            entry_price_usd,
                            entry_iteration
                        FROM wallet_history
                        WHERE exit_iteration IS NULL
                        ORDER BY token_id, id DESC
                    ) op ON op.token_id = t.id
                    LEFT JOIN (
                        SELECT DISTINCT ON (wh.token_id)
                            wh.token_id,
                            wh.exit_token_amount,
                            wh.exit_price_usd,
                            wh.exit_iteration,
                            wh.profit_usd
                        FROM wallet_history wh
                        INNER JOIN tokens t2 ON t2.id = wh.token_id 
                            AND t2.wallet_id IS NOT NULL
                            AND t2.wallet_id = wh.wallet_id
                        WHERE wh.exit_iteration IS NOT NULL
                            AND NOT EXISTS (
                                SELECT 1
                                FROM wallet_history wh_open
                                WHERE wh_open.token_id = wh.token_id
                                    AND wh_open.wallet_id = wh.wallet_id
                                    AND wh_open.exit_iteration IS NULL
                            )
                        ORDER BY wh.token_id, wh.id DESC
                    ) cp ON cp.token_id = t.id
                    LEFT JOIN (
                        SELECT DISTINCT ON (token_id)
                            token_id,
                            pattern_id,
                            confidence
                        FROM ai_token_patterns
                        ORDER BY token_id, confidence DESC, source ASC
                    ) atp ON atp.token_id = t.id
                    LEFT JOIN ai_patterns p ON p.id = atp.pattern_id
                    WHERE t.token_address = $1
                """, token_address)
                
                if not row:
                    return {
                        "success": False,
                        "error": "Token not found",
                        "token": None
                    }
                
                # Build code->name and code->score maps from catalog once
                code_to_name = {}
                code_to_score = {}
                for item in PATTERN_SEED:
                    code = item.get("code")
                    name = item.get("name")
                    score = item.get("score", 0)
                    if code is None or name is None:
                        continue
                    code_str = getattr(code, "value", str(code))
                    code_to_name[code_str] = name
                    code_to_score[code_str] = score

                # Resolve pattern pretty name
                t_pattern_code = row.get('t_pattern_code')
                p_code = row.get('p_code')
                p_name = row.get('p_name')
                pattern_code = t_pattern_code or p_code
                pattern_display = code_to_name.get(pattern_code) or p_name or (pattern_code.replace('_',' ').title() if pattern_code else "")
                pattern_score = code_to_score.get(pattern_code, 0)

                token = {
                    "id": row['id'],
                    "token_address": row['token_address'],
                    "name": row['name'] or "Unknown",
                    "symbol": row['symbol'] or "UNKNOWN",
                    "icon": row['icon'] or "",
                    "decimals": row['decimals'] or 0,
                    "dev": row['dev'] or "",
                    "circ_supply": float(row['circ_supply']) if row['circ_supply'] else 0,
                    "total_supply": float(row['total_supply']) if row['total_supply'] else 0,
                    "token_program": row['token_program'] or "",
                    "holders": row['holder_count'] or 0,
                    "price": float(row['usd_price']) if row['usd_price'] else 0,
                    "liquidity": float(row['liquidity']) if row['liquidity'] else 0,
                    "fdv": float(row['fdv']) if row['fdv'] else 0,
                    "mcap": float(row['mcap']) if row['mcap'] else 0,
                    "organic_score": float(row['organic_score']) if row['organic_score'] else 0,
                    "organic_score_label": row['organic_score_label'] or "",
                    "dex": "Jupiter",
                    "pair": row['token_pair'],
                    "wallet_id": row.get('wallet_id'),
                    "is_honeypot": row.get('is_honeypot'),
                    "pattern": pattern_display,
                    "pattern_code": pattern_code,
                    "pattern_score": pattern_score,
                    "check_dexscreener": 0,
                    "check_sol_rpc": 0,
                    "mint_authority_disabled": row['mint_authority_disabled'],
                    "freeze_authority_disabled": row['freeze_authority_disabled'],
                    "top_holders_percentage": float(row['top_holders_percentage']) if row['top_holders_percentage'] else None,
                    "dev_balance_percentage": float(row['dev_balance_percentage']) if row['dev_balance_percentage'] else None,
                    
                    "price_change_5m": float(row['price_change_5m']) if row['price_change_5m'] else None,
                    "holder_change_5m": float(row['holder_change_5m']) if row['holder_change_5m'] else None,
                    "liquidity_change_5m": float(row['liquidity_change_5m']) if row['liquidity_change_5m'] else None,
                    "volume_change_5m": float(row['volume_change_5m']) if row['volume_change_5m'] else None,
                    "buy_volume_5m": float(row['buy_volume_5m']) if row['buy_volume_5m'] else None,
                    "sell_volume_5m": float(row['sell_volume_5m']) if row['sell_volume_5m'] else None,
                    "buy_organic_volume_5m": float(row['buy_organic_volume_5m']) if row['buy_organic_volume_5m'] else None,
                    "sell_organic_volume_5m": float(row['sell_organic_volume_5m']) if row['sell_organic_volume_5m'] else None,
                    "num_buys_5m": row['num_buys_5m'],
                    "num_sells_5m": row['num_sells_5m'],
                    "num_traders_5m": row['num_traders_5m'],
                    
                    "price_change_1h": float(row['price_change_1h']) if row['price_change_1h'] else None,
                    "holder_change_1h": float(row['holder_change_1h']) if row['holder_change_1h'] else None,
                    "liquidity_change_1h": float(row['liquidity_change_1h']) if row['liquidity_change_1h'] else None,
                    "volume_change_1h": float(row['volume_change_1h']) if row['volume_change_1h'] else None,
                    "buy_volume_1h": float(row['buy_volume_1h']) if row['buy_volume_1h'] else None,
                    "sell_volume_1h": float(row['sell_volume_1h']) if row['sell_volume_1h'] else None,
                    "buy_organic_volume_1h": float(row['buy_organic_volume_1h']) if row['buy_organic_volume_1h'] else None,
                    "sell_organic_volume_1h": float(row['sell_organic_volume_1h']) if row['sell_organic_volume_1h'] else None,
                    "num_buys_1h": row['num_buys_1h'],
                    "num_sells_1h": row['num_sells_1h'],
                    "num_traders_1h": row['num_traders_1h'],
                    
                    "price_change_6h": float(row['price_change_6h']) if row['price_change_6h'] else None,
                    "holder_change_6h": float(row['holder_change_6h']) if row['holder_change_6h'] else None,
                    "liquidity_change_6h": float(row['liquidity_change_6h']) if row['liquidity_change_6h'] else None,
                    "volume_change_6h": float(row['volume_change_6h']) if row['volume_change_6h'] else None,
                    "buy_volume_6h": float(row['buy_volume_6h']) if row['buy_volume_6h'] else None,
                    "sell_volume_6h": float(row['sell_volume_6h']) if row['sell_volume_6h'] else None,
                    "buy_organic_volume_6h": float(row['buy_organic_volume_6h']) if row['buy_organic_volume_6h'] else None,
                    "sell_organic_volume_6h": float(row['sell_organic_volume_6h']) if row['sell_organic_volume_6h'] else None,
                    "num_buys_6h": row['num_buys_6h'],
                    "num_sells_6h": row['num_sells_6h'],
                    "num_traders_6h": row['num_traders_6h'],
                    
                    "price_change_24h": float(row['price_change_24h']) if row['price_change_24h'] else None,
                    "holder_change_24h": float(row['holder_change_24h']) if row['holder_change_24h'] else None,
                    "liquidity_change_24h": float(row['liquidity_change_24h']) if row['liquidity_change_24h'] else None,
                    "volume_change_24h": float(row['volume_change_24h']) if row['volume_change_24h'] else None,
                    "buy_volume_24h": float(row['buy_volume_24h']) if row['buy_volume_24h'] else None,
                    "sell_volume_24h": float(row['sell_volume_24h']) if row['sell_volume_24h'] else None,
                    "buy_organic_volume_24h": float(row['buy_organic_volume_24h']) if row['buy_organic_volume_24h'] else None,
                    "sell_organic_volume_24h": float(row['sell_organic_volume_24h']) if row['sell_organic_volume_24h'] else None,
                    "num_buys_24h": row['num_buys_24h'],
                    "num_sells_24h": row['num_sells_24h'],
                    "num_traders_24h": row['num_traders_24h'],
                    # This method always queries from tokens table (not tokens_history)
                    # If token is found, it's not archived
                    "security_analyzed_at": None,
                    "updated_at": None,
                    "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                    "live_time": self._calculate_live_time(int(row['iteration_count']) if row['iteration_count'] else 0, False),
                    "wallet_id": row.get('wallet_id'),
                    "entry_token_amount": float(row['entry_token_amount']) if row.get('entry_token_amount') else None,
                    "entry_price_usd": float(row['entry_price_usd']) if row.get('entry_price_usd') else None,
                    "entry_iteration": row.get('entry_iteration'),
                    "exit_token_amount": float(row['exit_token_amount']) if row.get('exit_token_amount') else None,
                    "exit_price_usd": float(row['exit_price_usd']) if row.get('exit_price_usd') else None,
                    "exit_iteration": row.get('exit_iteration'),
                    "profit_usd": float(row['profit_usd']) if row.get('profit_usd') else None,
                    "plan_sell_iteration": row.get('plan_sell_iteration'),
                    "plan_sell_price_usd": float(row['plan_sell_price_usd']) if row.get('plan_sell_price_usd') else None,
                    "cur_income_price_usd": float(row.get('cur_income_price_usd') or 0)
                }
                
                # PREVIEW FORECAST: If no real position but plan_sell_* exists, calculate preview entry data
                # This allows frontend to show "Bought" section with preview entry at AI_PREVIEW_ENTRY_SEC
                # Only show preview if AI_PREVIEW_FORECAST_ENABLED = True
                AI_PREVIEW_FORECAST_ENABLED = getattr(config, 'AI_PREVIEW_FORECAST_ENABLED', True)
                if token["entry_iteration"] is None and token["plan_sell_iteration"] is not None and token["plan_sell_price_usd"] is not None and AI_PREVIEW_FORECAST_ENABLED:
                    try:
                        AI_PREVIEW_ENTRY_SEC = int(getattr(config, 'AI_PREVIEW_ENTRY_SEC', 60))
                        DEFAULT_ENTRY_AMOUNT_USD = float(getattr(config, 'DEFAULT_ENTRY_AMOUNT_USD', 5.0))
                        
                        # Get entry price at AI_PREVIEW_ENTRY_SEC (60s) from token_metrics_seconds
                        preview_entry_row = await conn.fetchrow(
                            """
                            SELECT usd_price
                            FROM token_metrics_seconds
                            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
                            ORDER BY ts ASC
                            OFFSET $2 LIMIT 1
                            """,
                            row['id'], max(0, AI_PREVIEW_ENTRY_SEC - 1)
                        )
                        
                        if preview_entry_row and preview_entry_row.get('usd_price'):
                            preview_entry_price = float(preview_entry_row['usd_price'])
                            # Calculate preview entry data
                            token["entry_price_usd"] = preview_entry_price
                            token["entry_iteration"] = AI_PREVIEW_ENTRY_SEC
                            token["entry_token_amount"] = DEFAULT_ENTRY_AMOUNT_USD / preview_entry_price if preview_entry_price > 0 else None
                            
                        # if self.debug:
                        #     print(f"[TokensReader] token {row['id']}: PREVIEW entry calculated (get_token_by_address) - iter={token['entry_iteration']}, price=${token['entry_price_usd']:.6f}, amount={token['entry_token_amount']:.6f}")
                    except Exception as e:
                        # If preview calculation fails, keep entry_* as None (no preview shown)
                        # if self.debug:
                        #     print(f"[TokensReader] token {row['id']}: preview entry calculation failed (get_token_by_address): {e}")
                        pass
                
                return {
                    "success": True,
                    "token": token
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "token": None
            }
    
    async def search_tokens(self, query: str, limit: int = 50) -> Dict[str, Any]:
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        id, token_address, name, symbol, mcap, usd_price, holder_count
                    FROM tokens
                    WHERE LOWER(name) LIKE LOWER($1) OR LOWER(symbol) LIKE LOWER($2)
                    ORDER BY mcap DESC
                    LIMIT $3
                """, f"%{query}%", f"%{query}%", limit)
                
                formatted_tokens = []

                for row in rows:
                    formatted_tokens.append({
                        "id": row['id'],
                        "token_address": row['token_address'],
                        "name": row['name'] or "Unknown",
                        "symbol": row['symbol'] or "UNKNOWN",
                        "mcap": float(row['mcap']) if row['mcap'] else 0,
                        "price": float(row['usd_price']) if row['usd_price'] else 0,
                        "holders": row['holder_count'] or 0,
                        "dex": "Jupiter",
                        "pair": None
                    })
                
                return {
                    "success": True,
                    "tokens": formatted_tokens,
                    "total_found": len(formatted_tokens),
                    "query": query,
                    "scan_time": datetime.now().isoformat()
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "tokens": []
            }
    
    async def add_client(self, websocket: WebSocket):
        self.connected_clients.append(websocket)
        # Ensure auto-refresh starts when the first client connects
        if (not self._use_history_source()) and self.auto_refresh_task is None:
            try:
                self.auto_refresh_task = asyncio.create_task(self._auto_refresh_loop())
            except Exception:
                # Keep silent; websocket will still receive initial payload
                pass
    
    def remove_client(self, websocket: WebSocket):
        if websocket in self.connected_clients:
            self.connected_clients.remove(websocket)
    
    async def start_auto_refresh(self):
        if self._use_history_source():
            # if self.debug:
            #     print("[TokensReader] History mode - auto refresh disabled")
            return
        if self.auto_refresh_task is None:
            self.auto_refresh_task = asyncio.create_task(self._auto_refresh_loop())
    
    async def stop_auto_refresh(self):
        if self.auto_refresh_task:
            self.auto_refresh_task.cancel()
            try:
                await self.auto_refresh_task
            except asyncio.CancelledError:
                pass
            self.auto_refresh_task = None
    
    async def _auto_refresh_loop(self):
        while True:
            try:
                await asyncio.sleep(self.refresh_interval)
                
                if not self.connected_clients:
                    continue
                
                use_history = self._use_history_source()
                tokens_table = "tokens_history" if use_history else "tokens"
                
                pool = await get_db_pool()

                async with pool.acquire() as conn:
                    if use_history:
                        count = await conn.fetchval(f"SELECT COUNT(*) FROM {tokens_table}") or 0
                        total_count = count
                        last_updated_row = await conn.fetchrow(
                            f"SELECT MAX(COALESCE(archived_at, token_updated_at, created_at)) AS u FROM {tokens_table}"
                        )
                    elif self.disable_sort:
                        # For disable_sort, we show tokens from tokens_history table (archived tokens)
                        # Since archived tokens are not in tokens table, count from tokens_history
                        count = await conn.fetchval(
                            "SELECT COUNT(*) FROM tokens_history WHERE token_pair IS NOT NULL AND token_pair <> '' AND token_pair <> token_address"
                        ) or 0
                        total_count = await conn.fetchval(
                            "SELECT COUNT(*) FROM tokens_history"
                        ) or 0
                        last_updated_row = await conn.fetchrow(
                            "SELECT MAX(COALESCE(archived_at, updated_at, token_updated_at, created_at)) AS u FROM tokens_history"
                        )
                    elif self.history_mode:
                        count = await conn.fetchval(
                            "SELECT COUNT(*) FROM tokens WHERE token_pair IS NOT NULL AND token_pair <> '' AND token_pair <> token_address"
                        ) or 0
                        total_count = await conn.fetchval(
                            "SELECT COUNT(*) FROM tokens"
                        ) or 0
                        last_updated_row = await conn.fetchrow(
                            "SELECT MAX(COALESCE(updated_at, token_updated_at, created_at)) AS u FROM tokens"
                        )
                    else:
                        # Count tokens with valid pairs (archived tokens are not in tokens table)
                        count = await conn.fetchval(
                            "SELECT COUNT(*) FROM tokens WHERE token_pair IS NOT NULL AND token_pair <> '' AND token_pair <> token_address"
                        ) or 0
                        # Count all tokens (archived tokens are not in tokens table)
                        total_count = await conn.fetchval(
                            "SELECT COUNT(*) FROM tokens"
                        ) or 0
                        # Get last updated timestamp for change detection
                        last_updated_row = await conn.fetchrow(
                            "SELECT MAX(COALESCE(updated_at, token_updated_at, created_at)) AS u FROM tokens"
                        )
                    last_updated = str(last_updated_row['u']) if last_updated_row and 'u' in last_updated_row else ""

                has_changes = (count != self.last_token_count) or (last_updated != self.last_updated_at_sum) or (total_count != self.total_token_count)
                
                if has_changes:
                    result = await self.get_tokens_from_db(limit=1000)
                    
                # if self.debug:
                #     print(f"[TokensReader] _auto_refresh_loop: result['success']={result.get('success')}, tokens_count={len(result.get('tokens', []))}, count={count}, total_count={total_count}")
                    
                    if result["success"] and result["tokens"]:
                        # Update result with current counts
                        result["total_found"] = count
                        result["total_count"] = total_count
                        
                        json_data = json.dumps(result, ensure_ascii=False)
                        
                        disconnected_clients = []
                        for client in self.connected_clients:
                            try:
                                await client.send_text(json_data)
                            except Exception as e:
                                disconnected_clients.append(client)
                        
                        for client in disconnected_clients:
                            self.connected_clients.remove(client)
                        
                        self.last_token_count = count
                        self.total_token_count = total_count
                        self.last_updated_at_sum = last_updated
                    
            except asyncio.CancelledError:
                break

    async def push_now(self):
        """Immediately broadcast current tokens to all connected clients."""
        if not self.connected_clients:
            # if self.debug:
            #     print(f"[TokensReader] push_now: no connected clients")
            return
        try:
            result = await self.get_tokens_from_db(limit=1000)
            if result.get("success"):
                json_data = json.dumps(result, ensure_ascii=False)
                disconnected = []
                for client in self.connected_clients:
                    try:
                        await client.send_text(json_data)
                    except Exception:
                        disconnected.append(client)
                for c in disconnected:
                    self.connected_clients.remove(c)
        except Exception as e:
            # if self.debug:
            #     print(f"[TokensReader] push_now error: {e}")
            pass
    
    def _calculate_live_time(self, iteration_count, is_archived):
        """Calculate live time from iteration count (each iteration = 1 second)"""
        
        if iteration_count is None or iteration_count == 0:
            return "Ended" if is_archived else "Live"
        
        total_seconds = iteration_count
        
        # Handle different time ranges
        diff_days = total_seconds // 86400  # 86400 seconds = 1 day
        diff_hours = (total_seconds % 86400) // 3600  # remaining hours
        diff_minutes = (total_seconds % 3600) // 60  # remaining minutes
        remaining_seconds = total_seconds % 60
        
        # Build time string based on duration
        if diff_days > 0:
            time_str = f"{diff_days}d {diff_hours}h {diff_minutes}m"
        elif diff_hours > 0:
            time_str = f"{diff_hours}h {diff_minutes}m {remaining_seconds}s"
        elif diff_minutes > 0:
            time_str = f"{diff_minutes}m {remaining_seconds}s"
        else:
            time_str = f"{remaining_seconds}s"
        
        return f"Ended ({time_str})" if is_archived else f"Live {time_str}"

    
    
    def get_status(self):
        return {
            "connected_clients": len(self.connected_clients),
            "database": "PostgreSQL (crypto.db)",
            "debug": self.debug,
            "auto_refresh_running": self.auto_refresh_task is not None,
            "token_count": self.last_token_count,
            "total_token_count": self.total_token_count
        }

if __name__ == "__main__":
    pass
