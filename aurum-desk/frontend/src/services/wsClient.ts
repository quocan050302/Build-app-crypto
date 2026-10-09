/**
 * Shared WebSocket Client (V7.2):
 * - Single persistent WebSocket transport to /ws across the entire frontend.
 * - Heartbeat management (ping every 25s, pong detection).
 * - Exponential backoff auto-reconnect with jitter and 30s cap.
 * - Clean subscriber registry; eliminates duplicate connections and zombie sockets.
 */

type MessageHandler = (data: any) => void;

class SharedWSClient {
  private ws: WebSocket | null = null;
  private subscribers: Set<MessageHandler> = new Set();
  private pingInterval: any = null;
  private reconnectTimeout: any = null;
  private attempt: number = 0;
  private isDisposed: boolean = false;
  private isConnecting: boolean = false;

  constructor() {
    // Lazily initialized on first subscription or explicit start
  }

  private getWsUrl(): string {
    if (typeof window === 'undefined') {
      return 'ws://localhost:8000/ws';
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws`;
  }

  private connect(): void {
    if (this.isDisposed || this.isConnecting) return;
    if (typeof WebSocket === 'undefined') return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.isConnecting = true;
    const wsUrl = this.getWsUrl();

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.isConnecting = false;
        this.attempt = 0;

        // Clear existing ping interval
        if (this.pingInterval) clearInterval(this.pingInterval);
        this.pingInterval = setInterval(() => {
          if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send('ping');
          }
        }, 25000);
      };

      this.ws.onmessage = (event: MessageEvent) => {
        if (event.data === 'pong') return;

        try {
          const msg = JSON.parse(event.data);
          for (const handler of this.subscribers) {
            try {
              handler(msg);
            } catch (err) {
              console.error('WS Subscriber handler error:', err);
            }
          }
        } catch {
          // Ignore non-json or malformed
        }
      };

      this.ws.onclose = () => {
        this.isConnecting = false;
        this.cleanupHeartbeat();
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.isConnecting = false;
        this.cleanupHeartbeat();
        // onclose will be triggered
      };
    } catch {
      this.isConnecting = false;
      this.scheduleReconnect();
    }
  }

  private cleanupHeartbeat(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.isDisposed || this.subscribers.size === 0) return;
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);

    this.attempt++;
    const delay = Math.min(30000, 1000 * Math.pow(1.8, Math.min(this.attempt, 5))) + Math.random() * 500;
    this.reconnectTimeout = setTimeout(() => {
      this.connect();
    }, delay);
  }

  public subscribe(handler: MessageHandler): () => void {
    this.subscribers.add(handler);

    // If socket not active, start connection
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.connect();
    }

    return () => {
      this.subscribers.delete(handler);
      // If no subscribers left after grace period, could disconnect or keep warm
    };
  }

  public dispatchForTesting(data: any): void {
    for (const handler of this.subscribers) {
      try {
        handler(data);
      } catch (err) {
        console.error('WS Subscriber handler error:', err);
      }
    }
  }

  public dispose(): void {
    this.isDisposed = true;
    this.cleanupHeartbeat();
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this.subscribers.clear();
  }
}

export const wsClient = new SharedWSClient();
