import { describe, expect, it } from 'vitest';
import {
  deriveEntryDecisionView,
  getEntryDistanceLabel,
  isEligibilityBoundToIntent,
  isSamePlan,
} from './utils/entryGate';
import type { BindableIntent, EntryEligibility } from './utils/entryGate';
import { formatRR } from './utils/planLevels';
import { getCatalogTemplate } from './utils/userMessageCatalog';

const intent = (over: Partial<BindableIntent> = {}): BindableIntent => ({
  setup_id: 'setup-1',
  setup_instance_id: 'inst-1',
  revision: 3,
  direction: 'SHORT',
  plannedEntry: 4198.29,
  stopLoss: 4203.29,
  takeProfit: 4198.0,
  ...over,
});

const elig = (over: Partial<EntryEligibility> = {}): EntryEligibility => ({
  setup_id: 'setup-1',
  setup_instance_id: 'inst-1',
  version: 3,
  direction: 'SHORT',
  resolved_entry: 4198.29,
  resolved_sl: 4203.29,
  resolved_tp: 4198.0,
  can_arm: true,
  can_execute_now: false,
  eligible: true,
  reason_codes: [],
  blockers: [],
  ...over,
});

const baseInput = (over = {}) => ({
  hasOpenPosition: false,
  hasArmedOrderForIntent: false,
  hasIntent: true,
  planKind: 'CONFIRMED' as const,
  strategyState: 'READY',
  isQuoteFresh: true,
  eligibility: elig(),
  intent: intent(),
  ...over,
});

describe('V14.2 eligibility binding', () => {
  it('binds only when identity, version, direction and the whole price triple match', () => {
    expect(isEligibilityBoundToIntent(elig(), intent())).toBe(true);
    expect(isEligibilityBoundToIntent(elig({ version: 4 }), intent())).toBe(false);
    expect(isEligibilityBoundToIntent(elig({ direction: 'LONG' }), intent())).toBe(false);
    expect(isEligibilityBoundToIntent(elig({ setup_instance_id: 'inst-2' }), intent())).toBe(false);
    expect(isEligibilityBoundToIntent(elig({ setup_id: 'other' }), intent())).toBe(false);
  });

  it.each([
    ['entry', { plannedEntry: 4198.4 }],
    ['stop loss', { stopLoss: 4203.9 }],
    ['take profit', { takeProfit: 4190.0 }],
  ])('a dragged %s makes the cached eligibility stale', (_n, change) => {
    expect(isEligibilityBoundToIntent(elig(), intent(change as Partial<BindableIntent>))).toBe(false);
  });

  it('treats missing eligibility or intent as not bound (never green)', () => {
    expect(isEligibilityBoundToIntent(undefined, intent())).toBe(false);
    expect(isEligibilityBoundToIntent(elig(), null)).toBe(false);
    expect(isEligibilityBoundToIntent(elig({ version: undefined, revision: undefined }), intent())).toBe(false);
    expect(isEligibilityBoundToIntent(elig({ resolved_tp: undefined }), intent())).toBe(false);
  });

  it('accepts null instance ids on both sides and legacy `revision` alias', () => {
    expect(
      isEligibilityBoundToIntent(
        elig({ setup_instance_id: undefined, version: undefined, revision: 3 }),
        intent({ setup_instance_id: undefined }),
      ),
    ).toBe(true);
  });

  it('isSamePlan compares every identity field', () => {
    const a = { setup_id: 's', setup_instance_id: 'i', setup_version: 1, direction: 'LONG', plan_fingerprint: 'f', risk_config_version: 1 };
    expect(isSamePlan(a, { ...a })).toBe(true);
    expect(isSamePlan(a, { ...a, direction: 'SHORT' })).toBe(false);
    expect(isSamePlan(a, { ...a, plan_fingerprint: 'g' })).toBe(false);
    expect(isSamePlan(a, { ...a, risk_config_version: 2 })).toBe(false);
  });
});

describe('V14.2 distance label', () => {
  it('never shows a numeric distance for a provisional entry', () => {
    expect(getEntryDistanceLabel({ planKind: 'PROVISIONAL', isQuoteFresh: true, last: 4198.29, entry: 4198.29 })).toBe('Điểm vào chưa xác nhận');
  });
  it('asks for a fresh quote when the feed is stale', () => {
    expect(getEntryDistanceLabel({ planKind: 'CONFIRMED', isQuoteFresh: false, last: 4200, entry: 4198 })).toBe('Chờ báo giá mới');
  });
  it('formats a confirmed distance and handles missing/invalid numbers', () => {
    expect(getEntryDistanceLabel({ planKind: 'CONFIRMED', isQuoteFresh: true, last: 4200.5, entry: 4198 })).toBe('2.50 USDT');
    expect(getEntryDistanceLabel({ planKind: 'DRAFT', isQuoteFresh: true, last: null, entry: 4198 })).toBe('Chưa có dữ liệu khoảng cách');
    expect(getEntryDistanceLabel({ planKind: 'CONFIRMED', isQuoteFresh: true, last: NaN, entry: 4198 })).toBe('Chưa có dữ liệu khoảng cách');
    expect(getEntryDistanceLabel({ planKind: 'CONFIRMED', isQuoteFresh: true, last: 0, entry: 4198 })).toBe('Chưa có dữ liệu khoảng cách');
  });
});

describe('V14.2 decision view', () => {
  it('CAN_ARM only with a bound, unblocked decision and a fresh quote', () => {
    const v = deriveEntryDecisionView(baseInput());
    expect(v.status).toBe('CAN_ARM');
    expect(v.canSubmit).toBe(true);
    expect(v.ctaKind).toBe('ARM_PENDING'); // can_execute_now=false -> pending order, not "entered"
  });

  it('uses a market CTA only when the backend says execute-now', () => {
    const v = deriveEntryDecisionView(baseInput({ eligibility: elig({ can_execute_now: true }) }));
    expect(v.ctaKind).toBe('ARM_MARKET');
  });

  it('missing eligibility is WAITING, never a success', () => {
    const v = deriveEntryDecisionView(baseInput({ eligibility: undefined }));
    expect(v.status).toBe('WAITING');
    expect(v.canSubmit).toBe(false);
  });

  it('a stale (unbound) eligibility cannot submit and is flagged stale', () => {
    const v = deriveEntryDecisionView(baseInput({ eligibility: elig({ version: 2 }) }));
    expect(v.canSubmit).toBe(false);
    expect(v.isStale).toBe(true);
    expect(v.primaryCode).toBe('STALE_ELIGIBILITY');
  });

  it('backend blockers win and carry the backend reason', () => {
    const v = deriveEntryDecisionView(baseInput({
      eligibility: elig({
        can_arm: false,
        eligible: false,
        reason_codes: ['NEWS_BLACKOUT'],
        blockers: [{ code: 'NEWS_BLACKOUT', message_vi: 'Tin High Impact: CPI' }],
      }),
    }));
    expect(v.status).toBe('BLOCKED');
    expect(v.primaryCode).toBe('NEWS_BLACKOUT');
    expect(v.primaryReason).toBe('Tin High Impact: CPI');
    expect(v.canSubmit).toBe(false);
  });

  it('can_arm undefined (legacy payload) is not treated as true', () => {
    const v = deriveEntryDecisionView(baseInput({ eligibility: elig({ can_arm: undefined, eligible: true }) }));
    expect(v.canSubmit).toBe(false);
  });

  it('provisional plan and WAITING_MSS never become submit-able', () => {
    const p = deriveEntryDecisionView(baseInput({ planKind: 'PROVISIONAL' }));
    expect(p.status).toBe('WAITING');
    expect(p.primaryCode).toBe('UNCONFIRMED_ENTRY');
    expect(p.canSubmit).toBe(false);
    const m = deriveEntryDecisionView(baseInput({ strategyState: 'WAITING_MSS' }));
    expect(m.primaryReason).toBe('Chờ xác nhận phá vỡ cấu trúc.');
    expect(m.canSubmit).toBe(false);
  });

  it('stale quote waits even if eligibility is green', () => {
    const v = deriveEntryDecisionView(baseInput({ isQuoteFresh: false }));
    expect(v.status).toBe('WAITING');
    expect(v.primaryCode).toBe('QUOTE_STALE');
    expect(v.canSubmit).toBe(false);
  });

  it('armed order and open position switch to management views', () => {
    const a = deriveEntryDecisionView(baseInput({ hasArmedOrderForIntent: true }));
    expect(a.status).toBe('ARMED');
    expect(a.canSubmit).toBe(false);
    const o = deriveEntryDecisionView(baseInput({ hasOpenPosition: true }));
    expect(o.status).toBe('OPEN');
    expect(o.canSubmit).toBe(false);
  });

  it('SHORT screenshot plan: geometry is valid but a stale/blocked decision still cannot submit', () => {
    // TP < entry < SL is valid SHORT geometry; economics are decided by the backend decision only.
    const i = intent();
    expect(i.takeProfit! < i.plannedEntry && i.plannedEntry < i.stopLoss).toBe(true);
    const v = deriveEntryDecisionView(baseInput({
      eligibility: elig({
        can_arm: false,
        blockers: [{ code: 'NET_REWARD_NON_POSITIVE', message_vi: 'Lợi nhuận ròng kỳ vọng ≤ 0' }],
      }),
    }));
    expect(v.status).toBe('BLOCKED');
    expect(v.primaryCode).toBe('NET_REWARD_NON_POSITIVE');
  });
});

describe('V14.2 signed R:R and catalog', () => {
  it('keeps zero and negative R:R visible', () => {
    expect(formatRR(0)).toBe('1:0.00');
    expect(formatRR(-0.37)).toBe('1:-0.37');
    expect(formatRR(undefined)).toBe('—');
  });

  it('catalog has a human title for every new guard code (not the UNKNOWN fallback)', () => {
    for (const code of ['NEWS_BLACKOUT', 'NET_REWARD_NON_POSITIVE', 'ENTRY_GUARD_DATA_UNAVAILABLE', 'UNCONFIRMED_ENTRY', 'STALE_ELIGIBILITY', 'QUOTE_STALE']) {
      const t = getCatalogTemplate(code);
      expect(t.code).toBe(code);
      expect(t.title.length).toBeGreaterThan(5);
      expect(t.next_steps.length).toBeGreaterThan(5);
    }
  });
});
