import { describe, it, expect } from 'vitest';
import { buildResearchReplayRequest, computeRequestFingerprint } from './utils/researchReplayRequest';
import { deriveResearchVerdict } from './utils/deriveResearchVerdict';
import { getComparisonRows } from './utils/comparisonRows';
import type { ReplayRunResponse } from './api/client';

describe('V13.3 Frontend Utilities', () => {
  it('buildResearchReplayRequest properly builds canonical payload and caps', () => {
    const req = buildResearchReplayRequest({
      startDate: '2026-06-01',
      endDate: '2026-08-30',
      strategyVariant: 'NY_ADAPTIVE',
      entryCadence: 'DAILY_PAPER',
      initialCapital: 1000,
      leverage: 30,
      maxRiskPct: 0.5,
      nyMaxFills: 3,
      dailyMinFillsTarget: 1
    });

    expect(req.symbol).toBe('XAUUSDT');
    expect(req.entry_cadence).toBe('DAILY_PAPER');
    expect(req.strategy_variant).toBe('NY_ADAPTIVE');
    expect(req.ny_max_fills).toBe(3);
    expect(req.daily_min_fills_target).toBe(1);
    expect(req.include_5m).toBe(true);
    expect(req.use_5m_driver).toBe(true);

    const fp = computeRequestFingerprint(req);
    expect(fp).toContain('DAILY_PAPER');
    expect(fp).toContain('NY_ADAPTIVE');
  });

  it('deriveResearchVerdict prioritizes data missing and zero closed', () => {
    // 1. Data missing
    const resMissing: Partial<ReplayRunResponse> = {
      warnings: ['HISTORICAL_DATA_UNAVAILABLE: 15M candles incomplete'],
      total_trades: 0,
      total_net_pnl: 0,
      win_rate_pct: 0
    };
    const v1 = deriveResearchVerdict(resMissing as ReplayRunResponse, 90);
    expect(v1?.badge).toContain('THIẾU DỮ LIỆU');

    // 2. Zero closed
    const resZero: Partial<ReplayRunResponse> = {
      warnings: [],
      total_trades: 0,
      closed_count: 0,
      total_net_pnl: 0,
      win_rate_pct: 0,
      effective_config: { strategy_variant: 'CURRENT_BASELINE' }
    };
    const v2 = deriveResearchVerdict(resZero as ReplayRunResponse, 90);
    expect(v2?.badge).toContain('CHƯA CÓ LỆNH KHỚP');
    expect(v2?.improvements[0].suggestion).toContain('NY_ADAPTIVE');

    // 3. Small sample recommendation does not recommend variant already running
    const resSmallNY: Partial<ReplayRunResponse> = {
      warnings: [],
      total_trades: 2,
      closed_count: 2,
      total_net_pnl: 10,
      win_rate_pct: 50,
      effective_config: { strategy_variant: 'NY_ADAPTIVE' }
    };
    const v3 = deriveResearchVerdict(resSmallNY as ReplayRunResponse, 90);
    expect(v3?.badge).toContain('CHƯA ĐỦ SỐ LỆNH');
    expect(v3?.improvements[0].suggestion).toContain('DAILY_PAPER');
  });

  it('getComparisonRows returns dynamic rows without hardcoded fallbacks', () => {
    const candidate: Partial<ReplayRunResponse> = {
      strategy_variant: 'NY_ADAPTIVE',
      total_trades: 15,
      wins: 6,
      losses: 9,
      total_net_pnl: 32.50,
      win_rate_pct: 40.0,
      max_drawdown_pct: 4.2,
      session_breakdown: {
        days_total: 90,
        days_with_trades: 14
      },
      effective_config: {
        entry_cadence: 'DAILY_PAPER'
      }
    };

    const { rows } = getComparisonRows(null, candidate as ReplayRunResponse);
    expect(rows.length).toBe(6);

    const tradesRow = rows.find(r => r.label.includes('Tổng lệnh'));
    expect(tradesRow?.candidateVal).toBe('15 lệnh (6W / 9L)');

    const pnlRow = rows.find(r => r.label.includes('Net PnL'));
    expect(pnlRow?.candidateVal).toBe('+$32.50 USD');

    const wrRow = rows.find(r => r.label.includes('Tỷ lệ thắng'));
    expect(wrRow?.candidateVal).toBe('40.0%');

    const daysRow = rows.find(r => r.label.includes('Số ngày'));
    expect(daysRow?.candidateVal).toBe('14 ngày / 90 ngày');
  });
});
