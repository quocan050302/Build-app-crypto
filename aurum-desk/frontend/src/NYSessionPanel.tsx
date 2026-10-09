import React from 'react';
import type { NYSessionStatus, TradingPolicy } from './api/client';
import {
  Clock,
  Shield,
  Target,
  Zap,
  CheckCircle2,
  Flame,
  Sparkles
} from 'lucide-react';

interface NYSessionPanelProps {
  nySession: NYSessionStatus | null;
  policy: TradingPolicy | null;
  autoPaperActive: boolean;
  onRefresh?: () => void;
}

export const NYSessionPanel: React.FC<NYSessionPanelProps> = ({
  nySession,
  policy,
  autoPaperActive: _autoPaperActive,
}) => {
  if (!nySession) {
    return (
      <div className="bg-charcoal-900 border-b border-charcoal-750 px-4 py-2 text-xs text-gray-400 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-aurum-400 animate-spin" />
          <span>Đang đồng bộ trạng thái Cửa Sổ Phiên Mỹ & Quota V7...</span>
        </div>
      </div>
    );
  }

  const {
    is_in_ny_window,
    is_fallback_active,
    quota_state,
    daily_fills,
    max_daily_fills,
    remaining_daily_slots: _remaining_daily_slots,
    ny_fills,
    ny_min_fills,
    reserved_slots,
    local_ny_time,
    local_vn_time,
    ny_window_display,
    vn_window_display,
    minutes_to_window_start,
    minutes_to_fallback,
    minutes_to_window_end,
    allowed,
    reason_code,
    reason_message
  } = nySession;

  // Determine badge styling based on quota_state
  const getBadgeStyle = () => {
    switch (quota_state) {
      case 'FULFILLED':
        return 'bg-emerald-950/80 border-emerald-500 text-emerald-300 font-bold';
      case 'SEEKING_STANDARD':
        return 'bg-blue-950/80 border-blue-500 text-blue-300 font-semibold';
      case 'SEEKING_FALLBACK':
        return 'bg-amber-950/80 border-amber-500 text-amber-300 font-semibold animate-pulse';
      case 'ARMED_PENDING_FILL':
        return 'bg-yellow-950/80 border-yellow-500 text-yellow-300 font-bold animate-pulse';
      case 'BLOCKED':
        return 'bg-rose-950/80 border-rose-500 text-rose-300 font-bold';
      case 'MISSED':
        return 'bg-rose-950/60 border-rose-700 text-rose-400 font-medium';
      default:
        return 'bg-charcoal-800 border-charcoal-700 text-gray-400';
    }
  };

  const getStatusLabel = () => {
    switch (quota_state) {
      case 'FULFILLED':
        return 'ĐÃ HOÀN THÀNH CHỈ TIÊU (1/1)';
      case 'SEEKING_STANDARD':
        return 'ƯU TIÊN SMC CHUẨN';
      case 'SEEKING_FALLBACK':
        return 'TÌM LỆNH FALLBACK (10:30+)';
      case 'ARMED_PENDING_FILL':
        return 'ĐÃ LÊN NÒNG — CHỜ KHỚP';
      case 'BLOCKED':
        return `BỊ CHẶN (${reason_code || 'SAFETY_GUARD'})`;
      case 'MISSED':
        return `LỠ PHIÊN (${reason_code || 'NO_FILL'})`;
      case 'NOT_STARTED':
        return 'CHƯA TỚI CỬA SỔ';
      default:
        return quota_state;
    }
  };

  const getStrategyModeText = () => {
    if (quota_state === 'FULFILLED') {
      return 'Đã hoàn thành mục tiêu 1 lệnh PAPER trong phiên Mỹ. Các lệnh tiếp theo (nếu có) phải đảm bảo chuẩn SMC và không vượt trần 3 lệnh.';
    }
    if (quota_state === 'SEEKING_FALLBACK' || is_fallback_active) {
      return 'Đang quét tìm cơ hội fallback PAPER (risk cap <= 0.10%, Net RR >= 2.0). Đảm bảo phân loại trung thực NY_QUOTA_PAPER.';
    }
    if (quota_state === 'SEEKING_STANDARD') {
      return 'Đang quét tìm setup SMC chuẩn: HTF Aligned, Liquidity Sweep, Displacement CHOCH/BOS, và FVG Retest xác thực.';
    }
    if (quota_state === 'ARMED_PENDING_FILL') {
      return 'Lệnh đã được đưa vào trạng thái ARMED. ExecutionCoordinator đang theo dõi điều kiện retest/trigger để khớp lệnh PAPER.';
    }
    if (quota_state === 'BLOCKED') {
      return reason_message || 'Entry bị chặn tạm thời bởi các quy tắc an toàn (Risk / Vị thế / Cooldown / Tin tức).';
    }
    if (quota_state === 'MISSED') {
      return reason_message || 'Cửa sổ phiên Mỹ đã kết thúc mà không có lệnh PAPER nào đạt chuẩn.';
    }
    return 'Ngoài cửa sổ vào lệnh phiên Mỹ (Hệ thống vẫn thu thập dữ liệu, phân tích đa khung và theo dõi thoát lệnh).';
  };

  return (
    <div className="bg-charcoal-900 border-b border-charcoal-750 px-4 py-2.5 transition-all select-none">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Left: Clock, Window, Policy Info */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className={`w-2.5 h-2.5 rounded-full ${is_in_ny_window ? 'bg-emerald-400 animate-ping' : 'bg-gray-500'}`} />
            <span className="font-bold text-xs tracking-wider text-aurum-400 flex items-center gap-1.5">
              <Flame className="w-3.5 h-3.5 text-amber-400" />
              PHIÊN MỸ V7
            </span>
          </div>

          <div className="flex items-center gap-1.5 bg-charcoal-850 px-2 py-0.5 rounded border border-charcoal-700 text-[11px] font-mono">
            <Clock className="w-3 h-3 text-indigo-400" />
            <span className="text-gray-400">NY:</span>
            <span className="text-gray-200 font-bold">{local_ny_time || '--:--'}</span>
            <span className="text-charcoal-600">|</span>
            <span className="text-gray-400">VN:</span>
            <span className="text-aurum-300 font-bold">{local_vn_time || '--:--'}</span>
          </div>

          <div className="flex items-center gap-1 text-[11px] text-gray-300">
            <span className="text-gray-400">Cửa sổ:</span>
            <span className="font-semibold text-gray-200">{ny_window_display}</span>
            <span className="text-gray-400">({vn_window_display})</span>
          </div>

          <div className="hidden sm:flex items-center gap-1 text-[10px] px-2 py-0.5 rounded bg-charcoal-800 text-gray-400 border border-charcoal-700">
            <Shield className="w-3 h-3 text-emerald-400" />
            <span>Policy: {policy?.entry_session_policy === 'ALL_SESSIONS_WITH_NY_RESERVE' ? 'Tất cả phiên (giữ slot Mỹ)' : 'Chỉ Phiên Mỹ (NY_ONLY)'}</span>
          </div>
        </div>

        {/* Center / Right: Metrics & Quota State */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Metric 1: Today Fills */}
          <div className="flex items-center gap-1.5 bg-charcoal-850 px-2.5 py-1 rounded border border-charcoal-700 text-xs">
            <span className="text-gray-400">Khớp Hôm Nay:</span>
            <span className={`font-bold font-mono ${daily_fills >= max_daily_fills ? 'text-rose-400' : 'text-emerald-400'}`}>
              {daily_fills} / {max_daily_fills}
            </span>
            <span className="text-[10px] text-gray-500">(Trần 3)</span>
          </div>

          {/* Metric 2: NY Quota Target */}
          <div className="flex items-center gap-1.5 bg-charcoal-850 px-2.5 py-1 rounded border border-charcoal-700 text-xs">
            <Target className="w-3.5 h-3.5 text-aurum-400" />
            <span className="text-gray-400">Mục Tiêu Phiên Mỹ:</span>
            <span className={`font-bold font-mono ${ny_fills >= ny_min_fills ? 'text-emerald-400' : 'text-amber-400'}`}>
              {ny_fills} / {ny_min_fills}
            </span>
            {ny_fills >= ny_min_fills && (
              <CheckCircle2 className="w-3 h-3 text-emerald-400" />
            )}
          </div>

          {/* Metric 3: Reserved slots */}
          {reserved_slots > 0 && (
            <div className="hidden md:flex items-center gap-1 bg-amber-950/40 border border-amber-800/60 px-2 py-1 rounded text-[11px] text-amber-300">
              <Zap className="w-3 h-3 text-amber-400" />
              <span>Dự Trữ: {reserved_slots} Slot Cho Mỹ</span>
            </div>
          )}

          {/* Quota State Badge */}
          <div className={`px-2.5 py-1 rounded border text-[11px] flex items-center gap-1.5 ${getBadgeStyle()}`}>
            <span>{getStatusLabel()}</span>
          </div>
        </div>
      </div>

      {/* Subline: Status Reasoning & Countdown */}
      <div className="mt-1.5 pt-1.5 border-t border-charcoal-800 flex flex-wrap items-center justify-between text-[11px] text-gray-400 gap-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-aurum-400 flex items-center gap-1">
            <Sparkles className="w-3 h-3 text-aurum-400" />
            {quota_state === 'SEEKING_FALLBACK' ? 'Chế độ Fallback:' : 'Định hướng:'}
          </span>
          <span className="text-gray-300">{getStrategyModeText()}</span>
        </div>

        {/* Countdown */}
        <div className="flex items-center gap-2">
          {minutes_to_window_start != null && minutes_to_window_start > 0 && (
            <span className="text-indigo-300 bg-indigo-950/40 px-2 py-0.5 rounded border border-indigo-800/50">
              Còn <strong>{minutes_to_window_start} phút</strong> tới Cửa Sổ Phiên Mỹ
            </span>
          )}
          {minutes_to_fallback != null && minutes_to_fallback > 0 && (
            <span className="text-amber-300 bg-amber-950/40 px-2 py-0.5 rounded border border-amber-800/50">
              Còn <strong>{minutes_to_fallback} phút</strong> tới mốc Fallback (10:30 NY)
            </span>
          )}
          {minutes_to_window_end != null && minutes_to_window_end > 0 && (
            <span className="text-rose-300 bg-rose-950/40 px-2 py-0.5 rounded border border-rose-800/50">
              Còn <strong>{minutes_to_window_end} phút</strong> trước khi đóng Cửa Sổ (11:00 NY)
            </span>
          )}
          {!allowed && reason_code && (
            <span className="text-rose-400 font-mono text-[10px] bg-rose-950/60 px-1.5 py-0.5 rounded border border-rose-900">
              {reason_code}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
