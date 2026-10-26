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
  value: number | null;
  source: string;
  fingerprint?: string;
  isConfirmed: boolean;
}

export type RrPolicyStatus = 'loading' | 'ready' | 'saving' | 'error';
export type PolicyReadStatus = 'READY' | 'LOADING' | 'ERROR' | 'UNCONFIRMED';
export type ErrorOrigin = 'READ' | 'SAVE' | null;

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
  policyReadStatus: PolicyReadStatus;
  errorOrigin: ErrorOrigin;
  saveError: string | null;
  readError: string | null;
}

export interface UseRrPolicySettingsOptions {
  onSaveSuccess?: (saved: RrPolicySaved) => void | Promise<void>;
}

export function useRrPolicySettings(options?: UseRrPolicySettingsOptions): UseRrPolicySettingsReturn {
  const [saved, setSaved] = useState<RrPolicySaved>({
    value: null,
    source: 'UNCONFIRMED',
    isConfirmed: false,
  });
  const [draftText, setDraftTextState] = useState<string>('2.00');
  const [status, setStatus] = useState<RrPolicyStatus>('loading');
  const [policyReadStatus, setPolicyReadStatus] = useState<PolicyReadStatus>('LOADING');
  const [errorOrigin, setErrorOrigin] = useState<ErrorOrigin>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const isUserEditedRef = useRef<boolean>(false);
  const isDirtyRef = useRef<boolean>(false);
  const draftTextRef = useRef<string>(draftText);
  const savedRef = useRef<RrPolicySaved>(saved);
  const requestGenRef = useRef<number>(0);
  const isSavingRef = useRef<boolean>(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  draftTextRef.current = draftText;
  savedRef.current = saved;

  const parsedDraft = Number(draftText.trim());
  const isDirty = isUserEditedRef.current && (
    saved.value === null ||
    !Number.isFinite(parsedDraft) ||
    Math.abs(parsedDraft - saved.value) > 1e-4
  );
  isDirtyRef.current = isDirty;

  const setDraftText = useCallback((val: string) => {
    isUserEditedRef.current = true;
    setDraftTextState(val);
    setError(null);
    setSuccessMessage(null);
  }, []);

  const setPreset = useCallback((val: number) => {
    isUserEditedRef.current = true;
    setDraftTextState(val.toFixed(2));
    setError(null);
    setSuccessMessage(null);
  }, []);

  const resetDraft = useCallback(() => {
    isUserEditedRef.current = false;
    setDraftTextState(savedRef.current.value != null ? savedRef.current.value.toFixed(2) : '2.00');
    setError(null);
    setSuccessMessage(null);
  }, []);

  const fetchPolicy = useCallback(async (signal?: AbortSignal) => {
    const currentGen = ++requestGenRef.current;
    setStatus((prev) => (prev === 'loading' ? 'loading' : prev));
    setPolicyReadStatus((prev) => (prev === 'LOADING' ? 'LOADING' : prev));
    try {
      const res = await fetch('/api/scalp/config', { signal });
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
          isConfirmed: true,
        };
        setSaved(nextSaved);
        savedRef.current = nextSaved;
        setStatus('ready');
        setPolicyReadStatus('READY');
        setErrorOrigin(null);
        setError(null);
        setReadError(null);

        // Only sync draft if user is NOT currently editing an unsaved draft
        if (!isUserEditedRef.current) {
          setDraftTextState(norm.value.toFixed(2));
        }
      } else {
        const errMsg = norm.error || 'Cấu hình R:R không hợp lệ';
        setStatus('error');
        setPolicyReadStatus('ERROR');
        setErrorOrigin('READ');
        setReadError(errMsg);
        setError(errMsg);
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      if (currentGen !== requestGenRef.current) return;
      const errMsg = err?.message || 'Không thể tải cấu hình R:R';
      setStatus('error');
      setPolicyReadStatus('ERROR');
      setErrorOrigin('READ');
      setReadError(errMsg);
      setError(errMsg);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    abortControllerRef.current = controller;
    fetchPolicy(controller.signal);
    return () => {
      ++requestGenRef.current;
      controller.abort();
    };
  }, [fetchPolicy]);

  const save = useCallback(async (): Promise<boolean> => {
    if (isSavingRef.current) return false;

    const trimmed = draftTextRef.current.trim();
    const val = Number(trimmed);
    if (!Number.isFinite(val) || val < 1.5) {
      setError('Ngưỡng Min Net R:R phải là số hợp lệ từ 1.50 trở lên');
      return false;
    }

    isSavingRef.current = true;
    setStatus('saving');
    setError(null);
    setSaveError(null);
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
          isConfirmed: true,
        };
        setSaved(newSaved);
        savedRef.current = newSaved;
        setStatus('ready');
        setPolicyReadStatus('READY');
        setErrorOrigin(null);
        setSaveError(null);
        setReadError(null);
        setError(null);
        setSuccessMessage(`Đã lưu ngưỡng tối thiểu 1:${norm.value.toFixed(2)} (áp dụng cho đánh giá mới)`);

        isUserEditedRef.current = false;
        if (draftTextRef.current === capturedDraft) {
          setDraftTextState(norm.value.toFixed(2));
        }

        try {
          await Promise.resolve(options?.onSaveSuccess?.(newSaved));
        } catch (callbackErr) {
          console.warn('Post-save callback error (isolated from save success):', callbackErr);
        }
        isSavingRef.current = false;
        return true;
      } else {
        throw new Error(norm.error || 'Dữ liệu phản hồi sau khi lưu không hợp lệ');
      }
    } catch (err: any) {
      if (saveGen === requestGenRef.current) {
        const errMsg = err?.message || 'Lưu cài đặt R:R thất bại';
        setStatus('error');
        setErrorOrigin('SAVE');
        setSaveError(errMsg);
        setError(errMsg);
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
    refresh: () => fetchPolicy(),
    policyReadStatus,
    errorOrigin,
    saveError,
    readError,
  };
}
