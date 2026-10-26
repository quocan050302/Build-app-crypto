/**
 * frontend/src/components/OpportunityTimeline.tsx
 * Aurum V17.0 — Visual timeline for observed milestones with clear provenance.
 *
 * Rules 041 - 050, 124 - 126:
 * - Only renders milestones that were actually recorded.
 * - Distinguishes FUNNEL_STAGE_SNAPSHOT, COMMITTED_ORDER_MILESTONE, and TRACE.
 * - Displays first recorded time and last updated time for UPSERT stages.
 * - Does not invent ticks or intermediate steps.
 */

import React from 'react';
import { Clock, CheckCircle2, AlertCircle, ShieldAlert, ArrowRight, Info } from 'lucide-react';
import type { ObservedMilestone } from '../types/paperOpportunityMonitor';
import { formatMonitorTime } from '../utils/paperOpportunityText';

export interface OpportunityTimelineProps {
  milestones: ObservedMilestone[];
  className?: string;
}

export const OpportunityTimeline: React.FC<OpportunityTimelineProps> = ({
  milestones,
  className = '',
}) => {
  if (!milestones || milestones.length === 0) {
    return (
      <div className={`p-4 text-center text-xs text-gray-400 bg-charcoal-850/40 rounded-lg border border-charcoal-750 ${className}`}>
        Chưa có mốc tiến trình nào được ghi nhận cho cơ hội này.
      </div>
    );
  }

  const getStageIcon = (stage: string, _source: string) => {
    const s = stage.toUpperCase();
    if (s === 'FILLED' || s === 'CLOSED') {
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />;
    }
    if (s === 'ARMED') {
      return <Clock className="w-3.5 h-3.5 text-cyan-400" />;
    }
    if (s.includes('BLOCKED') || s === 'GUARD_BLOCKED') {
      return <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />;
    }
    if (s === 'INVALIDATED' || s === 'EXPIRED') {
      return <AlertCircle className="w-3.5 h-3.5 text-rose-400" />;
    }
    return <ArrowRight className="w-3.5 h-3.5 text-gray-400" />;
  };

  const getSourceBadge = (source: string) => {
    switch (source) {
      case 'COMMITTED_ORDER_MILESTONE':
        return (
          <span className="px-1 py-0.2 rounded text-[9px] font-mono bg-emerald-950/70 text-emerald-300 border border-emerald-800/80">
            Lệnh Thực Thi
          </span>
        );
      case 'TRACE':
        return (
          <span className="px-1 py-0.2 rounded text-[9px] font-mono bg-indigo-950/70 text-indigo-300 border border-indigo-800/80">
            Đo Lường Telemetry
          </span>
        );
      case 'FUNNEL_STAGE_SNAPSHOT':
      default:
        return (
          <span className="px-1 py-0.2 rounded text-[9px] font-mono bg-charcoal-800 text-gray-400 border border-charcoal-700">
            Snapshot Mốc
          </span>
        );
    }
  };

  return (
    <div className={`space-y-3 ${className}`} data-testid="opportunity-timeline">
      <div className="relative border-l border-charcoal-700 ml-2 pl-4 space-y-4">
        {milestones.map((m, idx) => {
          const timeStr = formatMonitorTime(m.snapshot_as_of_ms || m.first_recorded_at_ms);
          const hasUpdatedTime =
            m.first_recorded_at_ms &&
            m.last_updated_at_ms &&
            m.first_recorded_at_ms !== m.last_updated_at_ms;

          return (
            <div key={m.id || idx} className="relative group" data-testid={`timeline-item-${m.stage}`}>
              {/* Dot on line */}
              <div className="absolute -left-[23px] top-0.5 w-4 h-4 rounded-full bg-charcoal-900 border border-charcoal-700 flex items-center justify-center">
                {getStageIcon(m.stage, m.source)}
              </div>

              <div className="bg-charcoal-850/60 p-2.5 rounded-lg border border-charcoal-750/70 text-xs space-y-1">
                {/* Header: Stage label + Source badge + Time */}
                <div className="flex items-center justify-between gap-1 flex-wrap">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-gray-200">{m.stage_label_vi || m.stage}</span>
                    {getSourceBadge(m.source)}
                  </div>
                  <span className="text-[11px] font-mono text-gray-400 flex items-center gap-1">
                    <Clock className="w-2.5 h-2.5 text-gray-500" />
                    {timeStr}
                  </span>
                </div>

                {/* Reason description */}
                {m.reason_summary_vi && (
                  <p className="text-[11px] text-gray-300 leading-snug">
                    {m.reason_summary_vi}
                  </p>
                )}

                {/* Evidence count or IDs */}
                {m.evidence_ids && m.evidence_ids.length > 0 && (
                  <div className="text-[10px] text-gray-400 flex items-center gap-1 flex-wrap pt-0.5">
                    <span className="text-gray-500">Bằng chứng:</span>
                    {m.evidence_ids.map((ev, eIdx) => (
                      <span key={eIdx} className="font-mono text-indigo-300/90 bg-charcoal-900 px-1 py-0.2 rounded border border-charcoal-800">
                        {ev}
                      </span>
                    ))}
                  </div>
                )}

                {/* UPSERT timing notice */}
                {hasUpdatedTime && (
                  <div className="text-[10px] text-amber-300/80 bg-amber-950/30 px-1.5 py-0.5 rounded border border-amber-900/40 flex items-center gap-1">
                    <Info className="w-2.5 h-2.5 shrink-0" />
                    <span>
                      Ghi nhận đầu: {formatMonitorTime(m.first_recorded_at_ms)} • Cập nhật cuối: {formatMonitorTime(m.last_updated_at_ms)}
                    </span>
                  </div>
                )}

                {m.limitation && (
                  <p className="text-[10px] text-gray-500 italic">
                    *{m.limitation}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
