"use client";

interface StatusBarProps {
  totalBalance: number;
  tokenCount: number;
  totalTokenCount: number; // Total tokens in database
  inTradeCount: number;
  scannerStatus: string;
  analyzerStatus: string;
  tradesStatus: string;
  wsConnected?: boolean;
  wsTokensConnected?: boolean;
  wsChartConnected?: boolean;
  balanceTimerRunning?: boolean;
  tokensTimerRunning?: boolean;
  chartTimerRunning?: boolean;
  onAllStart: () => void;   // Big left Start (connect all WS)
  onAllStop: () => void;    // Big left Stop (disconnect all WS)
  onAnalyzerStart: () => void;
  onAnalyzerStop: () => void;
  onTradesStart: () => void;
  onTradesStop: () => void;
  onToggleScanner?: () => void; // Stop/resume scanner
  scannerPaused?: boolean;
  aiEnabled?: boolean;
  onAIToggle?: () => void;
}

const statusColor = (status: string) => {
  const normalized = status.toLowerCase();
  if (normalized.includes("run") || normalized.includes("connect") || normalized.includes("stream") || normalized.includes("monitor")) {
    return "#10b981";
  }
  if (normalized.includes("start")) {
    return "#0ea5e9";
  }
  if (normalized.includes("stop") || normalized.includes("discon") || normalized.includes("error") || normalized.includes("fail")) {
    return "#b91c1c";
  }
  return "#6b7280";
};

const formatCurrency = (value: number) => {
  if (!Number.isFinite(value)) {
    return "$0.00";
  }
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export function StatusBar({
  totalBalance,
  tokenCount,
  totalTokenCount,
  inTradeCount,
  scannerStatus,
  analyzerStatus,
  tradesStatus,
  wsConnected = false,
  wsTokensConnected = false,
  wsChartConnected = false,
  balanceTimerRunning = false,
  tokensTimerRunning = false,
  chartTimerRunning = false,
  onAllStart,
  onAllStop,
  // onNewTokensStart,
  // onNewTokensStop,
  onAnalyzerStart,
  onAnalyzerStop,
  onTradesStart,
  onTradesStop,
  onToggleScanner,
  scannerPaused = false,
  aiEnabled = false,
  onAIToggle
}: StatusBarProps) {
  return (
    <div style={{
      backgroundColor: 'white',
      borderRadius: '16px',
      border: '1px solid #e5e7eb',
      padding: '20px',
      marginBottom: '16px',
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'space-between',
      width: '100%'
    }}>
      {/* Left Section - WS statuses + big Start/Stop */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        minWidth: '280px'
      }}>
        {/* Three mini title/value blocks */}
        <div style={{ display: 'flex', gap: '24px' }}>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>Balance WS</div>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: balanceTimerRunning ? '#10b981' : '#b91c1c' }}>{wsConnected ? (balanceTimerRunning ? 'Connected' : 'Stopped') : 'Stopped'}</div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>Tokens WS</div>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: tokensTimerRunning ? '#10b981' : '#b91c1c' }}>{wsTokensConnected ? (tokensTimerRunning ? 'Connected' : 'Stopped') : 'Stopped'}</div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>Chart WS</div>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: chartTimerRunning ? '#10b981' : '#b91c1c' }}>{wsChartConnected ? (chartTimerRunning ? 'Connected' : 'Stopped') : 'Stopped'}</div>
          </div>
        </div>

        {/* Big Start/Stop buttons for overall WS control */}
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <button onClick={onAllStart} style={{ backgroundColor: '#22c55e', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer', marginTop: '8px', marginBottom: '10px' }}>Start</button>
          <button onClick={onAllStop} style={{ backgroundColor: '#c75c5c', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer' }}>Stop</button>
        </div>
      </div>

      {/* Metrics Section */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '80px' }}>
          <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', textAlign: 'center', lineHeight: '1' }}>Balance</div>
          <div style={{ fontSize: '16px', fontWeight: 'bold', color: '#000000', textAlign: 'center' }}>{formatCurrency(totalBalance)}</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '80px' }}>
          <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', textAlign: 'center', lineHeight: '1' }}>Tokens</div>
          <div style={{ fontSize: '16px', fontWeight: 'bold', color: '#000000', textAlign: 'center' }}>{tokenCount} / {totalTokenCount}</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '60px' }}>
          <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', textAlign: 'center', lineHeight: '1' }}>In Trade</div>
          <div style={{ fontSize: '16px', fontWeight: 'bold', color: '#000000', textAlign: 'center' }}>{inTradeCount}</div>
        </div>
      </div>

      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        minWidth: '320px'
      }}>
        <div style={{ display: 'flex', gap: '24px' }}>
          {/**
           * New Tokens controls hidden: managed by unified Jupiter Scheduler.
           * Uncomment block below to restore.
           *
           * <div style={{ display: 'flex', flexDirection: 'column', minWidth: '100px' }}>
           *   <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>New Tokens</div>
           *   <div style={{ fontSize: '16px', fontWeight: 'bold', color: statusColor(scannerStatus) }}>{scannerStatus}</div>
           *   <button onClick={onNewTokensStart} style={{ backgroundColor: '#22c55e', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer', marginTop: '10px', marginBottom: '10px' }}>Start</button>
           *   <button onClick={onNewTokensStop} style={{ backgroundColor: '#c75c5c', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer' }}>Stop</button>
           * </div>
           */}
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: '100px' }}>
            <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>Analyzer</div>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: statusColor(analyzerStatus) }}>{analyzerStatus}</div>
            <button onClick={onAnalyzerStart} style={{ backgroundColor: '#22c55e', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer', marginTop: '16px', marginBottom: '10px' }}>Start</button>
            <button onClick={onAnalyzerStop} style={{ backgroundColor: '#c75c5c', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer', marginBottom: '10px' }}>Stop</button>
            {/* {onStopNewTokens && (
              <button onClick={onStopNewTokens} style={{ backgroundColor: '#f59e0b', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer' }}>Stop New Tokens</button>
            )} */}
          </div>
          {/* <div style={{ display: 'flex', flexDirection: 'column', minWidth: '100px' }}>
            <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>Trades</div>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: statusColor(tradesStatus) }}>{tradesStatus}</div>
            <button onClick={onTradesStart} style={{ backgroundColor: '#22c55e', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer', marginTop: '16px', marginBottom: '10px' }}>Start</button>
            <button onClick={onTradesStop} style={{ backgroundColor: '#c75c5c', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer' }}>Stop</button>
          </div> */}
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: '120px' }}>
            <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>AI Forecast</div>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: aiEnabled ? '#10b981' : '#6b7280' }}>{aiEnabled ? 'On' : 'Off'}</div>
            <button onClick={onAIToggle} style={{ backgroundColor: aiEnabled ? '#f59e0b' : '#9ca3af', color: 'white', border: 'none', borderRadius: '12px', padding: '7px 10px', fontSize: '14px', fontWeight: 'bold', cursor: 'pointer', marginTop: '16px', marginBottom: '10px' }}>{aiEnabled ? 'Hide' : 'Show'}</button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: '140px' }}>
            <div style={{ fontSize: '10px', fontWeight: '500', color: '#6b7280', lineHeight: '1' }}>New Tokens</div>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: scannerPaused ? '#b91c1c' : '#10b981' }}>{scannerStatus}</div>
            {onToggleScanner && (
              <button
                onClick={onToggleScanner}
                style={{
                  backgroundColor: scannerPaused ? '#22c55e' : '#f97316',
                  color: 'white',
                  border: 'none',
                  borderRadius: '12px',
                  padding: '7px 10px',
                  fontSize: '14px',
                  fontWeight: 'bold',
                  cursor: 'pointer',
                  marginTop: '16px',
                  marginBottom: '10px'
                }}
              >
                {scannerPaused ? 'Start' : 'Stop'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
