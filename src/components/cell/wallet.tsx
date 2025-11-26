"use client";

import { useState } from "react";

interface WalletCellProps {
    index: number;
    name: string;
    balance: number;
    entryAmount: string;
    tokenId: number;
    onEntryAmountChange: (amount: string) => void;
}

export function WalletCell({
    index,
    name,
    balance,
    entryAmount,
    tokenId,
    onEntryAmountChange
}: WalletCellProps) {
    // Wallet color palette (1..5)
    const walletColors: Record<number, string> = {
        1: '#3b82f6', // blue
        2: '#a855f7', // purple
        3: '#f59e0b', // amber
        4: '#10b981', // emerald
        5: '#ef4444', // red
    };
    const bandBg = tokenId == 0 ? '#f3f4f6' : (walletColors[index] || '#3b82f6');
    const bandFg = tokenId == 0 ? '#6b7280' : '#ffffff';
    return (
        <div style={{ marginBottom: '32px' }}>
            {/* Header with wallet ID */}
            <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '4px',
                padding: '0 12px'
            }}>
                <div style={{
                    fontSize: '14px',
                    fontFamily: 'monospace',
                    color: '#4b5563'
                }}>
                    {name}
                </div>
            </div>

            {/* Main wallet card with rounded corners */}
            <div style={{
                display: 'flex',
                alignItems: 'flex-start',
                backgroundColor: 'white',
                borderRadius: '16px',
                overflow: 'hidden',
                border: '1px solid #e5e7eb',
                width: '200px',
                maxWidth: '200px'
            }}>
                {/* Index number inside white card */}
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: '40px',
                    height: '140px',
                    backgroundColor: bandBg,
                    flexShrink: 0,
                    borderRight: '1px solid #e5e7eb'
                }}>
                    <span style={{
                        fontSize: '18px',
                        fontWeight: 'bold',
                        color: bandFg
                    }}>
                        {index}
                    </span>
                </div>

                {/* Main content area */}
                <div style={{
                    flex: 1,
                    minWidth: 0
                }}>
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        height: '140px'
                    }}>
                        {/* Wallet content */}
                        <div style={{ width: '100%' }}>
                            <div style={{
                                display: 'flex',
                                flexDirection: 'column',
                                height: '100%',
                                paddingLeft: '8px',
                                paddingRight: '12px'
                            }}>
                                {/* Balance Section - Top */}
                                <div style={{ display: 'flex', flexDirection: 'column' }}>
                                    <div style={{
                                        fontSize: '10px',
                                        fontWeight: '500',
                                        color: '#6b7280',
                                        lineHeight: '0.7'
                                    }}>Balance</div>
                                    <div style={{
                                        fontSize: '18px',
                                        fontWeight: 'bold',
                                        color: '#22c55e'
                                    }}>
                                        $ {balance.toFixed(2)}
                                    </div>
                                </div>

                                <div style={{ height: '30px' }}></div>

                                {/* Entry Amount Section - Bottom */}
                                <div style={{ display: 'flex', flexDirection: 'column' }}>
                                    <div style={{
                                        fontSize: '10px',
                                        fontWeight: '500',
                                        color: '#6b7280',
                                        lineHeight: '2'
                                    }}>Entry Amount ($)</div>
                                    <input
                                        type="text"
                                        value={entryAmount}
                                        onChange={(e) => {
                                            onEntryAmountChange(e.target.value);
                                        }}
                                        style={{
                                            width: '100%',
                                            padding: '6px 8px',
                                            border: '1px solid #e5e7eb',
                                            borderRadius: '8px',
                                            fontSize: '14px',
                                            fontWeight: 'bold',
                                            color: '#111827',
                                            backgroundColor: 'white',
                                            outline: 'none',
                                            height: '32px'
                                        }}
                                        placeholder="0.00"
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
