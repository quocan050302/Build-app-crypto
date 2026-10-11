import React, { useState } from 'react';
import { useNotification } from '../context/NotificationContext';
import {
  Bell,
  X,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Info,
  Trash2,
  CheckCheck,
  ChevronRight
} from 'lucide-react';

export const NotificationCenterDrawer: React.FC = () => {
  const { isCenterOpen, closeCenter, history, clearHistory, markAllAsRead, openDetails } = useNotification();
  const [filter, setFilter] = useState<'all' | 'errors' | 'trades'>('all');

  if (!isCenterOpen) return null;

  const filteredHistory = history.filter((item) => {
    if (filter === 'errors') {
      return item.severity === 'error' || item.severity === 'warning';
    }
    if (filter === 'trades') {
      return item.code.startsWith('TRADE_') || item.code.startsWith('ORDER_') || item.code.startsWith('SETUP_');
    }
    return true;
  });

  const getIcon = (severity: string) => {
    switch (severity) {
      case 'success':
        return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />;
      case 'warning':
        return <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />;
      case 'error':
        return <XCircle className="w-3.5 h-3.5 text-rose-400 shrink-0 mt-0.5" />;
      case 'info':
      default:
        return <Info className="w-3.5 h-3.5 text-aurum-400 shrink-0 mt-0.5" />;
    }
  };

  return (
    <div className="fixed inset-0 z-50 overflow-hidden bg-black/60 backdrop-blur-xs flex justify-end">
      <div className="w-full max-w-sm bg-charcoal-900 border-l border-charcoal-750 h-full flex flex-col shadow-2xl animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="p-3.5 bg-charcoal-850 border-b border-charcoal-750 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bell className="w-4 h-4 text-aurum-400" />
            <h3 className="font-bold text-sm text-white">Trung Tâm Thông Báo</h3>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-charcoal-750 text-gray-300">
              {history.length}
            </span>
          </div>

          <button
            onClick={closeCenter}
            className="text-gray-400 hover:text-white p-1 rounded transition hover:bg-charcoal-750"
            title="Đóng bảng thông báo"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Filter Bar & Quick Actions */}
        <div className="p-2 bg-charcoal-900 border-b border-charcoal-800 flex items-center justify-between text-xs gap-1">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setFilter('all')}
              className={`px-2 py-1 rounded text-[11px] font-medium transition ${
                filter === 'all' ? 'bg-aurum-500 text-charcoal-950 font-bold' : 'text-gray-400 hover:text-white'
              }`}
            >
              Tất cả
            </button>
            <button
              onClick={() => setFilter('trades')}
              className={`px-2 py-1 rounded text-[11px] font-medium transition ${
                filter === 'trades' ? 'bg-aurum-500 text-charcoal-950 font-bold' : 'text-gray-400 hover:text-white'
              }`}
            >
              Lệnh
            </button>
            <button
              onClick={() => setFilter('errors')}
              className={`px-2 py-1 rounded text-[11px] font-medium transition ${
                filter === 'errors' ? 'bg-aurum-500 text-charcoal-950 font-bold' : 'text-gray-400 hover:text-white'
              }`}
            >
              Cảnh báo & Lỗi
            </button>
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={markAllAsRead}
              className="p-1 text-gray-400 hover:text-emerald-400 rounded transition"
              title="Đánh dấu tất cả đã đọc"
            >
              <CheckCheck className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={clearHistory}
              className="p-1 text-gray-400 hover:text-rose-400 rounded transition"
              title="Xóa toàn bộ lịch sử"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* List of Messages */}
        <div className="flex-1 overflow-y-auto divide-y divide-charcoal-800/60 p-1">
          {filteredHistory.length === 0 ? (
            <div className="py-12 text-center text-gray-500 text-xs space-y-1">
              <Bell className="w-6 h-6 mx-auto text-gray-600 mb-2 opacity-50" />
              <p>Chưa có thông báo nào trong phiên này.</p>
              <p className="text-[10px] text-gray-600">Các sự kiện và cảnh báo giao dịch sẽ hiển thị tại đây.</p>
            </div>
          ) : (
            filteredHistory.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => openDetails(item)}
                className={`w-full p-2.5 text-left rounded-lg transition flex items-start gap-2.5 group hover:bg-charcoal-800/80 ${
                  item.read ? 'opacity-75' : 'bg-charcoal-850/40'
                }`}
              >
                {getIcon(item.severity)}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-semibold text-xs text-gray-200 group-hover:text-aurum-300 transition truncate">
                      {item.title}
                    </span>
                    <span className="text-[9px] text-gray-500 font-mono shrink-0">
                      {new Date(item.occurred_at).toLocaleTimeString('vi-VN')}
                    </span>
                  </div>
                  <p className="text-[11px] text-gray-400 line-clamp-2 mt-0.5 leading-snug">
                    {item.summary}
                  </p>
                </div>
                <ChevronRight className="w-3.5 h-3.5 text-gray-600 group-hover:text-gray-300 shrink-0 mt-1 transition" />
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
