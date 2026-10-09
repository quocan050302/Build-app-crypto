import React from 'react';
import { useNotification } from '../context/NotificationContext';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Info,
  X,
  ChevronRight,
  ExternalLink
} from 'lucide-react';

const getActionLabel = (action: any): string => {
  if (typeof action === 'object' && action?.label) return action.label;
  switch (action) {
    case 'OPEN_POSITION':
    case 'VIEW_TRADE':
      return 'Xem vị thế';
    case 'VIEW_PENDING_ORDER':
      return 'Xem lệnh chờ';
    case 'REFRESH_SETUP':
      return 'Làm mới';
    case 'OPEN_NEWS':
      return 'Xem lịch tin';
    case 'OPEN_SETTINGS':
      return 'Cài đặt';
    case 'OPEN_TELEGRAM_HISTORY':
      return 'Lịch sử Telegram';
    case 'RECONCILE_STATUS':
      return 'Đồng bộ lại';
    default:
      return 'Xem chi tiết';
  }
};

export const NotificationToastContainer: React.FC = () => {
  const { activeToasts, dismissToast, openDetails, onActionClick } = useNotification();

  if (activeToasts.length === 0) return null;

  const getSeverityStyle = (severity: string) => {
    switch (severity) {
      case 'success':
        return {
          bg: 'bg-emerald-950/95 border-emerald-600/80 text-emerald-200',
          icon: <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />,
          badge: 'bg-emerald-900/60 text-emerald-300 border-emerald-700/60',
        };
      case 'warning':
        return {
          bg: 'bg-amber-950/95 border-amber-600/80 text-amber-200',
          icon: <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />,
          badge: 'bg-amber-900/60 text-amber-300 border-amber-700/60',
        };
      case 'error':
        return {
          bg: 'bg-rose-950/95 border-rose-600/80 text-rose-200',
          icon: <XCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />,
          badge: 'bg-rose-900/60 text-rose-300 border-rose-700/60',
        };
      case 'info':
      default:
        return {
          bg: 'bg-charcoal-900/95 border-aurum-500/80 text-aurum-200',
          icon: <Info className="w-4 h-4 text-aurum-400 shrink-0 mt-0.5" />,
          badge: 'bg-charcoal-800 text-aurum-300 border-aurum-600/50',
        };
    }
  };

  return (
    <aside
      aria-label="Thông báo hệ thống"
      aria-live="polite"
      className="fixed top-4 right-4 z-50 flex flex-col gap-2.5 max-w-sm w-full pointer-events-none"
    >
      {activeToasts.map((toast) => {
        const style = getSeverityStyle(toast.severity);
        const hasDetails = Boolean(toast.explanation || toast.next_steps || toast.impact || toast.technical_details);

        return (
          <div
            key={toast.id}
            role="status"
            className={`pointer-events-auto p-3 rounded-lg border shadow-2xl backdrop-blur-md transition-all duration-300 transform translate-y-0 ${style.bg}`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-start gap-2.5 min-w-0">
                {style.icon}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-bold text-xs tracking-wide">{toast.title}</span>
                    {toast.outcome_certainty === 'unknown' && (
                      <span className="text-[9px] px-1.5 py-0.2 rounded border bg-amber-900/40 text-amber-300 border-amber-700">
                        Chưa rõ kết quả
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-300 mt-0.5 leading-relaxed line-clamp-3">
                    {toast.summary}
                  </p>
                </div>
              </div>

              {/* Close Button */}
              <button
                onClick={() => dismissToast(toast.id)}
                className="text-gray-400 hover:text-white p-1 rounded transition hover:bg-white/10 shrink-0"
                title="Đóng thông báo"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Actions / View Details footer */}
            <div className="mt-2.5 pt-2 border-t border-white/10 flex items-center justify-between gap-2 text-[10px]">
              {hasDetails ? (
                <button
                  onClick={() => openDetails(toast)}
                  className="flex items-center gap-1 text-aurum-300 hover:text-aurum-200 font-medium transition underline-offset-2 hover:underline"
                >
                  <span>Xem chi tiết & hướng dẫn</span>
                  <ChevronRight className="w-3 h-3" />
                </button>
              ) : (
                <span className="text-gray-400 text-[10px]">
                  {new Date(toast.occurred_at).toLocaleTimeString('vi-VN')}
                </span>
              )}

              {toast.action && onActionClick && (
                <button
                  onClick={() => onActionClick(toast.action!)}
                  className="px-2 py-0.5 bg-white/10 hover:bg-white/20 rounded font-medium text-white transition flex items-center gap-1"
                >
                  <span>{getActionLabel(toast.action)}</span>
                  <ExternalLink className="w-2.5 h-2.5" />
                </button>
              )}
            </div>
          </div>
        );
      })}
    </aside>
  );
};
