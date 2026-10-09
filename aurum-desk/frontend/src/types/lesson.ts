export type LessonSeverity = 'INFO' | 'WARN' | 'CRITICAL' | 'ADVISORY' | 'WARNING' | 'BLOCKER';
export type LessonEffect = 'ANNOTATE' | 'WARN_ENTRY' | 'BLOCK_ENTRY' | 'PROPOSE_PLAN_ADJUSTMENT';

export interface LessonDecisionItem {
  rule_id: number | string;
  rule_version: number;
  lesson_id?: number | string | null;
  severity: LessonSeverity;
  effect: LessonEffect;
  title: string;
  message: string;
  next_step?: string | null;
  matched: boolean;
  evaluated: boolean;
  data_unavailable?: boolean;
  would_block?: boolean;
  effective_block?: boolean;
  reason_code?: string | null;
  evaluated_at: number;
  metric_value?: any;
  symbol?: string;
  direction?: string;
  timeframe?: string;
  setup_instance_id?: string;
  revision?: number;
}

export interface NormalizedLessonItem {
  rule_id?: number | string;
  lesson_id?: number | string;
  rule_version?: number;
  severity: LessonSeverity;
  effect: LessonEffect;
  title: string;
  message: string;
  human_message?: string;
  rule_text?: string;
  action_rule?: string;
  next_step?: string;
  matched: boolean;
  evaluated: boolean;
  effective_block: boolean;
  would_block: boolean;
  reason_code?: string;
  evaluated_at?: number;
  isLegacy?: boolean;
}

export function normalizeLessonItem(raw: any, fallbackId?: string): NormalizedLessonItem {
  if (raw === null || raw === undefined) {
    const id = fallbackId || 'UNKNOWN';
    return {
      rule_id: id,
      lesson_id: id,
      severity: 'INFO',
      effect: 'ANNOTATE',
      title: 'Thông tin bài học chưa đầy đủ',
      message: 'Thông tin bài học chưa đầy đủ',
      human_message: 'Thông tin bài học chưa đầy đủ',
      rule_text: 'Thông tin bài học chưa đầy đủ',
      action_rule: 'Thông tin bài học chưa đầy đủ',
      matched: false,
      evaluated: false,
      effective_block: false,
      would_block: false,
    };
  }

  // Legacy string format support (L02, L09)
  if (typeof raw === 'string') {
    const text = raw.trim() || 'Thông tin bài học chưa đầy đủ';
    const id = fallbackId || 'LESSON';
    return {
      rule_id: id,
      lesson_id: id,
      severity: 'ADVISORY',
      effect: 'ANNOTATE',
      title: text,
      message: text,
      human_message: text,
      rule_text: text,
      action_rule: text,
      matched: true,
      evaluated: true,
      effective_block: false,
      would_block: false,
      isLegacy: true,
    };
  }

  // Structured DTO format (L01, L03, L04)
  const rawSev = String(raw.severity || '').toUpperCase();
  const severity: LessonSeverity =
    rawSev === 'CRITICAL' || rawSev === 'BLOCKER' || raw.effect === 'BLOCK_ENTRY'
      ? 'CRITICAL'
      : rawSev === 'WARN' || rawSev === 'WARNING' || raw.effect === 'WARN_ENTRY'
      ? 'WARN'
      : 'INFO';

  const rawEffect = String(raw.effect || '').toUpperCase();
  const effect: LessonEffect =
    rawEffect === 'BLOCK_ENTRY' || severity === 'CRITICAL'
      ? 'BLOCK_ENTRY'
      : rawEffect === 'WARN_ENTRY' || severity === 'WARN'
      ? 'WARN_ENTRY'
      : rawEffect === 'PROPOSE_PLAN_ADJUSTMENT'
      ? 'PROPOSE_PLAN_ADJUSTMENT'
      : 'ANNOTATE';

  const title = String(raw.title || raw.human_message || raw.message || raw.rule_text || 'Thông tin bài học chưa đầy đủ');
  const message = String(raw.message || raw.human_message || raw.title || raw.rule_text || 'Thông tin bài học chưa đầy đủ');
  const next_step = raw.next_step ? String(raw.next_step) : raw.action_rule ? String(raw.action_rule) : undefined;

  const rule_id =
    raw.rule_id !== undefined && raw.rule_id !== null
      ? raw.rule_id
      : raw.lesson_id !== undefined && raw.lesson_id !== null
      ? raw.lesson_id
      : raw.id !== undefined && raw.id !== null
      ? raw.id
      : fallbackId || 'UNKNOWN';
  const lesson_id =
    raw.lesson_id !== undefined && raw.lesson_id !== null
      ? raw.lesson_id
      : raw.id !== undefined && raw.id !== null
      ? raw.id
      : rule_id;

  const effective_block = Boolean(raw.effective_block || (severity === 'CRITICAL' && (raw.would_block || raw.matched)));
  const would_block = Boolean(raw.would_block || effective_block);

  return {
    rule_id,
    lesson_id,
    rule_version: typeof raw.rule_version === 'number' ? raw.rule_version : 1,
    severity,
    effect,
    title,
    message,
    human_message: message,
    rule_text: title,
    action_rule: next_step || title,
    next_step,
    matched: Boolean(raw.matched ?? true),
    evaluated: Boolean(raw.evaluated ?? true),
    effective_block,
    would_block,
    reason_code: raw.reason_code ? String(raw.reason_code) : undefined,
    evaluated_at: typeof raw.evaluated_at === 'number' ? raw.evaluated_at : undefined,
    isLegacy: false,
  };
}
