/**
 * frontend/src/utils/paperOpportunityText.ts
 * Aurum V17.0 — Pure helpers for deriving bot waiting summary and human-readable text.
 */
import type { CG01DisplayState } from './cg01DisplayState';
import { getCG01ReasonDescription } from './cg01ReasonText';
import type { BotWaitingSummary } from '../types/paperOpportunityMonitor';

/**
 * Derives a human-friendly, accurate explanation of what the bot is currently doing / waiting for.
 * Conforms to Rules 104 - 108.
 * Pure function: zero side-effects, does not perform network requests.
 */
export function deriveBotWaitingSummary(
  display: CG01DisplayState | null | undefined,
  _evaluation?: any | null,
): BotWaitingSummary {
  if (!display) {
    return {
      headline: 'Đang khởi động',
      detail: 'Chưa nhận được trạng thái đánh giá ban đầu.',
      tone: 'neutral',
      asOfMs: null,
      isHistoricalReference: false,
    };
  }

  const { isConnected, isStale, asOfMs, primaryReason, stage, tone, description } = display;

  // 1. Connection / Stale state takes highest precedence
  if (!isConnected) {
    return {
      headline: 'Mất kết nối',
      detail: 'Chưa có dữ liệu mới từ máy chủ. Đang chờ kết nối lại.',
      tone: 'danger',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (isStale) {
    const timeStr = asOfMs > 0 ? new Date(asOfMs).toLocaleTimeString('vi-VN') : '';
    return {
      headline: 'Dữ liệu chậm',
      detail: `Đánh giá gần nhất lúc ${timeStr || 'trước đó'}, chưa có dữ liệu mới.`,
      tone: 'warning',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  // 2. Model disabled
  if (primaryReason === 'MODEL_DISABLED' || stage === 'MODEL_DISABLED') {
    return {
      headline: 'CG-01 đang tắt',
      detail: 'Mô hình CG-01 hiện đang tắt trong cấu hình hệ thống.',
      tone: 'neutral',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  // 3. Waiting stages & conditions
  const stageUpper = String(stage || '').toUpperCase();
  const reasonUpper = String(primaryReason || '').toUpperCase();

  if (stageUpper === 'ARMED') {
    return {
      headline: 'Đã đặt lệnh chờ (ARM)',
      detail: 'Lệnh đã được kích hoạt và đang chờ khớp trên thị trường.',
      tone: 'success',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'GUARD_BLOCKED' || reasonUpper === 'GUARD_BLOCKED') {
    const reasonMsg = getCG01ReasonDescription(primaryReason);
    return {
      headline: 'Tạm dừng vào lệnh',
      detail: reasonMsg || 'Cơ chế kiểm soát rủi ro (Guard) đang chủ động bảo vệ tài khoản.',
      tone: 'warning',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'EXECUTABLE') {
    return {
      headline: 'Đủ điều kiện kỹ thuật',
      detail: 'Setup đạt đầy đủ tiêu chuẩn SMC/ICT, đang chuẩn bị bước đặt lệnh.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_RETEST' || stageUpper === 'WAIT_ENTRY' || reasonUpper === 'WAIT_RETEST') {
    return {
      headline: 'Chờ giá hồi về (Retest)',
      detail: 'Đã xác nhận cấu trúc phá vỡ (MSS), đang chờ nến M1 hồi vào vùng FVG.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_FVG' || reasonUpper === 'WAIT_FVG') {
    return {
      headline: 'Chờ tạo FVG',
      detail: 'Đã xác nhận MSS, đang chờ nến M1 tạo khoảng trống giá (FVG) hợp lệ.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_MSS' || reasonUpper === 'WAIT_MSS') {
    return {
      headline: 'Chờ phá cấu trúc (MSS)',
      detail: 'Đã quét thanh khoản; đang chờ nến M1 đóng cửa xác nhận phá vỡ cấu trúc.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_SWEEP' || reasonUpper === 'WAIT_SWEEP') {
    return {
      headline: 'Chờ quét thanh khoản',
      detail: 'Đang theo dõi vùng giá trọng tâm, chờ quét qua các mốc thanh khoản.',
      tone: 'info',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  if (stageUpper === 'WAIT_POI') {
    return {
      headline: 'Chờ tiếp cận vùng',
      detail: 'Đang chờ giá di chuyển vào vùng giá quan tâm (POI) đã chọn.',
      tone: 'neutral',
      asOfMs,
      isHistoricalReference: false,
    };
  }

  // 4. Default observing
  const fallbackDetail = description && description.trim().length > 0
    ? description
    : 'Đang quét thị trường tìm cấu trúc SMC đủ điều kiện.';

  return {
    headline: 'Đang theo dõi',
    detail: fallbackDetail,
    tone: tone || 'neutral',
    asOfMs,
    isHistoricalReference: false,
  };
}

/**
 * Formats epoch ms into localized Vietnam time (HH:MM:SS) or date string.
 */
export function formatMonitorTime(epochMs: number | null | undefined): string {
  if (!epochMs || epochMs <= 0) return '--:--:--';
  return new Date(epochMs).toLocaleTimeString('vi-VN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatMonitorDateTime(epochMs: number | null | undefined): string {
  if (!epochMs || epochMs <= 0) return '--/--/---- --:--';
  const d = new Date(epochMs);
  return `${d.toLocaleDateString('vi-VN')} ${d.toLocaleTimeString('vi-VN', { hour12: false })}`;
}

/**
 * Phần 052 - 055, C2: Pure helper converting filter selections into exact half-open [startMs, endMs) UTC timestamps.
 * Guarantees consistent Asia/Ho_Chi_Minh boundary interpretation regardless of browser local timezone.
 */
export function buildMonitorTimeWindow(
  filters: { period: 'today' | '7d' | 'custom'; customStartDate?: string; customEndDate?: string },
  timezone: string = 'Asia/Ho_Chi_Minh',
  nowMs: number = Date.now()
): { startMs: number | null; endMs: number; start_ms: number | null; end_ms: number } {
  let startMs: number | null = null;
  let endMs: number = nowMs;

  if (filters.period === 'today') {
    // Determine start of today at 00:00:00 in Vietnam timezone (+07:00)
    try {
      const formatter = new Intl.DateTimeFormat('en-CA', {
        timeZone: timezone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      });
      const dateStr = formatter.format(new Date(nowMs)); // "YYYY-MM-DD"
      startMs = new Date(`${dateStr}T00:00:00+07:00`).getTime();
    } catch {
      // Fallback
      const d = new Date(nowMs);
      d.setHours(0, 0, 0, 0);
      startMs = d.getTime();
    }
    endMs = nowMs;
  } else if (filters.period === '7d') {
    startMs = nowMs - 7 * 24 * 60 * 60 * 1000;
    endMs = nowMs;
  } else if (filters.period === 'custom') {
    if (filters.customStartDate) {
      startMs = new Date(`${filters.customStartDate}T00:00:00+07:00`).getTime();
    }
    if (filters.customEndDate) {
      // Half-open: exclusive end is the start of the next calendar day
      endMs = new Date(`${filters.customEndDate}T00:00:00+07:00`).getTime() + 24 * 60 * 60 * 1000;
    } else if (startMs !== null) {
      endMs = startMs + 24 * 60 * 60 * 1000;
    }
  }

  return { startMs, endMs, start_ms: startMs, end_ms: endMs };
}

import { getCanonicalSetupPlan } from './planLevels';
import type {
  BoundMonitorQuery,
  MonitorResolution,
  MonitorQueryParams,
  OpportunityMonitorSummary,
} from '../types/paperOpportunityMonitor';

/**
 * Phần 011 - 020, B2: Pure resolution of authoritative query scope and bound context from Summary response.
 */
export function asResolvedMonitorQuery(
  summary: OpportunityMonitorSummary | null | undefined,
  requested?: MonitorQueryParams
): MonitorResolution {
  if (!summary || typeof summary !== 'object') {
    return {
      status: 'UNAVAILABLE',
      reason: 'MISSING_SUMMARY',
      message_vi: 'Chưa nhận được phản hồi tóm tắt từ máy chủ.',
    };
  }

  const qs = summary.query_scope || {};
  const effectiveRunId =
    (typeof qs.run_id === 'string' && qs.run_id.trim() ? qs.run_id.trim() : null) ||
    (typeof summary.coverage?.run_id === 'string' && summary.coverage.run_id.trim()
      ? summary.coverage.run_id.trim()
      : null) ||
    (typeof summary.run_id === 'string' && summary.run_id.trim() ? summary.run_id.trim() : null) ||
    (typeof requested?.run_id === 'string' && requested.run_id.trim() ? requested.run_id.trim() : null);

  if (!effectiveRunId) {
    return {
      status: 'UNAVAILABLE',
      reason: summary.status === 'NOT_CONFIGURED' ? 'NOT_CONFIGURED' : 'NO_ACTIVE_RUN',
      message_vi:
        summary.status === 'NOT_CONFIGURED'
          ? 'Chưa cấu hình phiên quan sát phù hợp.'
          : 'Chưa có phiên quan sát phù hợp.',
    };
  }

  const queryKey =
    typeof summary.query_key === 'string' && summary.query_key.trim()
      ? summary.query_key.trim()
      : null;

  const startMs =
    typeof qs.start_ms === 'number' && Number.isFinite(qs.start_ms)
      ? qs.start_ms
      : typeof requested?.start_ms === 'number' && Number.isFinite(requested.start_ms)
      ? requested.start_ms
      : null;

  const endMs =
    typeof qs.end_ms === 'number' && Number.isFinite(qs.end_ms)
      ? qs.end_ms
      : typeof requested?.end_ms === 'number' && Number.isFinite(requested.end_ms)
      ? requested.end_ms
      : null;

  if (startMs == null || endMs == null || startMs >= endMs) {
    return {
      status: 'UNAVAILABLE',
      reason: 'INVALID_TIME_WINDOW',
      message_vi: 'Khoảng thời gian hiệu lực từ máy chủ không hợp lệ.',
    };
  }

  const symbol = (qs.symbol || requested?.symbol || 'XAUUSDT').toUpperCase();
  const scope = (qs.scope || requested?.scope || 'CG01').toUpperCase();

  const bound: BoundMonitorQuery = {
    symbol,
    scope,
    run_id: effectiveRunId,
    model_id: qs.model_id ?? (requested?.model_id ?? null),
    session_id: qs.session_id ?? (requested?.session_id ?? null),
    start_ms: startMs,
    end_ms: endMs,
    query_key: queryKey || '',
  };

  if (summary.status === 'EMPTY') {
    return { status: 'EMPTY', query: bound };
  }

  return { status: 'RESOLVED', query: bound };
}

/**
 * Phần 013, B1: Pure adapter creating exact request params from bound query.
 */
export function toMonitorRequestParams(q: BoundMonitorQuery): MonitorQueryParams {
  return {
    symbol: q.symbol,
    scope: q.scope,
    run_id: q.run_id,
    model_id: q.model_id ?? undefined,
    session_id: q.session_id ?? undefined,
    start_ms: q.start_ms,
    end_ms: q.end_ms,
  };
}

/**
 * Phần 017, B5: Verifies response envelope belongs to the pinned bound context.
 */
export function assertMonitorResponseContext(
  response: any,
  bound: BoundMonitorQuery,
  resourceKind: 'LIST' | 'DETAIL' | 'FOCUS'
): void {
  if (!response || typeof response !== 'object') {
    throw new Error(`MALFORMED_RESPONSE: Phản hồi ${resourceKind} rỗng hoặc không đúng định dạng.`);
  }

  if (response.query_key && response.query_key !== bound.query_key) {
    throw new Error(
      `SCOPE_MISMATCH: Khóa truy vấn của ${resourceKind} (${response.query_key}) không khớp với phiên đã liên kết (${bound.query_key}).`
    );
  }

  if (resourceKind === 'LIST') {
    if (response.run_id && response.run_id !== bound.run_id) {
      throw new Error(
        `RUN_MISMATCH: Danh sách thuộc phiên '${response.run_id}', không khớp với phiên quan sát '${bound.run_id}'.`
      );
    }
  } else if (resourceKind === 'DETAIL') {
    if (response.item?.run_id && response.item.run_id !== bound.run_id) {
      throw new Error(
        `RUN_MISMATCH: Cơ hội thuộc phiên '${response.item.run_id}', không khớp với phiên đang xem '${bound.run_id}'.`
      );
    }
  }
}

/**
 * Phần 093, G3: Pure filename formatter respecting local Asia/Ho_Chi_Minh calendar boundaries.
 */
export function formatMonitorReportFilename(
  bound: { symbol: string; start_ms: number; end_ms: number },
  timezone: string = 'Asia/Ho_Chi_Minh'
): string {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  const startDateStr = formatter.format(new Date(bound.start_ms));
  // End timestamp is half-open (start of next day); subtract 1ms for the calendar label of the end day
  const endDateStr = formatter.format(new Date(Math.max(bound.start_ms, bound.end_ms - 1)));
  const dateTag = startDateStr === endDateStr ? startDateStr : `${startDateStr}_to_${endDateStr}`;
  return `aurum-opportunity-report-${bound.symbol}-${dateTag}.md`;
}

/**
 * Phần 095, C7: Pure client-side validation of canonical chart focus context.
 * Enforces identity, geometry, finite prices, and discriminated kind.
 */
export function validateMonitorChartFocusContext(
  result: any,
  expected?: {
    queryKey?: string | null;
    opportunityId?: string;
    expectedOpportunityId?: string;
    targetId?: string;
    expectedTargetId?: string;
    kind?: string;
    expectedKind?: string;
    symbol?: string;
    expectedSymbol?: string;
    revision?: number;
  }
): import('../types/paperOpportunityMonitor').MonitorChartFocusContext {
  if (!result || typeof result !== 'object') {
    return {
      status: 'UNAVAILABLE',
      query_key: null,
      opportunity_id: expected?.expectedOpportunityId || expected?.opportunityId || '',
      reason_code: 'MALFORMED_RESPONSE',
      message_vi: 'Phản hồi từ máy chủ không hợp lệ.',
    };
  }

  if (result.status !== 'AVAILABLE') {
    const st =
      result.status === 'STALE_TARGET'
        ? 'STALE_TARGET'
        : result.status === 'SCOPE_MISMATCH'
        ? 'SCOPE_MISMATCH'
        : 'UNAVAILABLE';
    return {
      status: st,
      query_key: result.query_key ?? null,
      opportunity_id: result.opportunity_id || expected?.expectedOpportunityId || expected?.opportunityId || '',
      reason_code: result.reason_code || 'UNAVAILABLE',
      message_vi: result.message_vi || 'Mục tiêu biểu đồ không khả dụng.',
    };
  }

  // 1. Query Key Check
  if (expected?.queryKey && result.query_key && result.query_key !== expected.queryKey) {
    return {
      status: 'SCOPE_MISMATCH',
      query_key: result.query_key,
      opportunity_id: result.opportunity_id || '',
      reason_code: 'QUERY_KEY_MISMATCH',
      message_vi: 'Khóa truy vấn không khớp với phiên quan sát hiện tại.',
    };
  }

  // 2. Opportunity ID Check
  const expOppId = expected?.expectedOpportunityId || expected?.opportunityId;
  if (expOppId && result.opportunity_id !== expOppId) {
    return {
      status: 'SCOPE_MISMATCH',
      query_key: result.query_key ?? null,
      opportunity_id: result.opportunity_id || '',
      reason_code: 'OPPORTUNITY_MISMATCH',
      message_vi: 'Mã cơ hội trả về không khớp với lựa chọn hiện tại.',
    };
  }

  // 3. Target ID Check
  const expTargetId = expected?.expectedTargetId || expected?.targetId;
  if (expTargetId && result.target_id !== expTargetId) {
    return {
      status: 'SCOPE_MISMATCH',
      query_key: result.query_key ?? null,
      opportunity_id: result.opportunity_id || '',
      reason_code: 'TARGET_MISMATCH',
      message_vi: 'Mã mục tiêu không khớp với bằng chứng yêu cầu.',
    };
  }

  // 4. Kind Check
  const expKind = expected?.expectedKind || expected?.kind;
  if (expKind && result.kind !== expKind) {
    return {
      status: 'SCOPE_MISMATCH',
      query_key: result.query_key ?? null,
      opportunity_id: result.opportunity_id || '',
      reason_code: 'KIND_MISMATCH',
      message_vi: `Loại mục tiêu (${result.kind}) không khớp với yêu cầu (${expKind}).`,
    };
  }

  const dto = result.dto;
  if (!dto || typeof dto !== 'object') {
    return {
      status: 'UNAVAILABLE',
      query_key: result.query_key ?? null,
      opportunity_id: result.opportunity_id || '',
      reason_code: 'MISSING_DTO',
      message_vi: 'Không có dữ liệu DTO chi tiết từ máy chủ.',
    };
  }

  // 5. DTO ID Check
  if (expTargetId && dto.id && dto.id !== expTargetId) {
    return {
      status: 'SCOPE_MISMATCH',
      query_key: result.query_key ?? null,
      opportunity_id: result.opportunity_id || '',
      reason_code: 'DTO_ID_MISMATCH',
      message_vi: 'Mã định danh DTO không khớp với mục tiêu yêu cầu.',
    };
  }

  // Validate ORDER
  if (result.kind === 'ORDER') {
    const expSymbol = expected?.expectedSymbol || expected?.symbol;
    if (expSymbol && dto.instrument && dto.instrument.toUpperCase() !== expSymbol.toUpperCase()) {
      return {
        status: 'SCOPE_MISMATCH',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'SYMBOL_MISMATCH',
        message_vi: `Mã sản phẩm của lệnh (${dto.instrument}) không khớp với (${expSymbol}).`,
      };
    }

    const isFiniteNum = (v: any) => typeof v === 'number' && Number.isFinite(v) && v > 0;
    if (!isFiniteNum(dto.planned_entry) || !isFiniteNum(dto.stop_loss) || !isFiniteNum(dto.take_profit)) {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_PRICES',
        message_vi: 'Dữ liệu giá lệnh không hợp lệ hoặc thiếu giá trị.',
      };
    }
    if (dto.direction !== 'LONG' && dto.direction !== 'SHORT') {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_DIRECTION',
        message_vi: 'Hướng lệnh không xác định.',
      };
    }
    // Geometry check
    if (dto.direction === 'LONG' && (dto.stop_loss >= dto.planned_entry || dto.take_profit <= dto.planned_entry)) {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_GEOMETRY',
        message_vi: 'Cấu trúc giá lệnh LONG không hợp lệ (SL >= Entry hoặc TP <= Entry).',
      };
    }
    if (dto.direction === 'SHORT' && (dto.stop_loss <= dto.planned_entry || dto.take_profit >= dto.planned_entry)) {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_GEOMETRY',
        message_vi: 'Cấu trúc giá lệnh SHORT không hợp lệ (SL <= Entry hoặc TP >= Entry).',
      };
    }
    return {
      status: 'AVAILABLE',
      kind: 'ORDER',
      query_key: result.query_key,
      opportunity_id: result.opportunity_id,
      target_id: result.target_id,
      dto: dto,
    };
  }

  // Validate SETUP
  if (result.kind === 'SETUP') {
    // Check expected revision
    if (expected?.revision != null && dto.version != null && dto.version !== expected.revision) {
      return {
        status: 'STALE_TARGET',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'SETUP_REVISION_MISMATCH',
        message_vi: `Phiên bản kế hoạch hiện tại (v${dto.version}) đã khác thời điểm ghi nhận (v${expected.revision}).`,
      };
    }

    // Atomic price resolution & resolved_display_plan precedence via canonical helper
    const setupDto =
      dto.resolved_display_plan &&
      dto.resolved_display_plan.is_valid === undefined &&
      dto.resolved_display_plan.entry != null
        ? {
            ...dto,
            resolved_display_plan: {
              ...dto.resolved_display_plan,
              is_valid: true,
            },
          }
        : dto;
    const planRes = getCanonicalSetupPlan(setupDto);
    if (!planRes.isValid) {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_PRICES',
        message_vi: planRes.reason,
      };
    }

    const { entry, stopLoss: sl, takeProfit: tp, direction } = planRes.plan;
    const isFiniteNum = (v: any) => typeof v === 'number' && Number.isFinite(v) && v > 0;

    if (!isFiniteNum(entry) || !isFiniteNum(sl) || !isFiniteNum(tp)) {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_PRICES',
        message_vi: 'Kế hoạch thiếu thông số giá Entry / SL / TP hợp lệ.',
      };
    }
    if (direction !== 'LONG' && direction !== 'SHORT') {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_DIRECTION',
        message_vi: 'Hướng kế hoạch không xác định.',
      };
    }
    if (direction === 'LONG' && (sl >= entry || tp <= entry)) {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_GEOMETRY',
        message_vi: 'Cấu trúc giá kế hoạch LONG không hợp lệ (SL >= Entry hoặc TP <= Entry).',
      };
    }
    if (direction === 'SHORT' && (sl <= entry || tp >= entry)) {
      return {
        status: 'UNAVAILABLE',
        query_key: result.query_key ?? null,
        opportunity_id: result.opportunity_id || '',
        reason_code: 'INVALID_GEOMETRY',
        message_vi: 'Cấu trúc giá kế hoạch SHORT không hợp lệ (SL <= Entry hoặc TP >= Entry).',
      };
    }
    return {
      status: 'AVAILABLE',
      kind: 'SETUP',
      query_key: result.query_key,
      opportunity_id: result.opportunity_id,
      target_id: result.target_id,
      dto: dto,
    };
  }

  return {
    status: 'UNAVAILABLE',
    query_key: result.query_key ?? null,
    opportunity_id: result.opportunity_id || '',
    reason_code: 'UNSUPPORTED_KIND',
    message_vi: `Loại mục tiêu không được hỗ trợ: ${result.kind}`,
  };
}
