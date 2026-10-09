import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Smartphone,
  Send,
  Save,
  RefreshCw,
  Clock,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Eye,
  EyeOff,
  Trash2,
  Bell,
  BellOff,
  HelpCircle,
} from 'lucide-react';
import {
  api,
  extractErrorMessage,
  type TelegramConfig,
  type NotificationHistoryItem,
} from './api/client';
import { wsClient } from './services/wsClient';

interface TelegramTabProps {
  showToast?: (title: string, message: string, type?: 'info' | 'success' | 'warn') => void;
}

const EVENT_OPTIONS = [
  { id: 'NEAR_ENTRY', label: 'Sắp Tiếp Cận Entry', desc: 'Cảnh báo khi giá tiệm cận vùng Entry của setup' },
  { id: 'READY', label: 'Tín Hiệu READY', desc: 'Setup đủ điều kiện SMC (Liquidity Sweep, MSS, POI/FVG)' },
  { id: 'ARMED', label: 'Đã Arm Lệnh Chờ', desc: 'Lệnh đã được kích hoạt chờ khớp' },
  { id: 'FILLED', label: 'Đã Khớp Lệnh (FILLED)', desc: 'Vị thế chính thức được mở' },
  { id: 'TP_HIT', label: 'Chốt Lời (TP)', desc: 'Vị thế đạt lợi nhuận mục tiêu' },
  { id: 'SL_HIT', label: 'Cắt Lỗ (SL)', desc: 'Vị thế chạm mức dừng lỗ' },
  { id: 'MANUAL_CLOSED', label: 'Đóng Thủ Công', desc: 'Người dùng chủ động đóng vị thế (có lãi hoặc cắt lỗ)' },
  { id: 'LIQUIDATED', label: 'Thanh Lý Vị thế', desc: 'Cảnh báo chạm giá thanh lý Isolated' },
  { id: 'REJECTED', label: 'Lệnh Bị Từ Chối', desc: 'Vi phạm Execution Guards / Risk Checks' },
  { id: 'CANCELLED', label: 'Hủy Setup / Lệnh', desc: 'Người dùng chủ động hủy setup hoặc lệnh chờ' },
  { id: 'EXPIRED', label: 'Hết Hạn Thời Gian', desc: 'Setup hoặc lệnh chờ vượt quá thời hạn hiệu lực' },
  { id: 'INVALIDATED', label: 'Hủy Cấu Trúc', desc: 'Cấu trúc setup bị phá vỡ' },
  { id: 'FEED_DOWN', label: 'Cảnh Báo Nguồn Nến', desc: 'Mất kết nối hoặc nến trễ quá ngưỡng quy định' },
  { id: 'RECOVERED', label: 'Nguồn Nến Phục Hồi', desc: 'Nguồn cấp dữ liệu đã kết nối và ổn định trở lại' },
];

export const TelegramTab: React.FC<TelegramTabProps> = ({ showToast }) => {
  // Authoritative saved config from BE
  const [savedConfig, setSavedConfig] = useState<TelegramConfig | null>(null);
  // Working draft config in FE
  const [draftConfig, setDraftConfig] = useState<TelegramConfig>({
    enabled: false,
    bot_token: '',
    has_token: false,
    token_configured: false,
    chat_id: '',
    subscribed_events: ['ARMED', 'FILLED', 'TP_HIT', 'SL_HIT', 'MANUAL_CLOSED', 'LIQUIDATED'],
    near_entry_mode: 'ATR',
    near_entry_price_dist: 2.0,
    near_entry_atr_mult: 0.5,
    near_entry_cooldown_min: 30,
    quiet_hours_enabled: false,
    quiet_hours_start: '23:00',
    quiet_hours_end: '06:00',
    quiet_hours_timezone: 'Asia/Ho_Chi_Minh',
    bypass_critical_quiet_hours: true,
  });

  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showBotToken, setShowBotToken] = useState(false);

  // Test status
  const [testStatus, setTestStatus] = useState<{
    loading: boolean;
    msg: string | null;
    ok: boolean | null;
    message_id?: string | number | null;
  }>({ loading: false, msg: null, ok: null });

  // Outbox history
  const [outboxItems, setOutboxItems] = useState<NotificationHistoryItem[]>([]);
  const [outboxTotal, setOutboxTotal] = useState(0);
  const [outboxPage, setOutboxPage] = useState(1);
  const outboxPageSize = 15;
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [typeFilter, setTypeFilter] = useState<string>('ALL');
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [retryingId, setRetryingId] = useState<number | null>(null);

  // Load config from server
  const loadConfig = useCallback(async () => {
    try {
      const cfg = await api.getTelegramConfig();
      setSavedConfig(cfg);
      setDraftConfig({
        ...cfg,
        bot_token: '', // Never populate token in draft text field unless typed
      });
      setSaveError(null);
    } catch (err) {
      console.error('Failed to load telegram config', err);
    }
  }, []);

  // Load notification history
  const loadHistory = useCallback(async () => {
    setIsLoadingHistory(true);
    try {
      const params: any = {
        page: outboxPage,
        page_size: outboxPageSize,
      };
      if (statusFilter !== 'ALL') params.status = statusFilter;
      if (typeFilter !== 'ALL') params.message_type = typeFilter;

      const res = await api.getNotificationHistoryPaginated(params);
      setOutboxItems(res.items || []);
      setOutboxTotal(res.total || 0);
    } catch (err) {
      console.error('Failed to load notification history', err);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [outboxPage, outboxPageSize, statusFilter, typeFilter]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  useEffect(() => {
    loadHistory();
    const unsub = wsClient.subscribe((msg: any) => {
      if (
        msg.type === 'OUTBOX_UPDATED' ||
        (msg.type === 'DOMAIN_EVENT' && (
          msg.event?.event_type?.startsWith('notification.') ||
          msg.event?.event_type === 'trade.opened' ||
          msg.event?.event_type === 'trade.closed' ||
          msg.event?.event_type === 'order.armed' ||
          msg.event?.event_type === 'order.cancelled' ||
          msg.event?.event_type === 'setup.cancelled'
        ))
      ) {
        loadHistory();
      }
    });
    return () => unsub();
  }, [loadHistory]);

  // Check if draft has unsaved changes
  const isDirty = useMemo(() => {
    if (!savedConfig) return false;
    if (draftConfig.bot_token && draftConfig.bot_token.trim().length > 0) return true;
    if (draftConfig.enabled !== savedConfig.enabled) return true;
    if (draftConfig.chat_id.trim() !== (savedConfig.chat_id || '').trim()) return true;
    if (draftConfig.near_entry_mode !== (savedConfig.near_entry_mode || 'ATR')) return true;
    if (draftConfig.near_entry_price_dist !== (savedConfig.near_entry_price_dist ?? 2.0)) return true;
    if (draftConfig.near_entry_atr_mult !== (savedConfig.near_entry_atr_mult ?? 0.5)) return true;
    if (draftConfig.near_entry_cooldown_min !== (savedConfig.near_entry_cooldown_min ?? 30)) return true;
    if (draftConfig.quiet_hours_enabled !== (savedConfig.quiet_hours_enabled ?? false)) return true;
    if (draftConfig.quiet_hours_start !== (savedConfig.quiet_hours_start || '23:00')) return true;
    if (draftConfig.quiet_hours_end !== (savedConfig.quiet_hours_end || '06:00')) return true;
    if (draftConfig.bypass_critical_quiet_hours !== (savedConfig.bypass_critical_quiet_hours ?? true)) return true;

    // Subscribed events
    const dSubs = [...(draftConfig.subscribed_events || [])].sort();
    const sSubs = [...(savedConfig.subscribed_events || [])].sort();
    if (dSubs.join(',') !== sSubs.join(',')) return true;

    return false;
  }, [draftConfig, savedConfig]);

  // Handle Save
  const handleSave = async () => {
    if (isSaving) return;
    setIsSaving(true);
    setSaveError(null);

    // Validate Chat ID if enabled
    if (draftConfig.enabled && !draftConfig.chat_id.trim()) {
      setSaveError('Vui lòng nhập Chat ID trước khi kích hoạt thông báo tự động.');
      setIsSaving(false);
      return;
    }

    // Validate token if enabling and server doesn't have token
    if (draftConfig.enabled && !savedConfig?.has_token && !draftConfig.bot_token?.trim()) {
      setSaveError('Vui lòng nhập Bot Token trước khi kích hoạt thông báo tự động.');
      setIsSaving(false);
      return;
    }

    try {
      const payload: any = {
        enabled: draftConfig.enabled,
        chat_id: draftConfig.chat_id.trim(),
        subscribed_events: draftConfig.subscribed_events,
        near_entry_mode: draftConfig.near_entry_mode,
        near_entry_price_dist: Number(draftConfig.near_entry_price_dist) || 2.0,
        near_entry_atr_mult: Number(draftConfig.near_entry_atr_mult) || 0.5,
        near_entry_cooldown_min: Number(draftConfig.near_entry_cooldown_min) || 30,
        quiet_hours_enabled: draftConfig.quiet_hours_enabled,
        quiet_hours_start: draftConfig.quiet_hours_start,
        quiet_hours_end: draftConfig.quiet_hours_end,
        quiet_hours_timezone: draftConfig.quiet_hours_timezone || 'Asia/Ho_Chi_Minh',
        bypass_critical_quiet_hours: draftConfig.bypass_critical_quiet_hours,
      };

      // Only send bot_token if user actually typed a new one
      if (draftConfig.bot_token && draftConfig.bot_token.trim().length > 0) {
        payload.bot_token = draftConfig.bot_token.trim();
      }

      const updated = await api.updateTelegramConfig(payload);
      setSavedConfig(updated);
      setDraftConfig({
        ...updated,
        bot_token: '', // Clear raw token from draft input
      });
      setSaveError(null);
      if (showToast) {
        showToast('Telegram', 'Đã lưu cấu hình Telegram thành công!', 'success');
      }
    } catch (err: any) {
      const msg = extractErrorMessage(err, 'Lỗi khi lưu cấu hình Telegram');
      setSaveError(msg);
    } finally {
      setIsSaving(false);
    }
  };

  // Handle Clear Token
  const handleClearToken = async () => {
    if (!window.confirm('Bạn có chắc chắn muốn xóa Bot Token đã lưu trên hệ thống? Thông báo tự động sẽ bị tắt.')) {
      return;
    }
    setIsSaving(true);
    try {
      const updated = await api.updateTelegramConfig({
        clear_token: true,
        enabled: false,
      });
      setSavedConfig(updated);
      setDraftConfig({
        ...updated,
        bot_token: '',
      });
      if (showToast) {
        showToast('Telegram', 'Đã xóa Bot Token thành công.', 'info');
      }
    } catch (err: any) {
      setSaveError(extractErrorMessage(err, 'Lỗi khi xóa Bot Token'));
    } finally {
      setIsSaving(false);
    }
  };

  // Handle Test Message
  const handleTestMessage = async () => {
    const chatId = draftConfig.chat_id.trim();
    if (!chatId) {
      setTestStatus({
        loading: false,
        msg: 'Vui lòng nhập Chat ID để gửi tin nhắn thử nghiệm.',
        ok: false,
      });
      return;
    }

    const token = draftConfig.bot_token?.trim() || undefined;
    if (!token && !savedConfig?.has_token) {
      setTestStatus({
        loading: false,
        msg: 'Chưa có Bot Token (trong ô nhập hoặc đã lưu trên server). Vui lòng nhập token.',
        ok: false,
      });
      return;
    }

    setTestStatus({
      loading: true,
      msg: 'Đang gửi tin nhắn thử nghiệm tới Telegram...',
      ok: null,
    });

    try {
      const res = await api.testTelegram({
        bot_token: token,
        chat_id: chatId,
      });

      let successMsg = `Thành công: Đã gửi tin nhắn test tới Chat ID ${chatId} (Telegram Message ID: ${res.message_id || 'OK'}).`;
      if (!savedConfig?.enabled) {
        successMsg += ' ℹ️ Lưu ý: Thông báo tự động realtime của hệ thống hiện vẫn đang TẮT. Hãy bật công tắc và bấm "Lưu Cấu Hình" để kích hoạt.';
      }

      setTestStatus({
        loading: false,
        msg: successMsg,
        ok: true,
        message_id: res.message_id,
      });
      // Also refresh outbox
      loadHistory();
    } catch (err: any) {
      const errMsg = extractErrorMessage(err, 'Lỗi kết nối không xác định tới Telegram API');
      const retryAfter = err.response?.data?.detail?.retry_after;
      const fullMsg = retryAfter ? `${errMsg} (Telegram giới hạn tốc độ: thử lại sau ${retryAfter}s)` : errMsg;
      setTestStatus({
        loading: false,
        msg: `Thất bại: ${fullMsg}`,
        ok: false,
      });
    }
  };

  // Handle Retry Outbox Item
  const handleRetryItem = async (item: NotificationHistoryItem) => {
    if (item.status === 'SENT') {
      alert('Tin nhắn này đã gửi thành công trước đó (SENT). Không thể gửi lại để tránh spam.');
      return;
    }

    if (item.status === 'AMBIGUOUS') {
      const confirmed = window.confirm(
        'CẢNH BÁO: Tin nhắn này có trạng thái AMBIGUOUS (kết nối mạng timeout trong lúc gửi). ' +
        'Rất có thể Telegram đã nhận và chuyển tới điện thoại của bạn rồi.\n\n' +
        'Bạn có chắc chắn muốn thử gửi lại (nguy cơ gửi trùng)?'
      );
      if (!confirmed) return;
    }

    setRetryingId(item.id);
    try {
      await api.retryOutboxItem(item.id);
      if (showToast) {
        showToast('Hàng Đợi', `Đã đặt tin nhắn #${item.id} vào lại hàng đợi gửi.`, 'success');
      }
      loadHistory();
    } catch (err: any) {
      alert(extractErrorMessage(err, 'Lỗi khi gửi lại tin nhắn'));
    } finally {
      setRetryingId(null);
    }
  };

  const totalPages = Math.ceil(outboxTotal / outboxPageSize) || 1;

  return (
    <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-5 overflow-y-auto max-h-[820px]">
      {/* Header */}
      <div className="flex flex-wrap justify-between items-center border-b border-charcoal-750 pb-3 gap-3">
        <div>
          <h2 className="text-base font-bold text-aurum-400 flex items-center gap-2">
            <Smartphone className="w-5 h-5 text-aurum-400" />
            Cấu Hình Thông Báo Điện Thoại Qua Telegram
          </h2>
          <p className="text-xs text-gray-400 mt-0.5">
            Nhận cảnh báo tức thời về điện thoại cho setup NEAR_ENTRY, READY, lệnh ARMED, khớp FILLED, TP/SL và biến động vị thế.
          </p>
        </div>

        {/* Realtime Backend System Status Badge */}
        <div className="flex items-center gap-2">
          {isDirty && (
            <span className="px-2.5 py-1 rounded bg-amber-950/90 text-amber-300 border border-amber-600 text-xs font-bold animate-pulse">
              ● Có thay đổi chưa lưu
            </span>
          )}
          <div
            className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border ${
              savedConfig?.enabled
                ? 'bg-emerald-950/80 text-emerald-300 border-emerald-600'
                : 'bg-charcoal-800 text-gray-400 border-charcoal-700'
            }`}
          >
            {savedConfig?.enabled ? (
              <>
                <Bell className="w-3.5 h-3.5 text-emerald-400" />
                <span>THÔNG BÁO TỰ ĐỘNG: ĐÃ BẬT</span>
              </>
            ) : (
              <>
                <BellOff className="w-3.5 h-3.5 text-gray-400" />
                <span>THÔNG BÁO TỰ ĐỘNG: ĐANG TẮT</span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Diagnostics Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-charcoal-850 p-3 rounded-lg border border-charcoal-750 text-xs">
        <div>
          <span className="text-gray-400 block text-[11px]">Trạng Thái Token Máy Chủ</span>
          <span className={`font-bold flex items-center gap-1 mt-0.5 ${savedConfig?.has_token ? 'text-emerald-400' : 'text-amber-400'}`}>
            {savedConfig?.has_token ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
            {savedConfig?.has_token ? 'Đã Lưu An Toàn' : 'Chưa Cấu Hình'}
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">Chat ID Nhận Tin</span>
          <span className="font-mono font-semibold text-gray-200 block mt-0.5 truncate" title={savedConfig?.chat_id || 'Chưa có'}>
            {savedConfig?.chat_id || '— Chưa cài đặt —'}
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">Khung Giờ Yên Lặng</span>
          <span className="font-semibold text-gray-300 block mt-0.5">
            {savedConfig?.quiet_hours_enabled
              ? `${savedConfig.quiet_hours_start} - ${savedConfig.quiet_hours_end} (VN)`
              : 'Đang tắt'}
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">Sự Kiện Đăng Ký</span>
          <span className="font-semibold text-aurum-300 block mt-0.5">
            {savedConfig?.subscribed_events?.length || 0} / {EVENT_OPTIONS.length} sự kiện
          </span>
        </div>
      </div>

      {/* Main Form Settings Card */}
      <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 flex flex-col gap-4 text-xs">
        {/* Toggle Draft Switch */}
        <div className="flex justify-between items-center border-b border-charcoal-700 pb-3">
          <div>
            <span className="font-bold text-gray-200 block text-sm">Bật Thông Báo Telegram Tự Động</span>
            <span className="text-[11px] text-gray-400">
              Gửi tin độc lập qua outbox worker nền, tự động retry khi gặp sự cố mạng hoặc Telegram rate-limit.
            </span>
          </div>
          <div className="flex items-center gap-3">
            {draftConfig.enabled !== savedConfig?.enabled && (
              <span className="text-[11px] text-amber-400 font-semibold italic">
                (Draft: {draftConfig.enabled ? 'Bật' : 'Tắt'} · Cần bấm Lưu)
              </span>
            )}
            <button
              type="button"
              onClick={() => setDraftConfig({ ...draftConfig, enabled: !draftConfig.enabled })}
              className={`px-4 py-1.5 rounded-full font-bold text-xs transition border ${
                draftConfig.enabled
                  ? 'bg-emerald-500 text-charcoal-950 border-emerald-400'
                  : 'bg-charcoal-800 text-gray-400 border-charcoal-700 hover:text-gray-200'
              }`}
            >
              {draftConfig.enabled ? 'ĐÃ BẬT TRÊN FORM' : 'ĐANG TẮT TRÊN FORM'}
            </button>
          </div>
        </div>

        {/* Credentials row: Bot Token & Chat ID */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Bot Token */}
          <div className="space-y-1.5">
            <div className="flex justify-between items-center">
              <label className="font-semibold text-gray-300 flex items-center gap-1.5">
                <span>Bot Token (Từ @BotFather):</span>
                {savedConfig?.has_token && (
                  <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-300 border border-emerald-700">
                    Đã có token
                  </span>
                )}
              </label>
              <div className="flex items-center gap-2">
                {savedConfig?.has_token && (
                  <button
                    type="button"
                    onClick={handleClearToken}
                    disabled={isSaving}
                    className="text-[11px] text-rose-400 hover:text-rose-300 flex items-center gap-1"
                    title="Xóa token khỏi máy chủ"
                  >
                    <Trash2 className="w-3 h-3" />
                    <span>Xóa</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setShowBotToken(!showBotToken)}
                  className="text-[11px] text-indigo-400 hover:text-indigo-300 flex items-center gap-1"
                >
                  {showBotToken ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                  <span>{showBotToken ? 'Ẩn' : 'Hiện'}</span>
                </button>
              </div>
            </div>
            <input
              type={showBotToken ? 'text' : 'password'}
              value={draftConfig.bot_token || ''}
              onChange={(e) => setDraftConfig({ ...draftConfig, bot_token: e.target.value })}
              placeholder={savedConfig?.has_token ? '●●●●●●●● (Giữ nguyên token đã lưu)' : 'VD: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz...'}
              className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-2 text-xs font-mono text-gray-200 focus:outline-none focus:border-aurum-500 placeholder-gray-500"
            />
            <p className="text-[10px] text-gray-500">
              * Để trống nếu muốn giữ nguyên token cũ đã lưu. Token được bảo vệ bí mật, không lưu trong logs hay frontend bundle.
            </p>
          </div>

          {/* Chat ID */}
          <div className="space-y-1.5">
            <div className="flex justify-between items-center">
              <label className="font-semibold text-gray-300">Chat ID / Group ID Của Bạn:</label>
              <span className="text-[10px] text-gray-400">Hỗ trợ số âm cho Group/Channel</span>
            </div>
            <input
              type="text"
              value={draftConfig.chat_id || ''}
              onChange={(e) => setDraftConfig({ ...draftConfig, chat_id: e.target.value })}
              placeholder="VD: 6919390280 hoặc -1001234567890"
              className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-2 text-xs font-mono text-gray-200 focus:outline-none focus:border-aurum-500"
            />
            <p className="text-[10px] text-gray-500">
              * Lấy Chat ID bằng cách chat với bot <code className="text-gray-300">@userinfobot</code> hoặc <code className="text-gray-300">@getidsbot</code> trên Telegram.
            </p>
          </div>
        </div>

        {/* Subscribed Events Toggle Pills */}
        <div className="space-y-2 pt-2 border-t border-charcoal-750">
          <div className="flex justify-between items-center">
            <label className="font-semibold text-gray-300 block">Đăng Ký Loại Sự Kiện Nhận Cảnh Báo:</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setDraftConfig({ ...draftConfig, subscribed_events: EVENT_OPTIONS.map((x) => x.id) })}
                className="text-[10px] text-indigo-400 hover:text-indigo-300"
              >
                Chọn tất cả
              </button>
              <span className="text-gray-600">|</span>
              <button
                type="button"
                onClick={() => setDraftConfig({ ...draftConfig, subscribed_events: [] })}
                className="text-[10px] text-gray-400 hover:text-gray-300"
              >
                Bỏ chọn hết
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {EVENT_OPTIONS.map((item) => {
              const isSubscribed = (draftConfig.subscribed_events || []).includes(item.id);
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    const current = draftConfig.subscribed_events || [];
                    const next = isSubscribed
                      ? current.filter((x: string) => x !== item.id)
                      : [...current, item.id];
                    setDraftConfig({ ...draftConfig, subscribed_events: next });
                  }}
                  className={`px-2.5 py-1 rounded text-[11px] font-semibold transition border ${
                    isSubscribed
                      ? 'bg-aurum-500/20 border-aurum-400 text-aurum-300'
                      : 'bg-charcoal-900 border-charcoal-700 text-gray-500 hover:text-gray-400'
                  }`}
                  title={item.desc}
                >
                  {isSubscribed ? '✓ ' : '+ '}
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Proximity / Near-entry Settings */}
        <div className="space-y-2 pt-2 border-t border-charcoal-750">
          <label className="font-semibold text-aurum-400 block">
            Cấu Hình Cảnh Báo Gần Vùng Entry (NEAR_ENTRY Proximity):
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            <div>
              <label className="text-[11px] text-gray-400 block mb-1">Phương Thức Đo:</label>
              <select
                value={draftConfig.near_entry_mode || 'ATR'}
                onChange={(e) => setDraftConfig({ ...draftConfig, near_entry_mode: e.target.value })}
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
              >
                <option value="ATR">Theo Hệ Số ATR 15M (Tự Động Theo Biến Động)</option>
                <option value="PRICE_DISTANCE">Khoảng Cách Giá Cố Định (USDT)</option>
              </select>
            </div>

            {draftConfig.near_entry_mode === 'PRICE_DISTANCE' ? (
              <div>
                <label className="text-[11px] text-gray-400 block mb-1">Khoảng Cách Giá (USDT):</label>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  max="50"
                  value={draftConfig.near_entry_price_dist ?? 2.0}
                  onChange={(e) => setDraftConfig({ ...draftConfig, near_entry_price_dist: Number(e.target.value) })}
                  className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
                />
              </div>
            ) : (
              <div>
                <label className="text-[11px] text-gray-400 block mb-1">Hệ Số ATR (× ATR 15M):</label>
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="3.0"
                  value={draftConfig.near_entry_atr_mult ?? 0.5}
                  onChange={(e) => setDraftConfig({ ...draftConfig, near_entry_atr_mult: Number(e.target.value) })}
                  className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
                />
              </div>
            )}

            <div>
              <label className="text-[11px] text-gray-400 block mb-1">Cooldown Giữa 2 Lần Báo (Phút):</label>
              <input
                type="number"
                min="5"
                max="180"
                value={draftConfig.near_entry_cooldown_min ?? 30}
                onChange={(e) => setDraftConfig({ ...draftConfig, near_entry_cooldown_min: Number(e.target.value) })}
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
              />
            </div>
          </div>
        </div>

        {/* Quiet Hours Settings */}
        <div className="space-y-2 pt-2 border-t border-charcoal-750">
          <div className="flex justify-between items-center">
            <label className="font-semibold text-gray-300">Khung Giờ Yên Lặng (Quiet Hours):</label>
            <label className="flex items-center gap-1.5 text-[11px] text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={draftConfig.quiet_hours_enabled || false}
                onChange={(e) => setDraftConfig({ ...draftConfig, quiet_hours_enabled: e.target.checked })}
                className="rounded border-charcoal-700 text-aurum-500 focus:ring-0"
              />
              <span>Kích hoạt Giờ Yên Lặng</span>
            </label>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="text-[11px] text-gray-400 block mb-1">Giờ Bắt Đầu (Giờ VN UTC+7):</label>
              <input
                type="text"
                value={draftConfig.quiet_hours_start || '23:00'}
                onChange={(e) => setDraftConfig({ ...draftConfig, quiet_hours_start: e.target.value })}
                placeholder="23:00"
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-1.5 text-xs text-gray-200 font-mono"
              />
            </div>
            <div>
              <label className="text-[11px] text-gray-400 block mb-1">Giờ Kết Thúc (Giờ VN UTC+7):</label>
              <input
                type="text"
                value={draftConfig.quiet_hours_end || '06:00'}
                onChange={(e) => setDraftConfig({ ...draftConfig, quiet_hours_end: e.target.value })}
                placeholder="06:00"
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-1.5 text-xs text-gray-200 font-mono"
              />
            </div>
            <div className="flex items-center pt-3 sm:pt-4">
              <label className="flex items-center gap-2 text-[11px] text-emerald-300 font-semibold cursor-pointer">
                <input
                  type="checkbox"
                  checked={draftConfig.bypass_critical_quiet_hours !== false}
                  onChange={(e) => setDraftConfig({ ...draftConfig, bypass_critical_quiet_hours: e.target.checked })}
                  className="rounded border-charcoal-700 text-emerald-500 focus:ring-0"
                />
                <span>Vẫn gửi FILLED / TP / SL / Thanh Lý khi yên lặng</span>
              </label>
            </div>
          </div>
        </div>

        {/* Save error message if present */}
        {saveError && (
          <div className="p-3 rounded bg-rose-950/90 border border-rose-700 text-rose-300 text-xs flex items-center gap-2">
            <XCircle className="w-4 h-4 shrink-0 text-rose-400" />
            <span>{saveError}</span>
          </div>
        )}

        {/* Buttons Row: Test & Save */}
        <div className="flex flex-wrap justify-between items-center gap-3 pt-3 border-t border-charcoal-750">
          <button
            type="button"
            onClick={handleTestMessage}
            disabled={testStatus.loading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-charcoal-700 text-white font-semibold rounded text-xs transition"
          >
            {testStatus.loading ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Send className="w-3.5 h-3.5" />
            )}
            <span>{testStatus.loading ? 'Đang gửi test...' : 'Gửi Tin Nhắn Thử Nghiệm'}</span>
          </button>

          <div className="flex items-center gap-2">
            {isDirty && (
              <button
                type="button"
                onClick={() => {
                  if (savedConfig) {
                    setDraftConfig({
                      ...savedConfig,
                      bot_token: '',
                    });
                    setSaveError(null);
                  }
                }}
                className="px-3 py-1.5 bg-charcoal-800 hover:bg-charcoal-700 text-gray-300 rounded text-xs transition"
              >
                Hủy thay đổi
              </button>
            )}
            <button
              type="button"
              onClick={handleSave}
              disabled={isSaving}
              className={`flex items-center gap-1.5 px-4 py-1.5 font-bold rounded text-xs transition ${
                isSaving
                  ? 'bg-charcoal-700 text-gray-400 cursor-not-allowed'
                  : 'bg-aurum-500 hover:bg-aurum-400 text-charcoal-950'
              }`}
            >
              {isSaving ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              <span>{isSaving ? 'Đang lưu...' : 'Lưu Cấu Hình Telegram'}</span>
            </button>
          </div>
        </div>

        {/* Test Result Banner */}
        {testStatus.msg && (
          <div
            className={`p-3 rounded border text-xs flex items-start gap-2 ${
              testStatus.ok
                ? 'bg-emerald-950/80 border-emerald-700 text-emerald-300'
                : 'bg-rose-950/80 border-rose-700 text-rose-300'
            }`}
          >
            {testStatus.ok ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400 mt-0.5" />
            ) : (
              <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400 mt-0.5" />
            )}
            <div>
              <p className="font-semibold">{testStatus.msg}</p>
              {testStatus.message_id && (
                <p className="text-[10px] text-emerald-400 font-mono mt-0.5">
                  Telegram Message ID xác nhận: #{testStatus.message_id}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Outbox Queue & Delivery History Table */}
      <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 flex flex-col gap-3 text-xs">
        <div className="flex flex-wrap justify-between items-center border-b border-charcoal-750 pb-2 gap-2">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-aurum-400" />
            <h3 className="font-bold text-gray-200">
              Lịch Sử Hàng Đợi Gửi Tin Nhắn (Outbox Queue & Status)
            </h3>
            <span className="text-[10px] px-2 py-0.5 rounded bg-charcoal-900 text-gray-400 font-mono">
              Tổng: {outboxTotal} tin
            </span>
          </div>

          <div className="flex items-center gap-2">
            {/* Status filter */}
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setOutboxPage(1);
              }}
              className="bg-charcoal-900 border border-charcoal-700 rounded px-2 py-1 text-[11px] text-gray-300"
            >
              <option value="ALL">Tất cả trạng thái</option>
              <option value="SENT">SENT (Đã gửi)</option>
              <option value="PENDING">PENDING (Chờ gửi)</option>
              <option value="RETRYING">RETRYING (Đang retry)</option>
              <option value="FAILED">FAILED (Thất bại)</option>
              <option value="AMBIGUOUS">AMBIGUOUS (Không chắc chắn)</option>
              <option value="SUPPRESSED">SUPPRESSED (Bị chặn/Bỏ qua)</option>
            </select>

            {/* Type filter */}
            <select
              value={typeFilter}
              onChange={(e) => {
                setTypeFilter(e.target.value);
                setOutboxPage(1);
              }}
              className="bg-charcoal-900 border border-charcoal-700 rounded px-2 py-1 text-[11px] text-gray-300"
            >
              <option value="ALL">Tất cả loại tin</option>
              {EVENT_OPTIONS.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  {ev.id}
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={loadHistory}
              disabled={isLoadingHistory}
              className="p-1 rounded bg-charcoal-800 hover:bg-charcoal-700 text-aurum-400 transition"
              title="Làm mới lịch sử"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isLoadingHistory ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Table Content */}
        {outboxItems.length === 0 ? (
          <p className="text-[11px] text-gray-500 italic py-4 text-center">
            {isLoadingHistory ? 'Đang tải hàng đợi...' : 'Không có tin nhắn nào khớp với bộ lọc hiện tại.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-charcoal-700 text-[10px] text-gray-400 uppercase tracking-wider">
                  <th className="py-2 px-2">ID</th>
                  <th className="py-2 px-2">Thời gian</th>
                  <th className="py-2 px-2">Loại Tin</th>
                  <th className="py-2 px-2">Ưu Tiên</th>
                  <th className="py-2 px-2">Khóa / Đối Tượng</th>
                  <th className="py-2 px-2">Trạng Thái</th>
                  <th className="py-2 px-2">Số Thử</th>
                  <th className="py-2 px-2">Msg ID / Lỗi</th>
                  <th className="py-2 px-2 text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-charcoal-800 text-[11px]">
                {outboxItems.map((item) => {
                  const statusColor =
                    item.status === 'SENT'
                      ? 'bg-emerald-950 text-emerald-300 border-emerald-700'
                      : item.status === 'PENDING'
                      ? 'bg-amber-950 text-amber-300 border-amber-700'
                      : item.status === 'RETRYING'
                      ? 'bg-orange-950 text-orange-300 border-orange-700'
                      : item.status === 'AMBIGUOUS'
                      ? 'bg-purple-950 text-purple-300 border-purple-700'
                      : item.status === 'SUPPRESSED'
                      ? 'bg-charcoal-800 text-gray-400 border-charcoal-700'
                      : 'bg-rose-950 text-rose-300 border-rose-700';

                  return (
                    <tr key={item.id} className="hover:bg-charcoal-800/40">
                      <td className="py-2 px-2 font-mono text-gray-400">#{item.id}</td>
                      <td className="py-2 px-2 text-gray-300 whitespace-nowrap">
                        {new Date(item.created_at).toLocaleTimeString('vi-VN')}
                        <span className="text-[9px] text-gray-500 block">
                          {new Date(item.created_at).toLocaleDateString('vi-VN')}
                        </span>
                      </td>
                      <td className="py-2 px-2 font-semibold text-aurum-300">
                        {item.message_type}
                      </td>
                      <td className="py-2 px-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                            item.priority === 'CRITICAL'
                              ? 'bg-rose-900/60 text-rose-300 border border-rose-700'
                              : 'bg-charcoal-750 text-gray-400'
                          }`}
                        >
                          {item.priority || 'STANDARD'}
                        </span>
                      </td>
                      <td
                        className="py-2 px-2 font-mono text-gray-400 text-[10px] truncate max-w-[120px]"
                        title={item.dedupe_key || ''}
                      >
                        {item.dedupe_key || '—'}
                      </td>
                      <td className="py-2 px-2">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${statusColor}`}>
                          {item.status}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-center text-gray-300">
                        {item.attempts}/{item.max_retries || 5}
                      </td>
                      <td className="py-2 px-2 text-gray-400 text-[10px] max-w-[180px]">
                        {item.provider_message_id && (
                          <span className="text-emerald-400 font-mono block">
                            MsgID: #{item.provider_message_id}
                          </span>
                        )}
                        {item.error_message && (
                          <span
                            className="text-rose-400 block truncate"
                            title={item.error_message}
                          >
                            {item.error_code ? `[${item.error_code}] ` : ''}
                            {item.error_message}
                          </span>
                        )}
                        {!item.provider_message_id && !item.error_message && '—'}
                      </td>
                      <td className="py-2 px-2 text-right">
                        {item.status === 'SENT' ? (
                          <span className="text-[10px] text-gray-500 italic">Đã giao</span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => handleRetryItem(item)}
                            disabled={retryingId === item.id}
                            className={`px-2 py-0.5 rounded text-[10px] font-semibold transition ${
                              item.status === 'AMBIGUOUS'
                                ? 'bg-purple-700 hover:bg-purple-600 text-white'
                                : 'bg-indigo-600/80 hover:bg-indigo-600 text-white'
                            }`}
                          >
                            {retryingId === item.id ? 'Đang gửi...' : 'Thử lại'}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex justify-between items-center pt-2 border-t border-charcoal-800 text-[11px] text-gray-400">
            <span>
              Trang {outboxPage} / {totalPages} (Tổng {outboxTotal} tin)
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setOutboxPage((p) => Math.max(1, p - 1))}
                disabled={outboxPage <= 1}
                className="px-2.5 py-1 rounded bg-charcoal-800 hover:bg-charcoal-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-300"
              >
                Trước
              </button>
              <button
                type="button"
                onClick={() => setOutboxPage((p) => Math.min(totalPages, p + 1))}
                disabled={outboxPage >= totalPages}
                className="px-2.5 py-1 rounded bg-charcoal-800 hover:bg-charcoal-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-300"
              >
                Tiếp
              </button>
            </div>
          </div>
        )}
      </div>

      {/* User Guide Card */}
      <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 text-xs space-y-2">
        <h3 className="font-bold text-aurum-400 uppercase tracking-wider flex items-center gap-1.5">
          <HelpCircle className="w-4 h-4 text-aurum-400" />
          Hướng Dẫn Thiết Lập Telegram Bot Nhanh (3 Phút):
        </h3>
        <ol className="list-decimal list-inside space-y-1 text-gray-300">
          <li>
            Mở Telegram, tìm kiếm bot <strong className="text-gray-100">@BotFather</strong> và gửi lệnh <code className="text-aurum-300">/newbot</code>.
          </li>
          <li>
            Làm theo hướng dẫn đặt tên, sao chép chuỗi <strong className="text-gray-100">HTTP API Token</strong> dán vào ô <em>Bot Token</em> ở trên.
          </li>
          <li>
            Bấm <strong>Start</strong> trên con bot bạn vừa tạo để cấp quyền nhận tin.
          </li>
          <li>
            Chat với bot <strong className="text-gray-100">@userinfobot</strong> để lấy số <strong>Id</strong> cá nhân, dán vào ô <em>Chat ID</em>.
          </li>
          <li>
            Bấm <strong>Gửi Tin Nhắn Thử Nghiệm</strong>. Sau khi thấy tin báo trên điện thoại, bấm <strong>Lưu Cấu Hình</strong> để kích hoạt realtime!
          </li>
        </ol>
      </div>
    </div>
  );
};
