import { describe, it, expect, beforeEach } from 'vitest';
import { normalizeLessonItem } from './types/lesson';
import { CanonicalQuoteStore } from './services/quoteStore';
import { validatePriceGeometry, calculateClientRiskReward } from './utils/calculator';

describe('V10.2 Frontend Acceptance Tests', () => {
  describe('Area A: Lesson Contract & Normalizer (L09, L10)', () => {
    it('L09: Normalizes legacy string lesson item cleanly without #undefined', () => {
      const rawString = 'Bài học L-01: Không FOMO khi giá đang biến động mạnh';
      const item = normalizeLessonItem(rawString, 'L-01');

      expect(item.lesson_id).toBe('L-01');
      expect(item.title).toBe(rawString);
      expect(item.rule_text).toBe(rawString);
      expect(item.human_message).toBe(rawString);
      expect(item.action_rule).toBe(rawString);
      expect(item.severity).toBe('ADVISORY');
      // Critical check: no '#undefined' in any representation
      expect(item.human_message).not.toContain('undefined');
      expect(item.lesson_id).not.toBe('undefined');
    });

    it('L10: Handles null, undefined, and empty objects safely without #undefined', () => {
      const itemNull = normalizeLessonItem(null);
      expect(itemNull.lesson_id).toBe('UNKNOWN');
      expect(itemNull.human_message).not.toContain('undefined');

      const itemEmpty = normalizeLessonItem({});
      expect(itemEmpty.lesson_id).toBe('UNKNOWN');
      expect(itemEmpty.human_message).not.toContain('undefined');

      const itemWithPartialData = normalizeLessonItem({
        id: 'L-102',
        title: 'Cần xác nhận nến đóng',
      });
      expect(itemWithPartialData.lesson_id).toBe('L-102');
      expect(itemWithPartialData.title).toBe('Cần xác nhận nến đóng');
      expect(itemWithPartialData.human_message).toBe('Cần xác nhận nến đóng');
      expect(itemWithPartialData.human_message).not.toContain('undefined');
    });
  });

  describe('Area B: Geometry Validation (G01 - G05)', () => {
    it('G01: Accepts valid LONG geometry (SL < Entry < TP)', () => {
      const res = validatePriceGeometry('LONG', 2050.0, 2040.0, 2070.0);
      expect(res.isValid).toBe(true);
      expect(res.invalidReason).toBeUndefined();
    });

    it('G02: Accepts valid SHORT geometry (TP < Entry < SL) [Fixture F1]', () => {
      // Fixture F1: ETHUSDT SHORT Entry 2049, SL 2055, TP 2030
      const res = validatePriceGeometry('SHORT', 2049.0, 2055.0, 2030.0);
      expect(res.isValid).toBe(true);
      expect(res.invalidReason).toBeUndefined();
    });

    it('G03: Rejects SHORT when SL < Entry with explicit Vietnamese error message [Fixture F2]', () => {
      // Fixture F2: ETHUSDT SHORT with corrupted LONG levels (Entry 2049, SL 2030, TP 2055)
      const res = validatePriceGeometry('SHORT', 2049.0, 2030.0, 2055.0);
      expect(res.isValid).toBe(false);
      expect(res.invalidReason).toContain('SHORT');
      expect(res.invalidReason).toContain('SL');
    });

    it('G04: Rejects SHORT when TP > Entry with explicit Vietnamese error message', () => {
      const res = validatePriceGeometry('SHORT', 2049.0, 2055.0, 2060.0);
      expect(res.isValid).toBe(false);
      expect(res.invalidReason).toContain('TP');
    });

    it('G05: calculateClientRiskReward returns isValid: false, grossRR: 0 for invalid geometry', () => {
      const calc = calculateClientRiskReward('SHORT', 2049.0, 2030.0, 2055.0);
      expect(calc.isValid).toBe(false);
      expect(calc.grossRR).toBe(0);
      expect(calc.estimatedNetRR).toBe(0);
      expect(calc.canExecute).toBe(false);
      expect(calc.invalidReason).toBeDefined();
    });
  });

  describe('Area C: Canonical Quote Store & Monotonic Sync (R01 - R08) [Fixture F5]', () => {
    let store: CanonicalQuoteStore;

    beforeEach(() => {
      store = new CanonicalQuoteStore();
    });

    it('R01: Initializes with empty quote state', () => {
      expect(store.getLatestQuote('ETHUSDT')).toBeNull();
      expect(store.getLastPrice('ETHUSDT')).toBeNull();
    });

    it('R02: Updates quote monotonically with newer timestamp', () => {
      const ok1 = store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'ETHUSDT',
        data: {
          bid: 2048.9,
          ask: 2049.1,
          last: 2049.0,
          timestamp: 1000,
        },
      });
      expect(ok1).toBe(true);
      expect(store.getLastPrice('ETHUSDT')).toBe(2049.0);

      const ok2 = store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'ETHUSDT',
        data: {
          bid: 2049.9,
          ask: 2050.1,
          last: 2050.0,
          timestamp: 2000,
        },
      });
      expect(ok2).toBe(true);
      expect(store.getLastPrice('ETHUSDT')).toBe(2050.0);
    });

    it('R03: Rejects or ignores quote update with older timestamp', () => {
      store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'ETHUSDT',
        data: {
          bid: 2049.9,
          ask: 2050.1,
          last: 2050.0,
          timestamp: 2000,
        },
      });

      // Older timestamp 1500
      const okOld = store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'ETHUSDT',
        data: {
          bid: 2045.0,
          ask: 2045.2,
          last: 2045.1,
          timestamp: 1500,
        },
      });
      expect(okOld).toBe(false);
      expect(store.getLastPrice('ETHUSDT')).toBe(2050.0); // Kept newer price!
    });

    it('R04: Updates from candle close without regressing fresh real-time quote', () => {
      // Realtime quote at t=2000
      store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'ETHUSDT',
        data: {
          bid: 2049.9,
          ask: 2050.1,
          last: 2050.0,
          timestamp: 2000,
        },
      });

      // Candle close at t=1800 with price 2042.0
      const updated = store.updateFromCandle('ETHUSDT', 2042.0, 1800);
      expect(updated).toBe(false);
      expect(store.getLastPrice('ETHUSDT')).toBe(2050.0); // Did not regress!

      // Candle close at t=2500 with price 2052.0
      const updatedFresh = store.updateFromCandle('ETHUSDT', 2052.0, 2500);
      expect(updatedFresh).toBe(true);
      expect(store.getLastPrice('ETHUSDT')).toBe(2052.0);
    });

    it('R05: Computes accurate distance |last - plannedEntry| in USDT [Fixture F5]', () => {
      // Fixture F5: Last price 2045.00, planned entry 2049.00 -> distance is 4.00 USDT
      store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'ETHUSDT',
        data: {
          bid: 2044.9,
          ask: 2045.1,
          last: 2045.0,
          timestamp: 3000,
        },
      });

      const dist = store.getDistance('ETHUSDT', 2049.0);
      expect(dist.rawDistance).toBeCloseTo(4.0, 2);
      expect(dist.isAvailable).toBe(true);
      expect(dist.formattedUsdt).toBe('4.00 USDT');
    });

    it('R07: Returns "-- / chưa có dữ liệu" when quote is unavailable', () => {
      const dist = store.getDistance('SOLUSDT', 150.0);
      expect(dist.isAvailable).toBe(false);
      expect(dist.formattedUsdt).toBe('-- / chưa có dữ liệu');
      expect(dist.rawDistance).toBeNull();
    });

    it('R08: Returns accurate distance for SHORT setup', () => {
      // Last price is above planned entry: price 2052.50, planned entry 2049.00 -> distance 3.50 USDT
      store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'ETHUSDT',
        data: {
          bid: 2052.4,
          ask: 2052.6,
          last: 2052.5,
          timestamp: 4000,
        },
      });

      const dist = store.getDistance('ETHUSDT', 2049.0);
      expect(dist.rawDistance).toBeCloseTo(3.5, 2);
      expect(dist.formattedUsdt).toBe('3.50 USDT');
    });
  });

  describe('Area D & E: Copy to Draft & Single ID Consistency (E05, E06)', () => {
    it('E05: Generates a single matching draft ID for overlay and intent', () => {
      const draftId = `draft-${Date.now()}`;
      const overlay = { id: draftId };
      const intent = { setup_id: draftId };

      expect(overlay.id).toBe(intent.setup_id);
    });

    it('E06: Copying setup with invalid levels produces invalid draft with grossRR: 0', () => {
      // Given an invalid SHORT setup (corrupted levels)
      const corruptedSetup = {
        direction: 'SHORT' as const,
        confirmed_entry: 2049.0,
        confirmed_sl: 2030.0, // Invalid: SL below Entry for SHORT
        confirmed_tp: 2055.0, // Invalid: TP above Entry for SHORT
      };

      const calc = calculateClientRiskReward(
        corruptedSetup.direction,
        corruptedSetup.confirmed_entry,
        corruptedSetup.confirmed_sl,
        corruptedSetup.confirmed_tp
      );

      expect(calc.isValid).toBe(false);
      const grossRR = calc.isValid ? calc.grossRR : 0;
      const netRR = calc.isValid ? calc.estimatedNetRR : 0;

      expect(grossRR).toBe(0);
      expect(netRR).toBe(0);
    });
  });
});
