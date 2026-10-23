// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { CG01DiagnosticCard } from '../components/CG01DiagnosticCard';

const oldSettings = {
  status: 'CONFIRMED',
  entry_session_policy: 'ALL_SESSIONS_WITH_NY_RESERVE',
  timing_profile: 'SILVER_BULLET_LONDON_NY',
  effective_ny_goal: 1,
  reserve_ny_slot: true,
  ny_fallback_enabled: true,
  policy_version: 7,
};

const newSettings = {
  ...oldSettings,
  entry_session_policy: 'ALL_SESSIONS_SETUP_FIRST',
  timing_profile: 'CG01_ALL_SESSIONS_PAPER',
  effective_ny_goal: 0,
  reserve_ny_slot: false,
  ny_fallback_enabled: false,
  policy_version: 8,
};

const display: any = {
  stage: 'WAIT_SWEEP',
  stageLabel: 'Chờ sweep',
  tone: 'neutral',
  direction: null,
  poiId: null,
  poiIdCompact: null,
  description: 'Chờ cấu trúc',
  isStale: false,
  isConnected: true,
  asOfMs: 1,
  primaryReason: 'WAIT_SWEEP',
  allReasons: [],
  armedOrderId: null,
  directionPolicy: 'LEGACY_H1',
  dailyBias: null,
  dailyBiasSummary: null,
};

let host: HTMLDivElement, root: Root, requests: any[];
let sessionFail = false, directionFail = false;

const answer = (data: any, ok = true, status = 200) => ({
  ok,
  status,
  json: async () => data,
});

beforeEach(async () => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  requests = [];
  sessionFail = false;
  directionFail = false;

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: any, options: any = {}) => {
      requests.push({ url: String(url), ...options });
      if (options.method === 'PUT') {
        return answer(sessionFail ? { detail: 'SAVE_FAILED' } : newSettings, !sessionFail, sessionFail ? 500 : 200);
      }
      if (options.method === 'POST') {
        return answer(
          directionFail ? { detail: 'BIAS_SAVE_FAILED' } : { effective_config: { cg01_direction_policy: 'LEGACY_H1' } },
          !directionFail,
          directionFail ? 500 : 200
        );
      }
      return answer(String(url).includes('entry-session') ? oldSettings : { cg01_direction_policy: 'LEGACY_H1' });
    })
  );

  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<CG01DiagnosticCard displayState={display} />);
  });
  await act(async () => {
    host.querySelector<HTMLButtonElement>('[data-testid="cg01-toggle-settings-btn"]')!.click();
  });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function chooseNew() {
  await act(async () => {
    let s = host.querySelector<HTMLSelectElement>('[data-testid="cg01-timing-profile-select"]')!;
    s.value = 'CG01_ALL_SESSIONS_PAPER';
    s.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function save() {
  await act(async () => {
    host.querySelector<HTMLButtonElement>('[data-testid="cg01-save-policy-btn"]')!.click();
  });
}

async function saveDirection() {
  await act(async () => {
    host.querySelector<HTMLButtonElement>('[data-testid="cg01-save-direction-btn"]')!.click();
  });
}

it('applied banner must stay legacy until server confirms save', async () => {
  await chooseNew();
  expect(host.querySelector('[data-testid="v1639-paper-experiment-banner"]')!.textContent).not.toContain('Mọi phiên');
});

it('production save must send confirmed expected_version', async () => {
  await chooseNew();
  await save();
  const put = requests.find((x) => x.method === 'PUT');
  expect(JSON.parse(put.body).expected_version).toBe(7);
});

it('failed session save must not show draft mode as applied', async () => {
  sessionFail = true;
  await chooseNew();
  await save();
  expect(host.querySelector('[data-testid="cg01-policy-save-feedback"]')!.textContent).toContain('SAVE_FAILED');
  expect(host.querySelector('[data-testid="v1639-paper-experiment-banner"]')!.textContent).not.toContain('Mọi phiên');
});

it('changing session alone must not write direction policy', async () => {
  await chooseNew();
  await save();
  expect(requests.filter((x) => x.method === 'POST' && x.url === '/api/scalp/config')).toHaveLength(0);
});

it('direction save error must not be swallowed as successful combined save', async () => {
  directionFail = true;
  await saveDirection();
  expect(host.querySelector('[data-testid="cg01-direction-save-feedback"]')!.textContent).toContain('BIAS_SAVE_FAILED');
  expect(host.querySelector('[data-testid="cg01-direction-save-feedback"]')!.textContent).not.toContain('Đã lưu');
});
