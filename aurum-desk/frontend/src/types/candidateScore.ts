/**
 * frontend/src/types/candidateScore.ts
 * V16.39.11 Candidate Quality Score Types
 *
 * Authoritative types for CG-01 candidate quality assessment display.
 * Rules 051-065, 101:
 * - Quality score (e.g. 78/100) is a criteria match score, NOT win probability.
 * - Scores frozen at ARM time remain immutable regardless of trade outcome or new lessons.
 * - Legacy orders without snapshots report transparent UNAVAILABLE/LEGACY_NO_SCORE.
 */

export type CandidateScoreStatus =
  | 'AVAILABLE'
  | 'NOT_ASSESSED'
  | 'UNAVAILABLE'
  | 'INVALID'
  | 'CONFIG_CHANGED';

export type CandidateScoreSource =
  | 'ARM_SNAPSHOT'
  | 'VOLATILE_LATEST_EVALUATION'
  | 'NONE';

export interface CandidateScoreSummary {
  status: CandidateScoreStatus;
  score: number | null;
  max_score: number;
  base_score: number | null;
  lesson_delta: number | null;
  threshold: number | null;
  decision: string | null;
  as_of_ms: number | null;
  armed_at_ms?: number | null;
  source: CandidateScoreSource;
  frozen: boolean;
  assessment_id: string | null;
  candidate_key: string | null;
  model_id?: string | null;
  profile_version?: string | null;
  config_fingerprint?: string | null;
  reason_code?: string | null;
  disclaimer: string;
}

export interface CandidateScoreFactor {
  code: string;
  name?: string;
  group?: string;
  earned_points?: number;
  max_points?: number;
  score?: number;
  max_score?: number;
  weight?: number;
  status?: string;
  importance?: string;
  explanation_vi?: string;
  evidence_ids?: string[];
  known_at_ms?: number | null;
  measurement?: any;
  rule_version?: string;
  observed?: any;
  expected?: any;
  reason?: string;
}

export interface CandidateScoreDetail extends CandidateScoreSummary {
  order_id?: string | null;
  factors: CandidateScoreFactor[];
  group_scores?: Record<string, Record<string, number>>;
  missing_data?: string[];
  blockers?: string[];
}

export interface QualityScoreResponse {
  status: 'SUCCESS' | string;
  order_id?: string;
  detail?: CandidateScoreDetail | null;
}

export type QualityScoreErrorCode =
  | 'DETAIL_BINDING_MISSING'
  | 'DETAIL_BINDING_MISMATCH'
  | 'DETAIL_ASSESSMENT_MISSING'
  | 'DETAIL_ASSESSMENT_MISMATCH'
  | 'INVALID_DETAIL_PAYLOAD'
  | 'FAILED_TO_LOAD_DETAIL';

export interface ValidatedScoreDetailResult {
  valid: true;
  detail: CandidateScoreDetail;
  orderId: string;
}

export interface InvalidScoreDetailResult {
  valid: false;
  errorCode: QualityScoreErrorCode;
  errorMessage: string;
}

export type ScoreDetailValidationResult = ValidatedScoreDetailResult | InvalidScoreDetailResult;
