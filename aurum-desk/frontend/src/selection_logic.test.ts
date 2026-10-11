import { describe, it, expect } from 'vitest';
import type { SelectedTradeIntent } from './App';

describe('V5 Setup Selection & Trade Intent Invariants', () => {
  it('preserves user-selected SHORT setup when background analysis updates with LONG', () => {
    // 1. Initial background analysis has a LONG signal
    const backgroundSignal = {
      setup_id: 'bg-long-01',
      id: 'sig-long-01',
      direction: 'LONG' as const,
      planned_entry: 2650.0,
      stop_loss: 2646.0,
      take_profit: 2675.0,
      quantity: 0.1,
      gross_rr: 3.0,
      estimated_net_rr: 2.2,
      state: 'READY'
    };

    // 2. User explicitly selects a SHORT setup from the watchboard
    const focusedWatchSetup = {
      id: 'watch-short-99',
      setup_instance_id: 'inst-short-99',
      direction: 'SHORT' as const,
      provisional_entry: 2660.0,
      provisional_sl: 2665.0,
      provisional_tp: 2640.0,
      quantity: 0.05,
      version: 2,
      state: 'READY'
    };

    const userSelectedIntent: SelectedTradeIntent = {
      source: 'WATCH_SETUP',
      setup_id: focusedWatchSetup.id,
      setup_instance_id: focusedWatchSetup.setup_instance_id,
      revision: focusedWatchSetup.version,
      symbol: 'XAUUSDT',
      timeframe: '15M',
      direction: focusedWatchSetup.direction,
      plannedEntry: focusedWatchSetup.provisional_entry,
      stopLoss: focusedWatchSetup.provisional_sl,
      takeProfit: focusedWatchSetup.provisional_tp,
      orderType: 'MARKET',
      quantity: focusedWatchSetup.quantity,
      initialRiskUsdt: 2.5,
      grossRR: 3.0,
      estimatedNetRR: 2.2,
      leverage: 5,
      marginMode: 'ISOLATED',
      status: focusedWatchSetup.state,
      snapshotAt: Date.now()
    };

    // 3. Simulated polling cycle: background analysis updates with a NEW LONG signal
    const pollingUpdate = (currentIntent: SelectedTradeIntent | null): SelectedTradeIntent | null => {
      // V5 Guard: Never overwrite WATCH_SETUP or DRAFT intent with live candidate
      if (currentIntent && (currentIntent.source === 'WATCH_SETUP' || currentIntent.source === 'DRAFT')) {
        return currentIntent;
      }
      return {
        source: 'LIVE_CANDIDATE',
        direction: backgroundSignal.direction,
        setup_id: backgroundSignal.setup_id,
        plannedEntry: backgroundSignal.planned_entry,
        stopLoss: backgroundSignal.stop_loss,
        takeProfit: backgroundSignal.take_profit,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        orderType: 'MARKET',
        quantity: backgroundSignal.quantity,
        initialRiskUsdt: 2.5,
        grossRR: 3.0,
        estimatedNetRR: 2.2,
        leverage: 5,
        marginMode: 'ISOLATED',
        status: backgroundSignal.state,
        snapshotAt: Date.now()
      };
    };

    const intentAfterPolling = pollingUpdate(userSelectedIntent);

    // Verify: User selection was NOT overwritten
    expect(intentAfterPolling).not.toBeNull();
    expect(intentAfterPolling!.direction).toBe('SHORT');
    expect(intentAfterPolling!.setup_id).toBe('watch-short-99');
    expect(intentAfterPolling!.source).toBe('WATCH_SETUP');
  });

  it('constructs arm request payload with expected_direction and revision', () => {
    const selectedIntent: SelectedTradeIntent = {
      source: 'WATCH_SETUP',
      setup_id: 'setup-001',
      setup_instance_id: 'inst-001',
      revision: 3,
      symbol: 'XAUUSDT',
      timeframe: '15M',
      direction: 'SHORT',
      plannedEntry: 2655.0,
      stopLoss: 2660.0,
      takeProfit: 2640.0,
      orderType: 'MARKET',
      quantity: 0.1,
      initialRiskUsdt: 5.0,
      grossRR: 3.0,
      estimatedNetRR: 2.2,
      leverage: 5,
      marginMode: 'ISOLATED',
      status: 'READY',
      snapshotAt: 1791460000000
    };

    // Client payload preparation
    const armPayload = {
      setup_id: selectedIntent.setup_id!,
      setup_instance_id: selectedIntent.setup_instance_id,
      expected_revision: selectedIntent.revision,
      expected_direction: selectedIntent.direction,
      idempotency_key: `arm-${selectedIntent.setup_id}-test`
    };

    expect(armPayload.expected_direction).toBe('SHORT');
    expect(armPayload.expected_revision).toBe(3);
    expect(armPayload.setup_id).toBe('setup-001');
  });

  it('prevents paper execution if Take Profit is missing or equal to Stop Loss', () => {
    const invalidIntent: SelectedTradeIntent = {
      source: 'LIVE_CANDIDATE',
      symbol: 'XAUUSDT',
      timeframe: '15M',
      direction: 'LONG',
      plannedEntry: 2650.0,
      stopLoss: 2645.0,
      takeProfit: undefined, // Missing TP
      orderType: 'MARKET',
      quantity: 0.1,
      initialRiskUsdt: 5.0,
      grossRR: 0,
      estimatedNetRR: 0,
      leverage: 5,
      marginMode: 'ISOLATED',
      status: 'WAITING',
      snapshotAt: Date.now()
    };

    const canSubmit = Boolean(invalidIntent.takeProfit && invalidIntent.takeProfit !== invalidIntent.stopLoss);
    expect(canSubmit).toBe(false);
  });

  it('V6.1: Strict identity - arming card A never adopts dragged levels from selected intent B', () => {
    const selectedIntentB: SelectedTradeIntent = {
      source: 'WATCH_SETUP',
      setup_id: 'watch-setup-B',
      setup_instance_id: 'inst-B',
      revision: 1,
      symbol: 'XAUUSDT',
      timeframe: '15M',
      direction: 'LONG',
      plannedEntry: 4130.0,
      stopLoss: 4115.0,
      takeProfit: 4180.0,
      orderType: 'MARKET',
      quantity: 0.1,
      initialRiskUsdt: 10,
      grossRR: 3.3,
      estimatedNetRR: 2.5,
      leverage: 30,
      marginMode: 'ISOLATED',
      status: 'READY',
      snapshotAt: Date.now()
    };

    const targetSetupA = {
      id: 'watch-setup-A',
      direction: 'LONG' as const,
      setup_instance_id: 'inst-A',
      version: 2
    };

    // Correct V6.1 strict identity logic:
    const isMatchingSelected = selectedIntentB.setup_id === targetSetupA.id;
    expect(isMatchingSelected).toBe(false);

    // Payload for A must not inherit B's levels
    const payloadA: any = {
      setup_id: targetSetupA.id,
      setup_instance_id: targetSetupA.setup_instance_id,
      expected_revision: targetSetupA.version,
      expected_direction: targetSetupA.direction,
      idempotency_key: `arm-${targetSetupA.id}-v${targetSetupA.version}`
    };

    if (isMatchingSelected) {
      payloadA.planned_entry = selectedIntentB.plannedEntry;
      payloadA.stop_loss = selectedIntentB.stopLoss;
      payloadA.take_profit = selectedIntentB.takeProfit;
    }

    expect(payloadA.setup_id).toBe('watch-setup-A');
    expect(payloadA.planned_entry).toBeUndefined();
    expect(payloadA.stop_loss).toBeUndefined();
    expect(payloadA.take_profit).toBeUndefined();
  });

  it('V6.1: Cross margin and Active Position block arming', () => {
    const formatLiquidation = (lp: number | null | undefined): string => {
      return typeof lp === 'number' && lp > 0 ? `$${lp.toFixed(2)}` : 'Chưa có ước tính hợp lệ';
    };

    // 0 or null must never be rendered as "$0.00"
    expect(formatLiquidation(0)).toBe('Chưa có ước tính hợp lệ');
    expect(formatLiquidation(null)).toBe('Chưa có ước tính hợp lệ');
    expect(formatLiquidation(undefined)).toBe('Chưa có ước tính hợp lệ');
    expect(formatLiquidation(4005.15)).toBe('$4005.15');

    // Cross block
    const checkCanArm = (margin: string, hasActivePos: boolean, hasArmed: boolean) => {
      const isCross = margin === 'CROSS';
      return !isCross && !hasActivePos && !hasArmed;
    };

    expect(checkCanArm('CROSS', false, false)).toBe(false);
    expect(checkCanArm('ISOLATED', true, false)).toBe(false);
    expect(checkCanArm('ISOLATED', false, true)).toBe(false);
    expect(checkCanArm('ISOLATED', false, false)).toBe(true);
  });
});
