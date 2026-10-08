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

  getAccountStatus: async () => {
    const res = await apiClient.get('/api/v1/account/status');
    return res.data;
  },

  getActivePosition: async () => {
    const res = await apiClient.get('/api/v1/positions/active');
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
};
