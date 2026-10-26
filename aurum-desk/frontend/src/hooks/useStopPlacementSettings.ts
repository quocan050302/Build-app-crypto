/**
 * frontend/src/hooks/useStopPlacementSettings.ts
 * V17.5: Hook managing Stop Placement policy (LEGACY, SHADOW_COMPARE, PAPER_STRUCTURAL).
 *
 * Implements Parts 021 - 030, 154 - 155:
 * - Strict separation between confirmed server state and draft inputs.
 * - Initial confirmed state is null; drafts populated only after confirmed GET.
 * - expected_version strictly sourced from confirmed policy version.
 * - 409 stale version detection without corrupting user drafts.
 * - Clean isDirty derivation.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  getStopPlacementPolicy,
  updateStopPlacementPolicy,
  type StopPlacementPolicyDTO,
  extractErrorMessage,
} from '../api/client';

export interface UseStopPlacementSettingsResult {
  settings: StopPlacementPolicyDTO | null;
  draftMode: string;
  setDraftMode: (m: string) => void;
  draftAtrPeriod: number;
  setDraftAtrPeriod: (n: number) => void;
  draftMinBufferTicks: number;
  setDraftMinBufferTicks: (n: number) => void;
  draftAtrM1Multiplier: number;
  setDraftAtrM1Multiplier: (n: number) => void;
  draftAtrM5Multiplier: number;
  setDraftAtrM5Multiplier: (n: number) => void;
  draftSpreadMultiplier: number;
  setDraftSpreadMultiplier: (n: number) => void;
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
  saveSettings: () => Promise<boolean>;
  resetDrafts: () => void;
}

export function useStopPlacementSettings(): UseStopPlacementSettingsResult {
  const [settings, setSettings] = useState<StopPlacementPolicyDTO | null>(null);
  const [draftMode, setDraftModeState] = useState<string>('SHADOW_COMPARE');
  const [draftAtrPeriod, setDraftAtrPeriod] = useState<number>(14);
  const [draftMinBufferTicks, setDraftMinBufferTicks] = useState<number>(5);
  const [draftAtrM1Multiplier, setDraftAtrM1Multiplier] = useState<number>(0.25);
  const [draftAtrM5Multiplier, setDraftAtrM5Multiplier] = useState<number>(0.10);
  const [draftSpreadMultiplier, setDraftSpreadMultiplier] = useState<number>(1.50);

  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isStaleConflict, setIsStaleConflict] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<{
    type: 'idle' | 'saving' | 'success' | 'error';
    message?: string;
  }>({ type: 'idle' });

  const inFlightSaveRef = useRef<boolean>(false);
  const isMountedRef = useRef<boolean>(true);

  const setDraftMode = useCallback((m: string) => {
    setDraftModeState(m);
  }, []);

  const resetDrafts = useCallback(() => {
    if (settings) {
      setDraftModeState(settings.mode);
      setDraftAtrPeriod(settings.atr_period);
      setDraftMinBufferTicks(settings.min_buffer_ticks);
      setDraftAtrM1Multiplier(settings.atr_m1_multiplier);
      setDraftAtrM5Multiplier(settings.atr_m5_multiplier);
      setDraftSpreadMultiplier(settings.spread_multiplier);
    }
  }, [settings]);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const data = await getStopPlacementPolicy();
      if (!isMountedRef.current) return;
      setSettings(data);
      setDraftModeState(data.mode);
      setDraftAtrPeriod(data.atr_period);
      setDraftMinBufferTicks(data.min_buffer_ticks);
      setDraftAtrM1Multiplier(data.atr_m1_multiplier);
      setDraftAtrM5Multiplier(data.atr_m5_multiplier);
      setDraftSpreadMultiplier(data.spread_multiplier);
      setIsStaleConflict(false);
    } catch (err: any) {
      if (!isMountedRef.current) return;
      setLoadError(extractErrorMessage(err, 'Không thể tải cấu hình Stop Placement'));
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    refresh();
    return () => {
      isMountedRef.current = false;
    };
  }, [refresh]);

  const isDirty = Boolean(
    settings &&
      (draftMode !== settings.mode ||
        draftAtrPeriod !== settings.atr_period ||
        draftMinBufferTicks !== settings.min_buffer_ticks ||
        draftAtrM1Multiplier !== settings.atr_m1_multiplier ||
        draftAtrM5Multiplier !== settings.atr_m5_multiplier ||
        draftSpreadMultiplier !== settings.spread_multiplier)
  );

  const saveSettings = useCallback(async (): Promise<boolean> => {
    if (!settings || inFlightSaveRef.current) return false;
    inFlightSaveRef.current = true;
    setIsSaving(true);
    setSaveStatus({ type: 'saving', message: 'Đang lưu cấu hình...' });

    try {
      const updated = await updateStopPlacementPolicy({
        mode: draftMode,
        expected_version: settings.version,
        atr_period: draftAtrPeriod,
        min_buffer_ticks: draftMinBufferTicks,
        atr_m1_multiplier: draftAtrM1Multiplier,
        atr_m5_multiplier: draftAtrM5Multiplier,
        spread_multiplier: draftSpreadMultiplier,
      });

      if (!isMountedRef.current) return true;
      setSettings(updated);
      setDraftModeState(updated.mode);
      setDraftAtrPeriod(updated.atr_period);
      setDraftMinBufferTicks(updated.min_buffer_ticks);
      setDraftAtrM1Multiplier(updated.atr_m1_multiplier);
      setDraftAtrM5Multiplier(updated.atr_m5_multiplier);
      setDraftSpreadMultiplier(updated.spread_multiplier);
      setIsStaleConflict(false);
      setSaveStatus({ type: 'success', message: 'Đã lưu cấu hình Stop Placement thành công' });
      return true;
    } catch (err: any) {
      if (!isMountedRef.current) return false;
      const status = err.response?.status;
      if (status === 409) {
        setIsStaleConflict(true);
        setSaveStatus({
          type: 'error',
          message: 'Xung đột phiên bản: Cấu hình đã được thay đổi ở nơi khác. Vui lòng tải lại trang.',
        });
      } else {
        setSaveStatus({
          type: 'error',
          message: extractErrorMessage(err, 'Lỗi khi lưu cấu hình Stop Placement'),
        });
      }
      return false;
    } finally {
      inFlightSaveRef.current = false;
      if (isMountedRef.current) {
        setIsSaving(false);
      }
    }
  }, [
    settings,
    draftMode,
    draftAtrPeriod,
    draftMinBufferTicks,
    draftAtrM1Multiplier,
    draftAtrM5Multiplier,
    draftSpreadMultiplier,
  ]);

  return {
    settings,
    draftMode,
    setDraftMode,
    draftAtrPeriod,
    setDraftAtrPeriod,
    draftMinBufferTicks,
    setDraftMinBufferTicks,
    draftAtrM1Multiplier,
    setDraftAtrM1Multiplier,
    draftAtrM5Multiplier,
    setDraftAtrM5Multiplier,
    draftSpreadMultiplier,
    setDraftSpreadMultiplier,
    isDirty,
    isLoading,
    isSaving,
    isStaleConflict,
    loadError,
    saveStatus,
    refresh,
    saveSettings,
    resetDrafts,
  };
}
