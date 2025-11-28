"use client";

import { TokenCell } from "@/components/cell/token";
import { useMemo } from "react";

interface TokenListProps {
  tokens: Array<{
    index: number;
    tokenId: string;
    id: number; // INTEGER id з БД
    pattern: string;
    name: string;
    token_pair?: string; // Trading pair з DexScreener
    mcap: number;
    holders: number;
    buySell: number;
    buyPrice: number;
    currentPrice: number;
    income: number;
    sellPrice: number;
    walletId: number;
    chartData: number[];
    forecastData?: number[];
    totalTx: number;
    buyTx: number;
    sellTx: number;
    liveSeconds?: number | null;
    live_time?: string; // Готовий рядок часу життя з сервера
    // Real trading data from wallet_history and tokens table
    entry_token_amount?: number | null;  // Кількість токенів при вході
    entry_price_usd?: number | null;  // Ціна входу (USD)
    entry_iteration?: number | null;  // Ітерація входу
    exit_token_amount?: number | null;  // Кількість токенів при виході
    exit_price_usd?: number | null;  // Ціна виходу (USD)
    exit_iteration?: number | null;  // Ітерація виходу
    profit_usd?: number | null;  // Прибуток (USD)
    plan_sell_iteration?: number | null;  // Запланована ітерація продажу
    plan_sell_price_usd?: number | null;  // Запланована ціна продажу
    cur_income_price_usd?: number | null;  // Поточна вартість портфеля (USD)
    pattern_segments?: string[];
    pattern_segment_decision?: string | null;
    has_real_trading?: boolean | null;  // NULL = not checked, TRUE = has SWAP, FALSE = transfer only
    medianAmountUsd?: number | null;
  }>;
  showForecast?: boolean;
}

export function TokenList({ tokens, showForecast = false }: TokenListProps) {

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      width: '100%',
      flex: '1 1 auto',
      height: 'calc(100% - 108px)',
      overflowY: 'auto',
      paddingRight: '16px',
      paddingLeft: '16px',
      paddingTop: '8px',
      paddingBottom: '108px'
    }}>
      {tokens.map((token) => (
        <TokenCell
          key={token.tokenId}
          index={token.index}
          tokenId={token.tokenId}
          id={token.id}
          walletId={token.walletId}
          pattern={token.pattern}
          patternSegments={token.pattern_segments}
          patternDecision={token.pattern_segment_decision || null}
          name={token.name}
          token_pair={token.token_pair}
          mcap={token.mcap}
          holders={token.holders}
          buySell={token.buySell}
          buyPrice={token.buyPrice}
          currentPrice={token.currentPrice}
          income={token.income}
          sellPrice={token.sellPrice}
          chartData={token.chartData}
          forecastData={token.forecastData}
          // Always hide forecast overlay (remove yellow graph)
          showForecast={false}
          totalTx={token.totalTx}
          buyTx={token.buyTx}
          sellTx={token.sellTx}
          liveSeconds={token.liveSeconds}
          live_time={token.live_time}
          entry_token_amount={token.entry_token_amount}
          entry_price_usd={token.entry_price_usd}
          entry_iteration={token.entry_iteration}
          exit_token_amount={token.exit_token_amount}
          exit_price_usd={token.exit_price_usd}
          exit_iteration={token.exit_iteration}
          profit_usd={token.profit_usd}
          plan_sell_iteration={token.plan_sell_iteration}
          plan_sell_price_usd={token.plan_sell_price_usd}
          cur_income_price_usd={token.cur_income_price_usd}
          has_real_trading={token.has_real_trading ?? null}
          medianAmountUsd={token.medianAmountUsd ?? null}
        />
      ))}
    </div>
  );
}
