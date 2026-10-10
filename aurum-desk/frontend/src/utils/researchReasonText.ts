/**
 * frontend/src/utils/researchReasonText.ts
 * V13.4 Friendly Reason Translation for New Traders (Phần 57)
 */

export function getResearchReasonText(code?: string, _details?: any): string {
  if (!code || code === '-') {
    return 'Không có ghi chú thêm.';
  }

  const upper = code.toUpperCase();

  if (upper.includes('COOLDOWN')) {
    return 'Đang trong thời gian nghỉ theo quy tắc quản trị rủi ro sau lệnh trước (30 phút).';
  }

  if (upper.includes('CAP_3') || upper.includes('MAX_FILLS')) {
    return 'Đã đạt giới hạn tối đa 3 lệnh/ngày theo chính sách quản trị rủi ro.';
  }

  if (upper.includes('CONSECUTIVE') || upper.includes('2_SL')) {
    return 'Tạm dừng mở lệnh do chạm ngưỡng bảo vệ: 2 lệnh Stop Loss liên tiếp.';
  }

  if (upper.includes('DAILY_LOSS') || upper.includes('LOSS_BUDGET')) {
    return 'Tạm khóa giao dịch trong ngày do chạm hạn mức rủi ro ngày (-1.5%).';
  }

  if (upper.includes('WEEKEND')) {
    return 'Thị trường đóng cửa cuối tuần (Thứ Bảy / Chủ Nhật).';
  }

  if (upper.includes('DATA_MISSING') || upper.includes('INSUFFICIENT_BARS')) {
    return 'Dữ liệu nến lịch sử trong phiên chưa đầy đủ hoặc có khoảng trống dữ liệu.';
  }

  if (upper.includes('WARMUP')) {
    return 'Chưa đủ số nến quá khứ cần thiết để tính toán chỉ báo và cấu trúc xu hướng.';
  }

  if (upper.includes('NO_VALID_STRUCTURAL_TARGET') || upper.includes('NET_RR_TOO_LOW') || upper.includes('NO_VALID_PRICE_PLAN')) {
    return 'Chưa tìm được mục tiêu giá cấu trúc phù hợp đạt tỷ lệ Net R:R >= 2.0 sau khi trừ chi phí mô phỏng.';
  }

  if (upper.includes('OPEN_POSITION')) {
    return 'Đã có vị thế đang mở trong tài khoản nên không mở thêm lệnh mới.';
  }

  if (upper.includes('DEADLINE_NOT_YET_REACHED')) {
    return 'Chưa tới mốc thời gian xem xét lệnh dự phòng phiên Mỹ (14:30 NY).';
  }

  if (upper.includes('NO_DIRECTIONAL_BIAS')) {
    return 'Thị trường chưa xác nhận xu hướng rõ ràng trên khung H4/H1.';
  }

  if (upper.includes('FILLED') || upper.includes('TARGET_ACHIEVED')) {
    return 'Đã khớp lệnh thành công trong phiên.';
  }

  if (upper.includes('NEWS') || upper.includes('BLACKOUT')) {
    return 'Đang trong khung giờ hạn chế giao dịch do tin tức kinh tế biến động mạnh.';
  }

  return code;
}
