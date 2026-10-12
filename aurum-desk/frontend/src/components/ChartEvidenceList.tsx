import React, { useEffect, useRef } from 'react';
import type { ChartEvidenceBundleV144 } from '../types/smcScenario';
import { X, Crosshair, AlertTriangle, List } from 'lucide-react';

export interface ChartEvidenceListProps {
  isOpen: boolean;
  onClose: () => void;
  bundle: ChartEvidenceBundleV144 | null;
  onFocusEvidence?: (id: string) => void;
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
}

export interface FlattenedEvidenceItem {
  id: string;
  category: string;
  title: string;
  price?: number | null;
  timeMs?: number | null;
  hasAnchor: boolean;
  detail?: string;
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

  // Focus trap / escape key
  useEffect(() => {
    if (!isOpen) return;
    closeBtnRef.current?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        triggerRef?.current?.focus();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) return null;

  // Flatten evidence from bundle
  const items: FlattenedEvidenceItem[] = [];

  if (bundle) {
    // 1. Structure Events (Sweep, Break, Swing)
    for (const ev of bundle.structure_events || []) {
      items.push({
        id: ev.id || `ev-${items.length}`,
        category: ev.kind || 'Cấu trúc',
        title: ev.label || ev.kind || 'Sự kiện cấu trúc',
        price: ev.level ?? ev.price,
        timeMs: ev.occurredAtMs ?? ev.timeMs ?? ev.time_ms,
        hasAnchor: Boolean(ev.occurredAtMs || ev.timeMs || ev.time_ms),
        detail: ev.detail || (ev.level ? `Mức: ${ev.level}` : undefined),
      });
    }

    // 2. POI / FVG Zones
    for (const z of bundle.poi_zones || []) {
      items.push({
        id: z.id || `zone-${items.length}`,
        category: z.kind || 'FVG / POI',
        title: `${z.kind || 'Zone'} [${z.top?.toFixed(2)} - ${z.bottom?.toFixed(2)}]`,
        price: z.top && z.bottom ? (z.top + z.bottom) / 2 : z.top || z.bottom,
        timeMs: z.startAtMs || z.start_at_ms,
        hasAnchor: Boolean(z.startAtMs || z.start_at_ms),
        detail: z.state ? `Trạng thái: ${z.state}` : undefined,
      });
    }

    // 3. Liquidity Levels
    for (const l of bundle.liquidity_levels || []) {
      items.push({
        id: l.id || `liq-${items.length}`,
        category: 'Thanh khoản',
        title: l.label || `${l.side || 'Liquidity'} tại ${l.price}`,
        price: l.price,
        timeMs: l.start_at || l.timeMs,
        hasAnchor: Boolean(l.price),
        detail: l.state ? `Trạng thái: ${l.state}` : undefined,
      });
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Danh sách bằng chứng biểu đồ"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4"
    >
      <div
        ref={dialogRef}
        className="w-full max-w-lg bg-charcoal-900 border border-charcoal-750 rounded-xl shadow-2xl flex flex-col max-h-[85vh] overflow-hidden"
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-charcoal-750 flex items-center justify-between bg-charcoal-850">
          <div className="flex items-center gap-2">
            <List className="w-4 h-4 text-aurum-400" />
            <h3 className="font-bold text-gray-200 text-sm">Danh Sách Bằng Chứng Biểu Đồ (Evidence List)</h3>
            <span className="text-[10px] bg-charcoal-800 text-gray-400 px-1.5 py-0.5 rounded font-mono">
              {items.length} mục
            </span>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={() => {
              onClose();
              triggerRef?.current?.focus();
            }}
            className="p-1 rounded text-gray-400 hover:text-white hover:bg-charcoal-800 transition"
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
                key={item.id || idx}
                className="p-2.5 rounded-lg bg-charcoal-850 border border-charcoal-800 hover:border-aurum-500/50 hover:bg-charcoal-800/80 transition flex items-center justify-between gap-3 text-xs"
              >
                <div className="flex flex-col gap-0.5 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.2 rounded bg-charcoal-800 text-aurum-400 border border-charcoal-700">
                      {item.category}
                    </span>
                    <span className="font-semibold text-gray-200 truncate">{item.title}</span>
                  </div>
                  {item.detail && <span className="text-[11px] text-gray-400">{item.detail}</span>}
                  {!item.hasAnchor && (
                    <span className="text-[10px] text-amber-400 flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" /> Thiếu tọa độ neo thời gian thực
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {item.price && (
                    <span className="font-mono text-xs text-gray-300 font-bold">${item.price.toFixed(2)}</span>
                  )}
                  {onFocusEvidence && (
                    <button
                      type="button"
                      data-testid={`focus-evidence-${item.id}`}
                      onClick={() => onFocusEvidence(item.id)}
                      className="px-2 py-1 rounded bg-aurum-500/20 hover:bg-aurum-500 hover:text-charcoal-950 text-aurum-300 text-[10px] font-bold transition flex items-center gap-1 border border-aurum-500/30"
                      title="Focus vào bằng chứng trên chart"
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
