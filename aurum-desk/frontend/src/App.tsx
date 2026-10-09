import { useState, useEffect, useCallback, useRef } from 'react';
import { ChartComponent } from './ChartComponent';
import { api, extractErrorMessage } from './api/client';
import { wsClient } from './services/wsClient';
import { calculateClientRiskReward } from './utils/calculator';
import type { RiskRewardData } from './plugins/RiskRewardPrimitive';
import { TestingLabComponent } from './TestingLabComponent';
import {
  Activity,
  ShieldAlert,
  Calendar,
  BookOpen,
  History,
  TrendingUp,
  Layers,
  PauseCircle,
  PlayCircle,
  Upload,
  CheckCircle2,
  XCircle,
  Clock,
  ExternalLink,
  Info,
  Bell,
  Sliders,
  Eye,
  RefreshCw,
  Smartphone,
  Copy,
  ListOrdered,
  Compass,
  FlaskConical,
  Sparkles,
  X,
  AlertTriangle,
  RotateCcw,
  Check,
  ShieldCheck
} from 'lucide-react';
import { NYSessionPanel } from './NYSessionPanel';
import { ExpectedEntryPanel } from './ExpectedEntryPanel';
import { TelegramTab } from './TelegramTab';
import { JournalTab } from './JournalTab';
import type { TradingPolicy, NYSessionStatus } from './api/client';

export type SelectionSource = 'LIVE_CANDIDATE' | 'WATCH_SETUP' | 'DRAFT' | 'OPEN_POSITION';

export interface SelectedTradeIntent {
  source: SelectionSource;
  setup_id?: string;
  setup_instance_id?: string;
  revision?: number;
  symbol: string;
  timeframe: string;
  direction: 'LONG' | 'SHORT';
  plannedEntry: number;
  stopLoss: number;
  takeProfit?: number;
  orderType: 'MARKET' | 'LIMIT' | 'STOP';
  quantity: number;
  initialRiskUsdt: number;
  grossRR: number;
  estimatedNetRR: number;
  leverage: number;
  marginMode: string;
  estimatedLiquidation?: number | null;
  status: string;
  invalidationReason?: string;
  snapshotAt: number;
  eligibility?: any;
  conditions_met?: string[];
  conditions_remaining?: string[];
  distance_to_entry_usdt?: number;
  distance_to_entry_atr?: number;
}


export function App() {
  // Navigation & Timeframe
  const [activeTab, setActiveTab] = useState<
    'chart' | 'upcoming' | 'smc' | 'paper' | 'lab' | 'reports' | 'news' | 'journal' | 'telegram' | 'education'
  >('chart');
  const [timeframe, setTimeframe] = useState<string>('15M');
  const timeframes = [
    { id: '1M', label: '1M (1 Phút)' },
    { id: '5M', label: '5M' },
    { id: '15M', label: '15M' },
    { id: '1H', label: '1H' },
    { id: '4H', label: '4H' },
    { id: 'D', label: 'D (Daily)' },
  ];

  // System Health & Sessions
  const [health, setHealth] = useState<any>(null);
  const [sessionInfo, setSessionInfo] = useState<any>(null);

  // Market & SMC State
  const [analysis, setAnalysis] = useState<any>(null);
  const [activeOverlay, setActiveOverlay] = useState<RiskRewardData | null>(null);
  const [selectedIntent, setSelectedIntent] = useState<SelectedTradeIntent | null>(null);
  const [marketMatrix, setMarketMatrix] = useState<any>(null);
  const [rvolData, setRvolData] = useState<any>(null);
  // Paper Trading & Account Settings
  const [accountStatus, setAccountStatus] = useState<any>(null);
  const [activePosition, setActivePosition] = useState<any>(null);
  const [autoPaperActive, setAutoPaperActive] = useState<boolean>(true);

  // V7 Trading Policy & NY Session
  const [tradingPolicy, setTradingPolicy] = useState<TradingPolicy | null>(null);
  const [nySession, setNySession] = useState<NYSessionStatus | null>(null);

  // Authoritative Saved Risk Settings (Used for active executions, position sizing, order creation)
  const [savedRiskSettings, setSavedRiskSettings] = useState<{
    leverage: number;
    margin_mode: 'ISOLATED' | 'CROSS';
    risk_pct: number;
    config_version: number;
  }>({
    leverage: 5,
    margin_mode: 'ISOLATED',
    risk_pct: 0.25,
    config_version: 1,
  });

  // Draft Risk Settings (Tweaked by slider/inputs, unsaved until user submits)
  const [draftRiskSettings, setDraftRiskSettings] = useState<{
    leverage: number;
    margin_mode: 'ISOLATED' | 'CROSS';
    risk_pct: number;
    config_version: number;
  }>({
    leverage: 5,
    margin_mode: 'ISOLATED',
    risk_pct: 0.25,
    config_version: 1,
  });

  const [isSettingsDirty, setIsSettingsDirty] = useState<boolean>(false);
  const [isSavingSettings, setIsSavingSettings] = useState<boolean>(false);
  const [settingsSavedMsg, setSettingsSavedMsg] = useState<string | null>(null);
  const [settingsSaveError, setSettingsSaveError] = useState<string | null>(null);
  const [needsRefreshRetry, setNeedsRefreshRetry] = useState<boolean>(false);
  const [instrumentMeta, setInstrumentMeta] = useState<any>(null);

  // Async race protection refs
  const isDirtyRef = useRef<boolean>(false);
  const draftRevisionRef = useRef<number>(0);
  const inFlightSaveRevisionRef = useRef<number>(0);
  const pollSequenceRef = useRef<number>(0);
  const savedSettingsRef = useRef(savedRiskSettings);

  // Canonical shorthand getters for current active application state
  const leverage = savedRiskSettings.leverage;
  const marginMode = savedRiskSettings.margin_mode;
  const riskPct = savedRiskSettings.risk_pct;

  // Upcoming Setups & Scenarios
  const [upcomingData, setUpcomingData] = useState<{
    setups: any[];
    scenarios: any;
    server_time?: number;
  }>({ setups: [], scenarios: null });
  const [upcomingFilter, setUpcomingFilter] = useState<string>('ALL');
  const hasArmedOrder = upcomingData?.setups?.some((s: any) => s.state === 'ARMED') ?? false;

  // Additional Panels Data
  const [reports, setReports] = useState<any[]>([]);
  const [newsData, setNewsData] = useState<any>(null);
  const [educationList, setEducationList] = useState<any[]>([]);
  const [selectedEdu, setSelectedEdu] = useState<any>(null);
  const [importStatus, setImportStatus] = useState<string | null>(null);

  // Live Toast Notification
  const [liveToast, setLiveToast] = useState<{ title: string; message: string; type: 'info' | 'success' | 'warn' } | null>(null);
  const toastTimeoutRef = useRef<any>(null);

  const showToast = useCallback((title: string, message: string, type: 'info' | 'success' | 'warn' = 'info') => {
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    setLiveToast({ title, message, type });
    toastTimeoutRef.current = setTimeout(() => {
      setLiveToast(null);
    }, 6000);
  }, []);

  // 1. Fetch System Health & Account Status
  const refreshAccountAndHealth = useCallback(async () => {
    const seq = ++pollSequenceRef.current;
    try {
      const [healthData, accData, posData, autoData, metaData, nyData, policyData] = await Promise.all([
        api.getHealth('XAUUSDT', timeframe),
        api.getAccountStatus(),
        api.getActivePosition(),
        api.getAutoState(),
        api.getInstrumentMetadata('XAUUSDT').catch(() => null),
        api.getNYSessionStatus('XAUUSDT').catch(() => null),
        api.getTradingPolicy('XAUUSDT').catch(() => null),
      ]);

      if (seq < pollSequenceRef.current) {
        // Discard stale in-flight response
        return;
      }

      setHealth(healthData);
      setAccountStatus(accData);
      if (metaData) {
        setInstrumentMeta(metaData);
      }
      if (nyData) {
        setNySession(nyData);
      }
      if (policyData) {
        setTradingPolicy(policyData);
      }
      if (accData) {
        const canonical: {
          leverage: number;
          margin_mode: 'ISOLATED' | 'CROSS';
          risk_pct: number;
          config_version: number;
        } = {
          leverage: accData.leverage ?? 5,
          margin_mode: (accData.margin_mode?.toUpperCase() || 'ISOLATED') as 'ISOLATED' | 'CROSS',
          risk_pct: accData.risk_pct ?? 0.25,
          config_version: accData.config_version ?? 1,
        };
        setSavedRiskSettings(canonical);
        savedSettingsRef.current = canonical;

        // CRITICAL: Only update draft if user is NOT currently editing (checked via ref to prevent stale closures and late responses)
        if (!isDirtyRef.current) {
          setDraftRiskSettings(canonical);
        }
      }
      setNeedsRefreshRetry(false);

      if (autoData && typeof autoData.auto_paper_enabled === 'boolean') {
        setAutoPaperActive(autoData.auto_paper_enabled);
      }
      if (posData?.has_active_position) {
        setActivePosition(posData);
        const p = posData.position;
        const overlay: RiskRewardData = {
          id: p.id,
          direction: p.direction,
          state: 'paper_open',
          plannedEntry: p.planned_entry,
          actualEntry: p.actual_entry || p.planned_entry,
          stopLoss: p.stop_loss,
          takeProfit: p.take_profit,
          quantity: p.quantity,
          initialRiskUsdt: p.initial_risk_usdt,
          riskPct: p.risk_pct,
          grossRR: p.gross_rr,
          estimatedNetRR: p.estimated_net_rr,
          leverage: p.leverage,
          marginMode: p.margin_mode,
          estimatedLiquidation: p.estimated_liquidation,
        };
        setActiveOverlay(overlay);
        setSelectedIntent({
          source: 'OPEN_POSITION',
          setup_id: p.setup_id,
          setup_instance_id: p.setup_instance_id,
          symbol: p.instrument || 'XAUUSDT',
          timeframe: p.timeframe || timeframe,
          direction: p.direction,
          plannedEntry: p.actual_entry || p.planned_entry,
          stopLoss: p.stop_loss,
          takeProfit: p.take_profit,
          orderType: p.order_type || 'MARKET',
          quantity: p.quantity,
          initialRiskUsdt: p.initial_risk_usdt,
          grossRR: p.gross_rr,
          estimatedNetRR: p.estimated_net_rr,
          leverage: p.leverage,
          marginMode: p.margin_mode,
          estimatedLiquidation: p.estimated_liquidation,
          status: 'paper_open',
          snapshotAt: Date.now(),
        });
      } else {
        setActivePosition(null);
        setSelectedIntent((prev) => (prev?.source === 'OPEN_POSITION' ? null : prev));
      }
    } catch (err) {
      console.warn('Health check error:', err);
    }
  }, [timeframe]);

  const handleToggleAutoPaper = async () => {
    const nextState = !autoPaperActive;
    try {
      const res = await api.setAutoState(nextState);
      setAutoPaperActive(Boolean(res.auto_paper_enabled));
      showToast(
        'Chế độ Auto Paper',
        nextState ? 'Đã BẬT tự động vào lệnh khi setup đủ điều kiện' : 'Đã TẠM DỪNG tự động vào lệnh',
        nextState ? 'success' : 'warn'
      );
      await refreshAccountAndHealth();
    } catch (err) {
      console.error('Failed to toggle auto paper state:', err);
    }
  };

  // 2. Fetch SMC Market Analysis, RVOL & Matrix
  const refreshAnalysis = useCallback(async () => {
    try {
      const [analysisData, rvolRes, matrixRes] = await Promise.all([
        api.getAnalysis('XAUUSDT', timeframe),
        api.getRvol('XAUUSDT', timeframe).catch(() => null),
        api.getMarketMatrix('XAUUSDT').catch(() => null),
      ]);
      setAnalysis(analysisData);
      if (rvolRes) setRvolData(rvolRes);
      if (matrixRes) setMarketMatrix(matrixRes);

      // Only update candidate overlay if there's no open position AND user hasn't selected a watch setup or draft!
      if (!activePosition && analysisData?.active_signal) {
        setSelectedIntent((prev) => {
          // If user actively selected a watch setup or draft, DO NOT OVERWRITE!
          if (prev && (prev.source === 'WATCH_SETUP' || prev.source === 'DRAFT')) {
            return prev;
          }
          const sig = analysisData.active_signal;
          const targetPrice = sig.targets?.[0]?.price ?? sig.take_profit;
          const validTP = (targetPrice && targetPrice !== sig.stop_loss) ? targetPrice : undefined;

          const overlay: RiskRewardData = {
            id: sig.id || sig.signal_id,
            direction: sig.direction,
            state: sig.state,
            plannedEntry: sig.planned_entry,
            stopLoss: sig.stop_loss,
            takeProfit: validTP || sig.planned_entry,
            quantity: sig.quantity,
            initialRiskUsdt: sig.initial_risk_usdt,
            riskPct: sig.risk_pct,
            grossRR: sig.gross_rr,
            estimatedNetRR: sig.estimated_net_rr,
            leverage: leverage,
            marginMode: marginMode,
            estimatedLiquidation: sig.estimated_liquidation,
          };
          setActiveOverlay(overlay);
          return {
            source: 'LIVE_CANDIDATE',
            setup_id: sig.setup_id,
            setup_instance_id: sig.id || sig.signal_id,
            revision: 1,
            symbol: 'XAUUSDT',
            timeframe: timeframe,
            direction: sig.direction,
            plannedEntry: sig.planned_entry,
            stopLoss: sig.stop_loss,
            takeProfit: validTP,
            orderType: 'MARKET',
            quantity: sig.quantity,
            initialRiskUsdt: sig.initial_risk_usdt,
            grossRR: sig.gross_rr,
            estimatedNetRR: sig.estimated_net_rr,
            leverage: leverage,
            marginMode: marginMode,
            estimatedLiquidation: sig.estimated_liquidation,
            status: sig.state,
            snapshotAt: Date.now(),
          };
        });
      }
    } catch (err) {
      console.warn('SMC analysis error:', err);
    }
  }, [timeframe, activePosition, leverage, marginMode]);

  // 3. Fetch Upcoming Setups
  const refreshUpcoming = useCallback(async () => {
    try {
      const data = await api.getUpcomingSetups();
      setUpcomingData(data);
    } catch (err) {
      console.warn('Upcoming setups fetch error:', err);
    }
  }, []);

  const refreshAccountTimeoutRef = useRef<any>(null);
  const refreshUpcomingTimeoutRef = useRef<any>(null);

  const debouncedRefreshAccount = useCallback(() => {
    if (refreshAccountTimeoutRef.current) clearTimeout(refreshAccountTimeoutRef.current);
    refreshAccountTimeoutRef.current = setTimeout(() => {
      refreshAccountAndHealth();
    }, 300);
  }, [refreshAccountAndHealth]);

  const debouncedRefreshUpcoming = useCallback(() => {
    if (refreshUpcomingTimeoutRef.current) clearTimeout(refreshUpcomingTimeoutRef.current);
    refreshUpcomingTimeoutRef.current = setTimeout(() => {
      refreshUpcoming();
    }, 300);
  }, [refreshUpcoming]);

  // 4. WebSocket Domain Events Connection (V7.2: Consolidated Shared WebSocket)
  useEffect(() => {
    const unsubscribe = wsClient.subscribe((data: any) => {
      if (data?.type === 'DOMAIN_EVENT') {
        const evt = data.event || data;
        const type = evt?.event_type;
        const payload = evt?.payload || {};

        if (type === 'trade.opened') {
          const entry = payload.entry_price ?? payload.actual_entry ?? payload.planned_entry ?? '';
          showToast('Lệnh Đã Khớp (PAPER_OPEN)', `${payload.direction} XAUUSDT tại $${entry}`, 'success');
          debouncedRefreshAccount();
        } else if (type === 'trade.closed') {
          const pnl = payload.realized_pnl_net ?? payload.realized_pnl ?? payload.net_pnl ?? 0;
          const cause = payload.exit_cause ?? payload.exit_reason ?? 'CLOSED';
          showToast('Vị Thế Đã Đóng', `PnL: $${pnl} (${cause})`, pnl >= 0 ? 'success' : 'warn');
          debouncedRefreshAccount();
        } else if (type === 'trade.liquidated') {
          showToast('THANH LÝ (LIQUIDATED)', `Vị thế đã bị thanh lý tại giá Mark $${payload.exit_price ?? ''}`, 'warn');
          debouncedRefreshAccount();
        } else if (type === 'order.armed') {
          showToast('Lệnh Đã Armed', `Setup ${payload.setup_id} đã sẵn sàng chờ kích hoạt`, 'info');
          debouncedRefreshAccount();
        } else if (type === 'setup.ready') {
          showToast('Setup READY', `Setup ${payload.setup_id} đã hoàn tất điều kiện SMC`, 'info');
          debouncedRefreshUpcoming();
        } else if (type === 'setup.invalidated') {
          debouncedRefreshUpcoming();
        } else if (type === 'feed.degraded') {
          setHealth((prev: any) => prev ? { ...prev, status: 'degraded', feed_connected: false } : prev);
        } else if (type === 'feed.recovered') {
          setHealth((prev: any) => prev ? { ...prev, status: 'ok', feed_connected: true } : prev);
        }
      }
    });

    return () => {
      unsubscribe();
      if (refreshAccountTimeoutRef.current) clearTimeout(refreshAccountTimeoutRef.current);
      if (refreshUpcomingTimeoutRef.current) clearTimeout(refreshUpcomingTimeoutRef.current);
    };
  }, [debouncedRefreshAccount, debouncedRefreshUpcoming, showToast]);

  // Periodic polling with visibility check and 45s cycle
  useEffect(() => {
    refreshAccountAndHealth();
    refreshAnalysis();
    refreshUpcoming();
    const interval = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      refreshAccountAndHealth();
      refreshAnalysis();
      refreshUpcoming();
    }, 45000);
    return () => clearInterval(interval);
  }, [refreshAccountAndHealth, refreshAnalysis, refreshUpcoming]);

  // Tab-specific data loading
  useEffect(() => {
    if (activeTab === 'reports') {
      api.getReports().then((res) => {
        setReports(res.reports || []);
        setSessionInfo(res.session_info);
      }).catch(console.error);
    } else if (activeTab === 'news') {
      api.getNews().then(setNewsData).catch(console.error);
    } else if (activeTab === 'education') {
      api.getEducation().then((list) => {
        setEducationList(list);
        if (list.length > 0 && !selectedEdu) setSelectedEdu(list[0]);
      }).catch(console.error);
    }
  }, [activeTab, selectedEdu]);

  // Action Handlers
  const handleFollowLatestSignal = () => {
    if (analysis?.active_signal) {
      const sig = analysis.active_signal;
      const targetPrice = sig.targets?.[0]?.price ?? sig.take_profit;
      const validTP = (targetPrice && targetPrice !== sig.stop_loss) ? targetPrice : undefined;

      const overlay: RiskRewardData = {
        id: sig.id || sig.signal_id,
        direction: sig.direction,
        state: sig.state,
        plannedEntry: sig.planned_entry,
        stopLoss: sig.stop_loss,
        takeProfit: validTP || sig.planned_entry,
        quantity: sig.quantity,
        initialRiskUsdt: sig.initial_risk_usdt,
        riskPct: sig.risk_pct,
        grossRR: sig.gross_rr,
        estimatedNetRR: sig.estimated_net_rr,
        leverage: leverage,
        marginMode: marginMode,
        estimatedLiquidation: sig.estimated_liquidation,
      };
      setActiveOverlay(overlay);
      setSelectedIntent({
        source: 'LIVE_CANDIDATE',
        setup_id: sig.setup_id,
        setup_instance_id: sig.id || sig.signal_id,
        revision: 1,
        symbol: 'XAUUSDT',
        timeframe: timeframe,
        direction: sig.direction,
        plannedEntry: sig.planned_entry,
        stopLoss: sig.stop_loss,
        takeProfit: validTP,
        orderType: 'MARKET',
        quantity: sig.quantity,
        initialRiskUsdt: sig.initial_risk_usdt,
        grossRR: sig.gross_rr,
        estimatedNetRR: sig.estimated_net_rr,
        leverage: leverage,
        marginMode: marginMode,
        estimatedLiquidation: sig.estimated_liquidation,
        status: sig.state,
        snapshotAt: Date.now(),
      });
      showToast('Theo Tín Hiệu Mới Nhất', `Đã chuyển sang tín hiệu SMC ${sig.direction}`, 'info');
    }
  };

  const handleOpenPaperTrade = async () => {
    const intent = selectedIntent || (analysis?.active_signal ? {
      source: 'LIVE_CANDIDATE' as const,
      setup_id: analysis.active_signal.setup_id,
      setup_instance_id: analysis.active_signal.id || analysis.active_signal.signal_id,
      direction: analysis.active_signal.direction,
      timeframe: timeframe,
      plannedEntry: analysis.active_signal.planned_entry,
      stopLoss: analysis.active_signal.stop_loss,
      takeProfit: analysis.active_signal.targets?.[0]?.price ?? analysis.active_signal.take_profit,
      quantity: analysis.active_signal.quantity,
      initialRiskUsdt: analysis.active_signal.initial_risk_usdt,
      grossRR: analysis.active_signal.gross_rr,
      estimatedNetRR: analysis.active_signal.estimated_net_rr,
      orderType: 'MARKET' as const,
      leverage: leverage,
      marginMode: marginMode,
      symbol: 'XAUUSDT',
      status: analysis.active_signal.state,
      snapshotAt: Date.now()
    } : null);

    if (!intent) return;
    if (!intent.takeProfit || intent.takeProfit === intent.stopLoss) {
      alert('Không thể mở lệnh: Thiếu mức Take Profit hợp lệ hoặc TP bằng SL.');
      return;
    }

    try {
      await api.createPaperOrder({
        setup_id: intent.setup_id,
        signal_id: intent.setup_instance_id || `sig-${Date.now()}`,
        setup_instance_id: intent.setup_instance_id,
        instrument: 'XAUUSDT',
        direction: intent.direction,
        expected_direction: intent.direction,
        idempotency_key: `order-${intent.setup_id || Date.now()}-${Date.now()}`,
        state: 'paper_open',
        order_type: intent.orderType || 'MARKET',
        timeframe: intent.timeframe || timeframe,
        planned_entry: intent.plannedEntry,
        stop_loss: intent.stopLoss,
        take_profit: intent.takeProfit,
        quantity: intent.quantity,
        initial_risk_usdt: intent.initialRiskUsdt,
        risk_pct: riskPct,
        gross_rr: intent.grossRR,
        estimated_net_rr: intent.estimatedNetRR,
        leverage: intent.leverage || leverage,
        margin_mode: intent.marginMode || marginMode,
      });
      await refreshAccountAndHealth();
      showToast('Đã Mở Lệnh', `Khớp lệnh thị trường ${intent.direction} XAUUSDT thành công`, 'success');
      setActiveTab('chart');
    } catch (err: any) {
      if (err.response?.status === 409) {
        const msg = extractErrorMessage(err, 'Lệnh bị từ chối do xung đột trạng thái');
        alert(`Lệnh bị từ chối do xung đột (409): ${msg}`);
      } else {
        alert(extractErrorMessage(err, 'Không thể mở lệnh paper trade'));
      }
    }
  };

  const handleClosePosition = async (orderId: string) => {
    if (!confirm('Bạn có chắc chắn muốn đóng vị thế paper trading này tại giá thị trường?')) return;
    try {
      await api.closePaperOrder(orderId);
      await refreshAccountAndHealth();
      setActiveOverlay(null);
      showToast('Đã Đóng Vị Thế', `Vị thế ${orderId} đã được đóng tại giá thị trường`, 'info');
    } catch (err: any) {
      alert(extractErrorMessage(err, 'Lỗi khi đóng vị thế'));
    }
  };

  const handleArmWatchSetup = async (setupId: string, direction?: string, instanceId?: string, revision?: number) => {
    const targetDirection = (direction || selectedIntent?.direction || 'LONG') as 'LONG' | 'SHORT';
    try {
      // V6.1 Strict Identity Guard: Only pass custom chart levels if selectedIntent.setup_id exactly matches setupId!
      const isMatchingSetup = Boolean(selectedIntent && selectedIntent.setup_id === setupId);
      const res = await api.armSetup(setupId, {
        setup_id: setupId,
        setup_instance_id: instanceId || (isMatchingSetup ? selectedIntent?.setup_instance_id : undefined),
        expected_revision: revision || (isMatchingSetup ? selectedIntent?.revision : undefined),
        expected_direction: targetDirection,
        planned_entry: isMatchingSetup ? selectedIntent?.plannedEntry : undefined,
        stop_loss: isMatchingSetup ? selectedIntent?.stopLoss : undefined,
        take_profit: isMatchingSetup ? selectedIntent?.takeProfit : undefined,
        idempotency_key: `arm-${setupId}-v${revision || (isMatchingSetup ? selectedIntent?.revision : 1) || 1}`
      });
      showToast('Lệnh Đã Armed', res.message || 'Đã arm setup thành công', 'success');
      await refreshUpcoming();
      await refreshAccountAndHealth();
    } catch (err: any) {
      if (err.response?.status === 409) {
        const msg = extractErrorMessage(err, 'Setup bạn chọn đã thay đổi trạng thái hoặc hướng; hãy xem lại.');
        alert(`Setup đã thay đổi (409): ${msg}`);
      } else {
        alert(extractErrorMessage(err, 'Lỗi khi arm setup'));
      }
    }
  };

  const handleCancelWatchSetup = async (setupId: string) => {
    if (!confirm('Bạn có chắc chắn muốn hủy setup này?')) return;
    try {
      await api.cancelSetup(setupId);
      showToast('Đã Hủy Setup', 'Setup đã được hủy an toàn', 'info');
      await refreshUpcoming();
      await refreshAccountAndHealth();
    } catch (err: any) {
      alert(extractErrorMessage(err, 'Lỗi khi hủy setup'));
    }
  };

  const handleFocusSetupOnChart = (setup: any) => {
    setTimeframe(setup.timeframe || '15M');
    setActiveTab('chart');
    const entry = setup.confirmed_entry || setup.provisional_entry;
    const sl = setup.confirmed_sl || setup.provisional_sl;
    const tp = setup.confirmed_tp || setup.provisional_tp;

    let grossRR = setup.gross_rr || 0.0;
    let netRR = setup.net_rr || 0.0;
    if ((!grossRR || grossRR <= 0) && entry && sl && tp) {
      const calc = calculateClientRiskReward(
        setup.direction,
        entry,
        sl,
        tp,
        1000.0,
        0.25,
        2.0,
        setup.quantity || 0.05,
        setup.leverage || leverage,
        'ISOLATED'
      );
      if (calc.isValid) {
        grossRR = calc.grossRR;
        netRR = calc.estimatedNetRR;
      }
    }

    const overlay: RiskRewardData = {
      id: setup.id,
      direction: setup.direction,
      state: setup.state === 'READY' || setup.state === 'ARMED' ? 'candidate' : 'draft',
      plannedEntry: entry,
      stopLoss: sl,
      takeProfit: tp,
      quantity: setup.quantity || 0.05,
      initialRiskUsdt: setup.risk_usdt || 2.5,
      riskPct: 0.25,
      grossRR: grossRR,
      estimatedNetRR: netRR,
      leverage: setup.leverage || leverage,
      marginMode: setup.margin_mode || marginMode,
      estimatedLiquidation: setup.estimated_liquidation,
    };
    setActiveOverlay(overlay);
    setSelectedIntent({
      source: 'WATCH_SETUP',
      setup_id: setup.id,
      setup_instance_id: setup.setup_instance_id,
      revision: setup.version || 1,
      symbol: 'XAUUSDT',
      timeframe: setup.timeframe || '15M',
      direction: setup.direction,
      plannedEntry: entry,
      stopLoss: sl,
      takeProfit: tp,
      orderType: setup.state === 'READY' ? 'MARKET' : 'LIMIT',
      quantity: setup.quantity || 0.05,
      initialRiskUsdt: setup.risk_usdt || 2.5,
      grossRR: grossRR,
      estimatedNetRR: netRR,
      leverage: setup.leverage || leverage,
      marginMode: setup.margin_mode || marginMode,
      estimatedLiquidation: setup.estimated_liquidation,
      status: setup.state,
      invalidationReason: setup.invalidation_reason,
      eligibility: setup.eligibility,
      conditions_met: setup.conditions_met,
      conditions_remaining: setup.conditions_remaining,
      distance_to_entry_usdt: setup.distance_to_entry_usdt,
      distance_to_entry_atr: setup.distance_to_entry_atr,
      snapshotAt: Date.now(),
    });
    showToast(
      `Đã Chọn Setup ${setup.direction}`,
      `Đã cố định setup ${setup.direction} trên chart và nút mở lệnh. Polling nền sẽ không ghi đè.`,
      'info'
    );
  };

  const handleCopySetupToDraft = (setup: any) => {
    setTimeframe(setup.timeframe || '15M');
    setActiveTab('chart');
    const overlay: RiskRewardData = {
      id: `draft-${Date.now()}`,
      direction: setup.direction,
      state: 'draft',
      plannedEntry: setup.confirmed_entry || setup.provisional_entry,
      stopLoss: setup.confirmed_sl || setup.provisional_sl,
      takeProfit: setup.confirmed_tp || setup.provisional_tp,
      quantity: setup.quantity || 0.05,
      initialRiskUsdt: setup.risk_usdt || 2.5,
      riskPct: 0.25,
      grossRR: setup.gross_rr || 2.0,
      estimatedNetRR: setup.net_rr || 2.0,
      leverage: setup.leverage || leverage,
      marginMode: setup.margin_mode || marginMode,
      estimatedLiquidation: setup.estimated_liquidation,
    };
    setActiveOverlay(overlay);
    setSelectedIntent({
      source: 'DRAFT',
      setup_id: `draft-${Date.now()}`,
      symbol: 'XAUUSDT',
      timeframe: setup.timeframe || '15M',
      direction: setup.direction,
      plannedEntry: setup.confirmed_entry || setup.provisional_entry,
      stopLoss: setup.confirmed_sl || setup.provisional_sl,
      takeProfit: setup.confirmed_tp || setup.provisional_tp,
      orderType: 'LIMIT',
      quantity: setup.quantity || 0.05,
      initialRiskUsdt: setup.risk_usdt || 2.5,
      grossRR: setup.gross_rr || 2.0,
      estimatedNetRR: setup.net_rr || 2.0,
      leverage: setup.leverage || leverage,
      marginMode: setup.margin_mode || marginMode,
      estimatedLiquidation: setup.estimated_liquidation,
      status: 'draft',
      snapshotAt: Date.now(),
    });
    showToast('Bản Nháp (Draft)', 'Đã sao chép mức giá sang thước đo R:R trên chart. Bạn có thể kéo thả để điều chỉnh.', 'info');
  };

  const handleSaveRiskSettings = async () => {
    if (isSavingSettings) return; // Prevent double submit
    const currentRevision = draftRevisionRef.current;
    inFlightSaveRevisionRef.current = currentRevision;
    setIsSavingSettings(true);
    setSettingsSaveError(null);
    setNeedsRefreshRetry(false);

    // Immutable payload captured at save moment
    const payload = {
      leverage: Number(draftRiskSettings.leverage),
      margin_mode: draftRiskSettings.margin_mode,
      risk_pct: Number(draftRiskSettings.risk_pct),
      expected_config_version: savedRiskSettings.config_version,
    };

    try {
      const res = await api.updateAccountSettings(payload);
      const canonical = {
        leverage: res.requested_leverage ?? payload.leverage,
        margin_mode: (res.margin_mode ?? payload.margin_mode).toUpperCase() as 'ISOLATED' | 'CROSS',
        risk_pct: res.risk_pct ?? payload.risk_pct,
        config_version: res.config_version ?? (savedRiskSettings.config_version + 1),
      };

      setSavedRiskSettings(canonical);
      savedSettingsRef.current = canonical;

      // Only clear dirty if user did not edit further during the request execution
      if (draftRevisionRef.current === currentRevision) {
        isDirtyRef.current = false;
        setIsSettingsDirty(false);
        setDraftRiskSettings(canonical);
      } else {
        setDraftRiskSettings((prev) => ({
          ...prev,
          config_version: canonical.config_version,
        }));
      }

      setSettingsSavedMsg(`Đã lưu cấu hình (v${canonical.config_version}) thành công!`);
      showToast('Cài Đặt Rủi Ro', `Đã lưu cấu hình v${canonical.config_version} thành công!`, 'success');
      setTimeout(() => setSettingsSavedMsg(null), 4000);

      // Refresh application state
      try {
        await refreshAccountAndHealth();
      } catch (refreshErr) {
        console.error('Refresh after save failed:', refreshErr);
        setNeedsRefreshRetry(true);
        showToast('Cảnh báo', 'Đã lưu, đánh giá chưa cập nhật. Bấm nút Thử Lại để đồng bộ.', 'warn');
      }
    } catch (err: any) {
      if (err.response?.status === 409) {
        const conflictMsg = err.response?.data?.detail || 'Xung đột phiên bản cấu hình (409 Conflict). Vui lòng tải lại cấu hình mới nhất.';
        setSettingsSaveError(conflictMsg);
        showToast('Xung đột cấu hình (409)', conflictMsg, 'warn');
        try {
          await refreshAccountAndHealth();
        } catch {
          // ignore
        }
      } else {
        const errMsg = err.response?.data?.detail || err.message || 'Lỗi khi lưu cài đặt tài khoản';
        setSettingsSaveError(errMsg);
        showToast('Lỗi lưu cài đặt', errMsg, 'warn');
      }
    } finally {
      setIsSavingSettings(false);
    }
  };

  const handleDraftChange = (field: 'leverage' | 'marginMode' | 'riskPct', value: any) => {
    draftRevisionRef.current += 1;
    isDirtyRef.current = true;
    setIsSettingsDirty(true);
    setSettingsSaveError(null);

    setDraftRiskSettings((prev) => {
      const updated = { ...prev };
      if (field === 'leverage') updated.leverage = Number(value);
      if (field === 'marginMode') updated.margin_mode = value;
      if (field === 'riskPct') updated.risk_pct = Number(value);
      return updated;
    });
  };

  const handleCancelDraft = () => {
    draftRevisionRef.current += 1;
    isDirtyRef.current = false;
    setIsSettingsDirty(false);
    setSettingsSaveError(null);
    setDraftRiskSettings(savedSettingsRef.current);
    showToast('Đã Hủy Bản Nháp', 'Khôi phục cài đặt rủi ro đã lưu trên hệ thống', 'info');
  };

  const handleFocusTradeOnChart = (trade: any) => {
    setTimeframe(trade.timeframe || '15M');
    setActiveTab('chart');
    const overlay: RiskRewardData = {
      id: trade.id,
      direction: trade.direction,
      state: trade.state,
      plannedEntry: trade.planned_entry,
      actualEntry: trade.actual_entry,
      stopLoss: trade.stop_loss,
      takeProfit: trade.take_profit,
      quantity: trade.quantity,
      initialRiskUsdt: trade.initial_risk_usdt,
      riskPct: trade.risk_pct,
      grossRR: trade.gross_rr,
      estimatedNetRR: trade.estimated_net_rr,
      realizedPnlNet: trade.realized_pnl_net,
      realizedR: trade.realized_r,
      leverage: trade.leverage || leverage,
      marginMode: trade.margin_mode || marginMode,
      estimatedLiquidation: trade.estimated_liquidation,
    };
    setActiveOverlay(overlay);
  };

  // News CSV Import & Research Modal states
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importCsvText, setImportCsvText] = useState('');
  const [importFileName, setImportFileName] = useState('');
  const [importTimezone, setImportTimezone] = useState('America/New_York');
  const [importPreview, setImportPreview] = useState<any>(null);
  const [isImportLoading, setIsImportLoading] = useState(false);
  const [researchModalOpen, setResearchModalOpen] = useState(false);
  const [activeResearch, setActiveResearch] = useState<any>(null);
  const [researchLoadingId, setResearchLoadingId] = useState<number | null>(null);

  const handleOpenNewsResearch = async (newsId: number) => {
    setResearchLoadingId(newsId);
    try {
      const res = await api.getNewsResearch(newsId);
      setActiveResearch(res);
      setResearchModalOpen(true);
    } catch (err: any) {
      alert(`Lỗi khi tải nghiên cứu tin: ${extractErrorMessage(err)}`);
    } finally {
      setResearchLoadingId(null);
    }
  };

  const handleRefreshNewsResearch = async (newsId: number) => {
    setResearchLoadingId(newsId);
    try {
      const res = await api.triggerNewsResearch(newsId);
      setActiveResearch(res);
      showToast('Đã Cập Nhật Nghiên Cứu', 'Đã tải và cập nhật số liệu mới từ nguồn URL', 'success');
      api.getNews().then(setNewsData);
    } catch (err: any) {
      alert(`Lỗi khi cập nhật nghiên cứu: ${extractErrorMessage(err)}`);
    } finally {
      setResearchLoadingId(null);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.name.endsWith('.csv')) {
      const text = await file.text();
      setImportCsvText(text);
      setImportFileName(file.name);
      setIsImportLoading(true);
      try {
        const preview = await api.previewNewsImport(text, importTimezone);
        setImportPreview(preview);
        setImportModalOpen(true);
      } catch (err: any) {
        alert(`Lỗi khi xem trước file: ${extractErrorMessage(err)}`);
      } finally {
        setIsImportLoading(false);
      }
    } else {
      setImportStatus('Đang nhập dữ liệu...');
      try {
        const res = await api.importNews(file);
        setImportStatus(`Đã nhập thành công ${res.imported_count} sự kiện từ ${res.filename}`);
        api.getNews().then(setNewsData);
      } catch (err: any) {
        setImportStatus(`Lỗi nhập file: ${extractErrorMessage(err)}`);
      }
    }
  };

  const handleRecheckPreviewWithTimezone = async (tz: string) => {
    setImportTimezone(tz);
    if (!importCsvText) return;
    setIsImportLoading(true);
    try {
      const preview = await api.previewNewsImport(importCsvText, tz);
      setImportPreview(preview);
    } catch (err: any) {
      alert(`Lỗi: ${extractErrorMessage(err)}`);
    } finally {
      setIsImportLoading(false);
    }
  };

  const handleConfirmCommitImport = async () => {
    if (!importCsvText) return;
    setIsImportLoading(true);
    try {
      const res = await api.commitNewsImport(importCsvText, importTimezone);
      showToast('Nhập Lịch Thành Công', res.message, 'success');
      setImportModalOpen(false);
      setImportCsvText('');
      setImportPreview(null);
      await api.getNews().then(setNewsData);
      await refreshAnalysis();
    } catch (err: any) {
      alert(`Lỗi khi lưu lịch vào database: ${extractErrorMessage(err)}`);
    } finally {
      setIsImportLoading(false);
    }
  };

  // Filtered upcoming setups
  const filteredSetups = upcomingData.setups.filter((s) => {
    if (upcomingFilter === 'ALL') return true;
    if (upcomingFilter === 'READY') return s.state === 'READY';
    if (upcomingFilter === 'ARMED') return s.state === 'ARMED';
    if (upcomingFilter === 'WAITING') return s.state.startsWith('WAITING');
    if (upcomingFilter === 'TERMINAL') return ['CANCELLED', 'INVALIDATED', 'EXPIRED', 'CLOSED'].includes(s.state);
    return true;
  });

  return (
    <div className="min-h-screen bg-charcoal-950 text-gray-200 flex flex-col font-sans select-none">
      {/* Toast Notification Banner */}
      {liveToast && (
        <div
          className={`fixed top-4 right-4 z-50 p-3.5 rounded-lg border shadow-xl flex items-start gap-2.5 max-w-sm transition-all duration-300 ${
            liveToast.type === 'success'
              ? 'bg-emerald-950/90 border-emerald-500 text-emerald-200'
              : liveToast.type === 'warn'
              ? 'bg-rose-950/90 border-rose-500 text-rose-200'
              : 'bg-charcoal-900/95 border-aurum-500 text-aurum-200'
          }`}
        >
          <Bell className="w-4 h-4 shrink-0 mt-0.5 text-aurum-400" />
          <div className="text-xs">
            <span className="font-bold block">{liveToast.title}</span>
            <span className="text-gray-300">{liveToast.message}</span>
          </div>
        </div>
      )}

      {/* 1. Global Header Bar */}
      <header className="bg-charcoal-900 border-b border-charcoal-750 px-4 py-2.5 flex flex-wrap justify-between items-center gap-3">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-aurum-500 animate-pulse" />
            <h1 className="text-lg font-bold text-aurum-400 tracking-wider">
              AURUM DESK <span className="text-xs font-mono text-gray-400">v4.0</span>
            </h1>
            <span className="text-[10px] uppercase font-semibold tracking-wider bg-charcoal-800 text-gray-400 px-2 py-0.5 rounded border border-charcoal-700">
              BITGET XAUUSDT PERPETUAL · PAPER TRADING
            </span>
          </div>

          {/* Timeframe Selector */}
          <div className="flex bg-charcoal-950 p-0.5 rounded-md border border-charcoal-750">
            {timeframes.map((tf) => (
              <button
                key={tf.id}
                onClick={() => setTimeframe(tf.id)}
                className={`px-2.5 py-1 rounded text-xs font-medium transition ${
                  timeframe === tf.id
                    ? 'bg-aurum-500 text-charcoal-950 font-bold shadow-xs'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {tf.id}
              </button>
            ))}
          </div>
        </div>

        {/* Live System Health & Session Status */}
        <div className="flex items-center gap-3 text-xs">
          {/* Real Backend Auto Paper Toggle */}
          <button
            onClick={handleToggleAutoPaper}
            className={`flex items-center gap-1.5 px-3 py-1 rounded border text-xs font-semibold transition ${
              autoPaperActive
                ? 'bg-emerald-600/30 text-emerald-300 border-emerald-500/50 hover:bg-emerald-600/40'
                : 'bg-rose-950/60 text-rose-300 border-rose-800 hover:bg-rose-900/60'
            }`}
            title="Bật/Tắt chế độ tự động vào lệnh Paper Trading theo cấu trúc SMC đã xác nhận"
          >
            {autoPaperActive ? <PlayCircle className="w-3.5 h-3.5 text-emerald-400" /> : <PauseCircle className="w-3.5 h-3.5 text-rose-400" />}
            <span>Auto Paper: {autoPaperActive ? 'BẬT' : 'TẠM DỪNG'}</span>
          </button>

          <div className="flex items-center gap-1.5 bg-charcoal-850 px-2.5 py-1 rounded border border-charcoal-700">
            <Activity className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-gray-400">Feed:</span>
            <span className={health?.feed_connected ? 'text-emerald-400 font-medium' : 'text-rose-400'}>
              {health?.feed_connected ? `Live (${health.data_freshness_sec}s)` : 'Mất kết nối'}
            </span>
          </div>

          <div className="flex items-center gap-1.5 bg-charcoal-850 px-2.5 py-1 rounded border border-charcoal-700">
            <span className="text-gray-400">HTF D/4H:</span>
            <span
              className={`font-bold uppercase ${
                health?.d_4h_bias === 'BULLISH'
                  ? 'text-emerald-400'
                  : health?.d_4h_bias === 'BEARISH'
                  ? 'text-rose-400'
                  : 'text-amber-400'
              }`}
            >
              {health?.d_4h_bias || 'WAITING'}
            </span>
          </div>

          <div className="hidden lg:flex items-center gap-1.5 bg-charcoal-850 px-2.5 py-1 rounded border border-charcoal-700 text-gray-400">
            <Clock className="w-3.5 h-3.5 text-indigo-400" />
            <span>Phiên: {sessionInfo?.active_sessions?.join(', ') || 'Á - Âu - Mỹ'}</span>
          </div>
        </div>
      </header>

      {/* 2. Navigation Tabs */}
      <nav className="bg-charcoal-900 border-b border-charcoal-750 px-4 flex gap-1 text-xs overflow-x-auto">
        {[
          { id: 'chart', label: 'Chart & Vị Thế R:R', icon: TrendingUp },
          { id: 'upcoming', label: 'Kế Hoạch & Lệnh Dự Kiến', icon: ListOrdered },
          { id: 'smc', label: 'Phân Tích SMC & Ma Trận', icon: Layers },
          { id: 'paper', label: 'Quản Trị Vốn & Ký Quỹ', icon: ShieldAlert },
          { id: 'lab', label: 'Phòng Kiểm Thử Rủi Ro', icon: FlaskConical },
          { id: 'reports', label: 'Nghiên Cứu Phiên & Ngày', icon: Calendar },
          { id: 'news', label: 'Tin Tức & Blackout', icon: Clock },
          { id: 'journal', label: 'Nhật Ký & Bài Học', icon: History },
          { id: 'telegram', label: 'Cài Đặt Telegram', icon: Smartphone },
          { id: 'education', label: 'Thư Viện Kiến Thức', icon: BookOpen },
        ].map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`flex items-center gap-1.5 px-3 py-2.5 font-medium border-b-2 transition whitespace-nowrap ${
                isActive
                  ? 'border-aurum-500 text-aurum-400 bg-charcoal-850/60 font-semibold'
                  : 'border-transparent text-gray-400 hover:text-gray-200 hover:bg-charcoal-850/30'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{tab.label}</span>
              {tab.id === 'upcoming' && upcomingData.setups.length > 0 && (
                <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-charcoal-800 text-aurum-400 border border-aurum-500/30">
                  {upcomingData.setups.length}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* V7 BẢNG PHIÊN MỸ & MỤC TIÊU PAPER */}
      <NYSessionPanel
        nySession={nySession}
        policy={tradingPolicy}
        autoPaperActive={autoPaperActive}
        onRefresh={refreshAccountAndHealth}
      />

      {/* 3. Main Workspace Content */}
      <main className="flex-1 p-3 grid grid-cols-1 lg:grid-cols-4 gap-3 overflow-hidden">
        {/* Left Area (Col 1-3): Interactive Chart or Full Tab Pages */}
        <div className="lg:col-span-3 flex flex-col gap-3 h-full">
          {activeTab === 'chart' && (
            <div className="flex-1 flex flex-col min-h-[560px]">
              <ChartComponent
                symbol="XAUUSDT"
                timeframe={timeframe}
                accountEquity={accountStatus?.current_equity ?? 1000}
                riskPct={riskPct}
                leverage={leverage}
                marginMode={marginMode}
                rvolData={rvolData}
                activeOverlay={activeOverlay}
                onOverlayChange={(updated) => {
                  setActiveOverlay(updated);
                  if (updated) {
                    setSelectedIntent((prev) => ({
                      source: prev?.source === 'WATCH_SETUP' ? 'WATCH_SETUP' : 'DRAFT',
                      setup_id: prev?.setup_id || updated.id,
                      setup_instance_id: prev?.setup_instance_id,
                      revision: prev?.revision || 1,
                      symbol: 'XAUUSDT',
                      timeframe: timeframe,
                      direction: updated.direction,
                      plannedEntry: updated.plannedEntry,
                      stopLoss: updated.stopLoss,
                      takeProfit: updated.takeProfit,
                      orderType: 'LIMIT',
                      quantity: updated.quantity,
                      initialRiskUsdt: updated.initialRiskUsdt,
                      grossRR: updated.grossRR,
                      estimatedNetRR: updated.estimatedNetRR,
                      leverage: updated.leverage || leverage,
                      marginMode: updated.marginMode || marginMode,
                      estimatedLiquidation: updated.estimatedLiquidation,
                      status: updated.state,
                      snapshotAt: Date.now(),
                    }));
                  }
                }}
                smcLevels={{
                  swingHigh: analysis?.swing_high,
                  swingLow: analysis?.swing_low,
                  equilibrium: analysis?.equilibrium,
                }}
                smcStructure={{
                  swings: analysis?.swings || [],
                  structureEvents: analysis?.structure_events || [],
                }}
              />
            </div>
          )}

          {/* TAB: KẾ HOẠCH & LỆNH DỰ KIẾN (UPCOMING SETUPS & DAILY SCENARIOS) */}
          {activeTab === 'upcoming' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-5 overflow-y-auto max-h-[750px]">
              <div className="flex flex-wrap justify-between items-center border-b border-charcoal-750 pb-3 gap-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400 flex items-center gap-2">
                    <Compass className="w-4 h-4 text-aurum-400" />
                    Kế Hoạch Phiên & Danh Sách Lệnh Dự Kiến
                  </h2>
                  <p className="text-xs text-gray-400">
                    Biết trước điều kiện cần chờ trước khi vào lệnh. Setup chỉ chuyển sang READY và ARMED khi đủ chuỗi SMC xác nhận.
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={refreshUpcoming}
                    className="flex items-center gap-1.5 px-3 py-1 bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 rounded text-xs border border-charcoal-700"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                    <span>Làm mới</span>
                  </button>
                </div>
              </div>

              {/* 3 Daily Scenarios Cards */}
              <div>
                <h3 className="text-xs font-bold text-gray-300 uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
                  <Calendar className="w-3.5 h-3.5 text-indigo-400" />
                  Kịch Bản Giao Dịch Trong Ngày (Daily Scenarios)
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                  {/* Bullish Scenario */}
                  <div className="bg-charcoal-850 p-3.5 rounded-lg border border-emerald-900/50 flex flex-col gap-2">
                    <div className="flex items-center justify-between border-b border-charcoal-750 pb-1.5">
                      <span className="font-bold text-emerald-400">Kịch Bản Mua (Bullish)</span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 font-mono">
                        LONG
                      </span>
                    </div>
                    <p className="text-gray-300 text-[11px] leading-relaxed">
                      {upcomingData.scenarios?.bullish?.condition || 'HTF duy trì Bullish, giá hồi về Discount và sweep đáy + MSS tăng.'}
                    </p>
                    <div className="text-[10px] text-gray-400 mt-auto pt-2 border-t border-charcoal-750 space-y-1">
                      <p><span className="text-gray-300 font-medium">Mục tiêu (TP):</span> {upcomingData.scenarios?.bullish?.target || 'PDH / Swing High'}</p>
                      <p className="text-rose-400/90"><span className="text-gray-300 font-medium">Vô hiệu:</span> {upcomingData.scenarios?.bullish?.invalidation || 'Đóng dưới Swing Low'}</p>
                    </div>
                  </div>

                  {/* Bearish Scenario */}
                  <div className="bg-charcoal-850 p-3.5 rounded-lg border border-rose-900/50 flex flex-col gap-2">
                    <div className="flex items-center justify-between border-b border-charcoal-750 pb-1.5">
                      <span className="font-bold text-rose-400">Kịch Bản Bán (Bearish)</span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-rose-950 text-rose-300 border border-rose-800 font-mono">
                        SHORT
                      </span>
                    </div>
                    <p className="text-gray-300 text-[11px] leading-relaxed">
                      {upcomingData.scenarios?.bearish?.condition || 'HTF duy trì Bearish, giá hồi về Premium và sweep đỉnh + MSS giảm.'}
                    </p>
                    <div className="text-[10px] text-gray-400 mt-auto pt-2 border-t border-charcoal-750 space-y-1">
                      <p><span className="text-gray-300 font-medium">Mục tiêu (TP):</span> {upcomingData.scenarios?.bearish?.target || 'PDL / Swing Low'}</p>
                      <p className="text-rose-400/90"><span className="text-gray-300 font-medium">Vô hiệu:</span> {upcomingData.scenarios?.bearish?.invalidation || 'Đóng trên Swing High'}</p>
                    </div>
                  </div>

                  {/* No-Trade Scenario */}
                  <div className="bg-charcoal-850 p-3.5 rounded-lg border border-amber-900/50 flex flex-col gap-2">
                    <div className="flex items-center justify-between border-b border-charcoal-750 pb-1.5">
                      <span className="font-bold text-amber-400">Kịch Bản Đứng Ngoài (No-Trade)</span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 font-mono">
                        DEFENSE
                      </span>
                    </div>
                    <p className="text-gray-300 text-[11px] leading-relaxed">
                      {upcomingData.scenarios?.no_trade?.condition || 'Cửa sổ Blackout tin tức USD High Impact, thị trường chop hoặc Net R:R < 2.0.'}
                    </p>
                    <div className="text-[10px] text-gray-400 mt-auto pt-2 border-t border-charcoal-750 space-y-1">
                      <p className="text-amber-300/90"><span className="text-gray-300 font-medium">Hành động:</span> {upcomingData.scenarios?.no_trade?.action || 'Không mở lệnh. Giữ nguyên vốn và quota 3 lệnh.'}</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Setups Watchlist & Filter */}
              <div className="space-y-3">
                <div className="flex flex-wrap justify-between items-center gap-2">
                  <h3 className="text-xs font-bold text-gray-300 uppercase tracking-wider flex items-center gap-1.5">
                    <ListOrdered className="w-3.5 h-3.5 text-aurum-400" />
                    Danh Sách Setups Đang Theo Dõi ({filteredSetups.length} Setups)
                  </h3>
                  <div className="flex gap-1 bg-charcoal-850 p-1 rounded border border-charcoal-700 text-[11px]">
                    {['ALL', 'READY', 'ARMED', 'WAITING', 'TERMINAL'].map((flt) => (
                      <button
                        key={flt}
                        onClick={() => setUpcomingFilter(flt)}
                        className={`px-2.5 py-0.5 rounded font-medium transition ${
                          upcomingFilter === flt
                            ? 'bg-aurum-500 text-charcoal-950 font-bold'
                            : 'text-gray-400 hover:text-gray-200'
                        }`}
                      >
                        {flt}
                      </button>
                    ))}
                  </div>
                </div>

                {filteredSetups.length === 0 ? (
                  <div className="p-8 text-center text-xs text-gray-400 italic bg-charcoal-850 rounded-lg border border-charcoal-750">
                    Chưa có setup nào phù hợp bộ lọc hiện tại. Hệ thống đang quét chu kỳ nến để tìm kiếm POI và Sweep.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {filteredSetups.map((s) => (
                      <div
                        key={s.id}
                        className={`p-4 rounded-lg border text-xs flex flex-col gap-3 transition ${
                          s.state === 'READY'
                            ? 'bg-emerald-950/20 border-emerald-500/50 shadow-emerald-950/30'
                            : s.state === 'ARMED'
                            ? 'bg-amber-950/20 border-amber-500/50'
                            : 'bg-charcoal-850 border-charcoal-700'
                        }`}
                      >
                        {/* Top row: Strategy, Direction, State badge */}
                        <div className="flex flex-wrap justify-between items-center gap-2 border-b border-charcoal-750 pb-2">
                          <div className="flex items-center gap-2">
                            <span
                              className={`px-2 py-0.5 rounded font-bold text-xs ${
                                s.direction === 'LONG'
                                  ? 'bg-emerald-900/60 text-emerald-300'
                                  : 'bg-rose-900/60 text-rose-300'
                              }`}
                            >
                              {s.direction} XAUUSDT
                            </span>
                            <span className="font-semibold text-gray-200">{s.strategy}</span>
                            <span className="text-gray-400 font-mono text-[11px]">({s.timeframe})</span>
                            <span className="text-[10px] text-gray-400 font-mono">ID: {s.id}</span>
                          </div>

                          <div className="flex items-center gap-2">
                            <span
                              className={`px-2.5 py-0.5 rounded font-bold text-[11px] font-mono tracking-wider ${
                                s.state === 'READY'
                                  ? 'bg-emerald-500 text-charcoal-950 animate-pulse'
                                  : s.state === 'ARMED'
                                  ? 'bg-amber-500 text-charcoal-950'
                                  : s.state.startsWith('WAITING')
                                  ? 'bg-blue-900/60 text-blue-300 border border-blue-700'
                                  : 'bg-charcoal-800 text-gray-400 border border-charcoal-700'
                              }`}
                            >
                              {s.state}
                            </span>
                          </div>
                        </div>

                        {/* Mid row: Planned Levels, R:R, Distance */}
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-charcoal-900/60 p-2.5 rounded border border-charcoal-750">
                          <div>
                            <span className="text-gray-400 text-[10px] block">Entry (Dự kiến):</span>
                            <span className="font-bold text-gray-200 text-sm">
                              ${s.confirmed_entry || s.provisional_entry || 'Chưa xác định'}
                            </span>
                          </div>
                          <div>
                            <span className="text-gray-400 text-[10px] block">Stop Loss (SL):</span>
                            <span className="font-bold text-rose-400 text-sm">
                              ${s.confirmed_sl || s.provisional_sl || 'Chưa xác định'}
                            </span>
                          </div>
                          <div>
                            <span className="text-gray-400 text-[10px] block">Take Profit (TP):</span>
                            <span className="font-bold text-emerald-400 text-sm">
                              ${s.confirmed_tp || s.provisional_tp || 'Chưa xác định'}
                            </span>
                          </div>
                          <div>
                            <span className="text-gray-400 text-[10px] block">Net R:R & Kích thước:</span>
                            <span
                              className={`font-bold text-sm ${
                                s.net_rr >= 2.0 ? 'text-emerald-400' : 'text-amber-400'
                              }`}
                            >
                              1:{s.net_rr?.toFixed(2) || '2.00'} Net
                            </span>
                            <span className="text-gray-400 text-[10px] block">
                              {s.quantity || 0.05} oz (${s.risk_usdt || 2.5} Risk)
                            </span>
                          </div>
                        </div>

                        {/* Distance to zone & Estimated Liquidation */}
                        <div className="flex flex-wrap justify-between items-center gap-3 text-[11px] text-gray-400">
                          <div className="flex items-center gap-3">
                            <span>
                              Khoảng cách tới Entry:{' '}
                              <strong className="text-gray-200">
                                ${s.distance_to_entry_usdt?.toFixed(2) || '0.00'} USDT
                              </strong>{' '}
                              ({s.distance_to_entry_atr?.toFixed(2) || '0.0'} ATR)
                            </span>
                            <span>·</span>
                            <span>
                              Vùng Entry:{' '}
                              <strong className="text-gray-200 font-mono">
                                ${s.entry_zone_low?.toFixed(2) || s.provisional_entry?.toFixed(2)} — ${s.entry_zone_high?.toFixed(2) || s.provisional_entry?.toFixed(2)}
                              </strong>
                            </span>
                            {s.near_entry_alerted_at && (
                              <>
                                <span>·</span>
                                <span className="text-emerald-400 font-semibold">
                                  🔔 Đã báo gần Entry lúc {new Date(s.near_entry_alerted_at).toLocaleTimeString('vi-VN')}
                                </span>
                              </>
                            )}
                            <span>·</span>
                            <span>
                              Đòn bẩy:{' '}
                              <strong className="text-aurum-400 font-mono">
                                {s.leverage || leverage}x {s.margin_mode || marginMode}
                              </strong>
                            </span>
                            <span>·</span>
                            <span>
                              Thanh lý ước tính:{' '}
                              <strong className="text-rose-400 font-mono">
                                {typeof s.estimated_liquidation === 'number' && s.estimated_liquidation > 0
                                  ? `$${s.estimated_liquidation.toFixed(2)}`
                                  : 'Chưa có ước tính hợp lệ'}
                              </strong>
                            </span>
                          </div>
                        </div>

                        {/* Conditions Checklist: Met vs Remaining */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] pt-1">
                          <div className="bg-charcoal-900/50 p-2 rounded border border-charcoal-750">
                            <span className="text-emerald-400 font-semibold block mb-1 flex items-center gap-1">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              Điều kiện đã thỏa ({s.conditions_met?.length || 0}):
                            </span>
                            {s.conditions_met?.length > 0 ? (
                              <ul className="space-y-0.5 text-gray-300">
                                {s.conditions_met.map((c: string, idx: number) => (
                                  <li key={idx} className="flex items-center gap-1 text-[10px]">
                                    <span className="w-1 h-1 rounded-full bg-emerald-400" />
                                    <span>{c}</span>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <span className="text-gray-500 italic text-[10px]">Đang theo dõi bước đầu tiên</span>
                            )}
                          </div>

                          <div className="bg-charcoal-900/50 p-2 rounded border border-charcoal-750">
                            <span className="text-amber-400 font-semibold block mb-1 flex items-center gap-1">
                              <Clock className="w-3.5 h-3.5" />
                              Điều kiện đang chờ ({s.conditions_remaining?.length || 0}):
                            </span>
                            {s.conditions_remaining?.length > 0 ? (
                              <ul className="space-y-0.5 text-amber-300/90">
                                {s.conditions_remaining.map((c: string, idx: number) => (
                                  <li key={idx} className="flex items-center gap-1 text-[10px]">
                                    <span className="w-1 h-1 rounded-full bg-amber-400" />
                                    <span>{c}</span>
                                  </li>
                                ))}
                              </ul>
                            ) : s.conditions_met?.length > 0 ? (
                              <span className="text-emerald-400 font-semibold text-[10px]">
                                Đủ toàn bộ điều kiện! Sẵn sàng Arm.
                              </span>
                            ) : (
                              <span className="text-gray-400 italic text-[10px]">
                                Chưa có dữ liệu đánh giá điều kiện
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Actions bar */}
                        <div className="flex flex-wrap justify-between items-center gap-2 pt-2 border-t border-charcoal-750">
                          <div className="flex gap-2">
                            <button
                              onClick={() => handleFocusSetupOnChart(s)}
                              className="flex items-center gap-1 px-3 py-1.5 bg-charcoal-800 hover:bg-charcoal-750 text-gray-200 rounded text-xs border border-charcoal-700 transition"
                            >
                              <Eye className="w-3.5 h-3.5 text-indigo-400" />
                              <span>Xem Trên Chart</span>
                            </button>
                            <button
                              onClick={() => handleCopySetupToDraft(s)}
                              className="flex items-center gap-1 px-3 py-1.5 bg-charcoal-800 hover:bg-charcoal-750 text-gray-200 rounded text-xs border border-charcoal-700 transition"
                            >
                              <Copy className="w-3.5 h-3.5 text-aurum-400" />
                              <span>Chép sang Bản Nháp (Draft)</span>
                            </button>
                          </div>

                          <div className="flex gap-2">
                            {(() => {
                              const isCross = (s.margin_mode || marginMode) === 'CROSS';
                              const isBlockedByActive = !!activePosition?.has_active_position;
                              const isBlockedByArmed = hasArmedOrder;
                              const eligibilityBlocked = s.eligibility && !s.eligibility.can_arm;
                              const cannotArm = isCross || isBlockedByActive || isBlockedByArmed || eligibilityBlocked;
                              const armBlockReason = isCross
                                ? 'Chặn Arm: Cross margin chưa hỗ trợ (Cần Isolated)'
                                : isBlockedByActive
                                ? 'Không thể Arm: Đang có 1 vị thế mở'
                                : isBlockedByArmed
                                ? 'Không thể Arm: Đang có lệnh ARMED chờ khớp'
                                : (s.eligibility?.block_reasons?.[0] || 'Chưa đủ điều kiện Arm');

                              return ['READY', 'WAITING_PRICE', 'WAITING_RETRACE'].includes(s.state) && (
                                cannotArm ? (
                                  <button
                                    disabled
                                    title={armBlockReason}
                                    className="flex items-center gap-1.5 px-3 py-1.5 bg-charcoal-750 text-gray-400 font-bold rounded text-xs cursor-not-allowed border border-charcoal-700 shadow-sm"
                                  >
                                    <PlayCircle className="w-3.5 h-3.5 text-gray-500" />
                                    <span>{armBlockReason}</span>
                                  </button>
                                ) : (
                                  <button
                                    onClick={() => handleArmWatchSetup(s.id, s.direction, s.setup_instance_id, s.version)}
                                    className="flex items-center gap-1.5 px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded text-xs transition shadow-sm"
                                  >
                                    <PlayCircle className="w-3.5 h-3.5" />
                                    <span>Arm {s.direction} — PAPER</span>
                                  </button>
                                )
                              );
                            })()}

                            {!['CANCELLED', 'INVALIDATED', 'EXPIRED', 'CLOSED'].includes(s.state) && (
                              <button
                                onClick={() => handleCancelWatchSetup(s.id)}
                                className="flex items-center gap-1 px-2.5 py-1.5 bg-charcoal-800 hover:bg-rose-950 text-rose-300 rounded text-xs border border-rose-900/50 transition"
                              >
                                <XCircle className="w-3.5 h-3.5" />
                                <span>Hủy Setup</span>
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB: PHÂN TÍCH SMC/ICT & MA TRẬN ĐA KHUNG */}
          {activeTab === 'smc' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-4 overflow-y-auto max-h-[750px]">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400">Phân Tích Cấu Trúc SMC/ICT & Ma Trận Đa Khung</h2>
                  <p className="text-xs text-gray-400">Đánh giá causal tuần tự không lookahead: D/4H định hướng, H1 liên kết, 15M POI, 5M trigger</p>
                </div>
                <span className="text-xs text-gray-400">Khung đang xem: {timeframe}</span>
              </div>

              {/* Multi-Timeframe Matrix */}
              <div>
                <h3 className="text-xs font-semibold text-gray-300 mb-2 uppercase tracking-wider">
                  Ma Trận Xu Hướng Đa Khung Thời Gian (Multi-Timeframe Matrix)
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-6 gap-2 text-xs">
                  {marketMatrix?.matrix?.map((m: any) => (
                    <div
                      key={m.timeframe}
                      className={`p-3 rounded-lg border flex flex-col gap-1 ${
                        m.timeframe === timeframe
                          ? 'bg-charcoal-800 border-aurum-500'
                          : 'bg-charcoal-850 border-charcoal-700'
                      }`}
                    >
                      <div className="flex justify-between items-center">
                        <span className="font-bold text-gray-200">{m.timeframe}</span>
                        <span
                          className={`w-2 h-2 rounded-full ${
                            m.is_stale ? 'bg-amber-400' : 'bg-emerald-400'
                          }`}
                        />
                      </div>
                      <span
                        className={`font-bold ${
                          m.trend === 'BULLISH'
                            ? 'text-emerald-400'
                            : m.trend === 'BEARISH'
                            ? 'text-rose-400'
                            : 'text-amber-400'
                        }`}
                      >
                        {m.trend}
                      </span>
                      <span className="text-[10px] text-gray-400">{m.zone}</span>
                      <span className="text-[10px] text-indigo-300 font-mono">ATR: ${m.atr?.toFixed(2)}</span>
                    </div>
                  )) || (
                    <div className="col-span-6 text-xs text-gray-400 italic">Đang tải ma trận đa khung...</div>
                  )}
                </div>
              </div>

              {/* SMC Metrics in selected timeframe */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block mb-1">Xu Hướng Cấu Trúc ({timeframe})</span>
                  <span
                    className={`text-base font-bold ${
                      analysis?.trend === 'BULLISH'
                        ? 'text-emerald-400'
                        : analysis?.trend === 'BEARISH'
                        ? 'text-rose-400'
                        : 'text-amber-400'
                    }`}
                  >
                    {analysis?.trend || 'RANGING'}
                  </span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block mb-1">Vị Trí Dealing Range</span>
                  <span
                    className={`text-base font-bold ${
                      analysis?.zone === 'DISCOUNT' ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    {analysis?.zone || 'EQUILIBRIUM'} (${analysis?.equilibrium?.toFixed(2)})
                  </span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block mb-1">Biên Độ ATR (14)</span>
                  <span className="text-base font-bold text-indigo-300">
                    ${analysis?.atr?.toFixed(2)} USDT
                  </span>
                </div>
              </div>

              {/* FVG List */}
              <div>
                <h3 className="text-xs font-semibold text-gray-300 mb-2 uppercase tracking-wider">
                  Vùng Mất Cân Bằng (Fair Value Gaps - FVG Còn Hiệu Lực)
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                  {analysis?.active_fvgs?.map((fvg: any, idx: number) => (
                    <div
                      key={idx}
                      className="bg-charcoal-850 p-2.5 rounded border border-charcoal-700 flex justify-between items-center"
                    >
                      <div>
                        <span
                          className={`font-semibold ${
                            fvg.type === 'BULLISH_FVG' ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {fvg.type === 'BULLISH_FVG' ? 'Bullish FVG' : 'Bearish FVG'}
                        </span>
                        <span className="text-gray-400 block text-[11px]">
                          Vùng: ${fvg.bottom} – ${fvg.top}
                        </span>
                      </div>
                      <span className="px-2 py-0.5 rounded text-[10px] bg-charcoal-900 text-gray-300 border border-charcoal-700">
                        {fvg.state}
                      </span>
                    </div>
                  ))}
                  {(!analysis?.active_fvgs || analysis.active_fvgs.length === 0) && (
                    <p className="text-xs text-gray-500 italic">Không có FVG chưa lấp trong 150 nến gần nhất.</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB: QUẢN TRỊ VỐN & KÝ QUỸ (RISK & MARGIN) */}
          {activeTab === 'paper' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-5 overflow-y-auto max-h-[750px]">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400">Quản Trị Rủi Ro & Ký Quỹ Bitget Isolated</h2>
                  <p className="text-xs text-gray-400">Giả lập paper trading theo quy tắc ký quỹ chính thức của Bitget Classic USDT-Perpetual</p>
                </div>
                <button
                  onClick={handleToggleAutoPaper}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-bold transition ${
                    autoPaperActive
                      ? 'bg-emerald-600 hover:bg-emerald-500 text-white'
                      : 'bg-charcoal-800 text-gray-400 border border-charcoal-700'
                  }`}
                >
                  {autoPaperActive ? <PlayCircle className="w-4 h-4" /> : <PauseCircle className="w-4 h-4" />}
                  <span>{autoPaperActive ? 'Auto Paper: ĐANG BẬT' : 'Auto Paper: TẠM DỪNG'}</span>
                </button>
              </div>

              {/* Risk Cards */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block">Vốn Hiện Tại (Equity)</span>
                  <span className="text-lg font-bold text-emerald-400">
                    ${accountStatus?.current_equity?.toFixed(2) || '1,000.00'}
                  </span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block">Số Lệnh Hôm Nay</span>
                  <span className="text-lg font-bold text-aurum-400">
                    {accountStatus?.today_fills_count ?? accountStatus?.fills_count ?? 0} / 3 Lệnh
                  </span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block">PnL Realized Hôm Nay</span>
                  <span
                    className={`text-lg font-bold ${
                      accountStatus?.realized_pnl_today >= 0 ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    ${accountStatus?.realized_pnl_today?.toFixed(2) || '0.00'}
                  </span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block">Giới Hạn Lỗ Ngày (1.5%)</span>
                  <span className="text-lg font-bold text-rose-400">
                    ${accountStatus?.daily_loss_limit_usdt?.toFixed(2) || '15.00'}
                  </span>
                </div>
              </div>

              {/* Leverage & Margin Controls */}
              {(() => {
                const minLeverage = instrumentMeta?.min_leverage ?? accountStatus?.min_leverage ?? 1;
                const maxLeverage = instrumentMeta?.max_leverage ?? accountStatus?.max_leverage ?? 100;
                const currentEquity = accountStatus?.current_equity ?? 1000;
                const draftRiskBudgetUsdt = ((currentEquity * draftRiskSettings.risk_pct) / 100).toFixed(2);
                const isCrossSelected = draftRiskSettings.margin_mode === 'CROSS';

                return (
                  <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-700 flex flex-col gap-4 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-charcoal-750 pb-2.5">
                      <h3 className="font-bold text-gray-200 uppercase tracking-wider flex items-center gap-1.5">
                        <Sliders className="w-3.5 h-3.5 text-aurum-400" />
                        Cấu Hình Đòn Bẩy (Leverage) & Chế Độ Ký Quỹ
                      </h3>
                      <div className="flex items-center gap-2 text-[11px] text-gray-400">
                        <span className="px-2 py-0.5 rounded bg-charcoal-800 border border-charcoal-700 text-gray-300">
                          {instrumentMeta?.source || 'Bitget USDT-M Perpetual'}
                        </span>
                        <span>Giới hạn: <strong className="text-aurum-400 font-mono">{minLeverage}x - {maxLeverage}x</strong></span>
                        <span className="text-charcoal-600">|</span>
                        <span>Config: <strong className="text-gray-200 font-mono">v{savedRiskSettings.config_version}</strong></span>
                      </div>
                    </div>

                    {/* Unsaved Draft Banner */}
                    {isSettingsDirty && (
                      <div className="p-2.5 rounded bg-amber-950/40 border border-amber-500/50 text-amber-200 text-xs flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <Info className="w-4 h-4 text-amber-400 shrink-0" />
                          <span>
                            <strong>Bản Nháp Chưa Lưu (Draft v{savedRiskSettings.config_version} → v{savedRiskSettings.config_version + 1}):</strong> Đòn bẩy {draftRiskSettings.leverage}x · {draftRiskSettings.margin_mode} · {draftRiskSettings.risk_pct}%. Các giá trị này <em>chưa được áp dụng</em> vào chiến lược hay vị thế thực tế.
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={handleCancelDraft}
                          className="px-2.5 py-1 bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 border border-charcoal-700 rounded text-[11px] font-semibold flex items-center gap-1 shrink-0 transition"
                        >
                          <RotateCcw className="w-3 h-3 text-amber-400" />
                          Hủy Thay Đổi
                        </button>
                      </div>
                    )}

                    {/* Cross Margin Capability Warning */}
                    {isCrossSelected && (
                      <div className="p-3 rounded bg-rose-950/40 border border-rose-500/50 text-rose-200 text-xs flex items-start gap-2.5">
                        <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                        <div className="space-y-1">
                          <div className="font-bold text-rose-300">
                            CẢNH BÁO NĂNG LỰC (CAPABILITY NOTICE): CHƯA HỖ TRỢ THỰC THI CROSS
                          </div>
                          <p className="text-[11px] text-gray-300 leading-relaxed">
                            Môi trường Bitget PAPER trading hiện tại <strong>chưa hỗ trợ thực thi Cross Margin</strong>. Nếu lưu tùy chọn này, các lệnh mở mới sẽ bị chặn an toàn với mã lỗi <code className="text-rose-300 font-mono">CROSS_MARGIN_UNSUPPORTED</code>. Khuyến nghị: Chọn chế độ <strong>ISOLATED</strong> để lệnh được kích hoạt và bảo vệ rủi ro từng vị thế.
                          </p>
                        </div>
                      </div>
                    )}

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                      {/* Leverage slider & presets */}
                      <div className="space-y-2.5">
                        <div className="flex justify-between items-center">
                          <span className="text-gray-300 font-medium">Đòn bẩy dự kiến (Draft):</span>
                          <div className="flex items-center gap-1.5">
                            <input
                              type="number"
                              min={minLeverage}
                              max={maxLeverage}
                              value={draftRiskSettings.leverage}
                              onChange={(e) => {
                                const val = Math.max(minLeverage, Math.min(maxLeverage, Number(e.target.value) || minLeverage));
                                handleDraftChange('leverage', val);
                              }}
                              className="w-14 bg-charcoal-900 border border-charcoal-700 rounded px-1.5 py-0.5 text-right font-bold text-aurum-400 font-mono text-sm focus:outline-none focus:border-aurum-400"
                            />
                            <span className="text-sm font-bold text-aurum-400 font-mono">x</span>
                          </div>
                        </div>

                        <input
                          type="range"
                          min={minLeverage}
                          max={maxLeverage}
                          value={draftRiskSettings.leverage}
                          onChange={(e) => handleDraftChange('leverage', Number(e.target.value))}
                          className="w-full accent-aurum-500 cursor-pointer"
                        />

                        {/* Quick Presets */}
                        <div className="flex gap-1.5 pt-1">
                          {[1, 5, 20, 50, 100].map((preset) => {
                            const isAllowed = preset <= maxLeverage && preset >= minLeverage;
                            const isSelected = draftRiskSettings.leverage === preset;
                            return (
                              <button
                                key={preset}
                                type="button"
                                disabled={!isAllowed}
                                onClick={() => handleDraftChange('leverage', preset)}
                                className={`flex-1 py-1 rounded text-[11px] font-semibold border transition ${
                                  isSelected
                                    ? 'bg-aurum-500 text-charcoal-950 border-aurum-400 font-bold'
                                    : 'bg-charcoal-900 text-gray-300 hover:text-white border-charcoal-700 hover:bg-charcoal-800'
                                } disabled:opacity-30 disabled:cursor-not-allowed`}
                              >
                                {preset}x {preset === 5 ? '(Mặc định)' : preset === maxLeverage ? '(Max)' : ''}
                              </button>
                            );
                          })}
                        </div>

                        <p className="text-[10px] text-gray-400 leading-relaxed pt-1">
                          * Thay đổi đòn bẩy <strong>KHÔNG</strong> thay đổi rủi ro tính bằng USD ({draftRiskSettings.risk_pct}% vốn = ${draftRiskBudgetUsdt}) hay kích thước vị thế, mà chỉ thay đổi số tiền ký quỹ ban đầu yêu cầu và khoảng cách giá thanh lý (Liquidation Price).
                        </p>
                      </div>

                      {/* Margin Mode & Risk Pct */}
                      <div className="space-y-3.5">
                        <div>
                          <div className="flex justify-between items-center mb-1">
                            <span className="text-gray-300 font-medium">Chế độ ký quỹ:</span>
                            {isCrossSelected && (
                              <span className="text-[10px] text-rose-400 font-semibold px-1.5 py-0.5 rounded bg-rose-950/60 border border-rose-800">
                                Chặn Execution
                              </span>
                            )}
                          </div>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={() => handleDraftChange('marginMode', 'ISOLATED')}
                              className={`flex-1 py-2 rounded font-semibold text-xs border transition ${
                                draftRiskSettings.margin_mode === 'ISOLATED'
                                  ? 'bg-aurum-500 text-charcoal-950 border-aurum-400 font-bold shadow-sm'
                                  : 'bg-charcoal-900 text-gray-400 hover:text-gray-200 border-charcoal-700'
                              }`}
                            >
                              ISOLATED (Cô lập)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDraftChange('marginMode', 'CROSS')}
                              className={`flex-1 py-2 rounded font-semibold text-xs border transition ${
                                draftRiskSettings.margin_mode === 'CROSS'
                                  ? 'bg-rose-500/20 text-rose-300 border-rose-500 font-bold shadow-sm'
                                  : 'bg-charcoal-900 text-gray-400 hover:text-gray-200 border-charcoal-700'
                              }`}
                            >
                              CROSS (Toàn tài khoản)
                            </button>
                          </div>
                        </div>

                        <div>
                          <span className="text-gray-300 font-medium block mb-1">Rủi ro mỗi lệnh (% Equity):</span>
                          <div className="flex gap-2">
                            {[0.25, 0.5, 1.0].map((pct) => (
                              <button
                                key={pct}
                                type="button"
                                onClick={() => handleDraftChange('riskPct', pct)}
                                className={`flex-1 py-1.5 rounded font-semibold text-xs border transition ${
                                  draftRiskSettings.risk_pct === pct
                                    ? 'bg-aurum-500 text-charcoal-950 border-aurum-400 font-bold shadow-sm'
                                    : 'bg-charcoal-900 text-gray-400 hover:text-gray-200 border-charcoal-700'
                                }`}
                              >
                                {pct}% (${((currentEquity * pct) / 100).toFixed(2)})
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Messages & Actions */}
                    {settingsSavedMsg && (
                      <div className="p-2.5 rounded bg-emerald-950/80 border border-emerald-700 text-emerald-300 text-xs flex items-center gap-2">
                        <Check className="w-4 h-4 text-emerald-400" />
                        <span>{settingsSavedMsg}</span>
                      </div>
                    )}

                    {settingsSaveError && (
                      <div className="p-2.5 rounded bg-rose-950/80 border border-rose-700 text-rose-300 text-xs flex items-center gap-2">
                        <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
                        <span>{settingsSaveError}</span>
                      </div>
                    )}

                    {needsRefreshRetry && (
                      <div className="p-2.5 rounded bg-amber-950/80 border border-amber-600 text-amber-200 text-xs flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
                          <span>Đã lưu, đánh giá chưa cập nhật. Bấm Thử Lại để làm mới trạng thái tài khoản.</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => refreshAccountAndHealth()}
                          className="px-3 py-1 bg-amber-500 hover:bg-amber-400 text-charcoal-950 font-bold rounded text-xs transition"
                        >
                          Thử Lại
                        </button>
                      </div>
                    )}

                    <div className="flex justify-end items-center gap-3 pt-2 border-t border-charcoal-750">
                      {isSettingsDirty && (
                        <button
                          type="button"
                          onClick={handleCancelDraft}
                          className="px-3.5 py-2 bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 font-semibold rounded text-xs transition border border-charcoal-700"
                        >
                          Hủy Bản Nháp
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={handleSaveRiskSettings}
                        disabled={isSavingSettings || !isSettingsDirty}
                        className={`px-4 py-2 font-bold rounded text-xs transition flex items-center gap-1.5 ${
                          isSettingsDirty
                            ? 'bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 cursor-pointer shadow-md'
                            : 'bg-charcoal-800 text-gray-400 border border-charcoal-700 cursor-default'
                        } disabled:opacity-50`}
                      >
                        {isSavingSettings ? (
                          <>
                            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                            Đang Lưu...
                          </>
                        ) : isSettingsDirty ? (
                          'Lưu Cài Đặt (Draft)'
                        ) : (
                          `Cài Đặt Đã Đồng Bộ (v${savedRiskSettings.config_version})`
                        )}
                      </button>
                    </div>
                  </div>
                );
              })()}

              {/* Liquidation safety rules */}
              <div className="p-3.5 bg-charcoal-850 rounded-lg border border-charcoal-700 text-xs space-y-2">
                <span className="font-bold text-gray-200 block flex items-center gap-1.5">
                  <ShieldAlert className="w-4 h-4 text-emerald-400" />
                  Quy Tắc Bảo Vệ Giá Thanh Lý (Liquidation Invariants)
                </span>
                <p className="text-gray-300 leading-relaxed text-[11px]">
                  Hệ thống kiểm tra nghiêm ngặt: với lệnh Long, giá thanh lý bắt buộc phải thấp hơn Stop Loss kèm khoảng đệm an toàn (<code className="text-aurum-300">LP &lt; SL &lt; Entry</code>); với lệnh Short, giá thanh lý phải cao hơn Stop Loss (<code className="text-aurum-300">Entry &lt; SL &lt; LP</code>). Nếu đòn bẩy quá cao dẫn tới giá thanh lý nằm trước hoặc quá gần Stop Loss, hệ thống sẽ tự động chặn vào lệnh (<code>LIQUIDATION_BEFORE_SL</code>).
                </p>
              </div>
            </div>
          )}

          {/* TAB: PHÒNG KIỂM THỬ RỦI RO (TESTING LAB) */}
          {activeTab === 'lab' && (
            <TestingLabComponent onNotify={showToast} />
          )}

          {/* TAB: BÁO CÁO PHIÊN & NGÀY */}
          {activeTab === 'reports' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-4 overflow-y-auto max-h-[750px]">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <h2 className="text-base font-bold text-aurum-400">Báo Cáo Nghiên Cứu Phiên Á – Âu – Mỹ & Premarket</h2>
                <button
                  onClick={() => api.generateReport('SESSION_REPORT').then(() => api.getReports().then((r) => setReports(r.reports)))}
                  className="px-3 py-1 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition"
                >
                  Tạo Báo Cáo Mới
                </button>
              </div>

              <div className="space-y-4">
                {reports.map((rep) => (
                  <div key={rep.id} className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-700">
                    <div className="flex justify-between text-xs text-gray-400 mb-2 border-b border-charcoal-750 pb-1">
                      <span className="font-semibold text-aurum-400">{rep.report_type} · {rep.session_name}</span>
                      <span>{new Date(rep.created_at).toLocaleString('vi-VN')}</span>
                    </div>
                    <div className="text-xs text-gray-300 whitespace-pre-wrap font-mono leading-relaxed">
                      {rep.content_markdown}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB: TIN TỨC & BLACKOUT */}
          {activeTab === 'news' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-4 overflow-y-auto max-h-[750px]">
              <div className="flex flex-wrap justify-between items-center border-b border-charcoal-750 pb-3 gap-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400 flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-aurum-400" />
                    Lịch Tin Tức Vĩ Mô & Nghiên Cứu URL
                  </h2>
                  <p className="text-xs text-gray-400">
                    Phân tích kênh tác động tới Vàng (XAUUSDT), đọc nguồn Forex Factory và xác thực múi giờ
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-semibold cursor-pointer transition shadow-sm">
                    <Upload className="w-3.5 h-3.5" />
                    <span>Nhập Lịch (CSV / JSON)</span>
                    <input type="file" accept=".json,.csv" onChange={handleFileUpload} className="hidden" />
                  </label>
                </div>
              </div>

              {importStatus && (
                <div className="p-2.5 rounded bg-charcoal-800 border border-charcoal-700 text-xs text-aurum-400">
                  {importStatus}
                </div>
              )}

              {/* Blackout status banner */}
              <div
                className={`p-3 rounded-lg border flex items-center justify-between text-xs ${
                  newsData?.blackout_status?.is_blackout
                    ? 'bg-rose-950/80 border-rose-800 text-rose-200'
                    : 'bg-emerald-950/60 border-emerald-800 text-emerald-200'
                }`}
              >
                <div className="flex items-center gap-2">
                  <ShieldAlert className="w-4 h-4" />
                  <span>
                    {newsData?.blackout_status?.is_blackout
                      ? `Đang trong khung giờ Blackout: ${newsData.blackout_status.reason}`
                      : 'An toàn: Không có tin tức USD High Impact trong cửa sổ -30m / +15m'}
                  </span>
                </div>
                {newsData?.blackout_status?.is_blackout && (
                  <span className="font-bold">Còn {newsData.blackout_status.remaining_minutes} phút</span>
                )}
              </div>

              {/* News events table */}
              <div className="overflow-x-auto text-xs">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-charcoal-700 text-gray-400">
                      <th className="py-2">Thời gian (VN UTC+7)</th>
                      <th>Giờ Nguồn</th>
                      <th>Sự kiện</th>
                      <th>Quốc gia</th>
                      <th>Tác động</th>
                      <th>Liên quan Vàng</th>
                      <th>Dự báo / Kỳ trước</th>
                      <th>Thực tế</th>
                      <th className="text-right pr-2">Thao tác</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-charcoal-800">
                    {newsData?.events?.map((ev: any) => {
                      const goldRel = ev.gold_relevance || (ev.country === 'USD' && ev.impact === 'High' ? 'HIGH' : 'LOW');
                      return (
                        <tr key={ev.id} className="hover:bg-charcoal-850 transition">
                          <td className="py-2.5 text-gray-300 font-mono">
                            {new Date(ev.scheduled_at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}{' '}
                            <span className="text-[10px] text-gray-500">{new Date(ev.scheduled_at).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' })}</span>
                          </td>
                          <td className="text-gray-400 font-mono text-[11px]">
                            {ev.source_time_raw || '-'}
                          </td>
                          <td>
                            <div className="font-medium text-gray-200 flex items-center gap-1.5">
                              <span>{ev.title}</span>
                              {ev.source_url && (
                                <a
                                  href={ev.source_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-gray-500 hover:text-aurum-400 transition"
                                  title="Mở nguồn bài viết"
                                >
                                  <ExternalLink className="w-3 h-3" />
                                </a>
                              )}
                            </div>
                          </td>
                          <td className="text-gray-400 font-semibold">{ev.country}</td>
                          <td>
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                                ev.impact === 'High'
                                  ? 'bg-rose-900/60 text-rose-300 border border-rose-800/50'
                                  : ev.impact === 'Medium'
                                  ? 'bg-amber-900/60 text-amber-300 border border-amber-800/50'
                                  : 'bg-gray-800 text-gray-400'
                              }`}
                            >
                              {ev.impact}
                            </span>
                          </td>
                          <td>
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                goldRel === 'HIGH'
                                  ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                                  : goldRel === 'MEDIUM'
                                  ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                  : 'bg-charcoal-800 text-gray-400'
                              }`}
                            >
                              {goldRel === 'HIGH' ? '⚡ RẤT CAO' : goldRel === 'MEDIUM' ? 'TRUNG BÌNH' : 'THẤP'}
                            </span>
                          </td>
                          <td className="text-gray-400 font-mono">
                            {ev.forecast || '-'} / <span className="text-gray-500">{ev.previous || '-'}</span>
                          </td>
                          <td className="font-bold text-aurum-400 font-mono">{ev.actual || '-'}</td>
                          <td className="text-right pr-2">
                            <button
                              onClick={() => handleOpenNewsResearch(ev.id)}
                              disabled={researchLoadingId === ev.id}
                              className="px-2.5 py-1 bg-charcoal-750 hover:bg-charcoal-700 text-aurum-400 hover:text-aurum-300 rounded font-semibold text-[11px] inline-flex items-center gap-1 border border-charcoal-650 transition disabled:opacity-50"
                            >
                              <Sparkles className="w-3 h-3" />
                              <span>{researchLoadingId === ev.id ? 'Đang đọc...' : 'Nghiên Cứu'}</span>
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* MODAL: PREVIEW IMPORT CSV WITH TIMEZONE SELECTOR */}
              {importModalOpen && importPreview && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                  <div className="bg-charcoal-900 border border-charcoal-700 rounded-xl max-w-3xl w-full max-h-[85vh] flex flex-col shadow-2xl">
                    <div className="p-4 border-b border-charcoal-750 flex justify-between items-center">
                      <div>
                        <h3 className="font-bold text-sm text-aurum-400">Xem Trước Lịch Forex Factory ({importFileName})</h3>
                        <p className="text-xs text-gray-400">Xác thực múi giờ và chuyển đổi chính xác sang giờ Việt Nam (UTC+7)</p>
                      </div>
                      <button onClick={() => setImportModalOpen(false)} className="text-gray-400 hover:text-white">
                        <X className="w-5 h-5" />
                      </button>
                    </div>

                    <div className="p-4 flex-1 overflow-y-auto space-y-4 text-xs">
                      {/* Timezone Selector */}
                      <div className="flex flex-wrap items-center justify-between gap-3 bg-charcoal-850 p-3 rounded border border-charcoal-750">
                        <div>
                          <span className="font-semibold text-gray-300 block">Múi Giờ Của File Nguồn:</span>
                          <span className="text-[11px] text-gray-500">Forex Factory xuất mặc định theo giờ New York (EDT/EST)</span>
                        </div>
                        <select
                          value={importTimezone}
                          onChange={(e) => handleRecheckPreviewWithTimezone(e.target.value)}
                          className="bg-charcoal-900 border border-charcoal-700 text-gray-200 px-3 py-1.5 rounded text-xs focus:outline-none focus:border-aurum-500"
                        >
                          <option value="America/New_York">America/New_York (Forex Factory Tiêu Chuẩn)</option>
                          <option value="Asia/Ho_Chi_Minh">Asia/Ho_Chi_Minh (UTC+7 Việt Nam)</option>
                          <option value="UTC">UTC (Giờ Quốc Tế Phối Hợp)</option>
                          <option value="Europe/London">Europe/London (GMT/BST)</option>
                        </select>
                      </div>

                      {/* Stats */}
                      <div className="grid grid-cols-3 gap-3">
                        <div className="p-2.5 rounded bg-charcoal-850 border border-charcoal-750 text-center">
                          <span className="text-gray-400 text-[11px] block">Tổng số dòng</span>
                          <span className="font-bold text-sm text-gray-200">{importPreview.total_rows}</span>
                        </div>
                        <div className="p-2.5 rounded bg-emerald-950/40 border border-emerald-800/60 text-center">
                          <span className="text-emerald-300 text-[11px] block">Hợp lệ chuyển đổi</span>
                          <span className="font-bold text-sm text-emerald-400">{importPreview.valid_count}</span>
                        </div>
                        <div className="p-2.5 rounded bg-rose-950/40 border border-rose-800/60 text-center">
                          <span className="text-rose-300 text-[11px] block">Lỗi / Thiếu giờ</span>
                          <span className="font-bold text-sm text-rose-400">{importPreview.invalid_count}</span>
                        </div>
                      </div>

                      {/* Error rows if any */}
                      {importPreview.errors?.length > 0 && (
                        <div className="p-3 bg-rose-950/60 border border-rose-800 rounded">
                          <span className="font-bold text-rose-300 block mb-1">Các dòng bị lỗi định dạng (sẽ được bỏ qua):</span>
                          <div className="max-h-24 overflow-y-auto space-y-0.5 text-[11px] text-rose-400">
                            {importPreview.errors.map((e: string, i: number) => (
                              <p key={i}>• {e}</p>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Sample Rows Table */}
                      <div className="overflow-x-auto border border-charcoal-750 rounded">
                        <table className="w-full text-left">
                          <thead className="bg-charcoal-850 text-gray-400 text-[11px]">
                            <tr>
                              <th className="p-2">#</th>
                              <th>Sự kiện</th>
                              <th>Giờ Gốc ({importTimezone})</th>
                              <th>Giờ Quy Đổi (VN UTC+7)</th>
                              <th>Tác động</th>
                              <th>Vàng</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-charcoal-800 text-[11px]">
                            {importPreview.preview_rows?.slice(0, 10).map((r: any) => (
                              <tr key={r.row_index} className={r.is_valid ? 'hover:bg-charcoal-850' : 'bg-rose-950/20'}>
                                <td className="p-2 text-gray-500">{r.row_index}</td>
                                <td className="font-medium text-gray-200">{r.title}</td>
                                <td className="text-gray-400 font-mono">{r.source_time_str}</td>
                                <td className="text-aurum-400 font-mono font-semibold">{r.time_vn_str}</td>
                                <td>{r.impact}</td>
                                <td>{r.gold_relevance}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <p className="text-[10px] text-gray-500 italic text-center">Hiển thị mẫu 10 dòng đầu tiên trong tổng số {importPreview.total_rows} dòng.</p>
                    </div>

                    <div className="p-4 border-t border-charcoal-750 flex justify-end gap-2 bg-charcoal-850/50">
                      <button
                        onClick={() => setImportModalOpen(false)}
                        className="px-3 py-1.5 rounded border border-charcoal-700 text-gray-300 hover:bg-charcoal-800 text-xs"
                      >
                        Hủy
                      </button>
                      <button
                        onClick={handleConfirmCommitImport}
                        disabled={isImportLoading || importPreview.valid_count === 0}
                        className="px-4 py-1.5 rounded bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold text-xs transition disabled:opacity-50"
                      >
                        {isImportLoading ? 'Đang lưu vào DB...' : `Xác Nhận Nhập ${importPreview.valid_count} Sự Kiện`}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* MODAL: RESEARCH ASSESSMENT FOR XAUUSDT */}
              {researchModalOpen && activeResearch && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                  <div className="bg-charcoal-900 border border-charcoal-700 rounded-xl max-w-2xl w-full max-h-[85vh] flex flex-col shadow-2xl">
                    <div className="p-4 border-b border-charcoal-750 flex justify-between items-center">
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-bold text-base text-gray-100">{activeResearch.title}</span>
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              activeResearch.gold_relevance === 'HIGH'
                                ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                                : activeResearch.gold_relevance === 'MEDIUM'
                                ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                : 'bg-charcoal-800 text-gray-400'
                            }`}
                          >
                            Tác Động Vàng: {activeResearch.gold_relevance === 'HIGH' ? 'RẤT CAO' : activeResearch.gold_relevance === 'MEDIUM' ? 'TRUNG BÌNH' : 'THẤP'}
                          </span>
                        </div>
                        <p className="text-xs text-gray-400">
                          {activeResearch.country} · Tác động: {activeResearch.impact} · Thời gian:{' '}
                          {new Date(activeResearch.scheduled_at).toLocaleTimeString('vi-VN')} {new Date(activeResearch.scheduled_at).toLocaleDateString('vi-VN')} (UTC+7)
                        </p>
                      </div>
                      <button onClick={() => setResearchModalOpen(false)} className="text-gray-400 hover:text-white">
                        <X className="w-5 h-5" />
                      </button>
                    </div>

                    <div className="p-4 flex-1 overflow-y-auto space-y-4 text-xs leading-relaxed text-gray-300">
                      {/* Section 1: Meaning in Vietnamese */}
                      <div className="bg-charcoal-850 p-3 rounded border border-charcoal-750">
                        <span className="font-bold text-aurum-400 block mb-1 text-xs flex items-center gap-1.5">
                          <Info className="w-3.5 h-3.5" /> Ý Nghĩa Chỉ Số Kinh Tế
                        </span>
                        <p className="text-gray-300 text-[11px]">{activeResearch.meaning_vn}</p>
                      </div>

                      {/* Section 2: Transmission channels */}
                      {activeResearch.transmission_channels && Object.keys(activeResearch.transmission_channels).length > 0 && (
                        <div className="bg-charcoal-850 p-3 rounded border border-charcoal-750">
                          <span className="font-bold text-aurum-400 block mb-1.5 text-xs flex items-center gap-1.5">
                            <TrendingUp className="w-3.5 h-3.5" /> Kênh Tác Động Tới Vàng (XAUUSDT)
                          </span>
                          <div className="space-y-1.5">
                            {Object.entries(activeResearch.transmission_channels).map(([channel, desc]: any) => (
                              <div key={channel} className="text-[11px]">
                                <span className="font-semibold text-gray-200">{channel}:</span>{' '}
                                <span className="text-gray-400">{desc}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Section 3: Pre-release Scenarios */}
                      {activeResearch.pre_release_scenarios?.length > 0 && (
                        <div className="bg-charcoal-850 p-3 rounded border border-charcoal-750">
                          <span className="font-bold text-aurum-400 block mb-1.5 text-xs flex items-center gap-1.5">
                            <Compass className="w-3.5 h-3.5" /> Kịch Bản Dự Báo Trước Tin
                          </span>
                          <div className="space-y-1 text-[11px] text-gray-300">
                            {activeResearch.pre_release_scenarios.map((sc: string, idx: number) => (
                              <p key={idx}>• {sc}</p>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Section 4: Post-Release Surprise */}
                      {activeResearch.post_release_assessment && (
                        <div className="bg-emerald-950/40 p-3 rounded border border-emerald-800/60">
                          <span className="font-bold text-emerald-300 block mb-1 text-xs">
                            Đánh Giá Sau Công Bố (Surprise Analysis)
                          </span>
                          <p className="text-emerald-200 text-[11px]">{activeResearch.post_release_assessment}</p>
                        </div>
                      )}

                      {/* Section 5: Historical releases */}
                      {activeResearch.historical_releases?.length > 0 && (
                        <div className="bg-charcoal-850 p-3 rounded border border-charcoal-750">
                          <span className="font-bold text-aurum-400 block mb-1 text-xs flex items-center gap-1.5">
                            <History className="w-3.5 h-3.5" /> Lịch Sử Công Bố Gần Đây
                          </span>
                          <div className="overflow-x-auto">
                            <table className="w-full text-left text-[11px]">
                              <thead className="text-gray-400 border-b border-charcoal-700">
                                <tr>
                                  <th className="py-1">Ngày</th>
                                  <th>Thực tế</th>
                                  <th>Dự báo</th>
                                  <th>Trước đó</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-charcoal-800 font-mono">
                                {activeResearch.historical_releases.slice(0, 5).map((h: any, i: number) => (
                                  <tr key={i}>
                                    <td className="py-1 text-gray-300">{h.date}</td>
                                    <td className="font-bold text-aurum-400">{h.actual || '-'}</td>
                                    <td className="text-gray-400">{h.forecast || '-'}</td>
                                    <td className="text-gray-400">{h.previous || '-'}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}

                      {/* Limitations Disclaimer */}
                      <p className="text-[10px] text-gray-500 italic">
                        {activeResearch.limitations}
                      </p>
                    </div>

                    <div className="p-4 border-t border-charcoal-750 flex flex-wrap justify-between items-center gap-2 bg-charcoal-850/50">
                      <div className="flex gap-2">
                        {activeResearch.source_url && (
                          <a
                            href={activeResearch.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-3 py-1.5 bg-charcoal-750 hover:bg-charcoal-700 text-gray-200 rounded text-xs inline-flex items-center gap-1.5 border border-charcoal-650 transition"
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                            <span>Mở Nguồn Forex Factory</span>
                          </a>
                        )}
                        <button
                          onClick={() => handleRefreshNewsResearch(activeResearch.news_id)}
                          className="px-3 py-1.5 bg-charcoal-750 hover:bg-charcoal-700 text-aurum-400 rounded text-xs inline-flex items-center gap-1.5 border border-charcoal-650 transition"
                        >
                          <RefreshCw className="w-3.5 h-3.5" />
                          <span>Cập Nhật Lại Số Liệu</span>
                        </button>
                      </div>
                      <button
                        onClick={() => setResearchModalOpen(false)}
                        className="px-4 py-1.5 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition"
                      >
                        Đóng
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB: NHẬT KÝ & BÀI HỌC */}
          {activeTab === 'journal' && (
            <JournalTab
              onFocusChart={handleFocusTradeOnChart}
              showToast={showToast}
            />
          )}

          {/* TAB: CÀI ĐẶT TELEGRAM */}
          {activeTab === 'telegram' && (
            <TelegramTab
              showToast={showToast}
            />
          )}

          {/* TAB: THƯ VIỆN KIẾN THỨC */}
          {activeTab === 'education' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col md:flex-row gap-5 overflow-y-auto max-h-[750px]">
              {/* Sidebar Modules */}
              <div className="w-full md:w-1/3 flex flex-col gap-2 border-r border-charcoal-750 pr-4">
                <h3 className="text-xs font-bold text-aurum-400 uppercase tracking-wider mb-2">Chủ Đề Kiến Thức</h3>
                {educationList.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setSelectedEdu(item)}
                    className={`text-left p-3 rounded text-xs transition border ${
                      selectedEdu?.id === item.id
                        ? 'bg-aurum-500/10 border-aurum-500 text-aurum-400 font-semibold'
                        : 'bg-charcoal-850 border-charcoal-700 text-gray-300 hover:bg-charcoal-800'
                    }`}
                  >
                    <span className="text-[10px] text-gray-400 block uppercase font-mono">{item.category}</span>
                    <span>{item.title}</span>
                  </button>
                ))}
              </div>

              {/* Content Viewer */}
              <div className="flex-1 flex flex-col">
                {selectedEdu ? (
                  <div>
                    <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">
                      {selectedEdu.category}
                    </span>
                    <h2 className="text-base font-bold text-gray-100 mb-3">{selectedEdu.title}</h2>
                    <div className="text-xs text-gray-300 whitespace-pre-wrap leading-relaxed bg-charcoal-850 p-4 rounded-lg border border-charcoal-700">
                      {selectedEdu.content}
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-gray-500 italic">Chọn một chủ đề để đọc tài liệu.</div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right Side Panel: Live Trading & Risk Sidebar (Col 4) */}
        <div className="flex flex-col gap-3">
          {/* Active Open Position Card */}
          {activePosition ? (
            <div className="bg-charcoal-900 border-2 border-emerald-500/60 rounded-lg p-4 shadow-lg flex flex-col gap-3">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-2">
                <span className="text-xs font-bold text-emerald-400 flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
                  VỊ THẾ ĐANG MỞ (PAPER)
                </span>
                <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800">
                  {activePosition.position.direction}
                </span>
              </div>

              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-400">Entry Thực Tế:</span>
                  <span className="font-semibold text-gray-200">
                    ${activePosition.position.actual_entry || activePosition.position.planned_entry}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Cắt Lỗ (SL):</span>
                  <span className="text-rose-400 font-semibold">${activePosition.position.stop_loss}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Chốt Lời (TP):</span>
                  <span className="text-emerald-400 font-semibold">${activePosition.position.take_profit}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">R:R Khớp Lệnh (Gross / Net):</span>
                  <span className="text-aurum-400 font-bold">
                    1:{activePosition.position.gross_rr?.toFixed(2) || '---'} (Net 1:{activePosition.position.estimated_net_rr?.toFixed(2) || '---'})
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Đòn bẩy & Ký quỹ:</span>
                  <span className="font-mono text-gray-300">
                    {activePosition.position.leverage || leverage}x ({activePosition.position.margin_mode || marginMode})
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Thanh lý ước tính:</span>
                  <span className="text-rose-400 font-mono font-semibold">
                    {typeof activePosition.position.estimated_liquidation === 'number' && activePosition.position.estimated_liquidation > 0
                      ? `$${activePosition.position.estimated_liquidation.toFixed(2)} (Ước tính Isolated)`
                      : 'Chưa có ước tính hợp lệ'}
                  </span>
                </div>
                <div className="flex justify-between pt-1 border-t border-charcoal-750">
                  <span className="text-gray-400 font-medium">PnL Tạm Tính:</span>
                  <span
                    className={`font-bold text-sm ${
                      activePosition.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    {activePosition.unrealized_pnl >= 0 ? '+' : ''}${activePosition.unrealized_pnl}
                  </span>
                </div>
              </div>

              <button
                onClick={() => handleClosePosition(activePosition.position.id)}
                className="w-full py-1.5 bg-rose-600 hover:bg-rose-500 text-white rounded font-bold text-xs transition"
              >
                Đóng Vị Thế Ngay (Thị Trường)
              </button>
            </div>
          ) : (
            /* Selected Trade Intent / Candidate Card */
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-4 shadow-lg flex flex-col gap-3">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-2">
                <span className="text-xs font-bold text-aurum-400">
                  {selectedIntent?.source === 'WATCH_SETUP'
                    ? 'SETUP ĐANG CHỌN (WATCHBOARD)'
                    : selectedIntent?.source === 'DRAFT'
                    ? 'BẢN NHÁP R:R (TRÊN CHART)'
                    : 'TÍN HIỆU SMC TIẾP THEO'}
                </span>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-gray-400">{selectedIntent?.timeframe || timeframe}</span>
                  {selectedIntent && selectedIntent.source !== 'LIVE_CANDIDATE' && (
                    <button
                      onClick={handleFollowLatestSignal}
                      title="Quay lại tín hiệu phân tích SMC mới nhất"
                      className="text-[10px] px-1.5 py-0.5 rounded bg-charcoal-800 hover:bg-charcoal-750 text-aurum-400 border border-aurum-500/30 flex items-center gap-1 transition"
                    >
                      <RefreshCw className="w-2.5 h-2.5" />
                      Theo tín hiệu mới nhất
                    </button>
                  )}
                </div>
              </div>

              {selectedIntent ? (
                <ExpectedEntryPanel
                  selectedIntent={selectedIntent}
                  setups={upcomingData.setups}
                  activePosition={activePosition}
                  hasArmedOrder={hasArmedOrder}
                  leverage={leverage}
                  marginMode={marginMode}
                  onArmSetup={handleArmWatchSetup}
                  onCancelSetup={handleCancelWatchSetup}
                  onOpenMarket={handleOpenPaperTrade}
                />
              ) : (
                <div className="p-3 text-center text-xs text-gray-400 italic bg-charcoal-850 rounded border border-charcoal-750">
                  {analysis?.missing_conditions?.length > 0 ? (
                    <div className="text-left space-y-1">
                      <span className="text-gray-300 font-medium block not-italic">Đang Chờ Đủ Điều Kiện:</span>
                      {analysis.missing_conditions.map((mc: string, idx: number) => (
                        <p key={idx} className="text-[11px] text-amber-400/90">• {mc}</p>
                      ))}
                    </div>
                  ) : (
                    'Đang theo dõi phản ứng giá...'
                  )}
                </div>
              )}

            </div>
          )}

          {/* Upcoming Setups Quick Preview in Sidebar */}
          {upcomingData.setups.length > 0 && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-3.5 shadow-lg flex flex-col gap-2">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-1.5">
                <span className="text-xs font-bold text-gray-300 flex items-center gap-1.5">
                  <ListOrdered className="w-3.5 h-3.5 text-aurum-400" />
                  LỆNH DỰ KIẾN ({upcomingData.setups.length})
                </span>
                <button
                  onClick={() => setActiveTab('upcoming')}
                  className="text-[10px] text-aurum-400 hover:text-aurum-300 font-medium"
                >
                  Xem tất cả →
                </button>
              </div>

              <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
                {upcomingData.setups.slice(0, 3).map((st) => (
                  <div
                    key={st.id}
                    onClick={() => handleFocusSetupOnChart(st)}
                    className="p-2 rounded bg-charcoal-850 border border-charcoal-700 hover:border-aurum-500/50 cursor-pointer text-[11px] flex justify-between items-center transition"
                  >
                    <div>
                      <span
                        className={`font-bold mr-1.5 ${
                          st.direction === 'LONG' ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        {st.direction}
                      </span>
                      <span className="text-gray-300">${st.confirmed_entry || st.provisional_entry}</span>
                    </div>
                    <span
                      className={`px-1.5 py-0.2 rounded text-[9px] font-mono font-semibold ${
                        st.state === 'READY'
                          ? 'bg-emerald-500 text-charcoal-950'
                          : st.state === 'ARMED'
                          ? 'bg-amber-500 text-charcoal-950'
                          : 'bg-charcoal-800 text-gray-400'
                      }`}
                    >
                      {st.state}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Active Position Risk Monitoring Card */}
          {activePosition?.has_active_position && (
            <div className="bg-charcoal-900 border border-emerald-500/50 rounded-lg p-3.5 shadow-lg flex flex-col gap-2">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-2">
                <span className="text-xs font-bold text-emerald-400 flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                  GIÁM SÁT RỦI RO VỊ THẾ HIỆN TẠI
                </span>
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800">
                  {activePosition.position.direction}
                </span>
              </div>
              <div className="space-y-1.5 text-[11px]">
                <div className="p-2 rounded bg-charcoal-850 border border-charcoal-700 space-y-1">
                  <div className="flex justify-between">
                    <span className="text-gray-400">Net R:R Khớp Lệnh:</span>
                    <span className="text-emerald-400 font-bold">
                      1:{activePosition.position.estimated_net_rr?.toFixed(2) || '---'} (Gross 1:{activePosition.position.gross_rr?.toFixed(2) || '---'})
                    </span>
                  </div>
                  <span className="text-[10px] text-emerald-300/80 block">
                    ✓ Đạt ngưỡng tối thiểu Net R:R 1:2.0 tại thời điểm khớp lệnh.
                  </span>
                </div>
                <div className="p-2 rounded bg-charcoal-850 border border-charcoal-700 space-y-1">
                  <div className="flex justify-between">
                    <span className="text-gray-400">Giám sát TP / SL:</span>
                    <span className="text-gray-200 font-mono font-semibold">
                      TP ${activePosition.position.take_profit} / SL ${activePosition.position.stop_loss}
                    </span>
                  </div>
                  <span className="text-[10px] text-gray-400 block">
                    ExitMonitor đang theo dõi realtime; không bị ảnh hưởng bởi bộ lọc tín hiệu mới.
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Hard Filters Live Checklist */}
          <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-4 shadow-lg flex flex-col gap-2">
            <div className="border-b border-charcoal-750 pb-2">
              <span className="text-xs font-bold text-gray-300 block">
                BỘ LỌC TÍN HIỆU MỚI (SMC {analysis?.timeframe || '15M'})
              </span>
              <span className="text-[10px] text-gray-400 block mt-0.5">
                Đánh giá điều kiện cho cơ hội mới (Độc lập với vị thế đang chạy)
              </span>
            </div>
            <div className="space-y-2 text-xs">
              {analysis?.checklist?.map((chk: any) => (
                <div key={chk.id} className="p-2 rounded bg-charcoal-850 border border-charcoal-700 flex items-start gap-2">
                  {chk.status === 'PASS' ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                  ) : chk.status === 'FAIL' ? (
                    <XCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                  ) : (
                    <Info className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  )}
                  <div>
                    <span className="font-semibold text-gray-200 block text-[11px]">{chk.label}</span>
                    <span className="text-[10px] text-gray-400">
                      {chk.id === 'MIN_NET_RR' && !analysis?.active_signal
                        ? 'Chưa có setup mới hình thành trên nến hiện tại'
                        : chk.detail}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Section 10: "Vì sao chưa vào lệnh?" Explanation Card */}
          <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-4 shadow-lg text-xs space-y-2">
            <span className="font-bold text-amber-400 block border-b border-charcoal-750 pb-2 flex items-center gap-1.5">
              <Info className="w-4 h-4 text-amber-400" />
              VÌ SAO CHƯA VÀO LỆNH?
            </span>

            {activeOverlay?.state === 'draft' ? (
              <div className="space-y-1.5 text-gray-300">
                <div className="p-2 rounded bg-amber-950/40 border border-amber-800/60 text-amber-300 font-semibold text-[11px]">
                  MÃ: DRAFT_NOT_SUBMITTED
                </div>
                <p className="text-[11px] text-gray-400 leading-relaxed">
                  Đây là <strong className="text-amber-300">BẢN NHÁP</strong>; setup hiện chưa đủ điều kiện (Net R:R và sweep). Giá chạm Entry của bản nháp sẽ không bao giờ tự động tạo lệnh hay tăng bộ đếm.
                </p>
                {activeOverlay.estimatedNetRR < 2.0 && (
                  <p className="text-[11px] text-rose-400">
                    • Net R:R hiện tại (1:{activeOverlay.estimatedNetRR.toFixed(2)}) chưa đạt ngưỡng 1:2.0 (<code>NET_RR_TOO_LOW</code>).
                  </p>
                )}
                {analysis?.missing_conditions?.length > 0 && (
                  <p className="text-[11px] text-amber-400/90">
                    • Điều kiện chiến lược SMC: {analysis.missing_conditions.join('; ')}
                  </p>
                )}
              </div>
            ) : activePosition ? (
              <div className="p-2 rounded bg-emerald-950/40 border border-emerald-800/60 text-emerald-300 font-semibold text-[11px]">
                Đang có 1 vị thế mở ({activePosition.position.direction} tại ${activePosition.position.actual_entry || activePosition.position.planned_entry}). Hệ thống chỉ duy trì tối đa 1 vị thế cùng lúc.
              </div>
            ) : analysis?.engine_state === 'blocked_news' ? (
              <div className="p-2 rounded bg-rose-950/40 border border-rose-800/60 text-rose-300 font-semibold text-[11px]">
                MÃ: NEWS_BLACKOUT — Đang trong khung giờ bảo vệ tin tức vĩ mô High Impact.
              </div>
            ) : analysis?.engine_state === 'blocked_risk' ? (
              <div className="p-2 rounded bg-rose-950/40 border border-rose-800/60 text-rose-300 font-semibold text-[11px]">
                MÃ: RISK_BUDGET_EXCEEDED — Đã chạm giới hạn quản trị rủi ro ngày (3 lệnh hoặc 2 lỗ liên tiếp).
              </div>
            ) : !autoPaperActive ? (
              <div className="p-2 rounded bg-amber-950/40 border border-amber-800/60 text-amber-300 font-semibold text-[11px]">
                MÃ: AUTO_PAUSED — Chế độ tự động vào lệnh đang tạm dừng.
              </div>
            ) : analysis?.missing_conditions?.length > 0 ? (
              <div className="space-y-1 text-[11px]">
                <div className="p-2 rounded bg-charcoal-850 border border-charcoal-700 text-amber-400 font-semibold">
                  MÃ: {analysis.reason_code || 'WAITING_CONFIRMATION'}
                </div>
                <div className="text-gray-400 space-y-0.5">
                  <span className="text-gray-300 font-medium">Chi tiết thiếu điều kiện:</span>
                  {analysis.missing_conditions.map((mc: string, idx: number) => (
                    <p key={idx} className="text-amber-400/90">• {mc}</p>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-gray-400 italic text-[11px]">
                Hệ thống đang sẵn sàng và theo dõi cấu trúc nến đóng để kích hoạt.
              </p>
            )}
          </div>

          {/* Capital & Today's Limits with Separated Counters */}
          <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-4 shadow-lg text-xs space-y-2">
            <span className="font-bold text-aurum-400 block border-b border-charcoal-750 pb-2">
              HẠN MỨC NGÀY (UTC+7)
            </span>
            <div className="flex justify-between">
              <span className="text-gray-400">Vốn Ban Đầu:</span>
              <span className="font-mono">$1,000.00</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Vốn Khả Dụng (Equity):</span>
              <span className="font-mono text-emerald-400 font-bold">
                ${accountStatus?.current_equity?.toFixed(2) || '1,000.00'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Đòn bẩy / Ký quỹ:</span>
              <span className="font-mono text-gray-200">
                {leverage}x · {marginMode}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Vị thế đang mở:</span>
              <span className="font-mono text-emerald-400 font-semibold">{activePosition ? '1 / 1' : '0 / 1'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Lệnh đã vào hôm nay:</span>
              <span className="font-mono">
                {accountStatus?.today_fills_count ?? accountStatus?.fills_count ?? 0} / 3 Lệnh
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Lệnh chờ (Armed):</span>
              <span className="font-mono text-amber-400">{accountStatus?.armed_orders_count ?? 0} Lệnh</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Lỗ Liên Tiếp:</span>
              <span className="font-mono">{accountStatus?.consecutive_losses || 0} / 2 (Dừng)</span>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
