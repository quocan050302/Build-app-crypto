import { describe, it, expect, vi } from 'vitest';
import { wsClient } from './services/wsClient';

describe('V7.2 Realtime WebSocket Transport & Ordered Queue', () => {
  it('manages multiple subscribers and dispatches messages without duplicates', () => {
    const handlerA = vi.fn();
    const handlerB = vi.fn();

    const unsubA = wsClient.subscribe(handlerA);
    const unsubB = wsClient.subscribe(handlerB);

    // Simulate incoming server broadcast via dispatchForTesting
    const testMsg = {
      type: 'DOMAIN_EVENT',
      event: { event_type: 'trade.opened', aggregate_id: 'trade-test-1' }
    };

    wsClient.dispatchForTesting(testMsg);

    expect(handlerA).toHaveBeenCalledTimes(1);
    expect(handlerA).toHaveBeenCalledWith(testMsg);
    expect(handlerB).toHaveBeenCalledTimes(1);
    expect(handlerB).toHaveBeenCalledWith(testMsg);

    // Unsubscribe A
    unsubA();
    wsClient.dispatchForTesting(testMsg);

    // A is not called again, B is called second time
    expect(handlerA).toHaveBeenCalledTimes(1);
    expect(handlerB).toHaveBeenCalledTimes(2);

    unsubB();
  });

  it('preserves boundary closed bars in ordered queue without dropping', () => {
    // Simulate ChartComponent's ordered pendingBarsQueue
    const pendingBarsQueue: any[] = [];
    const updatedSeriesBars: any[] = [];

    const mockSeries = {
      update: vi.fn((bar) => {
        updatedSeriesBars.push(bar);
      })
    };

    // Simulate rapid arrival of CANDLE_CLOSED followed immediately by CANDLE_UPDATE (new open bar)
    const closedBar = { time: 1728447000, open: 2650, high: 2655, low: 2649, close: 2654 };
    const newOpenBar = { time: 1728447060, open: 2654, high: 2654, low: 2654, close: 2654 };

    // Both push to queue
    pendingBarsQueue.push(closedBar);
    pendingBarsQueue.push(newOpenBar);

    // Drain queue in order (simulating requestAnimationFrame processing)
    while (pendingBarsQueue.length > 0) {
      const nextBar = pendingBarsQueue.shift();
      if (nextBar) {
        mockSeries.update(nextBar);
      }
    }

    expect(mockSeries.update).toHaveBeenCalledTimes(2);
    expect(updatedSeriesBars[0]).toEqual(closedBar);
    expect(updatedSeriesBars[1]).toEqual(newOpenBar);
    expect(updatedSeriesBars[0].time).toBe(1728447000);
    expect(updatedSeriesBars[1].time).toBe(1728447060);
  });
});
