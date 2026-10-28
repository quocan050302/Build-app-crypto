import React, { useState, useEffect } from 'react';
import { getOrderStopEvidence } from '../api/client';

export interface SLTPIntelligenceProps {
  orderId?: string;
  evidenceData?: any;
}

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
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400">
        Đang tải thông tin SL/TP Intelligence...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-rose-900/40 bg-rose-950/20 p-4 text-xs text-rose-300">
        <div className="font-semibold mb-1">Lỗi tải dữ liệu</div>
        <div>{error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-xs text-slate-500">
        Chọn lệnh hoặc cơ hội để xem chi tiết SL/TP Intelligence.
      </div>
    );
  }

  if (data.status === 'UNAVAILABLE_LEGACY') {
    return (
      <div className="rounded-xl border border-amber-900/30 bg-amber-950/10 p-4 text-xs text-amber-300/80">
        <div className="flex items-center gap-2 font-medium">
          <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-mono text-amber-300">
            NOT_RECORDED
          </span>
          <span>Lệnh lịch sử không có bản ghi SL cấu trúc (Legacy FVG).</span>
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          Lệnh {data.order_id || orderId} đã được khớp trước khi áp dụng V17.8 Stop Intelligence.
        </p>
      </div>
    );
  }

  const stopPlacement = data.stop_placement || {};
  const volCtx = stopPlacement.volatility_context || {};
  const anchor = stopPlacement.anchor || {};
  const unicorn = data.unicorn_evidence || stopPlacement.unicorn_evidence;
  const isUnicornValid = unicorn && (unicorn.is_valid || unicorn.validity_status === 'CONFIRMED' || unicorn.validity_status === 'VALID');

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 shadow-sm text-xs space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <h4 className="font-semibold text-slate-100 flex items-center gap-1.5">
            <span>SL/TP Intelligence</span>
            <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-mono text-indigo-300 border border-indigo-500/30">
              V17.8
            </span>
          </h4>
          <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-300">
            {data.direction || 'SHORT'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-400">Chế độ:</span>
          <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-200">
            {data.stop_mode_at_arm || 'SHADOW_COMPARE'}
          </span>
        </div>
      </div>

      {/* Grid Comparisons */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Giá vào (Entry)</div>
          <div className="font-mono text-sm font-semibold text-slate-100 mt-0.5">
            {data.actual_entry ? Number(data.actual_entry).toFixed(2) : 'N/A'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Stop Loss đã chọn</div>
          <div className="font-mono text-sm font-semibold text-rose-400 mt-0.5">
            {data.actual_sl ? Number(data.actual_sl).toFixed(2) : 'N/A'}
          </div>
          <div className="text-[9px] text-slate-500 mt-0.5">
            Biến thể: {stopPlacement.applied_variant || 'C'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Take Profit (Mục tiêu)</div>
          <div className="font-mono text-sm font-semibold text-emerald-400 mt-0.5">
            {data.take_profit ? Number(data.take_profit).toFixed(2) : 'N/A'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Khoảng cách bảo vệ</div>
          <div className="font-mono text-sm font-semibold text-amber-300 mt-0.5">
            {data.min_protective_distance ? `${Number(data.min_protective_distance).toFixed(2)} USD` : 'N/A'}
          </div>
          {data.protective_expansion_applied && (
            <div className="text-[9px] text-amber-400 font-medium">★ Đã mở rộng</div>
          )}
        </div>
      </div>

      {/* Stop Loss Comparison Table */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <div className="text-[11px] font-semibold text-slate-300 mb-2">So sánh các biến thể Stop Loss</div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">A (Legacy FVG)</div>
            <div className="font-mono text-xs text-slate-300 mt-1">
              {data.legacy_sl ? Number(data.legacy_sl).toFixed(2) : 'N/A'}
            </div>
          </div>
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">B/C (Cấu trúc V17.7)</div>
            <div className="font-mono text-xs text-indigo-300 mt-1">
              {data.structural_sl ? Number(data.structural_sl).toFixed(2) : (anchor.price ? Number(anchor.price).toFixed(2) : 'N/A')}
            </div>
          </div>
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">C (Unicorn SL)</div>
            <div className="font-mono text-xs text-emerald-300 mt-1">
              {data.unicorn_sl_candidate ? Number(data.unicorn_sl_candidate).toFixed(2) : (isUnicornValid ? 'Hợp lệ' : 'Không có')}
            </div>
          </div>
        </div>
      </div>

      {/* ICT Unicorn Status */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[11px] font-semibold text-slate-300">Cấu trúc ICT Unicorn Model</span>
          <span className={`rounded px-1.5 py-0.5 text-[9px] font-mono font-medium ${
            isUnicornValid ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-400'
          }`}>
            {isUnicornValid ? 'UNICORN_CONFIRMED' : 'NO_UNICORN_CONFLUENCE'}
          </span>
        </div>
        {unicorn ? (
          <div className="grid grid-cols-2 gap-2 text-[10px] text-slate-400 mt-2">
            <div>Breaker Block: <span className="font-mono text-slate-200">{unicorn.breaker_id || 'N/A'}</span></div>
            <div>Mức Distal Invalidation: <span className="font-mono text-slate-200">{unicorn.invalidation_price || 'N/A'}</span></div>
            <div>Vùng Giao Thoa: <span className="font-mono text-slate-200">[{unicorn.overlap_low?.toFixed(2)} - {unicorn.overlap_high?.toFixed(2)}]</span></div>
            <div>Trạng thái: <span className="font-mono text-slate-200">{unicorn.validity_status || 'N/A'}</span></div>
          </div>
        ) : (
          <p className="text-[10px] text-slate-500">Setup không có hội tụ Breaker Block và FVG hợp lệ theo quy chuẩn Unicorn.</p>
        )}
      </div>

      {/* Microstructure Noise Metrics */}
      <div className="grid grid-cols-3 gap-2 text-[10px] text-slate-400">
        <div>ATR M1: <span className="font-mono text-slate-200">{volCtx.atr_m1 ? Number(volCtx.atr_m1).toFixed(4) : 'N/A'}</span></div>
        <div>ATR M5: <span className="font-mono text-slate-200">{volCtx.atr_m5 ? Number(volCtx.atr_m5).toFixed(4) : 'N/A'}</span></div>
        <div>Spread: <span className="font-mono text-slate-200">{volCtx.spread ? Number(volCtx.spread).toFixed(4) : 'N/A'}</span></div>
      </div>

      {/* Explanation */}
      {data.ui_explanation_vi && (
        <div className="rounded-lg bg-indigo-950/20 border border-indigo-900/30 p-2.5 text-[11px] text-indigo-300">
          {data.ui_explanation_vi}
        </div>
      )}
    </div>
  );
};
