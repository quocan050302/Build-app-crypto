import React, { useState, useEffect } from 'react';
import type { SelectedTradeIntent } from '../App';
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

export interface StalePlanNoticeData {
  setupId: string;
  oldRevision: number;
  newRevision: number;
  newDirection: string;
  newInstanceId: string;
}

export type DrawerSectionType = 'conditions' | 'lessons' | 'upcoming' | 'risk' | 'session';

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
}

export const QuickDecisionSidebar: React.FC<QuickDecisionSidebarProps> = ({
  selectedIntent,
  activePosition,
  hasArmedOrder,
  upcomingSetups,
  stalePlanNotice,
  onClearStalePlanNotice,
  onFocusSetupOnChart,
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
}) => {
  // Realtime quote subscriber for live distance & freshness
  const [currentQuote, setCurrentQuote] = useState(() => quoteStore.getQuote('XAUUSDT'));

  useEffect(() => {
    const unsub = quoteStore.subscribe('XAUUSDT', (q) => {
      if (q) setCurrentQuote(q);
    });
    return unsub;
  }, []);

  const hasActivePos = Boolean(activePosition?.has_active_position && activePosition?.position);
  const pos = activePosition?.position;

  // Find matching watch setup from list
  const currentSetup = selectedIntent
    ? upcomingSetups.find((s: any) => s.id === selectedIntent.setup_id)
    : null;

  const eligibility = selectedIntent?.eligibility || currentSetup?.eligibility;

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
  const isEligibilityBlocked = Boolean(eligibility && eligibility.eligible === false);

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

    if (isEligibilityBlocked) {
      const rCodes = eligibility.reason_codes || [];
      for (const code of rCodes) {
        if (!blockers.some((b) => b.code === code)) {
          const tmpl = getCatalogTemplate(code);
          blockers.push({
            code,
            title: tmpl.title,
            summary: typeof tmpl.summary === 'function' ? tmpl.summary(eligibility) : tmpl.summary,
          });
        }
      }
      if (blockers.length === 0 && eligibility.block_reasons?.length > 0) {
        blockers.push({
          code: 'POLICY_GATE',
          title: 'Chưa đủ điều kiện kích hoạt lệnh',
          summary: eligibility.block_reasons[0],
        });
      }
    }

    // Normalized lesson rules (V10.2)
    const normalizedBlockers = (eligibility?.lesson_blockers || []).map(normalizeLessonItem);
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

  // Lesson counts
  const redCount = (eligibility?.lesson_blockers || []).length;
  const yellowCount = (eligibility?.lesson_warnings || []).length;
  const greenCount = (eligibility?.lesson_advisories || []).length;

  // Condition counts
  const conditionsMet = selectedIntent?.conditions_met || currentSetup?.conditions_met || [];
  const conditionsRemaining = selectedIntent?.conditions_remaining || currentSetup?.conditions_remaining || [];
  const totalConditions = conditionsMet.length + conditionsRemaining.length;

  // Distance to entry
  let distanceDisplay = '-- / chưa có dữ liệu';
  if (selectedIntent && currentQuote && Number.isFinite(currentQuote.last) && currentQuote.last > 0 && selectedIntent.plannedEntry > 0) {
    const dist = Math.abs(currentQuote.last - selectedIntent.plannedEntry);
    distanceDisplay = `${dist.toFixed(2)} USDT`;
  } else if (currentSetup?.distance_to_entry_usdt != null && currentSetup.distance_to_entry_usdt > 0) {
    distanceDisplay = `${Number(currentSetup.distance_to_entry_usdt).toFixed(2)} USDT`;
  }

  // Margin calculation
  const calculatedMarginUsdt = selectedIntent
    ? (
        (selectedIntent.quantity * selectedIntent.plannedEntry) /
        (selectedIntent.leverage || leverage)
      ).toFixed(2)
    : '0.00';

  const grossRR = selectedIntent?.grossRR || currentSetup?.gross_rr || 0;
  const netRR = selectedIntent?.estimatedNetRR || currentSetup?.net_rr || 0;

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
                <span className="text-[10px] px-1.5 py-0.2 rounded bg-charcoal-800 text-gray-300 border border-charcoal-700 font-mono">
                  PAPER
                </span>
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

              {/* Sizing & R:R details */}
              <div className="space-y-1 text-[11px] text-gray-400 border-t border-charcoal-750 pt-1.5">
                <div className="flex items-center justify-between">
                  <span>Rủi ro dự kiến:</span>
                  <span className="text-gray-200 font-mono font-medium">
                    ${selectedIntent.initialRiskUsdt?.toFixed(2) || '2.50'} USDT •{' '}
                    <strong className={netRR >= 2.0 ? 'text-aurum-400' : 'text-rose-400'}>
                      Net 1:{netRR > 0 ? netRR.toFixed(2) : '--'}
                    </strong>
                    <span className="text-gray-500 text-[10px] ml-1">(Gross 1:{grossRR > 0 ? grossRR.toFixed(2) : '--'})</span>
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
            disabled={cannotArm || isActionLoading}
            aria-label={cannotArm && primaryBlocker ? `Không thể arm: ${primaryBlocker.title}` : `Arm ${selectedIntent.direction} PAPER`}
            className="w-full py-2.5 bg-gradient-to-r from-aurum-500 to-aurum-600 hover:from-aurum-400 hover:to-aurum-500 text-charcoal-950 font-bold rounded text-xs transition shadow disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
          >
            <PlayCircle className="w-4 h-4" />
            <span>
              {isActionLoading
                ? 'Đang xử lý...'
                : cannotArm && primaryBlocker
                ? primaryBlocker.title
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
