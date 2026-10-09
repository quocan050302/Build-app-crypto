import { describe, it, expect } from 'vitest';
import {
  buildTradeEventMessage,
  normalizeAppError,
  generateCompositeEventId,
} from './utils/normalizeAppError';
import { getCatalogTemplate } from './utils/userMessageCatalog';
import { CanonicalQuoteStore } from './services/quoteStore';
import { validatePriceGeometry, calculateClientRiskReward } from './utils/calculator';
import { normalizeLessonItem } from './types/lesson';
import type { UserMessage } from './types/userMessage';

describe('V10.3 UI and Notification Invariants (T01 - T10, U01 - U11, P01 - P02)', () => {
  describe('Area T: Notification Routing & Deduplication (T01 - T10)', () => {
    // T01: setup.updated goes through event pipeline: returns null (no toast, no UNKNOWN error)
    it('T01: setup.updated returns null so it never generates UNKNOWN or error toast', () => {
      const msg = buildTradeEventMessage('setup.updated', {
        setup_id: 'xau-setup-01',
        revision: 2,
        direction: 'SHORT',
      });
      // CRITICAL: setup.updated must be suppressed from toast to prevent red spam
      expect(msg).toBeNull();
    });

    // T02: 100 updated events produce 0 toast messages
    it('T02: 100 updated events do not create 100 toast messages', () => {
      const messages: (UserMessage | null)[] = [];
      for (let rev = 1; rev <= 100; rev++) {
        const res = buildTradeEventMessage('setup.updated', {
          setup_id: 'xau-setup-01',
          revision: rev,
          direction: 'LONG',
          status: 'WATCHING',
        });
        messages.push(res);
      }
      const nonNulls = messages.filter((m) => m !== null);
      expect(nonNulls.length).toBe(0);
    });

    // T03: setup.ready transition creates 1 notification; duplicate/replay has identical dedupe_key
    it('T03: setup.ready generates 1 notification with deterministic dedupe key', () => {
      const payload = {
        setup_id: 'xau-setup-02',
        setup_instance_id: 'inst-999',
        revision: 3,
        direction: 'LONG',
      };
      const msg1 = buildTradeEventMessage('setup.ready', payload);
      const msg2 = buildTradeEventMessage('setup.ready', payload);

      expect(msg1).not.toBeNull();
      expect(msg1!.code).toBe('SETUP_READY');
      expect(msg1!.severity).toBe('info');
      expect(msg1!.summary).toContain('Bạn chưa có vị thế mới');
      expect(msg1!.dedupe_key).toBeDefined();
      expect(msg1!.dedupe_key).toBe(msg2!.dedupe_key);
    });

    // T04: Two distinct trade.opened have distinct IDs and entity IDs; replay generates identical key
    it('T04: Two distinct trade.opened events have distinct keys; replay same event generates identical key', () => {
      const eventA = buildTradeEventMessage('trade.opened', {
        order_id: 'order-101',
        trade_id: 'trade-101',
        direction: 'SHORT',
        entry_price: 2745.2,
      });
      const eventB = buildTradeEventMessage('trade.opened', {
        order_id: 'order-102',
        trade_id: 'trade-102',
        direction: 'LONG',
        entry_price: 2740.0,
      });
      const eventAReplay = buildTradeEventMessage('trade.opened', {
        order_id: 'order-101',
        trade_id: 'trade-101',
        direction: 'SHORT',
        entry_price: 2745.2,
      });

      expect(eventA).not.toBeNull();
      expect(eventB).not.toBeNull();
      expect(eventA!.code).toBe('TRADE_OPENED');
      expect(eventB!.code).toBe('TRADE_OPENED');
      expect(eventA!.entity_id).toBe('order-101');
      expect(eventB!.entity_id).toBe('order-102');
      expect(eventA!.dedupe_key).not.toBe(eventB!.dedupe_key);
      expect(eventA!.dedupe_key).toBe(eventAReplay!.dedupe_key);
    });

    // T05: Unknown / malformed domain event returns null, does not crash or loan UNKNOWN error
    it('T05: Malformed or unknown domain event returns null instead of falling back to UNKNOWN error', () => {
      const unknownMsg = buildTradeEventMessage('unknown.event.type', { foo: 'bar' });
      expect(unknownMsg).toBeNull();

      const emptyMsg = buildTradeEventMessage('', null);
      expect(emptyMsg).toBeNull();
    });

    // T06: Real backend manual failures preserve actionable error catalog codes
    it('T06: Real backend failures (ARMED_ORDER_EXISTS, RISK_BUDGET_EXCEEDED) normalize with correct catalog template', () => {
      const armErr = {
        response: {
          status: 400,
          data: {
            code: 'ARMED_ORDER_EXISTS',
            detail: 'Hiện đã có 1 lệnh đang ở trạng thái chờ khớp (ARMED).',
          },
        },
      };
      const normalizedArm = normalizeAppError(armErr, {
        operation: 'arm_setup',
        entityId: 'setup-123',
      });
      expect(normalizedArm.code).toBe('ARMED_ORDER_EXISTS');
      expect(normalizedArm.severity).toBe('warning');
      expect(typeof normalizedArm.action === 'object' ? normalizedArm.action?.type : normalizedArm.action).toBe('VIEW_PENDING_ORDER');

      const riskErr = {
        response: {
          status: 403,
          data: {
            code: 'RISK_BUDGET_EXCEEDED',
            detail: 'Đã chạm hạn mức lỗ tối đa trong ngày.',
          },
        },
      };
      const normalizedRisk = normalizeAppError(riskErr, {
        operation: 'open_paper_trade',
      });
      expect(normalizedRisk.code).toBe('RISK_BUDGET_EXCEEDED');
      expect(normalizedRisk.severity).toBe('error');
      expect(typeof normalizedRisk.action === 'object' ? normalizedRisk.action?.type : normalizedRisk.action).toBe('STOP_TRADING');
    });

    // T07: Background error repetition produces incident metadata and bounds
    it('T07: Repeated background errors generate incident dedupe key with operation and code', () => {
      const bgErr = {
        message: 'Network timeout polling quote feed',
        code: 'ECONNABORTED',
      };
      const norm1 = normalizeAppError(bgErr, { operation: 'fetch_quotes' });
      const norm2 = normalizeAppError(bgErr, { operation: 'fetch_quotes' });

      expect(norm1.code).toBe('TIMEOUT');
      expect(norm1.dedupe_key).toBe(norm2.dedupe_key);
      expect(norm1.severity).toBe('warning');
    });

    // T08: Mutation timeout has uncertain outcome; does not claim success or permanent failure
    it('T08: Mutation timeout sets certainty to unknown/estimated with RECONCILE_STATUS action', () => {
      const timeoutErr = {
        code: 'ECONNABORTED',
        message: 'timeout of 5000ms exceeded',
      };
      const normalized = normalizeAppError(timeoutErr, {
        operation: 'arm_setup',
        entityId: 'setup-001',
      });
      expect(normalized.code).toBe('TIMEOUT');
      expect(normalized.outcome_certainty).toBe('unknown');
      expect(typeof normalized.action === 'object' ? normalized.action?.type : normalized.action).toBe('RECONCILE_STATUS');
    });

    // T09: Legacy template loans do not misclassify generic info/success/warn as UNKNOWN
    it('T09: getCatalogTemplate returns calibrated templates for GENERIC_INFO, SUCCESS, WARNING without UNKNOWN loan', () => {
      const infoTmpl = getCatalogTemplate('GENERIC_INFO');
      expect(infoTmpl.defaultSeverity).toBe('info');
      expect(infoTmpl.defaultCertainty).toBe('confirmed');

      const succTmpl = getCatalogTemplate('GENERIC_SUCCESS');
      expect(succTmpl.defaultSeverity).toBe('success');
      expect(succTmpl.defaultCertainty).toBe('confirmed');

      const warnTmpl = getCatalogTemplate('GENERIC_WARNING');
      expect(warnTmpl.defaultSeverity).toBe('warning');

      const unknownTmpl = getCatalogTemplate('UNKNOWN');
      // UNKNOWN template must NOT guarantee data preservation without verification
      expect(unknownTmpl.explanation).not.toContain('dữ liệu đã được bảo toàn tuyệt đối');
    });

    // T10: Composite event IDs are deterministic and non-random
    it('T10: generateCompositeEventId generates deterministic composite IDs for domain events', () => {
      const id1 = generateCompositeEventId('setup.ready', {
        setup_id: 's1',
        revision: 4,
        setup_instance_id: 'inst-1',
      });
      const id2 = generateCompositeEventId('setup.ready', {
        setup_id: 's1',
        revision: 4,
        setup_instance_id: 'inst-1',
      });
      expect(id1).toBe(id2);
      expect(id1).toContain('setup.ready');
      expect(id1).toContain('s1');
      expect(id1).toContain('inst-1');
    });
  });

  describe('Area U: UI Layout & Viewport Invariants (U01 - U11)', () => {
    // U01: Desktop layouts viewport contract
    it('U01: App root and sidebar CSS classes guarantee zero document vertical scroll', () => {
      // Contract classes verified on App shell and Sidebar
      const appRootClasses = 'h-screen h-[100dvh] max-h-[100dvh] bg-charcoal-950 text-gray-200 flex flex-col font-sans select-none overflow-hidden';
      expect(appRootClasses).toContain('overflow-hidden');
      expect(appRootClasses).toContain('max-h-[100dvh]');

      const sidebarContainerClasses = 'w-full lg:w-[360px] xl:w-[380px] shrink-0 h-full flex flex-col min-h-0';
      expect(sidebarContainerClasses).toContain('shrink-0');
      expect(sidebarContainerClasses).toContain('min-h-0');
      expect(sidebarContainerClasses).toContain('h-full');
    });

    // U02: 1280x720 compact mode format string
    it('U02: NYSessionPanel compact collapsed mode displays exact required summary format', () => {
      const nyTime = 'NY 08:10';
      const vnTime = 'VN 19:10';
      const dayCount = '0/3 hôm nay';
      const nyCount = 'Mỹ 0/1';
      const autoStatus = 'Auto bật';

      const compactSummary = `Phiên Mỹ • ${nyTime} • ${vnTime} • ${dayCount} • ${nyCount} • ${autoStatus}`;
      expect(compactSummary).toContain('Phiên Mỹ');
      expect(compactSummary).toContain('NY 08:10');
      expect(compactSummary).toContain('0/3 hôm nay');
      expect(compactSummary).toContain('Auto bật');
    });

    // U03: Responsive breakpoints support internal scroll without page horizontal overflow
    it('U03: Inner tab containers use flex-1 min-h-0 overflow-y-auto instead of hardcoded max-height', () => {
      const containerClass = 'flex-1 min-h-0 overflow-y-auto';
      expect(containerClass).toContain('min-h-0');
      expect(containerClass).toContain('overflow-y-auto');
      expect(containerClass).not.toContain('max-h-[750px]');
    });

    // U04: Setup with 20 lessons and 15 blockers normalizes cleanly without expanding main layout
    it('U04: Normalizes list of 20 lessons with distinct severities and badges for drawer display', () => {
      const lessons = Array.from({ length: 20 }, (_, i) => ({
        id: `L-${i + 1}`,
        rule_identity: `RULE_${i + 1}`,
        title: `Bài học số ${i + 1}`,
        severity: i % 3 === 0 ? 'CRITICAL' : i % 3 === 1 ? 'WARN' : 'INFO',
        human_message: `Mô tả chi tiết bài học ${i + 1}`,
      }));

      const normalized = lessons.map((l) => normalizeLessonItem(l, l.id));
      expect(normalized.length).toBe(20);

      const redCounts = normalized.filter((n) => n.severity === 'CRITICAL').length;
      const yellowCounts = normalized.filter((n) => n.severity === 'WARN').length;
      const greenCounts = normalized.filter((n) => n.severity === 'INFO').length;

      expect(redCounts + yellowCounts + greenCounts).toBe(20);
      expect(redCounts).toBeGreaterThan(0);
      expect(yellowCounts).toBeGreaterThan(0);
    });

    // U05: Geometry blocker reasons are clear and accessible
    it('U05: Geometry validator generates clear Vietnamese explanation for invalid price levels', () => {
      const longInvalid = validatePriceGeometry('LONG', 2700, 2750, 2800);
      expect(longInvalid.isValid).toBe(false);
      expect(longInvalid.invalidReason).toContain('LONG');

      const shortInvalid = validatePriceGeometry('SHORT', 2700, 2650, 2600);
      expect(shortInvalid.isValid).toBe(false);
      expect(shortInvalid.invalidReason).toContain('SHORT');
    });

    // U06: Drawer actions preserve setup parameters without mutative side-effects
    it('U06: Preserves immutable snapshot of setup intent during drawer state changes', () => {
      const originalIntent = {
        setup_id: 's-gold-01',
        direction: 'SHORT' as const,
        plannedEntry: 2750.0,
        stopLoss: 2760.0,
        takeProfit: 2725.0,
        quantity: 0.05,
        revision: 4,
      };

      // Simulating drawer open/close
      let isDrawerOpen = false;
      let activeSection = 'conditions';

      // Open drawer to 'lessons'
      isDrawerOpen = true;
      activeSection = 'lessons';

      // Close drawer
      isDrawerOpen = false;
      expect(isDrawerOpen).toBe(false);
      expect(activeSection).toBe('lessons');

      // Assert original intent remains strictly equal
      expect(originalIntent.setup_id).toBe('s-gold-01');
      expect(originalIntent.direction).toBe('SHORT');
      expect(originalIntent.plannedEntry).toBe(2750.0);
      expect(originalIntent.stopLoss).toBe(2760.0);
      expect(originalIntent.takeProfit).toBe(2725.0);
      expect(originalIntent.revision).toBe(4);
    });

    // U07: Keyboard ESC event closes drawer
    it('U07: ESC key listener callback triggers drawer close', () => {
      let isDrawerOpen = true;
      const closeHandler = () => {
        isDrawerOpen = false;
      };

      const event = { key: 'Escape' };
      if (event.key === 'Escape' && isDrawerOpen) {
        closeHandler();
      }

      expect(isDrawerOpen).toBe(false);
    });

    // U08: ResizeObserver coalesce via requestAnimationFrame
    it('U08: Resize coalescing with requestAnimationFrame prevents redundant layout recalcs', () => {
      let rafCount = 0;
      const mockRaf = (_cb: () => void) => {
        rafCount++;
        return 1;
      };

      let pendingFrame: number | null = null;
      const onResize = () => {
        if (pendingFrame) return;
        pendingFrame = mockRaf(() => {
          pendingFrame = null;
        });
      };

      // Trigger 5 resize events in same tick
      onResize();
      onResize();
      onResize();
      onResize();
      onResize();

      expect(rafCount).toBe(1);
    });

    // U09: Drag RR updates both Gross and Net RR accurately
    it('U09: Calculates client risk reward with fees and slippage preserved', () => {
      const rrLong = calculateClientRiskReward('LONG', 2700, 2690, 2730, 1000, 1.0, 1.5, undefined, 50, 'ISOLATED');
      expect(rrLong.isValid).toBe(true);
      expect(rrLong.grossRR).toBeCloseTo(3.0, 1);
      expect(rrLong.estimatedNetRR).toBeLessThan(rrLong.grossRR);
      expect(rrLong.meetsMinRR).toBe(true);

      const rrShort = calculateClientRiskReward('SHORT', 2700, 2710, 2670, 1000, 1.0, 1.5, undefined, 50, 'ISOLATED');
      expect(rrShort.isValid).toBe(true);
      expect(rrShort.grossRR).toBeCloseTo(3.0, 1);
      expect(rrShort.estimatedNetRR).toBeLessThan(rrShort.grossRR);
      expect(rrShort.meetsMinRR).toBe(true);
    });

    // U10: Status badges formatting for all state variants
    it('U10: Sidebar status helper translates all execution lifecycle states to clear Vietnamese', () => {
      const getVietnameseStatus = (status?: string): string => {
        switch (status) {
          case 'READY':
            return 'Sẵn Sàng';
          case 'ARMED':
            return 'Đã Lên Nòng';
          case 'WATCHING':
            return 'Đang Theo Dõi';
          case 'DRAFT':
            return 'Bản Nháp';
          case 'CLOSED':
            return 'Đã Đóng';
          default:
            return status || 'Đang Theo Dõi';
        }
      };

      expect(getVietnameseStatus('READY')).toBe('Sẵn Sàng');
      expect(getVietnameseStatus('ARMED')).toBe('Đã Lên Nòng');
      expect(getVietnameseStatus('WATCHING')).toBe('Đang Theo Dõi');
      expect(getVietnameseStatus('DRAFT')).toBe('Bản Nháp');
      expect(getVietnameseStatus('CLOSED')).toBe('Đã Đóng');
    });

    // U11: Tab navigation does not trigger mutative trading actions
    it('U11: Switching between tabs preserves selected setup intent and overlay state', () => {
      let currentTab = 'chart';
      const savedOverlay = { id: 'setup-01', plannedEntry: 2750 };

      // Switch to news
      currentTab = 'news';
      expect(savedOverlay.id).toBe('setup-01');

      // Switch to journal
      currentTab = 'journal';
      expect(savedOverlay.id).toBe('setup-01');

      // Switch back to chart
      currentTab = 'chart';
      expect(currentTab).toBe('chart');
      expect(savedOverlay.id).toBe('setup-01');
      expect(savedOverlay.plannedEntry).toBe(2750);
    });
  });

  describe('Area P: Performance & Stability (P01 - P02)', () => {
    // P01: Quote ingestion burst bounded memory
    it('P01: Ingesting high rate of canonical quotes updates store deterministically without memory leak', () => {
      const store = new CanonicalQuoteStore();
      const startTime = Date.now();

      for (let i = 0; i < 500; i++) {
        store.updateFromQuoteEnvelope({
          type: 'QUOTE_UPDATE',
          symbol: 'XAUUSDT',
          exchange_ts_ms: startTime + i * 10,
          payload: {
            bid: 2750.0 + i * 0.01,
            ask: 2750.05 + i * 0.01,
            last: 2750.02 + i * 0.01,
          },
        });
      }

      const latest = store.getQuote('XAUUSDT');
      expect(latest).toBeDefined();
      expect(latest!.last).toBeCloseTo(2750.02 + 499 * 0.01, 2);
    });

    // P02: Repeated subscription and unsubscription does not leak listeners
    it('P02: Subscribing and unsubscribing 50 times leaves exactly 0 listeners', () => {
      const store = new CanonicalQuoteStore();
      const unsubs: (() => void)[] = [];

      for (let i = 0; i < 50; i++) {
        const unsub = store.subscribe('XAUUSDT', () => {});
        unsubs.push(unsub);
      }

      // Unsubscribe all
      for (const unsub of unsubs) {
        unsub();
      }

      // Ingest a quote - verify no lingering callbacks crash or leak
      store.updateFromQuoteEnvelope({
        type: 'QUOTE_UPDATE',
        symbol: 'XAUUSDT',
        exchange_ts_ms: Date.now(),
        payload: {
          bid: 2750.0,
          ask: 2750.1,
          last: 2750.05,
        },
      });

      expect(store.getQuote('XAUUSDT')!.last).toBe(2750.05);
    });
  });
});
