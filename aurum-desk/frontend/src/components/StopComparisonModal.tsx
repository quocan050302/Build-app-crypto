import React from 'react';

interface StopComparisonModalProps {
  isOpen: boolean;
  onClose: () => void;
  stopComparison?: any;
  stopPlacement?: any;
}

export const StopComparisonModal: React.FC<StopComparisonModalProps> = ({
  isOpen,
  onClose,
  stopComparison,
  stopPlacement,
}) => {
  if (!isOpen) return null;

  const variants = stopComparison?.variants || {};
  const varA = variants['A_LEGACY'] || variants['A'] || null;
  const varB = variants['B_STRUCTURAL_TICK'] || variants['B'] || null;
  const varC = variants['C_STRUCTURAL_DYNAMIC'] || variants['C'] || null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div>
            <h3 className="text-lg font-semibold text-slate-100">So Sánh Điểm Dừng Lỗ (A / B / C)</h3>
            <p className="text-xs text-slate-400">
              Mô hình nghiên cứu PAPER song song — Không làm phát sinh thêm lệnh giao dịch thực tế
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
          >
            ✕
          </button>
        </div>

        <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Variant A: Legacy */}
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-xs text-amber-400 uppercase tracking-wider">Biến Thể A</span>
              <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">
                {stopPlacement?.mode === 'LEGACY' || stopPlacement?.mode === 'SHADOW_COMPARE' ? 'Thực thi' : 'Legacy'}
              </span>
            </div>
            <div className="text-xs text-slate-400 mb-2">SL Gốc (Mép FVG/Tick)</div>
            <div className="text-lg font-mono font-bold text-slate-200">
              {varA?.stop_loss != null ? varA.stop_loss.toFixed(2) : (stopComparison?.legacy_sl?.toFixed(2) ?? '—')}
            </div>
            <div className="mt-3 space-y-1.5 text-[11px] text-slate-300">
              <div className="flex justify-between">
                <span className="text-slate-500">Khoảng SL:</span>
                <span className="font-mono">{varA?.price_distance != null ? varA.price_distance.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Khối lượng:</span>
                <span className="font-mono">{varA?.quantity != null ? varA.quantity.toFixed(3) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Net R:R:</span>
                <span className="font-mono text-emerald-400">{varA?.net_rr != null ? varA.net_rr.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Trạng thái:</span>
                <span className="font-medium text-slate-300">{varA?.is_valid ? 'Hợp lệ' : 'Không đạt'}</span>
              </div>
            </div>
          </div>

          {/* Variant B: Structural Anchor + Tick Buffer */}
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-xs text-sky-400 uppercase tracking-wider">Biến Thể B</span>
              <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-300">Mô phỏng</span>
            </div>
            <div className="text-xs text-slate-400 mb-2">Cấu trúc + Tick buffer</div>
            <div className="text-lg font-mono font-bold text-slate-200">
              {varB?.stop_loss != null ? varB.stop_loss.toFixed(2) : '—'}
            </div>
            <div className="mt-3 space-y-1.5 text-[11px] text-slate-300">
              <div className="flex justify-between">
                <span className="text-slate-500">Khoảng SL:</span>
                <span className="font-mono">{varB?.price_distance != null ? varB.price_distance.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Khối lượng:</span>
                <span className="font-mono">{varB?.quantity != null ? varB.quantity.toFixed(3) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Net R:R:</span>
                <span className="font-mono text-emerald-400">{varB?.net_rr != null ? varB.net_rr.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Trạng thái:</span>
                <span className="font-medium text-slate-300">{varB?.is_valid ? 'Hợp lệ' : 'Không đạt'}</span>
              </div>
            </div>
          </div>

          {/* Variant C: Structural Anchor + Dynamic Buffer */}
          <div className="rounded-lg border border-indigo-500/40 bg-indigo-950/20 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-xs text-indigo-400 uppercase tracking-wider">Biến Thể C</span>
              <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] text-indigo-200">
                {stopPlacement?.mode === 'PAPER_STRUCTURAL' ? 'Thực thi' : 'Đề xuất'}
              </span>
            </div>
            <div className="text-xs text-slate-400 mb-2">Cấu trúc + ATR/Spread</div>
            <div className="text-lg font-mono font-bold text-indigo-300">
              {varC?.stop_loss != null ? varC.stop_loss.toFixed(2) : (stopPlacement?.selected_sl?.toFixed(2) ?? '—')}
            </div>
            <div className="mt-3 space-y-1.5 text-[11px] text-slate-300">
              <div className="flex justify-between">
                <span className="text-slate-500">Khoảng SL:</span>
                <span className="font-mono">{varC?.price_distance != null ? varC.price_distance.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Khối lượng:</span>
                <span className="font-mono">{varC?.quantity != null ? varC.quantity.toFixed(3) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Net R:R:</span>
                <span className="font-mono text-emerald-400">{varC?.net_rr != null ? varC.net_rr.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Trạng thái:</span>
                <span className="font-medium text-slate-300">{varC?.is_valid ? 'Hợp lệ' : 'Không đạt'}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-4 rounded-lg bg-slate-950 p-3 text-xs text-slate-400 border border-slate-800">
          <div className="font-medium text-slate-300 mb-1">Quy tắc bảo toàn mục tiêu (Target Invariance):</div>
          Mục tiêu chốt lời (TP) được giữ cố định theo baseline ban đầu, không bị kéo xa hơn khi stop loss được nới rộng.
        </div>

        <div className="mt-5 flex justify-end">
          <button
            onClick={onClose}
            className="rounded-lg bg-slate-800 px-4 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700 transition"
          >
            Đóng
          </button>
        </div>
      </div>
    </div>
  );
};
