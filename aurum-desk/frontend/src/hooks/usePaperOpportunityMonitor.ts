/**
 * frontend/src/hooks/usePaperOpportunityMonitor.ts
 * Aurum V17.2 — Fetch lifecycle hook for Paper Opportunity Monitor.
 *
 * Rules & Contracts:
 * - Sequential Overview Bootstrap: summary bootstrap -> pin context -> list with explicit run/window -> verify -> publish pair.
 * - Independent generations and abort controllers for overview, detail, and page pagination.
 * - Selection cancellation immediately aborts detail and resets detail loading/error.
 * - Only fetches when open is true; unmount cleanly cancels all in-flight work.
 * - Cadence refresh (30s) strictly bounded to today, open, and document visible.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { api, extractErrorMessage } from '../api/client';
import {
  buildMonitorTimeWindow,
  asResolvedMonitorQuery,
  toMonitorRequestParams,
  assertMonitorResponseContext,
} from '../utils/paperOpportunityText';
import type {
  OpportunityMonitorSummary,
  MonitorListPage,
  OpportunityMonitorDetail,
  MonitorFilterState,
  ResolvedMonitorQuery,
  MonitorQueryParams,
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
  const selectedIdRef = useRef<string | null>(initialSelectedId);

  const [isSummaryLoading, setIsSummaryLoading] = useState(false);
  const [isListLoading, setIsListLoading] = useState(false);
  const [isDetailLoading, setIsDetailLoading] = useState(false);

  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Sync selectedId if controlled prop changes
  useEffect(() => {
    setSelectedId(initialSelectedId);
    selectedIdRef.current = initialSelectedId;
  }, [initialSelectedId]);

  // Lifecycle refs
  const mountedRef = useRef(true);
  const overviewSequenceRef = useRef(0);
  const detailSequenceRef = useRef(0);
  const pageSequenceRef = useRef(0);

  const overviewAbortRef = useRef<AbortController | null>(null);
  const detailAbortControllerRef = useRef<AbortController | null>(null);
  const pageAbortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      overviewAbortRef.current?.abort();
      detailAbortControllerRef.current?.abort();
      pageAbortControllerRef.current?.abort();
    };
  }, []);

  // Fetch Summary and List (Page 1) sequentially
  const fetchOverviewData = useCallback(async () => {
    if (!open) return;

    // Abort previous overview and page requests
    if (overviewAbortRef.current) {
      overviewAbortRef.current.abort();
    }
    if (pageAbortControllerRef.current) {
      pageAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    overviewAbortRef.current = controller;

    const currentGen = ++overviewSequenceRef.current;
    const { start_ms: startMs, end_ms: endMs } = buildMonitorTimeWindow(filters, 'Asia/Ho_Chi_Minh');

    setIsSummaryLoading(true);
    setIsListLoading(true);
    setSummaryError(null);
    setListError(null);

    const isCurrent = () =>
      mountedRef.current &&
      !controller.signal.aborted &&
      currentGen === overviewSequenceRef.current;

    const requestedParams: MonitorQueryParams = {
      symbol,
      scope,
      run_id: runId || undefined,
      start_ms: startMs || undefined,
      end_ms: endMs || undefined,
    };

    if (filters.session !== 'all') {
      requestedParams.session_id = filters.session;
    }

    try {
      // Step 1: Bootstrap summary
      const nextSummary = await api.getOpportunityMonitorSummary(requestedParams, controller.signal);
      if (!isCurrent()) return;

      // Step 2: Resolve effective scope
      const resolution = asResolvedMonitorQuery(nextSummary, requestedParams);
      if (resolution.status === 'UNAVAILABLE') {
        setSummary(nextSummary);
        setPage(null);
        setResolvedQuery(null);
        setSummaryError(resolution.message_vi);
        return;
      }

      if (resolution.status === 'EMPTY') {
        const q = resolution.query;
        setSummary(nextSummary);
        setPage({
          items: [],
          total: 0,
          has_more: false,
          next_cursor: null,
          snapshot_as_of_ms: nextSummary.generated_at_ms,
          limitations: [],
          query_key: q.query_key,
          run_id: q.run_id,
        });
        setResolvedQuery(q);
        return;
      }

      // Step 3: Fetch page 1 with pinned run & window
      const q = resolution.query;
      const listParams = {
        ...toMonitorRequestParams(q),
        limit: 20,
      };

      const nextListPage = await api.getOpportunityMonitorOpportunities(listParams, controller.signal);
      if (!isCurrent()) return;

      assertMonitorResponseContext(nextListPage, q, 'LIST');

      // Step 4: Publish pair atomically
      setSummary(nextSummary);
      setPage(nextListPage);
      setResolvedQuery(q);
      setSummaryError(null);
      setListError(null);
    } catch (err: any) {
      if (isCurrent()) {
        const msg = extractErrorMessage(err, 'Lỗi tải dữ liệu cơ hội');
        setListError(msg);
      }
    } finally {
      if (isCurrent()) {
        setIsSummaryLoading(false);
        setIsListLoading(false);
      }
    }
  }, [open, symbol, scope, runId, filters]);

  // Load next page
  const loadNextPage = useCallback(async () => {
    if (!open || !page?.has_more || !page?.next_cursor || isListLoading || !resolvedQuery) return;

    if (pageAbortControllerRef.current) {
      pageAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    pageAbortControllerRef.current = controller;

    const currentGen = ++pageSequenceRef.current;
    const capturedQueryKey = resolvedQuery.query_key;
    setIsListLoading(true);

    try {
      const nextPage = await api.getOpportunityMonitorOpportunities(
        {
          ...toMonitorRequestParams({
            ...resolvedQuery,
            run_id: resolvedQuery.run_id || '',
            start_ms: resolvedQuery.start_ms || 0,
            end_ms: resolvedQuery.end_ms || 0,
            query_key: resolvedQuery.query_key || '',
          }),
          limit: 20,
          cursor: page.next_cursor,
        },
        controller.signal
      );

      if (
        mountedRef.current &&
        !controller.signal.aborted &&
        currentGen === pageSequenceRef.current &&
        resolvedQuery.query_key === capturedQueryKey
      ) {
        if (resolvedQuery.run_id) {
          assertMonitorResponseContext(
            nextPage,
            {
              ...resolvedQuery,
              run_id: resolvedQuery.run_id,
              start_ms: resolvedQuery.start_ms || 0,
              end_ms: resolvedQuery.end_ms || 0,
              query_key: resolvedQuery.query_key || '',
            },
            'LIST'
          );
        }
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
      if (mountedRef.current && !controller.signal.aborted && currentGen === pageSequenceRef.current) {
        setListError(extractErrorMessage(err, 'Lỗi tải trang tiếp theo'));
      }
    } finally {
      if (mountedRef.current && currentGen === pageSequenceRef.current) {
        setIsListLoading(false);
      }
    }
  }, [open, page, isListLoading, resolvedQuery]);

  // Fetch detail for selected opportunity
  const fetchDetail = useCallback(
    async (id: string) => {
      if (!open || !id) return;

      if (detailAbortControllerRef.current) {
        detailAbortControllerRef.current.abort();
      }
      const controller = new AbortController();
      detailAbortControllerRef.current = controller;

      const currentGen = ++detailSequenceRef.current;
      setIsDetailLoading(true);
      setDetailError(null);

      try {
        const detailParams: MonitorQueryParams = {
          symbol,
          scope,
          run_id: resolvedQuery?.run_id || runId || undefined,
          start_ms: resolvedQuery?.start_ms ?? undefined,
          end_ms: resolvedQuery?.end_ms ?? undefined,
          session_id: resolvedQuery?.session_id ?? (filters.session !== 'all' ? filters.session : undefined),
          model_id: resolvedQuery?.model_id ?? undefined,
        };

        const res = await api.getOpportunityMonitorDetail(id, detailParams, controller.signal);

        if (
          mountedRef.current &&
          !controller.signal.aborted &&
          currentGen === detailSequenceRef.current &&
          id === selectedIdRef.current
        ) {
          setDetail(res);
        }
      } catch (err: any) {
        if (
          mountedRef.current &&
          !controller.signal.aborted &&
          currentGen === detailSequenceRef.current &&
          id === selectedIdRef.current
        ) {
          setDetailError(extractErrorMessage(err, 'Lỗi tải chi tiết cơ hội'));
        }
      } finally {
        if (
          mountedRef.current &&
          currentGen === detailSequenceRef.current &&
          id === selectedIdRef.current
        ) {
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
      if (overviewAbortRef.current) overviewAbortRef.current.abort();
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
    selectedIdRef.current = id;
    setSelectedId(id);
    if (!id) {
      detailSequenceRef.current += 1;
      if (detailAbortControllerRef.current) {
        detailAbortControllerRef.current.abort();
        detailAbortControllerRef.current = null;
      }
      setDetail(null);
      setDetailError(null);
      setIsDetailLoading(false);
    }
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
