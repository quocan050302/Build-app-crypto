import { describe, it, expect } from 'vitest';
import {
  normalizeAppError,
  buildTradeEventMessage,
  formatPrice,
  formatCurrency,
  formatRRRatio,
  formatLeverage,
  sanitizeText,
} from './utils/normalizeAppError';
import { USER_MESSAGE_CATALOG, getCatalogTemplate } from './utils/userMessageCatalog';

describe('V9.1 User Message Catalog & Normalizer Suite', () => {
  describe('1. Catalog Coverage & Template Structure', () => {
    const requiredCodes = [
      'ACTIVE_POSITION_EXISTS',
      'ARMED_ORDER_EXISTS',
      'WAITING_STRUCTURE',
      'STRATEGY_NOT_READY',
      'SETUP_TERMINAL',
      'NEWS_BLACKOUT',
      'INSUFFICIENT_RR',
      'INVALID_PRICE_GEOMETRY',
      'CALCULATOR_INVALID',
      'MAX_DAILY_ENTRIES',
      'MAX_CONSECUTIVE_LOSSES',
      'DAY_BLOCKED',
      'COOLDOWN_ACTIVE',
      'OUTSIDE_ENTRY_WINDOW',
      'NY_SLOT_RESERVED',
      'CROSS_MARGIN_UNSUPPORTED',
      'TICKER_STALE',
      'FEED_DISCONNECTED',
      'EXECUTION_FEED_DEGRADED',
      'MALFORMED_QUOTE',
      'STALE_EDIT',
      'SETUP_CONFLICT',
      'INSUFFICIENT_MARGIN',
      'UNAUTHORIZED',
      'CHAT_NOT_FOUND',
      'RATE_LIMIT',
      'AMBIGUOUS',
      'UNKNOWN',
      'TRADE_OPENED',
      'TRADE_CLOSED_TP',
      'TRADE_CLOSED_SL',
      'TRADE_CLOSED_MANUAL',
      'ORDER_ARMED',
      'SETUP_READY',
    ];

    it('contains all required semantic codes with Vietnamese content', () => {
      for (const code of requiredCodes) {
        const tmpl = USER_MESSAGE_CATALOG[code];
        expect(tmpl, `Catalog missing code: ${code}`).toBeDefined();
        expect(tmpl.title.length, `Code ${code} must have a title`).toBeGreaterThan(3);
        expect(tmpl.explanation.length, `Code ${code} must have an explanation`).toBeGreaterThan(10);
        expect(tmpl.next_steps.length, `Code ${code} must have next_steps`).toBeGreaterThan(10);
        expect(tmpl.defaultSeverity).toBeDefined();
        expect(tmpl.defaultCertainty).toBeDefined();
      }
    });

    it('getCatalogTemplate falls back cleanly for unknown codes without throwing', () => {
      const tmpl = getCatalogTemplate('NON_EXISTENT_ERROR_XYZ');
      expect(tmpl.code).toBe('UNKNOWN');
      expect(tmpl.title).toBe('Chưa hoàn tất được thao tác này');
      expect(tmpl.defaultCertainty).toBe('unknown');
    });
  });

  describe('2. normalizeAppError - Error Types & Normalization', () => {
    it('normalizes string-only known codes cleanly', () => {
      const msg = normalizeAppError('ACTIVE_POSITION_EXISTS');
      expect(msg.code).toBe('ACTIVE_POSITION_EXISTS');
      expect(msg.title).toBe('Bạn đang có một lệnh mở');
      expect(msg.summary).toContain('Giới hạn tối đa 1 vị thế');
      expect(msg.outcome_certainty).toBe('confirmed');
    });

    it('normalizes string with legacy "CODE: message" prefix', () => {
      const msg = normalizeAppError('NEWS_BLACKOUT: Sự kiện CPI công bố lúc 19:30');
      expect(msg.code).toBe('NEWS_BLACKOUT');
      expect(msg.title).toBe('Tạm dừng vào lệnh gần giờ có tin tức');
      expect(msg.summary).toBe('Sự kiện CPI công bố lúc 19:30');
    });

    it('normalizes structured Axios response with detail object and parameters', () => {
      const axiosErr = {
        response: {
          status: 400,
          data: {
            detail: {
              code: 'INSUFFICIENT_RR',
              net_rr: 1.45,
              required_rr: 2.0,
            },
          },
        },
      };

      const msg = normalizeAppError(axiosErr);
      expect(msg.code).toBe('INSUFFICIENT_RR');
      expect(msg.title).toBe('Lợi nhuận dự kiến chưa đủ so với rủi ro');
      expect(msg.summary).toContain('1.45');
      expect(msg.summary).toContain('2.00');
    });

    it('normalizes FastAPI 422 validation errors with Vietnamese field names', () => {
      const fastApi422 = {
        response: {
          status: 422,
          data: {
            detail: [
              {
                loc: ['body', 'stop_loss'],
                msg: 'field required',
                type: 'value_error.missing',
              },
              {
                loc: ['body', 'planned_entry'],
                msg: 'ensure this value is greater than 0',
                type: 'value_error.number.not_gt',
              },
            ],
          },
        },
      };

      const msg = normalizeAppError(fastApi422);
      expect(msg.code).toBe('VALIDATION_ERROR');
      expect(msg.summary).toContain('Bạn chưa nhập Giá cắt lỗ.');
      expect(msg.summary).toContain('Giá vào lệnh phải lớn hơn 0.');
      expect(msg.technical_details?.field_errors).toHaveLength(2);
    });

    it('distinguishes HTTP 409 conflict based on operation context', () => {
      const conflictErr = {
        response: {
          status: 409,
          data: { detail: 'Conflict' },
        },
      };

      // 409 during arm_setup -> SETUP_CHANGED
      const armMsg = normalizeAppError(conflictErr, { operation: 'arm_setup' });
      expect(armMsg.code).toBe('SETUP_CHANGED');
      expect(armMsg.title).toBe('Kế hoạch đã có sự thay đổi');

      // 409 during save_risk_settings -> STALE_EDIT
      const settingsMsg = normalizeAppError(conflictErr, { operation: 'save_risk_settings' });
      expect(settingsMsg.code).toBe('STALE_EDIT');
      expect(settingsMsg.title).toBe('Dữ liệu đã thay đổi từ lúc bạn mở màn hình');

      // 409 during save_journal_review -> STALE_EDIT
      const reviewMsg = normalizeAppError(conflictErr, { operation: 'save_journal_review' });
      expect(reviewMsg.code).toBe('STALE_EDIT');

      // Generic 409 -> SETUP_CONFLICT
      const genericMsg = normalizeAppError(conflictErr);
      expect(genericMsg.code).toBe('SETUP_CONFLICT');
    });

    it('handles timeout and marks outcome_certainty as unknown with RECONCILE_STATUS', () => {
      const timeoutErr = {
        code: 'ECONNABORTED',
        message: 'timeout of 10000ms exceeded',
      };

      const msg = normalizeAppError(timeoutErr, { operation: 'create_paper_order' });
      expect(msg.code).toBe('TIMEOUT');
      expect(msg.outcome_certainty).toBe('unknown');
      expect(msg.summary).toContain('Chưa xác nhận được kết quả');
      expect(msg.summary).toContain('tránh tạo hoặc đóng trùng');
      const actionType = typeof msg.action === 'object' ? msg.action?.type : msg.action;
      expect(actionType).toBe('RECONCILE_STATUS');
    });

    it('handles Network Error and marks outcome_certainty as unknown', () => {
      const netErr = new Error('Network Error');
      const msg = normalizeAppError(netErr);
      expect(msg.code).toBe('NETWORK_ERROR');
      expect(msg.outcome_certainty).toBe('unknown');
      expect(msg.title).toBe('Mất kết nối với máy chủ nội bộ');
    });

    it('sanitizes sensitive Telegram bot tokens and credentials from errors', () => {
      const leakedTokenErr = {
        message: 'Request failed to https://api.telegram.org/bot123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456/sendMessage with status 401',
      };

      const msg = normalizeAppError(leakedTokenErr);
      expect(msg.summary).not.toContain('ABCdefGHIjklMNOpqrSTUvwxYZ123456');
      expect(msg.technical_details?.raw_error).not.toContain('ABCdefGHIjklMNOpqrSTUvwxYZ123456');
    });
  });

  describe('3. Formatting Utilities & Robustness', () => {
    it('formatPrice handles valid decimals and fallback on non-finite values', () => {
      expect(formatPrice(2745.5, 2)).toBe('2.745,50');
      expect(formatPrice(0, 2)).toBe('0,00');
      expect(formatPrice(null)).toBe('—');
      expect(formatPrice(undefined)).toBe('—');
      expect(formatPrice(NaN)).toBe('—');
      expect(formatPrice(Infinity)).toBe('—');
    });

    it('formatCurrency preserves 0 and negative values in USDT format', () => {
      expect(formatCurrency(0)).toBe('0,00 USDT');
      expect(formatCurrency(-5.25)).toBe('-5,25 USDT');
      expect(formatCurrency(1250.75)).toBe('1.250,75 USDT');
      expect(formatCurrency(null)).toBe('chưa có dữ liệu');
    });

    it('formatRRRatio formats valid ratios cleanly and guards against zero/non-finite values', () => {
      expect(formatRRRatio(2.5)).toBe('1:2.50');
      expect(formatRRRatio(1.0)).toBe('1:1.00');
      expect(formatRRRatio(0)).toBe('Chưa đạt');
      expect(formatRRRatio(null)).toBe('Chưa tính');
    });

    it('formatLeverage adds multiplier symbol correctly', () => {
      expect(formatLeverage(5)).toBe('5×');
      expect(formatLeverage(50)).toBe('50×');
      expect(formatLeverage(null)).toBe('—');
    });

    it('sanitizeText replaces bot tokens, basic auth, and bearer tokens', () => {
      const text = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and bot987654321:abcdefghijklmnopqrstuvwxyz012345678';
      const clean = sanitizeText(text);
      expect(clean).toContain('Bearer [REDACTED]');
      expect(clean).toContain('[REDACTED_TELEGRAM_BOT_TOKEN]');
    });
  });

  describe('4. buildTradeEventMessage - Trading Domain Events', () => {
    it('builds trade.opened event with PAPER simulation label and position details', () => {
      const event = buildTradeEventMessage('trade.opened', {
        direction: 'LONG',
        entry_price: 2740.5,
        stop_loss: 2730.0,
        take_profit: 2760.0,
        quantity: 0.1,
        initial_risk_usdt: 2.5,
        estimated_net_rr: 2.1,
        leverage: 5,
      });

      expect(event.code).toBe('TRADE_OPENED');
      expect(event.severity).toBe('success');
      expect(event.title).toBe('Lệnh mua (LONG) đã khớp');
      expect(event.summary).toContain('Vị thế PAPER LONG đã mở tại 2.740,50 USDT');
      expect(event.explanation).toContain('mô phỏng (PAPER TRADING)');
      expect(event.outcome_certainty).toBe('confirmed');
    });

    it('builds trade.closed event correctly distinguishes TP, SL and MANUAL exit causes', () => {
      // 1. Take Profit Exit
      const tpEvent = buildTradeEventMessage('trade.closed', {
        direction: 'LONG',
        exit_cause: 'TP',
        realized_pnl_net: 5.25,
        realized_r: 2.1,
        fee_usdt: 0.15,
      });
      expect(tpEvent.code).toBe('TRADE_CLOSED_TP');
      expect(tpEvent.title).toBe('Vị thế đã đóng: Đạt mục tiêu chốt lời (TP)');
      expect(tpEvent.summary).toContain('+5,25 USDT');
      expect(tpEvent.summary).toContain('+2.10R');

      // 2. Stop Loss Exit
      const slEvent = buildTradeEventMessage('trade.closed', {
        direction: 'SHORT',
        exit_cause: 'SL',
        realized_pnl_net: -2.5,
        realized_r: -1.0,
        fee_usdt: 0.15,
      });
      expect(slEvent.code).toBe('TRADE_CLOSED_SL');
      expect(slEvent.title).toBe('Vị thế đã đóng: Chạm giá cắt lỗ (SL)');
      expect(slEvent.summary).toContain('-2,50 USDT');

      // 3. Manual Close with loss (NOT classified as SL!)
      const manualLossEvent = buildTradeEventMessage('trade.closed', {
        direction: 'LONG',
        exit_cause: 'MANUAL',
        realized_pnl_net: -1.2,
        realized_r: -0.48,
      });
      expect(manualLossEvent.code).toBe('TRADE_CLOSED_MANUAL');
      expect(manualLossEvent.title).toBe('Vị thế đã đóng: Đóng lệnh thủ công');
      expect(manualLossEvent.summary).toContain('-1,20 USDT');

      // 4. Offline reconciliation (Estimated outcome)
      const offlineEvent = buildTradeEventMessage('trade.closed', {
        direction: 'LONG',
        exit_cause: 'RECONCILED_OFFLINE',
        realized_pnl_net: 0,
      });
      expect(offlineEvent.outcome_certainty).toBe('estimated');
      expect(offlineEvent.summary).toContain('đối soát');
    });

    it('builds order.armed event stating no open position has been created yet', () => {
      const armedEvent = buildTradeEventMessage('order.armed', {
        direction: 'SHORT',
        setup_id: 'setup-xau-123',
        planned_entry: 2750.0,
        order_type: 'LIMIT',
      });

      expect(armedEvent.code).toBe('ORDER_ARMED');
      expect(armedEvent.title).toBe('Đã đặt lệnh chờ khớp');
      expect(armedEvent.summary).toContain('Bạn chưa có vị thế mở từ lệnh này');
      expect(armedEvent.explanation).toContain('(Bid/Ask)');
    });

    it('builds setup.ready event stating conditions met without claiming trade is executed', () => {
      const readyEvent = buildTradeEventMessage('setup.ready', {
        direction: 'LONG',
        setup_id: 'setup-xau-456',
        is_blocked: false,
      });

      expect(readyEvent.code).toBe('SETUP_READY');
      expect(readyEvent.title).toBe('Có kế hoạch giao dịch đủ điều kiện');
      expect(readyEvent.summary).toContain('Bạn chưa có vị thế mới');
    });
  });
});
