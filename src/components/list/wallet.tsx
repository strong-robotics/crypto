"use client";

import { WalletCell } from "@/components/cell/wallet";

interface WalletListProps {
  wallets: Array<{
    id: number;
    name: string;
    balance: number;
    entryAmount: number;
    tokenId: number;
  }>;
  entryAmounts: { [key: number]: string };
  onEntryAmountChange: (walletId: number, amount: string) => void;
}

export function WalletList({ wallets, entryAmounts, onEntryAmountChange }: WalletListProps) {

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'flex-start',
      minWidth: '230px',
      maxWidth: '230px',
      width: '230px',
      flex: '0 0 auto',
      height: 'calc(100% - 108px)',
      overflowY: 'auto',
      paddingRight: '8px',
      paddingLeft: '16px',
      paddingTop: '8px',
      paddingBottom: '108px'
    }}>
      {wallets.map((wallet) => (
        <WalletCell
          key={wallet.id}
          index={wallet.id}
          name={wallet.name}
          balance={wallet.balance}
          entryAmount={entryAmounts[wallet.id] !== undefined ? entryAmounts[wallet.id] : wallet.entryAmount.toFixed(2)}
          tokenId={wallet.tokenId}
          onEntryAmountChange={(amount) => onEntryAmountChange(wallet.id, amount)}
        />
      ))}
    </div>
  );
}
