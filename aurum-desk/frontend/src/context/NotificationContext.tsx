import React, { createContext, useContext, useState, useRef, useCallback, useEffect } from 'react';
import type { UserMessage, SemanticActionType, SemanticActionInput } from '../types/userMessage';
import { normalizeAppError } from '../utils/normalizeAppError';
import { getCatalogTemplate } from '../utils/userMessageCatalog';

export type NotifyPayload = (Partial<UserMessage> & { summary?: string; code?: string; title?: string }) | string;

interface NotificationContextType {
  activeToasts: UserMessage[];
  history: UserMessage[];
  unreadCount: number;
  selectedMessage: UserMessage | null;
  isCenterOpen: boolean;
  notify: (message: NotifyPayload, options?: Partial<UserMessage>) => void;
  notifyError: (err: any, context?: { operation?: string; entityId?: string; fallbackTitle?: string; fallbackSummary?: string }) => void;
  dismissToast: (id: string) => void;
  clearHistory: () => void;
  openDetails: (message: UserMessage) => void;
  closeDetails: () => void;
  toggleCenter: () => void;
  closeCenter: () => void;
  markAllAsRead: () => void;
  onActionClick?: (action: SemanticActionInput) => void;
  setOnActionHandler: (handler: (action: SemanticActionType) => void) => void;
}

const NotificationContext = createContext<NotificationContextType | null>(null);

const MAX_VISIBLE_TOASTS = 3;
const MAX_HISTORY_ITEMS = 50;
const DEDUPE_WINDOW_MS = 3000;

export const NotificationProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activeToasts, setActiveToasts] = useState<UserMessage[]>([]);
  const [history, setHistory] = useState<UserMessage[]>([]);
  const [selectedMessage, setSelectedMessage] = useState<UserMessage | null>(null);
  const [isCenterOpen, setIsCenterOpen] = useState(false);

  // Storage for deduplication
  const recentDispatchesRef = useRef<Map<string, number>>(new Map());

  // Storage for active timers
  const toastTimersRef = useRef<Map<string, any>>(new Map());

  // Storage for notified ready setups (instance + revision) to prevent duplicate toasts on refresh
  const notifiedReadySetupsRef = useRef<Set<string>>(new Set());

  // Incident tracking for repeated background errors: incidentKey -> state
  const incidentsRef = useRef<Map<string, { first_seen: number; last_seen: number; count: number; status: 'active' | 'dismissed' | 'resolved' }>>(new Map());

  // Custom action handler
  const actionHandlerRef = useRef<((action: SemanticActionType) => void) | null>(null);

  const setOnActionHandler = useCallback((handler: (action: SemanticActionType) => void) => {
    actionHandlerRef.current = handler;
  }, []);

  const dismissToast = useCallback((id: string) => {
    setActiveToasts((prev) => {
      const target = prev.find((t) => t.id === id);
      if (target) {
        const incidentKey = `${target.code}:${target.operation || 'default'}:${target.entity_id || ''}`;
        const existingIncident = incidentsRef.current.get(incidentKey);
        if (existingIncident) {
          existingIncident.status = 'dismissed';
        }
      }
      return prev.filter((t) => t.id !== id);
    });
    if (toastTimersRef.current.has(id)) {
      clearTimeout(toastTimersRef.current.get(id));
      toastTimersRef.current.delete(id);
    }
  }, []);

  // Schedule auto-dismiss timer for a toast
  const scheduleDismiss = useCallback(
    (msg: UserMessage) => {
      // Errors and warnings persist until user dismissal or interaction
      if (msg.severity === 'error' || msg.severity === 'warning') {
        return;
      }

      const durationMs = msg.severity === 'success' ? 5000 : 7000;
      if (toastTimersRef.current.has(msg.id)) {
        clearTimeout(toastTimersRef.current.get(msg.id));
      }

      const timer = setTimeout(() => {
        dismissToast(msg.id);
      }, durationMs);

      toastTimersRef.current.set(msg.id, timer);
    },
    [dismissToast]
  );

  // Core push notification function
  const pushMessage = useCallback(
    (msg: UserMessage) => {
      const now = Date.now();

      // Deduplication check: prefer real event_id or composite identity
      const dedupeKey = msg.event_id
        ? `evt:${msg.event_id}`
        : `${msg.code}:${msg.entity_id || ''}:${msg.operation || ''}:${msg.title}`;

      const lastTime = recentDispatchesRef.current.get(dedupeKey);
      if (lastTime && now - lastTime < DEDUPE_WINDOW_MS) {
        // Skip duplicate burst
        return;
      }
      recentDispatchesRef.current.set(dedupeKey, now);

      // Handle setup.ready specific deduplication per instance + revision
      if (msg.code === 'SETUP_READY') {
        const readyKey = `${msg.entity_id || ''}:${msg.params?.setup_instance_id || ''}:${msg.params?.revision || ''}`;
        if (notifiedReadySetupsRef.current.has(readyKey)) {
          return;
        }
        notifiedReadySetupsRef.current.add(readyKey);
      }

      // Handle repeated incidents for background polling/stream errors
      const isBackgroundError =
        (msg.severity === 'error' || msg.severity === 'warning') &&
        (!msg.operation || msg.operation.startsWith('poll') || msg.operation.startsWith('background') || msg.operation === 'get_health');

      const incidentKey = `${msg.code}:${msg.operation || 'default'}:${msg.entity_id || ''}`;
      if (isBackgroundError) {
        const existing = incidentsRef.current.get(incidentKey);
        if (existing) {
          existing.count += 1;
          existing.last_seen = now;
          if (existing.status === 'dismissed' || existing.status === 'active') {
            // Do not pop up a new toast repeatedly for dismissed or active background incidents
            return;
          }
        } else {
          incidentsRef.current.set(incidentKey, {
            first_seen: now,
            last_seen: now,
            count: 1,
            status: 'active',
          });
        }
      }

      // If recovery event, resolve corresponding degraded incident
      if (msg.code === 'FEED_RECOVERED') {
        for (const [k, inc] of incidentsRef.current.entries()) {
          if (k.startsWith('FEED_DOWN') || k.startsWith('EXECUTION_FEED_DEGRADED')) {
            inc.status = 'resolved';
          }
        }
      }

      // Clean up old entries in recentDispatchesRef
      if (recentDispatchesRef.current.size > 200) {
        for (const [k, ts] of recentDispatchesRef.current.entries()) {
          if (now - ts > DEDUPE_WINDOW_MS * 4) {
            recentDispatchesRef.current.delete(k);
          }
        }
      }

      // Add to visible toasts (bounded to MAX_VISIBLE_TOASTS)
      setActiveToasts((prev) => {
        const next = [msg, ...prev.filter((t) => t.id !== msg.id)];
        return next.slice(0, MAX_VISIBLE_TOASTS);
      });

      // Add to history (bounded to MAX_HISTORY_ITEMS)
      setHistory((prev) => [msg, ...prev.filter((h) => h.id !== msg.id)].slice(0, MAX_HISTORY_ITEMS));

      // Schedule dismiss
      scheduleDismiss(msg);
    },
    [scheduleDismiss]
  );

  const notify = useCallback(
    (message: NotifyPayload, options?: Partial<UserMessage>) => {
      if (typeof message === 'string') {
        const fallbackCode =
          options?.code ||
          (options?.severity === 'success'
            ? 'GENERIC_SUCCESS'
            : options?.severity === 'warning'
            ? 'GENERIC_WARNING'
            : options?.severity === 'info'
            ? 'GENERIC_INFO'
            : 'UNKNOWN');
        const template = getCatalogTemplate(fallbackCode);
        const userMsg: UserMessage = {
          id: options?.id || `msg_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
          code: options?.code || template.code,
          severity: options?.severity || template.defaultSeverity,
          title: options?.title || template.title,
          summary: message,
          explanation: options?.explanation || (template.code !== 'UNKNOWN' ? template.explanation : ''),
          impact: options?.impact || (template.code !== 'UNKNOWN' ? template.impact : ''),
          next_steps: options?.next_steps || (template.code !== 'UNKNOWN' ? template.next_steps : ''),
          params: options?.params || {},
          action: options?.action || (template.code !== 'UNKNOWN' ? template.defaultAction : undefined),
          operation: options?.operation,
          entity_id: options?.entity_id,
          event_id: options?.event_id,
          occurred_at: options?.occurred_at || Date.now(),
          outcome_certainty: options?.outcome_certainty || template.defaultCertainty,
          technical_details: options?.technical_details,
          read: false,
        };
        pushMessage(userMsg);
      } else {
        const fallbackCode =
          message.code ||
          options?.code ||
          (message.severity === 'success' || options?.severity === 'success'
            ? 'GENERIC_SUCCESS'
            : message.severity === 'warning' || options?.severity === 'warning'
            ? 'GENERIC_WARNING'
            : message.severity === 'info' || options?.severity === 'info'
            ? 'GENERIC_INFO'
            : 'UNKNOWN');
        const template = getCatalogTemplate(fallbackCode);
        const userMsg: UserMessage = {
          id: message.id || options?.id || `msg_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
          code: message.code || options?.code || template.code,
          severity: message.severity || options?.severity || template.defaultSeverity,
          title: message.title || options?.title || template.title,
          summary:
            message.summary ||
            (typeof template.summary === 'function' ? template.summary(message.params || {}) : template.summary),
          explanation: message.explanation || options?.explanation || (template.code !== 'UNKNOWN' ? template.explanation : ''),
          impact: message.impact || options?.impact || (template.code !== 'UNKNOWN' ? template.impact : ''),
          next_steps: message.next_steps || options?.next_steps || (template.code !== 'UNKNOWN' ? template.next_steps : ''),
          params: { ...message.params, ...options?.params },
          action: message.action || options?.action || (template.code !== 'UNKNOWN' ? template.defaultAction : undefined),
          operation: message.operation || options?.operation,
          entity_id: message.entity_id || options?.entity_id,
          event_id: message.event_id || options?.event_id,
          occurred_at: message.occurred_at || options?.occurred_at || Date.now(),
          outcome_certainty: message.outcome_certainty || options?.outcome_certainty || template.defaultCertainty,
          technical_details: message.technical_details || options?.technical_details,
          read: false,
        };
        pushMessage(userMsg);
      }
    },
    [pushMessage]
  );

  const notifyError = useCallback(
    (err: any, context?: { operation?: string; entityId?: string; fallbackTitle?: string; fallbackSummary?: string }) => {
      const normalized = normalizeAppError(err, context);
      pushMessage(normalized);
    },
    [pushMessage]
  );

  const openDetails = useCallback((message: UserMessage) => {
    setSelectedMessage(message);
    // Mark as read in history
    setHistory((prev) =>
      prev.map((item) => (item.id === message.id ? { ...item, read: true } : item))
    );
  }, []);

  const closeDetails = useCallback(() => {
    setSelectedMessage(null);
  }, []);

  const toggleCenter = useCallback(() => {
    setIsCenterOpen((prev) => !prev);
  }, []);

  const closeCenter = useCallback(() => {
    setIsCenterOpen(false);
  }, []);

  const markAllAsRead = useCallback(() => {
    setHistory((prev) => prev.map((item) => ({ ...item, read: true })));
  }, []);

  const clearHistory = useCallback(() => {
    setHistory([]);
  }, []);

  const unreadCount = history.filter((h) => !h.read).length;

  // Handle keyboard events (ESC to close details or center)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectedMessage) {
          closeDetails();
        } else if (isCenterOpen) {
          closeCenter();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedMessage, isCenterOpen, closeDetails, closeCenter]);

  // Clean up all timers on unmount
  useEffect(() => {
    const timers = toastTimersRef.current;
    return () => {
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  }, []);

  return (
    <NotificationContext.Provider
      value={{
        activeToasts,
        history,
        unreadCount,
        selectedMessage,
        isCenterOpen,
        notify,
        notifyError,
        dismissToast,
        clearHistory,
        openDetails,
        closeDetails,
        toggleCenter,
        closeCenter,
        markAllAsRead,
        onActionClick: (action) => {
          if (actionHandlerRef.current) {
            const actionType = typeof action === 'string' ? action : action.type;
            actionHandlerRef.current(actionType);
          }
        },
        setOnActionHandler,
      }}
    >
      {children}
    </NotificationContext.Provider>
  );
};

export const useNotification = (): NotificationContextType => {
  const ctx = useContext(NotificationContext);
  if (!ctx) {
    throw new Error('useNotification must be used within a NotificationProvider');
  }
  return ctx;
};
