import aiohttp
import asyncio
import aiofiles
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Optional
from config import config

class HeliusTradesReporter:

    def __init__(self, helius_api_key: str):
        self.api_key = helius_api_key
        self.base_url = "https://api.helius.xyz/v0"
        self.sol_price_cache = {}
        self.file_cache = {}
        self.existing_signatures = {}
        self.session = None
        self.headers = {
            "User-Agent": "HeliusReporter/1.0",
            "Accept": "application/json"
        }
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def ensure_session(self):
        """Ensure session is created"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers)
    
    def get_sol_for_pump_fun(self, tx, token_mint, is_buy):
        amount_sol = 0
        
        if tx.get('accountData'):
            for account_data in tx['accountData']:
                if account_data.get('account') == token_mint:
                    balance_change = account_data.get('nativeBalanceChange', 0)

                    if balance_change != 0:
                        amount_sol = abs(balance_change) / 1_000_000_000

                        return amount_sol
        
        if tx.get('nativeTransfers') and amount_sol == 0:
            for native_transfer in tx['nativeTransfers']:
                from_user = native_transfer.get('fromUserAccount', '')
                to_user = native_transfer.get('toUserAccount', '')
                amount = native_transfer.get('amount', 0)
                
                if is_buy and to_user == token_mint:
                    amount_sol = amount / 1_000_000_000
                    break

                elif not is_buy and from_user == token_mint:
                    amount_sol = amount / 1_000_000_000
                    break
            
            if amount_sol == 0:
                max_amount = 0

                for native_transfer in tx['nativeTransfers']:
                    amount = native_transfer.get('amount', 0)
                    if amount > max_amount:
                        max_amount = amount

                if max_amount > 0:
                    amount_sol = max_amount / 1_000_000_000
        
        if amount_sol == 0:
            for transfer in tx.get('tokenTransfers', []):
                if transfer.get('mint') == 'So11111111111111111111111111111111111111112':
                    token_amount = transfer.get('tokenAmount', 0)

                    if token_amount > 1000:
                        amount_sol = token_amount / 1_000_000_000
                    else:
                        amount_sol = token_amount
                    
                    break
        
        return amount_sol

    def get_sol_for_jupiter_raydium(self, tx, token_mint, is_buy, sol_transfer):
        amount_sol = 0
        
        if sol_transfer:
            if is_buy:
                for transfer in tx.get('tokenTransfers', []):
                    if transfer.get('mint') == 'So11111111111111111111111111111111111111112':
                        to_user = transfer.get('toUserAccount', '')

                        if to_user == token_mint:
                            token_amount = transfer.get('tokenAmount', 0)
                            if token_amount > 1000:
                                amount_sol = token_amount / 1_000_000_000
                            else:
                                amount_sol = token_amount

                            break

            else:
                for transfer in tx.get('tokenTransfers', []):
                    if transfer.get('mint') == 'So11111111111111111111111111111111111111112':
                        to_user = transfer.get('toUserAccount', '')

                        if to_user != token_mint:
                            token_amount = transfer.get('tokenAmount', 0)

                            if token_amount > 1000:
                                amount_sol = token_amount / 1_000_000_000
                            else:
                                amount_sol = token_amount

                            break
            
            if amount_sol == 0:
                token_amount = sol_transfer.get('tokenAmount', 0)

                if token_amount > 1000:
                    amount_sol = token_amount / 1_000_000_000
                else:
                    amount_sol = token_amount

        elif tx.get('nativeTransfers'):
            total_native_amount = 0
            
            for native_transfer in tx['nativeTransfers']:
                total_native_amount += native_transfer.get('amount', 0)

            amount_sol = total_native_amount / 1_000_000_000

        elif tx.get('accountData'):
            for account_data in tx['accountData']:
                if account_data.get('account') == token_mint:
                    balance_change = account_data.get('nativeBalanceChange', 0)
                    amount_sol = abs(balance_change) / 1_000_000_000
                    
                    break
        
        return amount_sol

    async def get_sol_price(self) -> float:
        if not hasattr(self, '_sol_price_fetched'):
            self._sol_price_fetched = True
        
        if 'current_price' in self.sol_price_cache:
            return self.sol_price_cache['current_price']
        
        try:
            await self.ensure_session()
            
            async with self.session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana", "vs_currencies": "usd"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                
                if response.status == 200:
                    data = await response.json()
                    price = data['solana']['usd']
                    self.sol_price_cache['current_price'] = price
                    return price
                else:
                    return 0.0
            
        except Exception as e:
            return 0.0
    
    async def get_transactions(self, address: str) -> Optional[List[Dict]]:
        url = f"{self.base_url}/addresses/{address}/transactions"
        
        params = {
            "api-key": self.api_key,
            "limit": 100
        }

        try:
            await self.ensure_session()
            
            async with self.session.get(
                url, 
                params=params, 
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                
                if response.status != 200:
                    return None

                return await response.json()

        except Exception:
            return None
    
    def is_swap_transaction(self, tx: Dict) -> bool:
        if not tx.get('tokenTransfers'):
            return False
        
        token_transfers = tx['tokenTransfers']

        if len(token_transfers) < 1:
            return False
        
        token_transfer = None
        
        SOL_MINT = "So11111111111111111111111111111111111111112"
        
        for transfer in token_transfers:
            mint = transfer.get('mint', '')
            
            if mint != SOL_MINT and transfer.get('tokenSymbol') != 'SOL':
                token_transfer = transfer
        
        return token_transfer is not None
    
    def parse_swap_to_report(self, tx: Dict, target_token: str, sol_price: float = None) -> Optional[Dict]:
        if not tx.get('tokenTransfers'):
            return None
        
        token_transfers = tx['tokenTransfers']

        if len(token_transfers) < 1:
            return None
        
        token_transfer = None
        sol_transfer = None
        
        SOL_MINT = "So11111111111111111111111111111111111111112"
        
        for i, transfer in enumerate(token_transfers):
            mint = transfer.get('mint', '')
            to_user = transfer.get('toUserAccount', '')
            
            if mint != SOL_MINT and transfer.get('tokenSymbol') != 'SOL':
                if to_user == target_token:
                    token_transfer = transfer
                elif not token_transfer:
                    token_transfer = transfer

            elif mint == SOL_MINT or transfer.get('tokenSymbol') == 'SOL':
                sol_transfer = transfer
        
        
        if not token_transfer:
            return None
        
        tx_type = tx.get('type', '').upper()

        if tx_type == 'WITHDRAW':
            direction = "withdraw"

        else:
            is_buy = False
            
            description = tx.get('description', '')
            
            if 'swapped' in description.lower():
                if 'SOL for' in description:
                    is_buy = True
                
                elif 'for SOL' in description:
                    is_buy = False
                else:
                    token_from = token_transfer.get('fromUserAccount', '')
                    is_buy = token_from == target_token

            else:
                token_from = token_transfer.get('fromUserAccount', '')
                is_buy = token_from == target_token

            direction = "buy" if is_buy else "sell"
        
        amount_tokens = token_transfer.get('tokenAmount', 0)
        
        source = tx.get('source', '').upper()
        
        if direction == "withdraw":
            amount_sol = 0

            for transfer in tx.get('tokenTransfers', []):
                if transfer.get('mint') == 'So11111111111111111111111111111111111111112':
                    token_amount = transfer.get('tokenAmount', 0)

                    if token_amount > 1000:
                        amount_sol = token_amount / 1_000_000_000
                    else:
                        amount_sol = token_amount

                    break
                
        elif source == 'PUMP_FUN':
            amount_sol = self.get_sol_for_pump_fun(tx, target_token, is_buy)
        else:
            amount_sol = self.get_sol_for_jupiter_raydium(tx, target_token, is_buy, sol_transfer)

        timestamp = tx.get('timestamp', 0)
        signature = tx.get('signature', '')
        
        if sol_price is None:
            sol_price = self.get_sol_price()

        amount_usd = amount_sol * sol_price
        
        readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            "timestamp": timestamp,
            "readable_time": readable_time,
            "direction": direction,
            "amount_tokens": amount_tokens,
            "amount_sol": amount_sol,
            "amount_usd": amount_usd,
            "signature": signature
        }
    
    async def get_trades(self, token_mint: str) -> List[Dict]:
        current_sol_price = await self.get_sol_price()
        
        transactions = await self.get_transactions(token_mint)
        
        if not transactions:
            return []
        
        filename = f"database/trades_{token_mint[:8]}.json"
        os.makedirs("database", exist_ok=True)
        
        if token_mint not in self.file_cache:
            existing_data = []
            if os.path.exists(filename):
                try:
                    async with aiofiles.open(filename, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        existing_data = json.loads(content)
                except Exception as e:
                    existing_data = []
            
            self.file_cache[token_mint] = existing_data

            self.existing_signatures[token_mint] = set()

            for tx in existing_data:
                sig = tx.get('signature')
                if sig:
                    self.existing_signatures[token_mint].add(sig)
        
        existing_data = self.file_cache[token_mint]
        existing_signatures = self.existing_signatures[token_mint]
        
        if transactions: 
            transactions.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        
        new_report = []
        
        for tx in transactions:
            if self.is_swap_transaction(tx):
                entry = self.parse_swap_to_report(tx, token_mint, current_sol_price)

                if entry:
                    signature = entry.get('signature')
                    if signature and signature not in existing_signatures:
                        new_report.append(entry)
                        existing_signatures.add(signature)
        
        if not new_report:
            return existing_data
        
        all_transactions = new_report + existing_data
        
        formatted_report = []
        for entry in all_transactions:
            formatted_entry = entry.copy()

            if 'amount_sol' in formatted_entry:
                amount_sol = formatted_entry['amount_sol']
                if amount_sol == 0:
                    formatted_entry['amount_sol'] = 0.0
                else:
                    formatted_entry['amount_sol'] = f"{amount_sol:.8f}".rstrip('0').rstrip('.')
                    if formatted_entry['amount_sol'] == '':
                        formatted_entry['amount_sol'] = '0.0'
                
            if 'amount_usd' in formatted_entry:
                amount_usd = formatted_entry['amount_usd']
                if amount_usd == 0:
                    formatted_entry['amount_usd'] = "0.0"
                else:
                    formatted_entry['amount_usd'] = f"{amount_usd:.2f}"

            formatted_report.append(formatted_entry)
        
        self.file_cache[token_mint] = all_transactions
        
        async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(formatted_report, indent=2, ensure_ascii=False))
        
        return all_transactions

async def main(token_mint: str = None):
    HELIUS_API_KEY = config.HELIUS_API_KEY
    
    if not HELIUS_API_KEY:
        exit(1)
    
    if token_mint is None:
        token_mint = "Go6wx5drZ2Ekz44BoQ1QNCTonc1RwNxVCoGnwqFb1zb9"
    
    async with HeliusTradesReporter(helius_api_key=HELIUS_API_KEY) as reporter:
        await reporter.get_trades(token_mint=token_mint)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        token_mint = sys.argv[1]
        asyncio.run(main(token_mint))
    else:
        asyncio.run(main())
