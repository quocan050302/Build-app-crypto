/**
 * frontend/src/hooks/useEntrySessionSettings.ts
 * V17.4.1: Production hook managing coordinated entry session policy & timing profile settings.
 *
 * Implements Parts 021 - 040:
 * - Part 021: Strict separation between confirmed server state and draft inputs.
 * - Part 022: Initial confirmed state is null; drafts populated only after confirmed GET.
 * - Part 023: Monotonic integer sequences (getSeq, mutationSeq) and epoch tracking.
 * - Part 024: AbortController signal forwarded to getEntrySessionSettings.
 * - Part 025: GET lease protection — late GETs cannot revert confirmed settings after PUT.
 * - Part 026: PUT lease protection — late PUT resolution cannot update wrong context.
 * - Part 027: Synchronous in-flight ref prevents duplicate submissions in same event loop.
 * - Part 028: expected_version strictly sourced from confirmed policy_version.
 * - Part 029/030: 409 stale version does not auto-retry PUT; user draft preserved.
 * - Part 031/032: Response status CONFIRMED checked; does not echo draft.
 * - Part 035: Timer cleanup with monotonic timer sequence tokens.
 * - Part 036: Independent loading and saving states with guarded completion.
 * - Part 037: isDirty cleanly derived from confirmed vs draft fields.
 * - Part 038: StrictMode safe with zero request loops.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { api, type EntrySessionSettingsResponse } from '../api/client';

export interface UseEntrySessionSettingsResult {
  settings: EntrySessionSettingsResponse | null;
  draftPolicy: string;
  draftTiming: string;
  setDraftPolicy: (p: string) => void;
  setDraftTiming: (t: string) => void;
  isDirty: boolean;
  isLoading: boolean;
  isSaving: boolean;
  isStaleConflict: boolean;
  loadError: string | null;
  saveStatus: {
    type: 'idle' | 'saving' | 'success' | 'error';
    message?: string;
  };
  refresh: () => Promise<void>;
  saveSettings: (overridePolicy?: string, overrideTiming?: string) => Promise<boolean>;
}

export function useEntrySessionSettings(symbol: string = 'XAUUSDT'): UseEntrySessionSettingsResult {
  const [settings, setSettings] = useState<EntrySessionSettingsResponse | null>(null);
  const [draftPolicy, setDraftPolicyState] = useState<string>('ALL_SESSIONS_WITH_NY_RESERVE');
  const [draftTiming, setDraftTimingState] = useState<string>('SILVER_BULLET_LONDON_NY');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isStaleConflict, setIsStaleConflict] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<{
    type: 'idle' | 'saving' | 'success' | 'error';
    message?: string;
  }>({ type: 'idle' });

  // Synchronous draft refs to eliminate any event batching delay
  const draftPolicyRef = useRef<string>('ALL_SESSIONS_WITH_NY_RESERVE');
  const draftTimingRef = useRef<string>('SILVER_BULLET_LONDON_NY');
  const isDraftDirtyRef = useRef<boolean>(false);

  // Monotonic sequence refs (Part 023)
  const contextEpochRef = useRef<number>(0);
  const getSequenceRef = useRef<number>(0);
  const mutationSequenceRef = useRef<number>(0);
  const inFlightSaveRef = useRef<boolean>(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef<boolean>(true);
  const timerRef = useRef<any>(null);
  const timerSequenceRef = useRef<number>(0);

  const setDraftPolicy = useCallback((p: string) => {
    isDraftDirtyRef.current = true;
    draftPolicyRef.current = p;
    setDraftPolicyState(p);
  }, []);

  const setDraftTiming = useCallback((t: string) => {
    isDraftDirtyRef.current = true;
    draftTimingRef.current = t;
    setDraftTimingState(t);
    if (t === 'CG01_ALL_SESSIONS_PAPER') {
      draftPolicyRef.current = 'ALL_SESSIONS_SETUP_FIRST';
      setDraftPolicyState('ALL_SESSIONS_SETUP_FIRST');
    } else {
      draftPolicyRef.current = 'ALL_SESSIONS_WITH_NY_RESERVE';
      setDraftPolicyState('ALL_SESSIONS_WITH_NY_RESERVE');
    }
  }, []);

  // Derive isDirty (Part 037)
  const isDirty = settings != null
    ? (draftPolicy !== settings.entry_session_policy || draftTiming !== settings.timing_profile)
    : false;

  const refresh = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const ac = new AbortController();
    abortControllerRef.current = ac;

    const currentEpoch = contextEpochRef.current;
    const getSeq = ++getSequenceRef.current;
    const mutationSeqAtStart = mutationSequenceRef.current;

    setIsLoading(true);
    setLoadError(null);

    try {
      const data = await api.getEntrySessionSettings(symbol, ac.signal);
      // Validate lease (Part 025): must not apply if epoch changed, newer GET started, or mutation occurred
      if (
        isMountedRef.current &&
        !ac.signal.aborted &&
        contextEpochRef.current === currentEpoch &&
        getSequenceRef.current === getSeq &&
        mutationSequenceRef.current === mutationSeqAtStart
      ) {
        setSettings(data);
        if (!isDraftDirtyRef.current) {
          draftPolicyRef.current = data.entry_session_policy;
          draftTimingRef.current = data.timing_profile;
          setDraftPolicyState(data.entry_session_policy);
          setDraftTimingState(data.timing_profile);
        }
        setIsStaleConflict(false);
        setLoadError(null);
      }
    } catch (err: any) {
      if (
        isMountedRef.current &&
        !ac.signal.aborted &&
        contextEpochRef.current === currentEpoch &&
        getSequenceRef.current === getSeq
      ) {
        setLoadError(err?.message || 'Không thể tải cấu hình phiên');
      }
    } finally {
      if (
        isMountedRef.current &&
        contextEpochRef.current === currentEpoch &&
        getSequenceRef.current === getSeq
      ) {
        setIsLoading(false);
      }
    }
  }, [symbol]);

  useEffect(() => {
    isMountedRef.current = true;
    contextEpochRef.current += 1;
    refresh();

    return () => {
      isMountedRef.current = false;
      contextEpochRef.current += 1;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [refresh]);

  const saveSettings = useCallback(
    async (overridePolicy?: string, overrideTiming?: string): Promise<boolean> => {
      // Part 027: synchronous in-flight guard against double-clicks
      if (inFlightSaveRef.current) {
        return false;
      }

      const currentEpoch = contextEpochRef.current;
      const expectedVer = settings?.policy_version;

      // Part 028: expected_version must be a positive integer from confirmed server response
      if (expectedVer == null || typeof expectedVer !== 'number' || expectedVer < 1) {
        setSaveStatus({
          type: 'error',
          message: 'Chưa có phiên bản cấu hình xác nhận từ máy chủ. Vui lòng tải lại trước khi lưu.',
        });
        return false;
      }

      inFlightSaveRef.current = true;
      const mutationSeq = ++mutationSequenceRef.current;
      const tim = overrideTiming !== undefined ? overrideTiming : draftTimingRef.current;
      let pol = overridePolicy !== undefined ? overridePolicy : draftPolicyRef.current;
      if (tim === 'CG01_ALL_SESSIONS_PAPER') {
        pol = 'ALL_SESSIONS_SETUP_FIRST';
      }

      // Invalidate pending GET
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }

      setIsSaving(true);
      setIsStaleConflict(false);
      setSaveStatus({ type: 'saving', message: 'Đang lưu cấu hình phiên...' });

      try {
        const resp = await api.updateEntrySessionSettings({
          entry_session_policy: pol,
          timing_profile: tim,
          expected_version: expectedVer,
          symbol,
        });

        // Lease check (Part 026)
        if (
          isMountedRef.current &&
          contextEpochRef.current === currentEpoch &&
          mutationSequenceRef.current === mutationSeq
        ) {
          if (resp.status === 'CONFIRMED') {
            isDraftDirtyRef.current = false;
            setSettings(resp);
            draftPolicyRef.current = resp.entry_session_policy;
            draftTimingRef.current = resp.timing_profile;
            setDraftPolicyState(resp.entry_session_policy);
            setDraftTimingState(resp.timing_profile);
            setIsStaleConflict(false);
            setSaveStatus({
              type: 'success',
              message: (resp.message && resp.message.toLowerCase().includes('đã lưu'))
                ? resp.message
                : `Đã lưu cấu hình! ${resp.message || ''}`.trim(),
            });

            // Part 035: timer cleanup
            if (timerRef.current) {
              clearTimeout(timerRef.current);
            }
            const scheduledTimerSeq = ++timerSequenceRef.current;
            timerRef.current = setTimeout(() => {
              if (
                isMountedRef.current &&
                contextEpochRef.current === currentEpoch &&
                timerSequenceRef.current === scheduledTimerSeq
              ) {
                setSaveStatus((prev) => (prev.type === 'success' ? { type: 'idle' } : prev));
              }
            }, 4000);

            return true;
          } else {
            setSaveStatus({
              type: 'error',
              message: resp.message || 'Cấu hình chưa được xác nhận do mâu thuẫn.',
            });
            return false;
          }
        }
        return false;
      } catch (err: any) {
        if (
          isMountedRef.current &&
          contextEpochRef.current === currentEpoch &&
          mutationSequenceRef.current === mutationSeq
        ) {
          const status = err?.status || err?.response?.status;
          const detail = err?.response?.data?.detail || err?.detail || err?.message || 'Lỗi khi lưu cấu hình';

          if (status === 409) {
            setIsStaleConflict(true);
            setSaveStatus({
              type: 'error',
              message: 'Cấu hình đã được thay đổi ở nơi khác; hãy tải lại trước khi lưu.',
            });
            // Part 029, 030: refresh confirmed settings without overwriting user's draft
            api.getEntrySessionSettings(symbol)
              .then((fresh) => {
                if (
                  isMountedRef.current &&
                  contextEpochRef.current === currentEpoch &&
                  mutationSequenceRef.current === mutationSeq
                ) {
                  setSettings(fresh);
                }
              })
              .catch(() => {});
          } else {
            setSaveStatus({
              type: 'error',
              message: typeof detail === 'string' ? detail : JSON.stringify(detail),
            });
          }
        }
        return false;
      } finally {
        inFlightSaveRef.current = false;
        if (
          isMountedRef.current &&
          contextEpochRef.current === currentEpoch &&
          mutationSequenceRef.current === mutationSeq
        ) {
          setIsSaving(false);
        }
      }
    },
    [settings, symbol]
  );

  return {
    settings,
    draftPolicy,
    draftTiming,
    setDraftPolicy,
    setDraftTiming,
    isDirty,
    isLoading,
    isSaving,
    isStaleConflict,
    loadError,
    saveStatus,
    refresh,
    saveSettings,
  };
}
