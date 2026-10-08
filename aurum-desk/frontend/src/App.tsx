import { useState, useEffect, useCallback, useRef } from 'react';
import { ChartComponent } from './ChartComponent';
import { api } from './api/client';
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
  Send,
  Bell,
  Sliders,
  Eye,
  RefreshCw,
  Smartphone,
  Copy,
  ListOrdered,
  Compass,
  FlaskConical
} from 'lucide-react';

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
  snapshotAt: number;
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
  const [leverage, setLeverage] = useState<number>(5);
  const [marginMode, setMarginMode] = useState<'ISOLATED' | 'CROSS'>('ISOLATED');
  const [riskPct, setRiskPct] = useState<number>(0.25);
  const [settingsSavedMsg, setSettingsSavedMsg] = useState<string | null>(null);

  // Upcoming Setups & Scenarios
  const [upcomingData, setUpcomingData] = useState<{
    setups: any[];
    scenarios: any;
    server_time?: number;
  }>({ setups: [], scenarios: null });
  const [upcomingFilter, setUpcomingFilter] = useState<string>('ALL');

  // Telegram Settings & Test
  const [telegramConfig, setTelegramConfig] = useState<any>({
    enabled: false,
    bot_token: '',
    chat_id: '',
    quiet_hours_enabled: false,
    quiet_hours_start: '23:00',
    quiet_hours_end: '06:00',
    bypass_critical_quiet_hours: true,
    near_entry_mode: 'ATR',
    near_entry_atr_mult: 0.5,
    near_entry_price_dist: 2.0,
    near_entry_cooldown_min: 30,
    subscribed_events: ['READY', 'NEAR_ENTRY', 'ARMED', 'FILLED', 'TP_HIT', 'SL_HIT', 'MANUAL_CLOSED', 'LIQUIDATED', 'REJECTED', 'INVALIDATED', 'FEED_DOWN'],
  });
  const [showBotToken, setShowBotToken] = useState<boolean>(false);
  const [telegramTestStatus, setTelegramTestStatus] = useState<{ loading: boolean; msg: string | null; ok: boolean | null }>({
    loading: false,
    msg: null,
    ok: null,
  });
  const [outboxHistory, setOutboxHistory] = useState<any[]>([]);

  // Additional Panels Data
  const [reports, setReports] = useState<any[]>([]);
  const [newsData, setNewsData] = useState<any>(null);
  const [journalTrades, setJournalTrades] = useState<any[]>([]);
  const [lessons, setLessons] = useState<any[]>([]);
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
    try {
      const [healthData, accData, posData, autoData] = await Promise.all([
        api.getHealth('XAUUSDT', timeframe),
        api.getAccountStatus(),
        api.getActivePosition(),
        api.getAutoState(),
      ]);
      setHealth(healthData);
      setAccountStatus(accData);
      if (accData?.leverage) setLeverage(accData.leverage);
      if (accData?.margin_mode) setMarginMode(accData.margin_mode.toUpperCase());

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

  // 4. WebSocket Domain Events Connection
  useEffect(() => {
    let ws: WebSocket | null = null;
    let pingInterval: any = null;
    let reconnectTimeout: any = null;

    const connectWs = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws`;

      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          pingInterval = setInterval(() => {
            if (ws?.readyState === WebSocket.OPEN) {
              ws.send('ping');
            }
          }, 25000);
        };

        ws.onmessage = (event) => {
          if (event.data === 'pong') return;
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'DOMAIN_EVENT') {
              const evt = data.event;
              const type = evt?.event_type;
              const payload = evt?.payload || {};

              if (type === 'trade.opened') {
                showToast('Lệnh Đã Khớp (PAPER_OPEN)', `${payload.direction} XAUUSDT tại $${payload.entry_price || payload.actual_entry}`, 'success');
              } else if (type === 'trade.closed') {
                showToast('Vị Thế Đã Đóng', `PnL: $${payload.net_pnl} (${payload.exit_reason})`, payload.net_pnl >= 0 ? 'success' : 'warn');
              } else if (type === 'trade.liquidated') {
                showToast('THANH LÝ (LIQUIDATED)', `Vị thế đã bị thanh lý tại giá Mark $${payload.exit_price}`, 'warn');
              } else if (type === 'order.armed') {
                showToast('Lệnh Đã Armed', `Setup ${payload.setup_id} đã sẵn sàng chờ kích hoạt`, 'info');
              } else if (type === 'setup.ready') {
                showToast('Setup READY', `Setup ${payload.setup_id} đã hoàn tất điều kiện SMC`, 'info');
              }

              refreshAccountAndHealth();
              refreshAnalysis();
              refreshUpcoming();
            }
          } catch (e) {
            // Non-JSON or pong
          }
        };

        ws.onclose = () => {
          clearInterval(pingInterval);
          reconnectTimeout = setTimeout(connectWs, 5000);
        };

        ws.onerror = () => {
          ws?.close();
        };
      } catch (err) {
        reconnectTimeout = setTimeout(connectWs, 5000);
      }
    };

    connectWs();

    return () => {
      clearInterval(pingInterval);
      clearTimeout(reconnectTimeout);
      if (ws) ws.close();
    };
  }, [refreshAccountAndHealth, refreshAnalysis, refreshUpcoming, showToast]);

  // Periodic polling
  useEffect(() => {
    refreshAccountAndHealth();
    refreshAnalysis();
    refreshUpcoming();
    const interval = setInterval(() => {
      refreshAccountAndHealth();
      refreshAnalysis();
      refreshUpcoming();
    }, 10000);
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
    } else if (activeTab === 'journal') {
      api.getJournal().then(setJournalTrades).catch(console.error);
      api.getLessons().then(setLessons).catch(console.error);
    } else if (activeTab === 'telegram') {
      api.getTelegramConfig().then(setTelegramConfig).catch(console.error);
      api.getNotificationHistory().then(setOutboxHistory).catch(console.error);
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
        const detail = err.response?.data?.detail;
        const msg = typeof detail === 'object' ? detail.message : detail;
        alert(`Lệnh bị từ chối do xung đột (409): ${msg}`);
      } else {
        alert(err.response?.data?.detail || err.message || 'Không thể mở lệnh paper trade');
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
      alert(err.response?.data?.detail || err.message || 'Lỗi khi đóng vị thế');
    }
  };

  const handleArmWatchSetup = async (setupId: string, direction?: string, instanceId?: string, revision?: number) => {
    const targetDirection = (direction || selectedIntent?.direction || 'LONG') as 'LONG' | 'SHORT';
    try {
      const res = await api.armSetup(setupId, {
        setup_id: setupId,
        setup_instance_id: instanceId || selectedIntent?.setup_instance_id,
        expected_revision: revision || selectedIntent?.revision,
        expected_direction: targetDirection,
        idempotency_key: `arm-${setupId}-${Date.now()}`
      });
      showToast('Lệnh Đã Armed', res.message || 'Đã arm setup thành công', 'success');
      await refreshUpcoming();
      await refreshAccountAndHealth();
    } catch (err: any) {
      if (err.response?.status === 409) {
        const detail = err.response?.data?.detail;
        const msg = typeof detail === 'object' ? detail.message : detail;
        alert(`Setup đã thay đổi (409): ${msg || 'Setup bạn chọn đã thay đổi trạng thái hoặc hướng; hãy xem lại.'}`);
      } else {
        alert(err.response?.data?.detail || err.message || 'Lỗi khi arm setup');
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
      alert(err.response?.data?.detail || err.message || 'Lỗi khi hủy setup');
    }
  };

  const handleFocusSetupOnChart = (setup: any) => {
    setTimeframe(setup.timeframe || '15M');
    setActiveTab('chart');
    const overlay: RiskRewardData = {
      id: setup.id,
      direction: setup.direction,
      state: setup.state === 'READY' || setup.state === 'ARMED' ? 'candidate' : 'draft',
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
      source: 'WATCH_SETUP',
      setup_id: setup.id,
      setup_instance_id: setup.setup_instance_id,
      revision: setup.version || 1,
      symbol: 'XAUUSDT',
      timeframe: setup.timeframe || '15M',
      direction: setup.direction,
      plannedEntry: setup.confirmed_entry || setup.provisional_entry,
      stopLoss: setup.confirmed_sl || setup.provisional_sl,
      takeProfit: setup.confirmed_tp || setup.provisional_tp,
      orderType: setup.state === 'READY' ? 'MARKET' : 'LIMIT',
      quantity: setup.quantity || 0.05,
      initialRiskUsdt: setup.risk_usdt || 2.5,
      grossRR: setup.gross_rr || 2.0,
      estimatedNetRR: setup.net_rr || 2.0,
      leverage: setup.leverage || leverage,
      marginMode: setup.margin_mode || marginMode,
      estimatedLiquidation: setup.estimated_liquidation,
      status: setup.state,
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
    try {
      await api.updateAccountSettings({
        leverage,
        margin_mode: marginMode,
      });
      setSettingsSavedMsg('Đã lưu cấu hình đòn bẩy và ký quỹ thành công!');
      setTimeout(() => setSettingsSavedMsg(null), 4000);
      await refreshAccountAndHealth();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message || 'Lỗi khi lưu cài đặt tài khoản');
    }
  };

  const handleSaveTelegramConfig = async () => {
    try {
      await api.updateTelegramConfig(telegramConfig);
      showToast('Telegram', 'Đã lưu cấu hình Telegram thành công', 'success');
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message || 'Lỗi khi lưu cấu hình Telegram');
    }
  };

  const handleTestTelegram = async () => {
    setTelegramTestStatus({ loading: true, msg: 'Đang gửi tin nhắn thử nghiệm tới Telegram...', ok: null });
    try {
      const res = await api.testTelegram({
        bot_token: telegramConfig.bot_token || undefined,
        chat_id: telegramConfig.chat_id,
      });
      setTelegramTestStatus({
        loading: false,
        msg: `Thành công: Đã gửi tin nhắn test tới Chat ID ${telegramConfig.chat_id} (Message ID: ${res.message_id || 'OK'}). Hãy kiểm tra ứng dụng Telegram!`,
        ok: true,
      });
    } catch (err: any) {
      const respData = err.response?.data;
      let errMsg = '';
      if (typeof respData === 'string') {
        errMsg = respData;
      } else if (respData?.detail) {
        errMsg = typeof respData.detail === 'string' ? respData.detail : JSON.stringify(respData.detail);
      } else if (respData?.message) {
        errMsg = respData.message;
      } else if (err.message) {
        errMsg = err.message;
      } else {
        errMsg = 'Lỗi kết nối không xác định tới Telegram API';
      }

      setTelegramTestStatus({
        loading: false,
        msg: `Thất bại: ${errMsg}`,
        ok: false,
      });
    }
  };

  const handleRetryOutboxItem = async (itemId: number) => {
    try {
      await api.retryOutboxItem(itemId);
      showToast('Hàng Đợi', `Đã đặt lại tin nhắn #${itemId} về hàng đợi gửi`, 'success');
      const updated = await api.getNotificationHistory();
      setOutboxHistory(updated);
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message || 'Lỗi khi thử lại tin nhắn');
    }
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

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportStatus('Đang nhập dữ liệu...');
    try {
      const res = await api.importNews(file);
      setImportStatus(`Đã nhập thành công ${res.imported_count} sự kiện từ ${res.filename}`);
      api.getNews().then(setNewsData);
    } catch (err: any) {
      setImportStatus(`Lỗi nhập file: ${err.response?.data?.detail || err.message}`);
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
                                ${s.estimated_liquidation?.toFixed(2) || '---'}
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
                            ) : (
                              <span className="text-emerald-400 font-semibold text-[10px]">
                                Đủ toàn bộ điều kiện! Sẵn sàng Arm.
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
                            {['READY', 'WAITING_PRICE', 'WAITING_RETRACE'].includes(s.state) && (
                              <button
                                onClick={() => handleArmWatchSetup(s.id, s.direction, s.setup_instance_id, s.version)}
                                className="flex items-center gap-1.5 px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded text-xs transition shadow-sm"
                              >
                                <PlayCircle className="w-3.5 h-3.5" />
                                <span>Arm {s.direction} — PAPER</span>
                              </button>
                            )}

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
              <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-700 flex flex-col gap-4 text-xs">
                <h3 className="font-bold text-gray-200 uppercase tracking-wider flex items-center gap-1.5">
                  <Sliders className="w-3.5 h-3.5 text-aurum-400" />
                  Cấu Hình Đòn Bẩy (Leverage) & Chế Độ Ký Quỹ
                </h3>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Leverage slider */}
                  <div className="space-y-2">
                    <div className="flex justify-between items-center">
                      <span className="text-gray-300">Đòn bẩy mặc định:</span>
                      <span className="text-sm font-bold text-aurum-400 font-mono">{leverage}x</span>
                    </div>
                    <input
                      type="range"
                      min={1}
                      max={50}
                      value={leverage}
                      onChange={(e) => setLeverage(Number(e.target.value))}
                      className="w-full accent-aurum-500 cursor-pointer"
                    />
                    <div className="flex justify-between text-[10px] text-gray-400">
                      <span>1x (Spot-like)</span>
                      <span>5x (Default)</span>
                      <span>20x</span>
                      <span>50x (Max Cap)</span>
                    </div>
                    <p className="text-[10px] text-gray-400">
                      * Thay đổi đòn bẩy KHÔNG thay đổi rủi ro tính bằng USD ($2.50) hay kích thước vị thế, mà chỉ thay đổi số tiền ký quỹ ban đầu yêu cầu và khoảng cách giá thanh lý (Liquidation Price).
                    </p>
                  </div>

                  {/* Margin Mode & Risk Pct */}
                  <div className="space-y-3">
                    <div>
                      <span className="text-gray-300 block mb-1">Chế độ ký quỹ:</span>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => setMarginMode('ISOLATED')}
                          className={`flex-1 py-1.5 rounded font-semibold text-xs border transition ${
                            marginMode === 'ISOLATED'
                              ? 'bg-aurum-500 text-charcoal-950 border-aurum-400 font-bold'
                              : 'bg-charcoal-900 text-gray-400 border-charcoal-700'
                          }`}
                        >
                          ISOLATED (Cô lập)
                        </button>
                        <button
                          type="button"
                          onClick={() => setMarginMode('CROSS')}
                          className={`flex-1 py-1.5 rounded font-semibold text-xs border transition ${
                            marginMode === 'CROSS'
                              ? 'bg-aurum-500 text-charcoal-950 border-aurum-400 font-bold'
                              : 'bg-charcoal-900 text-gray-400 border-charcoal-700'
                          }`}
                        >
                          CROSS (Toàn tài khoản)
                        </button>
                      </div>
                    </div>

                    <div>
                      <span className="text-gray-300 block mb-1">Rủi ro mỗi lệnh:</span>
                      <div className="flex gap-2">
                        {[0.25, 0.5].map((pct) => (
                          <button
                            key={pct}
                            type="button"
                            onClick={() => setRiskPct(pct)}
                            className={`flex-1 py-1.5 rounded font-semibold text-xs border transition ${
                              riskPct === pct
                                ? 'bg-aurum-500 text-charcoal-950 border-aurum-400 font-bold'
                                : 'bg-charcoal-900 text-gray-400 border-charcoal-700'
                            }`}
                          >
                            {pct}% Vốn (${((accountStatus?.current_equity ?? 1000) * pct / 100).toFixed(2)})
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                {settingsSavedMsg && (
                  <div className="p-2.5 rounded bg-emerald-950/80 border border-emerald-700 text-emerald-300 text-xs">
                    {settingsSavedMsg}
                  </div>
                )}

                <div className="flex justify-end">
                  <button
                    onClick={handleSaveRiskSettings}
                    className="px-4 py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition"
                  >
                    Lưu Cài Đặt Đòn Bẩy & Ký Quỹ
                  </button>
                </div>
              </div>

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
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400">Lịch Tin Tức Vĩ Mô & Vùng Blackout</h2>
                  <p className="text-xs text-gray-400">Hỗ trợ nhập lịch Forex Factory (.json) hoặc CSV miễn phí</p>
                </div>
                <label className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-semibold cursor-pointer transition">
                  <Upload className="w-3.5 h-3.5" />
                  <span>Nhập Lịch (JSON / CSV)</span>
                  <input type="file" accept=".json,.csv" onChange={handleFileUpload} className="hidden" />
                </label>
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
                      <th className="py-2">Thời gian (UTC)</th>
                      <th>Sự kiện</th>
                      <th>Quốc gia</th>
                      <th>Tác động</th>
                      <th>Dự báo</th>
                      <th>Trước đó</th>
                      <th>Thực tế</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-charcoal-800">
                    {newsData?.events?.map((ev: any) => (
                      <tr key={ev.id} className="hover:bg-charcoal-850">
                        <td className="py-2 text-gray-400">{new Date(ev.scheduled_at).toLocaleTimeString('vi-VN')}</td>
                        <td className="font-medium text-gray-200">{ev.title}</td>
                        <td className="text-gray-400">{ev.country}</td>
                        <td>
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                              ev.impact === 'High'
                                ? 'bg-rose-900/60 text-rose-300'
                                : ev.impact === 'Medium'
                                ? 'bg-amber-900/60 text-amber-300'
                                : 'bg-gray-800 text-gray-400'
                            }`}
                          >
                            {ev.impact}
                          </span>
                        </td>
                        <td className="text-gray-400">{ev.forecast || '-'}</td>
                        <td className="text-gray-400">{ev.previous || '-'}</td>
                        <td className="font-bold text-aurum-400">{ev.actual || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB: NHẬT KÝ & BÀI HỌC */}
          {activeTab === 'journal' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-4 overflow-y-auto max-h-[750px]">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400">Nhật Ký Lệnh & Bài Học Đã Rút Ra</h2>
                  <p className="text-xs text-gray-400">Lưu trữ snapshot trước lệnh và bằng chứng sau khi đóng</p>
                </div>
              </div>

              <div className="space-y-3">
                {journalTrades.map((t) => (
                  <div
                    key={t.id}
                    className="bg-charcoal-850 p-3.5 rounded-lg border border-charcoal-700 flex flex-wrap justify-between items-center gap-3 text-xs"
                  >
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span
                          className={`px-2 py-0.5 rounded font-bold ${
                            t.direction === 'LONG'
                              ? 'bg-emerald-900/60 text-emerald-300'
                              : 'bg-rose-900/60 text-rose-300'
                          }`}
                        >
                          {t.direction}
                        </span>
                        <span className="font-semibold text-gray-200">XAUUSDT ({t.timeframe})</span>
                        <span className="text-gray-400">· {new Date(t.created_at).toLocaleDateString('vi-VN')}</span>
                      </div>
                      <div className="text-gray-400 text-[11px] flex gap-3">
                        <span>Entry: ${t.actual_entry || t.planned_entry}</span>
                        <span>SL: ${t.stop_loss}</span>
                        <span>TP: ${t.take_profit}</span>
                        <span>Gross R:R: 1:{t.gross_rr}</span>
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      <div className="text-right">
                        <span
                          className={`text-sm font-bold block ${
                            t.realized_pnl_net >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {t.realized_pnl_net !== null ? `${t.realized_pnl_net > 0 ? '+' : ''}$${t.realized_pnl_net}` : 'Đang chạy'}
                        </span>
                        {t.realized_r !== null && (
                          <span className="text-[11px] text-gray-400">
                            ({t.realized_r > 0 ? '+' : ''}{t.realized_r}R)
                          </span>
                        )}
                      </div>

                      <button
                        onClick={() => handleFocusTradeOnChart(t)}
                        className="flex items-center gap-1 px-3 py-1.5 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded transition"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                        <span>Xem Trên Chart</span>
                      </button>
                    </div>
                  </div>
                ))}
                {journalTrades.length === 0 && (
                  <p className="text-xs text-gray-500 italic text-center py-6">Chưa có giao dịch paper nào được ghi nhận.</p>
                )}
              </div>

              {/* Lessons Learned Memory Section */}
              <div className="mt-4 pt-4 border-t border-charcoal-750">
                <h3 className="text-xs font-bold text-aurum-400 uppercase tracking-wider mb-2">
                  Bộ Nhớ Bài Học & Quy Tắc Đã Truy Xuất (Lesson Memory)
                </h3>
                <div className="space-y-2">
                  {lessons.map((ls) => (
                    <div key={ls.id} className="p-3 bg-charcoal-850 rounded border border-charcoal-700 text-xs">
                      <div className="flex justify-between items-center mb-1">
                        <span className="font-bold text-gray-200">{ls.title}</span>
                        <span className="text-[10px] px-2 py-0.5 rounded bg-charcoal-900 text-indigo-300 border border-charcoal-700 font-mono">
                          {ls.category}
                        </span>
                      </div>
                      <p className="text-gray-400 text-[11px] mb-1">
                        <span className="text-gray-300 font-medium">Nhận xét:</span> {ls.reflection}
                      </p>
                      <p className="text-aurum-400 text-[11px]">
                        <span className="text-gray-300 font-medium">Hành động khắc phục:</span> {ls.action_rule}
                      </p>
                    </div>
                  ))}
                  {lessons.length === 0 && (
                    <p className="text-xs text-gray-500 italic">Chưa có bài học nào được ghi nhận.</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB: CÀI ĐẶT TELEGRAM */}
          {activeTab === 'telegram' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-5 overflow-y-auto max-h-[750px]">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400 flex items-center gap-2">
                    <Smartphone className="w-4 h-4 text-aurum-400" />
                    Cấu Hình Thông Báo Điện Thoại Qua Telegram
                  </h2>
                  <p className="text-xs text-gray-400">
                    Nhận cảnh báo trực tiếp về điện thoại khi có setup NEAR_ENTRY, READY, lệnh ARMED, hoặc lệnh khớp/đóng mà không cần mở trình duyệt
                  </p>
                </div>
              </div>

              {/* Bot Token & Chat ID Configuration Card */}
              <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 flex flex-col gap-4 text-xs">
                {/* Enable toggle */}
                <div className="flex justify-between items-center border-b border-charcoal-700 pb-3">
                  <div>
                    <span className="font-bold text-gray-200 block text-sm">Bật Thông Báo Telegram</span>
                    <span className="text-[11px] text-gray-400">
                      Gửi tin qua Outbox nền với cơ chế retry và hàng đợi bền vững
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setTelegramConfig({ ...telegramConfig, enabled: !telegramConfig.enabled })}
                    className={`px-4 py-1.5 rounded-full font-bold text-xs transition ${
                      telegramConfig.enabled
                        ? 'bg-emerald-500 text-charcoal-950'
                        : 'bg-charcoal-800 text-gray-400 border border-charcoal-700'
                    }`}
                  >
                    {telegramConfig.enabled ? 'ĐÃ BẬT' : 'ĐANG TẮT'}
                  </button>
                </div>

                {/* Bot Token Input */}
                <div className="space-y-1.5">
                  <div className="flex justify-between items-center">
                    <label className="font-semibold text-gray-300">Bot Token (Từ @BotFather):</label>
                    <button
                      type="button"
                      onClick={() => setShowBotToken(!showBotToken)}
                      className="text-[11px] text-indigo-400 hover:text-indigo-300"
                    >
                      {showBotToken ? 'Ẩn Token' : 'Hiện Token'}
                    </button>
                  </div>
                  <input
                    type={showBotToken ? 'text' : 'password'}
                    value={telegramConfig.bot_token || ''}
                    onChange={(e) => setTelegramConfig({ ...telegramConfig, bot_token: e.target.value })}
                    placeholder="VD: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz..."
                    className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-2 text-xs font-mono text-gray-200 focus:outline-none focus:border-aurum-500"
                  />
                  <p className="text-[10px] text-gray-500">
                    * Token được lưu trữ an toàn ở database máy local, không bao giờ được log hoặc nhúng vào bundle frontend.
                  </p>
                </div>

                {/* Chat ID Input */}
                <div className="space-y-1.5">
                  <label className="font-semibold text-gray-300">Chat ID Của Bạn:</label>
                  <input
                    type="text"
                    value={telegramConfig.chat_id || ''}
                    onChange={(e) => setTelegramConfig({ ...telegramConfig, chat_id: e.target.value })}
                    placeholder="VD: 6919390280"
                    className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-2 text-xs font-mono text-gray-200 focus:outline-none focus:border-aurum-500"
                  />
                </div>

                {/* Subscribed Events Toggle Pills */}
                <div className="space-y-2 pt-2 border-t border-charcoal-750">
                  <label className="font-semibold text-gray-300 block">Đăng Ký Loại Sự Kiện Nhận Thông Báo:</label>
                  <div className="flex flex-wrap gap-2">
                    {[
                      { id: 'NEAR_ENTRY', label: 'Sắp Tiếp Cận Entry', desc: 'Cảnh báo khi giá tiệm cận vùng Entry' },
                      { id: 'READY', label: 'Tín Hiệu Sẵn Sàng (READY)', desc: 'Setup đủ điều kiện SMC' },
                      { id: 'ARMED', label: 'Đã Arm Lệnh Chờ', desc: 'Lệnh đã được kích hoạt chờ khớp' },
                      { id: 'FILLED', label: 'Đã Khớp Lệnh (FILLED)', desc: 'Vị thế chính thức được mở' },
                      { id: 'TP_HIT', label: 'Chốt Lời (TP)', desc: 'Vị thế đạt lợi nhuận mục tiêu' },
                      { id: 'SL_HIT', label: 'Cắt Lỗ (SL)', desc: 'Vị thế chạm mức dừng lỗ' },
                      { id: 'MANUAL_CLOSED', label: 'Đóng Thủ Công', desc: 'Người dùng chủ động đóng vị thế' },
                      { id: 'LIQUIDATED', label: 'Thanh Lý Vị Thế', desc: 'Cảnh báo chạm giá thanh lý Isolated' },
                      { id: 'REJECTED', label: 'Lệnh Bị Từ Chối', desc: 'Vi phạm Execution Guards' },
                      { id: 'INVALIDATED', label: 'Hủy / Hết Hạn', desc: 'Cấu trúc setup bị phá vỡ' },
                      { id: 'FEED_DOWN', label: 'Cảnh Báo Nguồn Nến', desc: 'Mất kết nối hoặc nến bị trễ' },
                    ].map((item) => {
                      const isSubscribed = (telegramConfig.subscribed_events || []).includes(item.id);
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => {
                            const current = telegramConfig.subscribed_events || [];
                            const next = isSubscribed
                              ? current.filter((x: string) => x !== item.id)
                              : [...current, item.id];
                            setTelegramConfig({ ...telegramConfig, subscribed_events: next });
                          }}
                          className={`px-2.5 py-1 rounded text-[11px] font-semibold transition border ${
                            isSubscribed
                              ? 'bg-aurum-500/20 border-aurum-400 text-aurum-300'
                              : 'bg-charcoal-900 border-charcoal-700 text-gray-500 hover:text-gray-400'
                          }`}
                          title={item.desc}
                        >
                          {isSubscribed ? '✓ ' : '+ '}
                          {item.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Proximity Evaluator Settings */}
                <div className="space-y-2 pt-2 border-t border-charcoal-750">
                  <label className="font-semibold text-aurum-400 block">Cấu Hình Cảnh Báo Gần Vùng Entry (Proximity):</label>
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Chế Độ Đo:</label>
                      <select
                        value={telegramConfig.near_entry_mode || 'ATR'}
                        onChange={(e) => setTelegramConfig({ ...telegramConfig, near_entry_mode: e.target.value })}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
                      >
                        <option value="ATR">Theo Hệ Số ATR (Biến Động)</option>
                        <option value="PRICE_DISTANCE">Khoảng Cách Giá Cố Định (USDT)</option>
                      </select>
                    </div>

                    {telegramConfig.near_entry_mode === 'PRICE_DISTANCE' ? (
                      <div>
                        <label className="text-[11px] text-gray-400 block mb-1">Khoảng Cách Giá (USDT):</label>
                        <input
                          type="number"
                          step="0.5"
                          min="0.5"
                          max="20"
                          value={telegramConfig.near_entry_price_dist ?? 2.0}
                          onChange={(e) => setTelegramConfig({ ...telegramConfig, near_entry_price_dist: Number(e.target.value) })}
                          className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
                        />
                      </div>
                    ) : (
                      <div>
                        <label className="text-[11px] text-gray-400 block mb-1">Hệ Số ATR (× ATR 15M):</label>
                        <input
                          type="number"
                          step="0.1"
                          min="0.1"
                          max="3.0"
                          value={telegramConfig.near_entry_atr_mult ?? 0.5}
                          onChange={(e) => setTelegramConfig({ ...telegramConfig, near_entry_atr_mult: Number(e.target.value) })}
                          className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
                        />
                      </div>
                    )}

                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Cooldown Giữa 2 Lần Báo (Phút):</label>
                      <input
                        type="number"
                        min="5"
                        max="180"
                        value={telegramConfig.near_entry_cooldown_min ?? 30}
                        onChange={(e) => setTelegramConfig({ ...telegramConfig, near_entry_cooldown_min: Number(e.target.value) })}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Nguyên Tắc Chống Spam:</label>
                      <div className="text-[11px] text-gray-300 pt-1">
                        Hysteresis 1.5x & 1 alert / instance
                      </div>
                    </div>
                  </div>
                </div>

                {/* Quiet Hours & Critical Bypass */}
                <div className="space-y-2 pt-2 border-t border-charcoal-750">
                  <div className="flex justify-between items-center">
                    <label className="font-semibold text-gray-300">Khung Giờ Yên Lặng (Quiet Hours):</label>
                    <label className="flex items-center gap-1.5 text-[11px] text-gray-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={telegramConfig.quiet_hours_enabled || false}
                        onChange={(e) => setTelegramConfig({ ...telegramConfig, quiet_hours_enabled: e.target.checked })}
                        className="rounded border-charcoal-700 text-aurum-500 focus:ring-0"
                      />
                      <span>Kích hoạt Giờ Yên Lặng</span>
                    </label>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Giờ Bắt Đầu (Giờ VN UTC+7):</label>
                      <input
                        type="text"
                        value={telegramConfig.quiet_hours_start || '23:00'}
                        onChange={(e) => setTelegramConfig({ ...telegramConfig, quiet_hours_start: e.target.value })}
                        placeholder="23:00"
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-1.5 text-xs text-gray-200"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Giờ Kết Thúc (Giờ VN UTC+7):</label>
                      <input
                        type="text"
                        value={telegramConfig.quiet_hours_end || '06:00'}
                        onChange={(e) => setTelegramConfig({ ...telegramConfig, quiet_hours_end: e.target.value })}
                        placeholder="06:00"
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-3 py-1.5 text-xs text-gray-200"
                      />
                    </div>
                    <div className="flex items-center pt-4">
                      <label className="flex items-center gap-2 text-[11px] text-emerald-300 font-semibold cursor-pointer">
                        <input
                          type="checkbox"
                          checked={telegramConfig.bypass_critical_quiet_hours !== false}
                          onChange={(e) => setTelegramConfig({ ...telegramConfig, bypass_critical_quiet_hours: e.target.checked })}
                          className="rounded border-charcoal-700 text-emerald-500 focus:ring-0"
                        />
                        <span>Vẫn gửi Khớp Lệnh / TP / SL / Thanh Lý khi yên lặng</span>
                      </label>
                    </div>
                  </div>
                </div>

                {/* Buttons: Test and Save */}
                <div className="flex flex-wrap justify-between items-center gap-3 pt-3 border-t border-charcoal-750">
                  <button
                    type="button"
                    onClick={handleTestTelegram}
                    disabled={telegramTestStatus.loading}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-charcoal-700 text-white font-semibold rounded text-xs transition"
                  >
                    <Send className="w-3.5 h-3.5" />
                    <span>{telegramTestStatus.loading ? 'Đang gửi test...' : 'Gửi Tin Nhắn Thử Nghiệm'}</span>
                  </button>

                  <button
                    type="button"
                    onClick={handleSaveTelegramConfig}
                    className="px-4 py-1.5 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition"
                  >
                    Lưu Cấu Hình Telegram
                  </button>
                </div>

                {/* Test Feedback banner */}
                {telegramTestStatus.msg && (
                  <div
                    className={`p-3 rounded border text-xs ${
                      telegramTestStatus.ok
                        ? 'bg-emerald-950/80 border-emerald-700 text-emerald-300'
                        : 'bg-rose-950/80 border-rose-700 text-rose-300'
                    }`}
                  >
                    {telegramTestStatus.msg}
                  </div>
                )}
              </div>

              {/* Notification Outbox Queue & Delivery History */}
              <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 flex flex-col gap-3 text-xs">
                <div className="flex justify-between items-center border-b border-charcoal-750 pb-2">
                  <h3 className="font-bold text-gray-200 flex items-center gap-2">
                    <Clock className="w-4 h-4 text-aurum-400" />
                    Lịch Sử Hàng Đợi Gửi Tin Nhắn (Outbox Queue & Status)
                  </h3>
                  <button
                    type="button"
                    onClick={() => api.getNotificationHistory().then(setOutboxHistory)}
                    className="text-[11px] text-aurum-400 hover:text-aurum-300 font-semibold"
                  >
                    Làm mới
                  </button>
                </div>

                {outboxHistory.length === 0 ? (
                  <p className="text-[11px] text-gray-500 italic py-2">
                    Chưa có tin nhắn nào trong hàng đợi outbox.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left border-collapse">
                      <thead>
                        <tr className="border-b border-charcoal-700 text-[10px] text-gray-400 uppercase tracking-wider">
                          <th className="py-2 px-2">ID</th>
                          <th className="py-2 px-2">Thời gian</th>
                          <th className="py-2 px-2">Loại Tin</th>
                          <th className="py-2 px-2">Ưu Tiên</th>
                          <th className="py-2 px-2">Khóa / Đối Tượng</th>
                          <th className="py-2 px-2">Trạng Thái</th>
                          <th className="py-2 px-2">Số Thử</th>
                          <th className="py-2 px-2">Lỗi / Ghi chú</th>
                          <th className="py-2 px-2 text-right">Thao tác</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-charcoal-800 text-[11px]">
                        {outboxHistory.slice(0, 15).map((item) => {
                          const statusColor =
                            item.status === 'SENT'
                              ? 'bg-emerald-950 text-emerald-300 border-emerald-700'
                              : item.status === 'PENDING'
                              ? 'bg-amber-950 text-amber-300 border-amber-700'
                              : item.status === 'RETRYING'
                              ? 'bg-orange-950 text-orange-300 border-orange-700'
                              : item.status === 'SUPPRESSED'
                              ? 'bg-charcoal-800 text-gray-400 border-charcoal-700'
                              : 'bg-rose-950 text-rose-300 border-rose-700';

                          return (
                            <tr key={item.id} className="hover:bg-charcoal-800/40">
                              <td className="py-2 px-2 font-mono text-gray-400">#{item.id}</td>
                              <td className="py-2 px-2 text-gray-300">
                                {new Date(item.created_at).toLocaleTimeString('vi-VN')}
                              </td>
                              <td className="py-2 px-2 font-semibold text-aurum-300">
                                {item.message_type}
                              </td>
                              <td className="py-2 px-2">
                                <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                                  item.priority === 'CRITICAL' ? 'bg-rose-900/60 text-rose-300 border border-rose-700' : 'bg-charcoal-750 text-gray-400'
                                }`}>
                                  {item.priority || 'STANDARD'}
                                </span>
                              </td>
                              <td className="py-2 px-2 font-mono text-gray-400 text-[10px] truncate max-w-[120px]" title={item.dedupe_key}>
                                {item.dedupe_key}
                              </td>
                              <td className="py-2 px-2">
                                <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${statusColor}`}>
                                  {item.status}
                                </span>
                              </td>
                              <td className="py-2 px-2 text-center text-gray-300">
                                {item.attempts}/5
                              </td>
                              <td className="py-2 px-2 text-gray-400 text-[10px] truncate max-w-[180px]" title={item.error_message || ''}>
                                {item.error_message || '---'}
                              </td>
                              <td className="py-2 px-2 text-right">
                                {item.status !== 'SENT' && (
                                  <button
                                    type="button"
                                    onClick={() => handleRetryOutboxItem(item.id)}
                                    className="px-2 py-0.5 rounded bg-indigo-600/70 hover:bg-indigo-600 text-white text-[10px] font-semibold transition"
                                  >
                                    Thử lại
                                  </button>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Instructions Guide */}
              <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 text-xs space-y-2">
                <h3 className="font-bold text-aurum-400 uppercase tracking-wider">
                  Hướng Dẫn Thiết Lập Telegram Bot Nhanh (Miễn Phí):
                </h3>
                <ol className="list-decimal pl-5 space-y-1.5 text-gray-300 text-[11px] leading-relaxed">
                  <li>
                    Mở Telegram, tìm kiếm <strong>@BotFather</strong> và gõ lệnh <code>/newbot</code>.
                  </li>
                  <li>
                    Đặt tên cho Bot và username kết thúc bằng <code>bot</code> (VD: <code>MyAurumAlerts_bot</code>).
                  </li>
                  <li>
                    Copy mã <strong>HTTP API Token</strong> do BotFather cấp và dán vào ô <strong>Bot Token</strong> ở trên.
                  </li>
                  <li>
                    Mở bot vừa tạo và bấm <strong>Start</strong> (hoặc gõ <code>/start</code>) để bot được phép gửi tin cho bạn.
                  </li>
                  <li>
                    Tìm bot <strong>@userinfobot</strong> trên Telegram và bấm Start để lấy số <strong>Id</strong> của bạn, sau đó dán vào ô <strong>Chat ID</strong>.
                  </li>
                  <li>
                    Bấm <strong>Gửi Tin Nhắn Thử Nghiệm</strong>. Nếu điện thoại của bạn rung nhận được tin, bấm <strong>Lưu Cấu Hình Telegram</strong>.
                  </li>
                </ol>
              </div>
            </div>
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
                  <span className="text-gray-400">R:R Kỳ Vọng:</span>
                  <span className="text-aurum-400 font-bold">1:{activePosition.position.gross_rr}</span>
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
                    ${activePosition.position.estimated_liquidation?.toFixed(2) || '---'}
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
                <div className="space-y-2 text-xs">
                  <div className="p-2.5 rounded bg-charcoal-850 border border-charcoal-700">
                    <div className="flex justify-between font-bold text-sm mb-1">
                      <span className={selectedIntent.direction === 'LONG' ? 'text-emerald-400' : 'text-rose-400'}>
                        {selectedIntent.direction} XAUUSDT
                      </span>
                      <span className="text-aurum-400">R:R 1:{selectedIntent.grossRR}</span>
                    </div>
                    <div className="text-[11px] text-gray-400 space-y-0.5">
                      <p>Kế hoạch Entry: ${selectedIntent.plannedEntry}</p>
                      <p>Stop Loss: ${selectedIntent.stopLoss}</p>
                      <p>
                        Take Profit:{' '}
                        {selectedIntent.takeProfit && selectedIntent.takeProfit !== selectedIntent.stopLoss
                          ? `$${selectedIntent.takeProfit}`
                          : 'Chưa có TP (Không đủ điều kiện)'}
                      </p>
                      <p>
                        Khối lượng: {selectedIntent.quantity} oz | Ký quỹ: ${(selectedIntent.initialRiskUsdt * 2).toFixed(2)} USDT ({leverage}x)
                      </p>
                      {selectedIntent.status && (
                        <p className="text-[10px] text-gray-400 font-mono">
                          Trạng thái: <span className="text-aurum-400">{selectedIntent.status}</span>
                        </p>
                      )}
                    </div>
                  </div>

                  {selectedIntent.source === 'WATCH_SETUP' ? (
                    <button
                      onClick={() =>
                        handleArmWatchSetup(
                          selectedIntent.setup_id!,
                          selectedIntent.direction,
                          selectedIntent.setup_instance_id,
                          selectedIntent.revision
                        )
                      }
                      className="w-full py-2 bg-gradient-to-r from-aurum-500 to-aurum-600 hover:from-aurum-400 hover:to-aurum-500 text-charcoal-950 font-bold rounded text-xs transition shadow-sm"
                    >
                      Arm {selectedIntent.direction} — PAPER
                    </button>
                  ) : (
                    <button
                      onClick={handleOpenPaperTrade}
                      disabled={!selectedIntent.takeProfit || selectedIntent.takeProfit === selectedIntent.stopLoss}
                      className="w-full py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {!selectedIntent.takeProfit || selectedIntent.takeProfit === selectedIntent.stopLoss
                        ? 'Thiếu TP (Chưa đủ điều kiện)'
                        : `Mở ${selectedIntent.direction} ${selectedIntent.orderType} — PAPER`}
                    </button>
                  )}
                </div>
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

          {/* Hard Filters Live Checklist */}
          <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-4 shadow-lg flex flex-col gap-2">
            <span className="text-xs font-bold text-gray-300 border-b border-charcoal-750 pb-2">
              BỘ LỌC ĐIỀU KIỆN (CHECKLIST)
            </span>
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
                    <span className="text-[10px] text-gray-400">{chk.detail}</span>
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
