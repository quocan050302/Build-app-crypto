/**
 * frontend/src/components/RrPolicySettingsCard.tsx
 * Aurum V16.39.16 — Min Net R:R Settings Card
 *
 * Location: In tab 'Quản Trị Vốn & Ký Quỹ' (activeTab === 'paper').
 *
 * Requirements:
 * - Controlled component accepting state and callbacks from owner hook.
 * - Presets 1.50, 2.00, 2.50, 3.00 update draft only.
 * - Custom input with min 1.50, step 0.01.
 * - Independent Save button and Undo button (does not submit sibling account form).
 * - Clear Vietnamese explanations: after-fee threshold, no auto-resizing SL/TP,
 *   local backend DB scope, immutable snapshots for historical orders.
 */

import React from 'react';
import { Target, Check, AlertTriangle, RefreshCw, RotateCcw, Info } from 'lucide-react';
import type { RrPolicySaved, RrPolicyStatus } from '../hooks/useRrPolicySettings';

export interface RrPolicySettingsCardProps {
  id?: string;
  saved: RrPolicySaved;
  draftText: string;
  isDirty: boolean;
  status: RrPolicyStatus;
  error: string | null;
  successMessage: string | null;
  onDraftChange: (text: string) => void;
  onSelectPreset: (val: number) => void;
  onReset: () => void;
  onSave: () => void;
  onRetry?: () => void;
}

const PRESETS = [1.5, 2.0, 2.5, 3.0];

export const RrPolicySettingsCard: React.FC<RrPolicySettingsCardProps> = ({
  id = 'rr-policy-settings-card',
  saved,
  draftText,
  isDirty,
  status,
  error,
  successMessage,
  onDraftChange,
  onSelectPreset,
  onReset,
  onSave,
  onRetry,
}) => {
  const parsedVal = parseFloat(draftText);
  const isValid = Number.isFinite(parsedVal) && parsedVal >= 1.5;
  const isSaving = status === 'saving';
  const isLoading = status === 'loading';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isValid && isDirty && !isSaving) {
      onSave();
    }
  };

  return (
    <div
      id={id}
      data-testid="rr-policy-settings-card"
      className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-700 flex flex-col gap-4 text-xs scroll-mt-4"
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-charcoal-750 pb-2.5">
        <div>
          <h3 className="font-bold text-gray-200 uppercase tracking-wider flex items-center gap-1.5 text-sm">
            <Target className="w-4 h-4 text-aurum-400" />
            Cấu Hình Ngưỡng Lợi Nhuận / Rủi Ro Tối Thiểu (Min Net R:R)
          </h3>
          <p className="text-[11px] text-gray-400 mt-0.5">
            Ngưỡng tối thiểu sau phí sàn và trượt giá cho các đánh giá setup và lệnh ARM mới toàn app PAPER.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-400">Đang áp dụng:</span>
          {isLoading || saved.value === null ? (
            <span className="px-2 py-0.5 rounded bg-charcoal-800 border border-charcoal-700 text-gray-400 font-mono animate-pulse">
              Đang tải...
            </span>
          ) : (
            <span className="px-2.5 py-0.5 rounded bg-charcoal-800 border border-aurum-500/40 text-aurum-300 font-mono font-bold text-xs">
              1:{saved.value.toFixed(2)} ({saved.source})
            </span>
          )}
        </div>
      </div>

      {/* Unsaved draft alert */}
      {isDirty && (
        <div className="p-2.5 rounded bg-amber-950/40 border border-amber-500/50 text-amber-200 text-xs flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Info className="w-4 h-4 text-amber-400 shrink-0" />
            <span>
              <strong>Bản Nháp Chưa Lưu:</strong> Ngưỡng dự kiến <strong>1:{parsedVal.toFixed(2)}</strong>. Giá trị này <em>chưa được lưu</em> vào backend và chart/ARM vẫn đang dùng <strong>{saved.value != null ? `1:${saved.value.toFixed(2)}` : 'chưa xác nhận'}</strong>.
            </span>
          </div>
          <button
            type="button"
            onClick={onReset}
            disabled={isSaving}
            className="px-2.5 py-1 bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 border border-charcoal-700 rounded text-[11px] font-semibold flex items-center gap-1 shrink-0 transition"
          >
            <RotateCcw className="w-3 h-3 text-amber-400" />
            Hoàn Tác
          </button>
        </div>
      )}

      {/* Main input form */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Preset Buttons */}
          <div>
            <label className="text-gray-300 font-medium block mb-1.5">
              Chọn nhanh mức tiêu chuẩn (Presets):
            </label>
            <div className="grid grid-cols-4 gap-2">
              {PRESETS.map((preset) => {
                const isSelected = parsedVal === preset;
                return (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => onSelectPreset(preset)}
                    disabled={isSaving || isLoading}
                    className={`py-2 rounded font-mono font-bold text-xs border transition ${
                      isSelected
                        ? 'bg-aurum-500 text-charcoal-950 border-aurum-400 shadow-sm'
                        : 'bg-charcoal-900 text-gray-300 hover:bg-charcoal-800 hover:text-white border-charcoal-700'
                    }`}
                  >
                    1:{preset.toFixed(1)}
                  </button>
                );
              })}
            </div>
            <p className="text-[10px] text-gray-500 mt-1">
              Bấm preset để điền giá trị mẫu vào bản nháp. Cần bấm <strong>Lưu R:R</strong> để áp dụng.
            </p>
          </div>

          {/* Custom Input */}
          <div>
            <label htmlFor="min-net-rr-input" className="text-gray-300 font-medium block mb-1.5">
              Nhập giá trị tùy chỉnh (Sàn tối thiểu 1.50):
            </label>
            <div className="flex items-center gap-2">
              <span className="text-gray-400 font-mono font-bold text-sm bg-charcoal-900 px-3 py-2 rounded border border-charcoal-700">
                1 :
              </span>
              <input
                id="min-net-rr-input"
                type="number"
                step="0.01"
                min="1.50"
                max="10.00"
                value={draftText}
                onChange={(e) => onDraftChange(e.target.value)}
                disabled={isSaving || isLoading}
                placeholder="1.50"
                className={`flex-1 bg-charcoal-900 border rounded px-3 py-2 font-mono text-sm text-gray-100 placeholder-gray-600 focus:outline-none transition ${
                  !isValid && draftText.trim()
                    ? 'border-rose-500 focus:border-rose-400 text-rose-300'
                    : 'border-charcoal-700 focus:border-aurum-400'
                }`}
              />
            </div>
            {!isValid && draftText.trim() && (
              <p className="text-[11px] text-rose-400 mt-1">
                Ngưỡng R:R tối thiểu phải là số từ 1.50 trở lên.
              </p>
            )}
          </div>
        </div>

        {/* Informative Guidance */}
        <div className="p-3 bg-charcoal-900/70 rounded border border-charcoal-750 text-[11px] text-gray-300 space-y-1.5 leading-relaxed">
          <div className="font-semibold text-aurum-300 flex items-center gap-1.5">
            <Info className="w-3.5 h-3.5" />
            Lưu ý kỹ thuật quan trọng về cấu hình:
          </div>
          <ul className="list-disc list-inside space-y-1 text-gray-400">
            <li>
              <strong className="text-gray-200">Ngưỡng tối thiểu, không phải mục tiêu:</strong> Chọn 1.50 có nghĩa là setup có Net R:R từ 1.50 trở lên (như 1.80R, 2.50R) đều hợp lệ. Hệ thống không tự ý kéo TP/SL để ép về đúng 1.5R.
            </li>
            <li>
              <strong className="text-gray-200">Bảo toàn lệnh đã ARM / đang mở:</strong> Lệnh đã kích hoạt giữ nguyên snapshot ngưỡng tại thời điểm ARM và không bị cập nhật theo cấu hình mới này.
            </li>
            <li>
              <strong className="text-gray-200">Lưu trữ backend máy cục bộ:</strong> Cấu hình được lưu vào database của backend đang kết nối, không tự động đồng bộ qua Git hay giữa các máy khác nhau.
            </li>
          </ul>
        </div>

        {/* Notifications */}
        {successMessage && (
          <div className="p-2.5 rounded bg-emerald-950/80 border border-emerald-700 text-emerald-300 text-xs flex items-center gap-2">
            <Check className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>{successMessage}</span>
          </div>
        )}

        {error && (
          <div className="p-2.5 rounded bg-rose-950/80 border border-rose-700 text-rose-300 text-xs flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
              <span>{error}</span>
            </div>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="px-2.5 py-1 bg-rose-900/60 hover:bg-rose-800 text-rose-200 rounded border border-rose-700 text-[11px] font-semibold transition"
              >
                Thử Lại
              </button>
            )}
          </div>
        )}

        {/* Form Actions */}
        <div className="flex justify-end items-center gap-3 pt-2 border-t border-charcoal-750">
          {isDirty && (
            <button
              type="button"
              onClick={onReset}
              disabled={isSaving}
              className="px-3.5 py-2 bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 font-semibold rounded text-xs transition border border-charcoal-700"
            >
              Hủy Bản Nháp
            </button>
          )}

          <button
            type="submit"
            disabled={isSaving || !isDirty || !isValid || isLoading}
            className={`flex items-center gap-1.5 px-4 py-2 rounded text-xs font-bold transition shadow-sm ${
              isDirty && isValid && !isSaving && !isLoading
                ? 'bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 shadow-aurum-500/20'
                : 'bg-charcoal-800 text-gray-500 border border-charcoal-700 cursor-not-allowed'
            }`}
          >
            {isSaving ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                Đang Lưu R:R...
              </>
            ) : isDirty ? (
              `Lưu R:R (1:${isValid ? parsedVal.toFixed(2) : draftText})`
            ) : saved.value != null ? (
              `R:R Đã Đồng Bộ (1:${saved.value.toFixed(2)})`
            ) : (
              'Đang Tải Cấu Hình R:R...'
            )}
          </button>
        </div>
      </form>
    </div>
  );
};
