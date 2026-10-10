export interface ReportGeneratePayload {
  report_type?: string;
  selected_date?: string;
  time_of_day?: string;
  date_basis?: string;
  session?: string;
  mode?: string;
  as_of_ms?: number;
  custom_candles_15m?: any[];
}

export interface ResearchReportItem {
  id: number;
  report_type: string;
  session_name: string;
  created_at: number;
  d_4h_bias: string;
  h1_alignment: string;
  content_markdown: string;
  strategy_version?: string;
  research_date?: string | null;
  date_basis?: string;
  mode?: string;
  as_of_ms?: number;
  market_regime?: string;
  data_coverage_status?: string;
  quality_score?: number;
  scenarios?: any;
  structured_scenarios?: {
    bullish?: any;
    bearish?: any;
    no_trade?: any;
  } | null;
  timeframe_matrix?: any;
  provenance_metadata?: any;
  review_reference?: any;
}

import axios from 'axios';
import type { AxiosInstance, AxiosRequestConfig } from 'axios';

const BASE_URL = import.meta.env.VITE_API_URL || '';

export const apiClient: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * Safely extracts a human-readable error message from an error object (Axios / FastAPI / Error).
 * Never returns "[object Object]".
 */
export function extractErrorMessage(err: any, fallback: string = 'Đã xảy ra lỗi không xác định'): string {
  if (!err) return fallback;

  if (typeof err === 'string') return err;

  const status = err.response?.status;
  const responseData = err.response?.data;

  // U05 & U06: Differentiate 404 Route vs 404 Entity
  if (status === 404) {
    if (responseData?.detail === 'Not Found' || !responseData?.detail) {
      return 'API route không tồn tại trên backend (404 Not Found). Vui lòng kiểm tra phiên bản backend đang chạy hoặc cấu hình proxy.';
    }
    if (responseData?.detail?.code === 'LESSON_NOT_FOUND') {
      return responseData.detail.message || 'Không tìm thấy bài học này (ID không tồn tại trên hệ thống).';
    }
  }

  if (responseData) {
    const detail = responseData.detail;
    if (typeof detail === 'string') {
      if (detail === 'Not Found') {
        return 'API route không tồn tại trên backend (404 Not Found). Vui lòng kiểm tra phiên bản backend đang chạy hoặc cấu hình proxy.';
      }
      return detail;
    }
    if (Array.isArray(detail)) {
      // FastAPI ValidationError: [{ loc: [...], msg: "...", type: "..." }]
      const msgs = detail.map((d: any) => d.msg || (typeof d === 'object' ? JSON.stringify(d) : String(d))).filter(Boolean);
      if (msgs.length > 0) return msgs.join('; ');
    }
    if (detail && typeof detail === 'object') {
      if (detail.message) return String(detail.message);
      if (detail.reason) return String(detail.reason);
      if (detail.error) return String(detail.error);
      try {
        return JSON.stringify(detail);
      } catch {
        // fallback
      }
    }

    if (typeof responseData.message === 'string') {
      return responseData.message;
    }
    if (typeof responseData.error === 'string') {
      return responseData.error;
    }
  }

  if (typeof err.message === 'string' && err.message.trim().length > 0) {
    return err.message;
  }

  return fallback;
}


// ==================== V5 TYPE DEFINITIONS ====================

export interface ArmSetupRequest {
  setup_id: string;
  setup_instance_id?: string;
  expected_revision?: number;
  expected_direction: 'LONG' | 'SHORT';
  idempotency_key?: string;
  order_type?: string;
  planned_entry?: number;
  stop_loss?: number;
  take_profit?: number;
}

export interface ScenarioStepResult {
  step_index: number;
  name: string;
  status: 'PASS' | 'FAIL' | 'SKIPPED';
  expected: string;
  actual: string;
  detail?: string;
  timestamp: number;
  payload?: any;
}

export interface ScenarioRunResponse {
  scenario_id: string;
  name: string;
  description: string;
  status: 'PASS' | 'FAIL';
  steps: ScenarioStepResult[];
  started_at: number;
  completed_at: number;
  duration_ms: number;
  error?: string;
  config_version?: number;
  metadata_version?: number;
}

export interface ReplayTradeItem {
  id: string;
  setup_id?: string;
  direction: 'LONG' | 'SHORT';
  order_type: string;
  entry_time: number;
  entry_price: number;
  exit_time?: number;
  exit_price?: number;
  exit_cause?: string;
  stop_loss: number;
  take_profit: number;
  quantity: number;
  initial_risk_usdt: number;
  gross_pnl: number;
  fees: number;
  slippage: number;
  net_pnl: number;
  realized_r: number;
  session: string;
  is_ambiguous: boolean;
  notes?: string;
  is_win: boolean;
  cost_snapshot?: any;
  entry_session?: string;
  exit_session?: string;
  entry_fee?: number;
  exit_fee?: number;
  entry_slippage?: number;
  exit_slippage?: number;
  net_rr_planned?: number;
  net_rr_fill?: number;
  gross_rr?: number;
  strategy_family?: string;
  entry_type?: string;
  ny_session_id?: string;
  margin_usdt?: number;
}

// ==================== V7 TRADING POLICY & NY SESSION ====================

export interface TradingPolicy {
  id?: number;
  symbol: string;
  version?: number;
  is_active?: boolean;
  max_daily_fills: number;
  daily_timezone: string;
  entry_session_policy: string;
  ny_timezone: string;
  ny_entry_start: string;
  ny_entry_end: string;
  ny_min_fills: number;
  reserve_ny_slot: boolean;
  ny_fallback_enabled: boolean;
  ny_fallback_start: string;
  ny_fallback_risk_pct_cap: number;
  min_net_rr: number;
  max_open_positions: number;
  max_armed_orders: number;
  created_at?: number;
  updated_at?: number;
}

export interface NYSessionStatus {
  session_instance_id: string;
  symbol: string;
  is_in_ny_window: boolean;
  is_fallback_active: boolean;
  quota_state: string;
  daily_fills: number;
  max_daily_fills: number;
  remaining_daily_slots: number;
  ny_fills: number;
  ny_min_fills: number;
  reserved_slots: number;
  local_ny_time: string;
  local_vn_time: string;
  ny_window_display: string;
  vn_window_display: string;
  minutes_to_window_start?: number | null;
  minutes_to_fallback?: number | null;
  minutes_to_window_end?: number | null;
  allowed: boolean;
  reason_code?: string | null;
  reason_message?: string | null;
}


export interface EquityPoint {
  timestamp: number;
  equity: number;
  drawdown_usdt: number;
  drawdown_pct: number;
  daily_date: string;
}

export interface ReplayRunRequest {
  run_name?: string;
  symbol?: string;
  start_ts?: number;
  end_ts?: number;
  timeframe?: string;
  initial_equity?: number;
  risk_pct?: number;
  leverage?: number;
  margin_mode?: string;
  spread_multiplier?: number;
  slippage_multiplier?: number;
  fee_rate?: number;
  speed_ms?: number;
  seed?: number;
  custom_candles_json?: string;
  mode?: string; // 'HISTORICAL_MARKET' | 'SYNTHETIC_QA' | 'RECORDED_TICK'
  strategy_variant?: string; // 'CURRENT_BASELINE' | 'NY_ADAPTIVE' | 'NY_DAILY_PAPER_RESEARCH'
  ny_quota_target?: number;
  ny_deadline_hour?: number;
  ny_deadline_minute?: number;
  quota_risk_pct?: number;
  quality_risk_pct?: number;
  warmup_days?: number;
}

export interface JobCreateResponse {
  job_id: string;
  status: string;
  message: string;
  created_at: number;
  config_hash: string;
}

export interface JobStatusResponse {
  job_id: string;
  status: string;
  progress_pct: number;
  current_phase: string;
  completed_events: number;
  total_events: number;
  effective_config: Record<string, any>;
  quality_summary?: Record<string, any>;
  error_message?: string;
  created_at: number;
  updated_at: number;
}

export interface JobCancelResponse {
  job_id: string;
  status: string;
  message: string;
}

export interface ReplayRunResponse {
  id: string;
  run_name: string;
  symbol: string;
  start_ts: number;
  end_ts: number;
  initial_equity: number;
  final_equity: number;
  total_trades: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate_pct: number;
  profit_factor: number | null;
  max_drawdown_usdt: number;
  max_drawdown_pct: number;
  expectancy_r: number;
  total_net_pnl: number;
  total_fees: number;
  total_slippage: number;
  worst_day_pnl: number;
  max_consecutive_losses: number;
  loss_budget_breaches: number;
  signals_count: number;
  rejected_count: number;
  trades: ReplayTradeItem[];
  equity_curve: EquityPoint[];
  session_breakdown: Record<string, number>;
  rejection_reasons: Record<string, number>;
  warnings: string[];
  created_at: number;
  strategy_variant?: string;
  quality_trades_count?: number;
  quota_trades_count?: number;
  quality_net_pnl?: number;
  quota_net_pnl?: number;
  ny_fill_coverage_pct?: number;
  artifacts_dir?: string;
  dataset_hash?: string;
  dataset_type?: string;
  execution_fidelity?: string;
  cash_balance?: number;
  open_mtm?: number;
  news_coverage_status?: string;
  rules_coverage_status?: string;
  cost_model_version?: string;
  schema_version?: string;
}

export interface StressTestRequest {
  run_name?: string;
  symbol?: string;
  spread_multipliers?: number[];
  slippage_multipliers?: number[];
  fee_multipliers?: number[];
  latency_ms_list?: number[];
}

export interface StressTestResultRow {
  spread_mult: number;
  slippage_mult: number;
  fee_mult: number;
  latency_ms: number;
  trades_count: number;
  net_pnl: number;
  win_rate_pct: number;
  profit_factor: number | null;
  max_drawdown_pct: number;
  expectancy_r: number;
}

export interface StressTestResponse {
  id: string;
  run_name: string;
  symbol: string;
  baseline: StressTestResultRow;
  stress_matrix: StressTestResultRow[];
  created_at: number;
}

export interface NewsImportRowPreview {
  row_index: number;
  title: string;
  country: string;
  impact: string;
  source_time_str: string;
  scheduled_at_utc_ms: number;
  time_vn_str: string;
  forecast?: string;
  previous?: string;
  url?: string;
  gold_relevance: string;
  is_valid: boolean;
  error_message?: string;
}

export interface NewsImportPreviewResponse {
  total_rows: number;
  valid_count: number;
  invalid_count: number;
  source_timezone: string;
  preview_rows: NewsImportRowPreview[];
  errors: string[];
}

export interface NewsImportCommitResponse {
  status: string;
  imported_count: number;
  skipped_duplicates_count: number;
  error_count: number;
  message: string;
}

export interface NewsResearchResponse {
  news_id: number;
  title: string;
  country: string;
  impact: string;
  scheduled_at: number;
  source_url?: string;
  gold_relevance: string;
  research_status: string;
  meaning_vn: string;
  transmission_channels: Record<string, string>;
  pre_release_scenarios: string[];
  post_release_assessment?: string;
  citations: string[];
  historical_releases: Array<{
    date: string;
    time: string;
    actual: string;
    forecast: string;
    previous: string;
  }>;
  limitations: string;
  fetched_at?: number;
}

// Request generation tracker scoped per resource to prevent cross-component invalidation
const resourceGenerations = new Map<string, number>();

export function getNextRequestGeneration(resource: string = 'default'): number {
  const next = (resourceGenerations.get(resource) || 0) + 1;
  resourceGenerations.set(resource, next);
  return next;
}

export function isLatestGeneration(gen: number, resource: string = 'default'): boolean {
  return gen === (resourceGenerations.get(resource) || 0);
}

/**
 * Exponential backoff with jitter
 */
export async function fetchWithRetry<T>(
  fn: () => Promise<T>,
  retries: number = 2,
  baseDelayMs: number = 800
): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    if (retries <= 0) throw err;
    const jitter = Math.random() * 200;
    const delay = baseDelayMs + jitter;
    await new Promise((resolve) => setTimeout(resolve, delay));
    return fetchWithRetry(fn, retries - 1, baseDelayMs * 1.5);
  }
}

// API methods
export const api = {
  getHealth: async (symbol: string = 'XAUUSDT', timeframe: string = '15M') => {
    const res = await apiClient.get('/health', { params: { symbol, timeframe } });
    return res.data;
  },

  getCandles: async (
    symbol: string,
    timeframe: string,
    limit: number = 150,
    config?: AxiosRequestConfig
  ) => {
    const res = await apiClient.get(`/api/v1/candles/${symbol}/${timeframe}`, {
      params: { limit },
      ...config,
    });
    return res.data;
  },

  syncCandles: async (symbol: string, timeframe: string, limit: number = 150) => {
    const res = await apiClient.post(
      `/api/v1/candles/sync?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`
    );
    return res.data;
  },

  getTicker: async (symbol: string = 'XAUUSDT') => {
    const res = await apiClient.get(`/api/v1/ticker/${symbol}`);
    return res.data;
  },

  getAnalysis: async (symbol: string, timeframe: string, config?: AxiosRequestConfig) => {
    const res = await apiClient.get(`/api/v1/analysis/${symbol}/${timeframe}`, config);
    return res.data;
  },

  getMarketMatrix: async (symbol: string = 'XAUUSDT') => {
    const res = await apiClient.get('/api/v1/market/matrix', { params: { symbol } });
    return res.data;
  },

  getRvol: async (symbol: string = 'XAUUSDT', timeframe: string = '15M') => {
    const res = await apiClient.get(`/api/v1/volume/rvol/${symbol}/${timeframe}`);
    return res.data;
  },

  getUpcomingSetups: async () => {
    const res = await apiClient.get('/api/v1/setups/upcoming');
    return res.data;
  },

  armSetup: async (setupId: string, payload?: ArmSetupRequest) => {
    const res = await apiClient.post(`/api/v1/setups/arm/${setupId}`, payload);
    return res.data;
  },

  cancelSetup: async (setupId: string) => {
    const res = await apiClient.post(`/api/v1/setups/cancel/${setupId}`);
    return res.data;
  },

  // ==================== LAB & TESTING API ====================
  getLabScenarios: async () => {
    const res = await apiClient.get('/api/v1/lab/scenarios');
    return res.data;
  },

  runLabScenario: async (scenarioId: string): Promise<ScenarioRunResponse> => {
    const res = await apiClient.post(`/api/v1/lab/scenarios/run/${scenarioId}`);
    return res.data;
  },

  runAllLabScenarios: async (): Promise<{ status: string; passed_count: number; total_count: number; results: ScenarioRunResponse[] }> => {
    const res = await apiClient.post('/api/v1/lab/scenarios/run-all', {}, { timeout: 180000 });
    return res.data;
  },

  runLabReplay: async (request: ReplayRunRequest): Promise<ReplayRunResponse> => {
    const res = await apiClient.post('/api/v1/lab/replay/run', request, { timeout: 180000 });
    return res.data;
  },

  createLabJob: async (request: ReplayRunRequest): Promise<JobCreateResponse> => {
    const res = await apiClient.post('/api/v1/lab/jobs', request);
    return res.data;
  },

  getLabJobStatus: async (jobId: string): Promise<JobStatusResponse> => {
    const res = await apiClient.get(`/api/v1/lab/jobs/${jobId}`);
    return res.data;
  },

  cancelLabJob: async (jobId: string): Promise<JobCancelResponse> => {
    const res = await apiClient.post(`/api/v1/lab/jobs/${jobId}/cancel`);
    return res.data;
  },

  getLabJobResult: async (jobId: string): Promise<ReplayRunResponse> => {
    const res = await apiClient.get(`/api/v1/lab/jobs/${jobId}/result`);
    return res.data;
  },

  runLabStress: async (request: StressTestRequest): Promise<StressTestResponse> => {
    const res = await apiClient.post('/api/v1/lab/stress/run', request, { timeout: 180000 });
    return res.data;
  },

  getInstrumentMetadata: async (symbol: string = 'XAUUSDT') => {
    const now = Date.now();
    const cached = resourceGenerations.has(`meta_${symbol}`) ? (window as any)[`__meta_cache_${symbol}`] : null;
    const cacheTime = (window as any)[`__meta_time_${symbol}`] || 0;
    if (cached && (now - cacheTime < 300000)) {
      return cached;
    }
    const res = await apiClient.get('/api/v1/instrument/metadata', { params: { symbol } });
    (window as any)[`__meta_cache_${symbol}`] = res.data;
    (window as any)[`__meta_time_${symbol}`] = now;
    resourceGenerations.set(`meta_${symbol}`, 1);
    return res.data;
  },

  getAccountStatus: async () => {
    const res = await apiClient.get('/api/v1/account/status');
    return res.data;
  },

  updateAccountSettings: async (settings: { leverage: number; margin_mode: string; risk_pct?: number; expected_config_version?: number }) => {
    const res = await apiClient.post('/api/v1/account/settings', settings);
    return res.data;
  },

  getActivePosition: async () => {
    const res = await apiClient.get('/api/v1/positions/active');
    return res.data;
  },

  previewOrder: async (data: any) => {
    const res = await apiClient.post('/api/v1/orders/preview', data);
    return res.data;
  },

  createPaperOrder: async (orderData: any) => {
    const res = await apiClient.post('/api/v1/orders/paper', orderData);
    return res.data;
  },

  closePaperOrder: async (orderId: string) => {
    const res = await apiClient.post(`/api/v1/orders/close/${orderId}`);
    return res.data;
  },

  getReports: async (params?: {
    research_date?: string;
    date_basis?: string;
    session?: string;
    mode?: string;
    limit?: number;
    cursor?: number;
  }): Promise<{ session_info: any; reports: ResearchReportItem[]; filter_context?: any }> => {
    const res = await apiClient.get('/api/v1/reports', { params });
    return res.data;
  },

  generateReport: async (payloadOrType?: string | ReportGeneratePayload): Promise<any> => {
    let body: ReportGeneratePayload;
    if (typeof payloadOrType === 'string') {
      body = { report_type: payloadOrType };
    } else if (payloadOrType) {
      body = payloadOrType;
    } else {
      body = { report_type: 'SESSION_REPORT' };
    }
    const res = await apiClient.post('/api/v1/reports/generate', body);
    return res.data;
  },

  getReportDetail: async (reportId: number): Promise<ResearchReportItem> => {
    const res = await apiClient.get(`/api/v1/reports/${reportId}`);
    return res.data;
  },

  getReportReview: async (reportId: number): Promise<any> => {
    const res = await apiClient.get(`/api/v1/reports/${reportId}/review`);
    return res.data;
  },

  getNews: async () => {
    const res = await apiClient.get('/api/v1/news');
    return res.data;
  },

  importNews: async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await apiClient.post('/api/v1/news/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return res.data;
  },

  getJournal: async () => {
    const res = await apiClient.get('/api/v1/journal');
    return res.data;
  },

  getJournalPaginated: async (params?: JournalQueryParams): Promise<PaginatedJournalResponse> => {
    const res = await apiClient.get('/api/v1/journal/paginated', { params });
    return res.data;
  },

  getTradeReview: async (tradeId: string): Promise<TradeReviewData | null> => {
    try {
      const res = await apiClient.get(`/api/v1/journal/review/${tradeId}`);
      return res.data;
    } catch (err: any) {
      if (err.response?.status === 404) return null;
      throw err;
    }
  },

  saveTradeReview: async (tradeId: string, payload: TradeReviewPayload): Promise<TradeReviewData> => {
    const res = await apiClient.post(`/api/v1/journal/review/${tradeId}`, payload);
    return res.data;
  },

  getLessons: async (setupType?: string, status?: string): Promise<LessonItem[]> => {
    const res = await apiClient.get('/api/v1/lessons', { params: { setup_type: setupType, status } });
    return res.data;
  },

  approveLesson: async (lessonId: number): Promise<LessonItem> => {
    const res = await apiClient.post(`/api/v1/lessons/${lessonId}/approve`);
    return res.data;
  },

  rejectLesson: async (lessonId: number): Promise<LessonItem> => {
    const res = await apiClient.post(`/api/v1/lessons/${lessonId}/reject`);
    return res.data;
  },

  archiveLesson: async (lessonId: number): Promise<LessonItem> => {
    const res = await apiClient.post(`/api/v1/lessons/${lessonId}/archive`);
    return res.data;
  },

  updateLesson: async (lessonId: number, update: Partial<LessonItem>): Promise<LessonItem> => {
    const res = await apiClient.put(`/api/v1/lessons/${lessonId}`, update);
    return res.data;
  },

  toggleLessonEnable: async (lessonId: number): Promise<LessonItem> => {
    const res = await apiClient.post(`/api/v1/lessons/${lessonId}/toggle-enable`);
    return res.data;
  },

  setLessonEnable: async (lessonId: number, enabled: boolean, expectedRevision?: number): Promise<LessonItem> => {
    const res = await apiClient.post(`/api/v1/lessons/${lessonId}/set-enable`, {
      enabled,
      expected_revision: expectedRevision,
    });
    return res.data;
  },

  getLessonPolicy: async (): Promise<any> => {
    const res = await apiClient.get('/api/v1/lessons/policy');
    return res.data;
  },

  updateLessonPolicy: async (update: any): Promise<any> => {
    const res = await apiClient.put('/api/v1/lessons/policy', update);
    return res.data;
  },

  getSystemCapabilities: async (): Promise<any> => {
    const res = await apiClient.get('/api/v1/system/capabilities');
    return res.data;
  },

  validatePredicate: async (data: {
    predicate: any;
    severity?: string;
    effect?: string;
  }): Promise<{ is_valid: boolean; status: string; message: string; report: any }> => {
    const res = await apiClient.post('/api/v1/lessons/validate-predicate', data);
    return res.data;
  },

  getEducation: async () => {
    const res = await apiClient.get('/api/v1/education');
    return res.data;
  },

  getAutoState: async () => {
    const res = await apiClient.get('/api/v1/auto/state');
    return res.data;
  },

  setAutoState: async (enabled: boolean) => {
    const res = await apiClient.post('/api/v1/auto/state', { enabled });
    return res.data;
  },

  getTelegramConfig: async (): Promise<TelegramConfig> => {
    const res = await apiClient.get('/api/v1/telegram/config');
    return res.data;
  },

  updateTelegramConfig: async (config: any): Promise<TelegramConfig> => {
    const res = await apiClient.post('/api/v1/telegram/config', config);
    return res.data?.config || res.data;
  },

  testTelegram: async (testData: { bot_token?: string; chat_id: string }) => {
    const res = await apiClient.post('/api/v1/telegram/test', testData);
    return res.data;
  },

  getNotificationHistory: async (limit: number = 50): Promise<NotificationHistoryItem[]> => {
    const res = await apiClient.get('/api/v1/telegram/history', { params: { limit } });
    return res.data;
  },

  getNotificationHistoryPaginated: async (params?: NotificationHistoryParams): Promise<PaginatedNotificationHistoryResponse> => {
    const res = await apiClient.get('/api/v1/telegram/history', { params });
    if (Array.isArray(res.data)) {
      return {
        items: res.data,
        total: res.data.length,
        page: 1,
        page_size: res.data.length,
      };
    }
    return res.data;
  },

  retryOutboxItem: async (itemId: number) => {
    const res = await apiClient.post(`/api/v1/telegram/outbox/retry/${itemId}`);
    return res.data;
  },

  previewNewsImport: async (csvContent: string, sourceTimezone: string = 'America/New_York'): Promise<NewsImportPreviewResponse> => {
    const res = await apiClient.post('/api/v1/news/import/preview', { csv_content: csvContent, source_timezone: sourceTimezone });
    return res.data;
  },

  commitNewsImport: async (csvContent: string, sourceTimezone: string = 'America/New_York'): Promise<NewsImportCommitResponse> => {
    const res = await apiClient.post('/api/v1/news/import/commit', { csv_content: csvContent, source_timezone: sourceTimezone });
    return res.data;
  },

  getNewsResearch: async (newsId: number): Promise<NewsResearchResponse> => {
    const res = await apiClient.get(`/api/v1/news/${newsId}/research`);
    return res.data;
  },

  triggerNewsResearch: async (newsId: number): Promise<NewsResearchResponse> => {
    const res = await apiClient.post(`/api/v1/news/${newsId}/research`);
    return res.data;
  },

  getTradingPolicy: async (symbol: string = 'XAUUSDT'): Promise<TradingPolicy> => {
    const res = await apiClient.get('/api/v1/policy/trading', { params: { symbol } });
    return res.data;
  },

  updateTradingPolicy: async (policy: Partial<TradingPolicy>): Promise<TradingPolicy> => {
    const res = await apiClient.post('/api/v1/policy/trading', policy);
    return res.data;
  },

  getNYSessionStatus: async (symbol: string = 'XAUUSDT'): Promise<NYSessionStatus> => {
    const res = await apiClient.get('/api/v1/policy/ny-session', { params: { symbol } });
    return res.data;
  },
};

// ==================== V8 TYPE DEFINITIONS ====================

export interface TradeReviewData {
  id?: number;
  trade_id: string;
  user_notes?: string;
  self_reported_entry_reason?: string;
  psychology_before?: string;
  psychology_during?: string;
  psychology_after?: string;
  emotions?: string[];
  confidence_score?: number | null;
  discipline_score?: number | null;
  user_loss_reason?: string;
  mistakes?: string;
  what_went_well?: string;
  improvement_plan?: string;
  execution_mode?: string;
  reviewed_at?: string | null;
  created_at?: string;
  updated_at?: string;
  revision: number;
}

export interface TradeReviewPayload {
  user_notes?: string;
  self_reported_entry_reason?: string;
  psychology_before?: string;
  psychology_during?: string;
  psychology_after?: string;
  emotions?: string[];
  confidence_score?: number | null;
  discipline_score?: number | null;
  user_loss_reason?: string;
  mistakes?: string;
  what_went_well?: string;
  improvement_plan?: string;
  execution_mode?: string;
  expected_revision?: number;
}

export interface LessonItem {
  id: number;
  setup_type: string;
  outcome: string;
  title: string;
  description?: string;
  reflection?: string;
  category?: string;
  evidence_snapshot?: Record<string, any> | null;
  hypothesis?: string | null;
  author?: string;
  action_rule: string;
  is_approved: boolean;
  status: 'PENDING_REVIEW' | 'APPROVED' | 'REJECTED' | 'ARCHIVED';
  related_trade_id?: string | null;
  reviewed_at?: string | null;
  created_at: string;
  audit_trail?: any[];
  // V10 Governed Rule & Three-Color Fields
  severity?: 'INFO' | 'WARNING' | 'CRITICAL';
  effect?: 'ANNOTATE' | 'WARN_ENTRY' | 'BLOCK_ENTRY' | 'PROPOSE_PLAN_ADJUSTMENT';
  enabled?: boolean;
  validation_status?: 'VALID' | 'INVALID' | 'UNVALIDATED';
  validated_at?: number | null;
  validation_report?: string | null;
  scope?: string | null;
  predicate?: string | null;
  stage?: string;
  version?: number;
  revision?: number;
  effective_at?: number | null;
  expiry_at?: number | null;
}

export interface JournalSummary {
  total_filtered: number;
  completed_count: number;
  open_count: number;
  pending_count: number;
  wins: number;
  losses: number;
  breakevens: number;
  winrate_pct: number;
  net_pnl: number;
  average_realized_r: number;
}

export interface PaginatedJournalResponse {
  items: any[];
  total: number;
  page: number;
  page_size: number;
  summary: JournalSummary;
}

export interface JournalQueryParams {
  page?: number;
  page_size?: number;
  state?: string;
  direction?: string;
  outcome?: string;
  strategy_family?: string;
  start_date?: string;
  end_date?: string;
  has_review?: boolean;
  search?: string;
  sort_by?: string;
  sort_dir?: 'asc' | 'desc';
}

export interface TelegramConfig {
  enabled: boolean;
  bot_token?: string;
  bot_token_masked?: string;
  has_token: boolean;
  token_configured: boolean;
  chat_id: string;
  subscribed_events: string[];
  near_entry_mode?: string;
  near_entry_price_dist?: number;
  near_entry_atr_mult?: number;
  near_entry_cooldown_min?: number;
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string;
  quiet_hours_end?: string;
  quiet_hours_timezone?: string;
  bypass_critical_quiet_hours?: boolean;
  base_chart_url?: string;
}

export interface NotificationHistoryItem {
  id: number;
  message_type: string;
  priority?: string;
  status: 'PENDING' | 'SENDING' | 'SENT' | 'RETRYING' | 'FAILED' | 'AMBIGUOUS' | 'SUPPRESSED';
  created_at: string;
  sent_at?: string | null;
  next_attempt_at?: string | null;
  attempts: number;
  max_retries?: number;
  error_code?: string | null;
  error_message?: string | null;
  provider_message_id?: string | null;
  dedupe_key?: string | null;
  message_preview?: string | null;
}

export interface NotificationHistoryParams {
  page?: number;
  page_size?: number;
  status?: string;
  message_type?: string;
  start_date?: string;
  end_date?: string;
  entity_id?: string;
  limit?: number;
}

export interface PaginatedNotificationHistoryResponse {
  items: NotificationHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}


