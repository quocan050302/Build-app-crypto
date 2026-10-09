/**
 * Error Normalizer for Aurum Desk V9.1.
 * Transforms diverse error types (Axios, FastAPI 422, strings, Error objects, domain events)
 * into a typed, structured, sanitized, beginner-friendly UserMessage.
 */
import type { UserMessage, TechnicalDetails } from '../types/userMessage';
import { getCatalogTemplate, USER_MESSAGE_CATALOG } from './userMessageCatalog';

// Vietnamese labels for backend schema fields
const FIELD_TRANSLATIONS: Record<string, string> = {
  planned_entry: 'Giá vào lệnh',
  stop_loss: 'Giá cắt lỗ',
  take_profit: 'Giá chốt lời',
  leverage: 'Đòn bẩy',
  risk_pct: 'Tỷ lệ rủi ro',
  margin_mode: 'Chế độ ký quỹ',
  order_type: 'Loại lệnh',
  quantity: 'Khối lượng giao dịch',
  capital_usdt: 'Vốn khả dụng',
  chat_id: 'Chat ID Telegram',
  bot_token: 'Bot Token Telegram',
  enabled: 'Trạng thái bật/tắt',
  symbol: 'Cặp tiền / Tài sản',
  instrument: 'Tài sản giao dịch',
  notes: 'Ghi chú đánh giá',
  psychology_tags: 'Thẻ tâm lý giao dịch',
  expected_revision: 'Phiên bản kế hoạch',
  expected_direction: 'Chiều lệnh dự kiến',
  setup_id: 'Mã kế hoạch (Setup ID)',
};

/**
 * Sanitizes strings to prevent exposing bot tokens, passwords, raw SQL or headers.
 */
export function sanitizeText(text: string): string {
  if (!text) return '';
  return text
    // Mask Telegram bot tokens: 123456789:ABC_... or bot123456...
    .replace(/(bot)?\b(\d{8,12}):[A-Za-z0-9_-]{25,}\b/g, '[REDACTED_TELEGRAM_BOT_TOKEN]')
    .replace(/(bot)(\d{8,12}:[A-Za-z0-9_-]+)/gi, '[REDACTED_TELEGRAM_BOT_TOKEN]')
    // Mask authorization headers or bearer tokens
    .replace(/(bearer\s+)[A-Za-z0-9._-]+/gi, 'Bearer [REDACTED]')
    .replace(/(password|secret|token)=["']?[^"'\s&]+["']?/gi, '$1=[REDACTED]');
}

/**
 * Formats price with proper precision and thousands separators (vi-VN locale).
 */
export function formatPrice(value: number | null | undefined, precision = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return Number(value).toLocaleString('vi-VN', {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

/**
 * Formats USDT currency amount preserving 0 and negatives.
 */
export function formatCurrency(value: number | null | undefined, precision = 2): string {
  if (value == null || !Number.isFinite(value)) return 'chưa có dữ liệu';
  const formatted = Number(value).toLocaleString('vi-VN', {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
  return `${formatted} USDT`;
}

/**
 * Formats R:R ratio cleanly.
 */
export function formatRRRatio(value: number | null | undefined): string {
  if (value == null) return 'Chưa tính';
  if (!Number.isFinite(value) || value <= 0) return 'Chưa đạt';
  return `1:${Number(value).toFixed(2)}`;
}

/**
 * Formats leverage multiplier (e.g., 5×, 50×).
 */
export function formatLeverage(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${Math.round(value)}×`;
}

/**
 * Maps FastAPI 422 Validation Error item to a clean Vietnamese sentence.
 */
function formatValidationErrorItem(item: any): { field: string; message: string } {
  const loc = item.loc || [];
  const fieldName = loc[loc.length - 1] || 'dữ liệu';
  const vietnameseField = FIELD_TRANSLATIONS[fieldName] || fieldName;
  const msg = (item.msg || '').toLowerCase();

  let message = '';
  if (msg.includes('field required') || msg.includes('missing')) {
    message = `Bạn chưa nhập ${vietnameseField}.`;
  } else if (msg.includes('greater than 0') || msg.includes('not_gt')) {
    message = `${vietnameseField} phải lớn hơn 0.`;
  } else if (msg.includes('valid float') || msg.includes('valid number')) {
    message = `${vietnameseField} phải là số hợp lệ.`;
  } else if (msg.includes('valid integer')) {
    message = `${vietnameseField} phải là số nguyên hợp lệ.`;
  } else if (msg.includes('greater than') || msg.includes('must be >')) {
    message = `${vietnameseField} phải lớn hơn giá trị quy định.`;
  } else if (msg.includes('less than') || msg.includes('must be <')) {
    message = `${vietnameseField} phải nhỏ hơn giá trị quy định.`;
  } else {
    message = `${vietnameseField}: ${item.msg}`;
  }

  return { field: vietnameseField, message };
}

/**
 * Normalizes any error object into a structured UserMessage.
 */
export function normalizeAppError(
  err: any,
  context?: {
    operation?: string;
    entityId?: string;
    fallbackTitle?: string;
    fallbackSummary?: string;
  }
): UserMessage {
  const now = Date.now();
  const id = `msg_${now}_${Math.random().toString(36).substring(2, 7)}`;

  // Default baseline
  let code = 'UNKNOWN';
  let customTitle: string | undefined = undefined;
  let summary: string | undefined = undefined;
  let params: Record<string, any> = {};
  let httpStatus: number | undefined = undefined;
  let rawSanitizedError = '';
  let fieldErrors: Array<{ field: string; message: string }> = [];

  // 1. Handle String input
  if (typeof err === 'string') {
    rawSanitizedError = sanitizeText(err);
    const colonIdx = err.indexOf(':');
    if (colonIdx > 0 && colonIdx < 35) {
      const potentialCode = err.substring(0, colonIdx).trim().toUpperCase();
      if (USER_MESSAGE_CATALOG[potentialCode]) {
        code = potentialCode;
        summary = err.substring(colonIdx + 1).trim();
      } else {
        summary = rawSanitizedError;
      }
    } else if (USER_MESSAGE_CATALOG[err.trim().toUpperCase()]) {
      code = err.trim().toUpperCase();
      summary = undefined;
    } else {
      summary = rawSanitizedError;
    }
  } else if (err && typeof err === 'object') {
    // 2. Handle Axios / HTTP Response
    const response = err.response;
    if (response) {
      httpStatus = response.status;
      const data = response.data;

      if (data?.code) {
        code = String(data.code);
      }

      // Check detail property
      if (data?.detail) {
        const detail = data.detail;

        // 2a. Detail is a structured Object
        if (typeof detail === 'object' && !Array.isArray(detail)) {
          if (detail.code) code = String(detail.code);
          params = { ...detail, ...(detail.params || {}) };
          if (detail.message) {
            summary = sanitizeText(String(detail.message));
          } else {
            summary = undefined;
          }
          if (detail.current_direction && detail.expected_direction) {
            params.direction = detail.current_direction;
            params.expected_direction = detail.expected_direction;
          }
          if (detail.eligibility) {
            params.eligibility = detail.eligibility;
            if (detail.eligibility.reason_codes?.[0]) {
              code = detail.eligibility.reason_codes[0];
            }
          }
          rawSanitizedError = sanitizeText(JSON.stringify(detail));
        }

        // 2b. Detail is a string (e.g. "ACTIVE_POSITION_EXISTS: Đã có vị thế đang mở...")
        else if (typeof detail === 'string') {
          rawSanitizedError = sanitizeText(detail);
          summary = rawSanitizedError;

          // Check if string starts with a recognized code prefix
          const colonIdx = detail.indexOf(':');
          if (colonIdx > 0 && colonIdx < 35) {
            const potentialCode = detail.substring(0, colonIdx).trim().toUpperCase();
            if (USER_MESSAGE_CATALOG[potentialCode]) {
              code = potentialCode;
              summary = detail.substring(colonIdx + 1).trim();
            }
          }
        }

        // 2c. Detail is an Array (FastAPI 422 Validation Error)
        else if (Array.isArray(detail)) {
          code = 'VALIDATION_ERROR';
          customTitle = 'Thông tin nhập chưa hợp lệ';
          fieldErrors = detail.map(formatValidationErrorItem);
          summary = fieldErrors.map((f) => f.message).join(' ');
          rawSanitizedError = sanitizeText(JSON.stringify(detail));
        }
      } else if (typeof data?.message === 'string') {
        summary = sanitizeText(data.message);
        rawSanitizedError = summary;
      } else if (typeof data?.error === 'string') {
        summary = sanitizeText(data.error);
        rawSanitizedError = summary;
      }

      // Check HTTP 409 Conflict specific semantic contexts
      if (httpStatus === 409) {
        if (!code || code === 'UNKNOWN') {
          if (context?.operation === 'arm_setup') {
            code = 'SETUP_CHANGED';
          } else if (
            context?.operation === 'save_review' ||
            context?.operation === 'save_risk_settings' ||
            context?.operation === 'save_journal_review'
          ) {
            code = 'STALE_EDIT';
          } else {
            code = 'SETUP_CONFLICT';
          }
        }
      }
    } else if (err.code === 'ECONNABORTED' || (err.message && err.message.toLowerCase().includes('timeout'))) {
      code = 'TIMEOUT';
      summary = undefined;
    } else if (err.message && err.message.toLowerCase().includes('network error')) {
      code = 'NETWORK_ERROR';
      summary = undefined;
    } else if (err.message) {
      rawSanitizedError = sanitizeText(err.message);
      summary = rawSanitizedError;
    }
  }

  // 3. Match with Catalog Template
  const template = getCatalogTemplate(code);

  // If summary was just the raw code or a generic string, resolve from template
  let finalSummary = summary;
  if (!finalSummary || finalSummary === code || finalSummary.startsWith(code + ':')) {
    if (typeof template.summary === 'function') {
      finalSummary = template.summary(params);
    } else if (template.summary) {
      finalSummary = template.summary;
    } else {
      finalSummary = context?.fallbackSummary || 'Đã xảy ra lỗi không xác định trong quá trình xử lý.';
    }
  }

  const technical_details: TechnicalDetails = {
    code,
    http_status: httpStatus,
    timestamp: now,
    operation: context?.operation,
    raw_error: rawSanitizedError || undefined,
    field_errors: fieldErrors.length > 0 ? fieldErrors : undefined,
  };

  const outcome_certainty =
    code === 'TIMEOUT' || code === 'AMBIGUOUS' || code === 'NETWORK_ERROR'
      ? 'unknown'
      : template.defaultCertainty;

  return {
    id,
    code,
    severity: template.defaultSeverity,
    title: customTitle || (code === 'UNKNOWN' && context?.fallbackTitle ? context.fallbackTitle : template.title),
    summary: finalSummary,
    explanation: template.explanation,
    impact: template.impact,
    next_steps: template.next_steps,
    params,
    action: template.defaultAction,
    operation: context?.operation,
    entity_id: context?.entityId,
    occurred_at: now,
    retryable: outcome_certainty === 'unknown' || code === 'TIMEOUT' || code === 'STALE_EDIT',
    outcome_certainty,
    technical_details,
    read: false,
  };
}

/**
 * Deterministically generates composite event ID for domain event deduplication.
 */
export function generateCompositeEventId(eventType: string, payload: any): string {
  const rawEventId = payload?.event_id || payload?.id;
  if (rawEventId) return String(rawEventId);
  const entityId = payload?.order_id || payload?.trade_id || payload?.setup_id || 'entity';
  const instanceOrRev = payload?.setup_instance_id || payload?.instance_id || payload?.revision || Date.now();
  return `${eventType}:${entityId}:${instanceOrRev}`;
}

/**
 * Builds a friendly UserMessage for trading domain events.
 * Returns null if the event does not require user notification (e.g. setup.updated, unknown/malformed).
 */
export function buildTradeEventMessage(eventType: string, payload: any): UserMessage | null {
  // 1. Explicitly ignore background sync events from creating notifications
  if (eventType === 'setup.updated') {
    return null;
  }

  const now = Date.now();
  let code: string | null = null;
  let customTitle: string | undefined = undefined;
  let customSummary: string | undefined = undefined;
  let certaintyOverride: 'confirmed' | 'pending' | 'unknown' | 'estimated' | undefined = undefined;

  if (eventType === 'trade.opened') {
    code = 'TRADE_OPENED';
    const dir = (payload?.direction || 'LONG').toUpperCase();
    customTitle = dir === 'SHORT' ? 'Lệnh bán (SHORT) đã khớp' : 'Lệnh mua (LONG) đã khớp';
    const entry = payload?.entry_price ?? payload?.actual_entry ?? payload?.planned_entry;
    const entryStr = entry != null ? `${formatPrice(entry)} USDT` : '';
    customSummary = `Vị thế PAPER ${dir} đã mở${entryStr ? ` tại ${entryStr}` : ''}.`;
  } else if (eventType === 'trade.closed') {
    const cause = (payload?.exit_cause || payload?.exit_reason || '').toUpperCase();
    const pnl = payload?.realized_pnl_net ?? payload?.realized_pnl ?? payload?.net_pnl;
    const pnlStr = pnl != null ? `${pnl >= 0 ? '+' : ''}${formatPrice(pnl)} USDT` : '';
    const r = payload?.realized_r;
    const rStr = r != null ? `(${r >= 0 ? '+' : ''}${Number(r).toFixed(2)}R)` : '';

    if (cause === 'TP' || cause === 'TP_HIT' || cause === 'TAKE_PROFIT') {
      code = 'TRADE_CLOSED_TP';
      customTitle = 'Vị thế đã đóng: Đạt mục tiêu chốt lời (TP)';
      customSummary = `Vị thế đã chạm mục tiêu Take Profit thành công. Lãi ròng: ${pnlStr} ${rStr}.`.trim();
    } else if (cause === 'SL' || cause === 'SL_HIT' || cause === 'STOP_LOSS') {
      code = 'TRADE_CLOSED_SL';
      customTitle = 'Vị thế đã đóng: Chạm giá cắt lỗ (SL)';
      customSummary = `Vị thế đã dừng tại mức cắt lỗ. Lỗ ròng: ${pnlStr} ${rStr}.`.trim();
    } else if (cause.includes('RECONCILE') || cause.includes('OFFLINE')) {
      code = 'TRADE_CLOSED_MANUAL';
      customTitle = 'Vị thế đã đóng (Đối soát dữ liệu)';
      customSummary = `Kết quả ước tính khi đối soát dữ liệu lúc app tắt: ${pnlStr} ${rStr}.`.trim();
      certaintyOverride = 'estimated';
    } else {
      code = 'TRADE_CLOSED_MANUAL';
      customTitle = 'Vị thế đã đóng: Đóng lệnh thủ công';
      customSummary = `Bạn đã chủ động đóng vị thế theo giá thị trường. Kết quả ròng: ${pnlStr} ${rStr}.`.trim();
    }
  } else if (eventType === 'trade.liquidated') {
    code = 'TRADE_LIQUIDATED';
  } else if (eventType === 'order.armed') {
    code = 'ORDER_ARMED';
  } else if (eventType === 'setup.ready') {
    code = 'SETUP_READY';
  } else if (eventType === 'order.cancelled' || eventType === 'setup.cancelled') {
    code = 'ORDER_CANCELLED';
  } else if (eventType === 'order.expired') {
    code = 'ORDER_EXPIRED';
  } else if (eventType === 'feed.degraded') {
    code = 'FEED_DOWN';
  } else if (eventType === 'feed.recovered') {
    code = 'FEED_RECOVERED';
  }

  // If the event does not map to any recognized notification code, return null (do NOT treat as user error)
  if (!code) {
    return null;
  }

  const template = getCatalogTemplate(code);
  const summary =
    customSummary || (typeof template.summary === 'function' ? template.summary(payload || {}) : template.summary);

  const rawEventId = payload?.event_id || payload?.id;
  const entityId = payload?.order_id || payload?.trade_id || payload?.setup_id;
  const compositeEventId = generateCompositeEventId(eventType, payload);
  const id = rawEventId ? `evt_${rawEventId}` : `evt_${now}_${Math.random().toString(36).substring(2, 7)}`;

  return {
    id,
    code,
    severity: template.defaultSeverity,
    title: customTitle || template.title,
    summary,
    explanation: template.explanation,
    impact: template.impact,
    next_steps: template.next_steps,
    params: payload || {},
    action: template.defaultAction,
    event_id: compositeEventId,
    dedupe_key: compositeEventId,
    entity_id: entityId,
    occurred_at: now,
    outcome_certainty: certaintyOverride || template.defaultCertainty,
    technical_details: {
      code,
      timestamp: now,
      raw_error: undefined,
    },
    read: false,
  };
}
