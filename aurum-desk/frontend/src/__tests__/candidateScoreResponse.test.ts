// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/candidateScoreResponse.test.ts
 * V16.39.13 Response Validator & Key Utility Tests
 *
 * Rules 021-023, 061-076, 086-087, 113-120, 138-139:
 * - Pure unit tests for validateQualityScoreResponse, makeScoreRequestKey, translateQualityScoreError.
 * - Strict envelope validation: { status: 'SUCCESS', order_id, detail }.
 * - Missing order_id -> DETAIL_BINDING_MISSING (no fallback to requested ID).
 * - Mismatched order_id -> DETAIL_BINDING_MISMATCH.
 * - Missing assessment_id when expected -> DETAIL_ASSESSMENT_MISSING.
 * - Mismatched assessment_id when expected -> DETAIL_ASSESSMENT_MISMATCH.
 * - Rejection of flat payloads (Rule 119) and non-object scalars/arrays (Rule 120).
 * - Immutable validation: inputs are not mutated.
 */

import { describe, it, expect } from 'vitest';
import {
  validateQualityScoreResponse,
  makeScoreRequestKey,
  translateQualityScoreError,
} from '../utils/candidateScore';

describe('V16.39.13 makeScoreRequestKey (Rules 021-023)', () => {
  it('encodes stable structured key with orderId and assessmentId', () => {
    const key1 = makeScoreRequestKey('ord-123', 'assess-456');
    expect(key1).toBe(JSON.stringify(['ord-123', 'assess-456']));

    // Key with colons does not collide or break
    const keyWithColons = makeScoreRequestKey('ord:xau:1', 'assess:15m:long');
    expect(keyWithColons).toBe(JSON.stringify(['ord:xau:1', 'assess:15m:long']));
  });

  it('handles null or empty expectedAssessmentId gracefully', () => {
    const key = makeScoreRequestKey('ord-123', null);
    expect(key).toBe(JSON.stringify(['ord-123', null]));

    const keyEmpty = makeScoreRequestKey('ord-123', '');
    expect(keyEmpty).toBe(JSON.stringify(['ord-123', null]));
  });

  it('returns null when orderId is missing, empty, or whitespace', () => {
    expect(makeScoreRequestKey(null)).toBeNull();
    expect(makeScoreRequestKey(undefined)).toBeNull();
    expect(makeScoreRequestKey('')).toBeNull();
    expect(makeScoreRequestKey('   ')).toBeNull();
  });
});

describe('V16.39.13 validateQualityScoreResponse (Rules 061-076, 113-120, 138-139)', () => {
  const validProductionEnvelope = {
    status: 'SUCCESS',
    order_id: 'ord-100',
    detail: {
      order_id: 'ord-100',
      assessment_id: 'assess-100',
      score: 78.0,
      status: 'AVAILABLE' as const,
      factors: [
        {
          code: 'FVG_ALIGNMENT',
          group: 'STRUCTURE',
          earned_points: 18.0,
          max_points: 20.0,
          status: 'FULL',
          explanation_vi: 'Khối FVG tăng M15 hợp lệ',
        },
      ],
    },
  };

  it('accepts valid production envelope with matching orderId and assessmentId (Rule 117)', () => {
    const result = validateQualityScoreResponse(validProductionEnvelope, {
      orderId: 'ord-100',
      expectedAssessmentId: 'assess-100',
    });

    expect(result.valid).toBe(true);
    if (result.valid) {
      expect(result.orderId).toBe('ord-100');
      expect(result.detail.score).toBe(78.0);
      expect(result.detail.factors[0].code).toBe('FVG_ALIGNMENT');
    }
  });

  it('accepts valid legacy envelope with UNAVAILABLE detail and no expected assessmentId (Rule 118)', () => {
    const legacyEnvelope = {
      status: 'SUCCESS',
      order_id: 'ord-legacy',
      detail: {
        order_id: 'ord-legacy',
        assessment_id: null,
        score: null,
        status: 'UNAVAILABLE' as const,
        reason_code: 'LEGACY_NO_SCORE',
        factors: [],
      },
    };

    const result = validateQualityScoreResponse(legacyEnvelope, {
      orderId: 'ord-legacy',
      expectedAssessmentId: null,
    });

    expect(result.valid).toBe(true);
    if (result.valid) {
      expect(result.orderId).toBe('ord-legacy');
      expect(result.detail.status).toBe('UNAVAILABLE');
    }
  });

  it('rejects envelope when order_id is missing or empty (Rule 066, 113)', () => {
    const missingOrderId = {
      status: 'SUCCESS',
      detail: {
        score: 78.0,
        status: 'AVAILABLE',
        assessment_id: 'assess-100',
      },
    };

    const res1 = validateQualityScoreResponse(missingOrderId, { orderId: 'ord-100' });
    expect(res1.valid).toBe(false);
    if (!res1.valid) {
      expect(res1.errorCode).toBe('DETAIL_BINDING_MISSING');
    }

    const emptyOrderId = {
      status: 'SUCCESS',
      order_id: '   ',
      detail: { score: 78.0 },
    };
    const res2 = validateQualityScoreResponse(emptyOrderId, { orderId: 'ord-100' });
    expect(res2.valid).toBe(false);
    if (!res2.valid) {
      expect(res2.errorCode).toBe('DETAIL_BINDING_MISSING');
    }
  });

  it('rejects envelope when order_id mismatches expected orderId (Rule 066, 115)', () => {
    const mismatchedOrderId = {
      status: 'SUCCESS',
      order_id: 'ord-B',
      detail: {
        score: 64.0,
        status: 'AVAILABLE',
      },
    };

    const result = validateQualityScoreResponse(mismatchedOrderId, { orderId: 'ord-A' });
    expect(result.valid).toBe(false);
    if (!result.valid) {
      expect(result.errorCode).toBe('DETAIL_BINDING_MISMATCH');
    }
  });

  it('rejects envelope when expectedAssessmentId is specified but detail.assessment_id is missing (Rule 068, 114)', () => {
    const missingAssessmentId = {
      status: 'SUCCESS',
      order_id: 'ord-100',
      detail: {
        score: 78.0,
        status: 'AVAILABLE',
        // assessment_id omitted
      },
    };

    const result = validateQualityScoreResponse(missingAssessmentId, {
      orderId: 'ord-100',
      expectedAssessmentId: 'assess-100',
    });
    expect(result.valid).toBe(false);
    if (!result.valid) {
      expect(result.errorCode).toBe('DETAIL_ASSESSMENT_MISSING');
    }
  });

  it('rejects envelope when detail.assessment_id mismatches expectedAssessmentId (Rule 068, 116)', () => {
    const mismatchedAssessmentId = {
      status: 'SUCCESS',
      order_id: 'ord-100',
      detail: {
        score: 78.0,
        status: 'AVAILABLE',
        assessment_id: 'assess-OTHER',
      },
    };

    const result = validateQualityScoreResponse(mismatchedAssessmentId, {
      orderId: 'ord-100',
      expectedAssessmentId: 'assess-EXPECTED',
    });
    expect(result.valid).toBe(false);
    if (!result.valid) {
      expect(result.errorCode).toBe('DETAIL_ASSESSMENT_MISMATCH');
    }
  });

  it('rejects flat payload without production envelope (Rule 065, 119)', () => {
    const flatPayload = {
      order_id: 'ord-100',
      assessment_id: 'assess-100',
      score: 78.0,
      status: 'AVAILABLE',
      factors: [],
    };

    const result = validateQualityScoreResponse(flatPayload, { orderId: 'ord-100' });
    expect(result.valid).toBe(false);
    if (!result.valid) {
      expect(result.errorCode).toBe('INVALID_DETAIL_PAYLOAD');
    }
  });

  it('rejects null, array, string, or scalar values (Rule 063, 120)', () => {
    expect(validateQualityScoreResponse(null, { orderId: 'ord-1' }).valid).toBe(false);
    expect(validateQualityScoreResponse([], { orderId: 'ord-1' }).valid).toBe(false);
    expect(validateQualityScoreResponse('string', { orderId: 'ord-1' }).valid).toBe(false);
    expect(validateQualityScoreResponse(12345, { orderId: 'ord-1' }).valid).toBe(false);
    expect(validateQualityScoreResponse({ status: 'SUCCESS', order_id: 'ord-1', detail: null }, { orderId: 'ord-1' }).valid).toBe(false);
  });

  it('does not mutate input objects (Rule 078, 138)', () => {
    const inputPayload = Object.freeze({
      status: 'SUCCESS',
      order_id: 'ord-100',
      detail: Object.freeze({
        order_id: 'ord-100',
        assessment_id: 'assess-100',
        score: 78.0,
        status: 'AVAILABLE' as const,
        factors: Object.freeze([]),
      }),
    });

    const expected = Object.freeze({
      orderId: 'ord-100',
      expectedAssessmentId: 'assess-100',
    });

    expect(() => validateQualityScoreResponse(inputPayload, expected)).not.toThrow();
  });
});

describe('V16.39.13 translateQualityScoreError (Rule 076, 187)', () => {
  it('translates all known error codes into clear Vietnamese descriptions', () => {
    expect(translateQualityScoreError('DETAIL_BINDING_MISSING')).toContain('thiếu mã lệnh');
    expect(translateQualityScoreError('DETAIL_BINDING_MISMATCH')).toContain('không khớp với lệnh');
    expect(translateQualityScoreError('DETAIL_ASSESSMENT_MISSING')).toContain('thiếu mã đánh giá');
    expect(translateQualityScoreError('DETAIL_ASSESSMENT_MISMATCH')).toContain('không khớp với bản ghi lúc ARM');
    expect(translateQualityScoreError('INVALID_DETAIL_PAYLOAD')).toContain('không hợp lệ');
  });
});
