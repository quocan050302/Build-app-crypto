import { describe, it, expect } from 'vitest';
import { extractErrorMessage } from './api/client';

describe('extractErrorMessage', () => {
  it('handles null/undefined gracefully', () => {
    expect(extractErrorMessage(null, 'Mặc định')).toBe('Mặc định');
    expect(extractErrorMessage(undefined, 'Mặc định')).toBe('Mặc định');
  });

  it('handles string input directly', () => {
    expect(extractErrorMessage('Lỗi trực tiếp')).toBe('Lỗi trực tiếp');
  });

  it('extracts detail object with message property', () => {
    const err = {
      response: {
        data: {
          detail: {
            code: 'INSUFFICIENT_RR',
            message: 'Tỷ lệ Net R:R (1.52) không đạt ngưỡng tối thiểu 2.0',
          },
        },
      },
    };
    expect(extractErrorMessage(err)).toBe('Tỷ lệ Net R:R (1.52) không đạt ngưỡng tối thiểu 2.0');
  });

  it('extracts detail object with reason or error property', () => {
    const err1 = { response: { data: { detail: { reason: 'Vượt hạn mức' } } } };
    expect(extractErrorMessage(err1)).toBe('Vượt hạn mức');

    const err2 = { response: { data: { detail: { error: 'Lỗi cú pháp' } } } };
    expect(extractErrorMessage(err2)).toBe('Lỗi cú pháp');
  });

  it('extracts FastAPI validation error array', () => {
    const err = {
      response: {
        data: {
          detail: [
            { loc: ['body', 'planned_entry'], msg: 'field required', type: 'value_error.missing' },
            { loc: ['body', 'stop_loss'], msg: 'field required', type: 'value_error.missing' },
          ],
        },
      },
    };
    expect(extractErrorMessage(err)).toBe('field required; field required');
  });

  it('extracts standard Error message', () => {
    const err = new Error('Network Error');
    expect(extractErrorMessage(err)).toBe('Network Error');
  });

  it('never outputs [object Object]', () => {
    const complexErr = {
      response: {
        data: {
          detail: {
            arbitrary: 'data',
            nested: { num: 123 },
          },
        },
      },
    };
    const res = extractErrorMessage(complexErr);
    expect(res).not.toContain('[object Object]');
  });
});
