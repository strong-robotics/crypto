"use client";

import React from "react";
import { AreaChartComponent, type AreaChartComponentProps } from "../charts/area-chart";

type MarkerInput = {
  value: number;
  color?: string;
};

const DEFAULT_HISTORY_MARKERS: MarkerInput[] = [
  { value: 250, color: '#94a3b8' }, // окно 1 → окно 2 граница
  { value: 670, color: '#ef4444' }, // коридор A start
  { value: 730, color: '#22c55e' }, // коридор A end
  { value: 940, color: '#ef4444' }, // коридор B start
  { value: 1000, color: '#22c55e' }, // коридор B end
];

const parseMarkerValue = (part: string): MarkerInput | null => {
  if (!part) return null;
  const [valueRaw, colorRaw] = part.split(':').map(chunk => chunk.trim());
  const valueNum = Number(valueRaw);
  if (!Number.isFinite(valueNum) || valueNum <= 0) {
    return null;
  }
  return {
    value: valueNum,
    color: colorRaw || undefined,
  };
};

const HISTORY_MARKERS: MarkerInput[] = (() => {
  const envRaw = process.env.NEXT_PUBLIC_HISTORY_MARKERS;
  if (!envRaw || typeof envRaw !== 'string') {
    return DEFAULT_HISTORY_MARKERS;
  }
  const parsed = envRaw
    .split(',')
    .map(part => parseMarkerValue(part))
    .filter((marker): marker is MarkerInput => !!marker);
  return parsed.length > 0 ? parsed : DEFAULT_HISTORY_MARKERS;
})();

interface TokenCellProps {
  index: number;
  tokenId: string;
  id: number; // INTEGER id з БД для matching з charts
  walletId: number; // active wallet id bound to token (for color coding)
  pattern: string;
  patternSegments?: string[];
  patternDecision?: string | null;
  name: string;
  token_pair?: string; // опціональний, з'являється після DexScreener аналізу
  mcap: number;
  holders: number;
  buySell: number;
  buyPrice: number;
  currentPrice: number;
  income: number;
  sellPrice: number;
  chartData?: number[];
  forecastData?: number[];
  showForecast?: boolean;
  totalTx: number; // total transactions (24h)
  buyTx: number;   // buy transactions (24h)
  sellTx: number;  // sell transactions (24h)
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
  cur_income_price_sol?: number | null;  // Поточна вартість портфеля (SOL)
  profit_pct_sol?: number | null;  // Відсоток приросту портфеля в SOL
  entry_sol_price?: number | null;  // Курс SOL при покупці
  has_real_trading?: boolean | null;  // NULL = not checked, TRUE = has SWAP, FALSE = transfer only
  medianAmountUsd?: number | null; // Медіанна сума угоди (USD)
  targetReturn?: number;  // TARGET_RETURN from backend config
}

function arePropsEqual(prevProps: TokenCellProps, nextProps: TokenCellProps) {
  return (
    prevProps.id === nextProps.id &&
    prevProps.walletId === nextProps.walletId &&
    prevProps.currentPrice === nextProps.currentPrice &&
    prevProps.income === nextProps.income &&
    prevProps.mcap === nextProps.mcap &&
    prevProps.holders === nextProps.holders &&
    prevProps.buySell === nextProps.buySell &&
    prevProps.liveSeconds === nextProps.liveSeconds &&
    prevProps.patternDecision === nextProps.patternDecision &&
    prevProps.cur_income_price_usd === nextProps.cur_income_price_usd &&
    prevProps.profit_usd === nextProps.profit_usd &&
    prevProps.profit_pct_sol === nextProps.profit_pct_sol &&
    JSON.stringify(prevProps.chartData) === JSON.stringify(nextProps.chartData) &&
    JSON.stringify(prevProps.forecastData) === JSON.stringify(nextProps.forecastData)
  );
}

export const TokenCell = React.memo(function TokenCell({
  index,
  tokenId,
  id,
  walletId,
  pattern,
  patternSegments,
  patternDecision,
  name,
  token_pair,
  mcap,
  holders,
  buySell,
  buyPrice,
  currentPrice,
  income,
  sellPrice,
  chartData,
  forecastData,
  showForecast,
  totalTx,
  buyTx,
  sellTx,
  liveSeconds,
  live_time,
  entry_token_amount,
  entry_price_usd,
  entry_iteration,
  exit_token_amount,
  exit_price_usd,
  exit_iteration,
  profit_usd,
  plan_sell_iteration,
  plan_sell_price_usd,
  cur_income_price_usd,
  cur_income_price_sol,
  profit_pct_sol,
  entry_sol_price,
  has_real_trading,
  medianAmountUsd,
  targetReturn = 0.2  // Default to 0.2 (20%) if not provided, but should come from backend config
}: TokenCellProps) {
  // Всі значення приходять з бекенду
  const ENTRY_SEC: number | null = (typeof entry_iteration === 'number') ? entry_iteration : null;
  const isArchived = typeof live_time === 'string' && live_time.toLowerCase().includes('ended');
  const historyIterationMarkers = isArchived ? HISTORY_MARKERS : [];
  // Format pattern segments: replace "unknown" with "-" and filter out duplicates
  const formatPatternSegments = (segments: string[] | undefined, fallback: string) => {
    if (!segments || segments.length === 0) {
      return fallback === "unknown" ? "-" : fallback;
    }
    // Replace "unknown" with "-" and join
    const formatted = segments.map(s => s.toLowerCase() === "unknown" ? "-" : s);
    // If all are "-", show just "-"
    if (formatted.every(s => s === "-")) {
      return "-";
    }
    // Filter out "-" and join with ", "
    const filtered = formatted.filter(s => s !== "-");
    return filtered.length > 0 ? filtered.join(", ") : "-";
  };
  const segmentDisplay = formatPatternSegments(patternSegments, pattern);
  const normalizedDecision = (patternDecision || 'not').toLowerCase();
  const decisionColor = normalizedDecision === 'buy' ? '#10b981' : '#ef4444';

  // DEBUG для токена з ID = 9
  // if (tokenId === "FPGEiSDwEXcjMpvzhvicHpueNJ225F6DPhZrCRwXpump" || 
  //     tokenId === "8En9ZeLoMwKaHJY68TjMGmqFmoBPSD1xZaQ1VS6dm2R5") {
  //   console.log("🔍 DEBUG TOKEN CELL ID = 9:");
  //   console.log("  - tokenId:", tokenId);
  //   console.log("  - name:", name);
  //   console.log("  - chartData length:", chartData ? chartData.length : 0);
  //   console.log("  - chartData first 10:", chartData ? chartData.slice(0, 10) : []);
  //   console.log("  - chartData last 10:", chartData ? chartData.slice(-10) : []);
  //   console.log("  - chartData min:", chartData && chartData.length > 0 ? Math.min(...chartData) : 'N/A');
  //   console.log("  - chartData max:", chartData && chartData.length > 0 ? Math.max(...chartData) : 'N/A');
  //   console.log("  - chartData all values:", chartData);
  // }

  // Get current portfolio value from backend (all calculations on server)
  const chartSeconds = (Array.isArray(chartData) && chartData.length > 0)
    ? chartData.length
    : null;
  const liveSecondsNumeric =
    typeof liveSeconds === 'number' && Number.isFinite(liveSeconds)
      ? liveSeconds
      : null;
  const statusSeconds =
    liveSecondsNumeric !== null
      ? liveSecondsNumeric
      : chartSeconds;
  const statusLabel =
    typeof live_time === 'string' && live_time.length > 0
      ? live_time
      : (statusSeconds !== null ? `${statusSeconds}s` : '0s');
  const statusColor = isArchived ? '#6b7280' : '#10b981';

  const latestPriceFromChart = (Array.isArray(chartData) && chartData.length > 0)
    ? chartData[chartData.length - 1]
    : null;
  const spendAmountUsd = (typeof entry_token_amount === 'number' && typeof entry_price_usd === 'number')
    ? entry_token_amount * entry_price_usd
    : (typeof entry_token_amount === 'number' && typeof buyPrice === 'number')
      ? entry_token_amount * buyPrice
      : null;
  const investedUsd: number | null = spendAmountUsd ?? null;
  const fallbackCurrentValue =
    (typeof entry_token_amount === 'number' && Number.isFinite(currentPrice))
      ? entry_token_amount * (currentPrice as number)
      : (typeof entry_token_amount === 'number' && Number.isFinite(latestPriceFromChart))
        ? entry_token_amount * (latestPriceFromChart as number)
        : null;
  const currentPortfolioValueCandidate =
    typeof cur_income_price_usd === 'number' && Number.isFinite(cur_income_price_usd)
      ? cur_income_price_usd
      : fallbackCurrentValue;
  const currentPortfolioValue =
    typeof currentPortfolioValueCandidate === 'number' && Number.isFinite(currentPortfolioValueCandidate)
      ? currentPortfolioValueCandidate
      : (investedUsd !== null ? investedUsd : 0);

  const normalizedMedianAmountUsd =
    typeof medianAmountUsd === 'number' && Number.isFinite(medianAmountUsd)
      ? medianAmountUsd
      : null;

  const formatUsdAmount = (value: number) => {
    if (value >= 1000) {
      return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    }
    if (value >= 1) {
      return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    }
    if (value > 0) {
      return `$${value.toPrecision(2)}`;
    }
    return `$0`;
  };

  const computedProfitCandidate =
    typeof profit_usd === 'number' && Number.isFinite(profit_usd)
      ? profit_usd
      : (investedUsd !== null && typeof currentPortfolioValue === 'number'
        ? currentPortfolioValue - investedUsd
        : null);
  const currentProfitUsd =
    typeof computedProfitCandidate === 'number' && Number.isFinite(computedProfitCandidate)
      ? computedProfitCandidate
      : null;

  // Determine circle color based on profit/loss from backend
  // Green: profit (virtual_income > 0)
  // Red: loss (virtual_income < 0)
  // Gray: no data or zero profit
  let circleColor = '#9ca3af'; // Default gray
  if (typeof currentProfitUsd === 'number' && currentProfitUsd !== null) {
    if (currentProfitUsd > 0) {
      circleColor = '#22c55e'; // Green - profit
    } else if (currentProfitUsd < 0) {
      circleColor = '#ef4444'; // Red - loss
    } else {
      circleColor = '#9ca3af'; // Gray - break even
    }
  }

  const exitActualSec: number | null = (typeof exit_iteration === 'number') ? exit_iteration : null;
  const exitPlanSec: number | null = (typeof plan_sell_iteration === 'number') ? plan_sell_iteration : null;
  const chartExitSec: number | null = exitActualSec ?? exitPlanSec;

  // Calculate exit price per token: use actual exit price, or plan_sell_price_usd, or calculate from entry_price_usd with targetReturn from config
  const exitPricePerToken =
    exitActualSec !== null && typeof exit_price_usd === 'number'
      ? exit_price_usd
      : (typeof plan_sell_price_usd === 'number'
        ? plan_sell_price_usd
        : (typeof entry_price_usd === 'number' && entry_price_usd > 0
          ? entry_price_usd * (1.0 + targetReturn)
          : null));

  const exitTokens: number | null = exitActualSec !== null
    ? (typeof exit_token_amount === 'number'
      ? exit_token_amount
      : (typeof entry_token_amount === 'number' ? entry_token_amount : null))
    : (typeof entry_token_amount === 'number' ? entry_token_amount : null);

  // Calculate exit value: use exitTokens * exitPricePerToken, or fallback to entry_amount * (1 + targetReturn from config)
  const exitValueUsd =
    exitTokens !== null && exitPricePerToken !== null
      ? exitTokens * exitPricePerToken
      : (investedUsd !== null && investedUsd > 0
        ? investedUsd * (1.0 + targetReturn)
        : null);
  const exitProfitUsd =
    exitValueUsd !== null && investedUsd !== null
      ? exitValueUsd - investedUsd
      : null;
  const exitLabelSuffix = '';
  const exitIterationDisplay = exitActualSec ?? exitPlanSec;
  const exitTokenAmountDisplay = exitActualSec !== null
    ? (typeof exit_token_amount === 'number' ? exit_token_amount : null)
    : (typeof entry_token_amount === 'number' ? entry_token_amount : null);

  const forceSell = async () => {
    try {
      const resp = await fetch(`http://localhost:8002/api/sell/force?token_id=${id}`, { method: 'POST' });
      const data = await resp.json();
      // Optional: simple feedback in console; WS will refresh UI
      // console.log('[FORCE SELL]', data);
    } catch (e) {
      // console.error('Force sell error', e);
    }
  };

  // Конвертуємо секунди в хвилини:секунди для кращого відображення
  const formatExitTime = (seconds: number | null) => {
    if (seconds === null) return '—';
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}m ${remainingSeconds}s`;
  };

  // Логування для налагодження
  if (name === 'Meteora' || name === '华莱士🍔') {
    console.log(`🔍 DEBUG Token ${name}:`, {
      currentPrice,
      buyPrice,
      currentPortfolioValue,
      profit_usd,
      exitActualSec,
      exitPlanSec,
      exit_iteration,
      forecastDataLength: forecastData?.length || 0
    });
  }

  // Price formatter: full precision for small values
  const formatPrice = (v: number) => {
    if (!Number.isFinite(v)) return '$0';
    if (v >= 1) return `$${v.toFixed(2)}`;
    // For 0 < v < 1 — up to 10 decimals, trim trailing zeros
    const s = v.toFixed(10);
    return `$${s.replace(/\.?(0+)$/, '')}`;
  };
  const medianDisplay = normalizedMedianAmountUsd !== null ? ` (${formatUsdAmount(normalizedMedianAmountUsd)})` : "";

  // Wallet color palette (1..5)
  const walletColors: Record<number, string> = {
    1: '#3b82f6', // blue
    2: '#a855f7', // purple
    3: '#f59e0b', // amber
    4: '#10b981', // emerald
    5: '#ef4444', // red
  };
  const leftBarBg = (walletId && walletId > 0) ? (walletColors[walletId] || '#3b82f6') : '#f3f4f6';
  const leftBarFg = (walletId && walletId > 0) ? '#ffffff' : '#6b7280';
  return (
    <div style={{ marginBottom: '32px' }}>
      {/* Header with token ID and scan count */}
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
          color: '#4b5563',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          maxWidth: '100%',
          display: 'flex',
          gap: '4px'
        }}>
          {/* Token ID */}
          <span style={{ fontWeight: '500', color: '#6b7280' }}>ID:</span>
          <span style={{ fontWeight: 'bold' }}>{id}</span>

          {/* Status (Live/Ended) */}
          <span style={{ fontWeight: '500', marginLeft: '8px', color: '#6b7280' }}>Status:</span>
          <span style={{ color: statusColor, fontWeight: 'bold' }}>{statusLabel}</span>

          {/* Pair — всегда отображаем, чтобы видеть отсутствие */}
          <span style={{ fontWeight: '500', marginLeft: '8px', color: '#6b7280' }}>Pair:</span>
          <span style={{ fontWeight: 'bold' }}>{token_pair && token_pair !== tokenId ? token_pair : '—'}</span>

          {/* Holders у заголовку */}
          <span style={{ fontWeight: '500', marginLeft: '8px', color: '#6b7280' }}>Holders:</span>
          <span style={{ fontWeight: 'bold' }}>{holders}</span>

          {/* Pattern (служебно) */}
          <span style={{ fontWeight: '500', marginLeft: '8px', color: '#6b7280' }}>Pattern:</span>
          <span style={{ fontWeight: 'bold' }}>{segmentDisplay || 'Unknown'}</span>
          <span style={{ fontWeight: '500', marginLeft: '8px', color: '#6b7280' }}>Decision:</span>
          <span style={{ fontWeight: 'bold', color: decisionColor }}>{normalizedDecision.toUpperCase()}</span>
        </div>
      </div>

      {/* Main token card with rounded corners */}
      <div style={{
        display: 'flex',
        alignItems: 'flex-start',
        backgroundColor: 'white',
        borderRadius: '16px',
        overflow: 'hidden',
        border: '1px solid #e5e7eb'
      }}>
        {/* Index number inside white card; colored by active wallet if any */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '40px',
          height: '140px',
          backgroundColor: leftBarBg,
          flexShrink: 0,
          borderRight: '1px solid #e5e7eb'
        }}>
          <span style={{
            fontSize: '18px',
            fontWeight: 'bold',
            color: leftBarFg
          }}>
            {index}
          </span>
        </div>

        {/* Main content area */}
        <div style={{
          flex: 1,
          minWidth: 0
        }}>
          {/* Three-part layout: 25% - 50% - 25% */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            height: '140px'
          }}>

            {/* Left Section (25%) - Token Information */}
            <div style={{ width: '25%', padding: '0 16px' }}>
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', flexDirection: 'column', marginTop: '4px', marginBottom: '4px' }}>
                  <div style={{
                    fontSize: '10px',
                    fontWeight: '500',
                    color: '#6b7280',
                    lineHeight: '0.7'
                  }}>Name</div>
                  <div style={{
                    fontSize: '14px',
                    fontWeight: 'bold',
                    color: '#111827',
                    whiteSpace: 'nowrap',
                    width: '100%',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    display: '-webkit-box',
                    WebkitLineClamp: 1,
                    WebkitBoxOrient: 'vertical',
                  }}>{name || "Unknown"}</div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', marginBottom: '4px' }}>
                  <div style={{
                    fontSize: '10px',
                    fontWeight: '500',
                    color: '#6b7280',
                    lineHeight: '0.7'
                  }}>M.Cap</div>
                  <div style={{
                    fontSize: '14px',
                    fontWeight: 'bold',
                    color: '#111827',
                    whiteSpace: 'nowrap',
                    width: '100%',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    display: '-webkit-box',
                    WebkitLineClamp: 1,
                    WebkitBoxOrient: 'vertical',
                  }}>${mcap.toLocaleString()}</div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', marginBottom: '4px' }}>
                  <div style={{
                    fontSize: '10px',
                    fontWeight: '500',
                    color: '#6b7280',
                    lineHeight: '0.7'
                  }}>Transactions (Buy / Sell)</div>
                  <div style={{
                    fontSize: '14px',
                    fontWeight: 'bold',
                    color: '#111827',
                    whiteSpace: 'nowrap',
                    width: '100%',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    display: '-webkit-box',
                    WebkitLineClamp: 1,
                    WebkitBoxOrient: 'vertical',
                  }}>{`${totalTx} (${buyTx} / ${sellTx})`}</div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  <div style={{
                    fontSize: '10px',
                    fontWeight: '500',
                    color: '#6b7280',
                    lineHeight: '0.7'
                  }}>Price</div>
                  <div style={{
                    fontSize: '14px',
                    fontWeight: 'bold',
                    color: '#111827',
                    whiteSpace: 'nowrap',
                    width: '100%',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    display: '-webkit-box',
                    WebkitLineClamp: 1,
                    WebkitBoxOrient: 'vertical',
                  }}>{formatPrice(currentPrice)}{medianDisplay}</div>
                </div>
              </div>
            </div>

            {/* Middle Section (50%) - Chart area */}
            <div style={{
              width: '52%',
              height: '100%',
              marginTop: '13px',
            }}>
              <div style={{
                height: '124px',
                width: '100%',
                backgroundColor: 'white',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'flex-start',
                border: '1px solid #e5e7eb',
                overflowX: 'auto',
                overflowY: 'hidden',
                // marginTop: '8px',
                // paddingBottom: '8px',
              }}>
                <AreaChartComponent
                  timer={100000}
                  height={124}
                  chartData={chartData}
                  forecastData={[]}
                  showForecast={false}
                  entrySec={typeof entry_iteration === 'number' && entry_iteration >= 0 ? entry_iteration : null}
                  exitSec={typeof entry_iteration === 'number' && entry_iteration >= 0 ? chartExitSec : null}
                  historyMarkers={historyIterationMarkers}
                  hasRealTrading={has_real_trading ?? null}
                  medianAmountUsd={normalizedMedianAmountUsd}
                />
              </div>
            </div>

            {/* Right Section (25%) - Price Information */}
            <div style={{ width: '23%', padding: '8px' }}>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '0'
              }}>
                <div style={{ textAlign: 'center', flex: '1', minWidth: '60px' }}>
                  <div style={{
                    fontSize: '10px',
                    fontWeight: '500',
                    color: '#6b7280',
                  }}>Bought</div>
                  <div style={{
                    fontSize: '14px',
                    fontWeight: 'bold',
                    color: '#111827'
                  }}>
                    {investedUsd !== null ? `$ ${investedUsd.toFixed(2)}` : '—'}
                  </div>
                  {entry_token_amount != null && (
                    <div style={{ fontSize: '10px', color: '#6b7280' }}>
                      {entry_token_amount.toLocaleString(undefined, { maximumFractionDigits: 2 })} tokens
                    </div>
                  )}
                  <div style={{ fontSize: '10px', color: '#6b7280' }}>Entry: {ENTRY_SEC === null ? '—' : `${ENTRY_SEC}s`}</div>
                </div>

                <div style={{ textAlign: 'center', position: 'relative', flex: '0 0 80px' }}>
                  <div style={{
                    position: 'relative',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: '80px',
                    height: '80px',
                    borderRadius: '50%',
                    border: `2px solid ${circleColor}`,
                    backgroundColor: 'transparent'
                  }}>
                    <div style={{
                      fontSize: '10px',
                      fontWeight: '500',
                      color: '#6b7280',
                    }}>Current</div>
                    <span style={{
                      fontSize: '14px',
                      fontWeight: 'bold',
                      color: `${circleColor}`
                    }}>
                      $ {currentPortfolioValue.toFixed(2)}
                    </span>
                    {investedUsd !== null && investedUsd > 0 && typeof currentPortfolioValue === 'number' && (
                      <div style={{
                        fontSize: '10px',
                        color: circleColor,
                        marginTop: '2px',
                        fontWeight: '500'
                      }}>
                        {typeof profit_pct_sol === 'number' && Number.isFinite(profit_pct_sol) ? (
                          // Show SOL-based profit percentage (more accurate for SOL accumulation goal)
                          <span>{profit_pct_sol >= 0 ? '+' : ''}{profit_pct_sol.toFixed(1)}% SOL</span>
                        ) : (
                          // Fallback to USD-based calculation
                          <span>{((currentPortfolioValue / investedUsd - 1) * 100) >= 0 ? '+' : ''}{((currentPortfolioValue / investedUsd - 1) * 100).toFixed(1)}%</span>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <div style={{ textAlign: 'center', flex: '1', minWidth: '60px' }}>
                  <div style={{
                    fontSize: '10px',
                    fontWeight: '500',
                    color: '#6b7280',
                  }}>{exitActualSec !== null ? 'Sold' : 'Aim Sell'}</div>
                  <div style={{
                    fontSize: '14px',
                    fontWeight: 'bold',
                    color: '#111827'
                  }}>
                    {exitValueUsd !== null ? `$ ${exitValueUsd.toFixed(2)}` : '—'}
                  </div>
                  {/* {exitProfitUsd !== null && (
                    <div style={{ fontSize: '10px', color: exitProfitUsd >= 0 ? '#22c55e' : '#ef4444' }}>
                      {exitProfitUsd >= 0 ? '+' : '-'}${Math.abs(exitProfitUsd).toFixed(2)}
                    </div>
                  )} */}
                  {/* {exitTokenAmountDisplay != null && (
                    <div style={{ fontSize: '10px', color: '#6b7280' }}>
                      {exitTokenAmountDisplay.toLocaleString(undefined, { maximumFractionDigits: 2 })} tokens
                    </div>
                  )} */}
                  <div style={{ fontSize: '10px', color: '#6b7280' }}>
                    Exit: {formatExitTime(exitIterationDisplay)}{exitLabelSuffix}
                  </div>
                  {exitActualSec === null && exitPlanSec !== null && exitPricePerToken !== null && (
                    <div style={{ fontSize: '10px', color: '#6b7280' }}>
                      (target price ${exitPricePerToken.toFixed(6)})
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Control Buttons: Force Buy + Force Sell */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '60px',
          height: '140px',
          backgroundColor: 'transparent',
          flexShrink: 0,
          borderLeft: '1px solid #e5e7eb',
          marginLeft: '8px'
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <button onClick={async () => {
              try {
                await fetch(`http://localhost:8002/api/buy/force?token_id=${id}`, { method: 'POST' });
              } catch (e) { }
            }} title="Buy now" style={{
              fontSize: '14px',
              fontWeight: 'bold',
              color: '#10b981',
              background: 'transparent',
              border: '1px solid #10b981',
              borderRadius: '6px',
              padding: '2px 6px',
              cursor: 'pointer'
            }}>B</button>
            <button onClick={forceSell} title="Sell now" style={{
              fontSize: '14px',
              fontWeight: 'bold',
              color: '#ef4444',
              background: 'transparent',
              border: '1px solid #ef4444',
              borderRadius: '6px',
              padding: '2px 6px',
              cursor: 'pointer'
            }}>X</button>
          </div>
        </div>
      </div>
    </div >
  );
}, arePropsEqual);
