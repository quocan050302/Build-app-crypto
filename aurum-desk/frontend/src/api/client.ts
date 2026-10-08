import axios from 'axios';
import type { AxiosInstance, AxiosRequestConfig } from 'axios';

const BASE_URL = import.meta.env.VITE_API_URL || '';

export const apiClient: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 8000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ==================== V5 TYPE DEFINITIONS ====================

export interface ArmSetupRequest {
  setup_id: string;
  setup_instance_id?: string;
  expected_revision?: number;
  expected_direction: 'LONG' | 'SHORT';
  idempotency_key?: string;
  order_type?: string;
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
  status: string;
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
  profit_factor: number;
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
  profit_factor: number;
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

// Request generation tracker to prevent stale responses
let currentRequestGeneration = 0;
export function getNextRequestGeneration(): number {
  currentRequestGeneration += 1;
  return currentRequestGeneration;
}

export function isLatestGeneration(gen: number): boolean {
  return gen === currentRequestGeneration;
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
    const res = await apiClient.post('/api/v1/lab/scenarios/run-all');
    return res.data;
  },

  runLabReplay: async (request: ReplayRunRequest): Promise<ReplayRunResponse> => {
    const res = await apiClient.post('/api/v1/lab/replay/run', request);
    return res.data;
  },

  runLabStress: async (request: StressTestRequest): Promise<StressTestResponse> => {
    const res = await apiClient.post('/api/v1/lab/stress/run', request);
    return res.data;
  },

  getInstrumentMetadata: async (symbol: string = 'XAUUSDT') => {
    const res = await apiClient.get('/api/v1/instrument/metadata', { params: { symbol } });
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

  getReports: async () => {
    const res = await apiClient.get('/api/v1/reports');
    return res.data;
  },

  generateReport: async (reportType: string = 'SESSION_REPORT') => {
    const res = await apiClient.post(`/api/v1/reports/generate?report_type=${reportType}`);
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

  getLessons: async () => {
    const res = await apiClient.get('/api/v1/lessons');
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

  getTelegramConfig: async () => {
    const res = await apiClient.get('/api/v1/telegram/config');
    return res.data;
  },

  updateTelegramConfig: async (config: any) => {
    const res = await apiClient.post('/api/v1/telegram/config', config);
    return res.data;
  },

  testTelegram: async (testData: { bot_token?: string; chat_id: string }) => {
    const res = await apiClient.post('/api/v1/telegram/test', testData);
    return res.data;
  },

  getNotificationHistory: async (limit: number = 50) => {
    const res = await apiClient.get('/api/v1/telegram/history', { params: { limit } });
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
};
