import React from 'react';
import type { SessionProbability } from '../types/sessionForecast';

interface Props {
  probability?: SessionProbability | null;
  className?: string;
}

/**
 * Honest probability badge. A number is only ever shown when a VALIDATED model produced it, and then it is
 * labelled with its event/horizon (e.g. "TP trước SL trong 24 nến") — never as a generic "winrate" and never
 * derived from a checklist score. Otherwise it says there is no validated probability yet.
 */
export const SMCProbabilityBadge: React.FC<Props> = ({ probability, className = '' }) => {
  const p = probability?.p_tp_before_sl ?? null;
  const validated = probability?.status === 'VALIDATED' && typeof p === 'number' && Number.isFinite(p);

  if (!validated) {
    return (
      <span
        data-testid="probability-badge"
        data-status="UNAVAILABLE"
        title={probability?.note_vi || probability?.note || 'Chưa có mô hình xác suất được kiểm định trên dữ liệu ngoài mẫu.'}
        className={`inline-flex items-center px-1.5 py-0.5 rounded border border-gray-700 bg-gray-800/60 text-[10px] text-gray-400 ${className}`}
      >
        Chưa có xác suất được kiểm định
      </span>
    );
  }

  const horizon = probability?.horizon ? ` trong ${probability.horizon}` : '';
  const tip = [
    `n = ${probability?.support_n ?? 'n/a'}`,
    probability?.oos_from_ms && probability?.oos_to_ms
      ? `OOS ${new Date(probability.oos_from_ms).toLocaleDateString('vi-VN')} – ${new Date(probability.oos_to_ms).toLocaleDateString('vi-VN')}`
      : null,
    probability?.uncertainty ? `Bất định: ${JSON.stringify(probability.uncertainty)}` : null,
    probability?.model_version ? `Mô hình ${probability.model_version}` : null,
  ].filter(Boolean).join(' · ');

  return (
    <span
      data-testid="probability-badge"
      data-status="VALIDATED"
      title={tip}
      className={`inline-flex items-center px-1.5 py-0.5 rounded border border-cyan-800 bg-cyan-950/50 text-[10px] text-cyan-200 ${className}`}
    >
      Xác suất TP trước SL{horizon}: {(p * 100).toFixed(0)}%
    </span>
  );
};
