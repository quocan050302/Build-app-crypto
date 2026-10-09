import React, { useState } from 'react';
import type { SelectedTradeIntent } from './App';
import {
  CheckCircle2,
  Clock,
  ShieldAlert,
  PlayCircle,
  XCircle,
  HelpCircle,
  ChevronDown,
  ChevronUp,
  Brain,
  AlertTriangle,
  Info
} from 'lucide-react';
import { getCatalogTemplate } from './utils/userMessageCatalog';
import { normalizeLessonItem, type NormalizedLessonItem } from './types/lesson';
import { quoteStore } from './services/quoteStore';

interface ExpectedEntryPanelProps {
  selectedIntent: SelectedTradeIntent;
  setups: any[];
  activePosition: any;
  hasArmedOrder: boolean;
  leverage: number;
  marginMode: string;
  onArmSetup: (setupId: string, direction: 'LONG' | 'SHORT', instanceId?: string, revision?: number) => void;
  onCancelSetup: (setupId: string) => void;
  onOpenMarket?: () => void;
}

export const ExpectedEntryPanel: React.FC<ExpectedEntryPanelProps> = ({
  selectedIntent,
  setups,
  activePosition,
  hasArmedOrder,
  leverage,
  marginMode,
  onArmSetup,
  onCancelSetup,
}) => {
  // Find matching watch setup from list if source is WATCH_SETUP
  const currentSetup = setups.find((s: any) => s.id === selectedIntent.setup_id);

  // Authoritative eligibility from setup or selected intent
  const eligibility = selectedIntent.eligibility || currentSetup?.eligibility;

  const tp = selectedIntent.takeProfit;
  const isGeometryValid =
    tp != null &&
    (selectedIntent.direction === 'LONG'
      ? selectedIntent.stopLoss < selectedIntent.plannedEntry && selectedIntent.plannedEntry < tp
      : tp < selectedIntent.plannedEntry && selectedIntent.plannedEntry < selectedIntent.stopLoss);

  const isCrossBlocked = marginMode === 'CROSS' || selectedIntent.marginMode === 'CROSS';
  const isBlockedByActive = !!activePosition?.has_active_position;
  const isBlockedByArmed = hasArmedOrder && selectedIntent.status !== 'ARMED';
  const isEligibilityBlocked = Boolean(eligibility && eligibility.eligible === false);

  const [showAllBlockers, setShowAllBlockers] = useState(false);

  // Assemble all blocker conditions with beginner-friendly explanations
  const blockers: Array<{ code: string; title: string; summary: string; explanation?: string }> = [];

  if (!selectedIntent.takeProfit) {
    blockers.push({
      code: 'MISSING_TP',
      title: 'Chưa có giá Chốt Lời (Take Profit)',
      summary: 'Lệnh cần có mục tiêu Take Profit để tính toán tỷ lệ R:R và bảo vệ lợi nhuận tự động.',
      explanation: 'Take Profit giúp tự động khóa lợi nhuận khi giá chạm mục tiêu dự kiến mà không cần bạn phải canh màn hình liên tục.'
    });
  } else if (!isGeometryValid) {
    const geoTitle = selectedIntent.direction === 'SHORT'
      ? 'SHORT chưa hợp lệ: TP phải dưới Entry, SL phải trên Entry'
      : 'LONG chưa hợp lệ: SL phải dưới Entry, TP phải trên Entry';
    blockers.push({
      code: 'INVALID_PRICE_GEOMETRY',
      title: geoTitle,
      summary: `Mức giá hiện tại (Entry $${selectedIntent.plannedEntry}, SL $${selectedIntent.stopLoss}, TP $${selectedIntent.takeProfit}) vi phạm hình học của vị thế ${selectedIntent.direction}.`,
      explanation: 'Không thể đặt lệnh khi mức giá dừng lỗ hoặc chốt lời đặt sai phía đối với hướng vào lệnh.'
    });
  }

  if (isCrossBlocked) {
    const tmpl = getCatalogTemplate('CROSS_MARGIN_UNSUPPORTED');
    blockers.push({
      code: 'CROSS_MARGIN_UNSUPPORTED',
      title: tmpl.title,
      summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      explanation: tmpl.explanation
    });
  }

  if (isBlockedByActive) {
    const tmpl = getCatalogTemplate('ACTIVE_POSITION_EXISTS');
    blockers.push({
      code: 'ACTIVE_POSITION_EXISTS',
      title: tmpl.title,
      summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      explanation: tmpl.explanation
    });
  }

  if (isBlockedByArmed) {
    const tmpl = getCatalogTemplate('ARMED_ORDER_EXISTS');
    blockers.push({
      code: 'ARMED_ORDER_EXISTS',
      title: tmpl.title,
      summary: typeof tmpl.summary === 'function' ? tmpl.summary({}) : tmpl.summary,
      explanation: tmpl.explanation
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
          explanation: tmpl.explanation
        });
      }
    }
    if (blockers.length === 0 && eligibility.block_reasons?.length > 0) {
      blockers.push({
        code: 'POLICY_GATE',
        title: 'Chưa đủ điều kiện kích hoạt lệnh',
        summary: eligibility.block_reasons[0]
      });
    }
  }

  // Normalized Lesson DTO Rules (V10.2 Contract: L01-L04)
  const normalizedBlockers = (eligibility?.lesson_blockers || []).map(normalizeLessonItem);
  const normalizedWarnings = (eligibility?.lesson_warnings || []).map(normalizeLessonItem);
  const normalizedAdvisories = (eligibility?.lesson_advisories || []).map(normalizeLessonItem);

  for (const lb of normalizedBlockers) {
    const code = lb.reason_code || (lb.rule_id ? `LESSON_RULE_${lb.rule_id}` : 'LESSON_RULE_BLOCKED');
    if (!blockers.some((b) => b.code === code || b.code === 'LESSON_RULE_BLOCKED')) {
      const displayTitle = lb.rule_id
        ? `Chưa thể đặt lệnh: Quy tắc #${lb.rule_id} đang hạn chế entry`
        : `Chưa thể đặt lệnh: ${lb.title}`;
      blockers.push({
        code,
        title: displayTitle,
        summary: lb.message,
        explanation: 'Quy tắc bài học mức ĐỎ (CRITICAL) đã được bạn duyệt và kích hoạt để hạn chế entry có cấu trúc.'
      });
    }
  }

  const cannotArm = blockers.length > 0;
  const primaryBlocker = blockers[0];

  const calculatedMarginUsdt = (
    (selectedIntent.quantity * selectedIntent.plannedEntry) /
    (selectedIntent.leverage || leverage)
  ).toFixed(2);

  const grossRR = selectedIntent.grossRR || currentSetup?.gross_rr || 0;
  const netRR = selectedIntent.estimatedNetRR || currentSetup?.net_rr || 0;

  const conditionsMet = selectedIntent.conditions_met || currentSetup?.conditions_met || [];
  const conditionsRemaining =
    selectedIntent.conditions_remaining || currentSetup?.conditions_remaining || [];

  // Realtime distance to entry using canonical quote store (R01, R02)
  const currentQuote = quoteStore.getQuote(selectedIntent.symbol || 'XAUUSDT');
  let distanceDisplay = '-- / chưa có dữ liệu';
  if (currentQuote && Number.isFinite(currentQuote.last) && currentQuote.last > 0 && selectedIntent.plannedEntry > 0) {
    const dist = Math.abs(currentQuote.last - selectedIntent.plannedEntry);
    distanceDisplay = `${dist.toFixed(2)} USDT`;
  } else if (currentSetup?.distance_to_entry_usdt !== undefined && currentSetup?.distance_to_entry_usdt !== null && currentSetup.distance_to_entry_usdt > 0) {
    distanceDisplay = `${currentSetup.distance_to_entry_usdt.toFixed(2)} USDT`;
  }

  // Generate next concrete trigger requirement
  const getNextConcreteCondition = (): string => {
    if (selectedIntent.status === 'ARMED') {
      return `Lệnh đã lên nòng. Đang chờ giá chạm vùng Entry $${selectedIntent.plannedEntry} để ExecutionCoordinator khớp lệnh.`;
    }
    if (selectedIntent.status === 'READY') {
      return 'Tất cả điều kiện SMC đã thỏa mãn. Sẵn sàng Arm để đưa vào hàng đợi khớp tự động.';
    }
    if (conditionsRemaining.length > 0) {
      const atrDist = currentSetup?.distance_to_entry_atr ? ` / ${currentSetup.distance_to_entry_atr.toFixed(1)} ATR` : '';
      return `Chờ điều kiện tiếp theo: ${conditionsRemaining[0]} (Giá cách Entry: ${distanceDisplay}${atrDist})`;
    }
    return 'Chờ giá retest vùng POI/FVG và xác nhận nến đóng.';
  };

  const isConfirmed = selectedIntent.status === 'READY' || selectedIntent.status === 'ARMED';

  return (
    <div className="space-y-2 text-xs select-none">
      {/* 1. Header Card with Direction & R:R */}
      <div className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-700">
        <div className="flex justify-between items-center font-bold text-sm mb-1.5">
          <div className="flex items-center gap-1.5">
            <span
              className={`px-2 py-0.5 rounded text-xs ${
                selectedIntent.direction === 'LONG'
                  ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                  : 'bg-rose-950 text-rose-300 border border-rose-800'
              }`}
            >
              {selectedIntent.direction} XAUUSDT
            </span>
            <span className="text-[11px] font-mono text-gray-400">
              {selectedIntent.timeframe} · rev.{selectedIntent.revision || 1}
            </span>
          </div>

          <span
            className={`font-mono text-xs ${
              !isGeometryValid
                ? 'text-rose-400 font-bold'
                : netRR >= 2.0
                ? 'text-aurum-400 font-bold'
                : 'text-rose-400 font-medium'
            }`}
          >
            {!isGeometryValid
              ? 'R:R: Không hợp lệ'
              : `R:R 1:${grossRR > 0 ? grossRR.toFixed(2) : '--'} (Net 1:${netRR > 0 ? netRR.toFixed(2) : '--'})`}
          </span>
        </div>

        {/* Unified Blocker Alert Banner for Beginners */}
        {cannotArm && primaryBlocker && (
          <div className="mb-2.5 p-2 rounded-lg bg-amber-950/70 border border-amber-700/80 text-[11px] text-amber-200 space-y-1">
            <div className="flex items-center gap-1.5 font-bold text-amber-300">
              <ShieldAlert className="w-3.5 h-3.5 shrink-0 text-amber-400" />
              <span>{primaryBlocker.title}</span>
            </div>
            <p className="text-[10px] text-gray-300 leading-snug pl-5">
              {primaryBlocker.summary}
            </p>
            {blockers.length > 1 && (
              <div className="pt-1 border-t border-amber-800/40">
                <button
                  type="button"
                  onClick={() => setShowAllBlockers(!showAllBlockers)}
                  className="flex items-center gap-1 text-[10px] font-medium text-amber-400 hover:text-amber-200 transition"
                >
                  <span>Còn {blockers.length - 1} điều kiện an toàn cần kiểm tra</span>
                  {showAllBlockers ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                </button>
                {showAllBlockers && (
                  <div className="mt-1.5 space-y-1.5 pl-2 border-l border-amber-700/50">
                    {blockers.slice(1).map((b, idx) => (
                      <div key={idx} className="text-[10px]">
                        <span className="font-semibold text-amber-300 block">• {b.title}</span>
                        <span className="text-gray-300 pl-2 block">{b.summary}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Levels Grid */}
        <div className="grid grid-cols-3 gap-2 bg-charcoal-900/80 p-2 rounded border border-charcoal-750 text-[11px] mb-2 font-mono">
          <div>
            <span className="text-gray-500 text-[10px] block">Entry {isConfirmed ? '' : '(Dự kiến)'}:</span>
            <span className="text-gray-200 font-bold">${selectedIntent.plannedEntry}</span>
          </div>
          <div>
            <span className="text-gray-500 text-[10px] block">Stop Loss (SL):</span>
            <span className="text-rose-400 font-bold">${selectedIntent.stopLoss}</span>
          </div>
          <div>
            <span className="text-gray-500 text-[10px] block">Take Profit (TP):</span>
            <span className="text-emerald-400 font-bold">
              {selectedIntent.takeProfit && selectedIntent.takeProfit !== selectedIntent.stopLoss
                ? `$${selectedIntent.takeProfit}`
                : 'Chưa có TP'}
            </span>
          </div>
        </div>

        {/* Sizing & Margin Info */}
        <div className="text-[11px] text-gray-400 space-y-0.5 border-t border-charcoal-750 pt-2">
          <div className="flex justify-between">
            <span>Khối lượng & Rủi ro:</span>
            <span className="text-gray-200 font-mono">
              {selectedIntent.quantity} oz (${selectedIntent.initialRiskUsdt?.toFixed(2) || '2.50'} USDT)
            </span>
          </div>
          <div className="flex justify-between">
            <span>Ký quỹ & Đòn bẩy:</span>
            <span className="text-gray-200 font-mono">
              ${calculatedMarginUsdt} USDT ({selectedIntent.leverage || leverage}x {selectedIntent.marginMode || marginMode})
            </span>
          </div>
          <div className="flex justify-between">
            <span>Trạng thái Setup:</span>
            <span className="text-aurum-400 font-bold font-mono">
              {selectedIntent.status || 'WATCHING'}
            </span>
          </div>
        </div>
      </div>

      {/* 2. Checklist & Concrete Next Condition */}
      <div className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-2">
        <span className="text-[11px] font-bold text-gray-300 uppercase tracking-wider block flex items-center justify-between">
          <span>Điều Kiện Chiến Lược SMC & Gate</span>
          <span className="text-[10px] font-mono text-gray-400">
            {conditionsMet.length}/{conditionsMet.length + conditionsRemaining.length} Đạt
          </span>
        </span>

        {/* Conditions Met List */}
        {conditionsMet.length > 0 && (
          <div className="space-y-1">
            {conditionsMet.slice(0, 4).map((cond: string, idx: number) => (
              <div key={idx} className="flex items-center gap-1.5 text-[10px] text-emerald-300">
                <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" />
                <span className="truncate">{cond}</span>
              </div>
            ))}
          </div>
        )}

        {/* Conditions Remaining List */}
        {conditionsRemaining.length > 0 && (
          <div className="space-y-1">
            {conditionsRemaining.slice(0, 3).map((cond: string, idx: number) => (
              <div key={idx} className="flex items-center gap-1.5 text-[10px] text-amber-300">
                <Clock className="w-3 h-3 text-amber-400 shrink-0" />
                <span className="truncate">{cond}</span>
              </div>
            ))}
          </div>
        )}

        {/* Next Concrete Trigger Requirement */}
        <div className="mt-2 p-2 rounded bg-charcoal-900 border border-charcoal-750 text-[10px] text-gray-300">
          <div className="flex items-center gap-1 text-aurum-400 font-semibold mb-0.5">
            <HelpCircle className="w-3 h-3 text-aurum-400" />
            <span>Kế Hoạch Kích Hoạt Cụ Thể:</span>
          </div>
          <p className="leading-relaxed">{getNextConcreteCondition()}</p>
        </div>
      </div>

      {/* 2.5 Lesson Rules & Strategic Memory Section (V10) */}
      <div className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-bold text-gray-300 uppercase tracking-wider flex items-center gap-1.5">
            <Brain className="w-3.5 h-3.5 text-aurum-400" />
            <span>Quy Tắc & Bộ Nhớ Bài Học (V10)</span>
          </span>
          <span className="text-[10px] font-mono text-gray-400">
            {normalizedAdvisories.length + normalizedWarnings.length + normalizedBlockers.length} Phù Hợp
          </span>
        </div>

        {normalizedBlockers.length > 0 && (
          <div className="space-y-1.5">
            {normalizedBlockers.map((b: NormalizedLessonItem, idx: number) => {
              const ruleLabel = b.rule_id ? `Quy tắc #${b.rule_id}` : 'Quy tắc bài học';
              return (
                <div
                  key={idx}
                  className="p-2 rounded bg-rose-950/80 border border-rose-700/80 text-[10px] text-rose-200 space-y-0.5"
                >
                  <div className="flex items-center gap-1.5 font-bold text-rose-300">
                    <ShieldAlert className="w-3.5 h-3.5 shrink-0 text-rose-400" />
                    <span>Chưa thể đặt lệnh: {ruleLabel} đang hạn chế entry</span>
                  </div>
                  <p className="text-gray-300 pl-5 leading-snug">
                    {b.message}
                  </p>
                  {b.next_step && (
                    <p className="text-gray-400 pl-5 text-[9px] italic">
                      Bước tiếp theo: {b.next_step}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {normalizedWarnings.length > 0 && (
          <div className="space-y-1.5">
            {normalizedWarnings.map((w: NormalizedLessonItem, idx: number) => {
              const ruleLabel = w.rule_id ? `Quy tắc #${w.rule_id}` : 'Cảnh báo';
              return (
                <div
                  key={idx}
                  className="p-2 rounded bg-amber-950/70 border border-amber-700/80 text-[10px] text-amber-200 space-y-0.5"
                >
                  <div className="flex items-center gap-1.5 font-bold text-amber-300">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                    <span>{ruleLabel}: {w.title}</span>
                  </div>
                  <p className="text-gray-300 pl-5 text-[9px] leading-snug">
                    {w.message}
                  </p>
                  {w.next_step && (
                    <p className="text-amber-400/80 pl-5 text-[9px] italic">
                      Gợi ý: {w.next_step}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {normalizedAdvisories.length > 0 && (
          <div className="p-2 rounded bg-emerald-950/50 border border-emerald-800/60 text-[10px] text-emerald-200">
            <div className="flex items-center gap-1.5 font-semibold text-emerald-300 mb-0.5">
              <Info className="w-3 h-3 text-emerald-400 shrink-0" />
              <span>Bài học đã tham khảo: {normalizedAdvisories.length} bài học</span>
            </div>
            <div className="space-y-1 pl-4.5 pt-1">
              {normalizedAdvisories.map((a: NormalizedLessonItem, idx: number) => (
                <div key={idx} className="text-gray-300 text-[9px] leading-snug">
                  • <span className="font-semibold text-emerald-400">{a.rule_id ? `Quy tắc #${a.rule_id}: ` : ''}</span>
                  {a.message || a.title}
                </div>
              ))}
            </div>
          </div>
        )}

        {normalizedBlockers.length === 0 && normalizedWarnings.length === 0 && normalizedAdvisories.length === 0 && (
          <div className="p-2 rounded bg-charcoal-900 border border-charcoal-750 text-[10px] text-gray-400 italic text-center">
            Không có bài học phù hợp cho thiết lập này.
          </div>
        )}
      </div>

      {/* 3. Action Button (Arm / Cancel / Status) */}
      {selectedIntent.status === 'ARMED' ? (
        <div className="space-y-1.5">
          <div className="p-2 bg-amber-500/10 border border-amber-500/30 rounded text-center">
            <span className="text-amber-400 font-bold text-[11px] block">
              ĐÃ LÊN NÒNG (ARMED) — CHỜ KÍCH HOẠT
            </span>
            <span className="text-[10px] text-gray-400 block mt-0.5">
              Pending order đang được ExecutionCoordinator giám sát khớp
            </span>
          </div>
          <button
            onClick={() => onCancelSetup(selectedIntent.setup_id!)}
            className="w-full py-1.5 bg-rose-600/80 hover:bg-rose-600 text-white font-bold rounded text-xs transition flex items-center justify-center gap-1.5"
          >
            <XCircle className="w-3.5 h-3.5" />
            <span>Hủy Lệnh Chờ</span>
          </button>
        </div>
      ) : selectedIntent.source === 'WATCH_SETUP' ? (
        <button
          onClick={() =>
            onArmSetup(
              selectedIntent.setup_id!,
              selectedIntent.direction,
              selectedIntent.setup_instance_id,
              selectedIntent.revision
            )
          }
          disabled={cannotArm}
          title={cannotArm && primaryBlocker ? `${primaryBlocker.title}: ${primaryBlocker.summary}` : undefined}
          className="w-full py-2 bg-gradient-to-r from-aurum-500 to-aurum-600 hover:from-aurum-400 hover:to-aurum-500 text-charcoal-950 font-bold rounded text-xs transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
        >
          <PlayCircle className="w-3.5 h-3.5" />
          <span>{cannotArm && primaryBlocker ? primaryBlocker.title : `Arm ${selectedIntent.direction} — PAPER`}</span>
        </button>
      ) : (
        <button
          disabled
          className="w-full py-2 bg-charcoal-750 text-gray-400 font-medium rounded text-xs cursor-not-allowed border border-charcoal-700"
        >
          {selectedIntent.status === 'CLOSED'
            ? 'Lệnh Đã Đóng'
            : `Đang Theo Dõi (${selectedIntent.status || 'WATCHING'})`}
        </button>
      )}
    </div>
  );
};
