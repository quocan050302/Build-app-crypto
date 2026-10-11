import type { ReplayRunResponse } from '../api/client';

export interface ComparisonRow {
  label: string;
  baselineVal: string;
  candidateVal: string;
  evidence: string;
  baselineHighlight?: string;
  candidateHighlight?: string;
}

export function getComparisonRows(
  baselineResult: ReplayRunResponse | null,
  candidateResult: ReplayRunResponse | null
): { rows: ComparisonRow[]; warning?: string } {
  if (!candidateResult) {
    return {
      rows: [],
      warning: 'Chưa có kết quả đối chứng. Hãy chạy đánh giá để hiển thị số liệu thực tế.'
    };
  }

  const bTrades = baselineResult?.total_trades ?? 0;
  const cTrades = candidateResult.total_trades ?? 0;
  const bWins = baselineResult?.wins ?? 0;
  const bLosses = baselineResult?.losses ?? 0;
  const cWins = candidateResult.wins ?? 0;
  const cLosses = candidateResult.losses ?? 0;

  const bPnl = baselineResult?.total_net_pnl ?? 0;
  const cPnl = candidateResult.total_net_pnl ?? 0;

  const bWr = baselineResult?.win_rate_pct ?? 0;
  const cWr = candidateResult.win_rate_pct ?? 0;

  const bDd = baselineResult?.max_drawdown_pct ?? 0;
  const cDd = candidateResult.max_drawdown_pct ?? 0;

  const bDays = baselineResult?.session_breakdown?.days_with_trades ?? 0;
  const cDays = candidateResult.session_breakdown?.days_with_trades ?? 0;
  const totalDays = candidateResult.session_breakdown?.days_total ?? 90;

  const rows: ComparisonRow[] = [
    {
      label: 'Tập mẫu / Biến thể',
      baselineVal: baselineResult ? (baselineResult.strategy_variant || 'CURRENT_BASELINE') : 'Chưa chạy baseline',
      candidateVal: `${candidateResult.strategy_variant || 'CANDIDATE'} (${candidateResult.effective_config?.entry_cadence || 'CONFIRMED_ONLY'})`,
      evidence: 'Biến thể nghiên cứu và cơ chế lập lịch khớp lệnh'
    },
    {
      label: 'Tổng lệnh khớp (Fills)',
      baselineVal: baselineResult ? `${bTrades} lệnh (${bWins}W / ${bLosses}L)` : 'N/A',
      candidateVal: `${cTrades} lệnh (${cWins}W / ${cLosses}L)`,
      evidence: cTrades > 0 ? `Candidate ghi nhận ${cTrades} lệnh qua toàn kỳ` : 'Chưa có lệnh khớp',
      candidateHighlight: cTrades > 0 ? 'text-emerald-400 font-bold' : 'text-gray-400'
    },
    {
      label: 'Tỷ lệ thắng (Win Rate)',
      baselineVal: baselineResult ? `${bWr.toFixed(1)}%` : 'N/A',
      candidateVal: `${cWr.toFixed(1)}%`,
      evidence: `Tỷ lệ thắng của các lệnh đã đóng`,
      candidateHighlight: cWr >= 35 ? 'text-emerald-400 font-bold' : 'text-amber-400'
    },
    {
      label: 'Net PnL sau toàn bộ phí',
      baselineVal: baselineResult ? `${bPnl >= 0 ? '+' : ''}$${bPnl.toFixed(2)} USD` : 'N/A',
      candidateVal: `${cPnl >= 0 ? '+' : ''}$${cPnl.toFixed(2)} USD`,
      evidence: 'Đã trừ phí Taker và trượt giá mô phỏng',
      candidateHighlight: cPnl >= 0 ? 'text-emerald-400 font-bold' : 'text-rose-400'
    },
    {
      label: 'Mức giảm vốn lớn nhất (MaxDD)',
      baselineVal: baselineResult ? `-${bDd.toFixed(2)}%` : 'N/A',
      candidateVal: `-${cDd.toFixed(2)}%`,
      evidence: cDd <= 12 ? 'MaxDD kiểm soát an toàn dưới 12%' : 'MaxDD vượt ngưỡng an toàn',
      candidateHighlight: cDd <= 12 ? 'text-amber-400' : 'text-rose-400'
    },
    {
      label: 'Số ngày có lệnh / Tổng số ngày',
      baselineVal: baselineResult ? `${bDays} ngày` : 'N/A',
      candidateVal: `${cDays} ngày / ${totalDays} ngày`,
      evidence: `Độ phân bổ ngày giao dịch trong kỳ kiểm tra`,
      candidateHighlight: cDays > 0 ? 'text-emerald-400 font-bold' : 'text-gray-400'
    }
  ];

  return { rows };
}
