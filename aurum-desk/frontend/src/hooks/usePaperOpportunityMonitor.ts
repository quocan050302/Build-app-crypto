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
import { buildMonitorTimeWindow } from '../utils/paperOpportunityText';
import type {
  OpportunityMonitorSummary,
  MonitorListPage,
  OpportunityMonitorDetail,
  MonitorFilterState,
  ResolvedMonitorQuery,
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
  resolvedQuery: ResolvedMonitorQuery | null;
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
  const [resolvedQuery, setResolvedQuery] = useState<ResolvedMonitorQuery | null>(null);

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
  const pageAbortControllerRef = useRef<AbortController | null>(null);

  // Fetch Summary and List (Page 1)
  const fetchOverviewData = useCallback(async () => {
    if (!open) return;

    // Abort previous overview and page requests
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    if (pageAbortControllerRef.current) {
      pageAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const currentGen = ++generationRef.current;
    const { start_ms: startMs, end_ms: endMs } = buildMonitorTimeWindow(filters, 'Asia/Ho_Chi_Minh');

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

        const effectiveRunId =
          sumRes?.run_id ||
          listRes?.run_id ||
          (sumRes?.coverage?.run_id ?? null) ||
          runId ||
          null;
        const effectiveQueryKey = sumRes?.query_key || listRes?.query_key || null;

        const resolved: ResolvedMonitorQuery = {
          symbol,
          scope,
          run_id: effectiveRunId,
          start_ms: startMs,
          end_ms: endMs,
          session_id: filters.session !== 'all' ? filters.session : null,
          model_id: null,
          query_key: effectiveQueryKey,
        };
        setResolvedQuery(resolved);
      }
    } catch (err: any) {
      if (!controller.signal.aborted && currentGen === generationRef.current) {
        setListError(extractErrorMessage(err));
      }
    } finally {
      if (currentGen === generationRef.current) {
        setIsSummaryLoading(false);
        setIsListLoading(false);
      }
    }
  }, [open, symbol, scope, runId, filters]);

  // Load next page
  const loadNextPage = useCallback(async () => {
    if (!open || !page?.has_more || !page?.next_cursor || isListLoading) return;

    if (pageAbortControllerRef.current) {
      pageAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    pageAbortControllerRef.current = controller;

    const currentGen = generationRef.current;
    setIsListLoading(true);

    try {
      const nextPage = await api.getOpportunityMonitorOpportunities(
        {
          symbol,
          scope,
          run_id: resolvedQuery?.run_id || runId || undefined,
          start_ms: resolvedQuery?.start_ms ?? undefined,
          end_ms: resolvedQuery?.end_ms ?? undefined,
          session_id: resolvedQuery?.session_id ?? (filters.session !== 'all' ? filters.session : undefined),
          model_id: resolvedQuery?.model_id ?? undefined,
          limit: 20,
          cursor: page.next_cursor,
        },
        controller.signal
      );

      if (currentGen === generationRef.current && !controller.signal.aborted) {
        setPage((prev) => {
          if (!prev) return nextPage;
          const existingIds = new Set(prev.items.map((i) => i.opportunity_id));
          const freshItems = nextPage.items.filter((i) => !existingIds.has(i.opportunity_id));
          return {
            ...nextPage,
            items: [...prev.items, ...freshItems],
          };
        });
      }
    } catch (err: any) {
      if (!controller.signal.aborted && currentGen === generationRef.current) {
        setListError(extractErrorMessage(err, 'Lỗi tải trang tiếp theo'));
      }
    } finally {
      if (currentGen === generationRef.current) {
        setIsListLoading(false);
      }
    }
  }, [open, page, isListLoading, symbol, scope, runId, filters.session, resolvedQuery]);

  // Fetch detail for selected opportunity
  const fetchDetail = useCallback(
    async (id: string) => {
      if (!open || !id) return;

      if (detailAbortControllerRef.current) {
        detailAbortControllerRef.current.abort();
      }
      const controller = new AbortController();
      detailAbortControllerRef.current = controller;

      const currentGen = generationRef.current;
      setIsDetailLoading(true);
      setDetailError(null);

      try {
        const res = await api.getOpportunityMonitorDetail(
          id,
          {
            symbol,
            scope,
            run_id: resolvedQuery?.run_id || runId || undefined,
            start_ms: resolvedQuery?.start_ms ?? undefined,
            end_ms: resolvedQuery?.end_ms ?? undefined,
            session_id: resolvedQuery?.session_id ?? (filters.session !== 'all' ? filters.session : undefined),
            model_id: resolvedQuery?.model_id ?? undefined,
          },
          controller.signal
        );

        if (!controller.signal.aborted && currentGen === generationRef.current) {
          setDetail(res);
        }
      } catch (err: any) {
        if (!controller.signal.aborted && currentGen === generationRef.current) {
          setDetailError(extractErrorMessage(err, 'Lỗi tải chi tiết cơ hội'));
        }
      } finally {
        if (currentGen === generationRef.current) {
          setIsDetailLoading(false);
        }
      }
    },
    [open, symbol, scope, runId, filters.session, resolvedQuery]
  );

  // Trigger overview fetch on open or filter change
  useEffect(() => {
    if (open) {
      fetchOverviewData();
    } else {
      // Abort when closed
      if (abortControllerRef.current) abortControllerRef.current.abort();
      if (detailAbortControllerRef.current) detailAbortControllerRef.current.abort();
      if (pageAbortControllerRef.current) pageAbortControllerRef.current.abort();
      setDetail(null);
      setResolvedQuery(null);
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
    resolvedQuery,
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
