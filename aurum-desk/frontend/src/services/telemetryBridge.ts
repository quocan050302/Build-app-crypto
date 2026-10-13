/**
 * Test-only Telemetry Bridge (PHẦN 087, 088).
 * Tracks receive-to-render latency, queue depth, subscriber stability,
 * and chart instance lifecycle without leaking memory.
 */

export interface LatencyStats {
  p50: number;
  p95: number;
  p99: number;
  max: number;
  sample_count: number;
}

export interface SubscriberMeasurement {
  baseline_count: number;
  current_count: number;
  leak_delta: number;
  measured_at_ms: number;
}

export interface TelemetrySnapshot {
  render_latency_ms: LatencyStats;
  queue_current: number;
  queue_peak: number;
  active_subscriber_count: number;
  subscriber_baseline: number;
  subscriber_leak: number;
  reconnect_count: number;
  chart_create_count: number;
  chart_destroy_count: number;
  invalid_event_count: number;
  sequence_gap_count: number;
  dropped_execution_event_count: number;
  events_received_count: number;
  events_rendered_count: number;
  transport_kind: string;
  measurement_scope: string;
}

class TelemetryBridge {
  private renderLatenciesMs: number[] = [];
  private maxSamples = 10_000;
  private queueCurrent = 0;
  private queuePeak = 0;
  private activeSubscribers = 0;
  private subscriberBaseline = 0;
  private subscriberBaselineSet = false;
  private reconnectCount = 0;
  private chartCreateCount = 0;
  private chartDestroyCount = 0;
  private invalidEventCount = 0;
  private sequenceGapCount = 0;
  private droppedExecutionCount = 0;
  private eventsReceived = 0;
  private eventsRendered = 0;

  public reset(): void {
    this.renderLatenciesMs = [];
    this.queueCurrent = 0;
    this.queuePeak = 0;
    this.subscriberBaseline = 0;
    this.subscriberBaselineSet = false;
    this.reconnectCount = 0;
    this.chartCreateCount = 0;
    this.chartDestroyCount = 0;
    this.invalidEventCount = 0;
    this.sequenceGapCount = 0;
    this.droppedExecutionCount = 0;
    this.eventsReceived = 0;
    this.eventsRendered = 0;
  }

  public captureSubscriberBaseline(count?: number): void {
    this.subscriberBaseline = count !== undefined ? count : this.activeSubscribers;
    this.subscriberBaselineSet = true;
  }

  public getSubscriberMeasurement(): SubscriberMeasurement {
    const current = this.activeSubscribers;
    const baseline = this.subscriberBaselineSet ? this.subscriberBaseline : current;
    const leak = Math.max(0, current - baseline);
    return {
      baseline_count: baseline,
      current_count: current,
      leak_delta: leak,
      measured_at_ms: Date.now(),
    };
  }

  public recordChartCreate(): void {
    this.chartCreateCount += 1;
  }

  public recordChartDestroy(): void {
    this.chartDestroyCount += 1;
  }

  public acceptTransportFrame(): number {
    this.eventsReceived += 1;
    return typeof performance !== 'undefined' ? performance.now() : Date.now();
  }

  public recordEventReceived(): number {
    return this.acceptTransportFrame();
  }

  public markVisualCommit(_frameId: number | string, _kind: string, receivedAt: number, committedAt?: number): void {
    this.eventsRendered += 1;
    const now = committedAt ?? (typeof performance !== 'undefined' ? performance.now() : Date.now());
    const latency = Math.max(0, now - receivedAt);
    if (this.renderLatenciesMs.length < this.maxSamples) {
      this.renderLatenciesMs.push(latency);
    }
  }

  public recordRenderLatency(receiveTimeMs: number): void {
    this.markVisualCommit('legacy', 'RENDER_LATENCY', receiveTimeMs);
  }

  public updateQueueDepth(depth: number): void {
    this.queueCurrent = depth;
    if (depth > this.queuePeak) {
      this.queuePeak = depth;
    }
  }

  public setActiveSubscribers(count: number): void {
    this.activeSubscribers = count;
  }

  public recordReconnect(): void {
    this.reconnectCount += 1;
  }

  public recordSequenceGap(): void {
    this.sequenceGapCount += 1;
  }

  public recordInvalidEvent(): void {
    this.invalidEventCount += 1;
  }

  public recordDroppedExecution(): void {
    this.droppedExecutionCount += 1;
  }

  public getSnapshot(): TelemetrySnapshot {
    const latencies = [...this.renderLatenciesMs].sort((a, b) => a - b);
    const count = latencies.length;

    const calcPercentile = (p: number): number => {
      if (count === 0) return 0;
      const idx = Math.min(count - 1, Math.max(0, Math.floor((p / 100) * count)));
      return Number(latencies[idx].toFixed(2));
    };

    const maxVal = count > 0 ? Number(latencies[count - 1].toFixed(2)) : 0;

    const subMeas = this.getSubscriberMeasurement();

    return {
      render_latency_ms: {
        p50: calcPercentile(50),
        p95: calcPercentile(95),
        p99: calcPercentile(99),
        max: maxVal,
        sample_count: count,
      },
      queue_current: this.queueCurrent,
      queue_peak: this.queuePeak,
      active_subscriber_count: this.activeSubscribers,
      subscriber_baseline: subMeas.baseline_count,
      subscriber_leak: subMeas.leak_delta,
      reconnect_count: this.reconnectCount,
      chart_create_count: this.chartCreateCount,
      chart_destroy_count: this.chartDestroyCount,
      invalid_event_count: this.invalidEventCount,
      sequence_gap_count: this.sequenceGapCount,
      dropped_execution_event_count: this.droppedExecutionCount,
      events_received_count: this.eventsReceived,
      events_rendered_count: this.eventsRendered,
      transport_kind: 'WEBSOCKET',
      measurement_scope: 'QUOTE_DOM_COMMIT_RAF_PROXY',
    };
  }
}

export const telemetryBridge = new TelemetryBridge();

if (typeof window !== 'undefined' && import.meta.env.DEV) {
  (window as any).__AURUM_TELEMETRY__ = telemetryBridge;
}
