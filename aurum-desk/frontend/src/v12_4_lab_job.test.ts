import { describe, it, expect, beforeEach } from 'vitest';
import { LabJobManager, LAB_JOB_STORAGE_KEY } from './utils/labJobManager';

// Polyfill in-memory sessionStorage for Node testing environment if not present
if (typeof (globalThis as any).sessionStorage === 'undefined') {
  let store: Record<string, string> = {};
  (globalThis as any).sessionStorage = {
    getItem: (key: string) => (key in store ? store[key] : null),
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
}

describe('V12.4 Frontend Job Flow & Persistence Invariants (V124-07)', () => {
  beforeEach(() => {
    (globalThis as any).sessionStorage.clear();
  });

  it('T01: persists and retrieves active job ID without leakage', () => {
    expect(LabJobManager.getPersistedJobId()).toBeNull();
    LabJobManager.persistJobId('job-test-1234');
    expect(LabJobManager.getPersistedJobId()).toBe('job-test-1234');
    expect((globalThis as any).sessionStorage.getItem(LAB_JOB_STORAGE_KEY)).toBe('job-test-1234');

    LabJobManager.clearPersistedJobId();
    expect(LabJobManager.getPersistedJobId()).toBeNull();
  });

  it('T02: recognizes terminal states and prevents automatic rerun', () => {
    expect(LabJobManager.isTerminalState('SUCCEEDED')).toBe(true);
    expect(LabJobManager.isTerminalState('FAILED')).toBe(true);
    expect(LabJobManager.isTerminalState('CANCELLED')).toBe(true);

    expect(LabJobManager.isTerminalState('RUNNING')).toBe(false);
    expect(LabJobManager.isTerminalState('QUEUED')).toBe(false);
    expect(LabJobManager.isTerminalState('CANCEL_REQUESTED')).toBe(false);
  });

  it('T03: retains job reference on poll timeout (attempts >= MAX_POLLS)', () => {
    // Crucial V124-07 fix: do NOT clear currentJobId when polling times out!
    const MAX_POLLS = 300;
    expect(LabJobManager.shouldRetainReferenceOnPollTimeout(300, MAX_POLLS)).toBe(true);
    expect(LabJobManager.shouldRetainReferenceOnPollTimeout(350, MAX_POLLS)).toBe(true);
    expect(LabJobManager.shouldRetainReferenceOnPollTimeout(100, MAX_POLLS)).toBe(false);
  });

  it('T04: retains job reference on transient network disconnection', () => {
    expect(LabJobManager.shouldRetainReferenceOnNetworkError(1)).toBe(true);
    expect(LabJobManager.shouldRetainReferenceOnNetworkError(5)).toBe(true);
    expect(LabJobManager.shouldRetainReferenceOnNetworkError(0)).toBe(false);
  });

  it('T05: provides clear, informative Vietnamese status labels with actionable states', () => {
    expect(LabJobManager.getVietnameseStatusLabel('QUEUED')).toContain('xếp hàng');
    expect(LabJobManager.getVietnameseStatusLabel('RUNNING')).toContain('mô phỏng');
    expect(LabJobManager.getVietnameseStatusLabel('CANCEL_REQUESTED')).toContain('yêu cầu dừng');
    expect(LabJobManager.getVietnameseStatusLabel('DETACHED_RUNNING')).toContain('chạy nền');
    expect(LabJobManager.getVietnameseStatusLabel('CONNECTION_LOST')).toContain('Mất kết nối');
  });
});
