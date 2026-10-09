import React from 'react';
import type { SelectedTradeIntent } from './App';
import {
  CheckCircle2,
  Clock,
  ShieldAlert,
  PlayCircle,
  XCircle,
  HelpCircle
} from 'lucide-react';

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

  // Eligibility block from backend policy/engine
  const isEligibilityBlocked = eligibility && !eligibility.can_arm;

  const cannotArm =
    !isGeometryValid ||
    !selectedIntent.takeProfit ||
    isCrossBlocked ||
    isBlockedByActive ||
    isBlockedByArmed ||
    Boolean(isEligibilityBlocked);

  // Format human-friendly Vietnamese reason
  const getArmBlockReason = (): string => {
    if (!isGeometryValid) return 'Geometry Không Hợp Lệ (Yêu cầu SL < Entry < TP)';
    if (isCrossBlocked) return 'Chặn Arm Khi Chọn Cross Margin (Cần Isolated)';
    if (isBlockedByActive) return 'Không Thể Arm: Đang Có 1 Vị Thế Mở';
    if (isBlockedByArmed) return 'Không Thể Arm: Đang Có Lệnh ARMED Chờ Khớp';
    if (isEligibilityBlocked) {
      if (eligibility.block_reasons && eligibility.block_reasons.length > 0) {
        return eligibility.block_reasons[0];
      }
      if (eligibility.reason_codes && eligibility.reason_codes.length > 0) {
        return `Chặn: ${eligibility.reason_codes[0]}`;
      }
      return 'Chưa Đủ Điều Kiện Arm';
    }
    return `Arm ${selectedIntent.direction} — PAPER`;
  };

  const calculatedMarginUsdt = (
    (selectedIntent.quantity * selectedIntent.plannedEntry) /
    (selectedIntent.leverage || leverage)
  ).toFixed(2);

  const grossRR = selectedIntent.grossRR || currentSetup?.gross_rr || 0;
  const netRR = selectedIntent.estimatedNetRR || currentSetup?.net_rr || 0;

  const conditionsMet = selectedIntent.conditions_met || currentSetup?.conditions_met || [];
  const conditionsRemaining =
    selectedIntent.conditions_remaining || currentSetup?.conditions_remaining || [];

  // Generate next concrete trigger requirement
  const getNextConcreteCondition = (): string => {
    if (selectedIntent.status === 'ARMED') {
      return `Lệnh đã lên nòng. Đang chờ giá chạm vùng Entry $${selectedIntent.plannedEntry} để ExecutionCoordinator khớp lệnh.`;
    }
    if (selectedIntent.status === 'READY') {
      return 'Tất cả điều kiện SMC đã thỏa mãn. Sẵn sàng Arm để đưa vào hàng đợi khớp tự động.';
    }
    if (conditionsRemaining.length > 0) {
      return `Chờ điều kiện tiếp theo: ${conditionsRemaining[0]} (Giá cách Entry $${(
        currentSetup?.distance_to_entry_usdt ?? 0
      ).toFixed(2)} USDT / ${(currentSetup?.distance_to_entry_atr ?? 0).toFixed(1)} ATR)`;
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
              netRR >= 2.0 ? 'text-aurum-400 font-bold' : 'text-rose-400 font-medium'
            }`}
          >
            R:R 1:{grossRR > 0 ? grossRR.toFixed(2) : '--'} (Net 1:{netRR > 0 ? netRR.toFixed(2) : '--'})
          </span>
        </div>

        {/* Safety Warnings */}
        {!isGeometryValid && (
          <div className="mb-2 p-1.5 rounded bg-rose-950/80 border border-rose-800 text-[10px] text-rose-300 flex items-center gap-1.5">
            <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
            <span>
              Geometry không hợp lệ:{' '}
              {selectedIntent.direction === 'LONG' ? 'Yêu cầu SL < Entry < TP' : 'Yêu cầu TP < Entry < SL'}.
            </span>
          </div>
        )}

        {isCrossBlocked && (
          <div className="mb-2 p-1.5 rounded bg-amber-950/80 border border-amber-700 text-[10px] text-amber-300 flex items-center gap-1.5">
            <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
            <span>Paper Trading chỉ thực thi trên Isolated Margin. Vui lòng chuyển sang ISOLATED.</span>
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
          title={cannotArm ? getArmBlockReason() : undefined}
          className="w-full py-2 bg-gradient-to-r from-aurum-500 to-aurum-600 hover:from-aurum-400 hover:to-aurum-500 text-charcoal-950 font-bold rounded text-xs transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
        >
          <PlayCircle className="w-3.5 h-3.5" />
          <span>{cannotArm ? getArmBlockReason() : `Arm ${selectedIntent.direction} — PAPER`}</span>
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
