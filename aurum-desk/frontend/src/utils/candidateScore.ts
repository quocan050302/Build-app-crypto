/**
 * frontend/src/utils/candidateScore.ts
 * V16.39.11 Candidate Score Display Utilities
 *
 * Rules 102-104: Pure normalization, formatting, and translation functions.
 */

import type {
  CandidateScoreSummary,
  CandidateScoreDetail,
  CandidateScoreStatus,
  CandidateScoreSource,
  ScoreDetailValidationResult,
} from '../types/candidateScore';

/**
 * Normalizes raw API response into a strict, validated CandidateScoreSummary model.
 * Never multiplies by 100 or parses '%' strings into probabilities.
 */
export function normalizeCandidateScore(raw: any): CandidateScoreSummary {
  if (!raw || typeof raw !== 'object') {
    return {
      status: 'NOT_ASSESSED',
      score: null,
      max_score: 100,
      base_score: null,
      lesson_delta: null,
      threshold: null,
      decision: null,
      as_of_ms: null,
      source: 'NONE',
      frozen: false,
      assessment_id: null,
      candidate_key: null,
      reason_code: 'NO_RAW_DATA',
      disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
    };
  }

  // Parse numeric values safely
  const parseNum = (val: any): number | null => {
    if (typeof val === 'boolean') return null;
    if (typeof val === 'number' && Number.isFinite(val)) return val;
    if (typeof val === 'string' && val.trim() !== '') {
      const parsed = Number(val);
      if (Number.isFinite(parsed)) return parsed;
    }
    return null;
  };

  const rawScore = parseNum(raw.score);
  const baseScore = parseNum(raw.base_score);
  const lessonDelta = parseNum(raw.lesson_delta);
  const threshold = parseNum(raw.threshold);
  const maxScore = parseNum(raw.max_score) ?? 100;

  // Validate range 0 to max_score
  let validScore: number | null = null;
  if (rawScore !== null) {
    if (rawScore >= 0 && rawScore <= maxScore) {
      validScore = rawScore;
    }
  }

  let status: CandidateScoreStatus = (raw.status as CandidateScoreStatus) || 'NOT_ASSESSED';
  if (validScore === null && status === 'AVAILABLE') {
    status = 'INVALID';
  }

  const source: CandidateScoreSource = (raw.source as CandidateScoreSource) || 'NONE';
  const frozen = Boolean(raw.frozen);

  return {
    status,
    score: validScore,
    max_score: maxScore,
    base_score: baseScore,
    lesson_delta: lessonDelta,
    threshold,
    decision: typeof raw.decision === 'string' ? raw.decision : null,
    as_of_ms: typeof raw.as_of_ms === 'number' ? raw.as_of_ms : null,
    armed_at_ms: typeof raw.armed_at_ms === 'number' ? raw.armed_at_ms : null,
    source,
    frozen,
    assessment_id: typeof raw.assessment_id === 'string' ? raw.assessment_id : null,
    candidate_key: typeof raw.candidate_key === 'string' ? raw.candidate_key : null,
    model_id: typeof raw.model_id === 'string' ? raw.model_id : null,
    profile_version: typeof raw.profile_version === 'string' ? raw.profile_version : null,
    config_fingerprint: typeof raw.config_fingerprint === 'string' ? raw.config_fingerprint : null,
    reason_code: typeof raw.reason_code === 'string' ? raw.reason_code : null,
    disclaimer: typeof raw.disclaimer === 'string' ? raw.disclaimer : 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
  };
}

export interface FormattedCandidateScore {
  displayText: string;
  tooltipText: string;
  accessibleLabel: string;
  isAvailable: boolean;
  scoreNumber: number | null;
  badgeStyle: string;
}

/**
 * Formats candidate score for display.
 * e.g. 78 -> "78/100" (or "78.25/100", 0 -> "0/100").
 * Rules 103, 104, 108.
 */
export function formatCandidateScore(
  rawSummary?: any,
  options?: { compact?: boolean; labelPrefix?: string }
): FormattedCandidateScore {
  const summary = normalizeCandidateScore(rawSummary);
  const prefix = options?.labelPrefix || (summary.frozen ? 'Điểm lúc ARM' : 'Điểm đánh giá');

  if (summary.status === 'AVAILABLE' && summary.score !== null) {
    const scoreVal = summary.score;
    // Format up to 2 decimal places if fractional, otherwise integer
    const formattedNum = Number.isInteger(scoreVal) ? scoreVal.toString() : scoreVal.toFixed(2);
    const displayText = `${formattedNum}/${summary.max_score}`;

    const decisionVi = translateDecision(summary.decision);
    const thresholdText = summary.threshold !== null ? `Ngưỡng: ${summary.threshold}` : '';
    const tooltipParts: string[] = [
      `${prefix}: ${displayText}`,
      thresholdText,
      `Quyết định: ${decisionVi}`,
    ];
    if (summary.base_score !== null) tooltipParts.push(`Điểm gốc: ${summary.base_score}`);
    if (summary.lesson_delta !== null && summary.lesson_delta !== 0) {
      tooltipParts.push(`Điều chỉnh bài học: ${summary.lesson_delta > 0 ? '+' : ''}${summary.lesson_delta}`);
    }
    tooltipParts.push(summary.disclaimer);

    const accessibleLabel = `${prefix}: ${formattedNum} trên ${summary.max_score}${summary.threshold !== null ? `, ngưỡng ${summary.threshold}` : ''}, không phải xác suất thắng`;

    // Badge styling based on decision / eligibility (Rule 065: precedence to blocked/error/insufficient)
    let badgeStyle = 'bg-charcoal-800 text-gray-300 border-charcoal-700';
    if (summary.decision?.startsWith('BLOCKED')) {
      badgeStyle = 'bg-rose-950/80 text-rose-300 border-rose-700/80';
    } else if (summary.decision === 'INSUFFICIENT_DATA' || summary.decision === 'ERROR') {
      badgeStyle = 'bg-amber-950/80 text-amber-300 border-amber-700/80';
    } else if (
      summary.decision === 'ELIGIBLE_BY_SCORE' ||
      summary.decision === 'ELIGIBLE' ||
      (summary.threshold !== null && scoreVal >= summary.threshold)
    ) {
      badgeStyle = 'bg-cyan-950/80 text-cyan-300 border-cyan-700/80';
    } else if (
      summary.decision === 'BELOW_THRESHOLD' ||
      (summary.threshold !== null && scoreVal < summary.threshold)
    ) {
      badgeStyle = 'bg-amber-950/80 text-amber-300 border-amber-700/80';
    }

    return {
      displayText,
      tooltipText: tooltipParts.filter(Boolean).join(' · '),
      accessibleLabel,
      isAvailable: true,
      scoreNumber: scoreVal,
      badgeStyle,
    };
  }

  if (summary.status === 'INVALID') {
    return {
      displayText: 'Điểm chưa xác minh',
      tooltipText: 'Dữ liệu đánh giá điểm bị lỗi hoặc snapshot không hợp lệ.',
      accessibleLabel: `${prefix}: Điểm chưa xác minh`,
      isAvailable: false,
      scoreNumber: null,
      badgeStyle: 'bg-rose-950/60 text-rose-400 border-rose-800/60',
    };
  }

  // NOT_ASSESSED or UNAVAILABLE (legacy)
  const isLegacy = summary.reason_code === 'LEGACY_NO_SCORE' || summary.status === 'UNAVAILABLE';
  return {
    displayText: 'Chưa có điểm',
    tooltipText: isLegacy
      ? 'Lệnh này chưa lưu điểm ở phiên bản cũ (snapshot legacy).'
      : 'Setup đang chờ đánh giá chất lượng tiêu chí.',
    accessibleLabel: `${prefix}: Chưa có điểm`,
    isAvailable: false,
    scoreNumber: null,
    badgeStyle: 'bg-charcoal-800 text-gray-400 border-charcoal-700',
  };
}

/**
 * Rules 064, 095: Translates assessment decision codes into user-friendly Vietnamese text.
 */
export function translateDecision(decision?: string | null): string {
  switch (decision) {
    case 'ELIGIBLE_BY_SCORE':
    case 'ELIGIBLE':
      return 'Đạt ngưỡng điểm';
    case 'BELOW_THRESHOLD':
      return 'Dưới ngưỡng điểm';
    case 'BLOCKED_REQUIRED':
      return 'Thiếu điều kiện bắt buộc';
    case 'BLOCKED_GUARD':
      return 'Bị chặn bởi bộ lọc bảo vệ';
    case 'BLOCKED':
      return 'Bị chặn bởi điều kiện bảo vệ';
    case 'INSUFFICIENT_DATA':
      return 'Dữ liệu chưa đầy đủ';
    case 'ERROR':
      return 'Lỗi đánh giá';
    default:
      return decision || 'Đang quan sát';
  }
}

export interface ScoreTarget {
  kind: 'ORDER' | 'SETUP';
  id: string;
  assessmentId?: string | null;
}

/**
 * Rules 075-076: Pure resolver distinguishing ORDER from SETUP score targets.
 * Prevents requesting /orders/{setupId}/quality-score when selectedIntent is a setup.
 */
export function getSelectedScoreTarget(
  binding?: { kind?: string; source?: string; id: string } | null,
  selectedIntent?: {
    source: string;
    id?: string;
    order_id?: string;
    setup_id?: string;
    candidate_score_summary?: { assessment_id?: string | null } | null;
  } | null
): ScoreTarget | null {
  const bindingKind = binding?.kind || binding?.source;
  if (binding && bindingKind === 'ORDER' && binding.id) {
    return {
      kind: 'ORDER',
      id: binding.id,
      assessmentId: selectedIntent?.candidate_score_summary?.assessment_id ?? null,
    };
  }

  if (selectedIntent) {
    if (selectedIntent.source === 'OPEN_POSITION') {
      const targetOrderId = selectedIntent.order_id || selectedIntent.id || selectedIntent.setup_id;
      if (targetOrderId) {
        return {
          kind: 'ORDER',
          id: targetOrderId,
          assessmentId: selectedIntent.candidate_score_summary?.assessment_id ?? null,
        };
      }
    }
    if (selectedIntent.source === 'WATCH_SETUP' && selectedIntent.setup_id) {
      return {
        kind: 'SETUP',
        id: selectedIntent.setup_id,
        assessmentId: selectedIntent.candidate_score_summary?.assessment_id ?? null,
      };
    }
  }

  return null;
}

/**
 * Rules 021-023 (V16.39.13): Encodes stable structured target key.
 * Prevents string delimiter collision and returns null when target is invalid.
 */
export function makeScoreRequestKey(orderId?: string | null, expectedAssessmentId?: string | null): string | null {
  if (!orderId || typeof orderId !== 'string' || orderId.trim() === '') {
    return null;
  }
  return JSON.stringify([orderId.trim(), expectedAssessmentId ? expectedAssessmentId.trim() : null]);
}

/**
 * Rules 076, 187 (V16.39.13): User-friendly Vietnamese translation for error codes.
 */
export function translateQualityScoreError(code?: string | null): string {
  switch (code) {
    case 'DETAIL_BINDING_MISSING':
      return 'Dữ liệu phản hồi thiếu mã lệnh xác minh.';
    case 'DETAIL_BINDING_MISMATCH':
      return 'Mã lệnh phản hồi không khớp với lệnh yêu cầu.';
    case 'DETAIL_ASSESSMENT_MISSING':
      return 'Dữ liệu phản hồi thiếu mã đánh giá tiêu chí.';
    case 'DETAIL_ASSESSMENT_MISMATCH':
      return 'Mã đánh giá tiêu chí không khớp với bản ghi lúc ARM.';
    case 'INVALID_DETAIL_PAYLOAD':
      return 'Cấu trúc dữ liệu chi tiết điểm không hợp lệ.';
    case 'FAILED_TO_LOAD_DETAIL':
    default:
      return code || 'Chưa tải được chi tiết điểm của lệnh này.';
  }
}

/**
 * Rules 062-075, 138-139 (V16.39.13):
 * Strict pure validator for GET /api/v1/orders/{id}/quality-score response.
 * - Envelope must be object with status === 'SUCCESS'
 * - order_id must be non-empty string and EXACTLY match expected orderId (no fallback!)
 * - detail must be object
 * - If expectedAssessmentId is provided, detail.assessment_id must be non-empty string and EXACTLY match
 * - If detail.status === 'AVAILABLE', score must be finite and within 0..100
 */
export function validateQualityScoreResponse(
  raw: unknown,
  expected: { orderId: string; expectedAssessmentId?: string | null }
): ScoreDetailValidationResult {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {
      valid: false,
      errorCode: 'INVALID_DETAIL_PAYLOAD',
      errorMessage: translateQualityScoreError('INVALID_DETAIL_PAYLOAD'),
    };
  }

  const envelope = raw as Record<string, any>;
  if (envelope.status !== 'SUCCESS') {
    return {
      valid: false,
      errorCode: 'INVALID_DETAIL_PAYLOAD',
      errorMessage: translateQualityScoreError('INVALID_DETAIL_PAYLOAD'),
    };
  }

  // Rule 066: Order ID is strictly mandatory and must match expected
  if (!envelope.order_id || typeof envelope.order_id !== 'string' || envelope.order_id.trim() === '') {
    return {
      valid: false,
      errorCode: 'DETAIL_BINDING_MISSING',
      errorMessage: translateQualityScoreError('DETAIL_BINDING_MISSING'),
    };
  }

  if (envelope.order_id.trim() !== expected.orderId.trim()) {
    return {
      valid: false,
      errorCode: 'DETAIL_BINDING_MISMATCH',
      errorMessage: translateQualityScoreError('DETAIL_BINDING_MISMATCH'),
    };
  }

  // Detail payload must be a non-null object
  const detail = envelope.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return {
      valid: false,
      errorCode: 'INVALID_DETAIL_PAYLOAD',
      errorMessage: translateQualityScoreError('INVALID_DETAIL_PAYLOAD'),
    };
  }

  // Rule 068: If expectedAssessmentId is specified, detail.assessment_id is mandatory and must match
  if (expected.expectedAssessmentId && expected.expectedAssessmentId.trim() !== '') {
    if (!detail.assessment_id || typeof detail.assessment_id !== 'string' || detail.assessment_id.trim() === '') {
      return {
        valid: false,
        errorCode: 'DETAIL_ASSESSMENT_MISSING',
        errorMessage: translateQualityScoreError('DETAIL_ASSESSMENT_MISSING'),
      };
    }
    if (detail.assessment_id.trim() !== expected.expectedAssessmentId.trim()) {
      return {
        valid: false,
        errorCode: 'DETAIL_ASSESSMENT_MISMATCH',
        errorMessage: translateQualityScoreError('DETAIL_ASSESSMENT_MISMATCH'),
      };
    }
  }

  // Rule 071: Score finite range check when AVAILABLE
  if (detail.status === 'AVAILABLE') {
    if (typeof detail.score !== 'number' || !Number.isFinite(detail.score) || detail.score < 0 || detail.score > 100) {
      return {
        valid: false,
        errorCode: 'INVALID_DETAIL_PAYLOAD',
        errorMessage: translateQualityScoreError('INVALID_DETAIL_PAYLOAD'),
      };
    }
  }

  return {
    valid: true,
    detail: detail as CandidateScoreDetail,
    orderId: envelope.order_id.trim(),
  };
}
