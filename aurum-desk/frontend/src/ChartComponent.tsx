import { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { api, getNextRequestGeneration, isLatestGeneration } from './api/client';
import { RiskRewardPrimitive } from './plugins/RiskRewardPrimitive';
import type { RiskRewardData, DragTargetPart } from './plugins/RiskRewardPrimitive';
import { SMCStructurePrimitive } from './plugins/SMCStructurePrimitive';
import { calculateClientRiskReward } from './utils/calculator';
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
  smcStructure?: {
    swings?: any[];
    structureEvents?: any[];
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
  smcLevels,
  smcStructure,
}: ChartComponentProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const rrPrimitiveRef = useRef<RiskRewardPrimitive | null>(null);
  const smcPrimitiveRef = useRef<SMCStructurePrimitive | null>(null);

  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [lastDataAt, setLastDataAt] = useState<string | null>(null);
  const [dataIsStale, setDataIsStale] = useState(false);
  const [showOverlay, setShowOverlay] = useState(true);
  const [showSMCLevels, setShowSMCLevels] = useState(true);
  const [draftMode, setDraftMode] = useState(false);
  const [draftDirection, setDraftDirection] = useState<'LONG' | 'SHORT'>('LONG');

  const latestClosePriceRef = useRef<number>(4000.0);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentGenRef = useRef<number>(0);

  // Drag interaction state
  const dragStateRef = useRef<{
    pointerId: number;
    part: DragTargetPart;
    startX: number;
    startY: number;
    startPrice: number;
    startEntry: number;
    startSl: number;
    startTp: number;
    startBars: number;
    initialData: RiskRewardData;
  } | null>(null);

  // 1. Initialize Chart instance with React 19 / StrictMode safety
  useEffect(() => {
    if (!chartContainerRef.current) return;

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
        rightOffset: 15,
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

    // Attach SMC Structure Primitive
    const smcPrim = new SMCStructurePrimitive({
      swings: [],
      events: [],
      showSwings: true,
      showEvents: true,
    });
    candlestickSeries.attachPrimitive(smcPrim);
    smcPrimitiveRef.current = smcPrim;

    // ResizeObserver for dynamic container sizing
    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0 || !chartRef.current) return;
      const { width, height } = entries[0].contentRect;
      chartRef.current.applyOptions({ width: Math.max(100, width), height: Math.max(300, height) });
    });

    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      if (smcPrimitiveRef.current && seriesRef.current) {
        try {
          seriesRef.current.detachPrimitive(smcPrimitiveRef.current);
        } catch {}
        smcPrimitiveRef.current = null;
      }
      if (rrPrimitiveRef.current && seriesRef.current) {
        try {
          seriesRef.current.detachPrimitive(rrPrimitiveRef.current);
        } catch {}
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
      try {
        await api.syncCandles(symbol, timeframe, 120);
      } catch (syncErr: any) {
        console.warn('Sync attempt warning:', syncErr?.message || syncErr);
      }

      if (!isLatestGeneration(reqGen)) return;

      const res = await api.getCandles(symbol, timeframe, 150, { signal: controller.signal });
      if (!isLatestGeneration(reqGen)) return;

      if (res && Array.isArray(res.candles) && seriesRef.current) {
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
          latestClosePriceRef.current = formatted[formatted.length - 1].close;

          if (!isBackgroundSync) {
            seriesRef.current.setData(formatted);
          } else {
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
        return;
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
      // Do not clobber if user is dragging
      if (!dragStateRef.current) {
        loadCandles(true);
      }
    }, 8000);
    return () => clearInterval(timer);
  }, [loadCandles]);

  // 3. Update SMC Structure Overlay
  useEffect(() => {
    if (!smcPrimitiveRef.current) return;
    smcPrimitiveRef.current.setData({
      swings: smcStructure?.swings || [],
      events: smcStructure?.structureEvents || [],
      showSwings: showSMCLevels,
      showEvents: showSMCLevels,
    });
  }, [smcStructure, showSMCLevels]);

  // 4. Attach / Update RiskRewardPrimitive Overlay (Without infinite render loop)
  useEffect(() => {
    if (!seriesRef.current) return;

    let targetOverlay: RiskRewardData | null = null;

    if (draftMode) {
      // Build draft overlay initialized with authentic calculation from current close
      const currP = latestClosePriceRef.current;
      const entry = currP;
      const sl = draftDirection === 'LONG'
        ? (smcLevels?.swingLow && smcLevels.swingLow < entry ? smcLevels.swingLow : Number((entry - 8.0).toFixed(2)))
        : (smcLevels?.swingHigh && smcLevels.swingHigh > entry ? smcLevels.swingHigh : Number((entry + 8.0).toFixed(2)));
      const tp = draftDirection === 'LONG'
        ? (smcLevels?.swingHigh && smcLevels.swingHigh > entry ? smcLevels.swingHigh : Number((entry + 20.0).toFixed(2)))
        : (smcLevels?.swingLow && smcLevels.swingLow < entry ? smcLevels.swingLow : Number((entry - 20.0).toFixed(2)));

      const calc = calculateClientRiskReward(draftDirection, entry, sl, tp, 1000.0, 0.25);

      targetOverlay = {
        id: 'draft-trade',
        direction: draftDirection,
        state: 'draft',
        plannedEntry: entry,
        stopLoss: sl,
        takeProfit: tp,
        quantity: calc.quantity,
        initialRiskUsdt: calc.netRiskUsdt,
        riskPct: 0.25,
        grossRR: calc.grossRR,
        estimatedNetRR: calc.estimatedNetRR,
        isValid: calc.isValid,
        invalidReason: calc.invalidReason,
        projectedBars: 25,
      };
    } else if (activeOverlay) {
      targetOverlay = activeOverlay;
    }

    if (targetOverlay && showOverlay) {
      if (!rrPrimitiveRef.current) {
        const primitive = new RiskRewardPrimitive(targetOverlay);
        seriesRef.current.attachPrimitive(primitive);
        rrPrimitiveRef.current = primitive;
      } else {
        // Only update if not currently dragging
        if (!dragStateRef.current) {
          rrPrimitiveRef.current.setData(targetOverlay);
        }
      }
    } else {
      if (rrPrimitiveRef.current) {
        try {
          seriesRef.current.detachPrimitive(rrPrimitiveRef.current);
        } catch {}
        rrPrimitiveRef.current = null;
      }
    }
  }, [activeOverlay, showOverlay, draftMode, draftDirection, smcLevels]);

  // 5. Interactive Pointer Drag Handlers (Hit testing, Coordinate conversion, Zero loop)
  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!chartContainerRef.current || !rrPrimitiveRef.current || !seriesRef.current || !chartRef.current) return;

    const rect = chartContainerRef.current.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;

    const hit = rrPrimitiveRef.current.hitTest(localX, localY);
    if (!hit) return;

    // Freeze chart scrolling/scaling while dragging
    chartRef.current.applyOptions({
      handleScroll: false,
      handleScale: false,
    });

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {}

    const currentData = rrPrimitiveRef.current.getData();
    const priceAtClick = seriesRef.current.coordinateToPrice(localY) ?? (currentData.actualEntry ?? currentData.plannedEntry);

    dragStateRef.current = {
      pointerId: e.pointerId,
      part: hit.part,
      startX: localX,
      startY: localY,
      startPrice: priceAtClick,
      startEntry: currentData.actualEntry ?? currentData.plannedEntry,
      startSl: currentData.stopLoss,
      startTp: currentData.takeProfit,
      startBars: currentData.projectedBars || 25,
      initialData: { ...currentData },
    };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!chartContainerRef.current || !rrPrimitiveRef.current || !seriesRef.current) return;

    const rect = chartContainerRef.current.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;

    const drag = dragStateRef.current;
    if (!drag) {
      // Hover feedback when not dragging
      const hit = rrPrimitiveRef.current.hitTest(localX, localY);
      rrPrimitiveRef.current.setHoverPart(hit?.part || null);
      if (hit) {
        if (hit.part === 'entry' || hit.part === 'sl' || hit.part === 'tp') {
          chartContainerRef.current.style.cursor = 'ns-resize';
        } else if (hit.part === 'right_edge') {
          chartContainerRef.current.style.cursor = 'ew-resize';
        } else if (hit.part === 'body') {
          chartContainerRef.current.style.cursor = 'move';
        }
      } else {
        chartContainerRef.current.style.cursor = 'default';
      }
      return;
    }

    const currentPrice = seriesRef.current.coordinateToPrice(localY);
    if (currentPrice === null) return;

    const roundedPrice = Number(currentPrice.toFixed(2));
    let newEntry = drag.startEntry;
    let newSl = drag.startSl;
    let newTp = drag.startTp;
    let newBars = drag.startBars;

    if (drag.part === 'entry') {
      newEntry = roundedPrice;
    } else if (drag.part === 'sl') {
      newSl = roundedPrice;
    } else if (drag.part === 'tp') {
      newTp = roundedPrice;
    } else if (drag.part === 'body') {
      const delta = roundedPrice - drag.startPrice;
      newEntry = Number((drag.startEntry + delta).toFixed(2));
      newSl = Number((drag.startSl + delta).toFixed(2));
      newTp = Number((drag.startTp + delta).toFixed(2));
    } else if (drag.part === 'right_edge') {
      const dx = localX - drag.startX;
      const barDelta = Math.round(dx / 12);
      newBars = Math.max(10, Math.min(100, drag.startBars + barDelta));
    }

    const direction = drag.initialData.direction;
    const calc = calculateClientRiskReward(direction, newEntry, newSl, newTp, 1000.0, drag.initialData.riskPct || 0.25);

    const updatedData: RiskRewardData = {
      ...drag.initialData,
      plannedEntry: newEntry,
      stopLoss: newSl,
      takeProfit: newTp,
      quantity: calc.quantity,
      initialRiskUsdt: calc.netRiskUsdt,
      grossRR: calc.grossRR,
      estimatedNetRR: calc.estimatedNetRR,
      isValid: calc.isValid,
      invalidReason: calc.invalidReason,
      projectedBars: newBars,
    };

    requestAnimationFrame(() => {
      rrPrimitiveRef.current?.setData(updatedData);
    });
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStateRef.current || !chartRef.current) return;

    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {}

    chartRef.current.applyOptions({
      handleScroll: true,
      handleScale: true,
    });

    if (rrPrimitiveRef.current) {
      const finalData = rrPrimitiveRef.current.getData();
      if (onOverlayChange) {
        onOverlayChange(finalData);
      }
    }

    dragStateRef.current = null;
  };

  // 6. Keyboard Escape to cancel dragging
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dragStateRef.current && rrPrimitiveRef.current && chartRef.current) {
        chartRef.current.applyOptions({ handleScroll: true, handleScale: true });
        rrPrimitiveRef.current.setData(dragStateRef.current.initialData);
        dragStateRef.current = null;
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

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
          {/* SMC Levels & Structure Toggle */}
          <button
            onClick={() => setShowSMCLevels(!showSMCLevels)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs transition ${showSMCLevels ? 'bg-amber-600/30 text-amber-300 border border-amber-500/40' : 'bg-charcoal-900 text-gray-400 hover:text-gray-200'}`}
            title="Bật/Tắt Lớp Cấu Trúc SMC (Đỉnh/Đáy/BOS/CHoCH)"
          >
            <Layers className="w-3.5 h-3.5" />
            <span>SMC Cấu Trúc</span>
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
            title="Bật công cụ thử nghiệm kéo thả mức Entry/SL/TP (TradingView Long/Short Tool)"
          >
            <Crosshair className="w-3.5 h-3.5" />
            <span>Draft R:R Kéo Thả</span>
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

      {/* Main Chart Canvas Area with Pointer Event Listeners */}
      <div
        className="relative flex-1 w-full min-h-[520px] touch-none"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
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

export default ChartComponent;
