/**
 * Deterministic User Message Catalog for Aurum Desk V9.1.
 * Provides clear, beginner-friendly Vietnamese titles, summaries, explanations,
 * impacts, and next steps for all trading outcomes and error codes.
 */
import type { MessageSeverity, OutcomeCertainty, SemanticAction } from '../types/userMessage';

export interface CatalogTemplate {
  code: string;
  title: string;
  summary: string | ((params: Record<string, any>) => string);
  explanation: string;
  impact: string;
  next_steps: string;
  defaultSeverity: MessageSeverity;
  defaultCertainty: OutcomeCertainty;
  defaultAction?: SemanticAction;
}

export const USER_MESSAGE_CATALOG: Record<string, CatalogTemplate> = {
  // 1. INVARIANT & POSITION GUARDS
  ACTIVE_POSITION_EXISTS: {
    code: 'ACTIVE_POSITION_EXISTS',
    title: 'Bạn đang có một lệnh mở',
    summary: (p) =>
      p?.order_id
        ? `Hệ thống đang quản lý vị thế mở #${p.order_id}. Giới hạn tối đa 1 vị thế đồng thời để bảo vệ an toàn vốn.`
        : 'Hệ thống đang có 1 vị thế mở. Giới hạn tối đa 1 vị thế đồng thời để bảo vệ an toàn vốn.',
    explanation:
      'Aurum Desk tuân thủ quy tắc kỷ luật giao dịch nghiêm ngặt: chỉ duy trì tối đa 1 vị thế mở tại một thời điểm nhằm tập trung quản trị rủi ro và tránh rủi ro đòn bẩy kép.',
    impact: 'Lệnh mới chưa được đặt và vốn khả dụng của bạn chưa bị trích thêm ký quỹ.',
    next_steps:
      'Vui lòng theo dõi hoặc chờ vị thế hiện tại đóng trước khi tìm cơ hội mới. Không nên đóng vội vị thế đang chạy chỉ để vào lệnh mới.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_POSITION', label: 'Xem Vị Thế Đang Mở' },
  },

  ARMED_ORDER_EXISTS: {
    code: 'ARMED_ORDER_EXISTS',
    title: 'Bạn đã có một lệnh chờ khớp',
    summary: (p) =>
      p?.order_id
        ? `Hiện đã có lệnh #${p.order_id} đang ở trạng thái chờ khớp (ARMED). Hệ thống chỉ duy trì tối đa 1 lệnh chờ.`
        : 'Hiện đã có 1 lệnh đang ở trạng thái chờ khớp (ARMED). Hệ thống chỉ duy trì tối đa 1 lệnh chờ.',
    explanation:
      'Lệnh chờ đã lên nòng và đang chờ giá thị trường chạm điều kiện kích hoạt. Để tránh trùng lặp hoặc vi phạm kế hoạch, bạn không thể arm thêm lệnh khác.',
    impact: 'Lệnh mới chưa được đưa vào hàng đợi.',
    next_steps:
      'Xem lệnh chờ hiện tại trong danh sách hoặc hủy lệnh chờ nếu bạn thật sự muốn đổi kế hoạch giao dịch.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_PENDING_ORDER', label: 'Xem Lệnh Chờ' },
  },

  WAITING_STRUCTURE: {
    code: 'WAITING_STRUCTURE',
    title: 'Chưa đủ tín hiệu để đặt lệnh',
    summary:
      'Hệ thống đang theo dõi cấu trúc giá và chờ thị trường xác nhận các điều kiện SMC (quét thanh khoản hoặc tạo vùng mất cân bằng).',
    explanation:
      'Chiến lược giao dịch yêu cầu có bằng chứng xác nhận nến đóng hoặc kiểm tra lại vùng giá quan trọng trước khi cho phép vào lệnh.',
    impact: 'Chưa có rủi ro nào đối với tài khoản của bạn.',
    next_steps: 'Kiên nhẫn chờ nến đóng hoặc giá di chuyển vào vùng quan sát.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Làm Mới Tín Hiệu' },
  },

  STRATEGY_NOT_READY: {
    code: 'STRATEGY_NOT_READY',
    title: 'Kế hoạch chưa sẵn sàng',
    summary: 'Kế hoạch giao dịch này chưa đáp ứng đủ các tiêu chuẩn an toàn của chiến lược.',
    explanation:
      'Hệ thống lọc bỏ các tín hiệu yếu hoặc chưa hoàn tất chu kỳ hình thành. Điều này không có nghĩa phần mềm bị lỗi mà là một bộ lọc bảo vệ.',
    impact: 'Lệnh chưa được kích hoạt.',
    next_steps: 'Chờ kế hoạch hoàn thiện hoặc theo dõi các khung thời gian khác.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Làm Mới Danh Sách' },
  },

  SETUP_TERMINAL: {
    code: 'SETUP_TERMINAL',
    title: 'Kế hoạch này đã kết thúc',
    summary: (p) =>
      p?.state
        ? `Kế hoạch đã kết thúc với trạng thái: ${p.state}. Cấu trúc giá không còn giá trị phân tích.`
        : 'Kế hoạch đã hết hạn thời gian, đã bị hủy hoặc cấu trúc giá đã bị phá vỡ.',
    explanation:
      'Thị trường đã di chuyển vượt ra ngoài vùng giá dự kiến, làm cho kế hoạch ban đầu không còn cơ sở an toàn để giao dịch.',
    impact: 'Kế hoạch này đã đóng và không thể arm nữa.',
    next_steps: 'Làm mới danh sách để xem các kế hoạch giao dịch mới.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Xem Setup Mới' },
  },

  NEWS_BLACKOUT: {
    code: 'NEWS_BLACKOUT',
    title: 'Tạm dừng vào lệnh gần giờ có tin tức',
    summary: (p) =>
      p?.event_title
        ? `Tạm ngừng mở lệnh do tin tức: "${p.event_title}" (${p.impact || 'High Impact'}).`
        : 'Hệ thống đang tạm ngừng mở lệnh mới do có tin tức kinh tế quan trọng (High Impact) sắp hoặc vừa công bố.',
    explanation:
      'Khi tin tức USD mạnh công bố (như CPI, Non-Farm, FOMC), thị trường vàng thường biến động mạnh kèm trượt giá (slippage) và giãn spread. Tạm dừng vào lệnh là quy tắc bảo vệ vốn sống còn.',
    impact: 'Lệnh không được gửi đi để tránh trượt giá bất lợi.',
    next_steps: 'Xem lịch tin tức kinh tế để biết thời điểm cửa sổ an toàn mở lại.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_NEWS', label: 'Xem Lịch Tin Tức' },
  },

  INSUFFICIENT_RR: {
    code: 'INSUFFICIENT_RR',
    title: 'Lợi nhuận dự kiến chưa đủ so với rủi ro',
    summary: (p) => {
      const netRR = p?.net_rr != null ? `1:${Number(p.net_rr).toFixed(2)}` : 'chưa đạt';
      const minRR =
        p?.required_rr != null
          ? `1:${Number(p.required_rr).toFixed(2)}`
          : p?.min_net_rr != null
          ? `1:${Number(p.min_net_rr).toFixed(2)}`
          : '1:2.00';
      return `Tỷ lệ Lợi nhuận/Rủi ro ròng (${netRR}) sau khi trừ phí và trượt giá chưa đạt mức tối thiểu yêu cầu (${minRR}).`;
    },
    explanation:
      'Net R:R là tỷ lệ thực tế bạn nhận được sau khi đã tính toán phí sàn và trượt giá 2 chiều. Nếu tỷ lệ này quá thấp, xác suất tạo lợi nhuận dài hạn sẽ bị bào mòn bởi chi phí.',
    impact: 'Lệnh bị chặn để bảo vệ tài khoản khỏi các lệnh không có kỳ vọng toán học tốt.',
    next_steps:
      'Chờ điểm vào lệnh tốt hơn hoặc không nên cố kéo giãn Take Profit chỉ để hợp thức hóa con số.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Kiểm Tra Cài Đặt R:R' },
  },

  INVALID_PRICE_GEOMETRY: {
    code: 'INVALID_PRICE_GEOMETRY',
    title: 'Giá vào, cắt lỗ và chốt lời chưa hợp lệ',
    summary: (p) =>
      p?.direction === 'SHORT'
        ? 'Lệnh BÁN (SHORT) yêu cầu: Take Profit < Giá vào lệnh < Stop Loss.'
        : 'Lệnh MUA (LONG) yêu cầu: Stop Loss < Giá vào lệnh < Take Profit.',
    explanation:
      'Lệnh mua kỳ vọng giá tăng (chốt lời ở trên, cắt lỗ ở dưới). Lệnh bán kỳ vọng giá giảm (chốt lời ở dưới, cắt lỗ ở trên). Các mức giá không được trùng nhau.',
    impact: 'Lệnh bị từ chối để tránh khớp ngược chiều gây thiệt hại tức thì.',
    next_steps: 'Kiểm tra lại giá vào lệnh, điểm cắt lỗ và chốt lời trên thước đo biểu đồ.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Xem Lại Thước Đo' },
  },

  CALCULATOR_INVALID: {
    code: 'CALCULATOR_INVALID',
    title: 'Kế hoạch chưa đáp ứng điều kiện thực thi',
    summary: (p) =>
      p?.reason || 'Khối lượng tính toán hoặc thông số lệnh không đáp ứng quy chuẩn kỹ thuật của sàn giao dịch.',
    explanation:
      'Sàn giao dịch Bitget có các quy định về khối lượng tối thiểu (Min Qty), giá trị lệnh tối thiểu (Min Notional), và khoảng đệm an toàn giữa Stop Loss với giá thanh lý.',
    impact: 'Lệnh không được gửi đi.',
    next_steps: 'Xem chi tiết nguyên nhân cụ thể để điều chỉnh khối lượng hoặc mức rủi ro.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Xem Cài Đặt Rủi Ro' },
  },

  CALCULATOR_REJECTED: {
    code: 'CALCULATOR_REJECTED',
    title: 'Tính toán rủi ro bị từ chối',
    summary: (p) => p?.message || p?.reason || 'Thông số rủi ro hoặc khối lượng lệnh không hợp lệ.',
    explanation:
      'Hệ thống tính toán rủi ro tự động từ chối các lệnh có nguy cơ vi phạm quản trị vốn hoặc các quy chuẩn của sàn.',
    impact: 'Lệnh không được đưa vào hàng đợi.',
    next_steps: 'Kiểm tra lại cài đặt rủi ro, đòn bẩy và số dư ký quỹ.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Mở Cài Đặt Rủi Ro' },
  },

  MAX_DAILY_ENTRIES: {
    code: 'MAX_DAILY_ENTRIES',
    title: 'Đã đạt số lệnh tối đa hôm nay',
    summary: (p) => {
      const fills = p?.fills_count ?? p?.current_fills ?? 3;
      const maxFills = p?.max_daily_fills ?? 3;
      return `Bạn đã khớp đủ hạn mức ${fills}/${maxFills} lệnh trong ngày (tính theo ngày Việt Nam).`;
    },
    explanation:
      'Quy tắc giới hạn số lệnh trong ngày giúp người giao dịch tránh hội chứng giao dịch quá mức (Overtrading) sau các lệnh thắng hoặc thua.',
    impact: 'Hệ thống tạm ngừng mở lệnh mới cho tới 00:00 ngày hôm sau.',
    next_steps: 'Nghỉ ngơi, xem lại nhật ký giao dịch và chuẩn bị cho ngày tiếp theo.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Xem Nhật Ký Hôm Nay' },
  },

  MAX_CONSECUTIVE_LOSSES: {
    code: 'MAX_CONSECUTIVE_LOSSES',
    title: 'App đã tạm dừng sau chuỗi lệnh thua',
    summary: (p) => {
      const count = p?.consecutive_losses ?? 2;
      return `Hệ thống tự động kích hoạt chế độ bảo vệ sau khi chạm giới hạn ${count} lệnh lỗ liên tiếp.`;
    },
    explanation:
      'Khi gặp chuỗi thua, tâm lý gỡ gạc (revenge trading) rất dễ xuất hiện. Hệ thống chủ động dừng để bảo vệ tài khoản của bạn khỏi việc suy giảm vốn nhanh chóng.',
    impact: 'Khóa quyền vào lệnh cho đến hết thời gian quy định.',
    next_steps: 'Bình tĩnh ghi chép lại bài học trong phần Nhật Ký Giao Dịch.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Mở Nhật Ký & Bài Học' },
  },

  DAY_BLOCKED: {
    code: 'DAY_BLOCKED',
    title: 'Đang bảo vệ giới hạn lỗ trong ngày',
    summary: (p) =>
      p?.reason || 'Mức lỗ trong ngày đã chạm hoặc vượt quá ngưỡng cho phép (1.5% vốn).',
    explanation:
      'Chốt chặn lỗ theo ngày là chiếc phao cứu sinh quan trọng nhất trong quản lý vốn, ngăn chặn tình trạng tài khoản bị cháy trong một ngày thị trường biến động xấu.',
    impact: 'Mọi hoạt động vào lệnh mới bị khóa hoàn toàn cho tới ngày giao dịch tiếp theo.',
    next_steps: 'Đóng máy và không cố gắng gỡ gạc.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Xem Báo Cáo Ngày' },
  },

  DAILY_LOSS_LIMIT: {
    code: 'DAILY_LOSS_LIMIT',
    title: 'Đã chạm giới hạn lỗ trong ngày',
    summary: 'Tổng số tiền lỗ trong ngày hôm nay đã chạm ngưỡng rủi ro tối đa cho phép.',
    explanation:
      'Để bảo toàn vốn cho các ngày giao dịch sau, hệ thống ngắt toàn bộ quyền vào lệnh mới.',
    impact: 'Tài khoản không mở thêm vị thế.',
    next_steps: 'Chờ phiên ngày mới bắt đầu.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Xem Nhật Ký' },
  },

  COOLDOWN_ACTIVE: {
    code: 'COOLDOWN_ACTIVE',
    title: 'Đang trong thời gian nghỉ giãn cách',
    summary: (p) =>
      p?.remaining_sec != null
        ? `Hệ thống đang áp dụng thời gian nghỉ giữa các lệnh (còn lại ${p.remaining_sec} giây).`
        : 'Hệ thống đang áp dụng thời gian nghỉ giữa các lệnh để thị trường ổn định.',
    explanation:
      'Sau một lệnh đóng, hệ thống dành một khoảng thời gian nghỉ để tránh vào lại lệnh vội vàng khi xu hướng chưa rõ ràng.',
    impact: 'Tạm thời không thể Arm hoặc mở lệnh mới.',
    next_steps: 'Đồng hồ đếm ngược đang chạy, bạn có thể vào lệnh khi thời gian nghỉ kết thúc.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Kiểm Tra Lại' },
  },

  OUTSIDE_ENTRY_WINDOW: {
    code: 'OUTSIDE_ENTRY_WINDOW',
    title: 'Chưa đến khung giờ được phép vào lệnh',
    summary: (p) =>
      p?.window
        ? `Hiện tại nằm ngoài khung giờ giao dịch đã cấu hình (${p.window}).`
        : 'Thời điểm hiện tại nằm ngoài khung giờ giao dịch đã cấu hình (Phiên Mỹ: 08:00 - 11:00 NY).',
    explanation:
      'Chiến lược tập trung vào phiên New York - thời điểm thị trường có khối lượng giao dịch lớn nhất và biên độ di chuyển rõ ràng nhất.',
    impact: 'Chưa thể kích hoạt lệnh.',
    next_steps: 'Xem thanh trạng thái Phiên Mỹ ở đầu trang để biết thời gian đếm ngược tới phiên giao dịch.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Xem Giờ Phiên Mỹ' },
  },

  NY_SLOT_RESERVED: {
    code: 'NY_SLOT_RESERVED',
    title: 'Đang giữ lượt giao dịch cho phiên Mỹ',
    summary: (p) => {
      const reserved = p?.reserved_slots ?? 1;
      return `Số lượt giao dịch còn lại (${reserved} lượt) được ưu tiên dành riêng cho khung giờ trọng điểm của phiên Mỹ.`;
    },
    explanation:
      'Chính sách phân bổ lượt giao dịch đảm bảo bạn luôn còn quota để bắt những đợt sóng tốt nhất trong phiên Mỹ thay vì dùng hết vào các phiên biến động chậm.',
    impact: 'Lệnh ngoài phiên bị giữ lại.',
    next_steps: 'Chờ phiên Mỹ bắt đầu lúc 08:00 (giờ New York).',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Xem Thời Gian Phiên' },
  },

  CROSS_MARGIN_UNSUPPORTED: {
    code: 'CROSS_MARGIN_UNSUPPORTED',
    title: 'Chế độ Cross chưa hỗ trợ thực thi PAPER',
    summary: 'Hệ thống mô phỏng Paper Trading hiện tại yêu cầu chế độ ký quỹ Cô lập (ISOLATED Margin).',
    explanation:
      'Ký quỹ Cô lập (Isolated) giúp giới hạn tối đa rủi ro chỉ trong phạm vi số tiền ký quỹ của chính lệnh đó, bảo vệ phần vốn còn lại trong tài khoản.',
    impact: 'Không thể mở lệnh ở chế độ Cross.',
    next_steps: 'Vui lòng chọn chế độ ISOLATED trong cài đặt hoặc trên thanh công cụ.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Đổi Sang ISOLATED' },
  },

  TICKER_STALE: {
    code: 'TICKER_STALE',
    title: 'Giá nhận được từ thị trường đã cũ',
    summary: (p) =>
      p?.age_sec != null
        ? `Báo giá thị trường nhận được chậm hơn ${p.age_sec}s so với thời gian thực (ngưỡng an toàn là 15s).`
        : 'Báo giá thị trường nhận được chậm hơn 15 giây so với thời gian thực.',
    explanation: 'Giao dịch với dữ liệu giá bị trễ có thể dẫn đến việc khớp lệnh tại mức giá đã trôi xa so với thực tế.',
    impact: 'Tạm chặn vào lệnh mới để bảo vệ an toàn.',
    next_steps: 'Chờ kết nối nạp báo giá mới hoặc kiểm tra lại đường truyền mạng.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Kiểm Tra Kết Nối' },
  },

  BACKLOG_QUOTE_STALE: {
    code: 'BACKLOG_QUOTE_STALE',
    title: 'Báo giá hàng đợi bị trễ',
    summary: 'Báo giá trong hàng đợi xử lý quá cũ so với đồng hồ thị trường.',
    explanation: 'Hệ thống tự động loại trừ các báo giá lưu đệm cũ để đảm bảo tính thời gian thực.',
    impact: 'Bỏ qua báo giá trễ.',
    next_steps: 'Đợi báo giá mới từ kết nối WebSocket.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
  },

  FEED_DISCONNECTED: {
    code: 'FEED_DISCONNECTED',
    title: 'Chưa nhận được giá từ sàn giao dịch',
    summary: 'Mất luồng dữ liệu thời gian thực từ sàn Bitget.',
    explanation: 'Hệ thống đang tự động kích hoạt kênh dự phòng REST Fallback để kiểm tra lại dữ liệu.',
    impact: 'Các lệnh mới tạm dừng cho tới khi có dữ liệu tin cậy.',
    next_steps: 'Hệ thống sẽ tự động kết nối lại trong giây lát.',
    defaultSeverity: 'error',
    defaultCertainty: 'unknown',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Thử Lại' },
  },

  EXECUTION_FEED_DEGRADED: {
    code: 'EXECUTION_FEED_DEGRADED',
    title: 'Dữ liệu thực thi đang bị gián đoạn',
    summary: 'Hàng đợi xử lý dữ liệu thị trường bị đầy hoặc phát hiện khoảng trống dữ liệu (gap).',
    explanation:
      'Để tránh khớp sai lệnh trong giai đoạn mạng chập chờn, hệ thống tạm thời kích hoạt chốt an toàn.',
    impact: 'Tạm chặn các thao tác mở vị thế mới.',
    next_steps: 'Chờ hệ thống đồng bộ lại luồng nến và xác nhận tính toàn vẹn của dữ liệu.',
    defaultSeverity: 'warning',
    defaultCertainty: 'unknown',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Kiểm Tra Trạng Thái' },
  },

  EXECUTION_GAP_ACTIVE: {
    code: 'EXECUTION_GAP_ACTIVE',
    title: 'Đang xử lý khoảng trống dữ liệu',
    summary: 'Phát hiện gián đoạn trong chuỗi báo giá thực thi.',
    explanation: 'Hệ thống đang đối soát dữ liệu nến để xác định chính xác diễn biến thị trường.',
    impact: 'Tạm dừng khớp lệnh mới.',
    next_steps: 'Chờ hoàn tất đối soát.',
    defaultSeverity: 'warning',
    defaultCertainty: 'unknown',
  },

  MALFORMED_QUOTE: {
    code: 'MALFORMED_QUOTE',
    title: 'Dữ liệu giá không hợp lệ',
    summary: 'Giá nhận được từ sàn có cấu trúc bất thường hoặc thiếu dữ liệu.',
    explanation: 'Hệ thống tự động loại bỏ báo giá lỗi này để không làm sai lệch tính toán rủi ro.',
    impact: 'Bỏ qua báo giá lỗi.',
    next_steps: 'Chờ báo giá chuẩn tiếp theo.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
  },

  INVERTED_SPREAD: {
    code: 'INVERTED_SPREAD',
    title: 'Spread giá bị đảo ngược bất thường',
    summary: 'Giá Bid cao hơn giá Ask trong gói dữ liệu nhận được từ sàn.',
    explanation: 'Đây là hiện tượng dữ liệu lỗi tạm thời từ feed ngoài. Hệ thống bảo vệ tự động từ chối xử lý.',
    impact: 'Bỏ qua gói dữ liệu lỗi.',
    next_steps: 'Chờ báo giá ổn định tiếp theo.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
  },

  NON_FINITE_QUOTE: {
    code: 'NON_FINITE_QUOTE',
    title: 'Báo giá chứa giá trị không hợp lệ',
    summary: 'Báo giá nhận được có giá trị vô cực (Infinity) hoặc không phải số (NaN).',
    explanation: 'Hệ thống từ chối tính toán với các giá trị phi số học.',
    impact: 'Bỏ qua báo giá lỗi.',
    next_steps: 'Chờ báo giá hợp lệ.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
  },

  NEGATIVE_QUOTE: {
    code: 'NEGATIVE_QUOTE',
    title: 'Báo giá có giá trị âm hoặc bằng 0',
    summary: 'Giá thị trường nhận được nhỏ hơn hoặc bằng 0.',
    explanation: 'Hệ thống loại bỏ giá trị phi thực tế này.',
    impact: 'Bỏ qua báo giá lỗi.',
    next_steps: 'Chờ báo giá hợp lệ.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
  },

  STALE_EDIT: {
    code: 'STALE_EDIT',
    title: 'Dữ liệu đã thay đổi từ lúc bạn mở màn hình',
    summary: 'Kế hoạch hoặc cấu hình đã được cập nhật phiên bản mới ở máy chủ trong khi bạn đang thao tác.',
    explanation:
      'Cơ chế Khóa lạc quan (Optimistic Concurrency) phát hiện có sự thay đổi giữa bản nháp của bạn và dữ liệu thực tế nhằm tránh ghi đè nhầm.',
    impact: 'Thao tác trước đó chưa được ghi nhận.',
    next_steps: 'Hệ thống giữ lại bản nháp của bạn. Vui lòng bấm làm mới để xem bản cập nhật mới nhất và xác nhận lại.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Tải Lại Dữ Liệu Mới' },
  },

  SETUP_CONFLICT: {
    code: 'SETUP_CONFLICT',
    title: 'Xung đột phiên bản kế hoạch giao dịch',
    summary: (p) =>
      p?.message || 'Phiên bản hoặc trạng thái của kế hoạch đã thay đổi trên hệ thống.',
    explanation:
      'Kế hoạch đã chuyển sang trạng thái mới hoặc có người dùng khác vừa cập nhật.',
    impact: 'Lệnh không được arm với thông số cũ.',
    next_steps: 'Bấm làm mới để đồng bộ kế hoạch mới nhất.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Đồng Bộ Kế Hoạch' },
  },

  SETUP_CHANGED: {
    code: 'SETUP_CHANGED',
    title: 'Kế hoạch đã có sự thay đổi',
    summary: (p) =>
      p?.message || 'Chiều lệnh, phiên bản hoặc cấu trúc setup đã thay đổi so với thời điểm bạn chọn.',
    explanation:
      'Để bảo đảm an toàn, hệ thống ngăn chặn việc đặt lệnh khi thông số nền tảng đã bị thay đổi.',
    impact: 'Lệnh cũ bị từ chối.',
    next_steps: 'Xem lại kế hoạch cập nhật và quyết định lại.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Xem Kế Hoạch Mới' },
  },

  INSUFFICIENT_MARGIN: {
    code: 'INSUFFICIENT_MARGIN',
    title: 'Số dư ký quỹ không đủ',
    summary: (p) => {
      const req = p?.required_margin != null ? `$${Number(p.required_margin).toFixed(2)}` : 'mức yêu cầu';
      const cap = p?.available_capital != null ? `$${Number(p.available_capital).toFixed(2)}` : 'số dư';
      return `Số tiền ký quỹ yêu cầu (${req}) vượt quá vốn khả dụng hiện tại (${cap}).`;
    },
    explanation:
      'Mỗi lệnh đòi hỏi một khoản ký quỹ ban đầu (Initial Margin). Bạn không thể mở lệnh nếu số dư không đủ bảo đảm.',
    impact: 'Không thể mở lệnh.',
    next_steps: 'Giảm khối lượng giao dịch hoặc kiểm tra lại đòn bẩy trong phần Cài đặt.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Xem Cài Đặt Vốn' },
  },

  MIN_QTY_EXCEEDS_BUDGET: {
    code: 'MIN_QTY_EXCEEDS_BUDGET',
    title: 'Khối lượng tối thiểu vượt ngân sách rủi ro',
    summary: (p) =>
      p?.message || 'Khối lượng nhỏ nhất mà sàn cho phép đặt có số tiền rủi ro lớn hơn mức rủi ro bạn đã cấu hình.',
    explanation:
      'Sàn quy định khối lượng tối thiểu (ví dụ: 0.01 oz vàng). Nếu khoảng cách Stop Loss xa, số tiền lỗ khi chạm SL vẫn vượt qua mức rủi ro tối đa cho phép của bạn.',
    impact: 'Lệnh bị chặn để tránh rủi ro vượt mức cho phép.',
    next_steps: 'Thu hẹp khoảng cách Stop Loss hoặc tăng nhẹ ngân sách rủi ro nếu phù hợp.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Điều Chỉnh Rủi Ro' },
  },

  MIN_NOTIONAL_NOT_MET: {
    code: 'MIN_NOTIONAL_NOT_MET',
    title: 'Giá trị lệnh nhỏ hơn mức tối thiểu của sàn',
    summary: (p) =>
      p?.message || 'Tổng giá trị danh nghĩa của lệnh chưa đạt mức tối thiểu sàn giao dịch Bitget yêu cầu.',
    explanation: 'Sàn giao dịch Bitget yêu cầu giá trị mỗi lệnh phái sinh phải đạt mức tối thiểu (Notional Value).',
    impact: 'Lệnh không thể gửi tới hệ thống khớp lệnh.',
    next_steps: 'Tăng nhẹ khối lượng giao dịch để đạt mức quy định.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Xem Cài Đặt Khối Lượng' },
  },

  LIQUIDATION_BEFORE_SL: {
    code: 'LIQUIDATION_BEFORE_SL',
    title: 'Giá thanh lý ước tính chạm trước Stop Loss',
    summary: (p) => {
      const lp = p?.liquidation_price != null ? `$${Number(p.liquidation_price).toFixed(2)}` : 'Giá thanh lý';
      const sl = p?.stop_loss != null ? `$${Number(p.stop_loss).toFixed(2)}` : 'Stop Loss';
      return `Mức đòn bẩy hiện tại khiến giá thanh lý (${lp}) nằm trước hoặc bằng mức cắt lỗ ${sl}.`;
    },
    explanation:
      'Nếu giá thanh lý chạm trước Stop Loss, bạn sẽ bị mất toàn bộ số tiền ký quỹ kèm phí phạt thanh lý thay vì được cắt lỗ có kiểm soát.',
    impact: 'Lệnh bị chặn ngay lập tức để bảo vệ tài khoản khỏi rủi ro cháy vốn.',
    next_steps: 'Giảm đòn bẩy hoặc dời Stop Loss gần hơn về phía giá vào lệnh.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Giảm Đòn Bẩy' },
  },

  LIQUIDATION_BUFFER_TOO_TIGHT: {
    code: 'LIQUIDATION_BUFFER_TOO_TIGHT',
    title: 'Khoảng đệm an toàn giá thanh lý quá hẹp',
    summary: (p) =>
      p?.message || 'Khoảng cách giữa Stop Loss và Giá thanh lý nhỏ hơn mức đệm an toàn quy định.',
    explanation:
      'Thị trường biến động mạnh có thể gây trượt giá khiến lệnh bị thanh lý trước khi lệnh Stop Loss kịp xử lý.',
    impact: 'Lệnh bị chặn.',
    next_steps: 'Giảm đòn bẩy để nới rộng khoảng cách an toàn tới giá thanh lý.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Giảm Đòn Bẩy' },
  },

  UNAUTHORIZED: {
    code: 'UNAUTHORIZED',
    title: 'Xác thực không thành công',
    summary: 'Bot Token không hợp lệ hoặc thông tin xác thực bị từ chối.',
    explanation: 'Bot Telegram cần một token hợp lệ được cấp bởi BotFather.',
    impact: 'Không thể kết nối hoặc gửi tin nhắn.',
    next_steps: 'Kiểm tra và dán lại Bot Token chính xác trong cài đặt Telegram.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Cài Đặt Telegram' },
  },

  CHAT_NOT_FOUND: {
    code: 'CHAT_NOT_FOUND',
    title: 'Không tìm thấy cuộc trò chuyện Telegram',
    summary: 'Mã Chat ID không tồn tại hoặc bot chưa được kích hoạt trong cuộc trò chuyện đó.',
    explanation:
      'Bạn cần mở ứng dụng Telegram, tìm đến bot của bạn và bấm nút Start (hoặc gửi một tin nhắn cho bot) trước khi hệ thống có thể gửi tin.',
    impact: 'Tin nhắn không gửi được vào Telegram của bạn.',
    next_steps: 'Bấm Start bot trên Telegram, sau đó kiểm tra lại Chat ID.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Kiểm Tra Chat ID' },
  },

  FORBIDDEN: {
    code: 'FORBIDDEN',
    title: 'Bot bị chặn bởi người dùng',
    summary: 'Bot Telegram đã bị chặn hoặc bị xóa quyền trong kênh nhận tin.',
    explanation: 'Khi bot bị người dùng block, Telegram Bot API sẽ từ chối toàn bộ tin nhắn gửi đến.',
    impact: 'Không thể gửi thông báo.',
    next_steps: 'Mở ứng dụng Telegram, tìm bot và chọn "Unblock bot".',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_SETTINGS', label: 'Xem Cài Đặt Telegram' },
  },

  RATE_LIMIT: {
    code: 'RATE_LIMIT',
    title: 'Telegram yêu cầu chờ trước khi gửi tiếp',
    summary: (p) =>
      p?.retry_after != null
        ? `Telegram đang giới hạn tần suất. Hệ thống sẽ tự động thử lại sau ${p.retry_after} giây.`
        : 'Hệ thống đang tạm hoãn gửi tin để tuân thủ giới hạn tần suất của Telegram.',
    explanation:
      'Telegram Bot API có giới hạn số tin nhắn gửi trong 1 giây để chống spam. Hệ thống đã lên lịch gửi lại chính xác theo thời gian yêu cầu.',
    impact: 'Tin nhắn đang nằm trong hàng đợi an toàn.',
    next_steps: 'Không bấm gửi thử liên tục để tránh bị kéo dài thời gian phạt.',
    defaultSeverity: 'warning',
    defaultCertainty: 'pending',
    defaultAction: { type: 'OPEN_TELEGRAM_HISTORY', label: 'Xem Hàng Đợi' },
  },

  AMBIGUOUS: {
    code: 'AMBIGUOUS',
    title: 'Chưa xác nhận được kết quả gửi tin Telegram',
    summary: 'Đường truyền mạng bị gián đoạn sau khi gửi yêu cầu tới máy chủ Telegram.',
    explanation:
      'Yêu cầu đã được gửi đi nhưng kết nối mạng bị ngắt trước khi nhận được phản hồi. Có thể tin nhắn đã đến nơi.',
    impact: 'Trạng thái tin được đánh dấu là chưa chắc chắn để tránh gửi lặp tin nhắn.',
    next_steps: 'Kiểm tra ứng dụng Telegram trên điện thoại trước khi bấm gửi lại.',
    defaultSeverity: 'warning',
    defaultCertainty: 'unknown',
    defaultAction: { type: 'OPEN_TELEGRAM_HISTORY', label: 'Kiểm Tra Lịch Sử' },
  },

  NETWORK_ERROR: {
    code: 'NETWORK_ERROR',
    title: 'Mất kết nối với máy chủ nội bộ',
    summary: 'Không thể kết nối tới backend Aurum Desk. Vui lòng kiểm tra tiến trình server.',
    explanation:
      'Trình duyệt không nhận được phản hồi từ backend. Có thể server chưa khởi động hoặc cổng 8000 đang bị chặn.',
    impact: 'Dữ liệu chưa được lưu hoặc thao tác chưa được gửi đến máy chủ.',
    next_steps: 'Kiểm tra terminal chạy backend hoặc tải lại trang.',
    defaultSeverity: 'error',
    defaultCertainty: 'unknown',
    defaultAction: { type: 'RECONCILE_STATUS', label: 'Tải Lại Trang' },
  },

  TIMEOUT: {
    code: 'TIMEOUT',
    title: 'Chưa xác nhận được kết quả thao tác',
    summary:
      'Chưa xác nhận được kết quả thao tác. App đang kiểm tra trạng thái lệnh để tránh tạo hoặc đóng trùng.',
    explanation:
      'Máy chủ có thể đã ghi nhận lệnh nhưng mạng bị chậm khi trả lời. Để tránh đặt lệnh hoặc đóng lệnh 2 lần, hệ thống tạm thời đối soát trạng thái.',
    impact: 'Kết quả thao tác chưa được xác nhận chắc chắn.',
    next_steps: 'Kiểm tra danh sách vị thế hoặc làm mới trạng thái trước khi bấm lại.',
    defaultSeverity: 'warning',
    defaultCertainty: 'unknown',
    defaultAction: { type: 'RECONCILE_STATUS', label: 'Đối Soát Trạng Thái' },
  },

  UNKNOWN: {
    code: 'UNKNOWN',
    title: 'Chưa hoàn tất được thao tác này',
    summary: (p) => p?.message || 'Hệ thống ghi nhận phản hồi chưa mong đợi trong quá trình xử lý.',
    explanation:
      'Một thao tác vừa thực hiện gặp gián đoạn tạm thời. Hệ thống đã lưu lại mã lỗi kỹ thuật để kiểm tra.',
    impact: 'Trạng thái dữ liệu hiện tại được bảo toàn an toàn.',
    next_steps: 'Vui lòng bấm Làm Mới để đối soát trạng thái trước khi thử lại.',
    defaultSeverity: 'error',
    defaultCertainty: 'unknown',
    defaultAction: { type: 'RECONCILE_STATUS', label: 'Đối Soát Trạng Thái' },
  },

  TRADE_OPENED: {
    code: 'TRADE_OPENED',
    title: 'Lệnh đã khớp — Vị thế PAPER mở',
    summary: (p) => {
      const dir = p?.direction || 'LONG';
      const entry = p?.actual_entry != null ? `$${Number(p.actual_entry).toFixed(2)}` : '';
      return `Vị thế mô phỏng ${dir} XAUUSDT đã mở thành công ${entry ? `tại ${entry}` : ''}.`;
    },
    explanation:
      'Lệnh đã được khớp trên môi trường mô phỏng (PAPER TRADING) với đòn bẩy và chi phí thực tế. Tiền trong tài khoản là tiền ảo thử nghiệm, không phải giao dịch tiền thật trên Bitget.',
    impact: 'Vị thế đang chạy và được ExitMonitor giám sát liên tục.',
    next_steps: 'Theo dõi biến động giá trên biểu đồ hoặc xem chi tiết trong tab Vị Thế.',
    defaultSeverity: 'success',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'OPEN_POSITION', label: 'Xem Vị Thế Mở' },
  },

  TRADE_CLOSED_TP: {
    code: 'TRADE_CLOSED_TP',
    title: 'Lệnh đã đóng — Đạt Chốt Lời (Take Profit)',
    summary: (p) => {
      const pnl = p?.realized_pnl_net != null ? `${Number(p.realized_pnl_net) >= 0 ? '+' : ''}${Number(p.realized_pnl_net).toFixed(2)} USDT` : '';
      const r = p?.realized_r != null ? `(${Number(p.realized_r) >= 0 ? '+' : ''}${Number(p.realized_r).toFixed(2)}R)` : '';
      return `Vị thế đã chạm mục tiêu Take Profit thành công. Lãi ròng: ${pnl} ${r}.`.trim();
    },
    explanation:
      'Giá thị trường đã chạm ngưỡng Chốt Lời bạn đã đặt. Lợi nhuận ròng đã được cộng vào số dư tài khoản sau khi trừ phí giao dịch và trượt giá.',
    impact: 'Số dư tài khoản tăng lên; vị thế đã đóng hoàn toàn.',
    next_steps: 'Xem đánh giá lệnh và bài học kinh nghiệm trong tab Nhật Ký.',
    defaultSeverity: 'success',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Xem Nhật Ký Lệnh' },
  },

  TRADE_CLOSED_SL: {
    code: 'TRADE_CLOSED_SL',
    title: 'Lệnh đã đóng — Chạm Cắt Lỗ (Stop Loss)',
    summary: (p) => {
      const pnl = p?.realized_pnl_net != null ? `${Number(p.realized_pnl_net).toFixed(2)} USDT` : '';
      const r = p?.realized_r != null ? `(${Number(p.realized_r).toFixed(2)}R)` : '';
      return `Vị thế đã dừng tại mức cắt lỗ. Lỗ ròng: ${pnl} ${r}.`.trim();
    },
    explanation:
      'Thị trường đi ngược xu hướng và chạm mức cắt lỗ được thiết lập trước. Cắt lỗ kỷ luật là chìa khóa để bảo vệ vốn sống còn.',
    impact: 'Khoản lỗ ròng đã được hạch toán; vị thế đóng an toàn không bị trượt xa.',
    next_steps: 'Ghi lại nhận xét tâm lý và rút kinh nghiệm trong tab Nhật Ký Giao Dịch.',
    defaultSeverity: 'warning',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Ghi Nhận Bài Học' },
  },

  TRADE_CLOSED_MANUAL: {
    code: 'TRADE_CLOSED_MANUAL',
    title: 'Lệnh đã đóng chủ động theo giá thị trường',
    summary: (p) => {
      const pnl = p?.realized_pnl_net != null ? `${Number(p.realized_pnl_net) >= 0 ? '+' : ''}${Number(p.realized_pnl_net).toFixed(2)} USDT` : '';
      return `Bạn đã chủ động đóng vị thế theo giá thị trường. Kết quả ròng: ${pnl}.`.trim();
    },
    explanation:
      'Vị thế được đóng theo quyết định chủ quan của người dùng trước khi giá chạm TP hoặc SL.',
    impact: 'Vị thế đã đóng và hạn ngạch vị thế mở được giải phóng.',
    next_steps: 'Ghi chú lý do đóng lệnh tay vào nhật ký để theo dõi kỷ luật.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Xem Nhật Ký' },
  },

  TRADE_LIQUIDATED: {
    code: 'TRADE_LIQUIDATED',
    title: 'Cảnh báo: Vị thế chạm giá thanh lý',
    summary: (p) => {
      const price = p?.exit_price != null ? `$${Number(p.exit_price).toFixed(2)}` : '';
      return `Vị thế đã chạm giá thanh lý Isolated ${price ? `tại ${price}` : ''}.`;
    },
    explanation:
      'Giá thị trường biến động chạm ngưỡng thanh lý của chế độ Isolated Margin do đòn bẩy cao hoặc khoảng cắt lỗ chưa đủ rộng.',
    impact: 'Khoản ký quỹ của vị thế bị mất.',
    next_steps: 'Xem lại bài học và giảm đòn bẩy trong các lệnh sau.',
    defaultSeverity: 'error',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_TRADE', label: 'Xem Chi Tiết Bài Học' },
  },

  ORDER_ARMED: {
    code: 'ORDER_ARMED',
    title: 'Đã đặt lệnh chờ khớp',
    summary: (p) => {
      const dir = p?.direction || 'LONG';
      const entry = p?.planned_entry != null ? `$${Number(p.planned_entry).toFixed(2)}` : '';
      return `Lệnh ${dir} đang chờ giá chạm ${entry} để khớp. Bạn chưa có vị thế mở từ lệnh này.`;
    },
    explanation:
      'Lệnh chờ đã nằm trong hàng đợi của ExecutionCoordinator. Giá Last trên biểu đồ chưa đủ để khớp; hệ thống cần giá khớp (Bid/Ask) thực tế chạm điều kiện thực thi.',
    impact: 'Hệ thống giữ lệnh ở trạng thái chờ và sẵn sàng khớp khi có giá phù hợp.',
    next_steps: 'Kiên nhẫn chờ giá khớp hoặc hủy lệnh nếu kế hoạch thay đổi.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'VIEW_PENDING_ORDER', label: 'Xem Lệnh Chờ' },
  },

  SETUP_READY: {
    code: 'SETUP_READY',
    title: 'Có kế hoạch giao dịch đủ điều kiện',
    summary: (p) => {
      const dir = p?.direction || 'LONG';
      return `Kế hoạch ${dir} XAUUSDT đã đạt đầy đủ các tiêu chí SMC. Bạn chưa có vị thế mới.`;
    },
    explanation:
      'Chiến lược nhận diện vùng POI/FVG hợp lệ và cấu trúc giá đã được xác nhận. Bạn chưa có vị thế mở mới. Hãy kiểm tra các điều kiện an toàn và bấm Arm nếu đồng ý.',
    impact: 'Kế hoạch đã sẵn sàng để người dùng quyết định đưa vào hàng đợi khớp.',
    next_steps: 'Kiểm tra các thông số R:R, khối lượng và bấm Arm nếu muốn giao dịch.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
    defaultAction: { type: 'REFRESH_SETUP', label: 'Xem Kế Hoạch' },
  },

  ORDER_CANCELLED: {
    code: 'ORDER_CANCELLED',
    title: 'Đã hủy lệnh chờ an toàn',
    summary: 'Lệnh chờ đã được hủy khỏi hàng đợi. Bạn chưa có vị thế mở nào từ lệnh này.',
    explanation:
      'Hủy lệnh chờ không ảnh hưởng tới số dư tài khoản và không phát sinh phí giao dịch.',
    impact: 'Hàng ngạch lệnh chờ được giải phóng.',
    next_steps: 'Tìm kiếm cơ hội mới trong danh sách Upcoming Setups.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
  },

  ORDER_EXPIRED: {
    code: 'ORDER_EXPIRED',
    title: 'Lệnh chờ đã hết hạn hiệu lực',
    summary: 'Lệnh chờ đã tự động hủy do thị trường không chạm giá trong khung thời gian quy định.',
    explanation:
      'Lệnh chờ có thời hạn hiệu lực tối đa để tránh trường hợp khớp lệnh khi bối cảnh thị trường ban đầu đã thay đổi.',
    impact: 'Lệnh chờ bị hủy an toàn, không có vị thế mở.',
    next_steps: 'Xem danh sách kế hoạch mới để cập nhật phân tích.',
    defaultSeverity: 'info',
    defaultCertainty: 'confirmed',
  },

  FEED_DOWN: {
    code: 'FEED_DOWN',
    title: 'Mất kết nối dữ liệu giá thị trường',
    summary: 'Hệ thống tạm thời ngừng nhận dữ liệu nến từ sàn Bitget. Các thao tác vào lệnh mới tạm thời bị khóa.',
    explanation:
      'Để bảo đảm an toàn, hệ thống không cho phép mở lệnh mới khi không thể xác minh độ tươi của giá.',
    impact: 'Tạm ngưng mở lệnh mới.',
    next_steps: 'Hệ thống đang tự động kết nối lại.',
    defaultSeverity: 'warning',
    defaultCertainty: 'unknown',
  },

  FEED_RECOVERED: {
    code: 'FEED_RECOVERED',
    title: 'Dữ liệu giá thị trường đã phục hồi',
    summary: 'Kết nối dữ liệu nến thời gian thực đã ổn định trở lại.',
    explanation: 'Luồng dữ liệu giá đã được xác thực an toàn và hệ thống đã mở lại đầy đủ tính năng.',
    impact: 'Các chức năng giao dịch hoạt động bình thường.',
    next_steps: 'Bạn có thể tiếp tục theo dõi và giao dịch.',
    defaultSeverity: 'success',
    defaultCertainty: 'confirmed',
  },
};

/**
 * Returns a template for a code, falling back to a normalized pattern or UNKNOWN.
 */
export function getCatalogTemplate(code: string): CatalogTemplate {
  const normalized = (code || '').toUpperCase().trim();
  if (USER_MESSAGE_CATALOG[normalized]) {
    return USER_MESSAGE_CATALOG[normalized];
  }

  // Prefix matching for compound codes
  for (const key of Object.keys(USER_MESSAGE_CATALOG)) {
    if (normalized.startsWith(key)) {
      return USER_MESSAGE_CATALOG[key];
    }
  }

  return USER_MESSAGE_CATALOG.UNKNOWN;
}
