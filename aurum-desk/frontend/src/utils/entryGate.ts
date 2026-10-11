/**
 * V14.2 entry gate helpers (pure, no network, no side effects).
 *
 * The backend entry guard is the only authority for "can this plan be armed/filled".
 * These helpers only decide how a SELECTED plan is presented:
 *  - whether the cached backend eligibility still belongs to the selected plan (binding),
 *  - the single concise decision view (WAITING / BLOCKED / CAN_ARM / ARMED / OPEN),
 *  - the distance label (never a fake 0.00 for an unconfirmed entry).
 *
 * Nothing here derives "allowed" from quality scores, checklists or UI state.
 */

export type EntryDirection = 'LONG' | 'SHORT';
export type PlanKind = 'PROVISIONAL' | 'CONFIRMED' | 'DRAFT' | 'ORDER';

export interface PlanBinding {
  setup_id?: string | null;
  setup_instance_id?: string | null;
  setup_version?: number | null;
  direction?: EntryDirection | string | null;
  plan_fingerprint?: string | null;
  risk_config_version?: number | null;
  lesson_policy_version?: number | null;
  news_evaluated_at_ms?: number | null;
}

export interface EntryBlocker {
  code: string;
  message_vi: string;
  next_step_vi?: string;
}

/** Normalized backend eligibility (legacy keys kept as aliases by the API). */
export interface EntryEligibility {
  eligible?: boolean;
  can_arm?: boolean;
  can_execute_now?: boolean;
  allowed?: boolean;
  stage?: string;
  evaluated_at?: number;
  plan_kind?: PlanKind;
  reason_codes?: string[];
  block_reasons?: string[];
  blockers?: EntryBlocker[];
  warnings?: string[];
  news_view?: NewsView | null;
  binding?: PlanBinding;
  plan_fingerprint?: string | null;
  setup_id?: string;
  setup_instance_id?: string;
  version?: number;
  revision?: number;
  direction?: EntryDirection | string;
  resolved_entry?: number | null;
  resolved_sl?: number | null;
  resolved_tp?: number | null;
  lesson_blockers?: unknown[];
  lesson_warnings?: unknown[];
  lesson_advisories?: unknown[];
  lesson_items?: unknown[];
}

export interface NewsView {
  is_blackout: boolean;
  evaluated_at_ms?: number;
  event_id?: string | null;
  title?: string | null;
  currency?: string | null;
  impact?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  remaining_minutes?: number;
  upcoming_title?: string | null;
  upcoming_scheduled_ms?: number | null;
  calendar_status?: 'AVAILABLE' | 'EMPTY' | 'UNKNOWN' | string;
  calendar_updated_at_ms?: number | null;
  reason?: string | null;
}

export interface BindableIntent {
  setup_id?: string;
  setup_instance_id?: string;
  revision?: number;
  direction: EntryDirection;
  plannedEntry: number;
  stopLoss: number;
  takeProfit?: number;
}

const PRICE_EPS = 0.005;

function samePrice(a: unknown, b: unknown): boolean {
  if (typeof a !== 'number' || typeof b !== 'number') return false;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  return Math.abs(a - b) < PRICE_EPS;
}

export function isSamePlan(a: PlanBinding, b: PlanBinding): boolean {
  return (
    a.setup_id === b.setup_id &&
    a.setup_instance_id === b.setup_instance_id &&
    a.setup_version === b.setup_version &&
    a.direction === b.direction &&
    a.plan_fingerprint === b.plan_fingerprint &&
    a.risk_config_version === b.risk_config_version
  );
}

/**
 * True only when the cached eligibility was computed for exactly the plan that is selected now:
 * same setup, instance, version, direction and price triple. Missing identity => NOT bound.
 * (The fingerprint is a backend identity; the frontend never recomputes it.)
 */
export function isEligibilityBoundToIntent(
  eligibility: EntryEligibility | null | undefined,
  intent: BindableIntent | null | undefined,
): boolean {
  if (!eligibility || !intent) return false;
  if (!intent.setup_id || eligibility.setup_id !== intent.setup_id) return false;
  if ((eligibility.setup_instance_id ?? null) !== (intent.setup_instance_id ?? null)) return false;
  const elVersion = eligibility.version ?? eligibility.revision;
  if (typeof elVersion !== 'number' || elVersion !== intent.revision) return false;
  if (eligibility.direction !== intent.direction) return false;
  if (!samePrice(eligibility.resolved_entry, intent.plannedEntry)) return false;
  if (!samePrice(eligibility.resolved_sl, intent.stopLoss)) return false;
  if (!samePrice(eligibility.resolved_tp, intent.takeProfit)) return false;
  return true;
}

export function getEntryDistanceLabel(input: {
  planKind: PlanKind;
  isQuoteFresh: boolean;
  last: number | null | undefined;
  entry: number | null | undefined;
}): string {
  if (input.planKind === 'PROVISIONAL') return 'Điểm vào chưa xác nhận';
  if (!input.isQuoteFresh) return 'Chờ báo giá mới';
  const { last, entry } = input;
  if (
    last == null || entry == null ||
    !Number.isFinite(last) || !Number.isFinite(entry) ||
    last <= 0 || entry <= 0
  ) {
    return 'Chưa có dữ liệu khoảng cách';
  }
  return `${Math.abs(last - entry).toFixed(2)} USDT`;
}

export type EntryDecisionStatus = 'WAITING' | 'BLOCKED' | 'CAN_ARM' | 'ARMED' | 'OPEN';

export interface EntryDecisionView {
  status: EntryDecisionStatus;
  label: string;
  primaryReason: string | null;
  primaryCode: string | null;
  canSubmit: boolean;
  isStale: boolean;
  /** CTA wording: pending order, never "already entered". */
  ctaKind: 'NONE' | 'ARM_PENDING' | 'ARM_MARKET' | 'MANAGE';
}

export interface DeriveInput {
  hasOpenPosition: boolean;
  hasArmedOrderForIntent: boolean;
  hasIntent: boolean;
  planKind: PlanKind;
  strategyState?: string | null;
  isQuoteFresh: boolean;
  eligibility: EntryEligibility | null | undefined;
  intent: BindableIntent | null | undefined;
}

const WAITING_STATES = new Set(['WAITING_MSS', 'WAITING_PRICE', 'WATCHING']);

function firstBlocker(e: EntryEligibility): { code: string | null; reason: string | null } {
  const b = e.blockers?.[0];
  if (b) return { code: b.code, reason: b.message_vi };
  const code = e.reason_codes?.[0] ?? null;
  const reason = e.block_reasons?.[0] ?? code;
  return { code, reason };
}

export function deriveEntryDecisionView(input: DeriveInput): EntryDecisionView {
  const base = { isStale: false } as const;

  if (input.hasOpenPosition) {
    return { ...base, status: 'OPEN', label: 'Đã mở vị thế', primaryReason: 'Đang quản lý vị thế đang mở.', primaryCode: null, canSubmit: false, ctaKind: 'MANAGE' };
  }
  if (input.hasArmedOrderForIntent) {
    return { ...base, status: 'ARMED', label: 'Đã đặt lệnh chờ', primaryReason: 'Lệnh chờ đã được đặt; hệ thống kiểm tra lại trước khi khớp.', primaryCode: null, canSubmit: false, ctaKind: 'MANAGE' };
  }
  if (!input.hasIntent || !input.intent) {
    return { ...base, status: 'WAITING', label: 'Đang chờ', primaryReason: 'Chưa chọn kế hoạch.', primaryCode: null, canSubmit: false, ctaKind: 'NONE' };
  }
  if (input.planKind === 'PROVISIONAL' || (input.strategyState && WAITING_STATES.has(input.strategyState))) {
    const reason = input.strategyState === 'WAITING_MSS'
      ? 'Chờ xác nhận phá vỡ cấu trúc.'
      : 'Điểm vào chưa xác nhận.';
    return { ...base, status: 'WAITING', label: 'Đang chờ', primaryReason: reason, primaryCode: 'UNCONFIRMED_ENTRY', canSubmit: false, ctaKind: 'NONE' };
  }
  const el = input.eligibility;
  if (!el) {
    return { ...base, status: 'WAITING', label: 'Đang chờ', primaryReason: 'Đang kiểm tra điều kiện vào lệnh…', primaryCode: null, canSubmit: false, ctaKind: 'NONE' };
  }
  if (!isEligibilityBoundToIntent(el, input.intent)) {
    return { isStale: true, status: 'WAITING', label: 'Đang chờ', primaryReason: 'Kế hoạch đã đổi so với lần kiểm tra gần nhất; đang kiểm tra lại.', primaryCode: 'STALE_ELIGIBILITY', canSubmit: false, ctaKind: 'NONE' };
  }
  if (!input.isQuoteFresh) {
    return { ...base, status: 'WAITING', label: 'Đang chờ', primaryReason: 'Chờ báo giá mới.', primaryCode: 'QUOTE_STALE', canSubmit: false, ctaKind: 'NONE' };
  }
  const blocked = el.can_arm !== true || (el.blockers?.length ?? 0) > 0;
  if (blocked) {
    const { code, reason } = firstBlocker(el);
    return { ...base, status: 'BLOCKED', label: 'Bị chặn', primaryReason: reason ?? 'Chưa đủ điều kiện vào lệnh.', primaryCode: code, canSubmit: false, ctaKind: 'NONE' };
  }
  return {
    ...base,
    status: 'CAN_ARM',
    label: 'Có thể đặt lệnh',
    primaryReason: el.can_execute_now ? 'Đủ điều kiện đặt lệnh.' : 'Đủ điều kiện đặt lệnh chờ (chưa khớp ngay).',
    primaryCode: null,
    canSubmit: true,
    ctaKind: el.can_execute_now ? 'ARM_MARKET' : 'ARM_PENDING',
  };
}
