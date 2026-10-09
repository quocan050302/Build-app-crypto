export interface CanonicalQuote {
  symbol: string;
  last: number;
  bid?: number;
  ask?: number;
  spread?: number;
  exchange_ts_ms?: number;
  server_received_at_ms?: number;
  published_at_ms?: number;
  freshness_sec?: number;
  source?: string;
  updatedAt: number;
}

export interface QuoteDistanceResult {
  distanceUsdt: number | null;
  formatted: string;
  rawDistance: number | null;
  isAvailable: boolean;
  formattedUsdt: string;
}

export class CanonicalQuoteStore {
  private quotes: Map<string, CanonicalQuote> = new Map();
  private listeners: Map<string, Set<(quote: CanonicalQuote) => void>> = new Map();

  /**
   * Updates store with authoritative quote update from WebSocket.
   * Preserves chronological monotonicity (newer ticker always wins).
   */
  public updateFromQuoteEnvelope(envelope: any): boolean {
    if (!envelope || envelope.type !== 'QUOTE_UPDATE') return false;
    const symbol = envelope.symbol || 'XAUUSDT';
    const payload = envelope.payload || envelope.data || {};
    const last = typeof payload.last === 'number' ? payload.last : parseFloat(payload.last);
    if (!Number.isFinite(last)) return false;

    const exchangeTs =
      envelope.exchange_ts_ms ||
      envelope.timestamp ||
      payload.timestamp ||
      envelope.server_received_at_ms ||
      Date.now();
    const existing = this.quotes.get(symbol);

    // Monotonic freshness check: ignore out-of-order quotes with older exchange timestamp
    if (existing?.exchange_ts_ms && exchangeTs < existing.exchange_ts_ms) {
      return false;
    }

    const canonical: CanonicalQuote = {
      symbol,
      last,
      bid: typeof payload.bid === 'number' ? payload.bid : parseFloat(payload.bid) || undefined,
      ask: typeof payload.ask === 'number' ? payload.ask : parseFloat(payload.ask) || undefined,
      spread: typeof payload.spread === 'number' ? payload.spread : parseFloat(payload.spread) || undefined,
      exchange_ts_ms: exchangeTs,
      server_received_at_ms: envelope.server_received_at_ms,
      published_at_ms: envelope.published_at_ms,
      freshness_sec: payload.freshness_sec,
      source: payload.source || 'ws_ticker',
      updatedAt: Date.now(),
    };

    this.quotes.set(symbol, canonical);
    this.notify(symbol, canonical);
    return true;
  }

  /**
   * Handles candle updates without allowing old candle closes to overwrite newer ticker.
   */
  public updateFromCandle(symbol: string, closePrice: number, candleTsMs?: number): boolean {
    if (!Number.isFinite(closePrice)) return false;
    const existing = this.quotes.get(symbol);

    // If we have a fresh quote from exchange ticker that is newer than candle, don't overwrite last
    if (existing && existing.exchange_ts_ms && candleTsMs && existing.exchange_ts_ms > candleTsMs) {
      return false;
    }

    const canonical: CanonicalQuote = {
      symbol,
      last: closePrice,
      exchange_ts_ms: candleTsMs,
      source: 'candle_close',
      updatedAt: Date.now(),
    };
    this.quotes.set(symbol, canonical);
    this.notify(symbol, canonical);
    return true;
  }

  public getQuote(symbol: string = 'XAUUSDT'): CanonicalQuote | null {
    return this.quotes.get(symbol) || null;
  }

  public getLatestQuote(symbol: string = 'XAUUSDT'): CanonicalQuote | null {
    return this.getQuote(symbol);
  }

  public getLastPrice(symbol: string = 'XAUUSDT'): number | null {
    const q = this.getQuote(symbol);
    return q && Number.isFinite(q.last) ? q.last : null;
  }

  /**
   * Calculates distance between current live quote and planned entry price.
   * If quote is missing, returns formatted '-- / chưa có dữ liệu' (never 0.00).
   */
  public getDistance(symbol: string = 'XAUUSDT', plannedEntry: number): QuoteDistanceResult {
    if (!Number.isFinite(plannedEntry) || plannedEntry <= 0) {
      return {
        distanceUsdt: null,
        formatted: '-- / chưa có dữ liệu',
        rawDistance: null,
        isAvailable: false,
        formattedUsdt: '-- / chưa có dữ liệu',
      };
    }
    const q = this.getQuote(symbol);
    if (!q || !Number.isFinite(q.last) || q.last <= 0) {
      return {
        distanceUsdt: null,
        formatted: '-- / chưa có dữ liệu',
        rawDistance: null,
        isAvailable: false,
        formattedUsdt: '-- / chưa có dữ liệu',
      };
    }

    const dist = Math.abs(q.last - plannedEntry);
    const formatted = `${dist.toFixed(2)} USDT`;
    return {
      distanceUsdt: dist,
      formatted,
      rawDistance: dist,
      isAvailable: true,
      formattedUsdt: formatted,
    };
  }

  public subscribe(symbol: string, callback: (quote: CanonicalQuote) => void): () => void {
    if (!this.listeners.has(symbol)) {
      this.listeners.set(symbol, new Set());
    }
    this.listeners.get(symbol)!.add(callback);

    const current = this.quotes.get(symbol);
    if (current) {
      callback(current);
    }

    return () => {
      this.listeners.get(symbol)?.delete(callback);
    };
  }

  private notify(symbol: string, quote: CanonicalQuote): void {
    const subs = this.listeners.get(symbol);
    if (subs) {
      subs.forEach((cb) => {
        try {
          cb(quote);
        } catch {}
      });
    }
  }

  public clear(): void {
    this.quotes.clear();
  }
}

export const quoteStore = new CanonicalQuoteStore();
