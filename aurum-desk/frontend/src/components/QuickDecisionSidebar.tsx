import React, { useState, useEffect } from 'react';
import type { SelectedTradeIntent } from '../App';
import { SMCScenarioCard } from './SMCScenarioCard';
import { SMCSessionForecastCard } from './SMCSessionForecastCard';
import { CG01DiagnosticCard } from './CG01DiagnosticCard';
import { CandidateScoreBadge } from './CandidateScoreBadge';
import { useCG01RealtimeState } from '../hooks/useCG01RealtimeState';
import type { SMCScenarioView } from '../types/smcScenario';
import {
  ShieldAlert,
  PlayCircle,
  XCircle,
  ChevronRight,
  Layers,
  BookOpen,
  ListOrdered,
  AlertTriangle,
  CheckCircle2
} from 'lucide-react';
import { quoteStore } from '../services/quoteStore';
import { getCatalogTemplate } from '../utils/userMessageCatalog';
import { normalizeLessonItem } from '../types/lesson';
import { resolveSelectionRrPolicy, compareNetRr } from '../utils/rrPolicyView';
import {
  deriveEntryDecisionView,
  getEntryDistanceLabel,
  isEligibilityBoundToIntent,
} from '../utils/entryGate';
import type { EntryEligibility, PlanKind } from '../utils/entryGate';

export interface StalePlanNoticeData {
  setupId: string;
  oldRevision: number;
  newRevision: number;
  newDirection: string;
  newInstanceId: string;
}

export type DrawerSectionType = 'conditions' | 'lessons' | 'upcoming' | 'risk' | 'session' | 'quality';

export interface QuickDecisionSidebarProps {
  selectedIntent: SelectedTradeIntent | null;
  activePosition: any;
  hasArmedOrder: boolean;
  upcomingSetups: any[];
  stalePlanNotice: StalePlanNoticeData | null;
  onClearStalePlanNotice: () => void;
  onFocusSetupOnChart: (setup: any) => void;
  onArmSetup: (setupId: string, direction: 'LONG' | 'SHORT', instanceId?: string, revision?: number) => void;
  onCancelSetup: (setupId: string) => void;
  onClosePosition: () => void;
  onOpenMarket?: () => void;
  onFollowLatestSignal?: () => void;
  onOpenDetailsDrawer: (section: DrawerSectionType) => void;
  analysis: any;
  leverage: number;
  marginMode: string;
  isActionLoading?: boolean;
  selectedQualitySnapshot?: any;
  selectedQualityLoading?: boolean;
  selectedQualityError?: string | null;
  onFocusOnChart?: () => void;
  scenarios?: SMCScenarioView[];
  onSelectScenario?: (scenario: SMCScenarioView) => void;
  onFocusTradeOnChart?: (trade: any) => void;
  appliedMinNetRR?: number;
  rrPolicySource?: string;
  rrPolicyFingerprint?: string;
  rrPolicyStatus?: string;
  isRrPolicyConfirmed?: boolean;
  onOpenRrSettings?: () => void;
}

export const QuickDecisionSidebar: React.FC<QuickDecisionSidebarProps> = ({
  selectedIntent,
  activePosition,
  hasArmedOrder,
  upcomingSetups,
  stalePlanNotice,
  onClearStalePlanNotice,
  onFocusSetupOnChart,
  onFocusTradeOnChart,
  onArmSetup,
  onCancelSetup,
  onClosePosition,
  onOpenMarket: _onOpenMarket,
  onFollowLatestSignal,
  onOpenDetailsDrawer,
  analysis: _analysis,
  leverage,
  marginMode,
  isActionLoading = false,
  selectedQualitySnapshot,
  selectedQualityLoading = false,
  selectedQualityError: _selectedQualityError,
  onFocusOnChart,
  scenarios,
  onSelectScenario,
  appliedMinNetRR,
  rrPolicySource,
  rrPolicyFingerprint,
  rrPolicyStatus,
  isRrPolicyConfirmed,
  onOpenRrSettings,
}) => {
  // Realtime CG-01 diagnostics state (V16.29)
  const cg01State = useCG01RealtimeState({ symbol: 'XAUUSDT' });

  // Realtime quote subscriber for live distance & freshness
  const [currentQuote, setCurrentQuote] = useState(() => quoteStore.getQuote('XAUUSDT'));

  // Coarse clock used only for quote freshness labels (no extra WS connection, no refetch).
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const unsub = quoteStore.subscribe('XAUUSDT', (q) => {
      if (q) {
        setCurrentQuote(q);
        setNowMs(Date.now());
      }
    });
    const timer = setInterval(() => setNowMs(Date.now()), 5000);
    return () => {
      unsub();
      clearInterval(timer);
    };
  }, []);

  const hasActivePos = Boolean(activePosition?.has_active_position && activePosition?.position);
  const pos = activePosition?.position;

  // Find matching watch setup from list
  const currentSetup = selectedIntent
    ? upcomingSetups.find((s: any) => s.id === selectedIntent.setup_id)
    : null;

  // V14.2: cached eligibility is only valid for the exact plan it was computed for.
  // A newer server setup (other version/direction/prices) or a dragged draft is NOT bound -> stale.
  const intentEligibility = selectedIntent?.eligibility as EntryEligibility | undefined;
  const serverEligibility = currentSetup?.eligibility as EntryEligibility | undefined;
  const eligibility: EntryEligibility | undefined = isEligibilityBoundToIntent(intentEligibility, selectedIntent)
    ? intentEligibility
    : isEligibilityBoundToIntent(serverEligibility, selectedIntent)
    ? serverEligibility
    : undefined;
  const isEligibilityStale = Boolean(
    selectedIntent && selectedIntent.source === 'WATCH_SETUP' && selectedIntent.status !== 'ARMED' && !eligibility,
  );
  const planKind: PlanKind = (selectedIntent?.plan_kind as PlanKind | undefined) ?? 'CONFIRMED';
  const isQuoteFresh = Boolean(currentQuote && nowMs - (currentQuote.updatedAt || 0) <= 15000);

  // Geometry Validation
  const tp = selectedIntent?.takeProfit;
  const isGeometryValid =
    selectedIntent != null &&
    tp != null &&
    (selectedIntent.direction === 'LONG'
      ? selectedIntent.stopLoss < selectedIntent.plannedEntry && selectedIntent.plannedEntry < tp
      : tp < selectedIntent.plannedEntry && selectedIntent.plannedEntry < selectedIntent.stopLoss);

  // Blocker conditions evaluation
  const isCrossBlocked = marginMode === 'CROSS' || selectedIntent?.marginMode === 'CROSS';
  const isBlockedByActive = hasActivePos;
  const isBlockedByArmed = hasArmedOrder && selectedIntent?.status !== 'ARMED';
  const isEligibilityBlocked = Boolean(eligibility && (eligibility.can_arm === false || eligibility.eligible === false));

  const blockers: Array<{ code: string; title: string; summary: string }> = [];

  if (selectedIntent) {
    if (!selectedIntent.takeProfit) {
      blockers.push({
        code: 'MISSING_TP',
        title: 'Chưa có giá Chốt Lời (Take Profit)',
        summary: 'Cần có mức Take Profit để tính toán tỷ lệ R:R và bảo vệ lợi nhuận tự động.',
      });
    } else if (!isGeometryValid) {
      const geoTitle =
        selectedIntent.direction === 'SHORT'
          ? 'SHORT không hợp lệ: TP < Entry < SL'
          : 'LONG không hợp lệ: SL < Entry < TP';
      blockers.push({
        code: 'INVALID_PRICE_GEOMETRY',
        title: geoTitle,
        summary: `Mức giá hiện tại (Entry $${selectedIntent.plannedEntry}, SL $${selectedIntent.stopLoss}, TP $${selectedIntent.takeProfit}) vi phạm hình học vị thế ${selectedIntent.direction}.`,
      });
    }

    // Signed R:R: zero and negative values are real results (never hidden by `||`/`> 0`).
    const currentNetRRRaw = selectedIntent.estimatedNetRR ?? currentSetup?.net_rr;
    const currentNetRR = typeof currentNetRRRaw === 'number' && Number.isFinite(currentNetRRRaw) ? currentNetRRRaw : null;
    const netRewardUsdt = selectedIntent.netRewardUsdt;

    const selectionPolicyView = resolveSelectionRrPolicy(selectedIntent, {
      value: appliedMinNetRR ?? null,
      source: rrPolicySource,
      fingerprint: rrPolicyFingerprint,
      status: rrPolicyStatus,
      isConfirmed: isRrPolicyConfirmed,
    });
    const rrComparison = compareNetRr(currentNetRR, selectionPolicyView);

    if (typeof netRewardUsdt === 'number' && Number.isFinite(netRewardUsdt) && netRewardUsdt <= 0) {
      const tmpl = getCatalogTemplate('NET_REWARD_NON_POSITIVE');
      blockers.push({
        code: 'NET_REWARD_NON_POSITIVE',
        title: tmpl.title,
        summary: typeof tmpl.summary === 'function' ? tmpl.summary({ net_reward_usdt: netRewardUsdt }) : tmpl.summary,
      });
    } else if (selectionPolicyView.kind === 'CURRENT' && rrComparison.status === 'NOT_MET') {
      const reqStr = rrComparison.requiredMinNetRR != null ? `1:${rrComparison.requiredMinNetRR.toFixed(2)}` : 'yêu cầu';
      const actualStr = currentNetRR !== null ? `1:${currentNetRR.toFixed(2)}` : '—';
      const tmpl = getCatalogTemplate('NET_RR_TOO_LOW');
      blockers.push({
        code: 'NET_RR_TOO_LOW',
        title: `Net R:R ${actualStr} chưa đạt yêu cầu ${reqStr}`,
        summary: typeof tmpl.summary === 'function'
          ? tmpl.summary({ net_rr: currentNetRR, required_rr: rrComparison.requiredMinNetRR })
          : `Tỷ lệ Lợi nhuận/Rủi ro ròng ${actualStr} thấp hơn yêu cầu tối thiểu ${reqStr}.`,
      });
    } else if (
      selectionPolicyView.kind === 'CURRENT' &&
      (selectionPolicyView.status === 'LOADING' ||
        selectionPolicyView.status === 'ERROR' ||
        !selectionPolicyView.isAuthoritative ||
        selectionPolicyView.minNetRR === null)
    ) {
      const isErr = selectionPolicyView.status === 'ERROR' || rrComparison.label === 'Lỗi cấu hình R:R';
      blockers.push({
        code: 'POLICY_GATE',
        title: isErr ? 'Lỗi cấu hình R:R' : 'Chưa xác nhận cấu hình R:R',
        summary: isErr
          ? 'Không thể tải hoặc xác minh chính sách R:R từ máy chủ. Vui lòng kiểm tra lại kết nối hoặc cài đặt.'
          : 'Hệ thống đang tải hoặc xác thực ngưỡng R:R áp dụng trước khi cho phép kích hoạt lệnh.',
      });
    } else if (
      selectionPolicyView.kind === 'INVALID' ||
      rrComparison.status === 'INVALID'
    ) {
      blockers.push({
        code: 'POLICY_GATE',
        title: 'Cấu hình R:R không hợp lệ',
        summary: selectionPolicyView.kind === 'ORDER_SNAPSHOT'
          ? 'Ngưỡng snapshot lúc ARM của lệnh không hợp lệ.'
          : 'Dữ liệu hoặc tỷ lệ R:R không hợp lệ.',
      });
    }

    if (planKind === 'PROVISIONAL') {
      const tmpl = getCatalogTemplate('UNCONFIRMED_ENTRY');
      blockers.push({
        code: 'UNCONFIRMED_ENTRY',
        title: tmpl.title,
        summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      });
    }

    if (isEligibilityStale) {
      const tmpl = getCatalogTemplate('STALE_ELIGIBILITY');
      blockers.push({
        code: 'STALE_ELIGIBILITY',
        title: tmpl.title,
        summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      });
    }

    if (isCrossBlocked) {
      const tmpl = getCatalogTemplate('CROSS_MARGIN_UNSUPPORTED');
      blockers.push({
        code: 'CROSS_MARGIN_UNSUPPORTED',
        title: tmpl.title,
        summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      });
    }

    if (isBlockedByActive) {
      const tmpl = getCatalogTemplate('ACTIVE_POSITION_EXISTS');
      blockers.push({
        code: 'ACTIVE_POSITION_EXISTS',
        title: tmpl.title,
        summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      });
    }

    if (isBlockedByArmed) {
      const tmpl = getCatalogTemplate('ARMED_ORDER_EXISTS');
      blockers.push({
        code: 'ARMED_ORDER_EXISTS',
        title: tmpl.title,
        summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      });
    }

    if (isEligibilityBlocked && eligibility) {
      const el: EntryEligibility = eligibility;
      const rCodes = el.reason_codes || [];
      for (const code of rCodes) {
        if (!blockers.some((b) => b.code === code)) {
          const tmpl = getCatalogTemplate(code);
          const nv = el.news_view;
          const params: Record<string, unknown> = code === 'NEWS_BLACKOUT' && nv
            ? { ...el, event_title: nv.title, impact: nv.impact }
            : { ...el };
          blockers.push({
            code,
            title: tmpl.title,
            summary: typeof tmpl.summary === 'function' ? tmpl.summary(params) : tmpl.summary,
          });
        }
      }
      if (blockers.length === 0 && (el.block_reasons?.length ?? 0) > 0) {
        blockers.push({
          code: 'POLICY_GATE',
          title: 'Chưa đủ điều kiện kích hoạt lệnh',
          summary: el.block_reasons?.[0] ?? 'Chưa đủ điều kiện kích hoạt lệnh',
        });
      }
    }

    // Normalized lesson rules (V10.2)
    const normalizedBlockers = (eligibility?.lesson_blockers || []).map((raw) => normalizeLessonItem(raw));
    for (const lb of normalizedBlockers) {
      const code = lb.reason_code || (lb.rule_id ? `LESSON_RULE_${lb.rule_id}` : 'LESSON_RULE_BLOCKED');
      if (!blockers.some((b) => b.code === code || b.code === 'LESSON_RULE_BLOCKED')) {
        blockers.push({
          code,
          title: lb.rule_id ? `Quy tắc #${lb.rule_id} hạn chế entry` : lb.title,
          summary: lb.message,
        });
      }
    }
  }

  const cannotArm = blockers.length > 0;
  const primaryBlocker = blockers[0];

  // V14 Quality Status Derivation
  const qualityStatus: string = selectedQualitySnapshot?.assessment?.status || selectedIntent?.quality_status || (selectedIntent ? 'NOT_ASSESSED' : 'NOT_ASSESSED');
  const requiredMetCount: number = selectedQualitySnapshot?.assessment?.summary?.required_met ?? selectedIntent?.quality_summary?.required_met ?? 0;
  const requiredTotalCount: number = selectedQualitySnapshot?.assessment?.summary?.required_total ?? selectedIntent?.quality_summary?.required_total ?? 0;
  const primaryReason: string = selectedQualitySnapshot?.assessment?.summary?.headline_vi || selectedIntent?.quality_summary?.headline_vi || 'Đang quan sát cấu trúc SMC phiên Mỹ';
  const cautionOrNext: string = selectedQualitySnapshot?.assessment?.summary?.next_condition_vi || selectedQualitySnapshot?.assessment?.summary?.caution_vi || 'Chờ cấu trúc xác nhận (EXPLAIN_ONLY)';

  const getQualityBadgeLabel = (status: string) => {
    switch (status) {
      case 'SATISFIED': return 'Đủ checklist phân tích';
      case 'PARTIAL': return 'Đang chờ xác nhận';
      case 'INSUFFICIENT_DATA': return 'Thiếu dữ liệu';
      case 'BLOCKED_EXISTING': return 'Đang bị chặn';
      case 'ERROR': return 'Chưa đánh giá được';
      case 'NOT_ASSESSED':
      default: return 'Chưa có đánh giá';
    }
  };

  const getQualityBadgeStyle = (status: string) => {
    switch (status) {
      case 'SATISFIED': return 'bg-emerald-950/80 text-emerald-300 border-emerald-700/80';
      case 'PARTIAL': return 'bg-amber-950/80 text-amber-300 border-amber-700/80';
      case 'INSUFFICIENT_DATA': return 'bg-blue-950/80 text-blue-300 border-blue-700/80';
      case 'BLOCKED_EXISTING':
      case 'ERROR': return 'bg-rose-950/80 text-rose-300 border-rose-700/80';
      default: return 'bg-charcoal-800 text-gray-400 border-charcoal-700';
    }
  };

  // Lesson counts
  const redCount = (eligibility?.lesson_blockers || []).length;
  const yellowCount = (eligibility?.lesson_warnings || []).length;
  const greenCount = (eligibility?.lesson_advisories || []).length;

  // Condition counts
  const conditionsMet = selectedIntent?.conditions_met || currentSetup?.conditions_met || [];
  const conditionsRemaining = selectedIntent?.conditions_remaining || currentSetup?.conditions_remaining || [];
  const totalConditions = conditionsMet.length + conditionsRemaining.length;

  // Distance to entry
  // Provisional/unconfirmed entry never shows a fake "0.00 to Entry"; stale feed shows a wait label.
  let distanceDisplay = '-- / chưa có dữ liệu';
  if (selectedIntent) {
    distanceDisplay = getEntryDistanceLabel({
      planKind,
      isQuoteFresh,
      last: currentQuote?.last,
      entry: selectedIntent.plannedEntry,
    });
    if (distanceDisplay === 'Chưa có dữ liệu khoảng cách' && currentSetup?.distance_to_entry_usdt != null && currentSetup.distance_to_entry_usdt > 0 && planKind !== 'PROVISIONAL') {
      distanceDisplay = `${Number(currentSetup.distance_to_entry_usdt).toFixed(2)} USDT`;
    }
  }

  // Concise decision view derived from the bound backend decision (never from quality score).
  const decisionView = deriveEntryDecisionView({
    hasOpenPosition: hasActivePos,
    hasArmedOrderForIntent: selectedIntent?.status === 'ARMED',
    hasIntent: Boolean(selectedIntent),
    planKind,
    strategyState: selectedIntent?.status,
    isQuoteFresh,
    eligibility,
    intent: selectedIntent,
  });
  const newsView = eligibility?.news_view;

  // Margin calculation
  const calculatedMarginUsdt = selectedIntent
    ? (
        (selectedIntent.quantity * selectedIntent.plannedEntry) /
        (selectedIntent.leverage || leverage)
      ).toFixed(2)
    : '0.00';

  const grossRR = selectedIntent?.grossRR ?? currentSetup?.gross_rr ?? 0;
  const netRR = selectedIntent?.estimatedNetRR ?? currentSetup?.net_rr ?? 0;

  // Vietnamese status label
  const getVietnameseStatus = (status?: string): string => {
    switch (status) {
      case 'READY':
        return 'Sẵn Sàng';
      case 'ARMED':
        return 'Đã Lên Nòng';
      case 'WATCHING':
        return 'Đang Theo Dõi';
      case 'DRAFT':
        return 'Bản Nháp';
      case 'CLOSED':
        return 'Đã Đóng';
      default:
        return status || 'Đang Theo Dõi';
    }
  };

  return (
    <div className="flex flex-col h-full bg-charcoal-900 border border-charcoal-750 rounded-lg overflow-hidden select-none">
      {/* 1. Header (shrink-0) */}
      <div className="p-3 bg-charcoal-850 border-b border-charcoal-750 shrink-0 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold uppercase tracking-wider text-aurum-400 flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-aurum-400" />
            Quyết Định Lệnh
          </span>
          {selectedIntent && (
            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-charcoal-750 text-gray-300">
              rev.{selectedIntent.revision || 1}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
          {currentQuote && Number.isFinite(currentQuote.last) && (
            <span className="font-mono text-gray-200 font-semibold">
              ${currentQuote.last.toFixed(2)}
            </span>
          )}
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" title="Feed realtime hoạt động" />
        </div>
      </div>

      {/* 2. Scrollable Body (flex-1 min-h-0 overflow-y-auto) */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2.5 text-xs">
        {/* PRIORITY CARD: ACTIVE OPEN POSITION */}
        {hasActivePos && pos && (
          <div className="p-3 rounded-lg bg-emerald-950/40 border border-emerald-500/60 shadow space-y-2">
            <div className="flex items-center justify-between border-b border-emerald-800/40 pb-1.5">
              <div className="flex items-center gap-1.5">
                <span
                  className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                    pos.direction === 'LONG'
                      ? 'bg-emerald-900 text-emerald-300 border border-emerald-700'
                      : 'bg-rose-900 text-rose-300 border border-rose-700'
                  }`}
                >
                  {pos.direction} XAUUSDT
                </span>
                <span className="text-[10px] px-1.5 py-0.2 rounded bg-charcoal-800 text-gray-300 border border-charcoal-750 font-mono">
                  PAPER
                </span>
                {pos.candidate_score_summary && (
                  <CandidateScoreBadge
                    summary={pos.candidate_score_summary}
                    compact
                    labelPrefix="Điểm lúc ARM"
                    onOpenDetails={() => onOpenDetailsDrawer('quality')}
                  />
                )}
              </div>

              <div className="flex items-center gap-1 font-mono text-xs font-bold">
                <span className="text-gray-400 text-[10px]">PnL:</span>
                <span className={pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                  {pos.unrealized_pnl >= 0 ? '+' : ''}
                  {Number(pos.unrealized_pnl || 0).toFixed(2)} USDT
                </span>
              </div>
            </div>

            {/* Position Levels */}
            <div className="grid grid-cols-3 gap-1.5 font-mono text-[11px] bg-charcoal-900/80 p-2 rounded border border-charcoal-750">
              <div>
                <span className="text-gray-500 text-[10px] block">Entry:</span>
                <span className="text-gray-200 font-bold">${pos.entry_price || pos.actual_entry}</span>
              </div>
              <div>
                <span className="text-gray-500 text-[10px] block">Stop Loss:</span>
                <span className="text-rose-400 font-bold">${pos.stop_loss}</span>
              </div>
              <div>
                <span className="text-gray-500 text-[10px] block">Take Profit:</span>
                <span className="text-emerald-400 font-bold">${pos.take_profit}</span>
              </div>
            </div>

            <div className="flex items-center justify-between text-[10px] text-gray-400 pt-0.5">
              <span>Đòn bẩy: {pos.leverage || leverage}x</span>
              <span className="text-emerald-300/90 font-medium">ExitMonitor đang giám sát</span>
            </div>

            {/* Focus active order on chart */}
            {onFocusTradeOnChart && (
              <button
                type="button"
                data-testid="sidebar-focus-order"
                onClick={() => onFocusTradeOnChart(pos)}
                className="w-full py-1 text-center bg-charcoal-800 hover:bg-charcoal-750 text-emerald-400 rounded text-xs border border-charcoal-700 font-semibold transition"
              >
                Xem Lệnh Trên Chart
              </button>
            )}

            {/* Quick close position button */}
            <button
              type="button"
              onClick={onClosePosition}
              disabled={isActionLoading}
              className="w-full py-1.5 bg-rose-600 hover:bg-rose-500 text-white font-bold rounded text-xs transition flex items-center justify-center gap-1.5 shadow-sm disabled:opacity-50"
            >
              <XCircle className="w-3.5 h-3.5" />
              <span>{isActionLoading ? 'Đang đóng...' : 'Đóng Vị Thế Ngay (Thị Trường)'}</span>
            </button>
          </div>
        )}

        {/* STALE PLAN NOTICE BANNER */}
        {stalePlanNotice && (
          <div className="p-2.5 rounded-lg bg-amber-950/80 border border-amber-600/80 text-amber-200 text-xs space-y-1.5 shadow">
            <div className="flex items-center justify-between">
              <span className="font-bold text-amber-300 text-[11px] flex items-center gap-1">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                Kế hoạch đã có bản mới
              </span>
              <button
                type="button"
                onClick={() => {
                  const updated = upcomingSetups.find((s: any) => s.id === stalePlanNotice.setupId);
                  if (updated) {
                    onFocusSetupOnChart(updated);
                  }
                  onClearStalePlanNotice();
                }}
                className="px-2 py-0.5 rounded bg-amber-500 hover:bg-amber-400 text-charcoal-950 font-bold text-[11px] transition shadow"
              >
                Xem bản mới
              </button>
            </div>
            <p className="text-[10px] text-gray-300 leading-snug">
              Bản đang xem rev.{stalePlanNotice.oldRevision} • Backend đã cập nhật {stalePlanNotice.newDirection} rev.{stalePlanNotice.newRevision}.
            </p>
          </div>
        )}

        {/* CG-01 REALTIME CANDIDATE DIAGNOSTICS (V16.29) */}
        <CG01DiagnosticCard
          displayState={cg01State.displayState}
          appliedMinNetRR={appliedMinNetRR}
          rrPolicySource={rrPolicySource}
          isRrPolicyConfirmed={isRrPolicyConfirmed}
          rrPolicyStatus={rrPolicyStatus}
          onOpenRrSettings={onOpenRrSettings}
        />

        {/* THREE-SESSION CONDITIONAL FORECAST V14.4.1 (read-only, cached) */}
        <SMCSessionForecastCard
          symbol="XAUUSDT"
          refreshKey={(scenarios || []).reduce((acc, s) => acc + (s.revision || 0), 0)}
        />

        {/* REALTIME SMC SCENARIOS V14.4 */}
        {scenarios && scenarios.length > 0 && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs px-1">
              <span className="font-semibold text-gray-300 flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-cyan-400" />
                Kịch Bản SMC Realtime ({scenarios.length})
              </span>
              <span className="text-[10px] text-gray-500 font-mono">Phiên hiện tại</span>
            </div>
            {scenarios.map((sc, idx) => (
              <SMCScenarioCard
                key={sc.scenario_id}
                scenario={sc}
                isPrimary={idx === 0}
                onSelect={onSelectScenario}
              />
            ))}
          </div>
        )}

        {/* UPCOMING SETUPS SHORTCUT CARD */}
        {upcomingSetups && upcomingSetups.length > 0 && onFocusSetupOnChart && (
          <div className="p-2.5 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-1.5">
            <span className="text-[10px] text-gray-400 font-semibold uppercase tracking-wider block">Setup Tiềm Năng</span>
            <button
              type="button"
              data-testid="sidebar-focus-setup"
              onClick={() => onFocusSetupOnChart(upcomingSetups[0])}
              className="w-full py-1.5 px-2 bg-charcoal-800 hover:bg-charcoal-750 text-indigo-300 rounded text-xs border border-charcoal-700 font-semibold transition flex items-center justify-between"
            >
              <span>{upcomingSetups[0].direction} {upcomingSetups[0].symbol || 'XAUUSDT'}</span>
              <span className="text-gray-400 text-[10px]">Xem trên chart →</span>
            </button>
          </div>
        )}

        {/* SELECTED SETUP CARD */}
        {selectedIntent ? (
          <div className="space-y-2.5">
            {/* Setup Header & Distance */}
            <div className="p-2.5 rounded-lg bg-charcoal-850 border border-charcoal-700 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-bold ${
                      selectedIntent.direction === 'LONG'
                        ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                        : 'bg-rose-950 text-rose-300 border border-rose-800'
                    }`}
                  >
                    {selectedIntent.direction} XAUUSDT
                  </span>
                  <span className="text-[10px] text-gray-400 font-mono">
                    {selectedIntent.timeframe || '15M'}
                  </span>
                </div>

                <div className="flex items-center gap-1.5">
                  <CandidateScoreBadge
                    summary={selectedIntent.candidate_score_summary}
                    compact
                    labelPrefix={selectedIntent.status === 'ARMED' ? 'Điểm lúc ARM' : 'Điểm đánh giá'}
                    onOpenDetails={() => onOpenDetailsDrawer('quality')}
                  />
                  <span
                    className={`text-[11px] font-mono px-1.5 py-0.2 rounded font-semibold ${
                      selectedIntent.status === 'READY'
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                        : selectedIntent.status === 'ARMED'
                        ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                        : 'bg-charcoal-800 text-gray-400 border border-charcoal-700'
                    }`}
                  >
                    {getVietnameseStatus(selectedIntent.status)}
                  </span>
                </div>
              </div>

              {/* Distance to Entry */}
              <div className="flex items-center justify-between text-[11px] text-gray-400 border-t border-charcoal-750 pt-1.5">
                <span>Khoảng cách tới Entry:</span>
                <span className="font-mono text-gray-200 font-bold">{distanceDisplay}</span>
              </div>

              {/* Levels Row: Entry / SL / TP */}
              <div className="grid grid-cols-3 gap-1.5 bg-charcoal-900/90 p-2 rounded border border-charcoal-750 text-center font-mono">
                <div>
                  <span className="text-gray-500 text-[10px] block">Entry:</span>
                  <span className="text-gray-200 font-bold text-xs sm:text-sm">
                    ${selectedIntent.plannedEntry}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500 text-[10px] block">SL:</span>
                  <span className="text-rose-400 font-bold text-xs sm:text-sm">
                    ${selectedIntent.stopLoss}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500 text-[10px] block">TP:</span>
                  <span className="text-emerald-400 font-bold text-xs sm:text-sm">
                    {selectedIntent.takeProfit ? `$${selectedIntent.takeProfit}` : '—'}
                  </span>
                </div>
              </div>

              {/* V14 COMPACT SETUP QUALITY CARD (Max 4 lines, explanatory) */}
              <div className="p-2 rounded bg-charcoal-900/90 border border-charcoal-750 space-y-1 text-xs">
                {/* Line 1: Status badge & Counts */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] uppercase font-bold text-gray-400">Chất lượng SMC:</span>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${getQualityBadgeStyle(qualityStatus)}`}>
                      {getQualityBadgeLabel(qualityStatus)}
                    </span>
                  </div>
                  <span className="text-[10px] font-mono text-gray-400">
                    {selectedQualityLoading ? 'Đang tính...' : `${requiredMetCount}/${requiredTotalCount} chuẩn`}
                  </span>
                </div>

                {/* Line 2: Primary reason */}
                <div className="text-[11px] text-gray-300 truncate" title={primaryReason}>
                  <span className="text-gray-500 mr-1">Căn cứ:</span>
                  {primaryReason}
                </div>

                {/* Line 3: Caution or next condition */}
                <div className="text-[11px] text-gray-400 truncate" title={cautionOrNext}>
                  <span className="text-amber-500/80 mr-1">Lưu ý:</span>
                  {cautionOrNext}
                </div>

                {/* Line 4: Action buttons */}
                <div className="flex items-center justify-between pt-1 border-t border-charcoal-750 text-[11px]">
                  <button
                    type="button"
                    onClick={() => onOpenDetailsDrawer('quality')}
                    className="text-aurum-400 hover:text-aurum-300 font-semibold flex items-center gap-1 transition"
                  >
                    <span>Chi tiết chất lượng</span>
                    <ChevronRight className="w-3 h-3" />
                  </button>
                  {onFocusOnChart && (
                    <button
                      type="button"
                      onClick={onFocusOnChart}
                      className="text-indigo-400 hover:text-indigo-300 flex items-center gap-1 transition"
                      title="Định vị setup và bằng chứng trên biểu đồ"
                    >
                      <span>Định vị trên Chart</span>
                    </button>
                  )}
                </div>
              </div>

              {/* Sizing & R:R details */}
              <div className="space-y-1 text-[11px] text-gray-400 border-t border-charcoal-750 pt-1.5">
                <div className="flex items-center justify-between">
                  <span>R:R theo giá (Gross):</span>
                  <span className="text-gray-200 font-mono font-medium">
                    1:{grossRR > 0 ? grossRR.toFixed(2) : '--'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span>R:R sau chi phí (Net):</span>
                  <span className="font-mono font-bold">
                    {(() => {
                      const selView = resolveSelectionRrPolicy(selectedIntent, {
                        value: appliedMinNetRR ?? null,
                        source: rrPolicySource,
                        fingerprint: rrPolicyFingerprint,
                        status: rrPolicyStatus,
                        isConfirmed: isRrPolicyConfirmed,
                      });
                      const comp = compareNetRr(netRR, selView);
                      return (
                        <>
                          <span
                            className={
                              comp.status === 'MET'
                                ? 'text-aurum-400'
                                : comp.status === 'NOT_MET'
                                ? 'text-rose-400'
                                : 'text-gray-300'
                            }
                          >
                            {Number.isFinite(netRR) ? `1:${netRR.toFixed(2)}` : '—'}
                          </span>
                          {comp.status === 'NOT_MET' && comp.requiredMinNetRR != null && (
                            <span className="text-[10px] text-rose-400 font-normal ml-1">
                              (chưa đạt 1:{comp.requiredMinNetRR.toFixed(2)})
                            </span>
                          )}
                        </>
                      );
                    })()}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span>SL (Lỗ ròng / Lỗ giá):</span>
                  <span className="text-gray-200 font-mono">
                    <span className="text-rose-400 font-bold">${selectedIntent.initialRiskUsdt?.toFixed(2) || '2.50'}</span>
                    {selectedIntent.grossLossUsdt != null && Number.isFinite(selectedIntent.grossLossUsdt) && (
                      <span className="text-gray-400 text-[10px] ml-1">/ ${selectedIntent.grossLossUsdt.toFixed(2)}</span>
                    )}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span>TP (Lãi ròng / Lãi giá):</span>
                  <span className="text-gray-200 font-mono">
                    <span className={selectedIntent.netRewardUsdt != null && Number.isFinite(selectedIntent.netRewardUsdt) && selectedIntent.netRewardUsdt < 0 ? 'text-rose-400' : 'text-emerald-400 font-bold'}>
                      ${selectedIntent.netRewardUsdt != null && Number.isFinite(selectedIntent.netRewardUsdt) ? selectedIntent.netRewardUsdt.toFixed(2) : (netRR > 0 ? (selectedIntent.initialRiskUsdt * netRR).toFixed(2) : '--')}
                    </span>
                    {selectedIntent.grossRewardUsdt != null && Number.isFinite(selectedIntent.grossRewardUsdt) && (
                      <span className="text-gray-400 text-[10px] ml-1">/ ${selectedIntent.grossRewardUsdt.toFixed(2)}</span>
                    )}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span>Khối lượng & Ký quỹ:</span>
                  <span className="text-gray-200 font-mono">
                    {selectedIntent.quantity} oz • Ký quỹ ${calculatedMarginUsdt} USDT ({selectedIntent.leverage || leverage}x)
                  </span>
                </div>
              </div>
            </div>

            {/* V14.2 concise decision row: structural status + effective decision (never merged into one boolean) */}
            <div className="flex items-center gap-1.5 flex-wrap text-[10px]" id="entry-decision-row">
              <span
                id="entry-decision-status"
                className={`px-1.5 py-0.5 rounded font-bold border ${
                  decisionView.status === 'CAN_ARM'
                    ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                    : decisionView.status === 'BLOCKED'
                    ? 'bg-rose-500/15 text-rose-300 border-rose-500/40'
                    : decisionView.status === 'WAITING'
                    ? 'bg-charcoal-800 text-gray-300 border-charcoal-700'
                    : 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                }`}
              >
                {decisionView.label}
              </span>
              {newsView?.is_blackout ? (
                <button
                  type="button"
                  id="entry-news-chip"
                  onClick={() => onOpenDetailsDrawer('upcoming')}
                  className="px-1.5 py-0.5 rounded bg-amber-950/70 border border-amber-700/70 text-amber-300 hover:text-amber-200"
                >
                  Tạm dừng vào lệnh{newsView.remaining_minutes ? ` · còn ~${newsView.remaining_minutes}p` : ''}
                </button>
              ) : newsView?.upcoming_title ? (
                <button
                  type="button"
                  id="entry-news-chip"
                  onClick={() => onOpenDetailsDrawer('upcoming')}
                  className="px-1.5 py-0.5 rounded bg-charcoal-800 border border-charcoal-700 text-gray-300 hover:text-white"
                >
                  Sắp có tin
                </button>
              ) : null}
              {redCount > 0 && (
                <button
                  type="button"
                  id="entry-lesson-chip"
                  onClick={() => onOpenDetailsDrawer('lessons')}
                  className="px-1.5 py-0.5 rounded bg-rose-950/70 border border-rose-700/70 text-rose-300 hover:text-rose-200"
                >
                  Bài học đỏ: {redCount}
                </button>
              )}
            </div>

            {/* Primary Blocker Banner if cannot arm */}
            {cannotArm && primaryBlocker && (
              <div className="p-2.5 rounded-lg bg-amber-950/70 border border-amber-700/80 text-[11px] text-amber-200 space-y-1">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 font-bold text-amber-300">
                    <ShieldAlert className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                    <span>{primaryBlocker.title}</span>
                  </div>
                  {blockers.length > 1 && (
                    <span className="text-[10px] px-1.5 py-0.2 rounded bg-amber-900/60 border border-amber-700 text-amber-300">
                      +{blockers.length - 1} khác
                    </span>
                  )}
                </div>
                <p className="text-[10px] text-gray-300 leading-snug pl-5">
                  {primaryBlocker.summary}
                </p>
                <div className="pt-1 flex justify-end">
                  <button
                    type="button"
                    onClick={() => onOpenDetailsDrawer('conditions')}
                    className="text-[10px] text-aurum-400 hover:text-aurum-300 font-semibold hover:underline flex items-center gap-0.5"
                  >
                    <span>Xem {blockers.length} điều kiện & blockers</span>
                    <ChevronRight className="w-3 h-3" />
                  </button>
                </div>
              </div>
            )}

            {/* Quick Action Navigation Buttons */}
            <div className="grid grid-cols-3 gap-1.5 text-[10px]">
              <button
                type="button"
                onClick={() => onOpenDetailsDrawer('conditions')}
                className="p-2 rounded bg-charcoal-850 hover:bg-charcoal-800 border border-charcoal-700 text-gray-300 hover:text-white transition flex flex-col items-center gap-1 text-center"
              >
                <CheckCircle2 className="w-3.5 h-3.5 text-aurum-400" />
                <span className="font-semibold">Điều kiện</span>
                <span className="text-gray-400 text-[9px] font-mono">
                  {conditionsMet.length}/{totalConditions > 0 ? totalConditions : 4} Đạt
                </span>
              </button>

              <button
                type="button"
                onClick={() => onOpenDetailsDrawer('lessons')}
                className="p-2 rounded bg-charcoal-850 hover:bg-charcoal-800 border border-charcoal-700 text-gray-300 hover:text-white transition flex flex-col items-center gap-1 text-center"
              >
                <BookOpen className="w-3.5 h-3.5 text-indigo-400" />
                <span className="font-semibold">Bài học</span>
                <span className="text-[9px] font-mono">
                  <span className="text-rose-400 font-bold">{redCount}</span> /{' '}
                  <span className="text-amber-400 font-bold">{yellowCount}</span> /{' '}
                  <span className="text-emerald-400 font-bold">{greenCount}</span>
                </span>
              </button>

              <button
                type="button"
                onClick={() => onOpenDetailsDrawer('upcoming')}
                className="p-2 rounded bg-charcoal-850 hover:bg-charcoal-800 border border-charcoal-700 text-gray-300 hover:text-white transition flex flex-col items-center gap-1 text-center"
              >
                <ListOrdered className="w-3.5 h-3.5 text-emerald-400" />
                <span className="font-semibold">Lệnh dự kiến</span>
                <span className="text-gray-400 text-[9px] font-mono">
                  {upcomingSetups.length} Setup
                </span>
              </button>
            </div>
          </div>
        ) : (
          <div className="py-8 text-center text-xs text-gray-400 space-y-2 bg-charcoal-850/50 rounded-lg border border-charcoal-750 p-4">
            <Layers className="w-7 h-7 mx-auto text-gray-600" />
            <p className="font-medium text-gray-300">Chưa có kế hoạch nào được chọn</p>
            <p className="text-[11px] text-gray-500">
              Bấm vào danh sách lệnh dự kiến hoặc thước đo trên biểu đồ để xem chi tiết.
            </p>
            <div className="flex flex-wrap justify-center gap-2 mt-2">
              <button
                type="button"
                onClick={() => onOpenDetailsDrawer('upcoming')}
                className="px-3 py-1.5 rounded bg-charcoal-800 hover:bg-charcoal-750 text-aurum-400 text-xs font-semibold border border-charcoal-700 transition"
              >
                Mở Danh Sách Lệnh ({upcomingSetups.length}) →
              </button>
              {onFollowLatestSignal && (
                <button
                  type="button"
                  onClick={onFollowLatestSignal}
                  className="px-3 py-1.5 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-xs font-semibold border border-amber-500/40 transition"
                >
                  Theo Dõi Tín Hiệu SMC
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 3. Footer Action (shrink-0 ALWAYS VISIBLE) */}
      <div className="p-3 bg-charcoal-850 border-t border-charcoal-750 shrink-0 space-y-1.5">
        {selectedIntent?.status === 'ARMED' ? (
          <div className="space-y-1.5">
            <div className="p-1.5 bg-amber-500/10 border border-amber-500/30 rounded text-center">
              <span className="text-amber-400 font-bold text-[11px] block">
                LỆNH CHỜ ĐÃ ĐƯỢC TẠO — CHƯA KHỚP
              </span>
              <span className="text-[10px] text-gray-400 block">
                Đang chờ giá chạm Entry ${selectedIntent.plannedEntry} để khớp
              </span>
            </div>
            <button
              type="button"
              onClick={() => onCancelSetup(selectedIntent.setup_id!)}
              disabled={isActionLoading}
              className="w-full py-2 bg-rose-600 hover:bg-rose-500 text-white font-bold rounded text-xs transition flex items-center justify-center gap-1.5 shadow disabled:opacity-50"
            >
              <XCircle className="w-3.5 h-3.5" />
              <span>{isActionLoading ? 'Đang hủy...' : 'Hủy Lệnh Chờ'}</span>
            </button>
          </div>
        ) : selectedIntent?.source === 'WATCH_SETUP' ? (
          <button
            type="button"
            onClick={() =>
              onArmSetup(
                selectedIntent.setup_id!,
                selectedIntent.direction,
                selectedIntent.setup_instance_id,
                selectedIntent.revision
              )
            }
            id="entry-submit-cta"
            data-can-submit={String(!cannotArm && decisionView.canSubmit)}
            disabled={cannotArm || !decisionView.canSubmit || isActionLoading}
            aria-label={cannotArm && primaryBlocker ? `Không thể arm: ${primaryBlocker.title}` : !decisionView.canSubmit ? `Chưa thể đặt lệnh: ${decisionView.primaryReason ?? ''}` : `Arm ${selectedIntent.direction} PAPER`}
            className="w-full py-2.5 bg-gradient-to-r from-aurum-500 to-aurum-600 hover:from-aurum-400 hover:to-aurum-500 text-charcoal-950 font-bold rounded text-xs transition shadow disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
          >
            <PlayCircle className="w-4 h-4" />
            <span>
              {isActionLoading
                ? 'Đang xử lý...'
                : cannotArm && primaryBlocker
                ? primaryBlocker.title
                : !decisionView.canSubmit
                ? decisionView.primaryReason ?? 'Đang kiểm tra điều kiện'
                : decisionView.ctaKind === 'ARM_PENDING'
                ? `Đặt lệnh chờ ${selectedIntent.direction} — PAPER`
                : `Arm ${selectedIntent.direction} — PAPER`}
            </span>
          </button>
        ) : selectedIntent ? (
          <button
            type="button"
            disabled
            className="w-full py-2 bg-charcoal-750 text-gray-400 font-medium rounded text-xs cursor-not-allowed border border-charcoal-700 text-center"
          >
            {selectedIntent.status === 'CLOSED'
              ? 'Lệnh Đã Đóng'
              : `Đang Theo Dõi (${selectedIntent.status || 'WATCHING'})`}
          </button>
        ) : (
          <button
            type="button"
            disabled
            className="w-full py-2 bg-charcoal-800 text-gray-500 font-medium rounded text-xs cursor-not-allowed border border-charcoal-750 text-center"
          >
            Chọn Một Kế Hoạch Để Thực Hiện
          </button>
        )}
      </div>
    </div>
  );
};
