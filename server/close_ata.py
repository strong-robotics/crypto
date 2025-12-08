#!/usr/bin/env python3
"""
Close an associated token account (ATA) to reclaim the 0.00203928 SOL rent.

Usage examples:
  python3 server/close_ata.py --token-id 1087
  python3 server/close_ata.py --token-address <mint> --key-id 2
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Optional, Tuple

try:
    import asyncpg
except ModuleNotFoundError:
    asyncpg = None

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

from config import config

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")


def _wallet_file() -> Path:
    key_path = Path(config.WALLET_KEYS_FILE)
    if not key_path.is_absolute():
        key_path = Path(config.BASE_DIR) / key_path
    return key_path


def _load_keypair(key_id: int) -> Keypair:
    wallet_path = _wallet_file()
    if not wallet_path.exists():
        raise RuntimeError(f"Wallet keys file not found: {wallet_path}")
    with open(wallet_path) as fh:
        entries = json.load(fh)
    for entry in entries:
        if int(entry.get("id", -1)) != key_id:
            continue
        bits = entry.get("bits")
        if not bits:
            break
        return Keypair.from_bytes(bytes(bits))
    raise RuntimeError(f"Wallet key_id={key_id} not found in {wallet_path}")


async def _fetch_token_details(token_id: int) -> Tuple[int, str]:
    if asyncpg is None:
        raise RuntimeError(
            "asyncpg is required to resolve --token-id. Install it or pass --token-address/--key-id."
        )
    conn = await asyncpg.connect(
        host=config.DB_HOST,
        port=getattr(config, "DB_PORT", 5432),
        database=config.DB_NAME,
        user=config.DB_USER,
        password=getattr(config, "DB_PASSWORD", "") or None,
    )
    try:
        row = await conn.fetchrow(
            """
            SELECT
                wh.wallet_id,
                COALESCE(t.token_address, th.token_address) AS token_address
            FROM wallet_history wh
            LEFT JOIN tokens t ON t.id = wh.token_id
            LEFT JOIN tokens_history th ON th.id = wh.token_id
            WHERE wh.token_id = $1
            ORDER BY wh.id DESC
            LIMIT 1
            """,
            token_id,
        )
        if not row:
            raise RuntimeError(f"No wallet_history entry found for token_id={token_id}")
        if row["wallet_id"] is None or not row["token_address"]:
            raise RuntimeError(
                f"Incomplete history for token_id={token_id}: wallet_id={row['wallet_id']}, "
                f"token_address={row['token_address']}"
            )
        return int(row["wallet_id"]), str(row["token_address"])
    finally:
        await conn.close()


def _resolve_rpc_endpoint() -> str:
    helius_url = getattr(config, "HELIUS_RPC_URL", "").strip()
    if helius_url:
        return helius_url
    helius_key = getattr(config, "HELIUS_API_KEY", "").strip()
    if helius_key:
        return f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    return getattr(config, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    seeds = [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)]
    ata, _ = Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN_PROGRAM_ID)
    return ata


async def _close_ata(keypair: Keypair, token_address: str, *, force: bool = False) -> None:
    owner = keypair.pubkey()
    mint = Pubkey.from_string(token_address)
    ata = _derive_ata(owner, mint)
    rpc_url = _resolve_rpc_endpoint()

    async with AsyncClient(rpc_url, commitment="confirmed") as client:
        account_info = await client.get_account_info(ata)
        if account_info.value is None:
            print(f"[CloseATA] ℹ️ ATA {ata} does not exist. Nothing to close.")
            return
        lamports = account_info.value.lamports

        balance_resp = await client.get_token_account_balance(ata)
        balance = balance_resp.value
        ui_amount = float(balance.ui_amount) if balance and balance.ui_amount is not None else 0.0
        if ui_amount > 0:
            if not force:
                raise RuntimeError(
                    f"ATA holds {ui_amount} tokens. Sell/transfer them or rerun with --force to skip."
                )
            print(f"[CloseATA] ⚠️ Skipping close: ATA still has {ui_amount} tokens.")
            return

        instruction = Instruction(
            program_id=TOKEN_PROGRAM_ID,
            data=bytes([9]),  # SPL Token close-account discriminator
            accounts=[
                AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
            ],
        )
        message = Message([instruction], owner)
        latest_blockhash = (await client.get_latest_blockhash()).value.blockhash
        tx = Transaction([keypair], message, latest_blockhash if isinstance(latest_blockhash, Hash) else Hash.from_string(latest_blockhash))
        opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed", max_retries=3)
        response = await client.send_raw_transaction(bytes(tx), opts=opts)
        signature = response.value
        refund_sol = lamports / LAMPORTS_PER_SOL
        print(f"[CloseATA] ✅ Submitted close-account tx: {signature}")
        print(f"[CloseATA] 💸 Rent refund: {refund_sol:.9f} SOL (ATA {ata})")


async def main():
    parser = argparse.ArgumentParser(description="Close an associated token account to reclaim rent.")
    parser.add_argument("--token-id", type=int, help="Resolve wallet/mint from wallet_history.")
    parser.add_argument("--token-address", help="Token mint address (required without --token-id).")
    parser.add_argument("--key-id", type=int, help="Wallet key ID (required without --token-id).")
    parser.add_argument("--force", action="store_true", help="Close even if ATA still holds tokens.")
    args = parser.parse_args()

    token_address = args.token_address
    wallet_id = args.key_id

    if args.token_id:
        wallet_id, token_address = await _fetch_token_details(args.token_id)
        print(f"[CloseATA] Resolved token_id={args.token_id} → wallet_id={wallet_id}, token_address={token_address}")

    if not token_address or wallet_id is None:
        parser.error("Either provide --token-id or both --token-address and --key-id.")

    keypair = _load_keypair(wallet_id)
    print(f"[CloseATA] Loaded wallet {wallet_id}: {keypair.pubkey()}")
    await _close_ata(keypair, token_address, force=args.force)


if __name__ == "__main__":
    asyncio.run(main())
