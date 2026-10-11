import React, { useState } from 'react';
import { useNotification } from '../context/NotificationContext';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Info,
  X,
  HelpCircle,
  Shield,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Clock,
  Code
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

export const UserMessageDetailsModal: React.FC = () => {
  const { selectedMessage, closeDetails, onActionClick } = useNotification();
  const [showTechnical, setShowTechnical] = useState(false);

  if (!selectedMessage) return null;

  const getSeverityBadge = () => {
    switch (selectedMessage.severity) {
      case 'success':
        return (
          <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-700 flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-emerald-400" />
            <span>Thành công</span>
          </span>
        );
      case 'warning':
        return (
          <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-950 text-amber-300 border border-amber-700 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3 text-amber-400" />
            <span>Cảnh báo bảo vệ</span>
          </span>
        );
      case 'error':
        return (
          <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-950 text-rose-300 border border-rose-700 flex items-center gap-1">
            <XCircle className="w-3 h-3 text-rose-400" />
            <span>Lỗi từ chối</span>
          </span>
        );
      case 'info':
      default:
        return (
          <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-charcoal-800 text-aurum-300 border border-aurum-700 flex items-center gap-1">
            <Info className="w-3 h-3 text-aurum-400" />
            <span>Thông tin</span>
          </span>
        );
    }
  };

  const getCertaintyBadge = () => {
    switch (selectedMessage.outcome_certainty) {
      case 'confirmed':
        return <span className="text-[10px] text-emerald-400 font-medium">✓ Đã xác nhận</span>;
      case 'pending':
        return <span className="text-[10px] text-amber-400 font-medium">⏳ Đang xử lý</span>;
      case 'unknown':
        return <span className="text-[10px] text-rose-400 font-medium">⚠️ Chưa rõ kết quả (Cần đối soát)</span>;
      case 'estimated':
        return <span className="text-[10px] text-blue-400 font-medium">≈ Kết quả ước tính</span>;
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-in fade-in duration-200">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        className="w-full max-w-lg bg-charcoal-900 border border-charcoal-700 rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]"
      >
        {/* Header */}
        <div className="p-4 bg-charcoal-850 border-b border-charcoal-750 flex items-start justify-between gap-3">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 flex-wrap">
              {getSeverityBadge()}
              {getCertaintyBadge()}
              <span className="text-[10px] font-mono text-gray-400 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {new Date(selectedMessage.occurred_at).toLocaleTimeString('vi-VN')}
              </span>
            </div>
            <h2 id="modal-title" className="text-base font-bold text-white tracking-wide">
              {selectedMessage.title}
            </h2>
          </div>

          <button
            onClick={closeDetails}
            className="text-gray-400 hover:text-white p-1 rounded-lg hover:bg-charcoal-750 transition"
            title="Đóng cửa sổ"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Scrollable Body */}
        <div className="p-4 space-y-4 overflow-y-auto text-xs text-gray-300">
          {/* 1. Summary Card */}
          <div className="p-3 rounded-lg bg-charcoal-800/80 border border-charcoal-750">
            <span className="text-[11px] font-semibold text-gray-400 block mb-1">Tóm tắt sự kiện:</span>
            <p className="text-gray-200 text-xs leading-relaxed">{selectedMessage.summary}</p>
          </div>

          {/* 2. What Happened (Explanation) */}
          {selectedMessage.explanation && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-aurum-400 font-semibold text-[11px]">
                <HelpCircle className="w-3.5 h-3.5" />
                <span>Điều gì vừa xảy ra & Vì sao có quy tắc này?</span>
              </div>
              <p className="text-gray-300 leading-relaxed pl-5 border-l-2 border-aurum-500/40">
                {selectedMessage.explanation}
              </p>
            </div>
          )}

          {/* 3. Account / Order Impact */}
          {selectedMessage.impact && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-emerald-400 font-semibold text-[11px]">
                <Shield className="w-3.5 h-3.5" />
                <span>Tác động đến tài khoản & lệnh của bạn:</span>
              </div>
              <p className="text-gray-300 leading-relaxed pl-5 border-l-2 border-emerald-500/40">
                {selectedMessage.impact}
              </p>
            </div>
          )}

          {/* 4. Next Steps */}
          {selectedMessage.next_steps && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-blue-400 font-semibold text-[11px]">
                <ArrowRight className="w-3.5 h-3.5" />
                <span>Bạn cần làm gì tiếp theo?</span>
              </div>
              <p className="text-gray-200 font-medium leading-relaxed pl-5 border-l-2 border-blue-500/40">
                {selectedMessage.next_steps}
              </p>
            </div>
          )}

          {/* 5. Collapsible Technical Details */}
          {selectedMessage.technical_details && (
            <div className="pt-2 border-t border-charcoal-750">
              <button
                type="button"
                onClick={() => setShowTechnical(!showTechnical)}
                className="w-full py-1.5 flex items-center justify-between text-[11px] text-gray-400 hover:text-gray-200 transition"
              >
                <span className="flex items-center gap-1.5 font-mono">
                  <Code className="w-3 h-3 text-aurum-400" />
                  <span>Thông tin kỹ thuật (Diagnostic Code)</span>
                </span>
                {showTechnical ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </button>

              {showTechnical && (
                <div className="mt-2 p-2.5 rounded bg-charcoal-950 border border-charcoal-800 font-mono text-[10px] space-y-1 text-gray-400 break-all">
                  <div>
                    <span className="text-gray-500">Mã định danh (Code):</span>{' '}
                    <span className="text-aurum-400 font-bold">{selectedMessage.technical_details.code || selectedMessage.code}</span>
                  </div>
                  {selectedMessage.technical_details.http_status && (
                    <div>
                      <span className="text-gray-500">Mã HTTP Status:</span>{' '}
                      <span className="text-blue-300">{selectedMessage.technical_details.http_status}</span>
                    </div>
                  )}
                  {selectedMessage.technical_details.operation && (
                    <div>
                      <span className="text-gray-500">Thao tác (Operation):</span>{' '}
                      <span className="text-gray-300">{selectedMessage.technical_details.operation}</span>
                    </div>
                  )}
                  {selectedMessage.technical_details.raw_error && (
                    <div className="mt-1.5 pt-1.5 border-t border-charcoal-800">
                      <span className="text-gray-500 block mb-0.5">Nội dung phản hồi (Sanitized):</span>
                      <pre className="text-gray-400 whitespace-pre-wrap max-h-28 overflow-y-auto">
                        {selectedMessage.technical_details.raw_error}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="p-3 bg-charcoal-850 border-t border-charcoal-750 flex items-center justify-end gap-2">
          {selectedMessage.action && onActionClick && (
            <button
              onClick={() => {
                onActionClick(selectedMessage.action!);
                closeDetails();
              }}
              className="px-3 py-1.5 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded-lg text-xs transition flex items-center gap-1.5 shadow-sm"
            >
              <span>{getActionLabel(selectedMessage.action)}</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </button>
          )}

          <button
            onClick={closeDetails}
            className="px-3 py-1.5 bg-charcoal-750 hover:bg-charcoal-700 text-gray-200 font-medium rounded-lg text-xs transition"
          >
            Đóng
          </button>
        </div>
      </div>
    </div>
  );
};
