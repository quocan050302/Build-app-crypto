import { useState, useEffect, useCallback } from 'react';
import { ChartComponent } from './ChartComponent';
import { api } from './api/client';
import type { RiskRewardData } from './plugins/RiskRewardPrimitive';
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
  Info
} from 'lucide-react';


export function App() {
  // Navigation & Timeframe
  const [activeTab, setActiveTab] = useState<'chart' | 'smc' | 'paper' | 'reports' | 'news' | 'journal' | 'education'>('chart');
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

  // Paper Trading & Account
  const [accountStatus, setAccountStatus] = useState<any>(null);
  const [activePosition, setActivePosition] = useState<any>(null);
  const [autoPaperActive, setAutoPaperActive] = useState<boolean>(true);

  // Additional Panels Data
  const [reports, setReports] = useState<any[]>([]);
  const [newsData, setNewsData] = useState<any>(null);
  const [journalTrades, setJournalTrades] = useState<any[]>([]);
  const [lessons, setLessons] = useState<any[]>([]);
  const [educationList, setEducationList] = useState<any[]>([]);
  const [selectedEdu, setSelectedEdu] = useState<any>(null);

  // Import file feedback
  const [importStatus, setImportStatus] = useState<string | null>(null);

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
      if (autoData && typeof autoData.auto_paper_enabled === 'boolean') {
        setAutoPaperActive(autoData.auto_paper_enabled);
      }
      if (posData?.has_active_position) {
        setActivePosition(posData);
        // Set chart overlay to current open position
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
        };
        setActiveOverlay(overlay);
      } else {
        setActivePosition(null);
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
      await refreshAccountAndHealth();
    } catch (err) {
      console.error('Failed to toggle auto paper state:', err);
    }
  };

  // 2. Fetch SMC Market Analysis
  const refreshAnalysis = useCallback(async () => {
    try {
      const data = await api.getAnalysis('XAUUSDT', timeframe);
      setAnalysis(data);

      // If no active open position, show candidate signal if available
      if (!activePosition && data.active_signal) {
        const sig = data.active_signal;
        const overlay: RiskRewardData = {
          id: sig.id,
          direction: sig.direction,
          state: sig.state,
          plannedEntry: sig.planned_entry,
          stopLoss: sig.stop_loss,
          takeProfit: sig.targets?.[0]?.price || sig.stop_loss,
          quantity: sig.quantity,
          initialRiskUsdt: sig.initial_risk_usdt,
          riskPct: sig.risk_pct,
          grossRR: sig.gross_rr,
          estimatedNetRR: sig.estimated_net_rr,
        };
        setActiveOverlay(overlay);
      }
    } catch (err) {
      console.warn('SMC analysis error:', err);
    }
  }, [timeframe, activePosition]);

  // Periodic polling for realtime updates
  useEffect(() => {
    refreshAccountAndHealth();
    refreshAnalysis();
    const interval = setInterval(() => {
      refreshAccountAndHealth();
      refreshAnalysis();
    }, 10000);
    return () => clearInterval(interval);
  }, [refreshAccountAndHealth, refreshAnalysis]);

  // 3. Tab-specific data loading
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
    } else if (activeTab === 'education') {
      api.getEducation().then((list) => {
        setEducationList(list);
        if (list.length > 0 && !selectedEdu) setSelectedEdu(list[0]);
      }).catch(console.error);
    }
  }, [activeTab, selectedEdu]);

  // 4. Action Handlers
  const handleOpenPaperTrade = async () => {
    if (!analysis?.active_signal) return;
    const sig = analysis.active_signal;
    const targetPrice = sig.targets?.[0]?.price ?? sig.take_profit;
    try {
      await api.createPaperOrder({
        setup_id: sig.setup_id,
        signal_id: sig.signal_id,
        instrument: 'XAUUSDT',
        direction: sig.direction,
        state: 'paper_open',
        order_type: 'MARKET',
        timeframe: timeframe,
        planned_entry: sig.planned_entry,
        stop_loss: sig.stop_loss,
        take_profit: targetPrice,
        quantity: sig.quantity,
        initial_risk_usdt: sig.initial_risk_usdt,
        risk_pct: sig.risk_pct,
        gross_rr: sig.gross_rr,
        estimated_net_rr: sig.estimated_net_rr,
      });
      await refreshAccountAndHealth();
      setActiveTab('chart');
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message || 'Không thể mở lệnh paper trade');
    }
  };

  const handleClosePosition = async (orderId: string) => {
    if (!confirm('Bạn có chắc chắn muốn đóng vị thế paper trading này tại giá thị trường?')) return;
    try {
      await api.closePaperOrder(orderId);
      await refreshAccountAndHealth();
      setActiveOverlay(null);
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message || 'Lỗi khi đóng vị thế');
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

  return (
    <div className="min-h-screen bg-charcoal-950 text-gray-200 flex flex-col font-sans select-none">
      {/* 1. Global Header Bar */}
      <header className="bg-charcoal-900 border-b border-charcoal-750 px-4 py-2.5 flex flex-wrap justify-between items-center gap-3">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-aurum-500 animate-pulse" />
            <h1 className="text-lg font-bold text-aurum-400 tracking-wider">
              AURUM DESK
            </h1>
            <span className="text-[10px] uppercase font-semibold tracking-wider bg-charcoal-800 text-gray-400 px-2 py-0.5 rounded border border-charcoal-700">
              PAPER TRADING · VỐN GIẢ LẬP $1,000
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
            <span className="text-gray-400">Feed Bitget:</span>
            <span className={health?.feed_connected ? 'text-emerald-400 font-medium' : 'text-rose-400'}>
              {health?.feed_connected ? `Live (${health.data_freshness_sec}s)` : 'Mất kết nối'}
            </span>
          </div>

          <div className="flex items-center gap-1.5 bg-charcoal-850 px-2.5 py-1 rounded border border-charcoal-700">
            <span className="text-gray-400">Trạng thái:</span>
            <span className={`font-semibold capitalize ${
              analysis?.engine_state === 'paper_open' ? 'text-emerald-400' :
              analysis?.engine_state === 'candidate' ? 'text-aurum-400' :
              analysis?.engine_state === 'blocked_news' ? 'text-rose-400' : 'text-blue-400'
            }`}>
              {analysis?.engine_state || 'Analyzing...'}
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
          { id: 'smc', label: 'Phân Tích SMC/ICT', icon: Layers },
          { id: 'paper', label: 'Quản Trị Vốn (0.5% Risk)', icon: ShieldAlert },
          { id: 'reports', label: 'Nghiên Cứu Phiên & Ngày', icon: Calendar },
          { id: 'news', label: 'Tin Tức & Blackout', icon: Clock },
          { id: 'journal', label: 'Nhật Ký & Bài Học', icon: History },
          { id: 'education', label: 'Thư Viện Kiến Thức', icon: BookOpen },
        ].map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`flex items-center gap-1.5 px-3.5 py-2.5 font-medium border-b-2 transition ${
                isActive
                  ? 'border-aurum-500 text-aurum-400 bg-charcoal-850/60'
                  : 'border-transparent text-gray-400 hover:text-gray-200 hover:bg-charcoal-850/30'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{tab.label}</span>
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
                activeOverlay={activeOverlay}
                onOverlayChange={(updated) => setActiveOverlay(updated)}
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

          {activeTab === 'smc' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-4 overflow-y-auto max-h-[750px]">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <h2 className="text-base font-bold text-aurum-400">Chi Tiết Cấu Trúc Thị Trường SMC/ICT</h2>
                <span className="text-xs text-gray-400">Khung: {timeframe} · Cập nhật nến đóng</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block mb-1">Xu Hướng Cấu Trúc (Trend)</span>
                  <span className={`text-base font-bold ${analysis?.trend === 'BULLISH' ? 'text-emerald-400' : analysis?.trend === 'BEARISH' ? 'text-rose-400' : 'text-amber-400'}`}>
                    {analysis?.trend || 'RANGING'}
                  </span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block mb-1">Vị Trí Dealing Range</span>
                  <span className={`text-base font-bold ${analysis?.zone === 'DISCOUNT' ? 'text-emerald-400' : 'text-rose-400'}`}>
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
                    <div key={idx} className="bg-charcoal-850 p-2.5 rounded border border-charcoal-700 flex justify-between items-center">
                      <div>
                        <span className={`font-semibold ${fvg.type === 'BULLISH_FVG' ? 'text-emerald-400' : 'text-rose-400'}`}>
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

          {activeTab === 'paper' && (
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-4 overflow-y-auto max-h-[750px]">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-3">
                <div>
                  <h2 className="text-base font-bold text-aurum-400">Quản Trị Rủi Ro & Auto Paper Trading</h2>
                  <p className="text-xs text-gray-400">Giả lập môi trường Bitget USDT-Futures không rủi ro tiền thật</p>
                </div>
                <button
                  onClick={() => setAutoPaperActive(!autoPaperActive)}
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
                  <span className="text-gray-400 block">Vốn Hiện Tại</span>
                  <span className="text-lg font-bold text-emerald-400">${accountStatus?.current_equity?.toFixed(2) || '1,000.00'}</span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block">Số Lệnh Hôm Nay</span>
                  <span className="text-lg font-bold text-aurum-400">{accountStatus?.fills_count || 0} / 3 Lệnh</span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block">PnL Realized Hôm Nay</span>
                  <span className={`text-lg font-bold ${accountStatus?.realized_pnl_today >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    ${accountStatus?.realized_pnl_today?.toFixed(2) || '0.00'}
                  </span>
                </div>
                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-700">
                  <span className="text-gray-400 block">Giới Hạn Lỗ Ngày (1.5%)</span>
                  <span className="text-lg font-bold text-rose-400">${accountStatus?.daily_loss_limit_usdt?.toFixed(2) || '15.00'}</span>
                </div>
              </div>
            </div>
          )}

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
              <div className={`p-3 rounded-lg border flex items-center justify-between text-xs ${
                newsData?.blackout_status?.is_blackout
                  ? 'bg-rose-950/80 border-rose-800 text-rose-200'
                  : 'bg-emerald-950/60 border-emerald-800 text-emerald-200'
              }`}>
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
                          <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                            ev.impact === 'High' ? 'bg-rose-900/60 text-rose-300' :
                            ev.impact === 'Medium' ? 'bg-amber-900/60 text-amber-300' : 'bg-gray-800 text-gray-400'
                          }`}>
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
                  <div key={t.id} className="bg-charcoal-850 p-3.5 rounded-lg border border-charcoal-700 flex flex-wrap justify-between items-center gap-3 text-xs">
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`px-2 py-0.5 rounded font-bold ${t.direction === 'LONG' ? 'bg-emerald-900/60 text-emerald-300' : 'bg-rose-900/60 text-rose-300'}`}>
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
                        <span className={`text-sm font-bold block ${t.realized_pnl_net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {t.realized_pnl_net !== null ? `${t.realized_pnl_net > 0 ? '+' : ''}$${t.realized_pnl_net}` : 'Đang chạy'}
                        </span>
                        {t.realized_r !== null && (
                          <span className="text-[11px] text-gray-400">({t.realized_r > 0 ? '+' : ''}{t.realized_r}R)</span>
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
                      <p className="text-gray-400 text-[11px] mb-1"><span className="text-gray-300 font-medium">Nhận xét:</span> {ls.reflection}</p>
                      <p className="text-aurum-400 text-[11px]"><span className="text-gray-300 font-medium">Hành động khắc phục:</span> {ls.action_rule}</p>
                    </div>
                  ))}
                  {lessons.length === 0 && (
                    <p className="text-xs text-gray-500 italic">Chưa có bài học nào được ghi nhận.</p>
                  )}
                </div>
              </div>
            </div>
          )}


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
                    <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">{selectedEdu.category}</span>
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
                  <span className="font-semibold text-gray-200">${activePosition.position.actual_entry || activePosition.position.planned_entry}</span>
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
                <div className="flex justify-between pt-1 border-t border-charcoal-750">
                  <span className="text-gray-400 font-medium">PnL Tạm Tính:</span>
                  <span className={`font-bold text-sm ${activePosition.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
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
            /* Candidate Signal Card */
            <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-4 shadow-lg flex flex-col gap-3">
              <div className="flex justify-between items-center border-b border-charcoal-750 pb-2">
                <span className="text-xs font-bold text-aurum-400">TÍN HIỆU SMC TIẾP THEO</span>
                <span className="text-[10px] text-gray-400">{timeframe}</span>
              </div>

              {analysis?.active_signal ? (
                <div className="space-y-2 text-xs">
                  <div className="p-2.5 rounded bg-charcoal-850 border border-charcoal-700">
                    <div className="flex justify-between font-bold text-sm mb-1">
                      <span className={analysis.active_signal.direction === 'LONG' ? 'text-emerald-400' : 'text-rose-400'}>
                        {analysis.active_signal.direction} XAUUSDT
                      </span>
                      <span className="text-aurum-400">R:R 1:{analysis.active_signal.gross_rr}</span>
                    </div>
                    <div className="text-[11px] text-gray-400 space-y-0.5">
                      <p>Kế hoạch Entry: ${analysis.active_signal.planned_entry}</p>
                      <p>Stop Loss: ${analysis.active_signal.stop_loss}</p>
                      <p>Take Profit: ${analysis.active_signal.targets?.[0]?.price}</p>
                    </div>
                  </div>

                  <button
                    onClick={handleOpenPaperTrade}
                    className="w-full py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition shadow-sm"
                  >
                    Vào Lệnh Giả Lập (0.5% Risk)
                  </button>
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
              <span className="text-gray-400">Vốn Khả Dụng:</span>
              <span className="font-mono text-emerald-400 font-bold">
                ${accountStatus?.current_equity?.toFixed(2) || '1,000.00'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Vị thế đang mở:</span>
              <span className="font-mono text-emerald-400 font-semibold">{activePosition ? '1 / 1' : '0 / 1'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Lệnh đã vào hôm nay:</span>
              <span className="font-mono">{accountStatus?.today_fills_count ?? accountStatus?.fills_count ?? 0} / 3 Lệnh</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Lệnh chờ (Armed):</span>
              <span className="font-mono">{accountStatus?.armed_orders_count ?? 0} Lệnh</span>
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
