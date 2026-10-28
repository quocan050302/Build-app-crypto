import React, { useState, useEffect } from 'react';
import { getOrderStopEvidence } from '../api/client';

export interface SLTPIntelligenceProps {
  orderId?: string;
  evidenceData?: any;
}

const extractMetricValue = (raw: any): number | null => {
  if (raw == null) return null;
  if (typeof raw === 'number' && !Number.isNaN(raw)) return raw;
  if (typeof raw === 'object' && raw.value != null && !Number.isNaN(Number(raw.value))) {
    return Number(raw.value);
  }
  const parsed = Number(raw);
  return Number.isNaN(parsed) ? null : parsed;
};

export const SLTPIntelligenceView: React.FC<SLTPIntelligenceProps> = ({ orderId, evidenceData }) => {
  const [data, setData] = useState<any>(evidenceData || null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (evidenceData) {
      setData(evidenceData);
      return;
    }
    if (!orderId) {
      return;
    }
    let isMounted = true;
    setIsLoading(true);
    setError(null);
    getOrderStopEvidence(orderId)
      .then((res) => {
        if (isMounted) {
          setData(res);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (isMounted) {
          setError(err?.message || 'Không thể tải bằng chứng dừng lỗ');
          setIsLoading(false);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [orderId, evidenceData]);

  if (isLoading) {
    return (
      <div
        data-testid="sltp-intelligence-loading"
        className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400"
      >
        Đang tải thông tin SL/TP Intelligence...
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="sltp-intelligence-error"
        className="rounded-xl border border-rose-900/40 bg-rose-950/20 p-4 text-xs text-rose-300"
      >
        <div className="font-semibold mb-1">Lỗi tải dữ liệu</div>
        <div>{error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div
        data-testid="sltp-intelligence-empty"
        className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-xs text-slate-500"
      >
        Chọn lệnh hoặc cơ hội để xem chi tiết SL/TP Intelligence.
      </div>
    );
  }

  if (data.status === 'UNAVAILABLE_LEGACY') {
    return (
      <div
        data-testid="sltp-intelligence-legacy"
        className="rounded-xl border border-amber-900/30 bg-amber-950/10 p-4 text-xs text-amber-300/80"
      >
        <div className="flex items-center gap-2 font-medium">
          <span
            data-testid="sltp-legacy-status-badge"
            className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-mono text-amber-300"
          >
            UNAVAILABLE_LEGACY
          </span>
          <span
            data-testid="sltp-not-recorded-badge"
            className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-300"
          >
            NOT_RECORDED
          </span>
          <span>Lệnh lịch sử không có bản ghi SL cấu trúc (Legacy FVG).</span>
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          Lệnh {data.order_id || orderId} không có dữ liệu V17.8 Stop Intelligence (Chế độ: {data.stop_mode_at_arm || 'NOT_RECORDED'} · Biến thể: {data.applied_variant || 'NOT_RECORDED'}).
        </p>
      </div>
    );
  }

  const stopPlacement = data.stop_placement || {};
  const volCtx = stopPlacement.volatility_context || {};
  const anchor = stopPlacement.anchor || {};
  const buffer = stopPlacement.buffer || {};
  const metrics = stopPlacement.metrics || {};
  const unicorn = data.unicorn_evidence || stopPlacement.unicorn_evidence || null;
  const isUnicornValid = Boolean(
    unicorn &&
      unicorn.is_valid === true &&
      (unicorn.validity_status === 'CONFIRMED' || unicorn.validity_status === 'VALID')
  );

  // Never fabricate applied_variant or stop_mode_at_arm when missing
  const stopModeAtArm = data.stop_mode_at_arm || stopPlacement.mode || 'NOT_RECORDED';
  const appliedVariant = data.applied_variant || stopPlacement.applied_variant || 'NOT_RECORDED';
  const direction = data.direction || 'UNKNOWN';
  const executedSl = data.executed_sl ?? data.actual_sl ?? null;
  const actualEntry = data.actual_entry ?? data.planned_entry ?? null;
  const takeProfit = data.take_profit ?? null;
  const minProtectiveDist =
    data.min_protective_distance ??
    buffer.buffer_price ??
    null;

  const atrM1Val = extractMetricValue(volCtx.atr_m1);
  const atrM5Val = extractMetricValue(volCtx.atr_m5);
  const spreadVal =
    extractMetricValue(volCtx.spread) ??
    extractMetricValue(volCtx.spread_snapshot?.spread);

  const quantityVal = extractMetricValue(data.quantity);
  const initialRiskVal =
    extractMetricValue(data.initial_risk_usdt) ??
    extractMetricValue(metrics.risk_usdt);
  const netRrVal =
    extractMetricValue(data.estimated_net_rr) ??
    extractMetricValue(metrics.net_rr);

  const invalidationLevel =
    (isUnicornValid ? extractMetricValue(unicorn?.invalidation_price) : null) ??
    extractMetricValue(anchor?.price);

  const isStructuralExecuted =
    appliedVariant === 'C' && stopModeAtArm === 'PAPER_STRUCTURAL';

  return (
    <div
      data-testid="sltp-intelligence-view"
      className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 shadow-sm text-xs space-y-4"
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-slate-800 pb-3 gap-2">
        <div className="flex items-center gap-2">
          <h4 className="font-semibold text-slate-100 flex items-center gap-1.5">
            <span>SL/TP Intelligence</span>
            <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-mono text-indigo-300 border border-indigo-500/30">
              V17.8.0.1
            </span>
          </h4>
          <span
            data-testid="sltp-direction-badge"
            className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-300"
          >
            {direction}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-400">Chế độ tại ARM:</span>
          <span
            data-testid="sltp-mode-badge"
            className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-200"
          >
            {stopModeAtArm}
          </span>
          <span className="text-[11px] text-slate-400">Biến thể thực thi:</span>
          <span
            data-testid="sltp-applied-variant-badge"
            className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-amber-300"
          >
            {appliedVariant}
          </span>
        </div>
      </div>

      {/* Primary Execution vs Protection Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Giá vào (Entry)</div>
          <div
            data-testid="sltp-entry-price"
            className="font-mono text-sm font-semibold text-slate-100 mt-0.5"
          >
            {actualEntry != null ? Number(actualEntry).toFixed(2) : 'NOT_RECORDED'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Stop Loss thực thi (Executed SL)</div>
          <div
            data-testid="sltp-executed-sl"
            className="font-mono text-sm font-semibold text-rose-400 mt-0.5"
          >
            {executedSl != null ? Number(executedSl).toFixed(2) : 'NOT_RECORDED'}
          </div>
          <div className="text-[9px] text-slate-400 mt-0.5">
            Biến thể áp dụng: <span className="font-mono">{appliedVariant}</span>
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Take Profit (Mục tiêu)</div>
          <div
            data-testid="sltp-take-profit"
            className="font-mono text-sm font-semibold text-emerald-400 mt-0.5"
          >
            {takeProfit != null ? Number(takeProfit).toFixed(2) : 'NOT_RECORDED'}
          </div>
          <div className="text-[9px] text-slate-500 mt-0.5">
            {stopPlacement.tp_rationale || data.tp_rationale || 'Target thanh khoản / cấu trúc'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Khoảng cách bảo vệ tối thiểu</div>
          <div
            data-testid="sltp-protective-distance"
            className="font-mono text-sm font-semibold text-amber-300 mt-0.5"
          >
            {minProtectiveDist != null ? `${Number(minProtectiveDist).toFixed(2)} USD` : 'NOT_RECORDED'}
          </div>
          {data.protective_expansion_applied && (
            <div className="text-[9px] text-amber-400 font-medium">★ Đã mở rộng bảo vệ</div>
          )}
        </div>
      </div>

      {/* Financial Authorization Summary (Quantity, Net Risk USDT, Net R:R) */}
      <div
        data-testid="sltp-financial-summary"
        className="grid grid-cols-3 gap-3 rounded-lg border border-slate-800/80 bg-slate-950/40 p-2.5 text-[11px]"
      >
        <div>
          <span className="text-slate-400 block text-[10px]">Khối lượng được duyệt (Quantity)</span>
          <span data-testid="sltp-authorized-qty" className="font-mono font-semibold text-slate-200">
            {quantityVal != null ? `${quantityVal.toFixed(2)} oz` : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          <span className="text-slate-400 block text-[10px]">Rủi ro ròng (Net Risk USDT)</span>
          <span data-testid="sltp-net-risk-usdt" className="font-mono font-semibold text-rose-300">
            {initialRiskVal != null ? `${initialRiskVal.toFixed(2)} USDT` : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          <span className="text-slate-400 block text-[10px]">Tỷ lệ R:R Ròng (Net R:R)</span>
          <span data-testid="sltp-net-rr" className="font-mono font-semibold text-emerald-300">
            {netRrVal != null ? `${netRrVal.toFixed(2)}R` : 'NOT_RECORDED'}
          </span>
        </div>
      </div>

      {/* Stop Loss Comparison Table: Executed vs Research Candidate SL */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-semibold text-slate-300">
            Đối chiếu Stop Loss Thực Thi vs Ứng Viên Nghiên Cứu (Shadow / Research)
          </span>
          <span className="text-[10px] font-mono text-slate-400">
            {isStructuralExecuted ? 'ĐANG THỰC THI CẤU TRÚC (C)' : 'CHẾ ĐỘ ĐỐI CHIẾU / LEGACY'}
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">
              A (Legacy FVG) {appliedVariant === 'A' ? '· [Đã thực thi]' : '· [Đối chiếu]'}
            </div>
            <div data-testid="sltp-variant-a-sl" className="font-mono text-xs text-slate-300 mt-1">
              {(data.legacy_sl ?? data.stop_comparison?.variant_a?.stop_loss) != null
                ? Number(data.legacy_sl ?? data.stop_comparison?.variant_a?.stop_loss).toFixed(2)
                : 'NOT_RECORDED'}
            </div>
          </div>
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">
              B/C (Cấu trúc V17.7) {isStructuralExecuted ? '· [Đã thực thi]' : '· [Ứng viên nghiên cứu]'}
            </div>
            <div data-testid="sltp-variant-b-sl" className="font-mono text-xs text-indigo-300 mt-1">
              {(data.structural_sl ?? data.stop_comparison?.variant_b?.stop_loss) != null
                ? Number(data.structural_sl ?? data.stop_comparison?.variant_b?.stop_loss).toFixed(2)
                : anchor.price != null
                ? Number(anchor.price).toFixed(2)
                : 'NOT_RECORDED'}
            </div>
          </div>
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">
              C (Unicorn SL) {isStructuralExecuted && isUnicornValid ? '· [Đã thực thi]' : '· [Ứng viên nghiên cứu]'}
            </div>
            <div data-testid="sltp-variant-c-sl" className="font-mono text-xs text-emerald-300 mt-1">
              {(data.unicorn_sl_candidate ?? data.stop_comparison?.variant_c?.stop_loss) != null
                ? Number(data.unicorn_sl_candidate ?? data.stop_comparison?.variant_c?.stop_loss).toFixed(2)
                : 'NOT_RECORDED'}
            </div>
          </div>
        </div>
      </div>

      {/* ICT Unicorn & Provenance Status */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[11px] font-semibold text-slate-300">Cấu trúc ICT Unicorn & Nguồn Gốc Nhân Quả</span>
          <span
            data-testid="sltp-unicorn-status-badge"
            className={`rounded px-1.5 py-0.5 text-[9px] font-mono font-medium ${
              isUnicornValid
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                : 'bg-slate-800 text-slate-400'
            }`}
          >
            {isUnicornValid ? 'UNICORN_CONFIRMED' : unicorn?.validity_status || 'NO_UNICORN_CONFLUENCE'}
          </span>
        </div>
        {unicorn ? (
          <div className="grid grid-cols-2 gap-2 text-[10px] text-slate-400 mt-2">
            <div>
              Breaker ID:{' '}
              <span data-testid="sltp-breaker-id" className="font-mono text-slate-200">
                {unicorn.breaker_id || 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Selected FVG ID:{' '}
              <span data-testid="sltp-fvg-id" className="font-mono text-slate-200">
                {unicorn.selected_fvg_id || unicorn.fvg_id || 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Mức Invalidation Cấu Trúc:{' '}
              <span data-testid="sltp-invalidation-price" className="font-mono text-slate-200">
                {invalidationLevel != null ? Number(invalidationLevel).toFixed(2) : 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Vùng Giao Thoa:{' '}
              <span className="font-mono text-slate-200">
                {unicorn.overlap_low != null && unicorn.overlap_high != null
                  ? `[${Number(unicorn.overlap_low).toFixed(2)} - ${Number(unicorn.overlap_high).toFixed(2)}]`
                  : 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Episode ID:{' '}
              <span className="font-mono text-slate-200">
                {unicorn.setup_episode_id || 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Trạng thái kiểm định:{' '}
              <span className="font-mono text-slate-200">
                {unicorn.validity_status || 'NOT_RECORDED'}
              </span>
            </div>
          </div>
        ) : (
          <div className="text-[10px] text-slate-400 space-y-1">
            <div>
              Anchor Cấu Trúc:{' '}
              <span data-testid="sltp-anchor-summary" className="font-mono text-slate-200">
                {anchor.kind
                  ? `${anchor.kind} (${anchor.id || 'no-id'}) @ ${
                      invalidationLevel != null ? Number(invalidationLevel).toFixed(2) : 'NOT_RECORDED'
                    }`
                  : 'NOT_RECORDED'}
              </span>
            </div>
            <p className="text-slate-500">
              Setup không có hội tụ Breaker Block và FVG hợp lệ theo quy chuẩn Unicorn.
            </p>
          </div>
        )}
      </div>

      {/* Microstructure Noise & Volatility Context */}
      <div
        data-testid="sltp-volatility-context"
        className="grid grid-cols-3 gap-2 text-[10px] text-slate-400"
      >
        <div>
          ATR M1:{' '}
          <span data-testid="sltp-atr-m1" className="font-mono text-slate-200">
            {atrM1Val != null ? atrM1Val.toFixed(4) : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          ATR M5:{' '}
          <span data-testid="sltp-atr-m5" className="font-mono text-slate-200">
            {atrM5Val != null ? atrM5Val.toFixed(4) : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          Spread:{' '}
          <span data-testid="sltp-spread" className="font-mono text-slate-200">
            {spreadVal != null ? spreadVal.toFixed(4) : 'NOT_RECORDED'}
          </span>
        </div>
      </div>

      {/* Explanation */}
      {data.ui_explanation_vi && (
        <div
          data-testid="sltp-explanation"
          className="rounded-lg bg-indigo-950/20 border border-indigo-900/30 p-2.5 text-[11px] text-indigo-300"
        >
          {data.ui_explanation_vi}
        </div>
      )}
    </div>
  );
};
