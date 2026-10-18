/**
 * Compact CG-01 Diagnostic Card Component (V16.29).
 * Renders live evaluation state, stage, direction, compact POI, and Vietnamese explanation.
 * Conforms to prompt rules 029, 088-089, 111-118.
 */

import React, { useState } from 'react';
import {
  Activity,
  ChevronDown,
  ChevronUp,
  WifiOff,
  Clock,
} from 'lucide-react';
import type { CG01DisplayState } from '../utils/cg01DisplayState';

export interface CG01DiagnosticCardProps {
  displayState: CG01DisplayState;
  className?: string;
}

export const CG01DiagnosticCard: React.FC<CG01DiagnosticCardProps> = ({
  displayState,
  className = '',
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  const {
    stageLabel,
    tone,
    direction,
    poiId,
    poiIdCompact,
    description,
    isStale,
    isConnected,
    asOfMs,
    primaryReason,
    allReasons,
    armedOrderId,
  } = displayState;

  // Tone color styling maps
  const toneBadgeClasses: Record<string, string> = {
    neutral: 'bg-charcoal-700/60 text-gray-300 border-charcoal-600',
    info: 'bg-cyan-950/60 text-cyan-300 border-cyan-800/80',
    warning: 'bg-amber-950/60 text-amber-300 border-amber-800/80',
    success: 'bg-emerald-950/60 text-emerald-300 border-emerald-800/80',
    danger: 'bg-rose-950/60 text-rose-300 border-rose-800/80',
  };

  const toneDotClasses: Record<string, string> = {
    neutral: 'bg-gray-400',
    info: 'bg-cyan-400 animate-pulse',
    warning: 'bg-amber-400',
    success: 'bg-emerald-400',
    danger: 'bg-rose-400',
  };

  const directionBadgeClasses =
    direction === 'LONG'
      ? 'bg-emerald-900/40 text-emerald-300 border-emerald-700/60'
      : direction === 'SHORT'
      ? 'bg-rose-900/40 text-rose-300 border-rose-700/60'
      : 'bg-charcoal-800 text-gray-400 border-charcoal-700';

  const timeFormatted = asOfMs > 0 ? new Date(asOfMs).toLocaleTimeString('vi-VN') : '--:--:--';

  return (
    <div
      data-testid="cg01-diagnostic-card"
      className={`rounded-lg bg-charcoal-850 border border-charcoal-750 p-2.5 space-y-2 text-xs transition-all shadow-sm ${className}`}
    >
      {/* Header: Title, Stage Badge & Expand Button */}
      <div className="flex items-center justify-between gap-1.5">
        <div className="flex items-center gap-1.5 min-w-0">
          <Activity className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
          <span className="font-semibold text-gray-200 tracking-wide truncate">
            CG-01 Diagnostics
          </span>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {!isConnected ? (
            <span
              data-testid="cg01-status-disconnected"
              className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-rose-950/80 text-rose-300 border border-rose-800 flex items-center gap-1"
            >
              <WifiOff className="w-2.5 h-2.5" />
              Mất kết nối
            </span>
          ) : isStale ? (
            <span
              data-testid="cg01-status-stale"
              className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-950/80 text-amber-300 border border-amber-800 flex items-center gap-1"
            >
              <Clock className="w-2.5 h-2.5" />
              Chậm update
            </span>
          ) : (
            <span
              data-testid="cg01-status-badge"
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium border flex items-center gap-1 ${
                toneBadgeClasses[tone] || toneBadgeClasses.neutral
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${toneDotClasses[tone] || toneDotClasses.neutral}`} />
              {stageLabel}
            </span>
          )}

          <button
            type="button"
            data-testid="cg01-expand-details-btn"
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-0.5 text-gray-400 hover:text-gray-200 rounded transition"
            title={isExpanded ? 'Thu gọn chi tiết' : 'Xem mã kỹ thuật'}
            aria-label="Toggle CG01 diagnostic details"
          >
            {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Meta Row: Direction, POI ID, and Timestamp */}
      <div className="flex items-center justify-between text-[11px] gap-2 pt-0.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          {/* Direction */}
          <span
            data-testid="cg01-direction-badge"
            className={`px-1.5 py-0.2 rounded font-mono font-semibold border ${directionBadgeClasses}`}
          >
            {direction}
          </span>

          {/* Compact POI */}
          {poiIdCompact ? (
            <span
              data-testid="cg01-poi-badge"
              className="px-1.5 py-0.2 rounded font-mono text-[10px] bg-charcoal-800 text-indigo-300 border border-charcoal-700 truncate max-w-[120px]"
              title={poiId || undefined}
            >
              POI: {poiIdCompact}
            </span>
          ) : (
            <span className="text-[10px] text-gray-500 font-mono">Chưa chọn POI</span>
          )}
        </div>

        {/* Timestamp */}
        <span
          data-testid="cg01-time-label"
          className="text-[10px] text-gray-400 font-mono shrink-0 flex items-center gap-1"
          title={`Thời điểm đánh giá: ${asOfMs}ms`}
        >
          <Clock className="w-2.5 h-2.5 text-gray-500" />
          {timeFormatted}
        </span>
      </div>

      {/* Primary Description Sentence (One sentence max - Rule 089) */}
      <p
        data-testid="cg01-description-text"
        className="text-[11px] text-gray-300 leading-snug line-clamp-2 bg-charcoal-900/40 p-1.5 rounded border border-charcoal-800/80"
      >
        {description}
      </p>

      {/* Expandable Technical Details (Rules 088, 118) */}
      {isExpanded && (
        <div
          data-testid="cg01-technical-details"
          className="pt-1.5 border-t border-charcoal-750/80 text-[10px] space-y-1 font-mono text-gray-400"
        >
          {poiId && (
            <div className="truncate">
              <span className="text-gray-500">Full POI: </span>
              <span className="text-indigo-300 select-all">{poiId}</span>
            </div>
          )}
          {primaryReason && (
            <div className="truncate">
              <span className="text-gray-500">Mã Reason: </span>
              <span className="text-amber-300/90 select-all">{primaryReason}</span>
            </div>
          )}
          {allReasons.length > 1 && (
            <div className="truncate">
              <span className="text-gray-500">Lý do phụ: </span>
              <span>{allReasons.slice(1).join(', ')}</span>
            </div>
          )}
          {armedOrderId && (
            <div className="truncate">
              <span className="text-gray-500">Lệnh chờ: </span>
              <span className="text-emerald-300 select-all">{armedOrderId}</span>
            </div>
          )}
          <div>
            <span className="text-gray-500">Epoch as_of: </span>
            <span>{asOfMs}</span>
          </div>
        </div>
      )}
    </div>
  );
};
