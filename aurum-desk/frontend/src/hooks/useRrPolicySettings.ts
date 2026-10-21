/**
 * frontend/src/hooks/useRrPolicySettings.ts
 * Aurum V16.39.16 — Single State Owner for Min Net R:R Settings
 *
 * Rules:
 * - One owner instance mounted at App root.
 * - Manages saved value, draft input, status, and authoritative mutation.
 * - Prevents race conditions with generation counter and abort control.
 * - Preserves user draft edits if background refresh completes while editing.
 * - Only sends { min_net_rr: value } on save, avoiding collateral mutation of other settings.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { normalizeRrPolicyResponse } from '../utils/rrPolicyView';

export interface RrPolicySaved {
  value: number;
  source: string;
  fingerprint?: string;
}

export type RrPolicyStatus = 'loading' | 'ready' | 'saving' | 'error';

export interface UseRrPolicySettingsReturn {
  saved: RrPolicySaved;
  draftText: string;
  isDirty: boolean;
  status: RrPolicyStatus;
  error: string | null;
  successMessage: string | null;
  setDraftText: (val: string) => void;
  setPreset: (val: number) => void;
  resetDraft: () => void;
  save: () => Promise<boolean>;
  refresh: () => Promise<void>;
}

export interface UseRrPolicySettingsOptions {
  onSaveSuccess?: (value: number) => void;
}

export function useRrPolicySettings(options?: UseRrPolicySettingsOptions): UseRrPolicySettingsReturn {
  const [saved, setSaved] = useState<RrPolicySaved>({
    value: 2.0,
    source: 'DEFAULT',
  });
  const [draftText, setDraftTextState] = useState<string>('2.00');
  const [status, setStatus] = useState<RrPolicyStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const isDirtyRef = useRef<boolean>(false);
  const draftTextRef = useRef<string>(draftText);
  const savedRef = useRef<RrPolicySaved>(saved);
  const requestGenRef = useRef<number>(0);
  const isSavingRef = useRef<boolean>(false);

  draftTextRef.current = draftText;
  savedRef.current = saved;

  const parsedDraft = parseFloat(draftText);
  const isDraftValid = Number.isFinite(parsedDraft) && parsedDraft >= 1.5;
  const isDirty =
    Boolean(draftText.trim()) &&
    isDraftValid &&
    Math.abs(parsedDraft - saved.value) > 1e-4;
  isDirtyRef.current = isDirty;

  const setDraftText = useCallback((val: string) => {
    setDraftTextState(val);
    setError(null);
    setSuccessMessage(null);
  }, []);

  const setPreset = useCallback((val: number) => {
    setDraftTextState(val.toFixed(2));
    setError(null);
    setSuccessMessage(null);
  }, []);

  const resetDraft = useCallback(() => {
    setDraftTextState(savedRef.current.value.toFixed(2));
    setError(null);
    setSuccessMessage(null);
  }, []);

  const fetchPolicy = useCallback(async () => {
    const currentGen = ++requestGenRef.current;
    setStatus((prev) => (prev === 'loading' ? 'loading' : prev));
    try {
      const res = await fetch('/api/scalp/config');
      if (currentGen !== requestGenRef.current) return;
      if (!res.ok) {
        throw new Error(`Máy chủ trả về mã lỗi HTTP ${res.status}`);
      }
      const data = await res.json();
      if (currentGen !== requestGenRef.current) return;

      const norm = normalizeRrPolicyResponse(data);
      if (norm.status === 'READY' && norm.value !== null) {
        const nextSaved: RrPolicySaved = {
          value: norm.value,
          source: norm.source,
          fingerprint: norm.fingerprint,
        };
        setSaved(nextSaved);
        savedRef.current = nextSaved;
        setStatus('ready');
        setError(null);

        // Only sync draft if user is NOT currently editing an unsaved draft
        if (!isDirtyRef.current) {
          setDraftTextState(norm.value.toFixed(2));
        }
      } else {
        setStatus('error');
        setError(norm.error || 'Cấu hình R:R không hợp lệ');
      }
    } catch (err: any) {
      if (currentGen !== requestGenRef.current) return;
      setStatus('error');
      setError(err?.message || 'Không thể tải cấu hình R:R');
    }
  }, []);

  useEffect(() => {
    fetchPolicy();
  }, [fetchPolicy]);

  const save = useCallback(async (): Promise<boolean> => {
    if (isSavingRef.current) return false;

    const val = parseFloat(draftTextRef.current);
    if (!Number.isFinite(val) || val < 1.5) {
      setError('Ngưỡng Min Net R:R phải là số hợp lệ từ 1.50 trở lên');
      return false;
    }

    isSavingRef.current = true;
    setStatus('saving');
    setError(null);
    setSuccessMessage(null);

    const saveGen = ++requestGenRef.current;
    const capturedDraft = draftTextRef.current;

    try {
      const res = await fetch('/api/scalp/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ min_net_rr: val }),
      });

      if (saveGen !== requestGenRef.current) {
        // Obsoleted by another newer operation
        isSavingRef.current = false;
        return false;
      }

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Lỗi máy chủ (${res.status})`);
      }

      const resData = await res.json();
      if (saveGen !== requestGenRef.current) {
        isSavingRef.current = false;
        return false;
      }

      const norm = normalizeRrPolicyResponse(resData);
      if (norm.status === 'READY' && norm.value !== null) {
        const newSaved: RrPolicySaved = {
          value: norm.value,
          source: norm.source,
          fingerprint: norm.fingerprint,
        };
        setSaved(newSaved);
        savedRef.current = newSaved;
        setStatus('ready');
        setSuccessMessage(`Đã lưu ngưỡng tối thiểu 1:${norm.value.toFixed(2)} (áp dụng cho đánh giá mới)`);

        // If user didn't continue typing during the request, update draft
        if (draftTextRef.current === capturedDraft) {
          setDraftTextState(norm.value.toFixed(2));
        }

        options?.onSaveSuccess?.(norm.value);
        isSavingRef.current = false;
        return true;
      } else {
        throw new Error(norm.error || 'Dữ liệu phản hồi sau khi lưu không hợp lệ');
      }
    } catch (err: any) {
      if (saveGen === requestGenRef.current) {
        setStatus('error');
        setError(err?.message || 'Lưu cài đặt R:R thất bại');
      }
      isSavingRef.current = false;
      return false;
    }
  }, [options]);

  return {
    saved,
    draftText,
    isDirty,
    status,
    error,
    successMessage,
    setDraftText,
    setPreset,
    resetDraft,
    save,
    refresh: fetchPolicy,
  };
}
