/**
 * CG-01 Reason & Stage Vietnamese Text Catalog (V16.29).
 * Single authoritative source of explanation for CG-01 diagnostic codes.
 * Conforms to prompt rules 071-086, 056-058.
 */

export const CG01_STAGE_TITLES: Record<string, string> = {
  MODEL_DISABLED: 'Mô Hình Đang Tắt',
  NO_QUOTE: 'Thiếu Báo Giá',
  WARMUP_INSUFFICIENT: 'Đang Nạp Dữ Liệu',
  ATR_WARMUP: 'Đang Nạp Dữ Liệu ATR',
  OBSERVING: 'Đang Quan Sát Thị Trường',
  WAIT_WINDOW: 'Chờ Khung Giờ Hoạt Động',
  WAIT_POI: 'Chờ Giá Tiếp Cận Vùng POI',
  WAIT_SWEEP: 'Chờ Quét Thanh Khoản',
  WAITING_SWEEP_CONFIRMATION: 'Chờ Quét Thanh Khoản',
  WAIT_MSS: 'Chờ Phá Cấu Trúc (MSS)',
  WAITING_MSS_CONFIRMATION: 'Chờ Phá Cấu Trúc (MSS)',
  WAIT_FVG: 'Chờ Tạo FVG',
  WAITING_FVG_FORMATION: 'Chờ Tạo FVG',
  WAIT_ENTRY: 'Chờ Giá Retest Vùng Entry',
  WAIT_RETEST: 'Chờ Giá Retest Vùng Entry',
  EXECUTABLE: 'Đủ Điều Kiện Kỹ Thuật',
  ARMED: 'Đã Đặt Lệnh Chờ',
  GUARD_BLOCKED: 'Bị Chặn Bởi Guard',
  DEGRADED: 'Hệ Thống Tạm Dừng',
  CYCLE_FAILED: 'Chu Kỳ Phân Tích Lỗi',
  EXPIRED: 'Hết Thời Gian Hiệu Lực',
  INVALIDATED: 'Kế Hoạch Bị Hủy',
};

export const CG01_REASON_DESCRIPTIONS: Record<string, string> = {
  // Sweep & MSS
  WAIT_SWEEP: 'Đang chờ giá quét mốc thanh khoản tại vùng đang theo dõi.',
  WAITING_SWEEP_CONFIRMATION: 'Đang chờ giá quét mốc thanh khoản tại vùng đang theo dõi.',
  WAIT_MSS: 'Đã có quét thanh khoản; đang chờ nến đóng xác nhận phá cấu trúc và lực đẩy.',
  WAITING_MSS_CONFIRMATION: 'Đã có quét thanh khoản; đang chờ nến đóng xác nhận phá cấu trúc và lực đẩy.',

  // FVG Lifecycle & Retest
  WAIT_FVG: 'Đã xác nhận cấu trúc; đang chờ FVG hợp lệ trên nhịp đẩy.',
  WAITING_FVG_FORMATION: 'Đã xác nhận cấu trúc; đang chờ FVG hợp lệ trên nhịp đẩy.',
  NO_FVG_ON_LEG: 'Nhịp đẩy hiện tại chưa tạo được FVG phù hợp.',
  FVG_USED_BEFORE_ARM: 'Vùng đã được chạm theo điều kiện entry trước lúc đặt chờ nên không còn dùng được cho setup này.',
  FVG_OVER_MITIGATED: 'Giá đã đi quá sâu vào vùng so với quy tắc đang cấu hình.',
  FVG_INVALIDATED: 'Vùng FVG đã bị vô hiệu hóa bởi diễn biến giá ngược chiều.',
  WAIT_RETEST: 'Đang chờ giá quay lại vùng và xác nhận theo entry mode.',
  WAITING_RETEST_ENTRY: 'Đang chờ giá quay lại vùng và xác nhận theo entry mode.',

  // Execution & Guards
  TRIGGER_EXPIRED: 'Setup đã quá thời hạn cho phép.',
  RR_BELOW_MINIMUM: 'Lợi nhuận dự kiến sau phí chưa đạt ngưỡng tối thiểu.',
  TARGET_UNAVAILABLE: 'Chưa xác định được mục tiêu chốt lời hợp lệ theo quy tắc hiện tại.',
  GEOMETRY_INVALID: 'Quan hệ entry/SL/TP chưa hợp lệ.',
  EXECUTABLE: 'Setup thỏa mãn đầy đủ điều kiện kỹ thuật của mô hình.',
  ARMED: 'Lệnh chờ đã được đặt thành công theo kế hoạch.',
  GUARD_BLOCKED: 'Lệnh bị chặn bởi cơ chế kiểm soát rủi ro hệ thống.',
  OTHER_MODEL_SELECTED: 'Hệ thống ưu tiên thực thi mô hình khác trong chu kỳ này.',

  // System & Feed Health
  MODEL_DISABLED: 'Mô hình CG-01 hiện đang tắt trong cấu hình.',
  NO_QUOTE: 'Thiếu báo giá hợp lệ từ sàn.',
  WARMUP_INSUFFICIENT: 'Đang thiếu dữ liệu nến cần thiết để tính toán chỉ báo và ATR.',
  ATR_WARMUP: 'Đang thiếu dữ liệu nến cần thiết để tính toán ATR và biên độ.',
  DEGRADED: 'Hệ thống tạm dừng tạo lệnh do lỗi ghi nhận dữ liệu; các lệnh đã có vẫn được bảo vệ.',
  CYCLE_FAILED: 'Chu kỳ phân tích gặp sự cố hệ thống, sẽ tự động thử lại ở chu kỳ tiếp theo.',
  TRANSACTION_COMMIT_FAILED: 'Giao dịch lưu dữ liệu không thành công; hệ thống đã phục hồi về trạng thái an toàn.',
};

/**
 * Returns user-facing title for a stage code.
 * Guaranteed never to return undefined, null, or raw object.
 */
export function getCG01StageTitle(stage: string | null | undefined): string {
  if (!stage || typeof stage !== 'string') {
    return 'Đang Theo Dõi';
  }
  return CG01_STAGE_TITLES[stage] || stage.replace(/_/g, ' ');
}

/**
 * Resolves user-friendly Vietnamese description for a machine reason code.
 * If backend summary contains raw technical constants, catalog takes precedence.
 * Guaranteed never to return undefined, null, or '[object Object]'.
 */
export function getCG01ReasonDescription(
  reasonCode: string | null | undefined,
  fallbackSummary?: string | null
): string {
  // If reason code is mapped in catalog, prefer catalog explanation
  if (reasonCode && typeof reasonCode === 'string' && CG01_REASON_DESCRIPTIONS[reasonCode]) {
    return CG01_REASON_DESCRIPTIONS[reasonCode];
  }

  // If backend provided a summary string, check whether it is friendly or contains raw code
  if (fallbackSummary && typeof fallbackSummary === 'string' && fallbackSummary.trim().length > 0) {
    // If fallbackSummary is just an unmapped technical constant, wrap it cleanly
    if (/^[A-Z0-9_]+$/.test(fallbackSummary.trim())) {
      const mapped = CG01_REASON_DESCRIPTIONS[fallbackSummary.trim()];
      if (mapped) return mapped;
      return `Đang theo dõi điều kiện: ${fallbackSummary.trim()}`;
    }
    return fallbackSummary.trim();
  }

  // If reasonCode is a string without direct mapping
  if (reasonCode && typeof reasonCode === 'string' && reasonCode.trim().length > 0) {
    return `Đang theo dõi điều kiện: ${reasonCode.trim()}`;
  }

  return 'Đang theo dõi diễn biến giá và điều kiện thị trường.';
}
