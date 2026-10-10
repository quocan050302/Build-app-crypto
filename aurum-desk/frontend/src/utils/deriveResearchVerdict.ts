import type { ReplayRunResponse } from '../api/client';

export interface ResearchVerdict {
  badge: string;
  color: string;
  summary: string;
  technicalStatus: 'PASS' | 'FAIL' | 'NOT_CHECKED';
  cadenceStatus: 'PASS' | 'UNMET' | 'NOT_APPLICABLE';
  economicStatus: 'PROFITABLE' | 'LOSING' | 'BREAKEVEN' | 'INSUFFICIENT_EVIDENCE';
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
  const totalFees = result.total_fees ?? 0;
  const warnings = result.warnings || [];
  const rejections = result.rejection_reasons || {};
  const currentVariant = result.effective_config?.strategy_variant || result.strategy_variant || 'CURRENT_BASELINE';
  const entryCadence = result.effective_config?.entry_cadence || 'CONFIRMED_ONLY';
  const integrity = result.integrity_summary;

  // Priority 1: Data Integrity / Missing Data / Integrity Fail
  const hasDataMissing =
    warnings.some(w => w.includes('INCOMPLETE') || w.includes('UNAVAILABLE') || w.includes('DATA_MISSING')) ||
    Boolean(rejections['HISTORICAL_DATA_UNAVAILABLE']) ||
    Boolean(rejections['DATA_MISSING']) ||
    Boolean(rejections['INSUFFICIENT_DATA']);

  const isIntegrityFailed = integrity?.status === 'FAIL';

  if (hasDataMissing || isIntegrityFailed) {
    return {
      badge: 'CHƯA THỂ ĐÁNH GIÁ (THIẾU DỮ LIỆU HOẶC KIỂM TOÁN LỖI)',
      color: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
      summary: hasDataMissing
        ? 'Dữ liệu nến lịch sử chưa đầy đủ hoặc có khoảng trống. Cần bổ sung dữ liệu trước khi nghiệm thu.'
        : 'Phát hiện sai lệch kiểm toán dữ liệu hoặc thứ tự thời gian nến. Cần kiểm tra lại log kiểm toán.',
      technicalStatus: 'FAIL',
      cadenceStatus: 'NOT_APPLICABLE',
      economicStatus: 'INSUFFICIENT_EVIDENCE',
      improvements: [
        {
          issue: 'Dữ liệu chưa hoàn chỉnh hoặc kiểm toán không khớp',
          evidence: warnings.join('; ') || 'Kiểm toán tính đúng dữ liệu chưa đạt',
          suggestion: 'Tải lại dữ liệu nến từ sàn hoặc kiểm tra lại file nến lịch sử.'
        }
      ],
      nextSteps: 'Đảm bảo dữ liệu nến liên tục và đầy đủ trước khi thực hiện đánh giá.'
    };
  }

  // Priority 2: Zero Closed Trades
  if (totalTrades === 0) {
    return {
      badge: 'CHƯA CÓ LỆNH KHỚP (0 TRADES)',
      color: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
      summary: 'Không có lệnh nào được khớp hoặc đóng trong giai đoạn đã chọn.',
      technicalStatus: 'PASS',
      cadenceStatus: entryCadence === 'DAILY_PAPER' ? 'UNMET' : 'NOT_APPLICABLE',
      economicStatus: 'INSUFFICIENT_EVIDENCE',
      improvements: [
        {
          issue: 'Không phát sinh lệnh giao dịch',
          evidence: '0 lệnh khớp trong ' + dateRangeDays + ' ngày.',
          suggestion: currentVariant === 'CURRENT_BASELINE'
            ? 'Bộ lọc baseline quá khắt khe. Hãy thử biến thể NY_ADAPTIVE hoặc bật chế độ Nghiên Cứu Lệnh Phiên Mỗi Ngày (DAILY_PAPER).'
            : 'Kiểm tra bảng phiên chưa đạt để nắm rõ các rào cản kỹ thuật hoặc quản trị rủi ro.'
        }
      ],
      nextSteps: 'Mở rộng điều kiện quét hoặc kiểm tra bảng phân tích phiên chưa đạt.'
    };
  }

  // Priority 3: Cadence Unfulfilled in Daily Paper Mode
  let cadenceStatus: 'PASS' | 'UNMET' | 'NOT_APPLICABLE' = 'NOT_APPLICABLE';
  if (entryCadence === 'DAILY_PAPER' && result.cadence_summary) {
    const cs = result.cadence_summary;
    cadenceStatus = (cs.unmet_sessions === 0) ? 'PASS' : 'UNMET';
  }

  // Priority 4: Sample Too Small (< 5 trades)
  if (totalTrades < 5) {
    let variantSuggestion = 'Mở rộng khung thời gian kiểm tra để có thêm mẫu quan sát.';
    if (currentVariant === 'CURRENT_BASELINE') {
      variantSuggestion = 'Chuyển sang biến thể SMC Phiên Mỹ (NY_ADAPTIVE) để quét thêm mẫu hình tiếp diễn B1 và breakout B2.';
    } else if (currentVariant === 'NY_ADAPTIVE') {
      variantSuggestion = 'Kích hoạt chế độ Nghiên Cứu Lệnh Phiên Mỗi Ngày (DAILY_PAPER) để lập lịch vào lệnh paper tại mốc deadline phiên Mỹ.';
    }

    return {
      badge: 'CHƯA ĐỦ SỐ LỆNH (MẪU QUÁ NHỎ)',
      color: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
      summary: 'Chỉ ghi nhận ' + totalTrades + ' lệnh trong giai đoạn chọn. Mẫu quá nhỏ để đưa ra kết luận thống kê tin cậy.',
      technicalStatus: 'PASS',
      cadenceStatus,
      economicStatus: 'INSUFFICIENT_EVIDENCE',
      improvements: [
        {
          issue: 'Số lượng cơ hội khớp lệnh thấp',
          evidence: 'Chỉ có ' + totalTrades + ' lệnh khớp qua khoảng ' + dateRangeDays + ' ngày.',
          suggestion: variantSuggestion
        }
      ],
      nextSteps: 'Chuyển sang chế độ DAILY_PAPER hoặc mở rộng tập mẫu để có thống kê tin cậy.'
    };
  }

  // Priority 5: Economic Verdict (>= 5 trades)
  const isBreakeven = Math.abs(netPnl) < 0.01;
  const isProfitable = netPnl > 0.01;
  const isDdControlled = maxDd <= 12;

  // Build specific improvements based on real metrics
  const improvements: Array<{ issue: string; evidence: string; suggestion: string }> = [];

  // Check quota group drag
  const quotaPnl = result.quota_net_pnl ?? 0;
  const quotaTrades = result.quota_trades_count ?? 0;
  if (quotaTrades > 0 && quotaPnl < 0) {
    improvements.push({
      issue: 'Nhóm lệnh lập lịch theo bối cảnh (Scheduled Paper) đang bị lỗ ròng',
      evidence: 'Nhóm này ghi nhận ' + quotaTrades + ' lệnh với tổng PnL: -$' + Math.abs(quotaPnl).toFixed(2) + ' USD.',
      suggestion: 'Cần tinh chỉnh tiêu chí xác định hướng hoặc yêu cầu thêm tín hiệu nến 5M trước mốc deadline.'
    });
  }

  // Check fee impact
  if (totalFees > Math.abs(netPnl) && totalFees > 20) {
    improvements.push({
      issue: 'Chi phí giao dịch (phí sàn + trượt giá) chiếm tỷ trọng đáng kể',
      evidence: 'Tổng phí mô phỏng ghi nhận $' + totalFees.toFixed(2) + ' USD so với PnL ròng $' + netPnl.toFixed(2) + ' USD.',
      suggestion: 'Ưu tiên giữ lệnh đến TP đầy đủ và tránh vào lệnh tại các thời điểm spread giãn rộng.'
    });
  }

  if (improvements.length === 0) {
    improvements.push({
      issue: 'Quản trị rủi ro & Kỷ luật',
      evidence: 'Max drawdown ghi nhận ' + maxDd.toFixed(2) + '% (ngưỡng tối đa 12%).',
      suggestion: 'Tiếp tục duy trì quy tắc dừng sau 2 lệnh SL liên tiếp và ngân sách rủi ro -1.5% vốn/ngày.'
    });
  }

  if (isBreakeven) {
    return {
      badge: 'HÒA VỐN (NET PNL: 0.00 USD)',
      color: 'bg-slate-500/10 text-slate-300 border-slate-500/30',
      summary: 'Giao dịch đạt trạng thái hòa vốn sau khi tính toàn bộ phí sàn và trượt giá.',
      technicalStatus: 'PASS',
      cadenceStatus,
      economicStatus: 'BREAKEVEN',
      improvements,
      nextSteps: 'Tập trung tối ưu tỷ lệ R:R để nâng cao lợi nhuận kỳ vọng.'
    };
  }

  if (isProfitable && isDdControlled) {
    return {
      badge: 'PHƯƠNG PHÁP KHẢ QUAN (CẦN THEO DÕI THÊM)',
      color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
      summary: 'Lợi nhuận dương (+$' + netPnl.toFixed(2) + ' USD) sau toàn bộ chi phí sàn, mức giảm vốn tối đa ' + maxDd.toFixed(2) + '% nằm trong ngưỡng kiểm soát an toàn.',
      technicalStatus: 'PASS',
      cadenceStatus,
      economicStatus: 'PROFITABLE',
      improvements,
      nextSteps: 'Tiếp tục theo dõi forward test trên môi trường paper trước khi kích hoạt giao dịch thật.'
    };
  } else if (isProfitable && !isDdControlled) {
    return {
      badge: 'CÓ LỜI NHƯNG GIẢM VỐN VƯỢT GIỚI HẠN',
      color: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
      summary: 'Có lợi nhuận ròng (+$' + netPnl.toFixed(2) + ' USD), nhưng mức giảm vốn tối đa (' + maxDd.toFixed(2) + '%) đã vượt ngưỡng cho phép (12%).',
      technicalStatus: 'PASS',
      cadenceStatus,
      economicStatus: 'PROFITABLE',
      improvements,
      nextSteps: 'Hạ tỷ lệ rủi ro mỗi lệnh để đưa mức giảm vốn tối đa về dưới 12%.'
    };
  } else {
    return {
      badge: 'CHƯA ĐẠT HIỆU QUẢ KINH TẾ',
      color: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
      summary: 'Phương pháp ghi nhận lỗ ròng (-$' + Math.abs(netPnl).toFixed(2) + ' USD) với mức sụt giảm vốn ' + maxDd.toFixed(2) + '%.',
      technicalStatus: 'PASS',
      cadenceStatus,
      economicStatus: 'LOSING',
      improvements,
      nextSteps: 'Điều chỉnh tiêu chuẩn setup và kiểm tra lại trên dữ liệu lịch sử.'
    };
  }
}
