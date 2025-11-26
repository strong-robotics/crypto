#!/usr/bin/env python3
"""
Перевірка даних перед міграцією - визначити, які дані можуть бути втрачені.

ВИКОРИСТАННЯ:
    python3 server/tools/check_data_before_migration.py
"""

import asyncio
from _v3_db_pool import get_db_pool


async def check_data():
    """Перевірити дані перед міграцією."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        print("=" * 60)
        print("🔍 Перевірка даних перед міграцією")
        print("=" * 60)
        
        # 1. Перевірка sim_* полів в tokens
        print("\n📊 1. Дані в tokens.sim_* полях:")
        sim_data = await conn.fetchrow("""
            SELECT 
                COUNT(*) FILTER (WHERE sim_buy_iteration IS NOT NULL) AS tokens_with_sim_buy,
                COUNT(*) FILTER (WHERE sim_sell_iteration IS NOT NULL) AS tokens_with_sim_sell,
                COUNT(*) FILTER (WHERE sim_wallet_id IS NOT NULL) AS tokens_with_sim_wallet,
                COUNT(*) FILTER (WHERE sim_buy_iteration IS NOT NULL AND sim_sell_iteration IS NULL) AS open_sim_positions,
                COUNT(*) FILTER (WHERE sim_buy_iteration IS NOT NULL AND sim_sell_iteration IS NOT NULL) AS closed_sim_positions
            FROM tokens
        """)
        
        if sim_data:
            print(f"  - Токени з sim_buy_iteration: {sim_data['tokens_with_sim_buy']}")
            print(f"  - Токени з sim_sell_iteration: {sim_data['tokens_with_sim_sell']}")
            print(f"  - Токени з sim_wallet_id: {sim_data['tokens_with_sim_wallet']}")
            print(f"  - Відкриті sim позиції: {sim_data['open_sim_positions']}")
            print(f"  - Закриті sim позиції: {sim_data['closed_sim_positions']}")
            
            if sim_data['open_sim_positions'] > 0 or sim_data['closed_sim_positions'] > 0:
                print(f"\n  ⚠️  УВАГА: Знайдено {sim_data['open_sim_positions'] + sim_data['closed_sim_positions']} позицій у sim_* полях!")
                print(f"     Ці дані будуть ВТРАЧЕНІ, якщо не мігрувати їх у wallet_history!")
        
        # 2. Перевірка sim_wallets
        print("\n📊 2. Дані в таблиці sim_wallets:")
        try:
            sim_wallets_count = await conn.fetchval("SELECT COUNT(*) FROM sim_wallets")
            print(f"  - Записів у sim_wallets: {sim_wallets_count}")
            
            if sim_wallets_count > 0:
                sim_wallets_data = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) FILTER (WHERE active_token_id IS NOT NULL) AS wallets_in_trade,
                        SUM(cash_usd) AS total_cash,
                        SUM(total_profit_usd) AS total_profit
                    FROM sim_wallets
                """)
                print(f"  - Кошельків в торгівлі: {sim_wallets_data['wallets_in_trade']}")
                print(f"  - Загальний cash_usd: {sim_wallets_data['total_cash']}")
                print(f"  - Загальний profit: {sim_wallets_data['total_profit']}")
                print(f"\n  ⚠️  УВАГА: Дані в sim_wallets будуть ВТРАЧЕНІ!")
                print(f"     Рекомендується мігрувати їх у таблицю wallets!")
        except Exception as e:
            print(f"  - Таблиця sim_wallets не існує або помилка: {e}")
        
        # 3. Перевірка sim_wallet_history
        print("\n📊 3. Дані в таблиці sim_wallet_history:")
        try:
            sim_history_count = await conn.fetchval("SELECT COUNT(*) FROM sim_wallet_history")
            print(f"  - Записів у sim_wallet_history: {sim_history_count}")
            
            if sim_history_count > 0:
                sim_history_data = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) FILTER (WHERE exit_iteration IS NULL) AS open_positions,
                        COUNT(*) FILTER (WHERE exit_iteration IS NOT NULL) AS closed_positions,
                        SUM(entry_amount_usd) AS total_entry_amount,
                        SUM(exit_amount_usd) AS total_exit_amount
                    FROM sim_wallet_history
                """)
                print(f"  - Відкритих позицій: {sim_history_data['open_positions']}")
                print(f"  - Закритих позицій: {sim_history_data['closed_positions']}")
                print(f"  - Загальна сума входів: {sim_history_data['total_entry_amount']}")
                print(f"  - Загальна сума виходів: {sim_history_data['total_exit_amount']}")
                print(f"\n  ℹ️  Ці дані будуть збережені (таблиця перейменовується в wallet_history)")
        except Exception as e:
            print(f"  - Таблиця sim_wallet_history не існує або помилка: {e}")
        
        # 4. Перевірка wallet_history (чи вже існує)
        print("\n📊 4. Дані в таблиці wallet_history:")
        try:
            wallet_history_count = await conn.fetchval("SELECT COUNT(*) FROM wallet_history")
            print(f"  - Записів у wallet_history: {wallet_history_count}")
        except Exception as e:
            print(f"  - Таблиця wallet_history не існує: {e}")
        
        # 5. Перевірка wallets (чи вже існує)
        print("\n📊 5. Дані в таблиці wallets:")
        try:
            wallets_count = await conn.fetchval("SELECT COUNT(*) FROM wallets")
            print(f"  - Записів у wallets: {wallets_count}")
        except Exception as e:
            print(f"  - Таблиця wallets не існує: {e}")
        
        # 6. Підсумок
        print("\n" + "=" * 60)
        print("📋 ПІДСУМОК:")
        print("=" * 60)
        
        data_at_risk = False
        warnings = []
        
        if sim_data and (sim_data['open_sim_positions'] > 0 or sim_data['closed_sim_positions'] > 0):
            data_at_risk = True
            warnings.append(f"⚠️  {sim_data['open_sim_positions'] + sim_data['closed_sim_positions']} позицій у tokens.sim_* будуть втрачені")
        
        try:
            if sim_wallets_count and sim_wallets_count > 0:
                data_at_risk = True
                warnings.append(f"⚠️  {sim_wallets_count} записів у sim_wallets будуть втрачені")
        except:
            pass
        
        if data_at_risk:
            print("\n❌ Є дані, які можуть бути втрачені!")
            for warning in warnings:
                print(f"  {warning}")
            print("\n💡 РЕКОМЕНДАЦІЇ:")
            print("  1. Створіть бекап БД перед міграцією")
            print("  2. Розкоментуйте відповідні блоки в 20251106_data_migration.sql")
            print("  3. Застосуйте data migration ПЕРЕД schema migration")
            print("  4. Перевірте результат після міграції")
        else:
            print("\n✅ Дані безпечні - немає даних, які будуть втрачені")
            print("   Можна безпечно застосувати міграції")
        
        print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(check_data())

