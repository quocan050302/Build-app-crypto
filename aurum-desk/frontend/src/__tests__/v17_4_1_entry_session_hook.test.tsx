// @vitest-environment happy-dom
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { useEntrySessionSettings, type UseEntrySessionSettingsResult } from '../hooks/useEntrySessionSettings';
import type { EntrySessionSettingsResponse } from '../api/client';

const mockConfirmedV1: EntrySessionSettingsResponse = {
  status: 'CONFIRMED',
  entry_session_policy: 'ALL_SESSIONS_WITH_NY_RESERVE',
  timing_profile: 'SILVER_BULLET_LONDON_NY',
  effective_ny_goal: 1,
  reserve_ny_slot: true,
  ny_fallback_enabled: true,
  max_daily_fills: 3,
  daily_timezone: 'Asia/Ho_Chi_Minh',
  policy_version: 1,
  execution_scope: 'PAPER',
  auto_execution_enabled: false,
  source: 'PERSISTED',
  reason_codes: [],
  message: null,
};

const mockConfirmedV2: EntrySessionSettingsResponse = {
  ...mockConfirmedV1,
  entry_session_policy: 'ALL_SESSIONS_SETUP_FIRST',
  timing_profile: 'CG01_ALL_SESSIONS_PAPER',
  effective_ny_goal: 0,
  reserve_ny_slot: false,
  ny_fallback_enabled: false,
  policy_version: 2,
  message: 'Đã lưu cấu hình phiên thành công!',
};

let latestHookResult: UseEntrySessionSettingsResult | null = null;
let host: HTMLDivElement;
let root: Root;
let requests: any[];

function HookHarness({ symbol }: { symbol?: string }) {
  const result = useEntrySessionSettings(symbol);
  useEffect(() => {
    latestHookResult = result;
  });
  return (
    <div>
      <span data-testid="status">{result.isLoading ? 'LOADING' : 'READY'}</span>
      <span data-testid="version">{result.settings?.policy_version ?? 'NONE'}</span>
      <span data-testid="policy">{result.settings?.entry_session_policy ?? 'NONE'}</span>
      <span data-testid="save-status">{result.saveStatus.type}</span>
      <span data-testid="save-message">{result.saveStatus.message ?? ''}</span>
      <span data-testid="conflict">{result.isStaleConflict ? 'CONFLICT' : 'NO_CONFLICT'}</span>
    </div>
  );
}

const makeResponse = (data: any, ok = true, status = 200) => ({
  ok,
  status,
  json: async () => data,
});

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  requests = [];
  latestHookResult = null;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it('Part 021/022: Initial load sets confirmed settings and populates drafts', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: any, options: any = {}) => {
      requests.push({ url: String(url), ...options });
      return makeResponse(mockConfirmedV1);
    })
  );

  await act(async () => {
    root.render(<HookHarness />);
  });

  expect(latestHookResult?.isLoading).toBe(false);
  expect(latestHookResult?.settings?.policy_version).toBe(1);
  expect(latestHookResult?.draftPolicy).toBe('ALL_SESSIONS_WITH_NY_RESERVE');
  expect(latestHookResult?.draftTiming).toBe('SILVER_BULLET_LONDON_NY');
  expect(latestHookResult?.isDirty).toBe(false);
});

it('Part 027/028: missing confirmed version prevents save without sending PUT', async () => {
  let resolveGet: any;
  const pendingGet = new Promise((resolve) => {
    resolveGet = resolve;
  });

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: any, options: any = {}) => {
      requests.push({ url: String(url), ...options });
      await pendingGet;
      return makeResponse(mockConfirmedV1);
    })
  );

  await act(async () => {
    root.render(<HookHarness />);
  });

  // Attempt save while GET is still pending (version is undefined)
  let success: boolean = true;
  await act(async () => {
    success = await latestHookResult!.saveSettings('ALL_SESSIONS_SETUP_FIRST', 'CG01_ALL_SESSIONS_PAPER');
  });

  expect(success).toBe(false);
  expect(latestHookResult?.saveStatus.type).toBe('error');
  expect(latestHookResult?.saveStatus.message).toContain('Chưa có phiên bản');
  expect(requests.filter((r) => r.method === 'PUT')).toHaveLength(0);

  // Complete pending GET
  await act(async () => {
    resolveGet();
  });
});

it('Part 027: in-flight double submission blocks second concurrent save', async () => {
  let resolvePut: any;
  const pendingPut = new Promise((resolve) => {
    resolvePut = resolve;
  });

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: any, options: any = {}) => {
      requests.push({ url: String(url), ...options });
      if (options.method === 'PUT') {
        await pendingPut;
        return makeResponse(mockConfirmedV2);
      }
      return makeResponse(mockConfirmedV1);
    })
  );

  await act(async () => {
    root.render(<HookHarness />);
  });

  expect(latestHookResult?.settings?.policy_version).toBe(1);

  // Trigger two saves simultaneously
  let promise1: Promise<boolean>;
  let promise2: Promise<boolean>;
  await act(async () => {
    promise1 = latestHookResult!.saveSettings('ALL_SESSIONS_SETUP_FIRST', 'CG01_ALL_SESSIONS_PAPER');
    promise2 = latestHookResult!.saveSettings('ALL_SESSIONS_SETUP_FIRST', 'CG01_ALL_SESSIONS_PAPER');
  });

  // Resolve the PUT
  await act(async () => {
    resolvePut();
    await Promise.all([promise1, promise2]);
  });

  // Exactly one PUT request was sent
  const puts = requests.filter((r) => r.method === 'PUT');
  expect(puts).toHaveLength(1);
});

it('Part 029/030: 409 conflict marks stale, does NOT auto-retry PUT', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: any, options: any = {}) => {
      requests.push({ url: String(url), ...options });
      if (options.method === 'PUT') {
        return makeResponse({ detail: 'STALE_POLICY_VERSION' }, false, 409);
      }
      return makeResponse(mockConfirmedV1);
    })
  );

  await act(async () => {
    root.render(<HookHarness />);
  });

  let saveResult: boolean = true;
  await act(async () => {
    saveResult = await latestHookResult!.saveSettings('ALL_SESSIONS_SETUP_FIRST', 'CG01_ALL_SESSIONS_PAPER');
  });

  expect(saveResult).toBe(false);
  expect(latestHookResult?.isStaleConflict).toBe(true);
  expect(latestHookResult?.saveStatus.type).toBe('error');
  expect(latestHookResult?.saveStatus.message).toContain('thay đổi ở nơi khác');

  // Verify only 1 PUT was attempted (zero auto-retry)
  const puts = requests.filter((r) => r.method === 'PUT');
  expect(puts).toHaveLength(1);
});

it('Part 025: Delayed GET resolving after PUT cannot revert newer confirmed state', async () => {
  let resolveDelayedGet: any;
  const delayedGetPromise = new Promise((resolve) => {
    resolveDelayedGet = resolve;
  });

  let getCount = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: any, options: any = {}) => {
      requests.push({ url: String(url), ...options });
      if (options.method === 'PUT') {
        return makeResponse(mockConfirmedV2);
      }
      getCount++;
      if (getCount === 1) {
        return makeResponse(mockConfirmedV1);
      } else {
        // Slow refresh GET
        await delayedGetPromise;
        return makeResponse(mockConfirmedV1); // Stale v1 response arriving late
      }
    })
  );

  await act(async () => {
    root.render(<HookHarness />);
  });
  expect(latestHookResult?.settings?.policy_version).toBe(1);

  // Trigger a background refresh (which hangs on delayedGetPromise)
  act(() => {
    latestHookResult!.refresh();
  });

  // User saves settings which succeeds and sets version 2
  await act(async () => {
    await latestHookResult!.saveSettings('ALL_SESSIONS_SETUP_FIRST', 'CG01_ALL_SESSIONS_PAPER');
  });
  expect(latestHookResult?.settings?.policy_version).toBe(2);

  // Now resolve the stale delayed GET
  await act(async () => {
    resolveDelayedGet();
  });

  // Confirmed version must stay 2 (not reverted back to 1)
  expect(latestHookResult?.settings?.policy_version).toBe(2);
  expect(latestHookResult?.settings?.entry_session_policy).toBe('ALL_SESSIONS_SETUP_FIRST');
});
