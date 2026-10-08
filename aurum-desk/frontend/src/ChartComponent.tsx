import { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { api, getNextRequestGeneration, isLatestGeneration } from './api/client';
import { RiskRewardPrimitive } from './plugins/RiskRewardPrimitive';
import type { RiskRewardData } from './plugins/RiskRewardPrimitive';
import { RefreshCw, AlertCircle, Eye, EyeOff, Crosshair, ArrowUpRight, ArrowDownRight, Layers } from 'lucide-react';

export interface ChartComponentProps {
  symbol: string;
  timeframe: string;
  activeOverlay?: RiskRewardData | null;
  onOverlayChange?: (data: RiskRewardData) => void;
  onFocusTrade?: (trade: any) => void;
  smcLevels?: {
    swingHigh?: number;
    swingLow?: number;
    equilibrium?: number;
  };
}

interface CandleData {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
}

export function ChartComponent({
  symbol,
  timeframe,
  activeOverlay,
  onOverlayChange,
  smcLevels
}: ChartComponentProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const rrPrimitiveRef = useRef<RiskRewardPrimitive | null>(null);

  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [lastDataAt, setLastDataAt] = useState<string | null>(null);
  const [dataIsStale, setDataIsStale] = useState(false);
  const [showOverlay, setShowOverlay] = useState(true);
  const [showSMCLevels, setShowSMCLevels] = useState(true);
  const [draftMode, setDraftMode] = useState(false);
  const [draftDirection, setDraftDirection] = useState<'LONG' | 'SHORT'>('LONG');


  const abortControllerRef = useRef<AbortController | null>(null);
  const currentGenRef = useRef<number>(0);

  // 1. Initialize Chart instance with React 19 / StrictMode safety
  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Create chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#121214' },
        textColor: '#9ca3af',
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
      },
      crosshair: {
        vertLine: { color: '#6366f1', width: 1, style: 2 },
        horzLine: { color: '#6366f1', width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: 'rgba(255, 255, 255, 0.1)',
        autoScale: true,
      },
      timeScale: {
        borderColor: 'rgba(255, 255, 255, 0.1)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 12,
      },
      width: chartContainerRef.current.clientWidth,
      height: 560,
    });

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;

    // ResizeObserver for dynamic container sizing
    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0 || !chartRef.current) return;
      const { width, height } = entries[0].contentRect;
      chartRef.current.applyOptions({ width: Math.max(100, width), height: Math.max(300, height) });
    });

    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      if (rrPrimitiveRef.current && seriesRef.current) {
        try {
          seriesRef.current.detachPrimitive(rrPrimitiveRef.current);
        } catch {
          // Ignore detach errors on unmount
        }
        rrPrimitiveRef.current = null;
      }
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // 2. Fetch Candle Data with generation check and AbortController
  const loadCandles = useCallback(async (isBackgroundSync = false) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const reqGen = getNextRequestGeneration();
    currentGenRef.current = reqGen;

    if (!isBackgroundSync) {
      setLoading(true);
      setErrorMessage(null);
    }

    try {
      // Background sync from Bitget if not already synced recently
      try {
        await api.syncCandles(symbol, timeframe, 120);
      } catch (syncErr: any) {
        console.warn('Sync attempt warning:', syncErr?.message || syncErr);
      }

      if (!isLatestGeneration(reqGen)) return;

      // Read validated candles from local backend
      const res = await api.getCandles(symbol, timeframe, 150, { signal: controller.signal });

      if (!isLatestGeneration(reqGen)) return;

      if (res && Array.isArray(res.candles) && seriesRef.current) {
        // Map and deduplicate by timestamp
        const seenTimes = new Set<number>();
        const formatted: CandleData[] = [];

        for (const c of res.candles) {
          const sec = Math.floor(c.timestamp / 1000) as unknown as number;
          if (!seenTimes.has(sec)) {
            seenTimes.add(sec);
            formatted.push({
              time: sec as Time,
              open: c.open,
              high: c.high,
              low: c.low,
              close: c.close,
            });
          }
        }

        formatted.sort((a, b) => (a.time as number) - (b.time as number));

        if (formatted.length > 0) {
          if (!isBackgroundSync) {
            seriesRef.current.setData(formatted);
          } else {
            // Incremental update of the latest bar
            const latest = formatted[formatted.length - 1];
            seriesRef.current.update(latest);
          }

          const latestDate = new Date(res.last_candle_time || Date.now());
          setLastDataAt(latestDate.toLocaleTimeString('vi-VN'));
          setDataIsStale(Boolean(res.is_stale));
        } else {
          setErrorMessage('Chưa có dữ liệu nến cho khung thời gian này.');
        }
      }
    } catch (err: any) {
      if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') {
        return; // Request was cleanly cancelled by timeframe switch
      }
      if (isLatestGeneration(reqGen)) {
        setErrorMessage(
          err.response?.data?.detail || err.message || 'Lỗi kết nối khi tải nến từ backend.'
        );
      }
    } finally {
      if (isLatestGeneration(reqGen)) {
        setLoading(false);
      }
    }
  }, [symbol, timeframe]);

  // Trigger load on symbol/timeframe changes
  useEffect(() => {
    loadCandles(false);
  }, [loadCandles]);

  // Realtime Polling Fallback (every 8 seconds)
  useEffect(() => {
    const timer = setInterval(() => {
      loadCandles(true);
    }, 8000);
    return () => clearInterval(timer);
  }, [loadCandles]);

  // 3. Attach / Update RiskRewardPrimitive Overlay
  useEffect(() => {
    if (!seriesRef.current) return;

    const targetOverlay = draftMode
      ? {
          id: 'draft-trade',
          direction: draftDirection,
          state: 'draft' as const,
          plannedEntry: smcLevels?.equilibrium || 4128.0,
          stopLoss: draftDirection === 'LONG' ? (smcLevels?.swingLow || 4118.0) : (smcLevels?.swingHigh || 4138.0),
          takeProfit: draftDirection === 'LONG' ? (smcLevels?.swingHigh || 4148.0) : (smcLevels?.swingLow || 4108.0),
          quantity: 0.1,
          initialRiskUsdt: 5.0,
          riskPct: 0.5,
          grossRR: 2.0,
          estimatedNetRR: 1.9,
        }
      : activeOverlay;

    if (targetOverlay && showOverlay) {
      if (!rrPrimitiveRef.current) {
        const primitive = new RiskRewardPrimitive(targetOverlay, (updated) => {
          if (onOverlayChange) onOverlayChange(updated);
        });
        seriesRef.current.attachPrimitive(primitive);
        rrPrimitiveRef.current = primitive;
      } else {
        rrPrimitiveRef.current.setData(targetOverlay);
      }
    } else {
      if (rrPrimitiveRef.current) {
        try {
          seriesRef.current.detachPrimitive(rrPrimitiveRef.current);
        } catch {
          // Detach error handling
        }
        rrPrimitiveRef.current = null;
      }
    }
  }, [activeOverlay, showOverlay, draftMode, draftDirection, smcLevels, onOverlayChange]);

  return (
    <div className="flex flex-col w-full h-full bg-charcoal-900 rounded-lg overflow-hidden border border-charcoal-700">
      {/* Chart Toolbar Controls */}
      <div className="flex items-center justify-between px-3 py-2 bg-charcoal-800 border-b border-charcoal-700 text-xs">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-aurum-400">
            {symbol} · Bitget {timeframe}
          </span>

          {lastDataAt && (
            <span className={`px-2 py-0.5 rounded text-[11px] ${dataIsStale ? 'bg-amber-950/60 text-amber-400 border border-amber-800' : 'bg-charcoal-900 text-gray-400'}`}>
              Cập nhật: {lastDataAt} {dataIsStale ? '(Dữ liệu cũ)' : ''}
            </span>
          )}
        </div>

        {/* Action Toggles */}
        <div className="flex items-center gap-2">
          {/* SMC Levels Toggle */}
          <button
            onClick={() => setShowSMCLevels(!showSMCLevels)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs transition ${showSMCLevels ? 'bg-amber-600/30 text-amber-300 border border-amber-500/40' : 'bg-charcoal-900 text-gray-400 hover:text-gray-200'}`}
            title="Bật/Tắt Lớp Cấu Trúc SMC (Đỉnh/Đáy/Equilibrium)"
          >
            <Layers className="w-3.5 h-3.5" />
            <span>SMC Levels</span>
          </button>

          {/* Overlay Toggle */}
          <button
            onClick={() => setShowOverlay(!showOverlay)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs transition ${showOverlay ? 'bg-indigo-600/30 text-indigo-300 border border-indigo-500/40' : 'bg-charcoal-900 text-gray-400 hover:text-gray-200'}`}
            title="Bật/Tắt Lớp Vị Thế R:R (TradingView Long/Short Position Tool)"
          >
            {showOverlay ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
            <span>Overlay R:R</span>
          </button>


          {/* Draft Tool Toggle */}
          <button
            onClick={() => setDraftMode(!draftMode)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs transition ${draftMode ? 'bg-aurum-500 text-charcoal-950 font-medium' : 'bg-charcoal-900 text-gray-400 hover:text-gray-200'}`}
            title="Bật công cụ thử nghiệm kéo thả mức Entry/SL/TP"
          >
            <Crosshair className="w-3.5 h-3.5" />
            <span>Draft R:R Tool</span>
          </button>

          {draftMode && (
            <div className="flex items-center gap-1 bg-charcoal-900 p-0.5 rounded border border-charcoal-700">
              <button
                onClick={() => setDraftDirection('LONG')}
                className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${draftDirection === 'LONG' ? 'bg-emerald-600 text-white' : 'text-gray-400'}`}
              >
                <ArrowUpRight className="w-3 h-3" /> Long
              </button>
              <button
                onClick={() => setDraftDirection('SHORT')}
                className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${draftDirection === 'SHORT' ? 'bg-rose-600 text-white' : 'text-gray-400'}`}
              >
                <ArrowDownRight className="w-3 h-3" /> Short
              </button>
            </div>
          )}

          {/* Manual Refresh */}
          <button
            onClick={() => loadCandles(false)}
            disabled={loading}
            className="p-1 hover:bg-charcoal-700 rounded text-gray-400 hover:text-aurum-400 transition"
            title="Đồng bộ nến mới nhất"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-aurum-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* Main Chart Canvas Area */}
      <div className="relative flex-1 w-full min-h-[520px]">
        {/* Loading Overlay */}
        {loading && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-charcoal-950/70 backdrop-blur-xs">
            <RefreshCw className="w-7 h-7 text-aurum-400 animate-spin mb-2" />
            <span className="text-xs text-aurum-300 font-medium tracking-wide">
              Đang tải nến {symbol} {timeframe}...
            </span>
          </div>
        )}

        {/* Error Banner */}
        {errorMessage && (
          <div className="absolute top-3 left-3 right-3 z-30 flex items-center justify-between p-3 bg-red-950/90 border border-red-800 text-red-200 rounded-lg shadow-xl text-xs">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <span>{errorMessage}</span>
            </div>
            <button
              onClick={() => loadCandles(false)}
              className="px-3 py-1 bg-red-800 hover:bg-red-700 text-white font-medium rounded transition"
            >
              Thử lại
            </button>
          </div>
        )}

        <div ref={chartContainerRef} className="w-full h-full" />
      </div>
    </div>
  );
}
