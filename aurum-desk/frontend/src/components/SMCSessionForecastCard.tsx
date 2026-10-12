import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Clock3, X } from 'lucide-react';
import { useSessionForecasts } from '../hooks/useSessionForecasts';
import { SESSION_CHIP_LABEL, defaultSessionKey } from '../types/sessionForecast';
import type { SessionKey } from '../types/sessionForecast';
import { buildForecastLines, dataStatusLabel } from '../utils/forecastLines';
import { SMCProbabilityBadge } from './SMCProbabilityBadge';

interface Props {
  symbol?: string;
  /** Bumps when scenarios change so the cached forecasts are re-read (GET only). */
  refreshKey?: number;
}

const SESSIONS: SessionKey[] = ['ASIA', 'LONDON', 'NY'];

/**
 * Compact Asia / Europe / NY reading. Forecasts are CONDITIONAL hypotheses ("nếu … thì …"), not events that
 * happened and not orders; analysing a session never enables entries in it (entry stays NY_ONLY by policy).
 */
export const SMCSessionForecastCard: React.FC<Props> = ({ symbol = 'XAUUSDT', refreshKey = 0 }) => {
  const { data, error } = useSessionForecasts(symbol, refreshKey);
  const [selected, setSelected] = useState<SessionKey>(() => defaultSessionKey(Date.now()));
  const [detailsOpen, setDetailsOpen] = useState(false);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  const forecast = data?.forecasts?.[selected] ?? null;
  const lines = useMemo(() => (forecast ? buildForecastLines(forecast) : []), [forecast]);
  const statusLabel = forecast ? dataStatusLabel(forecast) : null;

  useEffect(() => {
    if (!detailsOpen) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDetailsOpen(false);
        openerRef.current?.focus(); // focus restore
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [detailsOpen]);

  return (
    <section
      data-testid="session-forecast-card"
      aria-label="Dự đoán có điều kiện ba phiên"
      className="p-2.5 rounded-lg border border-charcoal-700 bg-charcoal-850 space-y-2"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-gray-300 flex items-center gap-1.5">
          <Clock3 className="w-3.5 h-3.5 text-cyan-400" />
          Dự đoán ba phiên
        </span>
        <div role="group" aria-label="Chọn phiên" className="flex gap-1">
          {SESSIONS.map((s) => (
            <button
              key={s}
              type="button"
              data-testid={`session-chip-${s}`}
              aria-pressed={selected === s}
              onClick={() => setSelected(s)}
              className={`px-2 py-0.5 rounded text-[11px] font-semibold border transition-colors ${
                selected === s
                  ? 'bg-cyan-900/60 border-cyan-600 text-cyan-100'
                  : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200'
              }`}
            >
              {SESSION_CHIP_LABEL[s]}
            </button>
          ))}
        </div>
      </div>

      {error && !forecast && <p className="text-[11px] text-amber-300">{error}</p>}
      {!error && !forecast && (
        <p className="text-[11px] text-gray-400" data-testid="forecast-empty">
          {data?.status === 'NOT_READY' || !data
            ? 'Chưa có dự đoán — chu kỳ đọc thị trường chưa chạy.'
            : 'Chưa có dự đoán cho phiên này.'}
        </p>
      )}

      {forecast && (
        <div className="space-y-1" data-testid={`forecast-${selected}`}>
          {statusLabel && (
            <span className="inline-block px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-800/60 text-[10px] text-amber-200">
              {statusLabel}
            </span>
          )}
          {lines.map((ln) => (
            <p key={ln.label} className="text-[11px] leading-snug text-gray-300">
              <span className="text-gray-500">{ln.label}: </span>
              {ln.text}
            </p>
          ))}
          <div className="flex items-center justify-between pt-1">
            <SMCProbabilityBadge probability={forecast.probability} />
            <button
              ref={openerRef}
              type="button"
              data-testid="forecast-details-open"
              onClick={() => setDetailsOpen(true)}
              className="px-2 py-0.5 rounded text-[11px] text-gray-300 bg-gray-800 hover:bg-gray-700 border border-gray-700"
            >
              Chi tiết
            </button>
          </div>
        </div>
      )}

      {detailsOpen && forecast && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Chi tiết phiên ${forecast.session_label_vi}`}
          data-testid="forecast-details"
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60"
        >
          <div className="w-full sm:max-w-lg max-h-[80vh] overflow-y-auto bg-charcoal-900 border border-charcoal-700 rounded-t-xl sm:rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-100">Phiên {forecast.session_label_vi} — chi tiết</h3>
              <button
                ref={closeRef}
                type="button"
                aria-label="Đóng chi tiết"
                onClick={() => {
                  setDetailsOpen(false);
                  openerRef.current?.focus();
                }}
                className="p-1 rounded text-gray-400 hover:text-white hover:bg-gray-800"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <section>
              <h4 className="text-[11px] text-gray-500 mb-1">Dữ kiện đã biết</h4>
              <ul className="list-disc pl-4 text-xs text-gray-300 space-y-0.5">
                {forecast.facts.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </section>
            {forecast.alternative && (
              <section>
                <h4 className="text-[11px] text-gray-500 mb-1">Giả thuyết thay thế ({forecast.alternative.direction ?? 'QUAN SÁT'})</h4>
                <p className="text-xs text-gray-300">{forecast.alternative.thesis_vi}</p>
              </section>
            )}
            <section>
              <h4 className="text-[11px] text-gray-500 mb-1">Điều kiện xác nhận</h4>
              <ul className="list-disc pl-4 text-xs text-gray-300 space-y-0.5">
                {forecast.confirmation.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </section>
            <section>
              <h4 className="text-[11px] text-gray-500 mb-1">Điều kiện hủy</h4>
              <ul className="list-disc pl-4 text-xs text-gray-300 space-y-0.5">
                {forecast.invalidation.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </section>
            <p className="text-[11px] text-gray-400 border-t border-gray-800 pt-2">{forecast.entry_note_vi}</p>
          </div>
        </div>
      )}
    </section>
  );
};
