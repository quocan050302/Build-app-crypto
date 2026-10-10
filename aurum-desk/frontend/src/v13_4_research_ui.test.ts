import { describe, it, expect } from 'vitest';
import { getResearchReasonText } from './utils/researchReasonText';
import { deriveResearchVerdict } from './utils/deriveResearchVerdict';
import type { ReplayRunResponse } from './api/client';

describe('V13.4 Frontend Research UI & Utilities (Phần 86)', () => {
  it('translates technical blocker codes into friendly explanations', () => {
    expect(getResearchReasonText('COOLDOWN_ACTIVE')).toContain('thời gian nghỉ');
    expect(getResearchReasonText('NO_VALID_STRUCTURAL_TARGET')).toContain('mục tiêu giá cấu trúc phù hợp');
    expect(getResearchReasonText('RISK_DAILY_CAP_3')).toContain('tối đa 3 lệnh/ngày');
    expect(getResearchReasonText('WEEKEND_MARKET_CLOSED')).toContain('Thứ Bảy / Chủ Nhật');
    expect(getResearchReasonText('DATA_MISSING')).toContain('Dữ liệu nến lịch sử');
  });

  it('formats realized_r correctly including 0R, positive, negative, and OPEN', () => {
    const formatR = (r: number | null | undefined, status: string) => {
      if (r != null && Number.isFinite(r)) {
        return `${r >= 0 ? '+' : ''}${r.toFixed(2)}R`;
      }
      return status === 'OPEN' ? 'Đang mở' : 'Chưa đủ dữ liệu';
    };

    expect(formatR(0.0, 'CLOSED')).toBe('+0.00R');
    expect(formatR(2.15, 'CLOSED')).toBe('+2.15R');
    expect(formatR(-1.0, 'CLOSED')).toBe('-1.00R');
    expect(formatR(null, 'OPEN')).toBe('Đang mở');
    expect(formatR(undefined, 'CLOSED')).toBe('Chưa đủ dữ liệu');
  });

  it('deriveResearchVerdict produces accurate technical, cadence, and economic statuses', () => {
    // 1. Integrity Failure
    const resIntegrityFail: Partial<ReplayRunResponse> = {
      total_trades: 10,
      total_net_pnl: 50,
      integrity_summary: { status: 'FAIL', causal_data_ok: false, guards_active: true }
    };
    const v1 = deriveResearchVerdict(resIntegrityFail as ReplayRunResponse, 90);
    expect(v1?.technicalStatus).toBe('FAIL');
    expect(v1?.badge).toContain('KIỂM TOÁN LỖI');

    // 2. High Drawdown with Positive PnL
    const resHighDd: Partial<ReplayRunResponse> = {
      total_trades: 20,
      closed_count: 20,
      total_net_pnl: 15.0,
      max_drawdown_pct: 15.5, // > 12%
      total_fees: 5.0,
      integrity_summary: { status: 'PASS', causal_data_ok: true, guards_active: true }
    };
    const v2 = deriveResearchVerdict(resHighDd as ReplayRunResponse, 90);
    expect(v2?.badge).toContain('GIẢM VỐN VƯỢT GIỚI HẠN');
    expect(v2?.economicStatus).toBe('PROFITABLE');

    // 3. Profitable and controlled
    const resControlled: Partial<ReplayRunResponse> = {
      total_trades: 25,
      closed_count: 25,
      total_net_pnl: 45.0,
      max_drawdown_pct: 3.5,
      total_fees: 12.0,
      integrity_summary: { status: 'PASS', causal_data_ok: true, guards_active: true }
    };
    const v3 = deriveResearchVerdict(resControlled as ReplayRunResponse, 90);
    expect(v3?.badge).toContain('PHƯƠNG PHÁP KHẢ QUAN');
    expect(v3?.economicStatus).toBe('PROFITABLE');
  });
});
