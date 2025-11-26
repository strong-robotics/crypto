#!/bin/bash
# Скрипт для відв'язки всіх токенів від кошельків
# Встановлює wallet_id = NULL для всіх токенів в таблиці tokens

cd "$(dirname "$0")" || exit 1

echo "🔓 Відв'язка токенів від кошельків..."
echo ""

# Активація venv та запуск Python скрипта
source server/venv/bin/activate

python3 << 'EOF'
import sys
import os
# Додати server до шляху для імпортів
server_path = os.path.join(os.getcwd(), 'server')
sys.path.insert(0, server_path)
import asyncio
from _v3_db_pool import get_db_pool

async def unbind_all_tokens():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # КРОК 1: Показати відкриті позиції в wallet_history
        open_positions = await conn.fetch(
            """
            SELECT h.id, h.wallet_id, h.token_id, h.entry_iteration, t.name, t.symbol
            FROM wallet_history h
            LEFT JOIN tokens t ON t.id = h.token_id
            WHERE h.exit_iteration IS NULL
            ORDER BY h.id
            """
        )
        
        if open_positions:
            print(f"📊 Знайдено {len(open_positions)} відкритих позицій в wallet_history:")
            for pos in open_positions:
                token_name = (pos.get('name') or pos.get('symbol') or 'N/A')
                if len(token_name) > 30:
                    token_name = token_name[:30] + '...'
                print(f"   Position {pos['id']}: wallet_id={pos['wallet_id']}, token_id={pos['token_id']}, entry_iter={pos['entry_iteration']}, name={token_name}")
            print()
            
            # Закрити всі відкриті позиції
            for pos in open_positions:
                # Використати entry_iteration + 1 як exit_iteration (або поточну ітерацію токена)
                exit_iter = pos['entry_iteration'] + 1 if pos['entry_iteration'] else 1
                await conn.execute(
                    """
                    UPDATE wallet_history
                    SET exit_iteration = $1,
                        exit_token_amount = COALESCE(exit_token_amount, entry_token_amount),
                        exit_price_usd = COALESCE(exit_price_usd, 0.0),
                        exit_amount_usd = COALESCE(exit_amount_usd, 0.0),
                        outcome = 'manual_unbind',
                        reason = 'unbind_script',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = $2
                    """,
                    exit_iter, pos['id']
                )
            print(f"✅ Закрито {len(open_positions)} відкритих позицій")
            print()
        
        # КРОК 2: Показати токени з wallet_id
        bound_tokens = await conn.fetch(
            """
            SELECT t.id, t.token_address, t.wallet_id, t.name, t.symbol
            FROM tokens t
            WHERE t.wallet_id IS NOT NULL
            ORDER BY t.id
            """
        )
        
        if bound_tokens:
            print(f"📊 Знайдено {len(bound_tokens)} прив'язаних токенів:")
            for token in bound_tokens:
                token_name = token.get('name') or token.get('symbol') or 'N/A'
                if len(token_name) > 30:
                    token_name = token_name[:30] + '...'
                print(f"   Token ID {token['id']}: wallet_id={token['wallet_id']}, name={token_name}")
            print()
            
            # Відв'язати всі токени
            await conn.execute(
                """
                UPDATE tokens
                SET wallet_id = NULL,
                    token_updated_at = CURRENT_TIMESTAMP
                WHERE wallet_id IS NOT NULL
                """
            )
            print(f"✅ Всі токени відв'язано від кошельків")
            print()
        else:
            print("✅ Немає прив'язаних токенів в таблиці tokens")
            print()
        
        # КРОК 3: Перевірити результат
        remaining_tokens = await conn.fetchval(
            "SELECT COUNT(*) FROM tokens WHERE wallet_id IS NOT NULL"
        )
        remaining_positions = await conn.fetchval(
            "SELECT COUNT(*) FROM wallet_history WHERE exit_iteration IS NULL"
        )
        
        if remaining_tokens == 0 and remaining_positions == 0:
            print("✅ Підтвердження: всі токени відв'язані, всі позиції закриті")
        else:
            if remaining_tokens > 0:
                print(f"⚠️  Увага: залишилося {remaining_tokens} прив'язаних токенів")
            if remaining_positions > 0:
                print(f"⚠️  Увага: залишилося {remaining_positions} відкритих позицій")

if __name__ == '__main__':
    asyncio.run(unbind_all_tokens())
EOF

deactivate

echo ""
echo "✅ Готово!"

