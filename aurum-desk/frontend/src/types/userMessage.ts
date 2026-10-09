/**
 * Presentation model for user-facing messages, notifications, and error diagnostics.
 * Designed to make all trading actions and errors understandable for beginners while
 * preserving machine semantic codes for developers and logs.
 */

export type MessageSeverity = 'info' | 'success' | 'warning' | 'error';

export type OutcomeCertainty = 'confirmed' | 'pending' | 'unknown' | 'estimated';

export type SemanticActionType =
  | 'OPEN_POSITION'
  | 'VIEW_PENDING_ORDER'
  | 'REFRESH_SETUP'
  | 'OPEN_NEWS'
  | 'OPEN_SETTINGS'
  | 'OPEN_TELEGRAM_HISTORY'
  | 'VIEW_TRADE'
  | 'RECONCILE_STATUS'
  | 'STOP_TRADING'
  | 'VIEW_BLOCKERS'
  | 'ACKNOWLEDGE'
  | 'DISMISS';

export interface SemanticAction {
  type: SemanticActionType;
  label: string;
  payload?: Record<string, any>;
}

export type SemanticActionInput = SemanticAction | SemanticActionType;

export interface TechnicalDetails {
  code?: string;
  http_status?: number;
  correlation_id?: string;
  timestamp?: number;
  operation?: string;
  raw_error?: string;
  field_errors?: Array<{ field: string; message: string }>;
  [key: string]: any;
}

export interface UserMessage {
  id: string;
  code: string;
  severity: MessageSeverity;
  title: string;
  summary: string;
  explanation?: string;
  impact?: string;
  next_steps?: string;
  params?: Record<string, any>;
  action?: SemanticActionInput;
  operation?: string;
  entity_id?: string;
  event_id?: string;
  dedupe_key?: string;
  occurred_at: number;
  retryable?: boolean;
  outcome_certainty: OutcomeCertainty;
  technical_details?: TechnicalDetails;
  read?: boolean;
}
