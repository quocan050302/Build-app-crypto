/**
 * frontend/src/candidateScore.test.ts
 * V16.39.11 Candidate Score Display Unit & Component Tests
 *
 * Rules 168, 170:
 * - Pure normalize & format tests: null, 0, decimal, invalid, stale, frozen
 * - Final score 78 does not take base 83 as headline
 * - No '%' suffix on quality scores
 * - Rounding 69.9 does not masquerade as meeting threshold 70
 * - Explicit disclaimers (score != winrate)
 */

import { describe, it, expect } from 'vitest';
import {
  normalizeCandidateScore,
  formatCandidateScore,
  translateDecision,
  getSelectedScoreTarget,
} from './utils/candidateScore';

describe('V16.39.11 Candidate Score Normalization (Rules 102, 168)', () => {
  it('normalizes valid ARM snapshot assessment (78/100, base 83, delta -5, threshold 70)', () => {
    const raw = {
      status: 'AVAILABLE',
      score: 78.0,
      base_score: 83.0,
      lesson_delta: -5.0,
      threshold: 70.0,
      decision: 'ELIGIBLE',
      as_of_ms: 1700000000000,
      source: 'ARM_SNAPSHOT',
      frozen: true,
      assessment_id: 'assess-123',
      candidate_key: 'XAUUSDT:15M:LONG:2650.0',
    };

    const norm = normalizeCandidateScore(raw);
    expect(norm.status).toBe('AVAILABLE');
    expect(norm.score).toBe(78.0);
    expect(norm.base_score).toBe(83.0);
    expect(norm.lesson_delta).toBe(-5.0);
    expect(norm.threshold).toBe(70.0);
    expect(norm.decision).toBe('ELIGIBLE');
    expect(norm.frozen).toBe(true);
    expect(norm.source).toBe('ARM_SNAPSHOT');
  });

  it('preserves valid zero score (0/100 is valid, not NOT_ASSESSED or missing)', () => {
    const raw = {
      status: 'AVAILABLE',
      score: 0.0,
      base_score: 0.0,
      lesson_delta: 0.0,
      threshold: 70.0,
      decision: 'BELOW_THRESHOLD',
    };

    const norm = normalizeCandidateScore(raw);
    expect(norm.status).toBe('AVAILABLE');
    expect(norm.score).toBe(0.0);
  });

  it('rejects boolean masquerading as number', () => {
    const raw = {
      status: 'AVAILABLE',
      score: true,
      base_score: false,
    };

    const norm = normalizeCandidateScore(raw);
    expect(norm.status).toBe('INVALID');
    expect(norm.score).toBeNull();
  });

  it('marks score out of range (>100 or <0) as INVALID', () => {
    const rawOver = { status: 'AVAILABLE', score: 105.0 };
    expect(normalizeCandidateScore(rawOver).status).toBe('INVALID');

    const rawUnder = { status: 'AVAILABLE', score: -5.0 };
    expect(normalizeCandidateScore(rawUnder).status).toBe('INVALID');
  });

  it('handles empty, null, or undefined gracefully without throwing', () => {
    expect(normalizeCandidateScore(null).status).toBe('NOT_ASSESSED');
    expect(normalizeCandidateScore(undefined).status).toBe('NOT_ASSESSED');
    expect(normalizeCandidateScore({}).status).toBe('NOT_ASSESSED');
  });
});

describe('V16.39.11 Candidate Score Formatting & Labels (Rules 103, 104, 130, 168)', () => {
  it('formats integer score 78 as "78/100" without % suffix', () => {
    const raw = {
      status: 'AVAILABLE',
      score: 78,
      base_score: 83,
      lesson_delta: -5,
      threshold: 70,
      decision: 'ELIGIBLE',
      frozen: true,
    };

    const fmt = formatCandidateScore(raw);
    expect(fmt.displayText).toBe('78/100');
    expect(fmt.displayText).not.toContain('%');
    expect(fmt.displayText).not.toContain('83');
    expect(fmt.isAvailable).toBe(true);
    expect(fmt.accessibleLabel).toContain('78 trên 100');
    expect(fmt.accessibleLabel).toContain('không phải xác suất thắng');
  });

  it('formats decimal score without misleading rounding to threshold (69.9 does not become 70)', () => {
    const raw = {
      status: 'AVAILABLE',
      score: 69.9,
      threshold: 70.0,
      decision: 'BELOW_THRESHOLD',
    };

    const fmt = formatCandidateScore(raw);
    expect(fmt.displayText).toBe('69.90/100');
    expect(fmt.tooltipText).toContain('Dưới ngưỡng điểm');
  });

  it('formats zero score as "0/100"', () => {
    const raw = {
      status: 'AVAILABLE',
      score: 0,
      threshold: 70,
      decision: 'BELOW_THRESHOLD',
    };

    const fmt = formatCandidateScore(raw);
    expect(fmt.displayText).toBe('0/100');
    expect(fmt.isAvailable).toBe(true);
  });

  it('formats legacy orders without snapshots as "Chưa có điểm" with legacy explanation', () => {
    const raw = {
      status: 'UNAVAILABLE',
      reason_code: 'LEGACY_NO_SCORE',
    };

    const fmt = formatCandidateScore(raw);
    expect(fmt.displayText).toBe('Chưa có điểm');
    expect(fmt.isAvailable).toBe(false);
    expect(fmt.tooltipText).toContain('phiên bản cũ');
  });

  it('formats invalid snapshots as "Điểm chưa xác minh"', () => {
    const raw = {
      status: 'INVALID',
      score: null,
    };

    const fmt = formatCandidateScore(raw);
    expect(fmt.displayText).toBe('Điểm chưa xác minh');
    expect(fmt.isAvailable).toBe(false);
  });
});

describe('V16.39.11 / V16.39.12 Decision Translation & Disclaimers (Rules 064, 133, 170)', () => {
  it('translates decision codes accurately into Vietnamese', () => {
    expect(translateDecision('ELIGIBLE_BY_SCORE')).toBe('Đạt ngưỡng điểm');
    expect(translateDecision('ELIGIBLE')).toBe('Đạt ngưỡng điểm');
    expect(translateDecision('BELOW_THRESHOLD')).toBe('Dưới ngưỡng điểm');
    expect(translateDecision('BLOCKED')).toBe('Bị chặn bởi điều kiện bảo vệ');
    expect(translateDecision('BLOCKED_REQUIRED')).toBe('Thiếu điều kiện bắt buộc');
    expect(translateDecision('BLOCKED_GUARD')).toBe('Bị chặn bởi bộ lọc bảo vệ');
    expect(translateDecision('ERROR')).toBe('Lỗi đánh giá');
    expect(translateDecision('UNKNOWN_CODE')).toBe('UNKNOWN_CODE');
  });

  it('includes mandatory disclaimer that score is not winrate in tooltip', () => {
    const raw = {
      status: 'AVAILABLE',
      score: 78,
      threshold: 70,
      decision: 'ELIGIBLE',
    };
    const fmt = formatCandidateScore(raw);
    expect(fmt.tooltipText).toContain('không phải xác suất thắng');
  });
});

import React from 'react';
import { renderToString } from 'react-dom/server';
import { CandidateScoreBadge } from './components/CandidateScoreBadge';
import { CandidateScoreDetails } from './components/CandidateScoreDetails';

describe('V16.39.11 Component Rendering Tests (Rules 169, 170)', () => {
  it('renders CandidateScoreBadge with 78/100, accessible aria-label, and frozen indicator', () => {
    const summary = {
      status: 'AVAILABLE' as const,
      score: 78,
      max_score: 100,
      base_score: 83,
      lesson_delta: -5,
      threshold: 70,
      decision: 'ELIGIBLE',
      as_of_ms: 1700000000000,
      source: 'ARM_SNAPSHOT' as const,
      frozen: true,
      assessment_id: 'assess-123',
      candidate_key: 'XAUUSDT:15M:LONG:2650.0',
      disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
    };

    const html = renderToString(React.createElement(CandidateScoreBadge, { summary }));
    expect(html).toContain('78/100');
    expect(html).toContain('Đã chốt');
    expect(html).toContain('data-score="78"');
    expect(html).toContain('aria-label="Điểm lúc ARM: 78 trên 100, ngưỡng 70, không phải xác suất thắng"');
  });

  it('renders CandidateScoreBadge as button when onOpenDetails is provided', () => {
    const summary = {
      status: 'AVAILABLE' as const,
      score: 78,
      max_score: 100,
      base_score: 83,
      lesson_delta: -5,
      threshold: 70,
      decision: 'ELIGIBLE',
      as_of_ms: 1700000000000,
      source: 'ARM_SNAPSHOT' as const,
      frozen: true,
      assessment_id: 'assess-123',
      candidate_key: 'XAUUSDT:15M:LONG:2650.0',
      disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
    };

    const html = renderToString(React.createElement(CandidateScoreBadge, { summary, onOpenDetails: () => {} }));
    expect(html).toContain('<button');
    expect(html).toContain('type="button"');
  });

  it('renders CandidateScoreBadge with "Chưa có điểm" for unassessed setups', () => {
    const html = renderToString(React.createElement(CandidateScoreBadge, { summary: null }));
    expect(html).toContain('Chưa có điểm');
  });

  it('renders CandidateScoreDetails with base 83, lesson -5, final 78, threshold 70, and disclaimer', () => {
    const summary = {
      status: 'AVAILABLE' as const,
      score: 78,
      max_score: 100,
      base_score: 83,
      lesson_delta: -5,
      threshold: 70,
      decision: 'ELIGIBLE',
      as_of_ms: 1700000000000,
      source: 'ARM_SNAPSHOT' as const,
      frozen: true,
      assessment_id: 'assess-123',
      candidate_key: 'XAUUSDT:15M:LONG:2650.0',
      disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
    };

    const html = renderToString(React.createElement(CandidateScoreDetails, { summary, orderId: 'ord-test-456' }));
    expect(html).toContain('83');
    expect(html).toContain('-5');
    expect(html).toContain('78/100');
    expect(html).toContain('70');
    expect(html).toContain('FROZEN');
    expect(html).toContain('ord-test-456');
    expect(html).toContain('KHÔNG');
    expect(html).toContain('xác suất thắng');
  });

  it('renders CandidateScoreDetails for legacy order with clear explanation without undefined/NaN', () => {
    const summary = {
      status: 'UNAVAILABLE' as const,
      score: null,
      max_score: 100,
      base_score: null,
      lesson_delta: null,
      threshold: null,
      decision: null,
      as_of_ms: null,
      source: 'NONE' as const,
      frozen: false,
      assessment_id: null,
      candidate_key: null,
      reason_code: 'LEGACY_NO_SCORE',
      disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
    };

    const html = renderToString(React.createElement(CandidateScoreDetails, { summary }));
    expect(html).toContain('phiên bản cũ');
    expect(html).not.toContain('undefined');
    expect(html).not.toContain('NaN');
    expect(html).not.toContain('null');
  });

  it('renders CandidateScoreDetails with canonical factors (FVG 18/20, Vietnamese explanation)', () => {
    const detail = {
      status: 'AVAILABLE' as const,
      score: 78,
      max_score: 100,
      base_score: null,
      lesson_delta: null,
      threshold: null,
      decision: 'ELIGIBLE_BY_SCORE',
      as_of_ms: 1700000000000,
      source: 'ARM_SNAPSHOT' as const,
      frozen: true,
      assessment_id: 'assess-123',
      candidate_key: 'XAUUSDT:15M:LONG:2650.0',
      disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
      factors: [
        {
          code: 'FVG_ALIGNMENT',
          group: 'STRUCTURE',
          earned_points: 18,
          max_points: 20,
          status: 'FULL',
          explanation_vi: 'Khối FVG tăng M15 đồng pha hợp lệ',
        },
      ],
      blockers: [],
      missing_data: [],
    };

    const html = renderToString(React.createElement(CandidateScoreDetails, { detail, orderId: 'ord-test-456' }));
    expect(html).toContain('FVG_ALIGNMENT');
    expect(html).toContain('Khối FVG tăng M15 đồng pha hợp lệ');
    expect(html).toContain('18/20');
    // Rules 072, 107: Threshold and base score should render 'Chưa lưu' when missing (no fabricated 70 or 78)
    expect(html).toContain('Chưa lưu');
  });

  it('renders inline error with retry button when detail fails to load (Rule 082)', () => {
    const summary = {
      status: 'AVAILABLE' as const,
      score: 78,
      max_score: 100,
      base_score: 83,
      lesson_delta: -5,
      threshold: 70,
      decision: 'ELIGIBLE',
      as_of_ms: 1700000000000,
      source: 'ARM_SNAPSHOT' as const,
      frozen: true,
      assessment_id: 'assess-123',
      candidate_key: 'XAUUSDT:15M:LONG:2650.0',
      disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
    };

    const html = renderToString(
      React.createElement(CandidateScoreDetails, {
        summary,
        error: 'Network connection failed',
        onRetry: () => {},
      })
    );
    expect(html).toContain('Chưa tải được chi tiết điểm:');
    expect(html).toContain('Network connection failed');
    expect(html).toContain('Thử lại');
  });
});

describe('V16.39.12 Target Resolver (Rules 075, 076)', () => {
  it('resolves ORDER kind when binding is ORDER', () => {
    const target = getSelectedScoreTarget(
      { kind: 'ORDER', id: 'ord-123' },
      { source: 'OPEN_POSITION', setup_id: 'ord-123', candidate_score_summary: { assessment_id: 'assess-1' } }
    );
    expect(target).toEqual({
      kind: 'ORDER',
      id: 'ord-123',
      assessmentId: 'assess-1',
    });
  });

  it('resolves SETUP kind when selectedIntent is WATCH_SETUP', () => {
    const target = getSelectedScoreTarget(
      null,
      { source: 'WATCH_SETUP', setup_id: 'setup-456', candidate_score_summary: { assessment_id: 'assess-2' } }
    );
    expect(target).toEqual({
      kind: 'SETUP',
      id: 'setup-456',
      assessmentId: 'assess-2',
    });
  });

  it('resolves ORDER kind when selectedIntent is OPEN_POSITION without binding', () => {
    const target = getSelectedScoreTarget(
      null,
      { source: 'OPEN_POSITION', setup_id: 'trade-789', candidate_score_summary: { assessment_id: null } }
    );
    expect(target).toEqual({
      kind: 'ORDER',
      id: 'trade-789',
      assessmentId: null,
    });
  });

  it('returns null when neither binding nor selectedIntent is available', () => {
    expect(getSelectedScoreTarget(null, null)).toBeNull();
  });
});
