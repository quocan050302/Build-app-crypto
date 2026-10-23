/**
 * frontend/src/hooks/useEntrySessionSettings.ts
 * V17.4: Hook managing coordinated entry session policy & timing profile settings.
 *
 * Conforms to Parts 122, 127, 128, 130:
 * - Manages confirmed state, draft state, loading, saving, and error states.
 * - Server response is the source of truth (does not infer success optimistically).
 * - AbortController and monotonic sequence tracking prevent out-of-order race conditions.
 * - Handles 409 conflict gracefully with clear user guidance.
 * - StrictMode safe with zero request loops.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { api, type EntrySessionSettingsResponse } from '../api/client';

export interface UseEntrySessionSettingsResult {
  settings: EntrySessionSettingsResponse | null;
  draftPolicy: string;
  draftTiming: string;
  setDraftPolicy: (p: string) => void;
  setDraftTiming: (t: string) => void;
  isLoading: boolean;
  isSaving: boolean;
  isStaleConflict: boolean;
  saveStatus: {
    type: 'idle' | 'saving' | 'success' | 'error';
    message?: string;
  };
  refresh: () => Promise<void>;
  saveSettings: (overridePolicy?: string, overrideTiming?: string) => Promise<boolean>;
}

export function useEntrySessionSettings(symbol: string = 'XAUUSDT'): UseEntrySessionSettingsResult {
  const [settings, setSettings] = useState<EntrySessionSettingsResponse | null>(null);
  const [draftPolicy, setDraftPolicy] = useState<string>('ALL_SESSIONS_WITH_NY_RESERVE');
  const [draftTiming, setDraftTiming] = useState<string>('SILVER_BULLET_LONDON_NY');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isStaleConflict, setIsStaleConflict] = useState<boolean>(false);
  const [saveStatus, setSaveStatus] = useState<{
    type: 'idle' | 'saving' | 'success' | 'error';
    message?: string;
  }>({ type: 'idle' });

  // Monotonic sequence tracking to prevent slow GETs from overwriting fresh PUT responses
  const lastMutationIdRef = useRef<number>(0);
  const abortControllerRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const ac = new AbortController();
    abortControllerRef.current = ac;
    const fetchId = Date.now();

    setIsLoading(true);
    try {
      const data = await api.getEntrySessionSettings(symbol);
      if (!ac.signal.aborted && fetchId >= lastMutationIdRef.current) {
        setSettings(data);
        setDraftPolicy(data.entry_session_policy);
        setDraftTiming(data.timing_profile);
        setIsStaleConflict(false);
      }
    } catch (err: any) {
      if (!ac.signal.aborted) {
        logger_warn('Failed to load entry session settings:', err);
      }
    } finally {
      if (!ac.signal.aborted) {
        setIsLoading(false);
      }
    }
  }, [symbol]);

  useEffect(() => {
    refresh();
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [refresh]);

  const saveSettings = useCallback(
    async (overridePolicy?: string, overrideTiming?: string): Promise<boolean> => {
      const pol = overridePolicy !== undefined ? overridePolicy : draftPolicy;
      const tim = overrideTiming !== undefined ? overrideTiming : draftTiming;
      const expectedVer = settings?.policy_version;

      setIsSaving(true);
      setSaveStatus({ type: 'saving', message: 'Đang lưu cấu hình phiên...' });
      setIsStaleConflict(false);
      lastMutationIdRef.current = Date.now();

      try {
        const resp = await api.updateEntrySessionSettings({
          entry_session_policy: pol,
          timing_profile: tim,
          expected_version: expectedVer,
          symbol,
          execution_scope: 'PAPER',
        });

        // Server confirmed
        setSettings(resp);
        setDraftPolicy(resp.entry_session_policy);
        setDraftTiming(resp.timing_profile);
        setSaveStatus({
          type: 'success',
          message: resp.message || 'Đã áp dụng cấu hình phiên thành công!',
        });
        setIsStaleConflict(false);

        setTimeout(() => {
          setSaveStatus((prev) => (prev.type === 'success' ? { type: 'idle' } : prev));
        }, 4000);

        return true;
      } catch (err: any) {
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail || err?.message || 'Lỗi khi lưu cấu hình';

        if (status === 409) {
          setIsStaleConflict(true);
          setSaveStatus({
            type: 'error',
            message: 'Cấu hình đã được thay đổi ở nơi khác; hãy tải lại trước khi lưu.',
          });
          // Refresh to sync current server version
          api.getEntrySessionSettings(symbol)
            .then((fresh) => {
              setSettings(fresh);
            })
            .catch(() => {});
        } else {
          setSaveStatus({
            type: 'error',
            message: detail,
          });
        }
        return false;
      } finally {
        setIsSaving(false);
      }
    },
    [draftPolicy, draftTiming, settings?.policy_version, symbol]
  );

  return {
    settings,
    draftPolicy,
    draftTiming,
    setDraftPolicy,
    setDraftTiming,
    isLoading,
    isSaving,
    isStaleConflict,
    saveStatus,
    refresh,
    saveSettings,
  };
}

function logger_warn(...args: any[]) {
  if (typeof console !== 'undefined' && console.warn) {
    console.warn(...args);
  }
}
