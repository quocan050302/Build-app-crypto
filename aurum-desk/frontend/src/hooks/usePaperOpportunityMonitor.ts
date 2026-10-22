/**
 * frontend/src/hooks/usePaperOpportunityMonitor.ts
 * Aurum V17.0 — Fetch lifecycle hook for Paper Opportunity Monitor.
 *
 * Rules 131 - 140:
 * - Only fetches when open is true.
 * - Zero polling when closed or when viewing historical dates.
 * - Auto-refresh maximum once every 30s when open, document is visible, and viewing CURRENT (today).
 * - Independent AbortController and generation counters to avoid race conditions.
 * - Does NOT subscribe to websocket or refetch on price ticks.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { api, extractErrorMessage } from '../api/client';
import type {
  OpportunityMonitorSummary,
  MonitorListPage,
  OpportunityMonitorDetail,
  MonitorFilterState,
} from '../types/paperOpportunityMonitor';

export interface UsePaperOpportunityMonitorOptions {
  open: boolean;
  symbol?: string;
  scope?: string;
  runId?: string | null;
  filters: MonitorFilterState;
  selectedOpportunityId?: string | null;
}

export interface UsePaperOpportunityMonitorResult {
  summary: OpportunityMonitorSummary | null;
  page: MonitorListPage | null;
  detail: OpportunityMonitorDetail | null;
  isSummaryLoading: boolean;
  isListLoading: boolean;
  isDetailLoading: boolean;
  summaryError: string | null;
  listError: string | null;
  detailError: string | null;
  refresh: () => Promise<void>;
  loadNextPage: () => Promise<void>;
  selectOpportunity: (id: string | null) => void;
}

export function usePaperOpportunityMonitor({
  open,
  symbol = 'XAUUSDT',
  scope = 'CG01',
  runId = null,
  filters,
  selectedOpportunityId: initialSelectedId = null,
}: UsePaperOpportunityMonitorOptions): UsePaperOpportunityMonitorResult {
  const [summary, setSummary] = useState<OpportunityMonitorSummary | null>(null);
  const [page, setPage] = useState<MonitorListPage | null>(null);
  const [detail, setDetail] = useState<OpportunityMonitorDetail | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(initialSelectedId);

  const [isSummaryLoading, setIsSummaryLoading] = useState(false);
  const [isListLoading, setIsListLoading] = useState(false);
  const [isDetailLoading, setIsDetailLoading] = useState(false);

  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Sync selectedId if controlled prop changes
  useEffect(() => {
    setSelectedId(initialSelectedId);
  }, [initialSelectedId]);

  // Generation counter to eliminate out-of-order race conditions
  const generationRef = useRef(0);
  const abortControllerRef = useRef<AbortController | null>(null);
  const detailAbortControllerRef = useRef<AbortController | null>(null);

  // Derive time window from filters
  const computeTimeWindow = useCallback(() => {
    const now = Date.now();
    let startMs: number | null = null;
    let endMs: number | null = null;

    if (filters.period === 'today') {
      // Start of today in Asia/Ho_Chi_Minh
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      startMs = d.getTime();
      endMs = now;
    } else if (filters.period === '7d') {
      startMs = now - 7 * 24 * 60 * 60 * 1000;
      endMs = now;
    } else if (filters.period === 'custom' && filters.customStartDate) {
      startMs = new Date(filters.customStartDate).getTime();
      if (filters.customEndDate) {
        endMs = new Date(filters.customEndDate).getTime() + 24 * 60 * 60 * 1000 - 1;
      }
    }

    return { startMs, endMs };
  }, [filters.period, filters.customStartDate, filters.customEndDate]);

  // Fetch Summary and List (Page 1)
  const fetchOverviewData = useCallback(async () => {
    if (!open) return;

    // Abort previous overview requests
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const currentGen = ++generationRef.current;
    const { startMs, endMs } = computeTimeWindow();

    setIsSummaryLoading(true);
    setIsListLoading(true);
    setSummaryError(null);
    setListError(null);

    const queryParams: any = {
      symbol,
      scope,
      run_id: runId || undefined,
      start_ms: startMs || undefined,
      end_ms: endMs || undefined,
    };

    if (filters.session !== 'all') {
      queryParams.session_id = filters.session;
    }

    // Parallel fetch summary and page 1
    try {
      const summaryPromise = api.getOpportunityMonitorSummary(queryParams, controller.signal);
      const listPromise = api.getOpportunityMonitorOpportunities(
        { ...queryParams, limit: 20 },
        controller.signal
      );

      const [sumRes, listRes] = await Promise.all([
        summaryPromise.catch((err) => {
          if (controller.signal.aborted) throw err;
          setSummaryError(extractErrorMessage(err, 'Lỗi tải tóm tắt cơ hội'));
          return null;
        }),
        listPromise.catch((err) => {
          if (controller.signal.aborted) throw err;
          setListError(extractErrorMessage(err, 'Lỗi tải danh sách cơ hội'));
          return null;
        }),
      ]);

      if (currentGen === generationRef.current && !controller.signal.aborted) {
        if (sumRes) setSummary(sumRes);
        if (listRes) setPage(listRes);
      }
    } catch (err: any) {
      if (!controller.signal.aborted) {
        setListError(extractErrorMessage(err));
      }
    } finally {
      if (currentGen === generationRef.current) {
        setIsSummaryLoading(false);
        setIsListLoading(false);
      }
    }
  }, [open, symbol, scope, runId, filters.session, computeTimeWindow]);

  // Load next page
  const loadNextPage = useCallback(async () => {
    if (!open || !page?.has_more || !page?.next_cursor || isListLoading) return;

    const controller = new AbortController();
    const { startMs, endMs } = computeTimeWindow();

    setIsListLoading(true);
    try {
      const nextPage = await api.getOpportunityMonitorOpportunities(
        {
          symbol,
          scope,
          run_id: runId || undefined,
          start_ms: startMs || undefined,
          end_ms: endMs || undefined,
          session_id: filters.session !== 'all' ? filters.session : undefined,
          limit: 20,
          cursor: page.next_cursor,
        },
        controller.signal
      );

      setPage((prev) => {
        if (!prev) return nextPage;
        return {
          ...nextPage,
          items: [...prev.items, ...nextPage.items],
        };
      });
    } catch (err: any) {
      setListError(extractErrorMessage(err, 'Lỗi tải trang tiếp theo'));
    } finally {
      setIsListLoading(false);
    }
  }, [open, page, isListLoading, symbol, scope, runId, filters.session, computeTimeWindow]);

  // Fetch detail for selected opportunity
  const fetchDetail = useCallback(async (id: string) => {
    if (!open || !id) return;

    if (detailAbortControllerRef.current) {
      detailAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    detailAbortControllerRef.current = controller;

    setIsDetailLoading(true);
    setDetailError(null);

    try {
      const res = await api.getOpportunityMonitorDetail(
        id,
        {
          symbol,
          scope,
          run_id: runId || undefined,
        },
        controller.signal
      );

      if (!controller.signal.aborted) {
        setDetail(res);
      }
    } catch (err: any) {
      if (!controller.signal.aborted) {
        setDetailError(extractErrorMessage(err, 'Lỗi tải chi tiết cơ hội'));
      }
    } finally {
      setIsDetailLoading(false);
    }
  }, [open, symbol, scope, runId]);

  // Trigger overview fetch on open or filter change
  useEffect(() => {
    if (open) {
      fetchOverviewData();
    } else {
      // Abort when closed
      if (abortControllerRef.current) abortControllerRef.current.abort();
      if (detailAbortControllerRef.current) detailAbortControllerRef.current.abort();
      setDetail(null);
    }
  }, [open, fetchOverviewData]);

  // Trigger detail fetch when selectedId changes
  useEffect(() => {
    if (open && selectedId) {
      fetchDetail(selectedId);
    } else {
      setDetail(null);
      setDetailError(null);
    }
  }, [open, selectedId, fetchDetail]);

  // Controlled 30-second cadence refresh ONLY when open, visible, and viewing today
  useEffect(() => {
    if (!open || filters.period !== 'today') return;

    const timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      fetchOverviewData();
    }, 30000);

    return () => clearInterval(timer);
  }, [open, filters.period, fetchOverviewData]);

  const selectOpportunity = useCallback((id: string | null) => {
    setSelectedId(id);
  }, []);

  return {
    summary,
    page,
    detail,
    isSummaryLoading,
    isListLoading,
    isDetailLoading,
    summaryError,
    listError,
    detailError,
    refresh: fetchOverviewData,
    loadNextPage,
    selectOpportunity,
  };
}
