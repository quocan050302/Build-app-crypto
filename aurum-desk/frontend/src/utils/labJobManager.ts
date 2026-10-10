/**
 * Aurum Desk V12.4 Lab Replay Job State Machine & Persistence Manager.
 * Solves V124-07:
 * - Deterministic state machine (IDLE, SUBMITTING, QUEUED, RUNNING, CANCEL_REQUESTED, SUCCEEDED, FAILED, CANCELLED, CONNECTION_LOST, DETACHED_RUNNING)
 * - Safe persistence in sessionStorage (reconnect on reload/tab reopen)
 * - Unmount cleanup and single active poll loop
 * - Terminal states FAILED/CANCELLED never auto-rerun
 * - Timeout / network errors keep job reference and offer "Theo dõi lại"
 */

export type LabJobState =
  | 'IDLE'
  | 'SUBMITTING'
  | 'QUEUED'
  | 'RUNNING'
  | 'VALIDATING'
  | 'EXPORTING'
  | 'CANCEL_REQUESTED'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'CONNECTION_LOST'
  | 'DETACHED_RUNNING';

export const LAB_JOB_STORAGE_KEY = 'aurum_active_lab_job_id';

let memStorage: Record<string, string> = {};

function getStorage() {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      return window.sessionStorage;
    }
    if (typeof globalThis !== 'undefined' && (globalThis as any).sessionStorage) {
      return (globalThis as any).sessionStorage;
    }
  } catch {
    // Ignore security errors
  }
  return {
    getItem: (key: string) => (key in memStorage ? memStorage[key] : null),
    setItem: (key: string, val: string) => {
      memStorage[key] = String(val);
    },
    removeItem: (key: string) => {
      delete memStorage[key];
    },
    clear: () => {
      memStorage = {};
    },
  };
}

export class LabJobManager {
  static getPersistedJobId(): string | null {
    try {
      const storage = getStorage();
      return storage.getItem(LAB_JOB_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  static persistJobId(jobId: string): void {
    try {
      const storage = getStorage();
      storage.setItem(LAB_JOB_STORAGE_KEY, jobId);
    } catch {
      // Ignore
    }
  }

  static clearPersistedJobId(): void {
    try {
      const storage = getStorage();
      storage.removeItem(LAB_JOB_STORAGE_KEY);
    } catch {
      // Ignore
    }
  }

  static isTerminalState(status: string): boolean {
    return status === 'SUCCEEDED' || status === 'FAILED' || status === 'CANCELLED';
  }

  static isRetainableActiveState(status: string): boolean {
    return (
      status === 'QUEUED' ||
      status === 'RUNNING' ||
      status === 'VALIDATING' ||
      status === 'EXPORTING' ||
      status === 'CANCEL_REQUESTED' ||
      status === 'DETACHED_RUNNING' ||
      status === 'CONNECTION_LOST'
    );
  }

  static shouldRetainReferenceOnPollTimeout(attempts: number, maxAttempts: number): boolean {
    return attempts >= maxAttempts;
  }

  static shouldRetainReferenceOnNetworkError(consecutiveErrors: number): boolean {
    return consecutiveErrors > 0;
  }

  static getVietnameseStatusLabel(status: LabJobState | string): string {
    switch (status) {
      case 'SUBMITTING':
        return 'Đang gửi yêu cầu...';
      case 'QUEUED':
        return 'Đang xếp hàng chờ xử lý';
      case 'RUNNING':
        return 'Đang chạy mô phỏng replay';
      case 'VALIDATING':
        return 'Đang kiểm tra dữ liệu nến';
      case 'EXPORTING':
        return 'Đang xuất báo cáo & sổ cái';
      case 'CANCEL_REQUESTED':
        return 'Đang yêu cầu dừng tác vụ...';
      case 'SUCCEEDED':
        return 'Hoàn tất thành công';
      case 'FAILED':
        return 'Thất bại trên máy chủ';
      case 'CANCELLED':
        return 'Đã dừng theo yêu cầu';
      case 'DETACHED_RUNNING':
        return 'Đang chạy nền (quá thời gian chờ poll)';
      case 'CONNECTION_LOST':
        return 'Mất kết nối tạm thời';
      default:
        return 'Sẵn sàng';
    }
  }
}
