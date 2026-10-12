import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { api, getNextRequestGeneration, isLatestGeneration } from './api/client';
import { wsClient } from './services/wsClient';
import { RiskRewardPrimitive } from './plugins/RiskRewardPrimitive';
import type { RiskRewardData, DragTargetPart } from './plugins/RiskRewardPrimitive';
import { SMCStructurePrimitive } from './plugins/SMCStructurePrimitive';
import type { SMCTimeAxis, SMCZone } from './plugins/SMCStructurePrimitive';
import { projectMsToBar } from './utils/chartTimeProjection';
import type { ChartEvidenceBundle } from './utils/chartEvidence';
import { buildEvidenceAnchorIndex, computeEvidenceFocusRange, type EvidenceAnchor } from './utils/evidenceFocus';
import { ChartLayerMenu } from './components/ChartLayerMenu';
import { useChartLayers } from './hooks/useChartLayers';
import { ChartEvidenceList } from './components/ChartEvidenceList';
import { SelectedChartHeader } from './components/SelectedChartHeader';
import { ChartEvidenceTooltip } from './components/ChartEvidenceTooltip';
import type { SMCHitItem } from './plugins/SMCStructurePrimitive';
import type { ChartBinding, ChartEvidenceBundleV144 } from './types/smcScenario';

import { calculateClientRiskReward } from './utils/calculator';
import { quoteStore } from './services/quoteStore';
import { RefreshCw, AlertCircle, Eye, EyeOff, Crosshair, ArrowUpRight, ArrowDownRight, BarChart2, List } from 'lucide-react';

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
  selectedEvidence?: ChartEvidenceBundle | null;
  evidenceMode?: 'SELECTED' | 'ALL' | 'NONE';
  focusRequest?: {
    nonce: number;
    minPrice?: number;
    maxPrice?: number;
    targetTime?: number;
    startAnchorMs?: number;
    endAnchorMs?: number;
    mode?: 'LIVE' | 'SELECTION';
  } | null;
  onEvidenceModeChange?: (mode: 'SELECTED' | 'ALL' | 'NONE') => void;
  selectedBinding?: ChartBinding | null;
  selectedRawBundle?: ChartEvidenceBundleV144 | null;
  selectedSummaryVi?: string;
  onClearSelection?: () => void;
  onOpenSelectionDetails?: () => void;
  selectedLoading?: boolean;
  selectedError?: string | null;
}

interface CandleData {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
}

const TF_MS: Record<string, number> = {
  '1M': 60_000,
  '5M': 300_000,
  '15M': 900_000,
  '1H': 3_600_000,
  '4H': 14_400_000,
  D: 86_400_000,
};

/** Closed/open candles from the API -> strictly ascending, de-duplicated, finite OHLC only. */
function toCandleList(raw: any): CandleData[] {
  if (!Array.isArray(raw)) return [];
  const byTime = new Map<number, CandleData>();
  for (const c of raw) {
    const ts = Math.floor(Number(c?.timestamp) / 1000);
    const vals = [c?.open, c?.high, c?.low, c?.close];
    if (!Number.isSafeInteger(ts) || ts <= 0 || !vals.every((v) => typeof v === 'number' && Number.isFinite(v))) continue;
    byTime.set(ts, { time: ts as Time, open: c.open, high: c.high, low: c.low, close: c.close });
  }
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([, v]) => v);
}

/** History window is merged under the live window; on a time collision the NEWER (live) bar wins. */
function mergeCandles(current: CandleData[], incoming: CandleData[]): CandleData[] {
  const byTime = new Map<number, CandleData>();
  for (const c of incoming) byTime.set(c.time as number, c);
  for (const c of current) byTime.set(c.time as number, c);
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([, v]) => v);
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
  selectedEvidence,
  evidenceMode = 'SELECTED',
  focusRequest,
  onEvidenceModeChange: _onEvidenceModeChange,
  selectedBinding,
  selectedRawBundle,
  selectedSummaryVi,
  onClearSelection,
  onOpenSelectionDetails,
  selectedLoading,
  selectedError,
}: ChartComponentProps) {
  const lastFocusNonceRef = useRef<number>(0);
  const draftBaseOverlayRef = useRef<RiskRewardData | null | undefined>(activeOverlay);
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
  const [showSMCLevels] = useState(true);
  const [draftMode, setDraftMode] = useState(false);
  const [layerSettings, setLayerSettings] = useChartLayers();
  const [isEvidenceListOpen, setIsEvidenceListOpen] = useState(false);
  const evidenceListTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [hoveredHitItem, setHoveredHitItem] = useState<SMCHitItem | null>(null);
  const [draftDirection, setDraftDirection] = useState<'LONG' | 'SHORT'>('LONG');

  const anchorIndex = useMemo(() => buildEvidenceAnchorIndex(selectedRawBundle), [selectedRawBundle]);

  const focusEvidence = useCallback((evidenceId: string) => {
    const anchor = anchorIndex.get(evidenceId);
    if (!anchor) {
      // Fallback: check if hit item exists in smcPrimitive
      const hits = smcPrimitiveRef.current?.getHitItems() || [];
      const match = hits.find((h) => h.id === evidenceId || h.externalId === evidenceId);
      if (match && chartRef.current && match.timeMs) {
        const stepMs = 5 * 60 * 1000;
        const fakeAnchor: EvidenceAnchor = {
          id: match.id,
          kind: match.category,
          occurredAtMs: match.timeMs,
          knownAtMs: match.timeMs,
          startAtMs: match.timeMs,
          endAtMs: null,
          price: match.price,
          hasCoordinates: true,
        };
        const range = computeEvidenceFocusRange(fakeAnchor, stepMs, 16);
        if (range && chartRef.current) {
          chartRef.current.timeScale().setVisibleRange({
            from: range.fromSec as unknown as Time,
            to: range.toSec as unknown as Time,
          });
        }
        smcPrimitiveRef.current?.setFocusedEvidenceId(evidenceId);
        setHoveredHitItem(match);
        setIsEvidenceListOpen(false);
        return;
      }
      console.warn(`Evidence ${evidenceId} anchor not found in bundle index.`);
      return;
    }

    if (!anchor.hasCoordinates) {
      console.warn(`Evidence ${evidenceId} lacks coordinates: ${anchor.missingReason}`);
      return;
    }

    const stepMs = 5 * 60 * 1000;
    const range = computeEvidenceFocusRange(anchor, stepMs, 16);
    if (range && chartRef.current) {
      chartRef.current.timeScale().setVisibleRange({
        from: range.fromSec as unknown as Time,
        to: range.toSec as unknown as Time,
      });
    }

    if (smcPrimitiveRef.current) {
      smcPrimitiveRef.current.setFocusedEvidenceId(evidenceId);
    }
    setIsEvidenceListOpen(false);
  }, [anchorIndex]);

  const latestClosePriceRef = useRef<number>(4000.0);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentGenRef = useRef<number>(0);
  const candlesRef = useRef<CandleData[]>([]);
  const timeAxisRef = useRef<SMCTimeAxis>({ startsMs: [], durationMs: 0 });
  const focusGenRef = useRef<number>(0);
  const focusAbortRef = useRef<AbortController | null>(null);
  const pendingFocusRef = useRef<ChartComponentProps['focusRequest']>(null);
  const liveReadyKeyRef = useRef<string>('');
  const runFocusRef = useRef<((req: NonNullable<ChartComponentProps['focusRequest']>) => Promise<void>) | null>(null);
  const loadCandlesRef = useRef<(() => Promise<void>) | null>(null);
  const dragRafRef = useRef<number | null>(null);
  const latestDragDataRef = useRef<RiskRewardData | null>(null);
  const issueCountRef = useRef<number>(0);
  const [candlesVersion, setCandlesVersion] = useState(0);
  const [historyNotice, setHistoryNotice] = useState<string | null>(null);
  const [, setHistoryMode] = useState(false);
  const [awayFromLive, setAwayFromLive] = useState(false);
  const [chartIssue, setChartIssue] = useState<{ code: string; count: number } | null>(null);
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

    // Test-only read accessor. `import.meta.env.DEV` is false in production builds, so this block (and the
    // global) is removed by the bundler: nothing here is reachable in a shipped app.
    if (import.meta.env.DEV) {
      (window as unknown as { __AURUM_CHART_TEST__?: unknown }).__AURUM_CHART_TEST__ = {
        smcHits: () =>
          (smcPrimitiveRef.current?.getHitItems() ?? []).map((h) => ({
            id: h.id, category: h.category, title: h.title, price: h.price, timeMs: h.timeMs, x: h.x, y: h.y,
          })),
        rr: () => {
          const p = rrPrimitiveRef.current;
          return p ? { data: p.getData(), geometry: p.getGeometry() } : null;
        },
        reservedRects: () => rrPrimitiveRef.current?.getReservedRects() ?? [],
        labelRects: () => (smcPrimitiveRef.current?.getHitItems() ?? []),
        focusedEvidenceId: () => smcPrimitiveRef.current?.getFocusedEvidenceId() ?? null,
        visibleRange: () => chartRef.current?.timeScale().getVisibleRange() ?? null,
        candleCount: () => candlesRef.current.length,
        firstCandleSec: () => (typeof candlesRef.current[0]?.time === 'number' ? (candlesRef.current[0]?.time as number) : null),
        lastCandleSec: () => (typeof candlesRef.current[candlesRef.current.length - 1]?.time === 'number' ? (candlesRef.current[candlesRef.current.length - 1]?.time as number) : null),
        firstCandleMs: () => (typeof candlesRef.current[0]?.time === 'number' ? (candlesRef.current[0]?.time as number) * 1000 : null),
        lastCandleMs: () => (typeof candlesRef.current[candlesRef.current.length - 1]?.time === 'number' ? (candlesRef.current[candlesRef.current.length - 1]?.time as number) * 1000 : null),
        focusEvidence: (id: string) => focusEvidence(id),
      };
    }

    let animationFrameId: number | null = null;

    const handleResize = () => {
      if (animationFrameId !== null) {
        cancelAnimationFrame(animationFrameId);
      }
      animationFrameId = requestAnimationFrame(() => {
        if (chartContainerRef.current && chartRef.current) {
          const width = chartContainerRef.current.clientWidth;
          const height = chartContainerRef.current.clientHeight;
          if (width > 0 && height > 0) {
            chartRef.current.applyOptions({
              width,
              height,
            });
          }
        }
      });
    };

    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined' && chartContainerRef.current) {
      resizeObserver = new ResizeObserver(() => {
        handleResize();
      });
      resizeObserver.observe(chartContainerRef.current);
    }

    window.addEventListener('resize', handleResize);
    handleResize();

    return () => {
      window.removeEventListener('resize', handleResize);
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
      if (animationFrameId !== null) {
        cancelAnimationFrame(animationFrameId);
      }
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
      if (import.meta.env.DEV) {
        delete (window as unknown as { __AURUM_CHART_TEST__?: unknown }).__AURUM_CHART_TEST__;
      }
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  const [currentPriceDisplay, setCurrentPriceDisplay] = useState<number>(4000.0);
  const [staleNotice, setStaleNotice] = useState<string | null>(null);

  const applyCandleList = useCallback((list: CandleData[]) => {
    candlesRef.current = list;
    timeAxisRef.current.startsMs = list.map((c) => (c.time as number) * 1000);
    timeAxisRef.current.durationMs = TF_MS[timeframe] ?? 0;
  }, [timeframe]);

  // Bounded diagnostics: the badge shows a count, never an unbounded log.
  const reportChartDataIssue = useCallback((code: string) => {
    issueCountRef.current = Math.min(issueCountRef.current + 1, 999);
    setChartIssue({ code, count: issueCountRef.current });
  }, []);

  // Define renderCandles before loadCandles for clean closure semantics
  const renderCandles = useCallback((data: any) => {
    const formatted = toCandleList(data.candles);
    applyCandleList(formatted);
    seriesRef.current?.setData(formatted);

    if (formatted.length > 0) {
      const last = formatted[formatted.length - 1];
      latestClosePriceRef.current = last.close;
      setCurrentPriceDisplay(last.close);
      setLastDataAt(new Date((last.time as number) * 1000).toLocaleTimeString('vi-VN'));
    }

    setDataIsStale(Boolean(data.is_stale));
    setStaleNotice(data.is_stale ? 'Dữ liệu cũ' : null);
    setLoading(false);
    setHistoryMode(false);
    setCandlesVersion((v) => v + 1);
    liveReadyKeyRef.current = `${symbol}_${timeframe}`;
    const pending = pendingFocusRef.current;
    if (pending) {
      pendingFocusRef.current = null;
      void runFocusRef.current?.(pending);
    }
  }, [symbol, timeframe, applyCandleList]);

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

  loadCandlesRef.current = loadCandles;
  useEffect(() => {
    loadCandles();
  }, [loadCandles]);

  // Real-time incremental candle delta listener via Shared wsClient (V7.2)
  useEffect(() => {
    let disposed = false;
    let pendingRaf: number | null = null;
    let recoveryTimer: ReturnType<typeof setTimeout> | null = null;
    const pendingBarsQueue: CandleData[] = [];
    const MAX_QUEUE = 100;

    // Gap / ordering problems are repaired from the API (debounced), never silently ignored.
    const scheduleRecovery = () => {
      if (recoveryTimer !== null || disposed) return;
      recoveryTimer = setTimeout(() => {
        recoveryTimer = null;
        if (!disposed) void loadCandlesRef.current?.();
      }, 2000);
    };

    const flushQueue = () => {
      pendingRaf = null;
      if (disposed || !seriesRef.current) return;
      while (pendingBarsQueue.length > 0) {
        const nextBar = pendingBarsQueue.shift();
        if (!nextBar) continue;
        const list = candlesRef.current;
        const last = list[list.length - 1];
        const t = nextBar.time as number;
        if (last && t < (last.time as number)) {
          reportChartDataIssue('OUT_OF_ORDER_BAR');
          scheduleRecovery();
          continue;
        }
        try {
          seriesRef.current.update(nextBar);
        } catch {
          reportChartDataIssue('SERIES_UPDATE_REJECTED');
          scheduleRecovery();
          continue;
        }
        if (last && t === (last.time as number)) {
          list[list.length - 1] = nextBar;
        } else {
          list.push(nextBar);
          timeAxisRef.current.startsMs.push(t * 1000);
        }
      }
    };

    const unsubscribe = wsClient.subscribe((msg: any) => {
      if (disposed) return;
      try {
        if (msg.type === 'CANDLE_UPDATE' || msg.type === 'CANDLE_CLOSED') {
          if (msg.symbol === symbol && msg.timeframe === timeframe && msg.payload) {
            const p = msg.payload;
            const finite = [p.open, p.high, p.low, p.close].every((v: unknown) => typeof v === 'number' && Number.isFinite(v));
            if (!Number.isSafeInteger(p.time) || p.time <= 0 || !finite || p.high < p.low) {
              reportChartDataIssue('MALFORMED_CANDLE');
              return;
            }
            const bar: CandleData = {
              time: p.time as Time,
              open: p.open,
              high: p.high,
              low: p.low,
              close: p.close,
            };

            const candleTs = p.time * 1000;
            quoteStore.updateFromCandle(symbol, p.close, candleTs);

            // R03: Do not overwrite ticker price with candle close if ticker is newer
            if (!latestQuoteTsRef.current || candleTs >= latestQuoteTsRef.current) {
              latestClosePriceRef.current = p.close;
              setCurrentPriceDisplay(p.close);
            }
            setDataIsStale(false);
            setStaleNotice(null);

            // Same-open updates coalesce; a closed bar followed by a NEW open bar keeps both (boundary preserved).
            const tail = pendingBarsQueue[pendingBarsQueue.length - 1];
            if (tail && (tail.time as number) === p.time) {
              pendingBarsQueue[pendingBarsQueue.length - 1] = bar;
            } else {
              pendingBarsQueue.push(bar);
            }
            if (pendingBarsQueue.length > MAX_QUEUE) {
              pendingBarsQueue.splice(0, pendingBarsQueue.length - MAX_QUEUE);
              reportChartDataIssue('QUEUE_OVERFLOW');
              scheduleRecovery();
            }

            if (pendingRaf === null) {
              pendingRaf = requestAnimationFrame(flushQueue);
            }
          }
        } else if (msg.type === 'QUOTE_UPDATE' && msg.payload?.last) {
          // Quotes are telemetry only: no setData, no scenario analysis in the browser.
          quoteStore.updateFromQuoteEnvelope(msg);
          if (msg.symbol === symbol) {
            const lastPrice = typeof msg.payload.last === 'number' ? msg.payload.last : parseFloat(msg.payload.last);
            if (Number.isFinite(lastPrice)) {
              latestClosePriceRef.current = lastPrice;
              latestQuoteTsRef.current = msg.exchange_ts_ms || msg.server_received_at_ms || Date.now();
              setCurrentPriceDisplay(lastPrice);
            }
          }
        }
      } catch {
        // A handler failure must be visible (bounded indicator), never swallowed silently.
        reportChartDataIssue('WS_HANDLER_ERROR');
      }
    });

    return () => {
      disposed = true;
      if (pendingRaf !== null) cancelAnimationFrame(pendingRaf);
      if (recoveryTimer !== null) clearTimeout(recoveryTimer);
      unsubscribe();
    };
  }, [symbol, timeframe, reportChartDataIssue]);

  // 3. Update SMC Primitive when props change (Supports focused selectedEvidence & global mode)
  useEffect(() => {
    if (!smcPrimitiveRef.current) return;
    const emptyData = {
      swings: [],
      events: [],
      zones: [],
      liquidityLevels: [],
      displacements: [],
      retests: [],
      timeAxis: timeAxisRef.current,
      showSwings: false,
      showEvents: false,
      showZones: false,
      showLiquidity: false,
      showDisplacements: false,
      showRetests: false,
      showScenarioPath: false,
    };
    if (!showSMCLevels || evidenceMode === 'NONE') {
      smcPrimitiveRef.current.setData(emptyData);
      return;
    }

    if (evidenceMode === 'SELECTED') {
      if (selectedEvidence) {
        // Zones use the real FVG source times; a zone without them is UNANCHORED and is not drawn at all.
        const mappedZones: SMCZone[] = [];
        for (const z of selectedEvidence.poiZones || []) {
          const startAt = z.startAtMs;
          const endAt = z.endAtMs ?? selectedEvidence.asOfMs;
          if (typeof startAt !== 'number' || typeof endAt !== 'number') continue;
          mappedZones.push({
            id: z.id || `zone-${mappedZones.length}`,
            kind: (z.kind as SMCZone['kind']) || 'FVG',
            top: z.top,
            bottom: z.bottom,
            start_at: startAt,
            end_at: endAt,
            direction: z.direction === 'LONG' ? 'BULLISH' : z.direction === 'SHORT' ? 'BEARISH' : undefined,
            state: (z.state as SMCZone['state']) || 'ACTIVE',
            label: z.label,
          });
        }

        // Displacement candle extremes come from the LOADED candle at the measured time (real data only).
        const dur = TF_MS[timeframe] ?? 0;
        const displacements = (selectedEvidence.displacements || []).map((d) => {
          const p = projectMsToBar(timeAxisRef.current.startsMs, dur, d.candle_time);
          if (p && p.kind === 'IN') {
            const bar = candlesRef.current[p.barIndex];
            if (bar) return { ...d, high: bar.high, low: bar.low };
          }
          return d;
        });

        smcPrimitiveRef.current.setData({
          swings: selectedEvidence.swings || [],
          events: selectedEvidence.structureEvents || [],
          zones: mappedZones,
          liquidityLevels: selectedEvidence.liquidityLevels || [],
          displacements,
          retests: selectedEvidence.retests || [],
          scenarioPath: selectedEvidence.scenarioPath,
          timeAxis: timeAxisRef.current,
          showSwings: layerSettings.showSwings,
          showEvents: layerSettings.showBreaks,
          showZones: layerSettings.showFvg,
          showLiquidity: layerSettings.showLiquidity,
          showDisplacements: layerSettings.showStructure,
          showRetests: layerSettings.showStructure,
          showScenarioPath: layerSettings.showScenarioPath,
          annotationExclusions: rrPrimitiveRef.current?.getReservedRects() || [],
        });
      } else {
        // Crucial fix: Never fall back to global structure when in SELECTED mode (loading/error included)!
        smcPrimitiveRef.current.setData(emptyData);
      }
      return;
    }

    // ALL mode - explicit global market structure overview
    if (evidenceMode === 'ALL') {
      smcPrimitiveRef.current.setData({
        ...emptyData,
        swings: smcStructure?.swings || [],
        events: smcStructure?.structureEvents || [],
        showSwings: layerSettings.showSwings,
        showEvents: layerSettings.showBreaks,
        showLiquidity: layerSettings.showLiquidity,
      });
    }
  }, [smcStructure, showSMCLevels, evidenceMode, selectedEvidence, layerSettings, candlesVersion, timeframe]);

  // Focus request handling: runs ONLY on an explicit click/focus (new nonce), never on quote ticks. The selected
  // anchors may be far in the past, so a bounded window of real candles is loaded first (<=480 bars, generation-guarded).
  const runFocus = async (req: NonNullable<ChartComponentProps['focusRequest']>) => {
    const chart = chartRef.current;
    if (!chart) return;
    focusAbortRef.current?.abort();
    const controller = new AbortController();
    focusAbortRef.current = controller;
    const gen = ++focusGenRef.current;
    const isValidMs = (v?: number): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v > 0;
    const anchorStart = isValidMs(req.startAnchorMs) ? req.startAnchorMs : req.targetTime;
    const anchorEnd = isValidMs(req.endAnchorMs) ? req.endAnchorMs : anchorStart;
    if (!isValidMs(anchorStart) || !isValidMs(anchorEnd)) {
      chart.priceScale('right').applyOptions({ autoScale: true });
      chart.timeScale().scrollToPosition(3, false);
      return;
    }
    const dur = TF_MS[timeframe] ?? 300_000;
    const lastAnchor = Math.max(anchorStart, anchorEnd);
    const padBefore = Math.max(30 * 60_000, 40 * dur);
    const padAfter = Math.max(60 * 60_000, 40 * dur);
    const startMs = anchorStart - padBefore;
    const endMs = lastAnchor + padAfter;

    const list = candlesRef.current;
    const firstMs = list.length ? (list[0].time as number) * 1000 : Infinity;
    const lastMs = list.length ? (list[list.length - 1].time as number) * 1000 + dur : -Infinity;
    const covered = firstMs <= startMs && lastMs >= lastAnchor;
    if (!covered) {
      const maxSpan = 480 * dur;
      const wEnd = Math.min(endMs, startMs + maxSpan);
      try {
        const data = await api.getCandles(symbol, timeframe, 300, { signal: controller.signal }, { startMs, endMs: wEnd });
        if (gen !== focusGenRef.current) return;
        const incoming = toCandleList(data?.candles);
        if (!incoming.length) {
          setHistoryNotice('Thiếu nến lịch sử quanh mục đã chọn — giá Entry/SL/TP vẫn đúng theo lệnh.');
        } else {
          const merged = mergeCandles(candlesRef.current, incoming);
          applyCandleList(merged);
          seriesRef.current?.setData(merged);
          setHistoryNotice(null);
          setCandlesVersion((v) => v + 1);
        }
        setHistoryMode(true);
      } catch (err: any) {
        if (controller.signal.aborted || err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') return;
        setHistoryNotice('Không tải được nến lịch sử quanh mục đã chọn.');
      }
    }
    if (gen !== focusGenRef.current) return;
    try {
      chart.timeScale().setVisibleRange({
        from: Math.floor(startMs / 1000) as unknown as Time,
        to: Math.floor(endMs / 1000) as unknown as Time,
      });
    } catch {
      try { chart.timeScale().scrollToPosition(3, false); } catch {}
    }
    chart.priceScale('right').applyOptions({ autoScale: true });
    setAwayFromLive(true);
  };
  runFocusRef.current = runFocus;

  useEffect(() => {
    if (!focusRequest || focusRequest.nonce === lastFocusNonceRef.current) return;
    lastFocusNonceRef.current = focusRequest.nonce;
    // A timeframe switch triggers a live reload that would overwrite the window: wait for it, then focus.
    if (liveReadyKeyRef.current !== `${symbol}_${timeframe}`) {
      pendingFocusRef.current = focusRequest;
      return;
    }
    void runFocusRef.current?.(focusRequest);
  }, [focusRequest, symbol, timeframe]);

  useEffect(() => () => {
    focusGenRef.current += 1;
    focusAbortRef.current?.abort();
    if (dragRafRef.current !== null) cancelAnimationFrame(dragRafRef.current);
  }, []);

  const goLive = () => {
    focusGenRef.current += 1;
    focusAbortRef.current?.abort();
    pendingFocusRef.current = null;
    setHistoryNotice(null);
    setAwayFromLive(false);
    void loadCandles().then(() => {
      chartRef.current?.priceScale('right').applyOptions({ autoScale: true });
      chartRef.current?.timeScale().scrollToRealTime();
    });
  };

  // 4. Attach / Update RiskRewardPrimitive Overlay using real equity and settings
  const selectionKey = selectedBinding
    ? `${selectedBinding.source}:${(selectedBinding as any).scenario_id || (selectedBinding as any).setup_id || (selectedBinding as any).order_id || selectedBinding.id}:${(selectedBinding as any).snapshotId || (selectedBinding as any).snapshot_id || ''}`
    : 'NONE';

  const prevSelectionKeyRef = useRef(selectionKey);

  useEffect(() => {
    if (selectionKey !== prevSelectionKeyRef.current) {
      prevSelectionKeyRef.current = selectionKey;
      setDraftMode(false);
      draftBaseOverlayRef.current = null;
      if (dragRafRef.current !== null) {
        cancelAnimationFrame(dragRafRef.current);
        dragRafRef.current = null;
      }
    }
  }, [selectionKey]);

  useEffect(() => {
    if (activeOverlay !== draftBaseOverlayRef.current) {
      setDraftMode(false);
      draftBaseOverlayRef.current = activeOverlay;
    }
  }, [activeOverlay]);

  useEffect(() => {
    if (!seriesRef.current) return;

    let targetOverlay: RiskRewardData | null = null;

    if (draftMode && activeOverlay === draftBaseOverlayRef.current) {
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
        grossLossUsdt: calc.grossLossUsdt,
        grossRewardUsdt: calc.grossRewardUsdt,
        netRewardUsdt: calc.netRewardUsdt,
        multiplier: calc.multiplier,
        canExecute: calc.canExecute,
        executionReason: calc.skipReason,
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

  // 5. Interactive Pointer Drag Handlers (DRAFT only; armed/open/closed/observation are read-only)
  const isReadOnlyOverlay = (d: RiskRewardData) =>
    d.readOnly === true || d.state === 'armed' || d.state === 'paper_open' || d.state === 'closed' || d.state === 'invalidated';

  const restoreChartInteraction = () => {
    chartRef.current?.applyOptions({ handleScroll: true, handleScale: true });
  };

  const cancelDragRaf = () => {
    if (dragRafRef.current !== null) {
      cancelAnimationFrame(dragRafRef.current);
      dragRafRef.current = null;
    }
  };

  // One pending RAF at most; it always paints the LATEST computed frame.
  const scheduleDragPaint = (data: RiskRewardData) => {
    latestDragDataRef.current = data;
    if (dragRafRef.current !== null) return;
    dragRafRef.current = requestAnimationFrame(() => {
      dragRafRef.current = null;
      if (latestDragDataRef.current) {
        rrPrimitiveRef.current?.setData(latestDragDataRef.current);
        const exclusions = rrPrimitiveRef.current?.getReservedRects() || [];
        smcPrimitiveRef.current?.setExclusions(exclusions);
      }
    });
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!chartContainerRef.current || !rrPrimitiveRef.current || !seriesRef.current || !chartRef.current) return;

    const rect = chartContainerRef.current.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;

    const hit = rrPrimitiveRef.current.hitTest(localX, localY);
    if (!hit) return;

    // Confirmed setups / orders / observations never mutate through the chart.
    const currentData = rrPrimitiveRef.current.getData();
    if (isReadOnlyOverlay(currentData)) return;

    chartRef.current.applyOptions({
      handleScroll: false,
      handleScale: false,
    });

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {}

    const priceAtClick = seriesRef.current.coordinateToPrice(localY) ?? (currentData.actualEntry ?? currentData.plannedEntry);

    latestDragDataRef.current = null;
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
        // R:R handles have priority over SMC annotations; hovering never creates or cancels anything.
        setHoveredHitItem(null);
        if (hit.part === 'entry' || hit.part === 'sl' || hit.part === 'tp') {
          chartContainerRef.current.style.cursor = 'ns-resize';
        } else if (hit.part === 'right_edge') {
          chartContainerRef.current.style.cursor = 'ew-resize';
        } else if (hit.part === 'body') {
          chartContainerRef.current.style.cursor = 'move';
        }
      } else {
        const smcHit = smcPrimitiveRef.current?.hitTest(localX, localY) ?? null;
        setHoveredHitItem((prev) => (prev?.id === smcHit?.id ? prev : smcHit));
        chartContainerRef.current.style.cursor = smcHit ? 'pointer' : 'default';
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
      scheduleDragPaint({ ...drag.initialData, projectedBars: newBars });
      return;
    }

    // Direction is fixed by the draft: dragging a SHORT stop below entry yields an invalid-geometry warning,
    // it never flips the trade or silently re-sorts the levels.
    const direction = drag.initialData.direction;
    const calc = calculateClientRiskReward(
      direction,
      newEntry,
      newSl,
      newTp,
      accountEquity,
      drag.initialData.riskPct || riskPct,
      2.0,
      drag.initialData.quantity,
      drag.initialData.leverage || leverage,
      drag.initialData.marginMode || marginMode
    );

    scheduleDragPaint({
      ...drag.initialData,
      plannedEntry: newEntry,
      stopLoss: newSl,
      takeProfit: newTp,
      quantity: calc.quantity,
      initialRiskUsdt: calc.netRiskUsdt,
      grossRewardUsdt: calc.grossRewardUsdt,
      grossLossUsdt: calc.grossLossUsdt,
      netRewardUsdt: calc.netRewardUsdt,
      multiplier: calc.multiplier,
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
    });
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStateRef.current || !chartRef.current) return;

    // Flush: the committed value is the last computed frame, never a stale painted one.
    cancelDragRaf();
    if (latestDragDataRef.current && rrPrimitiveRef.current) {
      rrPrimitiveRef.current.setData(latestDragDataRef.current);
    }

    restoreChartInteraction();

    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {}

    if (rrPrimitiveRef.current && latestDragDataRef.current) {
      const finalData = rrPrimitiveRef.current.getData();
      if (onOverlayChange) {
        onOverlayChange(finalData);
      }
    }

    latestDragDataRef.current = null;
    dragStateRef.current = null;
  };

  // PointerCancel / lost capture: roll back to the pre-drag geometry and restore pan/zoom.
  const handlePointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragStateRef.current;
    if (!drag) return;
    cancelDragRaf();
    latestDragDataRef.current = null;
    rrPrimitiveRef.current?.setData(drag.initialData);
    restoreChartInteraction();
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {}
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
            onClick={() => {
              if (!draftMode && selectedBinding) {
                onClearSelection?.();
              }
              draftBaseOverlayRef.current = activeOverlay;
              setDraftMode(!draftMode);
            }}
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

          {/* Layer Menu & Toggle */}
          <ChartLayerMenu
            settings={layerSettings}
            onChange={setLayerSettings}
            evidenceMode={evidenceMode}
            onEvidenceModeChange={_onEvidenceModeChange}
          />

          {/* Evidence List Button */}
          {selectedRawBundle && (
            <button
              ref={evidenceListTriggerRef}
              type="button"
              data-testid="open-evidence-list"
              onClick={() => setIsEvidenceListOpen(true)}
              className="p-1.5 rounded hover:bg-gray-800 text-aurum-400 flex items-center gap-1 border border-charcoal-700 bg-charcoal-850 text-xs"
              title="Xem danh sách bằng chứng (Evidence List)"
            >
              <List className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Bằng chứng</span>
            </button>
          )}

          <ChartEvidenceList
            isOpen={isEvidenceListOpen}
            onClose={() => setIsEvidenceListOpen(false)}
            bundle={selectedRawBundle ?? null}
            onFocusEvidence={focusEvidence}
            triggerRef={evidenceListTriggerRef}
          />

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

      {/* Selected Chart Header */}
      {selectedBinding && (
        <SelectedChartHeader
          binding={selectedBinding}
          state={selectedEvidence?.state}
          plan={selectedEvidence?.riskRewardPlan}
          summaryVi={selectedSummaryVi}
          onClear={onClearSelection}
          onOpenDetails={onOpenSelectionDetails}
        />
      )}

      {/* Main Chart Area */}
      <div
        ref={chartContainerRef}
        data-testid="chart-pane"
        className="flex-1 w-full relative touch-none"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        onLostPointerCapture={handlePointerCancel}
        onPointerLeave={() => setHoveredHitItem(null)}
      >
        {hoveredHitItem && (
          <ChartEvidenceTooltip
            data={{
              kind: hoveredHitItem.title,
              price: hoveredHitItem.price,
              occurredAtMs: hoveredHitItem.timeMs,
              detail: hoveredHitItem.detail,
              x: hoveredHitItem.x,
              y: hoveredHitItem.y,
            }}
            onClose={() => setHoveredHitItem(null)}
          />
        )}
        {selectedBinding && selectedLoading && (
          <div
            data-testid="selection-loading"
            className="absolute top-3 left-3 z-20 px-2.5 py-1 bg-gray-900/90 border border-cyan-800/70 rounded-md text-xs text-cyan-200"
          >
            Đang tải bằng chứng của mục đã chọn…
          </div>
        )}
        {selectedBinding && selectedError && !selectedLoading && (
          <div
            data-testid="selection-error"
            className="absolute top-3 left-3 z-20 px-2.5 py-1 bg-rose-950/80 border border-rose-800 rounded-md text-xs text-rose-200 max-w-[70%]"
          >
            {selectedError}
          </div>
        )}
        {historyNotice && (
          <div
            data-testid="history-notice"
            className="absolute bottom-3 left-3 z-20 px-2.5 py-1 bg-amber-950/80 border border-amber-800/80 rounded-md text-xs text-amber-200"
          >
            {historyNotice}
          </div>
        )}
        {awayFromLive && (
          <button
            type="button"
            data-testid="back-to-live"
            onClick={goLive}
            className="absolute bottom-3 right-16 z-20 px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white text-xs font-medium rounded-md shadow-lg"
          >
            Về hiện tại
          </button>
        )}
        {chartIssue && (
          <div
            data-testid="chart-data-issue"
            title={`Mã lỗi: ${chartIssue.code}`}
            className="absolute top-3 right-3 z-20 px-2 py-1 bg-amber-950/80 border border-amber-800/80 rounded-md text-[11px] text-amber-200"
          >
            Dữ liệu nến bất thường ({chartIssue.count})
          </div>
        )}
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
