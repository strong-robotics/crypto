#!/usr/bin/env python3
"""
Простий скрипт для застосування міграції торгових позицій
"""

import asyncio
import asyncpg
import os

async def apply_migration():
    """Застосовуємо міграцію для таблиці торгових позицій."""
    
    # Параметри підключення до БД
    DATABASE_URL = "postgresql://postgres:password@localhost:5433/crypto_db"
    
    try:
        # Підключаємося до БД
        conn = await asyncpg.connect(DATABASE_URL)
        print("✅ Підключено до БД")
        
        # Читаємо SQL міграцію
        migration_file = "server/ai/sql/migrations/ai_trading_positions.sql"
        with open(migration_file, 'r', encoding='utf-8') as f:
            migration_sql = f.read()
        
        # Застосовуємо міграцію
        await conn.execute(migration_sql)
        print("✅ Міграція застосована успішно")
        
        # Перевіряємо, що таблиця створена
        result = await conn.fetch("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_name = 'ai_trading_positions'
        """)
        
        if result:
            print("✅ Таблиця ai_trading_positions створена")
        else:
            print("❌ Таблиця ai_trading_positions не знайдена")
            
        await conn.close()
        print("🎉 Міграція завершена!")
        
    except Exception as e:
        print(f"❌ Помилка міграції: {e}")

if __name__ == "__main__":
    asyncio.run(apply_migration())
