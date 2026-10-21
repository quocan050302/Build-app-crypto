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
  score?: number;
  max_score?: number;
  weight?: number;
  status?: string;
  importance?: string;
  explanation_vi?: string;
  observed?: any;
  expected?: any;
}

export interface CandidateScoreDetail extends CandidateScoreSummary {
  factors: CandidateScoreFactor[];
  group_scores: Record<string, Record<string, number>>;
  missing_data: string[];
  blockers: string[];
}
