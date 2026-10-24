import React, { useState } from 'react';
import { useStopPlacementSettings } from '../hooks/useStopPlacementSettings';

export const StopPlacementSettings: React.FC = () => {
  const {
    settings,
    draftMode,
    setDraftMode,
    draftAtrPeriod,
    setDraftAtrPeriod,
    draftMinBufferTicks,
    setDraftMinBufferTicks,
    draftAtrM1Multiplier,
    setDraftAtrM1Multiplier,
    draftAtrM5Multiplier,
    setDraftAtrM5Multiplier,
    draftSpreadMultiplier,
    setDraftSpreadMultiplier,
    isDirty,
    isLoading,
    isSaving,
    isStaleConflict,
    loadError,
    saveStatus,
    refresh,
    saveSettings,
    resetDrafts,
  } = useStopPlacementSettings();

  const [showAdvanced, setShowAdvanced] = useState(false);

  if (isLoading && !settings) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400">
        Đang tải cấu hình Stop Placement...
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 p-4 text-xs text-rose-300">
        <div className="font-semibold mb-1">Lỗi tải cấu hình Stop Placement</div>
        <div>{loadError}</div>
        <button
          onClick={refresh}
          className="mt-2 rounded bg-slate-800 px-2.5 py-1 text-[11px] text-slate-200 hover:bg-slate-700"
        >
          Thử lại
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            Điểm Dừng Lỗ Cấu Trúc (Stop Placement)
            <span className="rounded bg-indigo-500/10 px-2 py-0.5 text-[10px] font-mono text-indigo-300 border border-indigo-500/20">
              V17.5
            </span>
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Xác định điểm mất hiệu lực theo cấu trúc của từng model và buffer biến động có nguồn gốc
          </p>
        </div>
        <div className="text-right">
          <span className="text-[11px] text-slate-500">Phiên bản: </span>
          <span className="font-mono text-xs text-slate-300">v{settings?.version ?? 1}</span>
        </div>
      </div>

      <div className="space-y-4 text-xs">
        {/* Mode Selector */}
        <div>
          <label className="block font-medium text-slate-300 mb-1.5">
            Chế độ đặt Stop Loss (Mode)
          </label>
          <select
            value={draftMode}
            onChange={(e) => setDraftMode(e.target.value)}
            disabled={isSaving}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
          >
            <option value="SHADOW_COMPARE">
              SHADOW_COMPARE (Mặc định: Thực thi A legacy, ghi nhận song song B & C để quan sát)
            </option>
            <option value="PAPER_STRUCTURAL">
              PAPER_STRUCTURAL (Thực thi C cấu trúc + buffer biến động cho lệnh PAPER mới)
            </option>
            <option value="LEGACY">
              LEGACY (Chỉ thực thi SL cố định theo FVG edge / tick buffer truyền thống)
            </option>
          </select>
          <p className="mt-1.5 text-[11px] text-slate-400">
            {draftMode === 'PAPER_STRUCTURAL' && (
              <span className="text-indigo-300 font-medium">
                ★ Áp dụng Biến Thể C cho các lệnh PAPER mới. Lệnh đang mở hoặc đang chờ giữ nguyên SL đã chốt.
              </span>
            )}
            {draftMode === 'SHADOW_COMPARE' && (
              <span>
                Thực thi A gốc trong production, lưu trữ đề xuất B & C vào hệ thống quan sát paired research.
              </span>
            )}
            {draftMode === 'LEGACY' && (
              <span>
                Bảo lưu hoàn toàn hình học SL cũ không tính toán thêm biến thể mới.
              </span>
            )}
          </p>
        </div>

        {/* Toggle Advanced */}
        <div className="pt-1">
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="text-[11px] text-slate-400 hover:text-slate-200 underline decoration-slate-600"
          >
            {showAdvanced ? 'Ẩn thông số biến động nâng cao ▲' : 'Tùy chỉnh thông số biến động nâng cao ▼'}
          </button>
        </div>

        {/* Advanced Accordion */}
        {showAdvanced && (
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3.5 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Chu kỳ ATR (M1 & M5):</label>
                <input
                  type="number"
                  min={2}
                  max={100}
                  value={draftAtrPeriod}
                  onChange={(e) => setDraftAtrPeriod(Math.max(2, parseInt(e.target.value) || 14))}
                  disabled={isSaving}
                  className="w-full rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-200 font-mono"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Buffer sàn tối thiểu (ticks):</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={draftMinBufferTicks}
                  onChange={(e) => setDraftMinBufferTicks(Math.max(1, parseInt(e.target.value) || 5))}
                  disabled={isSaving}
                  className="w-full rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-200 font-mono"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Hệ số ATR M1:</label>
                <input
                  type="number"
                  step="0.05"
                  min={0}
                  max={5}
                  value={draftAtrM1Multiplier}
                  onChange={(e) => setDraftAtrM1Multiplier(parseFloat(e.target.value) || 0.25)}
                  disabled={isSaving}
                  className="w-full rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-200 font-mono"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Hệ số ATR M5:</label>
                <input
                  type="number"
                  step="0.05"
                  min={0}
                  max={5}
                  value={draftAtrM5Multiplier}
                  onChange={(e) => setDraftAtrM5Multiplier(parseFloat(e.target.value) || 0.10)}
                  disabled={isSaving}
                  className="w-full rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-200 font-mono"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Hệ số Spread:</label>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={10}
                  value={draftSpreadMultiplier}
                  onChange={(e) => setDraftSpreadMultiplier(parseFloat(e.target.value) || 1.50)}
                  disabled={isSaving}
                  className="w-full rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-200 font-mono"
                />
              </div>
            </div>
            <p className="text-[10px] text-slate-500 italic">
              Công thức buffer: max(tick_floor, k_m1*ATR_M1, k_m5*ATR_M5, k_spread*spread).
            </p>
          </div>
        )}

        {/* Status Message */}
        {saveStatus.type !== 'idle' && (
          <div
            className={`rounded-lg p-2.5 text-xs ${
              saveStatus.type === 'success'
                ? 'bg-emerald-950/30 text-emerald-300 border border-emerald-800/40'
                : saveStatus.type === 'error'
                ? 'bg-rose-950/30 text-rose-300 border border-rose-800/40'
                : 'bg-slate-800/50 text-slate-300'
            }`}
          >
            {saveStatus.message}
          </div>
        )}

        {isStaleConflict && (
          <button
            onClick={refresh}
            className="w-full rounded-lg bg-amber-600/20 text-amber-300 border border-amber-500/30 py-1.5 text-xs font-medium hover:bg-amber-600/30 transition"
          >
            Tải lại phiên bản cấu hình mới nhất từ máy chủ
          </button>
        )}

        {/* Save button */}
        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
          {isDirty && (
            <button
              type="button"
              onClick={resetDrafts}
              disabled={isSaving}
              className="rounded-lg px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800"
            >
              Hủy thay đổi
            </button>
          )}
          <button
            type="button"
            onClick={saveSettings}
            disabled={!isDirty || isSaving}
            className={`rounded-lg px-4 py-1.5 text-xs font-medium transition ${
              isDirty && !isSaving
                ? 'bg-indigo-600 text-white hover:bg-indigo-500 shadow-sm'
                : 'bg-slate-800 text-slate-500 cursor-not-allowed'
            }`}
          >
            {isSaving ? 'Đang lưu...' : 'Lưu cấu hình SL'}
          </button>
        </div>
      </div>
    </div>
  );
};
