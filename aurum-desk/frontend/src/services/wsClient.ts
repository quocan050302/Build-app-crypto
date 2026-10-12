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
  private statusSubscribers: Set<(open: boolean) => void> = new Set();
  private pingInterval: any = null;
  private reconnectTimeout: any = null;
  private attempt: number = 0;
  private isDisposed: boolean = false;
  private isConnecting: boolean = false;

  private generation: number = 0;

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
      const localWs = new WebSocket(wsUrl);
      this.ws = localWs;
      this.generation++;

      localWs.onopen = () => {
        if (this.ws !== localWs) return;
        this.isConnecting = false;
        this.attempt = 0;
        this.notifyStatus(true);

        // Clear existing ping interval
        if (this.pingInterval) clearInterval(this.pingInterval);
        this.pingInterval = setInterval(() => {
          if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send('ping');
          }
        }, 25000);
      };

      localWs.onmessage = (event: MessageEvent) => {
        if (this.ws !== localWs) return;
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

      localWs.onclose = () => {
        if (this.ws !== localWs) return;
        this.isConnecting = false;
        this.cleanupHeartbeat();
        this.notifyStatus(false);
        this.scheduleReconnect();
      };

      localWs.onerror = () => {
        if (this.ws !== localWs) return;
        this.isConnecting = false;
        this.cleanupHeartbeat();
        // onclose will be triggered
      };
    } catch {
      this.isConnecting = false;
      this.scheduleReconnect();
    }
  }

  private notifyStatus(open: boolean): void {
    for (const cb of this.statusSubscribers) {
      try {
        cb(open);
      } catch (err) {
        console.error('WS status handler error:', err);
      }
    }
  }

  /** True only while the socket is OPEN (used to decide whether polling fallback is needed). */
  public isOpen(): boolean {
    return typeof WebSocket !== 'undefined' && !!this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  /** Connection open/close notifications; the callback receives `true` on every (re)connect. */
  public subscribeStatus(cb: (open: boolean) => void): () => void {
    this.statusSubscribers.add(cb);
    return () => {
      this.statusSubscribers.delete(cb);
    };
  }

  private cleanupHeartbeat(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.isDisposed || this.subscribers.size === 0) return;
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }

    this.attempt++;
    const delay = Math.min(30000, 1000 * Math.pow(1.8, Math.min(this.attempt, 5))) + Math.random() * 500;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
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

  public closeForTesting(): void {
    if (this.ws) {
      this.ws.close();
    }
  }

  public getDiagnostics() {
    return {
      isOpen: this.isOpen(),
      isConnecting: this.isConnecting,
      subscriberCount: this.subscribers.size,
      statusSubscriberCount: this.statusSubscribers.size,
      attempt: this.attempt,
      isDisposed: this.isDisposed,
      generation: this.generation,
    };
  }

  public dispose(): void {
    this.isDisposed = true;
    this.cleanupHeartbeat();
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this.subscribers.clear();
    this.statusSubscribers.clear();
  }
}

export const wsClient = new SharedWSClient();

if (typeof window !== 'undefined' && import.meta.env.DEV) {
  (window as any).__AURUM_WS_CLIENT__ = wsClient;
}
