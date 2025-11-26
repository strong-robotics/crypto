#!/usr/bin/env python3
"""
V3 Slot Synchronizer

Задача: Синхронизировать trades с метриками Jupiter по слотам
- Jupiter записывает в token_metrics_seconds с jupiter_slot = price_block_id
- Helius записывает в trades с slot (helios_slot)
- Найти соответствующие trades для каждой итерации Jupiter
- Вычислить медианы и обновить token_metrics_seconds
"""

import asyncio
from typing import Dict, List, Optional, Tuple
from statistics import median
from _v3_db_pool import get_db_pool
from config import config


def match_slot(slot_jup: int, helios_slots: List[int]) -> Optional[int]:
    """
    Найти соответствующий слот Helius для слота Jupiter
    
    Args:
        slot_jup: слот от Jupiter (price_block_id)
        helios_slots: список слотов от Helius
    
    Returns:
        Соответствующий слот Helius или None
    """
    if not helios_slots:
        return None
    
    best = min(helios_slots, key=lambda s: abs(s - slot_jup))
    diff = abs(best - slot_jup)
    window = config.LIVE_TRADES_SLOT_MATCH_WINDOW
    return best if diff <= window else None


class SlotSynchronizer:
    def __init__(self):
        self.debug = True

    async def get_recent_trades_without_medians(self, limit: int = 100) -> List[Dict]:
        """Получить недавние trades без синхронизированных медиан"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT t.token_id, t.slot, t.timestamp
                FROM trades t
                WHERE t.slot IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM token_metrics_seconds m 
                    WHERE m.token_id = t.token_id 
                    AND m.jupiter_slot = t.slot
                    AND m.median_amount_sol IS NOT NULL
                )
                ORDER BY t.timestamp DESC
                LIMIT $1
                """,
                limit
            )
            return [
                {
                    "token_id": int(row["token_id"]),
                    "helios_slot": int(row["slot"]),
                    "timestamp": int(row["timestamp"])
                }
                for row in rows
            ]

    async def get_last_3_jupiter_slots(self, token_id: int) -> List[Dict]:
        """Получить 3 последних записи метрик (по внутренним секундам системы)"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, jupiter_slot, ts FROM token_metrics_seconds 
                WHERE token_id = $1
                ORDER BY ts DESC
                LIMIT 3
                """,
                token_id
            )
            return [
                {
                    "id": int(row["id"]),
                    "jupiter_slot": int(row["jupiter_slot"]) if row["jupiter_slot"] else None,
                    "ts": int(row["ts"])
                }
                for row in rows
            ]

    async def find_jupiter_metrics_for_helios_slot(self, token_id: int, helios_slot: int) -> Optional[Dict]:
        """Найти Jupiter метрики для Helius слота (старая логика для совместимости)"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Получаем все Jupiter слоты для токена
            jupiter_slots_rows = await conn.fetch(
                """
                SELECT id, jupiter_slot, ts FROM token_metrics_seconds 
                WHERE token_id = $1 AND jupiter_slot IS NOT NULL
                ORDER BY jupiter_slot DESC
                LIMIT 20
                """,
                token_id
            )
            jupiter_slots = [int(row["jupiter_slot"]) for row in jupiter_slots_rows]
            
            # Находим соответствующий Jupiter слот
            matched_slot = match_slot(helios_slot, jupiter_slots)
            if not matched_slot:
                return None
            
            # Находим метрику с этим слотом
            row = await conn.fetchrow(
                """
                SELECT id, token_id, ts, jupiter_slot
                FROM token_metrics_seconds
                WHERE token_id = $1 AND jupiter_slot = $2
                """,
                token_id,
                matched_slot
            )
            
            if not row:
                return None
                
            return {
                "id": int(row["id"]),
                "token_id": int(row["token_id"]),
                "ts": int(row["ts"]),
                "jupiter_slot": int(row["jupiter_slot"])
            }

    async def get_trades_for_helios_slot(self, token_id: int, helios_slot: int) -> List[Dict]:
        """Получить trades для конкретного Helius слота"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            trades_rows = await conn.fetch(
                """
                SELECT amount_sol, amount_usd, amount_tokens, token_price_usd
                FROM trades
                WHERE token_id = $1 AND slot = $2
                """,
                token_id,
                helios_slot
            )
            
            return [
                {
                    "amount_sol": float(row["amount_sol"]) if row["amount_sol"] else 0.0,
                    "amount_usd": float(row["amount_usd"]) if row["amount_usd"] else 0.0,
                    "amount_tokens": float(row["amount_tokens"]) if row["amount_tokens"] else 0.0,
                    "token_price_usd": float(row["token_price_usd"]) if row["token_price_usd"] else 0.0,
                }
                for row in trades_rows
            ]

    async def get_trades_for_slot_range(self, token_id: int, helios_slot: int, jupiter_slot: int) -> List[Dict]:
        """Получить trades для диапазона слотов (helios_slot ±3 от jupiter_slot)"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Ищем trades в диапазоне ±N слотов от jupiter_slot (по умолчанию N=10)
            window = config.LIVE_TRADES_SLOT_MATCH_WINDOW
            slot_min = jupiter_slot - window
            slot_max = jupiter_slot + window
            
            trades_rows = await conn.fetch(
                """
                SELECT amount_sol, amount_usd, amount_tokens, token_price_usd, slot, direction
                FROM trades
                WHERE token_id = $1 AND slot BETWEEN $2 AND $3
                ORDER BY ABS(slot - $4) ASC
                """,
                token_id,
                slot_min,
                slot_max,
                jupiter_slot
            )
            
            return [
                {
                    "amount_sol": float(row["amount_sol"]) if row["amount_sol"] else 0.0,
                    "amount_usd": float(row["amount_usd"]) if row["amount_usd"] else 0.0,
                    "amount_tokens": float(row["amount_tokens"]) if row["amount_tokens"] else 0.0,
                    "token_price_usd": float(row["token_price_usd"]) if row["token_price_usd"] else 0.0,
                    "slot": int(row["slot"]),
                    "direction": (row["direction"] or '').lower()
                }
                for row in trades_rows
            ]


    def calculate_medians(self, trades: List[Dict]) -> Dict[str, Optional[str]]:
        """Вычислить медианы из trades"""
        if not trades:
            return {
                "median_amount_sol": None,
                "median_amount_usd": None,
                "median_amount_tokens": None,
                "median_token_price": None,
                "buy_count": None,
                "sell_count": None,
                "buy_usd": None,
                "sell_usd": None,
            }
        
        # Фильтруем нулевые значения
        sol_values = [t["amount_sol"] for t in trades if t["amount_sol"] and t["amount_sol"] > 0]
        usd_values = [t["amount_usd"] for t in trades if t["amount_usd"] and t["amount_usd"] > 0]
        tokens_values = [t["amount_tokens"] for t in trades if t["amount_tokens"] and t["amount_tokens"] > 0]
        price_values = [t["token_price_usd"] for t in trades if t["token_price_usd"] and t["token_price_usd"] > 0]

        # Flow aggregates per second window
        buy_count = sum(1 for t in trades if (t.get("direction") or '').lower() == 'buy')
        sell_count = sum(1 for t in trades if (t.get("direction") or '').lower() == 'sell')
        buy_usd = sum(float(t["amount_usd"]) for t in trades if (t.get("direction") or '').lower() == 'buy')
        sell_usd = sum(float(t["amount_usd"]) for t in trades if (t.get("direction") or '').lower() == 'sell')

        return {
            "median_amount_sol": str(median(sol_values)) if sol_values else None,
            "median_amount_usd": str(median(usd_values)) if usd_values else None,
            "median_amount_tokens": str(median(tokens_values)) if tokens_values else None,
            "median_token_price": str(median(price_values)) if price_values else None,
            "buy_count": int(buy_count),
            "sell_count": int(sell_count),
            "buy_usd": float(buy_usd) if buy_usd else None,
            "sell_usd": float(sell_usd) if sell_usd else None,
        }

    async def synchronize_helios_trades(self, token_id: int, helios_slot: int) -> bool:
        """Синхронизировать Helius trades с Jupiter метриками (старая логика)"""
        try:
            # Находим соответствующие Jupiter метрики
            metrics = await self.find_jupiter_metrics_for_helios_slot(token_id, helios_slot)
            if not metrics:
                return False
            
            # Получаем trades для Helius слота
            trades = await self.get_trades_for_helios_slot(token_id, helios_slot)
            if not trades:
                return False
            
            # Вычисляем медианы
            medians = self.calculate_medians(trades)
            
            # Обновляем метрики
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE token_metrics_seconds SET
                        median_amount_sol = $2,
                        median_amount_usd = $3,
                        median_amount_tokens = $4,
                        median_token_price = $5
                    WHERE id = $1
                    """,
                    metrics["id"],
                    medians["median_amount_sol"],
                    medians["median_amount_usd"],
                    medians["median_amount_tokens"],
                    medians["median_token_price"]
                )
                # Bump token_updated_at to trigger WS refreshes
                await conn.execute(
                    "UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP WHERE id = $1",
                    token_id,
                )
            
            if self.debug:
                print(f"🔄 Token {token_id}: synced {len(trades)} trades, helios_slot {helios_slot} -> jupiter_slot {metrics['jupiter_slot']}")
            
            return True
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error syncing token {token_id} helios_slot {helios_slot}: {e}")
            return False

    async def synchronize_multiple_slots(self, token_id: int, helios_slot: int) -> int:
        """Синхронизировать 3 последних записи метрик с Helius trades"""
        try:
            # Получаем 3 последних записи метрик (по внутренним секундам)
            jupiter_slots = await self.get_last_3_jupiter_slots(token_id)
            if not jupiter_slots:
                return 0
            
            updated_count = 0
            
            # Получаем пул соединений один раз для цикла
            pool = await get_db_pool()
            for jupiter_slot in jupiter_slots:
                # Получаем trades для диапазона слотов (только по слотам)
                if jupiter_slot["jupiter_slot"]:
                    trades = await self.get_trades_for_slot_range(
                        token_id, helios_slot, jupiter_slot["jupiter_slot"]
                    )
                else:
                    # Если нет jupiter_slot, пропускаем эту итерацию
                    continue
                
                if not trades:
                    continue
                
                # Вычисляем медианы
                medians = self.calculate_medians(trades)
                
                # Обновляем метрики (медианы + торговые агрегаты)
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE token_metrics_seconds SET
                            median_amount_sol = $2,
                            median_amount_usd = $3,
                            median_amount_tokens = $4,
                            median_token_price = $5,
                            buy_count = $6,
                            sell_count = $7,
                            buy_usd = $8,
                            sell_usd = $9
                        WHERE id = $1
                        """,
                        jupiter_slot["id"],
                        medians["median_amount_sol"],
                        medians["median_amount_usd"],
                        medians["median_amount_tokens"],
                        medians["median_token_price"],
                        medians.get("buy_count"),
                        medians.get("sell_count"),
                        medians.get("buy_usd"),
                        medians.get("sell_usd"),
                    )
                    # Bump token_updated_at so Tokens WS reflects changes promptly
                    await conn.execute(
                        "UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP WHERE id = $1",
                        token_id,
                    )
                
                updated_count += 1
                
                if self.debug:
                    print(f"🔄 Token {token_id}: synced {len(trades)} trades for ts {jupiter_slot['ts']}")
            
            return updated_count
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error syncing multiple slots for token {token_id}: {e}")
            return 0

    async def synchronize_all_metrics(self, limit: int = 100) -> Dict:
        """Синхронизировать все Helius trades с Jupiter метриками (новая логика)"""
        try:
            # Получаем trades без синхронизированных медиан
            trades = await self.get_recent_trades_without_medians(limit)
            if not trades:
                return {"success": True, "message": "No trades to synchronize", "updated": 0}
            
            updated_count = 0
            for trade in trades:
                # Используем новую логику синхронизации множественных слотов
                count = await self.synchronize_multiple_slots(
                    trade["token_id"],
                    trade["helios_slot"]
                )
                updated_count += count
            
            return {
                "success": True,
                "message": f"Synchronized {updated_count} Jupiter slots",
                "updated": updated_count,
                "total": len(trades)
            }
            
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "updated": 0}

    async def get_synchronization_stats(self) -> Dict:
        """Получить статистику синхронизации"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Общая статистика
            total_metrics = await conn.fetchval("SELECT COUNT(*) FROM token_metrics_seconds WHERE jupiter_slot IS NOT NULL")
            synced_metrics = await conn.fetchval("SELECT COUNT(*) FROM token_metrics_seconds WHERE jupiter_slot IS NOT NULL AND median_amount_sol IS NOT NULL")
            unsynced_metrics = total_metrics - synced_metrics if total_metrics else 0
            
            return {
                "total_metrics": total_metrics,
                "synced_metrics": synced_metrics,
                "unsynced_metrics": unsynced_metrics,
                "sync_percentage": (synced_metrics / total_metrics * 100) if total_metrics and total_metrics > 0 else 0
            }


# Глобальные функции для использования
async def sync_all_slots(limit: int = 100) -> Dict:
    """Синхронизировать все слоты (новая логика с 3 последними слотами)"""
    synchronizer = SlotSynchronizer()
    return await synchronizer.synchronize_all_metrics(limit)

async def sync_multiple_slots_for_token(token_id: int, helios_slot: int) -> int:
    """Синхронизировать 3 последних Jupiter слота для конкретного токена"""
    synchronizer = SlotSynchronizer()
    return await synchronizer.synchronize_multiple_slots(token_id, helios_slot)

async def get_sync_stats() -> Dict:
    """Получить статистику синхронизации"""
    synchronizer = SlotSynchronizer()
    return await synchronizer.get_synchronization_stats()


if __name__ == "__main__":
    async def main():
        # Тест синхронизации
        stats = await get_sync_stats()
        print(f"📊 Sync Stats: {stats}")
        
        # Синхронизация
        result = await sync_all_slots(50)
        print(f"🔄 Sync Result: {result}")
    
    asyncio.run(main())
