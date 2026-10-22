/**
 * frontend/src/utils/paperOpportunityText.ts
 * Aurum V17.0 — Pure helpers for deriving bot waiting summary and human-readable text.
 */
import type { CG01DisplayState } from './cg01DisplayState';
import { getCG01ReasonDescription } from './cg01ReasonText';
import type { BotWaitingSummary } from '../types/paperOpportunityMonitor';

/**
 * Derives a human-friendly, accurate explanation of what the bot is currently doing / waiting for.
 * Conforms to Rules 104 - 108.
 * Pure function: zero side-effects, does not perform network requests.
 */
export function deriveBotWaitingSummary(
  display: CG01DisplayState | null | undefined,
  _evaluation?: any | null,
): BotWaitingSummary {
  if (!display) {
    return {
      headline: 'Đang khởi động',
      detail: 'Chưa nhận được trạng thái đánh giá ban đầu.',
      tone: 'neutral',
      asOfMs: null,
      isHistoricalReference: false,
    };
  }

  const { isConnected, isStale, asOfMs, primaryReason, stage, tone, description } = display;

  // 1. Connection / Stale state takes highest precedence
  if (!isConnected) {
    return {
      headline: 'Mất kết nối',
      detail: 'Chưa có dữ liệu mới từ máy chủ. Đang chờ kết nối lại.',
      tone: 'danger',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (isStale) {
    const timeStr = asOfMs > 0 ? new Date(asOfMs).toLocaleTimeString('vi-VN') : '';
    return {
      headline: 'Dữ liệu chậm',
      detail: `Đánh giá gần nhất lúc ${timeStr || 'trước đó'}, chưa có dữ liệu mới.`,
      tone: 'warning',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  // 2. Model disabled
  if (primaryReason === 'MODEL_DISABLED' || stage === 'MODEL_DISABLED') {
    return {
      headline: 'CG-01 đang tắt',
      detail: 'Mô hình CG-01 hiện đang tắt trong cấu hình hệ thống.',
      tone: 'neutral',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  // 3. Waiting stages & conditions
  const stageUpper = String(stage || '').toUpperCase();
  const reasonUpper = String(primaryReason || '').toUpperCase();

  if (stageUpper === 'ARMED') {
    return {
      headline: 'Đã đặt lệnh chờ (ARM)',
      detail: 'Lệnh đã được kích hoạt và đang chờ khớp trên thị trường.',
      tone: 'success',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'GUARD_BLOCKED' || reasonUpper === 'GUARD_BLOCKED') {
    const reasonMsg = getCG01ReasonDescription(primaryReason);
    return {
      headline: 'Tạm dừng vào lệnh',
      detail: reasonMsg || 'Cơ chế kiểm soát rủi ro (Guard) đang chủ động bảo vệ tài khoản.',
      tone: 'warning',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'EXECUTABLE') {
    return {
      headline: 'Đủ điều kiện kỹ thuật',
      detail: 'Setup đạt đầy đủ tiêu chuẩn SMC/ICT, đang chuẩn bị bước đặt lệnh.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_RETEST' || stageUpper === 'WAIT_ENTRY' || reasonUpper === 'WAIT_RETEST') {
    return {
      headline: 'Chờ giá hồi về (Retest)',
      detail: 'Đã xác nhận cấu trúc phá vỡ (MSS), đang chờ nến M1 hồi vào vùng FVG.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_FVG' || reasonUpper === 'WAIT_FVG') {
    return {
      headline: 'Chờ tạo FVG',
      detail: 'Đã xác nhận MSS, đang chờ nến M1 tạo khoảng trống giá (FVG) hợp lệ.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_MSS' || reasonUpper === 'WAIT_MSS') {
    return {
      headline: 'Chờ phá cấu trúc (MSS)',
      detail: 'Đã quét thanh khoản; đang chờ nến M1 đóng cửa xác nhận phá vỡ cấu trúc.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_SWEEP' || reasonUpper === 'WAIT_SWEEP') {
    return {
      headline: 'Chờ quét thanh khoản',
      detail: 'Đang theo dõi vùng giá trọng tâm, chờ quét qua các mốc thanh khoản.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_POI') {
    return {
      headline: 'Chờ tiếp cận vùng',
      detail: 'Đang chờ giá di chuyển vào vùng giá quan tâm (POI) đã chọn.',
      tone: 'neutral',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  // 4. Default observing
  const fallbackDetail = description && description.trim().length > 0
    ? description
    : 'Đang quét thị trường tìm cấu trúc SMC đủ điều kiện.';

  return {
    headline: 'Đang theo dõi',
    detail: fallbackDetail,
    tone: tone || 'neutral',
    asOfMs,
    isHistoricalReference: false,
  };
}

/**
 * Formats epoch ms into localized Vietnam time (HH:MM:SS) or date string.
 */
export function formatMonitorTime(epochMs: number | null | undefined): string {
  if (!epochMs || epochMs <= 0) return '--:--:--';
  return new Date(epochMs).toLocaleTimeString('vi-VN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatMonitorDateTime(epochMs: number | null | undefined): string {
  if (!epochMs || epochMs <= 0) return '--/--/---- --:--';
  const d = new Date(epochMs);
  return `${d.toLocaleDateString('vi-VN')} ${d.toLocaleTimeString('vi-VN', { hour12: false })}`;
}
