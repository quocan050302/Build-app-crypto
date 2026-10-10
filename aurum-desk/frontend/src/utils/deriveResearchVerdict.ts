import type { ReplayRunResponse } from '../api/client';

export interface ResearchVerdict {
  badge: string;
  color: string;
  summary: string;
  improvements: Array<{
    issue: string;
    evidence: string;
    suggestion: string;
  }>;
  nextSteps: string;
}

export function deriveResearchVerdict(
  result: ReplayRunResponse | null,
  dateRangeDays: number = 90
): ResearchVerdict | null {
  if (!result) return null;

  const totalTrades = result.closed_count ?? result.total_trades ?? 0;
  const netPnl = result.total_net_pnl ?? 0;
  const maxDd = result.max_drawdown_pct ?? 0;
  const warnings = result.warnings || [];
  const rejections = result.rejection_reasons || {};
  const currentVariant = result.effective_config?.strategy_variant || result.strategy_variant || 'CURRENT_BASELINE';
  const entryCadence = result.effective_config?.entry_cadence || 'CONFIRMED_ONLY';

  // Priority 1: Data Integrity / Missing Data
  const hasDataMissing =
    warnings.some(w => w.includes('INCOMPLETE') || w.includes('UNAVAILABLE') || w.includes('DATA_MISSING')) ||
    Boolean(rejections['HISTORICAL_DATA_UNAVAILABLE']) ||
    Boolean(rejections['DATA_MISSING']) ||
    Boolean(rejections['INSUFFICIENT_DATA']);

  if (hasDataMissing) {
    return {
      badge: 'CHƯA THỂ ĐÁNH GIÁ (THIẾU DỮ LIỆU)',
      color: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
      summary: 'Dữ liệu nến lịch sử chưa đầy đủ hoặc bị gián đoạn trong giai đoạn đã chọn. Cần bổ sung dữ liệu trước khi nghiệm thu.',
      improvements: [
        {
          issue: 'Thiếu dữ liệu nến lịch sử',
          evidence: warnings.join('; ') || 'Nến lịch sử không đầy đủ trong cache hệ thống',
          suggestion: 'Tải bổ sung nến từ sàn hoặc chọn giai đoạn đã có nến hoàn chỉnh.'
        }
      ],
      nextSteps: 'Tải bổ sung dữ liệu nến sàn Bitget để kiểm tra lại.'
    };
  }

  // Priority 2: Zero Closed Trades
  if (totalTrades === 0) {
    return {
      badge: 'CHƯA CÓ LỆNH KHỚP (0 TRADES)',
      color: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
      summary: 'Không có lệnh nào được khớp hoặc đóng trong giai đoạn đã chọn.',
      improvements: [
        {
          issue: 'Không phát sinh lệnh giao dịch',
          evidence: `0 lệnh khớp trong ${dateRangeDays} ngày.`,
          suggestion: currentVariant === 'CURRENT_BASELINE'
            ? 'Bộ lọc baseline quá khắt khe. Hãy thử biến thể NY_ADAPTIVE hoặc bật chế độ Nghiên Cứu Lệnh Phiên Mỗi Ngày (DAILY_PAPER).'
            : 'Kiểm tra bảng phiên không fill để nắm rõ các rào cản kỹ thuật hoặc quản trị rủi ro.'
        }
      ],
      nextSteps: 'Mở rộng điều kiện quét hoặc kiểm tra bảng phân tích phiên không fill.'
    };
  }

  // Priority 3: Cadence Unfulfilled in Daily Paper Mode
  if (entryCadence === 'DAILY_PAPER' && result.cadence_summary) {
    const cs = result.cadence_summary;
    if (cs.executable_sessions > 0 && cs.coverage_pct < 60) {
      return {
        badge: 'CHƯA ĐẠT HẠN NGẠCH PHIÊN (DAILY UNMET)',
        color: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
        summary: `Tỷ lệ đạt mục tiêu phiên chỉ đạt ${cs.coverage_pct}% (${cs.sessions_with_fills}/${cs.executable_sessions} phiên đủ điều kiện).`,
        improvements: [
          {
            issue: 'Tần suất khớp lệnh phiên chưa đạt',
            evidence: `${cs.unmet_sessions} phiên không thể xây dựng cấu trúc lệnh đạt Net R:R >= 2.0R hoặc bị chặn rủi ro.`,
            suggestion: 'Rà soát lại quy tắc xác định swing levels và khoảng cách SL/TP tại mốc deadline.'
          }
        ],
        nextSteps: 'Xem chi tiết các phiên unfulfilled trong bảng kiểm toán để tối ưu kế hoạch giá.'
      };
    }
  }

  // Priority 4: Sample Too Small (< 5 trades)
  if (totalTrades < 5) {
    let variantSuggestion = 'Mở rộng khung thời gian kiểm tra để có thêm mẫu quan sát.';
    if (currentVariant === 'CURRENT_BASELINE') {
      variantSuggestion = 'Chuyển sang biến thể SMC Phiên Mỹ (NY_ADAPTIVE) để quét thêm mẫu hình tiếp diễn B1 và breakout B2.';
    } else if (currentVariant === 'NY_ADAPTIVE') {
      variantSuggestion = 'Kích hoạt chế độ Nghiên Cứu Lệnh Phiên Mỗi Ngày (DAILY_PAPER) để lập lịch vào lệnh paper tại mốc deadline phiên Mỹ.';
    } else {
      variantSuggestion = 'Kiểm tra các rào cản quản trị rủi ro (loss budget, cooldown) có thể đang thu hẹp số cơ hội.';
    }

    return {
      badge: 'CHƯA ĐỦ SỐ LỆNH (MẪU QUÁ NHỎ)',
      color: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
      summary: `Chỉ ghi nhận ${totalTrades} lệnh trong giai đoạn chọn. Mẫu quá nhỏ để đưa ra kết luận thống kê tin cậy.`,
      improvements: [
        {
          issue: 'Số lượng cơ hội khớp lệnh thấp',
          evidence: `Chỉ có ${totalTrades} lệnh khớp qua khoảng ${dateRangeDays} ngày.`,
          suggestion: variantSuggestion
        }
      ],
      nextSteps: 'Chuyển sang chế độ DAILY_PAPER hoặc mở rộng tập mẫu để có thống kê tin cậy.'
    };
  }

  // Priority 5: Economic Verdict (>= 5 trades)
  const isProfitable = netPnl > 0;
  const isDdControlled = maxDd <= 12;

  if (isProfitable && isDdControlled) {
    return {
      badge: 'PHƯƠNG PHÁP KHẢ QUAN (CẦN THEO DÕI THÊM)',
      color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
      summary: `Lợi nhuận dương (+$${netPnl.toFixed(2)} USD) sau toàn bộ chi phí sàn, mức giảm vốn tối đa ${maxDd.toFixed(2)}% nằm trong ngưỡng kiểm soát an toàn.`,
      improvements: [
        {
          issue: 'Cần duy trì kỷ luật quản trị vốn',
          evidence: `Max drawdown ghi nhận ${maxDd.toFixed(2)}% (ngưỡng tối đa 12%).`,
          suggestion: 'Tiếp tục duy trì quy tắc dừng sau 2 lệnh SL liên tiếp và ngân sách rủi ro -1.5% vốn/ngày.'
        }
      ],
      nextSteps: 'Tiếp tục theo dõi forward test trên môi trường paper trước khi kích hoạt giao dịch thật.'
    };
  } else {
    return {
      badge: 'CHƯA ĐẠT HIỆU QUẢ KINH TẾ',
      color: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
      summary: `Phương pháp ghi nhận lỗ ròng (-$${Math.abs(netPnl).toFixed(2)} USD) hoặc mức sụt giảm vốn lớn (${maxDd.toFixed(2)}%).`,
      improvements: [
        {
          issue: 'Hiệu suất giao dịch chưa đạt kỳ vọng',
          evidence: `Net PnL: $${netPnl.toFixed(2)} | MaxDD: ${maxDd.toFixed(2)}%`,
          suggestion: 'Nghiên cứu lại điều kiện vào lệnh, tăng khoảng cách lọc thanh khoản và giảm thiểu rủi ro cho mỗi lệnh.'
        }
      ],
      nextSteps: 'Điều chỉnh tiêu chuẩn setup và kiểm tra lại trên dữ liệu lịch sử.'
    };
  }
}
