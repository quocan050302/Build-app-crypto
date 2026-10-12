import React, { useEffect, useRef } from 'react';
import type { ChartEvidenceBundleV144 } from '../types/smcScenario';
import { X, Crosshair, AlertTriangle, List } from 'lucide-react';
import { flattenEvidenceBundle, type EvidenceAnchor } from '../utils/evidenceFocus';

export interface ChartEvidenceListProps {
  isOpen: boolean;
  onClose: () => void;
  bundle: ChartEvidenceBundleV144 | null;
  onFocusEvidence?: (id: string) => void;
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
}

export const ChartEvidenceList: React.FC<ChartEvidenceListProps> = ({
  isOpen,
  onClose,
  bundle,
  onFocusEvidence,
  triggerRef,
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  // Focus trap / keyboard management
  useEffect(() => {
    if (!isOpen) return;
    closeBtnRef.current?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        triggerRef?.current?.focus();
        return;
      }

      if (e.key === 'Tab' && dialogRef.current) {
        const focusable = Array.from(
          dialogRef.current.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [tabindex]:not([tabindex="-1"])'
          )
        );
        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) return null;

  const items: EvidenceAnchor[] = flattenEvidenceBundle(bundle);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Danh sách bằng chứng biểu đồ"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
          triggerRef?.current?.focus();
        }
      }}
    >
      <div
        ref={dialogRef}
        className="w-full max-w-lg bg-charcoal-900 border border-charcoal-700 rounded-xl shadow-2xl flex flex-col max-h-[80vh] overflow-hidden"
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-charcoal-800 bg-charcoal-850 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <List className="w-4 h-4 text-aurum-400" />
            <h2 className="font-bold text-gray-100 text-sm">Danh Sách Bằng Chứng Của Lệnh / Setup</h2>
            <span className="text-[11px] font-mono text-gray-400 bg-charcoal-800 px-1.5 py-0.5 rounded border border-charcoal-750">
              {items.length} mục
            </span>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            data-testid="evidence-list-close-btn"
            onClick={() => {
              onClose();
              triggerRef?.current?.focus();
            }}
            className="p-1 rounded-lg hover:bg-charcoal-800 text-gray-400 hover:text-gray-200 transition"
            aria-label="Đóng danh sách"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* List Content */}
        <div className="p-3 overflow-y-auto flex-1 flex flex-col gap-2">
          {items.length === 0 ? (
            <p className="text-xs text-gray-500 italic text-center py-6">
              Không có bằng chứng nào trong gói được chọn hoặc kịch bản trống.
            </p>
          ) : (
            items.map((item, idx) => (
              <div
                key={`${item.id}-${idx}`}
                className="p-2.5 rounded-lg bg-charcoal-850 border border-charcoal-800 hover:border-aurum-500/50 hover:bg-charcoal-800/80 transition flex items-center justify-between gap-3 text-xs"
              >
                <div className="flex flex-col gap-0.5 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-charcoal-800 text-aurum-400 border border-charcoal-700">
                      {item.kind}
                    </span>
                    <span className="font-semibold text-gray-200 truncate">{item.label || item.id}</span>
                  </div>
                  {!item.hasCoordinates && (
                    <span className="text-[10px] text-amber-400 flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3 shrink-0" />
                      {item.missingReason || 'Thiếu tọa độ neo thời gian thực'}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {item.price !== null && (
                    <span className="font-mono text-xs text-gray-300 font-bold">${item.price.toFixed(2)}</span>
                  )}
                  {onFocusEvidence && (
                    <button
                      type="button"
                      data-testid={`focus-evidence-${item.id}`}
                      disabled={!item.hasCoordinates}
                      onClick={() => onFocusEvidence(item.id)}
                      className={`px-2 py-1 rounded text-[10px] font-bold transition flex items-center gap-1 border ${
                        item.hasCoordinates
                          ? 'bg-aurum-500/20 hover:bg-aurum-500 hover:text-charcoal-950 text-aurum-300 border-aurum-500/30'
                          : 'bg-charcoal-800 text-gray-500 border-charcoal-700 cursor-not-allowed opacity-50'
                      }`}
                      title={item.hasCoordinates ? 'Focus vào bằng chứng trên chart' : item.missingReason || 'Thiếu tọa độ gốc nên chưa thể đưa lên biểu đồ'}
                    >
                      <Crosshair className="w-3 h-3" />
                      <span>Xem</span>
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-charcoal-800 bg-charcoal-850 flex items-center justify-between text-[11px] text-gray-500">
          <span>Nhấn ESC để đóng · Enter hoặc nút Xem để focus</span>
          <button
            type="button"
            onClick={() => {
              onClose();
              triggerRef?.current?.focus();
            }}
            className="px-3 py-1 rounded bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 font-semibold"
          >
            Đóng
          </button>
        </div>
      </div>
    </div>
  );
};
