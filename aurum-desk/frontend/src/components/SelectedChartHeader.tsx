import React from 'react';
import type { ChartBinding } from '../types/smcScenario';

interface Props {
  binding: ChartBinding | null;
  state?: string;
  summaryVi?: string;
  plan?: any;
  onClear?: () => void;
  onOpenDetails?: () => void;
}

export const SelectedChartHeader: React.FC<Props> = ({
  binding,
  state,
  summaryVi: _summaryVi,
  plan,
  onClear,
  onOpenDetails,
}) => {
  if (!binding) return null;

  const sourceLabels: Record<string, string> = {
    SCENARIO: 'Kịch bản',
    SETUP: 'Setup SMC',
    ORDER: 'Lệnh giao dịch',
    DRAFT: 'Bản thảo',
  };

  const isLong = binding.direction === 'LONG';
  const isShort = binding.direction === 'SHORT';

  const dirColor = isLong
    ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'
    : isShort
    ? 'text-rose-400 bg-rose-500/10 border-rose-500/30'
    : 'text-amber-400 bg-amber-500/10 border-amber-500/30';

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 bg-gray-900/90 border-b border-gray-800 text-xs backdrop-blur-sm z-10">
      <div className="flex items-center space-x-2">
        <span className={`px-2 py-0.5 rounded font-semibold border ${dirColor}`}>
          {binding.direction || 'CHƯA RÕ HƯỚNG'}
        </span>
        <span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700 font-medium">
          {sourceLabels[binding.source] || binding.source}
        </span>
        {state && (
          <span className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-300 border border-blue-500/30">
            {state}
          </span>
        )}
        <span className="text-gray-400 font-mono hidden sm:inline">
          {binding.symbol}
        </span>
      </div>

      {plan && (plan.planned_entry || plan.actual_entry) && (
        <div className="flex items-center space-x-3 text-xs">
          <span className="text-blue-400 font-mono">
            Entry: {(plan.actual_entry || plan.planned_entry)?.toFixed(2)}
          </span>
          <span className="text-rose-400 font-mono">
            SL: {plan.stop_loss?.toFixed(2)}
          </span>
          <span className="text-emerald-400 font-mono">
            TP: {plan.take_profit?.toFixed(2)}
          </span>
          {typeof plan.net_rr === 'number' && (
            <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-semibold font-mono">
              Net R:R {plan.net_rr.toFixed(2)}
            </span>
          )}
        </div>
      )}

      <div className="flex items-center space-x-1.5">
        {onOpenDetails && (
          <button
            type="button"
            onClick={onOpenDetails}
            className="px-2 py-0.5 rounded text-gray-300 hover:text-white bg-gray-800 hover:bg-gray-700 border border-gray-700 transition-colors"
          >
            Chi tiết
          </button>
        )}
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            className="px-2 py-0.5 rounded text-gray-400 hover:text-rose-400 hover:bg-gray-800 transition-colors"
            title="Trở về chế độ cấu trúc tổng quan"
          >
            ✕ Hủy chọn
          </button>
        )}
      </div>
    </div>
  );
};
