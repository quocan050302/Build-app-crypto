import React, { useState } from 'react';
import { StopComparisonModal } from './StopComparisonModal';

interface StopPlacementDetailsProps {
  order: any;
  chartEvidence?: any;
}

export const StopPlacementDetails: React.FC<StopPlacementDetailsProps> = ({ order, chartEvidence }) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [showTechnical, setShowTechnical] = useState(false);

  // Extract stop placement details from chartEvidence or order.cost_snapshot
  let stopPlacement = chartEvidence?.stop_placement;
  let stopComparison = chartEvidence?.stop_comparison;

  if (!stopPlacement && order?.cost_snapshot) {
    try {
      const snap = typeof order.cost_snapshot === 'string' ? JSON.parse(order.cost_snapshot) : order.cost_snapshot;
      stopPlacement = snap.stop_placement;
      stopComparison = snap.stop_comparison;
    } catch {
      // ignore
    }
  }

  const actualSl = order?.stop_loss != null ? Number(order.stop_loss).toFixed(2) : '—';
  const mode = stopPlacement?.mode || 'LEGACY';
  const version = stopPlacement?.policy_version || '1';

  const anchor = stopPlacement?.anchor;
  const anchorDesc = anchor
    ? `${anchor.kind} tại ${Number(anchor.price).toFixed(2)} (${anchor.timeframe || '1M'})`
    : 'Chưa có bằng chứng cấu trúc';

  const buffer = stopPlacement?.buffer;
  const bufferTotal = buffer?.buffer_price != null ? Number(buffer.buffer_price).toFixed(2) : '—';
  const dominant = buffer?.dominant_source ? ` (chính: ${buffer.dominant_source})` : '';

  const metrics = stopPlacement?.metrics;
  const priceDist = metrics?.price_distance != null ? Number(metrics.price_distance).toFixed(2) : '—';
  const atrM1 = stopPlacement?.volatility_context?.atr_m1?.value != null
    ? Number(stopPlacement.volatility_context.atr_m1.value).toFixed(2)
    : '—';

  const riskUsdt = metrics?.risk_usdt != null
    ? Number(metrics.risk_usdt).toFixed(2)
    : order?.initial_risk_usdt != null
    ? Number(order.initial_risk_usdt).toFixed(2)
    : '—';
  const netRr = order?.estimated_net_rr != null
    ? Number(order.estimated_net_rr).toFixed(2)
    : order?.net_rr != null
    ? Number(order.net_rr).toFixed(2)
    : '—';

  const status = stopPlacement?.status || (order?.stop_loss ? 'LEGACY_FIXED' : 'UNAVAILABLE');
  const appliedVariant = stopPlacement?.applied_variant || (mode === 'PAPER_STRUCTURAL' ? 'C' : 'A');
  const isStructuralVerified = status === 'VALID' && !!anchor && appliedVariant === 'C';

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
      <div className="flex items-center justify-between pb-2 border-b border-slate-800/80 mb-2">
        <span className="font-semibold text-slate-200">Chi Tiết Điểm Dừng Lỗ (SL)</span>
        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300 font-mono">
          {mode} v{version}
        </span>
      </div>

      {/* 6 concise lines */}
      <div className="space-y-1.5 text-slate-300">
        <div className="flex justify-between">
          <span className="text-slate-400">1. SL Thực tế:</span>
          <span className="font-mono font-medium text-slate-100">{actualSl}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-400">2. Điểm bảo vệ:</span>
          <span className="font-mono text-slate-200 truncate max-w-[200px]" title={anchorDesc}>
            {anchorDesc}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-400">3. Buffer:</span>
          <span className="font-mono text-slate-200">
            {bufferTotal}
            {dominant}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-400">4. Khoảng cách / ATR:</span>
          <span className="font-mono text-slate-200">
            {priceDist} (ATR: {atrM1})
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-400">5. Rủi ro / Net R:R:</span>
          <span className="font-mono text-slate-200">
            {riskUsdt} USDT (R:R: <span className="text-emerald-400">{netRr}</span>)
          </span>
        </div>
        <div className="flex justify-between items-center pt-1 border-t border-slate-800/40">
          <span className="text-slate-400">6. Đánh giá:</span>
          <span
            className={`font-medium text-[11px] ${
              isStructuralVerified
                ? 'text-emerald-400'
                : status === 'VALID' || status === 'LEGACY_FIXED' || status === 'WAITING_EVIDENCE'
                ? 'text-amber-400'
                : 'text-slate-400'
            }`}
          >
            {isStructuralVerified
              ? 'Xác minh cấu trúc (C)'
              : status === 'VALID' && appliedVariant === 'A'
              ? 'SL Legacy hợp lệ (A)'
              : status === 'WAITING_EVIDENCE'
              ? 'Chờ bằng chứng cấu trúc'
              : status === 'LEGACY_FIXED'
              ? 'SL cố định legacy'
              : 'Thiếu dữ liệu'}
          </span>
        </div>
      </div>

      {/* Action buttons */}
      <div className="mt-3 flex items-center justify-between pt-2 border-t border-slate-800/60">
        <button
          onClick={() => setShowTechnical(!showTechnical)}
          className="text-[11px] text-slate-400 hover:text-slate-200 underline decoration-slate-600"
        >
          {showTechnical ? 'Ẩn kỹ thuật ▲' : 'Kỹ thuật ▼'}
        </button>

        <button
          onClick={() => setIsModalOpen(true)}
          className="rounded bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 px-2 py-1 text-[11px] font-medium transition"
        >
          Xem so sánh SL
        </button>
      </div>

      {/* Technical Accordion */}
      {showTechnical && (
        <div className="mt-2.5 rounded bg-slate-950 p-2 text-[10px] font-mono text-slate-400 space-y-1 border border-slate-800/80">
          <div>ATR Method: {stopPlacement?.volatility_context?.atr_m1?.method || (atrM1 !== '—' ? 'SMA_TR_V1' : 'Chưa có dữ liệu ATR')}</div>
          <div>Basis: {anchor?.price_basis || 'UNKNOWN'}</div>
          <div>Occurred at: {anchor?.occurred_at_ms ? new Date(anchor.occurred_at_ms).toLocaleTimeString() : '—'}</div>
          <div>Known at: {anchor?.known_at_ms ? new Date(anchor.known_at_ms).toLocaleTimeString() : '—'}</div>
          {stopPlacement?.reasons?.length > 0 && (
            <div className="text-amber-400">Reasons: {stopPlacement.reasons.join(', ')}</div>
          )}
        </div>
      )}

      {/* Modal */}
      <StopComparisonModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        stopComparison={stopComparison}
        stopPlacement={stopPlacement}
      />
    </div>
  );
};
