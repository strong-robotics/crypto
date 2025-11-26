#!/usr/bin/env python3
"""
Clear wallet bindings for tokens where positions are closed.

This script finds all tokens with wallet_id set and clears the binding
if the position is closed (exit_iteration IS NOT NULL in wallet_history).

Usage (run from project root):
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/clear_wallet_bindings.py
"""
from __future__ import annotations

import asyncio
from _v3_db_pool import get_db_pool


async def clear_wallet_bindings() -> dict:
    """Clear tokens.wallet_id for tokens where position is closed.
    Also close orphaned positions in wallet_history (tokens that don't exist anymore)."""
    pool = await get_db_pool()
    synced = 0
    errors = 0
    closed_orphaned = 0
    
    async with pool.acquire() as conn:
        try:
            # STEP 1: Close orphaned positions (tokens that don't exist anymore)
            print("🔍 Step 1: Checking for orphaned positions (tokens deleted but wallet_history still open)...")
            print()
            orphaned_positions = await conn.fetch(
                """
                SELECT h.id AS history_id, h.wallet_id, h.token_id, h.entry_iteration
                FROM wallet_history h
                LEFT JOIN tokens t ON t.id = h.token_id
                WHERE h.exit_iteration IS NULL
                  AND t.id IS NULL
                ORDER BY h.id DESC
                """
            )
            
            if orphaned_positions:
                print(f"   Found {len(orphaned_positions)} orphaned positions (tokens don't exist). Closing them...")
                print()
                for orphan in orphaned_positions:
                    try:
                        # Get current iteration (use entry_iteration + 1 as fallback)
                        current_iteration = orphan.get('entry_iteration', 0) + 1
                        
                        await conn.execute(
                            """
                            UPDATE wallet_history
                            SET exit_iteration=$2,
                                exit_token_amount=COALESCE(exit_token_amount, entry_token_amount),
                                exit_price_usd=0.0,
                                exit_amount_usd=0.0,
                                outcome='orphaned',
                                reason='token_deleted',
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=$1
                            """,
                            orphan['history_id'], current_iteration
                        )
                        closed_orphaned += 1
                        print(f"   ✅ [{closed_orphaned}] Closed orphaned position: history_id={orphan['history_id']}, wallet_id={orphan['wallet_id']}, token_id={orphan['token_id']} (token deleted)")
                    except Exception as e:
                        errors += 1
                        print(f"   ❌ Error closing orphaned position {orphan['history_id']}: {e}")
                print()
            else:
                print("   ✅ No orphaned positions found")
                print()
            
            # STEP 2: Find all tokens with wallet_id set
            print("🔍 Step 2: Checking tokens with wallet_id set...")
            print()
            tokens_with_wallet = await conn.fetch(
                """
                SELECT t.id AS token_id, t.wallet_id, t.name AS token_name, t.token_address
                FROM tokens t
                WHERE t.wallet_id IS NOT NULL
                ORDER BY t.id DESC
                """
            )
            
            if not tokens_with_wallet:
                print("   ✅ No tokens with wallet_id found.")
                total = 0
            else:
                total = len(tokens_with_wallet)
                print(f"   Found {total} tokens with wallet_id set. Checking positions...")
                print()
            
            # Check each token: if position is closed in wallet_history, clear wallet_id
            for token in tokens_with_wallet:
                token_id = token["token_id"]
                wallet_id = token["wallet_id"]
                token_name = token.get("token_name") or "N/A"
                token_address = token.get("token_address") or "N/A"
                
                try:
                    # Check if there's an open position (exit_iteration IS NULL)
                    open_position = await conn.fetchrow(
                        """
                        SELECT id, entry_iteration, exit_iteration
                        FROM wallet_history
                        WHERE token_id=$1 
                          AND wallet_id=$2
                          AND exit_iteration IS NULL
                        LIMIT 1
                        """,
                        token_id, wallet_id
                    )
                    
                    if not open_position:
                        # No open position - check if position was closed
                        closed_position = await conn.fetchrow(
                            """
                            SELECT id, exit_iteration, exit_amount_usd
                            FROM wallet_history
                            WHERE token_id=$1 
                              AND wallet_id=$2
                              AND exit_iteration IS NOT NULL
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            token_id, wallet_id
                        )
                        
                        if closed_position:
                            # Position was closed - clear wallet_id
                            await conn.execute(
                                """
                                UPDATE tokens
                                SET wallet_id = NULL,
                                    token_updated_at = CURRENT_TIMESTAMP
                                WHERE id=$1
                                """,
                                token_id
                            )
                            synced += 1
                            exit_iter = closed_position.get("exit_iteration", "N/A")
                            exit_usd = closed_position.get("exit_amount_usd", 0.0)
                            print(f"✅ [{synced}] Cleared wallet_id for token {token_id} (wallet_id={wallet_id})")
                            print(f"   Token: {token_name[:30]}... | Address: {token_address[:20]}...")
                            print(f"   Position closed at iteration {exit_iter}, exit_amount=${exit_usd:.2f}")
                            print()
                        else:
                            # No position in history at all - clear wallet_id anyway
                            await conn.execute(
                                """
                                UPDATE tokens
                                SET wallet_id = NULL,
                                    token_updated_at = CURRENT_TIMESTAMP
                                WHERE id=$1
                                """,
                                token_id
                            )
                            synced += 1
                            print(f"✅ [{synced}] Cleared wallet_id for token {token_id} (wallet_id={wallet_id})")
                            print(f"   Token: {token_name[:30]}... | Address: {token_address[:20]}...")
                            print(f"   No position found in wallet_history - cleared binding")
                            print()
                    else:
                        # Open position exists - keep wallet_id
                        entry_iter = open_position.get("entry_iteration", "N/A")
                        print(f"⏸️  Token {token_id} (wallet_id={wallet_id}) has OPEN position - keeping binding")
                        print(f"   Token: {token_name[:30]}... | Entry iteration: {entry_iter}")
                        print()
                except Exception as e:
                    errors += 1
                    print(f"❌ Error processing token {token_id}: {e}")
                    print()
            
            print("=" * 60)
            print(f"📊 Summary:")
            print(f"   Orphaned positions closed: {closed_orphaned}")
            print(f"   Total tokens checked: {total}")
            print(f"   Cleared bindings: {synced}")
            print(f"   Errors: {errors}")
            if total > 0:
                print(f"   Open positions (kept): {total - synced - errors}")
            print("=" * 60)
            
            return {
                "success": True,
                "synced": synced,
                "closed_orphaned": closed_orphaned,
                "errors": errors,
                "total": total
            }
        except Exception as e:
            print(f"❌ Fatal error: {e}")
            return {"success": False, "error": str(e), "synced": synced, "errors": errors, "total": 0}


async def main():
    """Main entry point."""
    print("🚀 Starting wallet bindings cleanup...")
    print()
    result = await clear_wallet_bindings()
    
    if result.get("success"):
        print()
        print("✅ Cleanup completed successfully!")
    else:
        print()
        print(f"❌ Cleanup failed: {result.get('error', 'Unknown error')}")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())

