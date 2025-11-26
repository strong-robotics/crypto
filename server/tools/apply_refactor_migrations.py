#!/usr/bin/env python3
"""
Скрипт для застосування міграцій рефакторингу (видалення simulation).

ВИКОРИСТАННЯ:
    python3 server/tools/apply_refactor_migrations.py

АБО з параметрами:
    python3 server/tools/apply_refactor_migrations.py --dry-run  # Тільки перевірка
    python3 server/tools/apply_refactor_migrations.py --force     # Без підтвердження
"""

import asyncio
import sys
import os
from pathlib import Path
from _v3_db_pool import get_db_pool

# Шлях до міграцій
MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# Порядок застосування міграцій
MIGRATION_FILES = [
    "rename_sim_wallet_history_to_wallet_history.sql",
    "20251106_data_migration.sql",  # Опціонально, якщо потрібно мігрувати дані
    "20251106_tokens_cleanup.sql",
    "20251106_drop_sim_wallets.sql",
    "20250116_remove_history_ready.sql",  # Remove deprecated history_ready columns
    "20250117_add_has_real_trading.sql",  # Add has_real_trading column for SWAP/TRANSFER check
]


async def check_migration_needed(conn) -> dict:
    """Перевірити, чи потрібні міграції."""
    status = {
        "sim_wallet_history_exists": False,
        "wallet_history_exists": False,
        "sim_wallets_exists": False,
        "wallets_exists": False,
        "sim_fields_exist": [],
        "new_fields_exist": [],
    }
    
    # Перевірка таблиць
    tables = await conn.fetch("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
          AND table_name IN ('sim_wallet_history', 'wallet_history', 'sim_wallets', 'wallets')
    """)
    for row in tables:
        name = row["table_name"]
        if name == "sim_wallet_history":
            status["sim_wallet_history_exists"] = True
        elif name == "wallet_history":
            status["wallet_history_exists"] = True
        elif name == "sim_wallets":
            status["sim_wallets_exists"] = True
        elif name == "wallets":
            status["wallets_exists"] = True
    
    # Перевірка полів tokens
    sim_fields = await conn.fetch("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'tokens' 
          AND column_name LIKE 'sim_%'
    """)
    status["sim_fields_exist"] = [r["column_name"] for r in sim_fields]
    
    new_fields = await conn.fetch("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'tokens' 
          AND column_name IN ('plan_sell_iteration', 'plan_sell_price_usd', 'wallet_id', 'cur_income_price_usd')
    """)
    status["new_fields_exist"] = [r["column_name"] for r in new_fields]
    
    return status


async def apply_migration_file(conn, filepath: Path, dry_run: bool = False) -> bool:
    """Застосувати один файл міграції."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            sql = f.read().strip()
        
        if not sql:
            print(f"⚠️  {filepath.name}: файл порожній, пропускаємо")
            return True
        
        if dry_run:
            print(f"🔍 [DRY-RUN] {filepath.name}: перевірка синтаксису...")
            # Перевірка синтаксису (спроба виконати в транзакції з rollback)
            async with conn.transaction():
                try:
                    await conn.execute("BEGIN")
                    await conn.execute(sql)
                    await conn.execute("ROLLBACK")
                    print(f"✅ [DRY-RUN] {filepath.name}: синтаксис правильний")
                    return True
                except Exception as e:
                    print(f"❌ [DRY-RUN] {filepath.name}: помилка синтаксису: {e}")
                    return False
        else:
            print(f"📝 Застосовуємо {filepath.name}...")
            await conn.execute(sql)
            print(f"✅ {filepath.name}: успішно застосовано")
            return True
    except Exception as e:
        print(f"❌ {filepath.name}: помилка: {e}")
        return False


async def main():
    """Головна функція."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Застосувати міграції рефакторингу")
    parser.add_argument("--dry-run", action="store_true", help="Тільки перевірка, без застосування")
    parser.add_argument("--force", action="store_true", help="Без підтвердження")
    args = parser.parse_args()
    
    print("=" * 60)
    print("🔧 Застосування міграцій рефакторингу")
    print("=" * 60)
    
    if args.dry_run:
        print("⚠️  РЕЖИМ ПЕРЕВІРКИ (dry-run) - зміни НЕ будуть застосовані")
        print()
    
    # Підключення до БД
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Перевірка поточного стану
            print("📊 Перевірка поточного стану БД...")
            status = await check_migration_needed(conn)
            
            print("\n📋 Поточний стан:")
            print(f"  - sim_wallet_history: {'✅ існує' if status['sim_wallet_history_exists'] else '❌ не існує'}")
            print(f"  - wallet_history: {'✅ існує' if status['wallet_history_exists'] else '❌ не існує'}")
            print(f"  - sim_wallets: {'✅ існує' if status['sim_wallets_exists'] else '❌ не існує'}")
            print(f"  - wallets: {'✅ існує' if status['wallets_exists'] else '❌ не існує'}")
            print(f"  - sim_* поля в tokens: {len(status['sim_fields_exist'])} ({', '.join(status['sim_fields_exist'][:5])}{'...' if len(status['sim_fields_exist']) > 5 else ''})")
            print(f"  - нові поля в tokens: {len(status['new_fields_exist'])} ({', '.join(status['new_fields_exist'])})")
            
            # Визначення, чи потрібні міграції
            needs_migration = (
                status["sim_wallet_history_exists"] or
                status["sim_wallets_exists"] or
                len(status["sim_fields_exist"]) > 0 or
                not status["wallet_history_exists"] or
                len(status["new_fields_exist"]) < 4
            )
            
            if not needs_migration:
                print("\n✅ Міграції не потрібні - БД вже в актуальному стані!")
                return 0
            
            print("\n⚠️  Потрібні міграції!")
            
            if not args.force and not args.dry_run:
                response = input("\n❓ Продовжити застосування міграцій? (yes/no): ")
                if response.lower() not in ("yes", "y", "так", "т"):
                    print("❌ Скасовано користувачем")
                    return 1
            
            # Застосування міграцій
            print("\n📝 Застосування міграцій...")
            success_count = 0
            failed_count = 0
            
            for filename in MIGRATION_FILES:
                filepath = MIGRATIONS_DIR / filename
                if not filepath.exists():
                    print(f"⚠️  {filename}: файл не знайдено, пропускаємо")
                    continue
                
                success = await apply_migration_file(conn, filepath, dry_run=args.dry_run)
                if success:
                    success_count += 1
                else:
                    failed_count += 1
                    if not args.dry_run:
                        print("❌ Зупиняємо через помилку")
                        break
            
            # Підсумок
            print("\n" + "=" * 60)
            if args.dry_run:
                print("🔍 РЕЗУЛЬТАТ ПЕРЕВІРКИ:")
            else:
                print("📊 РЕЗУЛЬТАТ ЗАСТОСУВАННЯ:")
            print(f"  ✅ Успішно: {success_count}")
            print(f"  ❌ Помилок: {failed_count}")
            
            if not args.dry_run and failed_count == 0:
                # Перевірка після міграції
                print("\n📊 Перевірка після міграції...")
                new_status = await check_migration_needed(conn)
                
                print(f"  - wallet_history: {'✅ існує' if new_status['wallet_history_exists'] else '❌ не існує'}")
                print(f"  - wallets: {'✅ існує' if new_status['wallets_exists'] else '❌ не існує'}")
                print(f"  - sim_* поля: {len(new_status['sim_fields_exist'])} (має бути 0)")
                print(f"  - нові поля: {len(new_status['new_fields_exist'])} (має бути 4)")
                
                if (not new_status["sim_wallet_history_exists"] and
                    not new_status["sim_wallets_exists"] and
                    len(new_status["sim_fields_exist"]) == 0 and
                    new_status["wallet_history_exists"] and
                    new_status["wallets_exists"] and
                    len(new_status["new_fields_exist"]) == 4):
                    print("\n🎉 Міграції успішно застосовані!")
                    return 0
                else:
                    print("\n⚠️  Міграції застосовані, але стан БД не відповідає очікуваному")
                    return 1
            
            return 0 if failed_count == 0 else 1
            
    except Exception as e:
        print(f"\n❌ Критична помилка: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

