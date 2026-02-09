"use client";
import { WalletList } from "../components/list/wallet";
import { TokenList } from "../components/list/token";
import { StatusBar } from "../components/status-bar";
import { useState, useEffect, useRef, useMemo } from "react";

type RawWalletMessage = {
  id: number;
  name: string;
  value_usd: number | string;
  entry_amount_usd?: number | string;  // User-configured entry amount from wallets table
  token_id?: number | string;  // Active token ID (if wallet has open position)
};

type RawTokenMessage = {
  id: number;
  token_address: string;
  pattern_segment_1?: string | null;
  pattern_segment_2?: string | null;
  pattern_segment_3?: string | null;
  pattern_segment_decision?: string | null;
  has_real_trading?: boolean | null;
  median_amount_usd?: number | string | null;
  pattern?: string;
  name?: string;
  pair?: string;
  mcap?: number;
  holders?: number;
  price?: number;
  stats_24h_num_buys?: number;
  stats_24h_num_sells?: number;
  history_ready?: boolean | string | number | null;
  live_seconds?: number | string | null;
  iteration_count?: number | string | null;
  // V3 Jupiter analyzer fields
  num_buys_24h?: number;
  num_sells_24h?: number;
  live_time?: string;
  // Real trading data from wallet_history and tokens table
  wallet_id?: number | null;  // ID гаманця, який тримає токен
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
};

type TokenState = {
  index: number;
  tokenId: string;
  id: number;
  scanCount: number;
  pattern: string;
  name: string;
  token_pair?: string;
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
  totalTx: number;
  buyTx: number;
  sellTx: number;
  liveSeconds?: number | null;
  live_time?: string;
  pattern_segments?: string[];
  pattern_segment_decision?: string | null;
  has_real_trading?: boolean | null;
  medianAmountUsd?: number | null;
};
export default function Home() {
  const [serverStatus, setServerStatus] = useState("Disconnected");
  const [scanStatus, setScanStatus] = useState("Unknown");
  const [scannerPaused, setScannerPaused] = useState(true);
  // History scanner UI removed per requirements
  const [wsConnected, setWsConnected] = useState(false);
  const [wsTokensConnected, setWsTokensConnected] = useState(false);
  const [wsChartConnected, setWsChartConnected] = useState(false);
  // Backend timers running flags
  const [balanceTimerRunning, setBalanceTimerRunning] = useState(false);
  const [tokensTimerRunning, setTokensTimerRunning] = useState(false);
  const [chartTimerRunning, setChartTimerRunning] = useState(false);
  const [analyzerStatus, setAnalyzerStatus] = useState("Stopped");
  const [tradesStatus, setTradesStatus] = useState("Stopped");
  const [aiEnabled, setAiEnabled] = useState(true); // ТЕСТОВИЙ РЕЖИМ: увімкнено прогнози ШІ
  const [tokenCount, setTokenCount] = useState(0); // Tokens with valid pairs and history_ready = false
  const [totalTokenCount, setTotalTokenCount] = useState(0); // Total tokens in database
  const [historyMode, setHistoryMode] = useState(false);
  const [targetReturn, setTargetReturn] = useState<number>(0.2); // TARGET_RETURN from backend config, default 0.2 (20%)
  const wsRef = useRef<WebSocket | null>(null);
  const wsTokensRef = useRef<WebSocket | null>(null);
  const wsChartRef = useRef<WebSocket | null>(null);

  // Real-time wallet data from WebSocket
  const [wallets, setWallets] = useState([
    {
      id: 0,
      name: "Loading...",
      balance: 0,
      cash: 0,
      entryAmount: 5.0,
      tokenId: 0
    }
  ]);

  // Real-time token data from WebSocket
  const [tokens, setTokens] = useState<TokenState[]>([
    {
      index: 1,
      tokenId: "Loading...",
      id: 0, // INTEGER id з БД
      scanCount: 0,
      pattern: "Unknown",
      name: "Loading...",
      token_pair: undefined, // Trading pair з DexScreener
      mcap: 0,
      holders: 0,
      buySell: 0,
      buyPrice: 0,
      currentPrice: 0,
      income: 0,
      sellPrice: 0,
      walletId: 0,
      chartData: [],
      totalTx: 0,
      buyTx: 0,
      sellTx: 0,
      liveSeconds: null,
      plan_sell_iteration: null,
      plan_sell_price_usd: null,
      pattern_segments: ["unknown", "unknown", "unknown"],
      pattern_segment_decision: null,
      medianAmountUsd: null,
    }
  ]);

  // Entry amounts state
  const [entryAmounts, setEntryAmounts] = useState<{ [key: number]: string }>({
    1: "5.0",
    2: "6.0",
    3: "5.0",
    4: "5.0",
    5: "5.0"
  });

  const handleEntryAmountChange = async (walletId: number, amount: string) => {
    // Update local state immediately for UI responsiveness (allow empty string for editing)
    setEntryAmounts(prev => ({
      ...prev,
      [walletId]: amount
    }));

    // If field is empty, don't save to server (user is still editing)
    if (amount.trim() === '') {
      return;
    }

    // Save to database via API
    try {
      const amountNum = parseFloat(amount);
      // Allow 0 (wallet disabled) but block negative numbers and NaN
      if (isNaN(amountNum) || amountNum < 0) {
        console.error(`Invalid entry amount: ${amount}`);
        return;
      }

      const API_BASE = "http://localhost:8002";
      const response = await fetch(`${API_BASE}/api/wallet/${walletId}/entry-amount`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          entry_amount_usd: amountNum
        })
      });

      if (!response.ok) {
        const error = await response.json();
        console.error(`Failed to save entry amount for wallet ${walletId}:`, error);
        // Revert local state on error
        setEntryAmounts(prev => {
          const updated = { ...prev };
          delete updated[walletId];
          return updated;
        });
      } else {
        const result = await response.json();
        console.log(`✅ Entry amount saved for wallet ${walletId}: $${amountNum}`);
      }
    } catch (error) {
      console.error(`Error saving entry amount for wallet ${walletId}:`, error);
      // Revert local state on error
      setEntryAmounts(prev => {
        const updated = { ...prev };
        delete updated[walletId];
        return updated;
      });
    }
  };

  // Sort wallets: active first, then by wallet ID asc (to align with tokens sorting by walletId)
  const sortedWallets = [...wallets].sort((a, b) => {
    const aActive = a.tokenId > 0 ? 1 : 0;
    const bActive = b.tokenId > 0 ? 1 : 0;
    if (aActive !== bActive) return bActive - aActive; // active (1) before inactive (0)
    return a.id - b.id;
  });

  // Sort tokens for display:
  // 1) In-trade tokens first, ordered by walletId asc (align with left wallets)
  // 2) Then the rest in original server order
  const activeByWallet = tokens.filter(t => (t.walletId || 0) > 0).sort((x, y) => (x.walletId || 0) - (y.walletId || 0));
  const inactive = tokens.filter(t => !t.walletId || t.walletId === 0);
  const sortedTokens = [...activeByWallet, ...inactive];

  const API_BASE = "http://localhost:8002";
  const WS_BASE = "ws://localhost:8002";

  // WebSocket connection for balance monitoring
  const connectWebSocket = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      wsRef.current = new WebSocket(`${WS_BASE}/ws/balances`);

      wsRef.current.onopen = () => {
        // console.log("🔗 Balance WebSocket connected");
        setWsConnected(true);
        setServerStatus("Connected");
      };

      wsRef.current.onmessage = (event) => {
        try {
          // console.log("📨 Balance WebSocket message received:", event.data);
          const walletData: RawWalletMessage[] = JSON.parse(event.data);
          // console.log("📊 Parsed wallet data:", walletData);

          // Конвертуємо дані з сервера в формат для WalletList
          const formattedWallets = walletData.map((wallet) => {
            const walletExt = wallet as RawWalletMessage & { cash_usd?: number | string; entry_amount_usd?: number | string; token_id?: number | string };

            // Get entry_amount_usd from server (database value) or fallback to local state or default
            const serverEntryAmount = walletExt.entry_amount_usd;
            const entryAmountValue = (() => {
              // Allow 0 (wallet disabled) - check for valid number (>= 0)
              if (serverEntryAmount !== undefined && serverEntryAmount !== null) {
                const num = typeof serverEntryAmount === "number" ? serverEntryAmount : parseFloat(String(serverEntryAmount));
                if (Number.isFinite(num) && num >= 0) {
                  return num;
                }
              }
              // Fallback to local state (if user changed but not saved yet) or default
              // If local state exists (even if empty), prefer it over server value (user is editing)
              const localAmount = entryAmounts[wallet.id];
              if (localAmount !== undefined && localAmount !== null) {
                // User has local state - use it (even if empty, user is editing)
                if (localAmount === '') {
                  // Empty string - user cleared field, return 0 for display (but keep empty in entryAmounts)
                  return 0;
                }
                const localNum = parseFloat(localAmount);
                if (Number.isFinite(localNum) && localNum >= 0) {
                  return localNum;
                }
              }
              // No local state - use server value or default
              return 5.0; // Default
            })();

            // Update local state with server value if it exists (sync on first load)
            // BUT: Don't overwrite if user is currently editing (empty string in local state)
            // Allow 0 (wallet disabled) - check for valid number (>= 0)
            if (serverEntryAmount !== undefined && serverEntryAmount !== null) {
              const serverNum = typeof serverEntryAmount === "number" ? serverEntryAmount : parseFloat(String(serverEntryAmount));
              if (Number.isFinite(serverNum) && serverNum >= 0) {
                setEntryAmounts(prev => {
                  // Don't overwrite if user is editing (empty string means user cleared the field)
                  const currentLocalValue = prev[wallet.id];
                  if (currentLocalValue === '' || currentLocalValue === undefined) {
                    // User is editing - don't overwrite with server value
                    return prev;
                  }
                  // Only update if server value is different from current local value
                  if (prev[wallet.id] !== String(serverNum)) {
                    return { ...prev, [wallet.id]: String(serverNum) };
                  }
                  return prev;
                });
              }
            }

            return {
              id: wallet.id,
              name: wallet.name, // Назва з keys.json ("bot 1", "Ksu & Rich 2" тощо)
              cash: (() => {
                return typeof walletExt.cash_usd === "number" ? walletExt.cash_usd : parseFloat(String(walletExt.cash_usd || "0"));
              })(),
              balance: ((): number => {
                const c = walletExt.cash_usd;
                if (typeof c === "number") return c;
                const parsed = parseFloat(String(c || "0"));
                return Number.isFinite(parsed) ? parsed : 0;
              })(),
              entryAmount: entryAmountValue, // Use server value from DB, or local state, or default
              tokenId: (() => {
                return walletExt.token_id ? Number(walletExt.token_id) : 0;
              })()
            };
          });

          // console.log("🔄 Formatted wallets:", formattedWallets);
          setWallets(formattedWallets);
          // console.log("✅ Updated wallets state");
        } catch (err) {
          console.error("❌ Error parsing balance WebSocket message:", err);
        }
      };

      wsRef.current.onclose = () => {
        // console.log("🔌 Balance WebSocket disconnected");
        setWsConnected(false);
        setServerStatus("Disconnected");
        // Не перепідключаємо автоматично - тільки при натисканні "Старт"
      };

      wsRef.current.onerror = (error) => {
        console.error("❌ Balance WebSocket error:", error);
        setWsConnected(false);
        setServerStatus("Disconnected");
      };
    } catch (err) {
      console.error("❌ Failed to connect Balance WebSocket:", err);
    }
  };

  // WebSocket connection for tokens
  const connectTokensWebSocket = () => {
    if (wsTokensRef.current?.readyState === WebSocket.OPEN) return;

    try {
      wsTokensRef.current = new WebSocket(`${WS_BASE}/ws/tokens`);

      wsTokensRef.current.onopen = () => {
        // console.log("🔗 Tokens WebSocket connected");
        setWsTokensConnected(true);
      };

      wsTokensRef.current.onmessage = (event) => {
        try {
          // console.log("📨 Tokens WebSocket message received:", event.data);
          const tokenData = JSON.parse(event.data) as {
            success?: boolean;
            tokens?: RawTokenMessage[];
            total_count?: number;
            total_found?: number;
            target_return?: number;  // TARGET_RETURN from backend config
          };
          // console.log("📊 Parsed token data:", tokenData);

          // Update token counts from database
          // total_found = tokens with valid pairs and history_ready = false
          // total_count = all tokens with history_ready = false
          if (tokenData.total_found !== undefined) {
            setTokenCount(tokenData.total_found);
          }
          if (tokenData.total_count !== undefined) {
            setTotalTokenCount(tokenData.total_count);
          }
          // Update target_return from backend config
          if (tokenData.target_return !== undefined && typeof tokenData.target_return === 'number') {
            setTargetReturn(tokenData.target_return);
          }

          if (tokenData.success && tokenData.tokens) {
            // Конвертуємо дані з сервера в формат для TokenList
            // ЗБЕРІГАЄМО chartData з попереднього стану, щоб не затирати графіки
            setTokens(prevTokens => {
              const byId = new Map(prevTokens.map(t => [t.id, t]));
              const formattedTokens: TokenState[] = (tokenData.tokens || []).map((token, index) => {
                const liveSecondsRaw = token.live_seconds ?? token.iteration_count;
                const liveSecondsValue = (() => {
                  if (liveSecondsRaw === null || liveSecondsRaw === undefined) return NaN;
                  if (typeof liveSecondsRaw === "number") return liveSecondsRaw;
                  const parsed = Number(liveSecondsRaw);
                  return Number.isFinite(parsed) ? parsed : NaN;
                })();
                const prev = byId.get(token.id);
                const preservedChart = prev && Array.isArray(prev.chartData) && prev.chartData.length > 0
                  ? prev.chartData
                  : [];
                const preservedForecast = prev && Array.isArray(prev.forecastData) && prev.forecastData.length > 0
                  ? prev.forecastData
                  : [];

                // ДІАГНОСТИКА для токена 1275
                if (token.id === 1275) {
                  console.log(`🧪 Full list update for token 1275:`);
                  console.log(`  - newToken (from WS): chartDataLength=${preservedChart?.length || 0}, forecastDataLength=${preservedForecast?.length || 0}`);
                  console.log(`  - existingToken (from prev state): chartDataLength=${prev?.chartData?.length || 0}, forecastDataLength=${prev?.forecastData?.length || 0}`);
                }

                const priceNum = token.price === null || token.price === undefined ? NaN : Number(token.price);
                const mcapNum = token.mcap === null || token.mcap === undefined ? NaN : Number(token.mcap);
                const holdersNum = token.holders === null || token.holders === undefined ? NaN : Number(token.holders);
                const toNumber = (value: unknown) => {
                  if (value === null || value === undefined) return NaN;
                  if (typeof value === "number") return value;
                  const parsed = Number(value);
                  return Number.isFinite(parsed) ? parsed : NaN;
                };
                // Real trading data from backend (wallet_history and tokens table)
                const entryIterNum = toNumber(token.entry_iteration);
                const exitIterNum = toNumber(token.exit_iteration);
                const curIncomeNum = toNumber(token.cur_income_price_usd);
                const curIncomeSolNum = toNumber(token.cur_income_price_sol);
                const profitNum = toNumber(token.profit_usd);
                const profitPctSolNum = toNumber(token.profit_pct_sol);
                const entrySolPriceNum = toNumber(token.entry_sol_price);
                const entryAmountNum = toNumber(token.entry_token_amount);
                const exitAmountNum = toNumber(token.exit_token_amount);
                const entryPriceNum = toNumber(token.entry_price_usd);
                const exitPriceNum = toNumber(token.exit_price_usd);
                const planSellIterNum = toNumber(token.plan_sell_iteration);
                const planSellPriceNum = toNumber(token.plan_sell_price_usd);
                const medianAmountUsdRaw = token.median_amount_usd;
                const medianAmountUsdNum = (() => {
                  if (medianAmountUsdRaw === null || medianAmountUsdRaw === undefined) return NaN;
                  if (typeof medianAmountUsdRaw === "number") return medianAmountUsdRaw;
                  const parsed = Number(medianAmountUsdRaw);
                  return Number.isFinite(parsed) ? parsed : NaN;
                })();
                const normalizeSegment = (value: unknown, fallback: string) => {
                  if (typeof value === "string" && value.length > 0) {
                    // Replace "unknown" with "-" to save space
                    return value.toLowerCase() === "unknown" ? "-" : value;
                  }
                  if (value === null || value === undefined) {
                    // Replace fallback "unknown" with "-"
                    return fallback.toLowerCase() === "unknown" ? "-" : fallback;
                  }
                  const strValue = String(value);
                  return strValue.toLowerCase() === "unknown" ? "-" : strValue;
                };
                const segment1 = normalizeSegment(token.pattern_segment_1, prev?.pattern_segments?.[0] || "-");
                const segment2 = normalizeSegment(token.pattern_segment_2, prev?.pattern_segments?.[1] || "-");
                const segment3 = normalizeSegment(token.pattern_segment_3, prev?.pattern_segments?.[2] || "-");
                const patternSegments = [segment1, segment2, segment3];
                const patternDecision = typeof token.pattern_segment_decision === "string"
                  ? token.pattern_segment_decision
                  : (prev?.pattern_segment_decision ?? null);
                const result = {
                  index: index + 1,
                  tokenId: token.token_address, // mint address для відображення в заголовку
                  id: token.id, // INTEGER id з БД для matching з charts
                  scanCount: 0, // Default scan count
                  pattern: patternSegments.join(", "),
                  name: token.name || "Unknown",
                  token_pair: token.pair || undefined, // Trading pair з DexScreener
                  mcap: Number.isFinite(mcapNum) ? mcapNum : 0,
                  holders: Number.isFinite(holdersNum) ? holdersNum : 0,
                  buySell: 0,
                  buyPrice: 0,
                  currentPrice: Number.isFinite(priceNum) ? priceNum : 0,
                  income: 0, // Буде обчислено в компоненті
                  sellPrice: 0,
                  walletId: (() => {
                    const walletIdRaw = token.wallet_id;
                    if (walletIdRaw === null || walletIdRaw === undefined) return 0;
                    const walletIdNum = Number(walletIdRaw);
                    return Number.isFinite(walletIdNum) && walletIdNum > 0 ? walletIdNum : 0;
                  })(),
                  chartData: preservedChart,
                  forecastData: preservedForecast,
                  // Використовуємо поля, які реально повертає V3 tokens_reader
                  totalTx: (token.num_buys_24h || 0) + (token.num_sells_24h || 0),
                  buyTx: token.num_buys_24h || 0,
                  sellTx: token.num_sells_24h || 0,
                  liveSeconds: Number.isFinite(liveSecondsValue)
                    ? liveSecondsValue
                    : prev?.liveSeconds ?? null,
                  live_time: token.live_time,
                  // Real trading data from wallet_history and tokens table
                  // Use only server-provided values; if null → no entry/exit -> hide reference lines
                  entry_iteration: Number.isFinite(entryIterNum) ? entryIterNum : prev?.entry_iteration ?? null,
                  exit_iteration: (() => {
                    if (Number.isFinite(exitIterNum)) return exitIterNum;
                    return prev?.exit_iteration ?? null;
                  })(),
                  cur_income_price_usd: Number.isFinite(curIncomeNum) ? curIncomeNum : prev?.cur_income_price_usd ?? null,
                  cur_income_price_sol: Number.isFinite(curIncomeSolNum) ? curIncomeSolNum : prev?.cur_income_price_sol ?? null,
                  profit_pct_sol: Number.isFinite(profitPctSolNum) ? profitPctSolNum : prev?.profit_pct_sol ?? null,
                  entry_sol_price: Number.isFinite(entrySolPriceNum) ? entrySolPriceNum : prev?.entry_sol_price ?? null,
                  profit_usd: Number.isFinite(profitNum) ? profitNum : prev?.profit_usd ?? null,
                  entry_token_amount: Number.isFinite(entryAmountNum) ? entryAmountNum : prev?.entry_token_amount ?? null,
                  exit_token_amount: Number.isFinite(exitAmountNum) ? exitAmountNum : prev?.exit_token_amount ?? null,
                  entry_price_usd: Number.isFinite(entryPriceNum) ? entryPriceNum : prev?.entry_price_usd ?? null,
                  exit_price_usd: Number.isFinite(exitPriceNum) ? exitPriceNum : prev?.exit_price_usd ?? null,
                  plan_sell_iteration: Number.isFinite(planSellIterNum) ? planSellIterNum : prev?.plan_sell_iteration ?? null,
                  plan_sell_price_usd: Number.isFinite(planSellPriceNum) ? planSellPriceNum : prev?.plan_sell_price_usd ?? null,
                  pattern_segments: patternSegments,
                  pattern_segment_decision: patternDecision,
                  has_real_trading: token.has_real_trading ?? null,
                  medianAmountUsd: Number.isFinite(medianAmountUsdNum) ? medianAmountUsdNum : (prev?.medianAmountUsd ?? null),
                };

                return result;
              });

              return formattedTokens;
            });
          }
        } catch (err) {
          console.error("❌ Error parsing tokens WebSocket message:", err);
        }
      };

      wsTokensRef.current.onclose = () => {
        // console.log("🔌 Tokens WebSocket disconnected");
        setWsTokensConnected(false);
      };

      wsTokensRef.current.onerror = (error) => {
        console.error("❌ Tokens WebSocket error:", error);
        setWsTokensConnected(false);
      };
    } catch (err) {
      console.error("❌ Failed to connect Tokens WebSocket:", err);
    }
  };

  // WebSocket connection for chart data
  const connectChartWebSocket = () => {
    if (wsChartRef.current?.readyState === WebSocket.OPEN) return;

    try {
      wsChartRef.current = new WebSocket(`${WS_BASE}/ws/chart-data`);

      wsChartRef.current.onopen = () => {
        setWsChartConnected(true);
      };

      wsChartRef.current.onmessage = (event) => {
        try {
          const rawData = JSON.parse(event.data);

          // Helper to process individual update
          const applyUpdate = (t: any, update: any) => {
            const hasChart = Array.isArray(update.chart_data);
            const hasForecast = Array.isArray(update.forecast_p50);

            const prevChart = Array.isArray(t.chartData) ? t.chartData : [];
            const prevForecast = Array.isArray(t.forecastData) ? t.forecastData : [];

            let chartChanged = false;
            if (hasChart) {
              const newChart = update.chart_data;
              chartChanged = newChart.length !== prevChart.length || newChart[newChart.length - 1] !== prevChart[prevChart.length - 1];
            }

            let forecastChanged = false;
            if (hasForecast) {
              const newForecast = update.forecast_p50;
              forecastChanged = newForecast.length !== prevForecast.length || newForecast[newForecast.length - 1] !== prevForecast[prevForecast.length - 1];
            }

            // Always update plan data fields if they are present in the update
            const planFields = {
              plan_entry_sec: update.plan_entry_sec ?? t.plan_entry_sec,
              plan_exit_sec: update.plan_exit_sec ?? t.plan_exit_sec,
              plan_decision: update.plan_decision ?? t.plan_decision,
              plan_eta_sec: update.plan_eta_sec ?? t.plan_eta_sec,
              plan_confidence: update.plan_confidence ?? t.plan_confidence,
              plan_drawdown: update.plan_drawdown ?? t.plan_drawdown,
              plan_reason: update.plan_reason ?? t.plan_reason,
              plan_prior: update.plan_prior ?? t.plan_prior,
              plan_similarity: update.plan_similarity ?? t.plan_similarity,
              plan_score: update.plan_score ?? t.plan_score,
            };

            if (!chartChanged && !forecastChanged &&
              planFields.plan_decision === t.plan_decision &&
              planFields.plan_score === t.plan_score) {
              return t;
            }

            return {
              ...t,
              ...planFields,
              chartData: chartChanged ? [...update.chart_data] : t.chartData,
              forecastData: forecastChanged ? [...update.forecast_p50] : t.forecastData,
            };
          };

          if (rawData.type === "chart_batch" && Array.isArray(rawData.data)) {
            setTokens(prevTokens => {
              const next = [...prevTokens];
              let changed = false;
              rawData.data.forEach((update: any) => {
                const idx = next.findIndex(t => t.id === update.id);
                if (idx !== -1) {
                  const updated = applyUpdate(next[idx], update);
                  if (updated !== next[idx]) {
                    next[idx] = updated;
                    changed = true;
                  }
                }
              });
              return changed ? next : prevTokens;
            });
          } else {
            // Legacy/individual update
            const update = rawData;
            if (update.id) {
              setTokens(prevTokens => {
                const idx = prevTokens.findIndex(t => t.id === update.id);
                if (idx === -1) return prevTokens;
                const updated = applyUpdate(prevTokens[idx], update);
                if (updated === prevTokens[idx]) return prevTokens;
                const next = [...prevTokens];
                next[idx] = updated;
                return next;
              });
            }
          }
        } catch (err) {
          console.error("❌ Error parsing chart WebSocket message:", err);
        }
      };

      wsChartRef.current.onclose = () => {
        // console.log("🔌 Chart WebSocket disconnected");
        setWsChartConnected(false);
      };

      wsChartRef.current.onerror = (error) => {
        console.error("❌ Chart WebSocket error:", error);
        setWsChartConnected(false);
      };
    } catch (err) {
      console.error("❌ Failed to connect Chart WebSocket:", err);
    }
  };

  // Manual toggle for Chart WS (UI Start/Stop on the right blocks)
  // Live Trades controls (right block)
  const startLiveTrades = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/live-trades/start`, { method: "POST" });
      const data = await resp.json();
      setTradesStatus(data && data.success ? "Running" : "Stopped");
    } catch (e) {
      console.error("❌ LiveTrades start error:", e);
      setTradesStatus("Stopped");
    }
  };
  const stopLiveTrades = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/live-trades/stop`, { method: "POST" });
      await resp.json();
      setTradesStatus("Stopped");
    } catch (e) {
      console.error("❌ LiveTrades stop error:", e);
    }
  };

  // Backend timers status fetcher (used in Start/Stop and on mount)
  const checkTimersStatus = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/system/timers/status`);
      const data = await resp.json();
      if (data && data.success && data.status) {
        setBalanceTimerRunning(!!data.status.balance?.is_running);
        setTokensTimerRunning(!!data.status.tokens?.auto_refresh_running);
        setChartTimerRunning(!!data.status.charts?.is_running);
        setAiEnabled(!!data.status.ai_forecast?.is_running);
        if (typeof data.status.tokens?.token_count === 'number') {
          setTokenCount(data.status.tokens.token_count);
        }
        if (typeof data.status.tokens?.total_token_count === 'number') {
          setTotalTokenCount(data.status.tokens.total_token_count);
        }
        if (typeof data.status.mode === 'string') {
          setHistoryMode(data.status.mode.toLowerCase() === 'history');
        }
      }
    } catch (e) {
      console.error("❌ Error checking timers status:", e);
    }
  };

  const checkAnalyzerStatus = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/analyzer/status`);
      const data = await response.json();
      setAnalyzerStatus(data && data.is_running ? "Running" : "Stopped");
    } catch (err) {
      console.error("❌ Error checking analyzer status:", err);
    }
  };

  const checkLiveTradesStatus = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/live-trades/status`);
      const data = await resp.json();
      setTradesStatus(data && data.is_running ? "Running" : "Stopped");
    } catch (e) {
      console.error("❌ Error checking live trades status:", e);
    }
  };

  // Big left Start/Stop — управляют всеми тремя WS
  const startAllWS = async (mode: 'live' | 'history' = 'live') => {
    // Запускаем фоновые таймеры на сервере
    try {
      await fetch(`${API_BASE}/api/system/timers/start?mode=${mode}`, { method: "POST" });
      setHistoryMode(mode === 'history');
      // Локально помечаем таймеры как запущенные (уточним реальное состояние ниже)
      setBalanceTimerRunning(true);
      setTokensTimerRunning(true);
      setChartTimerRunning(true);
    } catch (e) {
      console.error("❌ Failed to start backend timers", e);
    }
    // Подключаем WS (если еще не подключены)
    connectWebSocket();
    await new Promise(r => setTimeout(r, 200));
    connectTokensWebSocket();
    await new Promise(r => setTimeout(r, 200));
    connectChartWebSocket();
    // Синхронизируемся с бэкендом
    await checkTimersStatus();

    await checkAnalyzerStatus();
    await fetchScannerStatus();
  };
  const stopAllWS = async () => {
    // Останавливаем фоновые таймеры на сервере
    try {
      await fetch(`${API_BASE}/api/system/timers/stop`, { method: "POST" });
      setBalanceTimerRunning(false);
      setTokensTimerRunning(false);
      setChartTimerRunning(false);
    } catch (e) {
      console.error("❌ Failed to stop backend timers", e);
    }
    // WS не закрываем: соединения остаются активными, просто лупы на сервере остановлены
    setTradesStatus("Stopped");
    await checkTimersStatus();
    await checkAnalyzerStatus();
    await fetchScannerStatus();
  };
  const startLiveWS = async () => startAllWS('live');
  const startHistoryWS = async () => startAllWS('history');

  const handleAnalyzerStart = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/analyzer/start`, { method: "POST" });
      const data = await resp.json();
      setAnalyzerStatus(data && data.success ? "Running" : "Stopped");
      await fetchScannerStatus();
    } catch (e) {
      console.error("❌ Analyzer start error:", e);
      setAnalyzerStatus("Stopped");
    }
  };

  const handleAnalyzerStop = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/analyzer/stop`, { method: "POST" });
      const data = await resp.json();
      setAnalyzerStatus("Stopped");
      await fetchScannerStatus();
    } catch (e) {
      console.error("❌ Analyzer stop error:", e);
      setAnalyzerStatus("Stopped");
    }
  };

  const fetchScannerStatus = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/new-tokens/status`);
      const data = await resp.json();
      if (data && data.success) {
        const running = !!data.is_running;
        const paused = !running || !!data.scanner_paused;
        setScannerPaused(paused);
        let statusText = "Running";
        if (!running) {
          statusText = "Stopped";
        } else if (paused) {
          statusText = data.manual_skip ? "Paused (manual)" : "Paused (auto)";
        }
        setScanStatus(statusText);
      }
    } catch (e) {
      console.error("❌ Error checking scanner status:", e);
      setScanStatus("Unknown");
    }
  };

  const handleToggleScanner = async () => {
    try {
      const endpoint = scannerPaused ? `${API_BASE}/api/new-tokens/start` : `${API_BASE}/api/new-tokens/stop`;
      const resp = await fetch(endpoint, { method: "POST" });
      await resp.json();
      await fetchScannerStatus();
    } catch (e) {
      console.error("❌ Toggle new tokens scanner error:", e);
    }
  };

  // Общий кэш-баланс (без нереализованной стоимости позиций)
  const totalBalance = useMemo(() => {
    return wallets.reduce((sum, wallet) => sum + (wallet.cash || 0), 0);
  }, [wallets]);

  const inTradeCount = useMemo(() => {
    return tokens.filter(token => token.walletId && token.walletId > 0).length;
  }, [tokens]);

  // Jupiter scan counters removed: no New/Scanned counters

  // Trades UI статус не зависит от WS; управляется кнопками Start/Stop
  useEffect(() => {
    // Статус бекенд-таймеров
    checkAnalyzerStatus();
    fetchScannerStatus();
    checkTimersStatus();
    checkLiveTradesStatus();

    // WebSocket'и НЕ підключаємо автоматично - тільки через кнопку Start
    // connectWebSocket();
    // connectTokensWebSocket();
    // connectChartWebSocket();

    // НЕ закриваємо WebSocket'и в cleanup - вони мають працювати постійно
    // Cleanup спрацьовує в React Strict Mode двічі і закриває з'єднання
    // WebSocket'и будуть закриті автоматично при unmount компонента
    // eslint-disable-next-line react-hooks/exhaustive-deps -- WebSockets ініціалізуємо один раз при монтуванні
  }, []);

  return (
    <div style={{
      height: '100vh',
      backgroundColor: '#f3f4f6',
      overflow: 'hidden',
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0
    }}>
      {/* Status Bar */}
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        paddingLeft: '16px',
        paddingRight: '16px',
        paddingTop: '16px',
      }}>
        <StatusBar
          totalBalance={totalBalance}
          tokenCount={tokenCount}
          totalTokenCount={totalTokenCount}
          inTradeCount={inTradeCount}
          scannerStatus={scanStatus}
          analyzerStatus={analyzerStatus}
          tradesStatus={tradesStatus}
          wsConnected={wsConnected}
          wsTokensConnected={wsTokensConnected}
          wsChartConnected={wsChartConnected}
          balanceTimerRunning={balanceTimerRunning}
          tokensTimerRunning={tokensTimerRunning}
          chartTimerRunning={chartTimerRunning}
          onAllStartLive={startLiveWS}
          onAllStartHistory={startHistoryWS}
          onAllStop={stopAllWS}
          onAnalyzerStart={handleAnalyzerStart}
          onAnalyzerStop={handleAnalyzerStop}
          onToggleScanner={handleToggleScanner}
          scannerPaused={scannerPaused}
          onTradesStart={startLiveTrades}
          onTradesStop={stopLiveTrades}
          aiEnabled={aiEnabled}
          historyMode={historyMode}
          onAIToggle={async () => {
            try {
              if (!aiEnabled) {
                await fetch(`${API_BASE}/api/ai-forecast/start`, { method: 'POST' });
                setAiEnabled(true);
              } else {
                await fetch(`${API_BASE}/api/ai-forecast/stop`, { method: 'POST' });
                setAiEnabled(false);
              }
            } catch (e) {
              console.error('❌ AI forecast toggle error:', e);
            }
          }}
        />
      </div>

      <div style={{
        height: '1px',
        backgroundColor: '#e5e7eb',
        width: '100%'
      }}></div>

      {/* Two Column Layout */}
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        // gap: '16px',
        flexDirection: 'row',
        alignItems: 'flex-start',
        height: 'calc(100vh - 140px)',
        // paddingLeft: '16px',
        // paddingRight: '16px'
      }}>
        {/* Left Column - Wallet List */}
        <WalletList
          wallets={sortedWallets}
          entryAmounts={entryAmounts}
          onEntryAmountChange={handleEntryAmountChange}
        />

        <div style={{
          width: '1px',
          backgroundColor: '#e5e7eb',
          height: '100%'
        }}></div>

        {/* Right Column - Token List */}
        <TokenList tokens={sortedTokens} showForecast={aiEnabled} targetReturn={targetReturn} />
      </div>
    </div>
  );
}
