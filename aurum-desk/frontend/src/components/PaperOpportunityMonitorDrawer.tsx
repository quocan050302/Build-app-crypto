/**
 * frontend/src/components/PaperOpportunityMonitorDrawer.tsx
 * Aurum V17.0 — Paper Opportunity & Non-Entry Reason Monitor Drawer.
 *
 * Requirements (Phần 111 - 130, D1 - D6):
 * - Single drawer overlay (desktop 560-640px, mobile fullscreen 390x844).
 * - Accessible dialog (role="dialog", aria-modal="true", Escape key closes).
 * - Master/Detail pattern inside the same drawer (no nested modals).
 * - Minimalist filters (Today / 7 Days / Custom; Session; Status).
 * - Truthful metrics (0 closed trades => N/A, verified breakeven => 0.00).
 * - Timeline with clear milestone provenance.
 * - Focus chart action: closes drawer first, then delegates to production handlers.
 * - Markdown report download on-demand (no automated Telegram spam).
 */

import React, { useState, useEffect, useCallback, useId, useRef } from 'react';
import {
  X,
  ArrowLeft,
  RefreshCw,
  Download,
  Clock,
  Layers,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  SlidersHorizontal,
  Compass,
  FileText,
  AlertCircle,
  ExternalLink,
} from 'lucide-react';

import { usePaperOpportunityMonitor } from '../hooks/usePaperOpportunityMonitor';
import { OpportunityTimeline } from './OpportunityTimeline';
import {
  formatMonitorTime,
  formatMonitorDateTime,
  formatMonitorReportFilename,
  validateMonitorChartFocusContext,
} from '../utils/paperOpportunityText';
import type { CG01DisplayState } from '../utils/cg01DisplayState';
import type { MonitorFilterState, OpportunityMonitorListItem } from '../types/paperOpportunityMonitor';
import { api } from '../api/client';

export interface PaperOpportunityMonitorDrawerProps {
  open: boolean;
  onClose: () => void;
  symbol?: string;
  initialScope?: string;
  liveDisplayState?: CG01DisplayState | null;
  onFocusSetupOnChart?: (setup: any) => void;
  onFocusTradeOnChart?: (trade: any) => void;
}

export const PaperOpportunityMonitorDrawer: React.FC<PaperOpportunityMonitorDrawerProps> = ({
  open,
  onClose,
  symbol = 'XAUUSDT',
  initialScope = 'CG01',
  liveDisplayState: _liveDisplayState,
  onFocusSetupOnChart,
  onFocusTradeOnChart,
}) => {
  const titleId = useId();
  const [selectedOpportunityId, setSelectedOpportunityId] = useState<string | null>(null);
  const [showTechAccordion, setShowTechAccordion] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isFocusing, setIsFocusing] = useState(false);
  const [focusError, setFocusError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const focusAbortControllerRef = useRef<AbortController | null>(null);
  const focusGenerationRef = useRef(0);
  const focusInFlightRef = useRef(false);

  const exportAbortControllerRef = useRef<AbortController | null>(null);
  const exportGenerationRef = useRef(0);

  // Filters state
  const [filters, setFilters] = useState<MonitorFilterState>({
    period: 'today',
    session: 'all',
    status: 'all',
  });

  const {
    summary,
    page,
    detail,
    resolvedQuery,
    isSummaryLoading,
    isListLoading,
    isDetailLoading,
    summaryError,
    listError,
    detailError,
    refresh,
    loadNextPage,
    selectOpportunity,
  } = usePaperOpportunityMonitor({
    open,
    symbol,
    scope: initialScope,
    filters,
    selectedOpportunityId,
  });

  const invalidateFocusRequest = useCallback(() => {
    focusGenerationRef.current += 1;
    if (focusAbortControllerRef.current) {
      focusAbortControllerRef.current.abort();
      focusAbortControllerRef.current = null;
    }
    focusInFlightRef.current = false;
    if (mountedRef.current) {
      setIsFocusing(false);
    }
  }, []);

  const invalidateExportRequest = useCallback(() => {
    exportGenerationRef.current += 1;
    if (exportAbortControllerRef.current) {
      exportAbortControllerRef.current.abort();
      exportAbortControllerRef.current = null;
    }
    if (mountedRef.current) {
      setIsExporting(false);
    }
  }, []);

  const requestCloseMonitor = useCallback(() => {
    invalidateFocusRequest();
    invalidateExportRequest();
    setSelectedOpportunityId(null);
    selectOpportunity(null);
    setShowTechAccordion(false);
    setFocusError(null);
    setExportError(null);
    onClose();
  }, [invalidateFocusRequest, invalidateExportRequest, selectOpportunity, onClose]);

  // Handle Escape key to close or back
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectedOpportunityId) {
          // If in detail view, go back to list first
          invalidateFocusRequest();
          setSelectedOpportunityId(null);
          selectOpportunity(null);
          setFocusError(null);
        } else {
          requestCloseMonitor();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, selectedOpportunityId, invalidateFocusRequest, selectOpportunity, requestCloseMonitor]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      invalidateFocusRequest();
      invalidateExportRequest();
    };
  }, [invalidateFocusRequest, invalidateExportRequest]);

  useEffect(() => {
    if (!open) {
      invalidateFocusRequest();
      invalidateExportRequest();
      setSelectedOpportunityId(null);
      selectOpportunity(null);
      setShowTechAccordion(false);
      setFocusError(null);
      setExportError(null);
    }
  }, [open, invalidateFocusRequest, invalidateExportRequest, selectOpportunity]);

  const handleSelectRow = (item: OpportunityMonitorListItem) => {
    invalidateFocusRequest();
    setSelectedOpportunityId(item.opportunity_id);
    selectOpportunity(item.opportunity_id);
    setFocusError(null);
  };

  const handleBackToList = () => {
    invalidateFocusRequest();
    setSelectedOpportunityId(null);
    selectOpportunity(null);
    setShowTechAccordion(false);
    setFocusError(null);
  };

  // Focus chart handler (Fetch canonical DTO -> validate -> close drawer -> delegate)
  const handleFocusChart = useCallback(async () => {
    if (!detail?.item?.chart_target?.verified || !selectedOpportunityId) return;
    if (focusInFlightRef.current) return;

    const target = detail.item.chart_target;
    const captured = {
      opportunityId: selectedOpportunityId,
      targetId: target.id,
      kind: target.kind,
      revision: target.revision ?? null,
      setupInstanceId: target.setup_instance_id ?? null,
      queryKey: resolvedQuery?.query_key ?? summary?.query_key ?? null,
      runId: resolvedQuery?.run_id || summary?.coverage?.run_id || undefined,
      startMs: resolvedQuery?.start_ms ?? undefined,
      endMs: resolvedQuery?.end_ms ?? undefined,
      sessionId: resolvedQuery?.session_id ?? (filters.session !== 'all' ? filters.session : undefined),
      modelId: resolvedQuery?.model_id ?? undefined,
    };

    invalidateFocusRequest();
    focusInFlightRef.current = true;
    setIsFocusing(true);
    setFocusError(null);

    const controller = new AbortController();
    focusAbortControllerRef.current = controller;
    const currentGen = focusGenerationRef.current;

    try {
      const result = await api.getOpportunityMonitorChartTarget(
        captured.opportunityId,
        {
          symbol,
          scope: initialScope,
          run_id: captured.runId,
          start_ms: captured.startMs,
          end_ms: captured.endMs,
          session_id: captured.sessionId,
          model_id: captured.modelId,
          expected_target_id: captured.targetId,
          expected_kind: captured.kind,
          ...(captured.kind === 'SETUP' && captured.revision != null
            ? { expected_revision: captured.revision }
            : {}),
        },
        controller.signal
      );

      if (
        !mountedRef.current ||
        controller.signal.aborted ||
        currentGen !== focusGenerationRef.current ||
        selectedOpportunityId !== captured.opportunityId
      ) {
        return;
      }

      const validated = validateMonitorChartFocusContext(result, {
        queryKey: captured.queryKey,
        expectedKind: captured.kind,
        expectedTargetId: captured.targetId,
        expectedOpportunityId: captured.opportunityId,
        expectedSymbol: symbol,
        revision: captured.revision ?? undefined,
      });

      if (validated.status !== 'AVAILABLE') {
        setFocusError(validated.message_vi);
        return;
      }

      const callback = validated.kind === 'ORDER' ? onFocusTradeOnChart : onFocusSetupOnChart;
      if (!callback) {
        setFocusError('Chưa thể mở đối tượng này trên biểu đồ. Vui lòng tải lại giao diện.');
        return;
      }

      if (
        !mountedRef.current ||
        controller.signal.aborted ||
        currentGen !== focusGenerationRef.current
      ) {
        return;
      }

      // Validated successfully: invalidate pending focus tokens, close drawer, and invoke callback
      invalidateFocusRequest();
      setSelectedOpportunityId(null);
      selectOpportunity(null);
      onClose();

      callback(validated.dto);
    } catch (err: any) {
      if (
        mountedRef.current &&
        !controller.signal.aborted &&
        currentGen === focusGenerationRef.current
      ) {
        setFocusError(err?.message || 'Không thể tải bằng chứng chart canonical');
      }
    } finally {
      if (mountedRef.current && currentGen === focusGenerationRef.current) {
        focusInFlightRef.current = false;
        setIsFocusing(false);
      }
    }
  }, [
    detail,
    selectedOpportunityId,
    resolvedQuery,
    summary,
    symbol,
    initialScope,
    filters.session,
    invalidateFocusRequest,
    selectOpportunity,
    onClose,
    onFocusTradeOnChart,
    onFocusSetupOnChart,
  ]);

  // Markdown Report Download
  const handleDownloadReport = async () => {
    invalidateExportRequest();
    setIsExporting(true);
    setExportError(null);

    const controller = new AbortController();
    exportAbortControllerRef.current = controller;
    const currentGen = exportGenerationRef.current;

    const capturedBound = {
      symbol,
      scope: initialScope,
      run_id: resolvedQuery?.run_id || summary?.coverage?.run_id || undefined,
      start_ms: resolvedQuery?.start_ms ?? summary?.query_scope?.start_ms ?? undefined,
      end_ms: resolvedQuery?.end_ms ?? summary?.query_scope?.end_ms ?? undefined,
      session_id: resolvedQuery?.session_id ?? (filters.session !== 'all' ? filters.session : undefined),
      model_id: resolvedQuery?.model_id ?? undefined,
    };

    let objectUrl: string | null = null;
    try {
      const md = await api.getOpportunityMonitorReportMarkdown(
        capturedBound,
        controller.signal
      );

      if (!mountedRef.current || controller.signal.aborted || currentGen !== exportGenerationRef.current) {
        return;
      }

      const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
      objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;

      if (capturedBound.start_ms && capturedBound.end_ms) {
        a.download = formatMonitorReportFilename({
          symbol,
          start_ms: capturedBound.start_ms,
          end_ms: capturedBound.end_ms,
        });
      } else {
        a.download = `aurum-opportunity-report-${symbol}-${new Date().toISOString().slice(0, 10)}.md`;
      }

      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (err: any) {
      if (mountedRef.current && !controller.signal.aborted && currentGen === exportGenerationRef.current) {
        console.error('Failed to download report:', err);
        setExportError(err?.message || 'Không thể tải báo cáo Markdown');
      }
    } finally {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
      if (mountedRef.current && currentGen === exportGenerationRef.current) {
        setIsExporting(false);
      }
    }
  };

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-xs transition-opacity animate-fadeIn"
      data-testid="paper-opportunity-monitor-drawer"
    >
      {/* Backdrop click closes */}
      <div className="absolute inset-0" onClick={requestCloseMonitor} aria-hidden="true" />

      {/* Drawer Container: 560-640px on Desktop, Fullscreen on Mobile */}
      <div className="relative w-full sm:w-[580px] lg:w-[620px] h-full bg-charcoal-900 border-l border-charcoal-750 shadow-2xl flex flex-col z-10 overflow-hidden text-gray-200">
        {/* Sticky Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-charcoal-750 bg-charcoal-850 shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            {selectedOpportunityId ? (
              <button
                type="button"
                data-testid="monitor-back-btn"
                onClick={handleBackToList}
                className="p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-charcoal-750 transition cursor-pointer"
                title="Quay lại danh sách"
                aria-label="Back to list"
              >
                <ArrowLeft className="w-4 h-4" />
              </button>
            ) : (
              <Compass className="w-4 h-4 text-cyan-400 shrink-0" />
            )}
            <div className="min-w-0">
              <h2 id={titleId} className="text-sm font-bold text-gray-100 truncate">
                {selectedOpportunityId ? 'Chi Tiết Cơ Hội' : 'Theo Dõi Cơ Hội Paper'}
              </h2>
              <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono">
                <span className="text-cyan-300 font-semibold">{symbol}</span>
                <span>•</span>
                <span>{initialScope}</span>
                <span>•</span>
                <span>Asia/Ho_Chi_Minh</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              data-testid="monitor-refresh-btn"
              onClick={() => refresh()}
              disabled={isSummaryLoading || isListLoading}
              className="p-1.5 rounded text-gray-400 hover:text-gray-200 hover:bg-charcoal-750 transition cursor-pointer disabled:opacity-50"
              title="Làm mới dữ liệu"
              aria-label="Refresh monitor data"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${(isSummaryLoading || isListLoading) ? 'animate-spin' : ''}`} />
            </button>
            <button
              type="button"
              data-testid="monitor-close-btn"
              onClick={requestCloseMonitor}
              className="p-1.5 rounded text-gray-400 hover:text-gray-200 hover:bg-charcoal-750 transition cursor-pointer"
              title="Đóng bảng quan sát"
              aria-label="Close monitor drawer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Content Region */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {!selectedOpportunityId ? (
            /* ================= MASTER LIST VIEW ================= */
            <>
              {/* Quick Filters */}
              <div className="bg-charcoal-850/80 p-2.5 rounded-lg border border-charcoal-750 space-y-2 text-xs">
                <div className="flex items-center justify-between text-[11px] text-gray-400 font-medium">
                  <span className="flex items-center gap-1">
                    <SlidersHorizontal className="w-3 h-3 text-cyan-400" />
                    Bộ Lọc Quan Sát
                  </span>
                  <span className="text-[10px] font-mono text-gray-500">Mặc định hôm nay</span>
                </div>

                {/* Period tabs */}
                <div className="grid grid-cols-3 gap-1 bg-charcoal-900/80 p-0.5 rounded border border-charcoal-800 text-[11px]">
                  <button
                    type="button"
                    data-testid="filter-period-today"
                    onClick={() => setFilters((f) => ({ ...f, period: 'today' }))}
                    className={`py-1 rounded font-medium transition ${
                      filters.period === 'today'
                        ? 'bg-charcoal-750 text-cyan-300 font-semibold'
                        : 'text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    Hôm nay
                  </button>
                  <button
                    type="button"
                    data-testid="filter-period-7d"
                    onClick={() => setFilters((f) => ({ ...f, period: '7d' }))}
                    className={`py-1 rounded font-medium transition ${
                      filters.period === '7d'
                        ? 'bg-charcoal-750 text-cyan-300 font-semibold'
                        : 'text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    7 ngày
                  </button>
                  <button
                    type="button"
                    data-testid="filter-period-custom"
                    onClick={() => setFilters((f) => ({ ...f, period: 'custom' }))}
                    className={`py-1 rounded font-medium transition ${
                      filters.period === 'custom'
                        ? 'bg-charcoal-750 text-cyan-300 font-semibold'
                        : 'text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    Tùy chọn
                  </button>
                </div>

                {/* Custom date range inputs */}
                {filters.period === 'custom' && (
                  <div className="grid grid-cols-2 gap-2 pt-1">
                    <div>
                      <label className="block text-[10px] text-gray-400 mb-0.5">Từ ngày</label>
                      <input
                        type="date"
                        value={filters.customStartDate || ''}
                        onChange={(e) => setFilters((f) => ({ ...f, customStartDate: e.target.value }))}
                        className="w-full bg-charcoal-900 border border-charcoal-700 text-gray-200 rounded px-1.5 py-1 text-[11px]"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-gray-400 mb-0.5">Đến ngày</label>
                      <input
                        type="date"
                        value={filters.customEndDate || ''}
                        onChange={(e) => setFilters((f) => ({ ...f, customEndDate: e.target.value }))}
                        className="w-full bg-charcoal-900 border border-charcoal-700 text-gray-200 rounded px-1.5 py-1 text-[11px]"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Summary Strip */}
              {summary && (
                <div className="bg-charcoal-850/60 p-3 rounded-lg border border-charcoal-750 space-y-2.5" data-testid="monitor-summary-card">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-gray-300 flex items-center gap-1.5">
                      <Layers className="w-3.5 h-3.5 text-cyan-400" />
                      Tiến Trình Quan Sát (Funnel)
                    </span>
                    <span className="text-[10px] font-mono text-gray-400">
                      Tổng cơ hội: <strong className="text-cyan-300">{summary.counts.total_unique_opportunities}</strong>
                    </span>
                  </div>

                  {/* Funnel Progress Badges */}
                  <div className="grid grid-cols-4 gap-1.5 text-center text-[10px]">
                    <div className="bg-charcoal-900/80 p-1.5 rounded border border-charcoal-800">
                      <span className="block text-gray-400">Chạm vùng</span>
                      <strong className="text-gray-200 text-xs">{summary.counts.zones_touched}</strong>
                    </div>
                    <div className="bg-charcoal-900/80 p-1.5 rounded border border-charcoal-800">
                      <span className="block text-gray-400">Trigger</span>
                      <strong className="text-cyan-300 text-xs">{summary.counts.trigger_confirmed}</strong>
                    </div>
                    <div className="bg-charcoal-900/80 p-1.5 rounded border border-charcoal-800">
                      <span className="block text-amber-400">Bị chặn</span>
                      <strong className="text-amber-300 text-xs">{summary.counts.guard_blocked}</strong>
                    </div>
                    <div className="bg-charcoal-900/80 p-1.5 rounded border border-charcoal-800">
                      <span className="block text-emerald-400">Đã khớp</span>
                      <strong className="text-emerald-300 text-xs">{summary.counts.filled}</strong>
                    </div>
                  </div>

                  {/* Financial Metrics Strip */}
                  <div className="pt-2 border-t border-charcoal-800 text-[11px] flex items-center justify-between flex-wrap gap-2">
                    <div>
                      <span className="text-gray-400">Lãi/lỗ sau phí: </span>
                      {summary.financial.closed_trades_count > 0 && summary.financial.net_realized_pnl_usd !== null ? (
                        <span className={`font-mono font-bold ${summary.financial.net_realized_pnl_usd >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {summary.financial.net_realized_pnl_usd >= 0 ? '+' : ''}
                          {summary.financial.net_realized_pnl_usd.toFixed(2)} USDT
                        </span>
                      ) : (
                        <span className="text-gray-500 font-mono">N/A</span>
                      )}
                    </div>
                    <div>
                      <span className="text-gray-400">Tỷ lệ thắng: </span>
                      {summary.financial.closed_trades_count > 0 && summary.financial.winrate_pct !== null ? (
                        <span className="font-mono font-bold text-gray-200">
                          {summary.financial.winrate_pct.toFixed(0)}% ({summary.financial.closed_trades_count} lệnh)
                        </span>
                      ) : (
                        <span className="text-gray-500 font-mono">N/A (0 lệnh)</span>
                      )}
                    </div>
                  </div>
                  <p className="text-[9px] text-gray-500 italic">
                    *Số liệu tài chính tính trên toàn bộ lệnh thuộc phiên quan sát trong khoảng thời gian.
                  </p>
                </div>
              )}

              {/* Error Notice */}
              {(summaryError || listError) && (
                <div className="p-2.5 rounded-lg bg-rose-950/60 border border-rose-800/80 text-rose-300 text-xs flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span className="truncate">{listError || summaryError}</span>
                </div>
              )}

              {/* Opportunities List */}
              <div className="space-y-1.5" data-testid="monitor-opportunities-list">
                <div className="flex items-center justify-between text-xs px-1">
                  <span className="font-semibold text-gray-300">
                    Danh Sách Cơ Hội ({page?.total || 0})
                  </span>
                  {page?.snapshot_as_of_ms && (
                    <span className="text-[10px] text-gray-500 font-mono">
                      Cập nhật: {formatMonitorTime(page.snapshot_as_of_ms)}
                    </span>
                  )}
                </div>

                {isListLoading && (!page || page.items.length === 0) ? (
                  <div className="p-8 text-center text-xs text-gray-400 space-y-2">
                    <RefreshCw className="w-5 h-5 animate-spin mx-auto text-cyan-400" />
                    <p>Đang tải danh sách cơ hội...</p>
                  </div>
                ) : !page || page.items.length === 0 ? (
                  <div className="p-8 text-center text-xs text-gray-400 bg-charcoal-850/40 rounded-lg border border-charcoal-750 space-y-1">
                    <Compass className="w-6 h-6 mx-auto text-gray-500 mb-1" />
                    <p className="font-semibold text-gray-300">Chưa có cơ hội nào được ghi nhận</p>
                    <p className="text-[11px] text-gray-500">
                      Hệ thống đang quan sát thị trường. Khi có cấu trúc POI/FVG đạt điều kiện, mốc sẽ xuất hiện tại đây.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {page.items.map((it) => (
                      <button
                        key={it.key}
                        type="button"
                        data-testid={`monitor-row-${it.opportunity_id}`}
                        onClick={() => handleSelectRow(it)}
                        className="w-full text-left bg-charcoal-850 hover:bg-charcoal-800 p-2.5 rounded-lg border border-charcoal-750/80 transition flex items-center justify-between gap-2 group cursor-pointer"
                      >
                        <div className="min-w-0 space-y-1">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-[10px] font-mono text-gray-400 flex items-center gap-1">
                              <Clock className="w-2.5 h-2.5 text-gray-500" />
                              {formatMonitorTime(it.last_observed_at_ms || it.first_recorded_at_ms)}
                            </span>
                            <span
                              className={`px-1.5 py-0.2 rounded font-mono font-semibold text-[10px] border ${
                                it.direction === 'LONG'
                                  ? 'bg-emerald-950/70 text-emerald-300 border-emerald-800'
                                  : it.direction === 'SHORT'
                                  ? 'bg-rose-950/70 text-rose-300 border-rose-800'
                                  : 'bg-charcoal-800 text-gray-400 border-charcoal-700'
                              }`}
                            >
                              {it.direction}
                            </span>
                            <span className="px-1.5 py-0.2 rounded text-[10px] font-medium bg-charcoal-800 text-cyan-300 border border-charcoal-700">
                              {it.stage_label_vi}
                            </span>
                            {it.score !== null && (
                              <span className="px-1 py-0.2 rounded text-[9px] font-mono bg-charcoal-900 text-aurum-300 border border-charcoal-800">
                                {it.score.toFixed(0)}đ
                              </span>
                            )}
                          </div>

                          <p className="text-[11px] text-gray-300 leading-snug truncate">
                            {it.primary_reason_vi}
                          </p>
                        </div>

                        <ChevronRight className="w-4 h-4 text-gray-500 group-hover:text-gray-300 transition shrink-0" />
                      </button>
                    ))}
                  </div>
                )}

                {/* Pagination button */}
                {page?.has_more && (
                  <button
                    type="button"
                    data-testid="monitor-load-more-btn"
                    onClick={() => loadNextPage()}
                    disabled={isListLoading}
                    className="w-full py-2 bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 text-xs font-semibold rounded-lg border border-charcoal-700 transition flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-50"
                  >
                    {isListLoading ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <span>Tải thêm cơ hội cũ hơn</span>
                    )}
                  </button>
                )}
              </div>

              {/* Bottom Actions */}
              <div className="pt-2 border-t border-charcoal-750 flex flex-col gap-1.5">
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    data-testid="monitor-download-report-btn"
                    onClick={handleDownloadReport}
                    disabled={isExporting}
                    className="px-3 py-1.5 rounded bg-charcoal-800 hover:bg-charcoal-750 text-gray-200 text-xs font-medium border border-charcoal-700 transition flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                  >
                    <Download className="w-3.5 h-3.5 text-cyan-400" />
                    <span>{isExporting ? 'Đang xuất...' : 'Tải Báo Cáo (.md)'}</span>
                  </button>
                  <span className="text-[10px] text-gray-500">
                    Dữ liệu quan sát thực tế • Không có lệnh giả lập
                  </span>
                </div>
                {exportError && (
                  <span className="text-[11px] text-rose-400" data-testid="monitor-export-error">
                    {exportError}
                  </span>
                )}
              </div>
            </>
          ) : (
            /* ================= DETAIL VIEW ================= */
            <>
              {isDetailLoading && !detail ? (
                <div className="p-8 text-center text-xs text-gray-400 space-y-2">
                  <RefreshCw className="w-5 h-5 animate-spin mx-auto text-cyan-400" />
                  <p>Đang tải chi tiết cơ hội...</p>
                </div>
              ) : detail ? (
                <div className="space-y-4" data-testid="monitor-detail-view">
                  {/* Status Banner */}
                  <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-mono text-gray-400">
                        Mã: <strong className="text-gray-200 select-all">{detail.item.opportunity_id}</strong>
                      </span>
                      <span
                        className={`px-1.5 py-0.2 rounded font-mono font-semibold text-[10px] border ${
                          detail.item.direction === 'LONG'
                            ? 'bg-emerald-950/70 text-emerald-300 border-emerald-800'
                            : detail.item.direction === 'SHORT'
                            ? 'bg-rose-950/70 text-rose-300 border-rose-800'
                            : 'bg-charcoal-800 text-gray-400 border-charcoal-700'
                        }`}
                      >
                        {detail.item.direction}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 rounded text-xs font-semibold bg-cyan-950/70 text-cyan-300 border border-cyan-800/80">
                        {detail.item.stage_label_vi}
                      </span>
                      {detail.item.score !== null && (
                        <span className="px-1.5 py-0.5 rounded text-[11px] font-mono bg-charcoal-900 text-aurum-300 border border-charcoal-800">
                          Điểm: {detail.item.score.toFixed(0)}/100
                        </span>
                      )}
                    </div>

                    <p className="text-xs text-gray-200 leading-snug">
                      {detail.item.primary_reason_vi}
                    </p>
                  </div>

                  {/* Plan & Price Levels */}
                  {detail.plan && (
                    <div className="bg-charcoal-850/60 p-3 rounded-lg border border-charcoal-750 space-y-2">
                      <span className="text-xs font-semibold text-gray-300 flex items-center gap-1.5">
                        <FileText className="w-3.5 h-3.5 text-cyan-400" />
                        Mức Giá Kế Hoạch (Dự Kiến)
                      </span>
                      <div className="grid grid-cols-3 gap-2 text-center text-xs font-mono">
                        <div className="bg-charcoal-900 p-1.5 rounded border border-charcoal-800">
                          <span className="block text-[10px] text-gray-400">Entry</span>
                          <span className="text-gray-100 font-bold">${detail.plan.entry?.toFixed(2) || 'N/A'}</span>
                        </div>
                        <div className="bg-charcoal-900 p-1.5 rounded border border-charcoal-800">
                          <span className="block text-[10px] text-rose-400">Stop Loss</span>
                          <span className="text-rose-300 font-bold">${detail.plan.stop_loss?.toFixed(2) || 'N/A'}</span>
                        </div>
                        <div className="bg-charcoal-900 p-1.5 rounded border border-charcoal-800">
                          <span className="block text-[10px] text-emerald-400">Take Profit</span>
                          <span className="text-emerald-300 font-bold">${detail.plan.take_profit?.toFixed(2) || 'N/A'}</span>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Action: Focus on Chart */}
                  {detail.item.chart_target?.verified && (
                    <div className="space-y-1.5">
                      <button
                        type="button"
                        data-testid="monitor-focus-chart-btn"
                        onClick={handleFocusChart}
                        disabled={isFocusing}
                        className="w-full py-2 bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-charcoal-950 font-bold text-xs rounded-lg transition shadow-md flex items-center justify-center gap-1.5 cursor-pointer disabled:cursor-not-allowed"
                      >
                        {isFocusing ? (
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <ExternalLink className="w-3.5 h-3.5" />
                        )}
                        <span>{isFocusing ? 'Đang tải bằng chứng...' : 'Xem Vị Trí Trên Biểu Đồ'}</span>
                      </button>
                      {focusError && (
                        <div
                          data-testid="monitor-focus-error"
                          className="p-2 rounded bg-rose-950/60 border border-rose-800 text-rose-300 text-xs flex items-center gap-1.5"
                        >
                          <AlertCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
                          <span>{focusError}</span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Timeline */}
                  <div className="space-y-2">
                    <span className="text-xs font-semibold text-gray-300 flex items-center gap-1.5">
                      <Clock className="w-3.5 h-3.5 text-cyan-400" />
                      Tiến Trình Các Mốc Đã Ghi Nhận ({detail.milestones.length})
                    </span>
                    <OpportunityTimeline milestones={detail.milestones} />
                  </div>

                  {/* Technical Accordion (Collapsed by default) */}
                  <div className="border border-charcoal-750 rounded-lg overflow-hidden text-xs">
                    <button
                      type="button"
                      data-testid="monitor-tech-accordion-btn"
                      onClick={() => setShowTechAccordion(!showTechAccordion)}
                      className="w-full p-2.5 bg-charcoal-850 hover:bg-charcoal-800 flex items-center justify-between text-gray-300 font-medium transition cursor-pointer"
                    >
                      <span className="flex items-center gap-1.5">
                        <SlidersHorizontal className="w-3 h-3 text-cyan-400" />
                        Thông Tin Kỹ Thuật & Đo Lường
                      </span>
                      {showTechAccordion ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                    </button>

                    {showTechAccordion && (
                      <div className="p-3 bg-charcoal-900/90 font-mono text-[11px] text-gray-400 space-y-1.5 border-t border-charcoal-750">
                        <div>
                          <span className="text-gray-500">Mã Reason gốc: </span>
                          <span className="text-amber-300">{detail.item.primary_reason_code || 'NONE'}</span>
                        </div>
                        <div>
                          <span className="text-gray-500">Phân loại Miss: </span>
                          <span className="text-gray-200">{detail.classifier_result?.classification || 'N/A'}</span>
                        </div>
                        {detail.latency && (
                          <div>
                            <span className="text-gray-500">Detector Runtime: </span>
                            <span className="text-cyan-300">{detail.latency.detector_runtime_ms?.toFixed(2) || 'N/A'} ms</span>
                          </div>
                        )}
                        <div>
                          <span className="text-gray-500">Ghi nhận đầu: </span>
                          <span>{formatMonitorDateTime(detail.item.first_recorded_at_ms)}</span>
                        </div>
                        <div>
                          <span className="text-gray-500">Quan sát cuối: </span>
                          <span>{formatMonitorDateTime(detail.item.last_observed_at_ms)}</span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="p-8 text-center text-xs text-rose-300">
                  {detailError || 'Không tìm thấy dữ liệu chi tiết của cơ hội này.'}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
