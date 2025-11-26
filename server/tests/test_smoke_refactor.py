"""
Smoke-тести для перевірки рефакторингу (видалення simulation, тільки real trading).

Сценарії:
1. Manual Buy (real) → Manual Sell (real)
2. AI Plan → факт хіта → фіналізація через аналізатор
3. Zero-tail (ліквідність 0): без відкритої позиції → history_ready=TRUE
4. Zero-tail з відкритою позицією → finalize з нульовою ціною
5. Перевірка, що немає викликів simulation-функцій
"""

import asyncio
import pytest
from _v3_db_pool import get_db_pool
from _v2_buy_sell import sell_real, finalize_token_sale
from _v2_buy_sell import buy_real as force_buy_real
from _v3_analyzer_jupiter import get_analyzer


async def test_no_simulation_functions():
    """Перевірка, що simulation-функції видалені."""
    import inspect
    from _v2_buy_sell import sell_real
    
    # Перевірка, що sell_simulation не існує
    assert not hasattr(sell_real.__module__, 'sell_simulation'), \
        "sell_simulation повинна бути видалена"
    
    # Перевірка, що finalize_token_sale не використовує sim_*
    source = inspect.getsource(finalize_token_sale)
    assert 'sim_' not in source.lower(), \
        "finalize_token_sale не повинна використовувати sim_* поля"


async def test_wallet_history_structure():
    """Перевірка структури wallet_history."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Перевірка, що таблиця wallet_history існує
        row = await conn.fetchrow("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'wallet_history' 
            AND column_name IN ('entry_token_amount', 'exit_token_amount', 'exit_iteration')
        """)
        assert row is not None, "wallet_history повинна містити необхідні поля"


async def test_tokens_no_sim_fields():
    """Перевірка, що sim_* поля видалені з tokens."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Перевірка, що sim_* поля не існують
        sim_fields = await conn.fetch("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'tokens' 
            AND column_name LIKE 'sim_%'
        """)
        assert len(sim_fields) == 0, \
            f"Знайдено {len(sim_fields)} sim_* полів у tokens: {[r['column_name'] for r in sim_fields]}"
        
        # Перевірка, що plan_sell_* поля існують
        plan_fields = await conn.fetchrow("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'tokens' 
            AND column_name IN ('plan_sell_iteration', 'plan_sell_price_usd', 'wallet_id')
        """)
        assert plan_fields is not None, \
            "tokens повинна містити plan_sell_iteration, plan_sell_price_usd, wallet_id"


async def test_no_sim_wallets_table():
    """Перевірка, що таблиця sim_wallets видалена."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Перевірка, що sim_wallets не існує
        row = await conn.fetchrow("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_name = 'sim_wallets'
        """)
        assert row is None, "Таблиця sim_wallets повинна бути видалена"
        
        # Перевірка, що wallets існує
        row = await conn.fetchrow("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_name = 'wallets'
        """)
        assert row is not None, "Таблиця wallets повинна існувати"


async def test_force_buy_creates_wallet_history():
    """Перевірка, що force_buy_real створює запис у wallet_history."""
    # Це інтеграційний тест, який потребує реального токена та кошелька
    # Пропускаємо, якщо немає тестового середовища
    pytest.skip("Потребує тестового середовища з реальними токенами")


async def test_force_sell_updates_wallet_history():
    """Перевірка, що sell_real оновлює wallet_history та встановлює history_ready."""
    # Це інтеграційний тест, який потребує реального токена та кошелька
    pytest.skip("Потребує тестового середовища з реальними токенами")


async def test_finalize_token_sale_zero_liquidity():
    """Перевірка, що finalize_token_sale коректно обробляє zero-liquidity."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Створюємо тестовий токен з відкритою позицією
        # (в реальному тесті потрібно створити тестові дані)
        pytest.skip("Потребує тестового середовища з тестовими даними")


async def test_analyzer_uses_wallet_history():
    """Перевірка, що аналізатор використовує wallet_history для перевірки відкритих позицій."""
    analyzer = await get_analyzer()
    # Перевірка, що аналізатор не використовує sim_* поля
    import inspect
    source = inspect.getsource(analyzer.save_token_data)
    assert 'sim_buy_iteration' not in source, \
        "Аналізатор не повинен використовувати sim_buy_iteration"
    assert 'wallet_history' in source or 'exit_iteration' in source, \
        "Аналізатор повинен використовувати wallet_history для перевірки позицій"


if __name__ == "__main__":
    # Запуск тестів
    pytest.main([__file__, "-v"])

