/**
 * frontend/src/hooks/usePaperOpportunityMonitor.ts
 * Aurum V17.3 — Fetch lifecycle hook for Paper Opportunity Monitor.
 *
 * Rules & Contracts:
 * - Single Context Owner: Hook owns current context snapshot (epoch, status, bound query).
 * - Sequential Overview Bootstrap: summary bootstrap -> pin context -> list with explicit run/window -> verify -> publish pair.
 * - Strict Response & Identity Validation: detail response must match bound context and requested opportunity_id.
 * - Immediate Action Invalidation: changing filters, refreshing, or closing invalidates action contexts.
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
  assertMonitorDetailIdentity,
  captureMonitorRequestLease,
  isMonitorRequestCurrent,
} from '../utils/paperOpportunityText';
import type {
  OpportunityMonitorSummary,
  MonitorListPage,
  OpportunityMonitorDetail,
  MonitorFilterState,
  ResolvedMonitorQuery,
  MonitorQueryParams,
  MonitorContextSnapshot,
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
  monitorContext: MonitorContextSnapshot;
  getCurrentMonitorContext: () => MonitorContextSnapshot;
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

  // Single Context Owner (Phần 011 - 020, C1 - C5)
  const initialContext: MonitorContextSnapshot = { status: 'CLOSED', epoch: 0, query: null };
  const contextRef = useRef<MonitorContextSnapshot>(initialContext);
  const [monitorContext, setMonitorContext] = useState<MonitorContextSnapshot>(initialContext);

  const getCurrentMonitorContext = useCallback(() => contextRef.current, []);

  const publishMonitorContext = useCallback((next: MonitorContextSnapshot) => {
    contextRef.current = next;
    if (mountedRef.current) {
      setMonitorContext(next);
    }
  }, []);

  const invalidateMonitorContext = useCallback(
    (status: 'LOADING' | 'UNAVAILABLE' | 'ERROR' | 'CLOSED', reason?: string) => {
      const nextEpoch = contextRef.current.epoch + 1;
      const nextSnapshot: MonitorContextSnapshot = {
        status,
        epoch: nextEpoch,
        query: null,
        reason,
      };
      contextRef.current = nextSnapshot;
      if (mountedRef.current) {
        setMonitorContext(nextSnapshot);
      }

      // Invalidate detail in-flight
      detailSequenceRef.current += 1;
      if (detailAbortControllerRef.current) {
        detailAbortControllerRef.current.abort();
        detailAbortControllerRef.current = null;
      }
      setDetail(null);
      setDetailError(null);
      setIsDetailLoading(false);

      // Invalidate page in-flight
      pageSequenceRef.current += 1;
      if (pageAbortControllerRef.current) {
        pageAbortControllerRef.current.abort();
        pageAbortControllerRef.current = null;
      }
      setIsListLoading(false);

      return nextEpoch;
    },
    []
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      contextRef.current = { status: 'CLOSED', epoch: contextRef.current.epoch + 1, query: null };
      overviewAbortRef.current?.abort();
      detailAbortControllerRef.current?.abort();
      pageAbortControllerRef.current?.abort();
    };
  }, []);

  // Fetch Summary and List (Page 1) sequentially
  const fetchOverviewData = useCallback(async () => {
    if (!open) return;

    // Abort previous overview request
    if (overviewAbortRef.current) {
      overviewAbortRef.current.abort();
    }
    const controller = new AbortController();
    overviewAbortRef.current = controller;

    const currentGen = ++overviewSequenceRef.current;
    const epoch = invalidateMonitorContext('LOADING');

    const { start_ms: startMs, end_ms: endMs } = buildMonitorTimeWindow(filters, 'Asia/Ho_Chi_Minh');

    setIsSummaryLoading(true);
    setIsListLoading(true);
    setSummaryError(null);
    setListError(null);

    const isCurrent = () =>
      mountedRef.current &&
      !controller.signal.aborted &&
      currentGen === overviewSequenceRef.current &&
      contextRef.current.epoch === epoch;

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
        publishMonitorContext({ status: 'UNAVAILABLE', epoch, query: null, reason: resolution.reason });
        return;
      }

      const q = resolution.query;
      const readyCandidate: MonitorContextSnapshot = { status: 'READY', epoch, query: q };
      if (!captureMonitorRequestLease(readyCandidate)) {
        throw new Error('Phạm vi dữ liệu chưa được xác minh đầy đủ. Vui lòng tải lại.');
      }

      if (resolution.status === 'EMPTY') {
        const emptyPage: MonitorListPage = {
          items: [],
          total: 0,
          has_more: false,
          next_cursor: null,
          snapshot_as_of_ms: nextSummary.generated_at_ms,
          limitations: [],
          query_key: q.query_key,
          run_id: q.run_id,
        };
        setSummary(nextSummary);
        setPage(emptyPage);
        setResolvedQuery(q);
        publishMonitorContext(readyCandidate);
        return;
      }

      // Step 3: Fetch page 1 with pinned run & window
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
      publishMonitorContext(readyCandidate);
    } catch (err: any) {
      if (isCurrent()) {
        const msg = extractErrorMessage(err, 'Lỗi tải dữ liệu cơ hội');
        setListError(msg);
        publishMonitorContext({ status: 'ERROR', epoch, query: null, reason: msg });
      }
    } finally {
      if (isCurrent()) {
        setIsSummaryLoading(false);
        setIsListLoading(false);
      }
    }
  }, [open, symbol, scope, runId, filters, invalidateMonitorContext, publishMonitorContext]);

  // Load next page
  const loadNextPage = useCallback(async () => {
    const lease = captureMonitorRequestLease(contextRef.current);
    if (!open || !page?.has_more || !page?.next_cursor || isListLoading || !lease) return;

    if (pageAbortControllerRef.current) {
      pageAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    pageAbortControllerRef.current = controller;

    const currentGen = ++pageSequenceRef.current;
    setIsListLoading(true);

    const isCurrent = () =>
      mountedRef.current &&
      !controller.signal.aborted &&
      currentGen === pageSequenceRef.current &&
      isMonitorRequestCurrent(contextRef.current, lease);

    try {
      const nextPage = await api.getOpportunityMonitorOpportunities(
        {
          ...toMonitorRequestParams(lease.query),
          limit: 20,
          cursor: page.next_cursor,
        },
        controller.signal
      );

      if (!isCurrent()) return;

      assertMonitorResponseContext(nextPage, lease.query, 'LIST');

      setPage((prev) => {
        if (!prev) return nextPage;
        const existingIds = new Set(prev.items.map((i) => i.opportunity_id));
        const freshItems = nextPage.items.filter((i) => !existingIds.has(i.opportunity_id));
        return {
          ...nextPage,
          items: [...prev.items, ...freshItems],
        };
      });
    } catch (err: any) {
      if (isCurrent()) {
        setListError(extractErrorMessage(err, 'Lỗi tải trang tiếp theo'));
      }
    } finally {
      if (isCurrent()) {
        setIsListLoading(false);
      }
      if (pageAbortControllerRef.current === controller) {
        pageAbortControllerRef.current = null;
      }
    }
  }, [open, page, isListLoading]);

  // Fetch detail for selected opportunity (Phần 041 - 050, D1)
  const fetchDetail = useCallback(
    async (id: string) => {
      const lease = captureMonitorRequestLease(contextRef.current);
      if (!open || !id || !lease || id !== selectedIdRef.current) return;

      if (detailAbortControllerRef.current) {
        detailAbortControllerRef.current.abort();
      }
      const controller = new AbortController();
      detailAbortControllerRef.current = controller;

      const sequence = ++detailSequenceRef.current;
      const capturedId = id;

      const isCurrent = () =>
        mountedRef.current &&
        !controller.signal.aborted &&
        sequence === detailSequenceRef.current &&
        selectedIdRef.current === capturedId &&
        isMonitorRequestCurrent(contextRef.current, lease);

      setDetail(null);
      setDetailError(null);
      setIsDetailLoading(true);

      try {
        const res = await api.getOpportunityMonitorDetail(
          capturedId,
          toMonitorRequestParams(lease.query),
          controller.signal
        );

        if (!isCurrent()) return;

        assertMonitorResponseContext(res, lease.query, 'DETAIL');
        assertMonitorDetailIdentity(res, lease.query, capturedId);

        if (!isCurrent()) return;

        setDetail(res);
      } catch (err: any) {
        if (isCurrent()) {
          setDetail(null);
          setDetailError(extractErrorMessage(err, 'Dữ liệu không khớp cơ hội đang chọn. Vui lòng tải lại.'));
        }
      } finally {
        if (isCurrent()) {
          setIsDetailLoading(false);
        }
        if (detailAbortControllerRef.current === controller) {
          detailAbortControllerRef.current = null;
        }
      }
    },
    [open]
  );

  // Trigger overview fetch on open or filter change
  useEffect(() => {
    if (open) {
      fetchOverviewData();
    } else {
      invalidateMonitorContext('CLOSED');
      if (overviewAbortRef.current) overviewAbortRef.current.abort();
      setSummary(null);
      setPage(null);
      setResolvedQuery(null);
    }
  }, [open, fetchOverviewData, invalidateMonitorContext]);

  // Trigger detail fetch when selectedId changes or new context becomes READY
  useEffect(() => {
    if (open && selectedId && monitorContext.status === 'READY') {
      fetchDetail(selectedId);
    } else if (!selectedId) {
      setDetail(null);
      setDetailError(null);
    }
  }, [open, selectedId, monitorContext.status, monitorContext.epoch, fetchDetail]);

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
    monitorContext,
    getCurrentMonitorContext,
  };
}
