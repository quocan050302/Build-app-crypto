import { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { api, getNextRequestGeneration, isLatestGeneration } from './api/client';
import { wsClient } from './services/wsClient';
import { RiskRewardPrimitive } from './plugins/RiskRewardPrimitive';
import type { RiskRewardData, DragTargetPart } from './plugins/RiskRewardPrimitive';
import { SMCStructurePrimitive } from './plugins/SMCStructurePrimitive';
import { calculateClientRiskReward } from './utils/calculator';
import { quoteStore } from './services/quoteStore';
import { RefreshCw, AlertCircle, Eye, EyeOff, Crosshair, ArrowUpRight, ArrowDownRight, Layers, BarChart2 } from 'lucide-react';

export interface ChartComponentProps {
  symbol: string;
  timeframe: string;
  activeOverlay?: RiskRewardData | null;
  onOverlayChange?: (data: RiskRewardData) => void;
  onFocusTrade?: (trade: any) => void;
  accountEquity?: number;
  riskPct?: number;
  leverage?: number;
  marginMode?: 'ISOLATED' | 'CROSS';
  rvolData?: any;
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
  accountEquity = 1000.0,
  riskPct = 0.25,
  leverage = 5,
  marginMode = 'ISOLATED',
  rvolData,
  smcLevels,
  smcStructure,
}: ChartComponentProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const rrPrimitiveRef = useRef<RiskRewardPrimitive | null>(null);
  const smcPrimitiveRef = useRef<SMCStructurePrimitive | null>(null);
  const latestQuoteTsRef = useRef<number | null>(null);

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
      },
    });

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });

    const smcPrimitive = new SMCStructurePrimitive();
    candlestickSeries.attachPrimitive(smcPrimitive);
    smcPrimitiveRef.current = smcPrimitive;

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);
    handleResize();

    return () => {
      window.removeEventListener('resize', handleResize);
      if (rrPrimitiveRef.current && seriesRef.current) {
        try {
          seriesRef.current.detachPrimitive(rrPrimitiveRef.current);
        } catch {}
        rrPrimitiveRef.current = null;
      }
      if (smcPrimitiveRef.current && seriesRef.current) {
        try {
          seriesRef.current.detachPrimitive(smcPrimitiveRef.current);
        } catch {}
        smcPrimitiveRef.current = null;
      }
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  const [currentPriceDisplay, setCurrentPriceDisplay] = useState<number>(4000.0);
  const [staleNotice, setStaleNotice] = useState<string | null>(null);

  // Define renderCandles before loadCandles for clean closure semantics
  const renderCandles = useCallback((data: any) => {
    const formatted: CandleData[] = data.candles.map((c: any) => ({
      time: Math.floor(c.timestamp / 1000) as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    seriesRef.current?.setData(formatted);

    if (data.candles.length > 0) {
      const last = data.candles[data.candles.length - 1];
      latestClosePriceRef.current = last.close;
      setCurrentPriceDisplay(last.close);
      setLastDataAt(new Date(last.timestamp).toLocaleTimeString('vi-VN'));
    }

    setDataIsStale(Boolean(data.is_stale));
    setStaleNotice(data.is_stale ? 'Dữ liệu cũ' : null);
    setLoading(false);
  }, []);

  // 2. Load Candles with AbortController & generation counter scoped per resource
  const loadCandles = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const resourceKey = `chart_${symbol}_${timeframe}`;
    const gen = getNextRequestGeneration(resourceKey);
    currentGenRef.current = gen;

    setLoading(true);
    setErrorMessage(null);

    try {
      const data = await api.getCandles(symbol, timeframe, 150, {
        signal: controller.signal,
      });

      if (!isLatestGeneration(gen, resourceKey)) return;

      if (!data?.candles || data.candles.length === 0) {
        // If series already has data, don't show full error banner
        if (seriesRef.current && latestClosePriceRef.current > 0) {
          setDataIsStale(true);
          setStaleNotice('Chưa có nến mới');
          setLoading(false);
          return;
        }

        setErrorMessage(`Chưa có dữ liệu nến cho ${symbol} (${timeframe}). Đang đồng bộ...`);
        try {
          const syncRes = await api.syncCandles(symbol, timeframe, 150);
          if (syncRes?.synced_count > 0) {
            const reData = await api.getCandles(symbol, timeframe, 150);
            if (reData?.candles?.length > 0 && isLatestGeneration(gen, resourceKey)) {
              renderCandles(reData);
              return;
            }
          }
        } catch {}
        setLoading(false);
        return;
      }

      renderCandles(data);
    } catch (err: any) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') return;
      if (!isLatestGeneration(gen, resourceKey)) return;

      // CRITICAL V7.1: If series already has candles, DO NOT wipe chart!
      if (seriesRef.current && latestClosePriceRef.current > 0) {
        setDataIsStale(true);
        setStaleNotice('Dữ liệu cũ / Đang kết nối lại...');
      } else {
        setErrorMessage(`Lỗi tải biểu đồ: ${err.message || 'Lỗi mạng'}`);
      }
      setLoading(false);
    }
  }, [symbol, timeframe, renderCandles]);

  useEffect(() => {
    loadCandles();
  }, [loadCandles]);

  // Real-time incremental candle delta listener via Shared wsClient (V7.2)
  useEffect(() => {
    let disposed = false;
    let pendingRaf: number | null = null;
    const pendingBarsQueue: CandleData[] = [];

    const unsubscribe = wsClient.subscribe((msg: any) => {
      if (disposed) return;
      try {
        if (msg.type === 'CANDLE_UPDATE' || msg.type === 'CANDLE_CLOSED') {
          if (msg.symbol === symbol && msg.timeframe === timeframe && msg.payload) {
            const p = msg.payload;
            const bar: CandleData = {
              time: p.time as Time,
              open: p.open,
              high: p.high,
              low: p.low,
              close: p.close,
            };

            const candleTs = typeof p.time === 'number' ? p.time * 1000 : undefined;
            quoteStore.updateFromCandle(symbol, p.close, candleTs);

            // R03: Do not overwrite ticker price with candle close if ticker is newer
            if (!latestQuoteTsRef.current || !candleTs || candleTs >= latestQuoteTsRef.current) {
              latestClosePriceRef.current = p.close;
              setCurrentPriceDisplay(p.close);
            }
            setDataIsStale(false);
            setStaleNotice(null);

            // Queue bar so boundary transition (closed bar -> new open bar) is never lost
            pendingBarsQueue.push(bar);

            if (pendingRaf === null) {
              pendingRaf = requestAnimationFrame(() => {
                if (seriesRef.current && !disposed && pendingBarsQueue.length > 0) {
                  while (pendingBarsQueue.length > 0) {
                    const nextBar = pendingBarsQueue.shift();
                    if (nextBar) {
                      seriesRef.current.update(nextBar);
                    }
                  }
                }
                pendingRaf = null;
              });
            }
          }
        } else if (msg.type === 'QUOTE_UPDATE' && msg.payload?.last) {
          quoteStore.updateFromQuoteEnvelope(msg);
          if (msg.symbol === symbol) {
            const lastPrice = typeof msg.payload.last === 'number' ? msg.payload.last : parseFloat(msg.payload.last);
            latestClosePriceRef.current = lastPrice;
            latestQuoteTsRef.current = msg.exchange_ts_ms || msg.server_received_at_ms || Date.now();
            setCurrentPriceDisplay(lastPrice);
          }
        }
      } catch {}
    });

    return () => {
      disposed = true;
      if (pendingRaf !== null) cancelAnimationFrame(pendingRaf);
      unsubscribe();
    };
  }, [symbol, timeframe]);

  // 3. Update SMC Primitive when props change
  useEffect(() => {
    if (!smcPrimitiveRef.current) return;
    smcPrimitiveRef.current.setData({
      swings: smcStructure?.swings || [],
      events: smcStructure?.structureEvents || [],
      showSwings: showSMCLevels,
      showEvents: showSMCLevels,
    });
  }, [smcStructure, showSMCLevels]);

  // 4. Attach / Update RiskRewardPrimitive Overlay using real equity and settings
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

      const calc = calculateClientRiskReward(
        draftDirection,
        entry,
        sl,
        tp,
        accountEquity,
        riskPct,
        2.0,
        undefined,
        leverage,
        marginMode
      );

      targetOverlay = {
        id: 'draft-trade',
        direction: draftDirection,
        state: 'draft',
        plannedEntry: entry,
        stopLoss: sl,
        takeProfit: tp,
        quantity: calc.quantity,
        initialRiskUsdt: calc.netRiskUsdt,
        riskPct: riskPct,
        grossRR: calc.grossRR,
        estimatedNetRR: calc.estimatedNetRR,
        isValid: calc.isValid,
        invalidReason: calc.invalidReason,
        leverage: calc.leverage,
        marginMode: calc.marginMode,
        estimatedLiquidation: calc.estimatedLiquidation,
        initialMargin: calc.initialMarginUsdt,
        tier: calc.tier,
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
  }, [activeOverlay, showOverlay, draftMode, draftDirection, smcLevels, accountEquity, riskPct, leverage, marginMode]);

  // 5. Interactive Pointer Drag Handlers
  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!chartContainerRef.current || !rrPrimitiveRef.current || !seriesRef.current || !chartRef.current) return;

    const rect = chartContainerRef.current.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;

    const hit = rrPrimitiveRef.current.hitTest(localX, localY);
    if (!hit) return;

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
    const calc = calculateClientRiskReward(
      direction,
      newEntry,
      newSl,
      newTp,
      accountEquity,
      drag.initialData.riskPct || riskPct,
      2.0,
      undefined,
      leverage,
      marginMode
    );

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
      leverage: calc.leverage,
      marginMode: calc.marginMode,
      estimatedLiquidation: calc.estimatedLiquidation,
      initialMargin: calc.initialMarginUsdt,
      tier: calc.tier,
      projectedBars: newBars,
    };

    requestAnimationFrame(() => {
      rrPrimitiveRef.current?.setData(updatedData);
    });
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStateRef.current || !chartRef.current) return;

    chartRef.current.applyOptions({
      handleScroll: true,
      handleScale: true,
    });

    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {}

    if (rrPrimitiveRef.current) {
      const finalData = rrPrimitiveRef.current.getData();
      if (onOverlayChange) {
        onOverlayChange(finalData);
      }
    }

    dragStateRef.current = null;
  };

  return (
    <div className="relative flex flex-col h-full bg-[#121214] select-none">
      {/* Top Chart Header / Controls */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#18181b] border-b border-gray-800 text-xs z-10 flex-wrap gap-2">
        <div className="flex items-center space-x-3">
          <span className="font-bold text-amber-400 text-sm tracking-wide">
            {symbol} Bitget Futures (USDT-M)
          </span>
          <span className="bg-gray-800 px-2 py-0.5 rounded text-gray-300 font-mono">
            {timeframe}
          </span>
          {dataIsStale && (
            <span className="flex items-center text-amber-500 font-medium bg-amber-950/40 px-2 py-0.5 rounded border border-amber-900/50">
              <AlertCircle className="w-3.5 h-3.5 mr-1" />
              Nến trễ ({lastDataAt})
            </span>
          )}

          {/* RVOL Badge */}
          {rvolData && rvolData.rvol !== null && (
            <span
              className={`flex items-center px-2 py-0.5 rounded font-mono font-medium border ${
                rvolData.classification === 'HIGH' || rvolData.classification === 'ULTRA_HIGH'
                  ? 'bg-emerald-950/40 text-emerald-400 border-emerald-800'
                  : 'bg-gray-800 text-gray-300 border-gray-700'
              }`}
            >
              <BarChart2 className="w-3 h-3 mr-1" />
              RVOL: {rvolData.rvol}x ({rvolData.classification})
            </span>
          )}

          {/* Leverage & Margin indicator */}
          <span className="bg-indigo-950/50 text-indigo-300 px-2 py-0.5 rounded border border-indigo-800/60 font-mono">
            {leverage}x {marginMode}
          </span>
        </div>

        {/* View toggles & Draft Mode */}
        <div className="flex items-center space-x-2">
          {/* Toggle Draft Mode */}
          <button
            onClick={() => setDraftMode(!draftMode)}
            className={`flex items-center px-2.5 py-1 rounded font-medium transition-all ${
              draftMode
                ? 'bg-amber-600 text-white shadow-lg shadow-amber-600/20'
                : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
            }`}
          >
            <Crosshair className="w-3.5 h-3.5 mr-1" />
            {draftMode ? 'Đang bật Kéo Draft R:R' : 'Vẽ Thử Kế Hoạch (Draft)'}
          </button>

          {draftMode && (
            <div className="flex items-center bg-gray-900 rounded p-0.5 border border-gray-700">
              <button
                onClick={() => setDraftDirection('LONG')}
                className={`flex items-center px-2 py-0.5 rounded ${
                  draftDirection === 'LONG' ? 'bg-emerald-600 text-white' : 'text-gray-400'
                }`}
              >
                <ArrowUpRight className="w-3 h-3 mr-0.5" /> Mua
              </button>
              <button
                onClick={() => setDraftDirection('SHORT')}
                className={`flex items-center px-2 py-0.5 rounded ${
                  draftDirection === 'SHORT' ? 'bg-rose-600 text-white' : 'text-gray-400'
                }`}
              >
                <ArrowDownRight className="w-3 h-3 mr-0.5" /> Bán
              </button>
            </div>
          )}

          {/* Toggle SMC Structure Visuals */}
          <button
            onClick={() => setShowSMCLevels(!showSMCLevels)}
            className={`p-1.5 rounded hover:bg-gray-800 ${
              showSMCLevels ? 'text-indigo-400' : 'text-gray-500'
            }`}
            title="Bật/Tắt Cấu trúc SMC (Swings, Sweeps, BOS, CHoCH)"
          >
            <Layers className="w-4 h-4" />
          </button>

          {/* Toggle Risk/Reward Overlay */}
          <button
            onClick={() => setShowOverlay(!showOverlay)}
            className={`p-1.5 rounded hover:bg-gray-800 ${
              showOverlay ? 'text-emerald-400' : 'text-gray-500'
            }`}
            title="Bật/Tắt Hiển thị Hộp R:R"
          >
            {showOverlay ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
          </button>

          {/* Manual Refresh */}
          <button
            onClick={loadCandles}
            disabled={loading}
            className="p-1.5 rounded text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
            title="Làm mới nến"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Main Chart Area */}
      <div
        ref={chartContainerRef}
        className="flex-1 w-full relative touch-none"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40 z-20 backdrop-blur-xs">
            <RefreshCw className="w-6 h-6 text-amber-500 animate-spin" />
            <span className="ml-2 text-sm text-gray-200">Đang tải nến sàn Bitget...</span>
          </div>
        )}

        {staleNotice && !errorMessage && (
          <div className="absolute top-4 right-4 flex items-center space-x-1.5 px-2.5 py-1 bg-amber-950/80 border border-amber-800/80 rounded-md z-20 text-amber-200 text-xs shadow-md">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
            <span>{staleNotice}</span>
          </div>
        )}

        {errorMessage && (
          <div className="absolute top-4 left-4 right-4 flex items-center justify-between p-3 bg-red-950/80 border border-red-800 rounded-md z-20 text-red-200 text-xs">
            <span>{errorMessage}</span>
            <button
              onClick={loadCandles}
              className="ml-3 px-2 py-1 bg-red-800 hover:bg-red-700 text-white rounded font-medium"
            >
              Thử lại
            </button>
          </div>
        )}
      </div>

      {/* Footer Info */}
      <div className="px-4 py-1.5 bg-[#141416] border-t border-gray-800/80 flex items-center justify-between text-[11px] text-gray-400">
        <div className="flex items-center space-x-4">
          <span>
            Giá hiện tại: <strong className="text-amber-400 font-mono">${currentPriceDisplay.toFixed(2)}</strong>
          </span>
          {smcLevels?.swingHigh && (
            <span>
              Swing High: <span className="text-emerald-400 font-mono">${smcLevels.swingHigh.toFixed(2)}</span>
            </span>
          )}
          {smcLevels?.swingLow && (
            <span>
              Swing Low: <span className="text-rose-400 font-mono">${smcLevels.swingLow.toFixed(2)}</span>
            </span>
          )}
          {smcLevels?.equilibrium && (
            <span>
              Equilibrium: <span className="text-indigo-400 font-mono">${smcLevels.equilibrium.toFixed(2)}</span>
            </span>
          )}
        </div>
        <div className="flex items-center space-x-3 text-gray-500">
          <span>Vốn giả lập: ${accountEquity.toFixed(2)}</span>
          <span>•</span>
          <span>Múi giờ: Asia/Ho_Chi_Minh (UTC+7)</span>
        </div>
      </div>
    </div>
  );
}
