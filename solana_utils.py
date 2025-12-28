import asyncio
from typing import Optional, Tuple
from config import config
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

TOKEN_2022_PROGRAM_ID = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")

def derive_ata(owner: Pubkey, mint: Pubkey, token_program_id: Pubkey = TOKEN_PROGRAM_ID) -> Pubkey:
    seeds = [bytes(owner), bytes(token_program_id), bytes(mint)]
    ata, _ = Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN_PROGRAM_ID)
    return ata


async def get_token_program_id(client: AsyncClient, mint: Pubkey) -> Pubkey:
    """Determine if mint is Standard Token or Token-2022."""
    try:
        info = await client.get_account_info(mint)
        if info.value:
            owner = info.value.owner
            if str(owner) == str(TOKEN_2022_PROGRAM_ID):
                return TOKEN_2022_PROGRAM_ID
    except Exception:
        pass
    return TOKEN_PROGRAM_ID

async def get_ata_balance(keypair: Keypair, token_address: str, rpc_url: str) -> Tuple[float, int]:
    """Get current token balance in ATA. Returns (ui_amount, raw_amount)."""
    owner = keypair.pubkey()
    mint = Pubkey.from_string(token_address)
    
    async with AsyncClient(rpc_url, commitment="confirmed") as client:
        # Resolve correct Token Program ID
        token_program_id = await get_token_program_id(client, mint)
        ata = derive_ata(owner, mint, token_program_id)
        
        try:
            balance_resp = await client.get_token_account_balance(ata)
            if balance_resp.value is None:
                return 0.0, 0
            balance = balance_resp.value
            ui_amount = float(balance.ui_amount) if balance.ui_amount is not None else 0.0
            raw_amount = int(balance.amount) if balance.amount else 0
            return ui_amount, raw_amount
        except Exception as e:
            # If account is not found, it means balance is 0
            if "could not find account" in str(e).lower():
                return 0.0, 0
            raise e

async def burn_tokens_logic(
    keypair: Keypair,
    token_address: str,
    token_amount: float,
    token_decimals: int,
    rpc_url: str
) -> Tuple[bool, Optional[str]]:
    """
    Burn tokens from ATA using BurnChecked instruction.
    Returns: (success, error_message or signature)
    """
    owner = keypair.pubkey()
    mint = Pubkey.from_string(token_address)
    # Convert token amount to raw units
    raw_amount = int(token_amount * (10 ** token_decimals))
    
    try:
        async with AsyncClient(rpc_url, commitment="confirmed") as client:
            token_program_id = await get_token_program_id(client, mint)
            ata = derive_ata(owner, mint, token_program_id)
            
            account_info = await client.get_account_info(ata)
            if account_info.value is None:
                return False, "ATA does not exist"
            
            balance_resp = await client.get_token_account_balance(ata)
            balance = balance_resp.value
            if balance is None:
                return False, "Failed to get token balance"
            
            actual_raw_amount = int(balance.amount)
            if actual_raw_amount < raw_amount:
                raw_amount = actual_raw_amount
            
            if raw_amount == 0:
                return True, "No tokens to burn"
            
            amount_bytes = raw_amount.to_bytes(8, byteorder='little')
            decimals_byte = token_decimals.to_bytes(1, byteorder='little')
            instruction_data = bytes([15]) + amount_bytes + decimals_byte
            
            instruction = Instruction(
                program_id=token_program_id,
                data=instruction_data,
                accounts=[
                    AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=mint, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
                ],
            )
            
            message = Message([instruction], owner)
            latest_blockhash = (await client.get_latest_blockhash()).value.blockhash
            tx = Transaction(
                [keypair],
                message,
                latest_blockhash if isinstance(latest_blockhash, Hash) else Hash.from_string(str(latest_blockhash))
            )
            
            opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed", max_retries=3)
            response = await client.send_raw_transaction(bytes(tx), opts=opts)
            signature = response.value
            
            if signature:
                return True, str(signature)
            else:
                return False, "Transaction failed"
                
    except Exception as e:
        error_msg = str(e)
        if "invalid instruction" in error_msg.lower() or "not supported" in error_msg.lower() or "0x1" in error_msg:
            return False, f"Token does not support burn: {error_msg}"
        return False, error_msg

async def close_ata_logic(
    keypair: Keypair,
    token_address: str,
    rpc_url: str
) -> Tuple[bool, Optional[str]]:
    """
    Close ATA to reclaim Rent.
    Returns: (success, error_message or signature)
    """
    owner = keypair.pubkey()
    mint = Pubkey.from_string(token_address)
    try:
        async with AsyncClient(rpc_url, commitment="confirmed") as client:
            token_program_id = await get_token_program_id(client, mint)
            ata = derive_ata(owner, mint, token_program_id)
            
            account_info = await client.get_account_info(ata)
            if account_info.value is None:
                return True, "ATA already closed"
            
            balance_resp = await client.get_token_account_balance(ata)
            balance = balance_resp.value
            ui_amount = float(balance.ui_amount) if balance and balance.ui_amount is not None else 0.0
            
            if ui_amount > 0.000001:
                return False, f"ATA still has {ui_amount:.8f} tokens"
            
            instruction = Instruction(
                program_id=token_program_id,
                data=bytes([9]),
                accounts=[
                    AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=owner, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
                ],
            )
            
            message = Message([instruction], owner)
            latest_blockhash = (await client.get_latest_blockhash()).value.blockhash
            tx = Transaction(
                [keypair],
                message,
                latest_blockhash if isinstance(latest_blockhash, Hash) else Hash.from_string(str(latest_blockhash))
            )
            
            opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed", max_retries=3)
            response = await client.send_raw_transaction(bytes(tx), opts=opts)
            signature = response.value
            
            if signature:
                return True, str(signature)
            else:
                return False, "Transaction failed"
                
    except Exception as e:
        error_msg = str(e)
        if "does not exist" in error_msg.lower() or "not found" in error_msg.lower():
            return True, "ATA already closed"
        return False, error_msg


def resolve_rpc_endpoint() -> str:
    """Resolve RPC endpoint from config (Helius or standard Solana)."""
    helius_url = getattr(config, "HELIUS_RPC_URL", "").strip()
    if helius_url:
        return helius_url
    helius_key = getattr(config, "HELIUS_API_KEY", "").strip()
    if helius_key:
        return f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    return getattr(config, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


async def wait_for_signature_confirmation(signature: str, rpc_endpoint: str, timeout_sec: float = 30.0, poll_interval: float = 1.0) -> bool:
    """Poll RPC for signature confirmation until timeout."""
    try:
        from solana.rpc.async_api import AsyncClient
        from solders.signature import Signature
        
        sig = Signature.from_string(signature)
        start_time = asyncio.get_running_loop().time()
        
        async with AsyncClient(rpc_endpoint, commitment="confirmed") as client:
            while True:
                try:
                    # Using confirm_transaction usually waits, but we want control
                    # Let's try get_signature_statuses
                    statuses = await client.get_signature_statuses([sig])
                    if statuses.value and statuses.value[0]:
                        status = statuses.value[0]
                        if status.confirmation_status in ["confirmed", "finalized"]:
                            if status.err:
                                return False
                            return True
                except Exception:
                    pass
                
                if asyncio.get_running_loop().time() - start_time > timeout_sec:
                    return False
                    
                await asyncio.sleep(poll_interval)
    except Exception:
        return False
