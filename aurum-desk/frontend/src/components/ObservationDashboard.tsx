/**
 * frontend/src/components/ObservationDashboard.tsx
 * AURUM V17.6 — Autonomous Observation, Live Decision Intelligence & Forward Paper Monitoring Dashboard.
 */

import React from 'react';
import {
  Activity,
  ShieldAlert,
  Clock,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  TrendingUp,
  RefreshCw,
  Eye,
  Layers,
  Target,
  FileText,
  Radio,
} from 'lucide-react';
import {
  useObservationDashboard,
  type ObservationTimeRange,
} from '../hooks/useObservationDashboard';

export interface ObservationDashboardProps {
  symbol?: string;
  scope?: string;
  runId?: string | null;
  autoPaperActive?: boolean;
  onToggleAutoPaper?: () => void;
  onFocusSetupOnChart?: (setupId: string) => void;
  onFocusTradeOnChart?: (orderId: string) => void;
}

export const ObservationDashboard: React.FC<ObservationDashboardProps> = ({
  symbol = 'XAUUSDT',
  scope = 'CG01',
  runId = null,
  autoPaperActive = false,
  onFocusSetupOnChart,
  onFocusTradeOnChart,
}) => {
  const {
    snapshot,
    refreshing,
    error,
    timeRange,
    timezone,
    setTimeRange,
    setTimezone,
    refresh,
    formatTimestamp,
  } = useObservationDashboard({
    symbol,
    scope,
    runId,
    pollingIntervalMs: 20000,
  });

  const engine = snapshot?.engine;
  const feed = snapshot?.feed;
  const coverage = snapshot?.coverage;
  const funnel = snapshot?.funnel || {};
  const financial = snapshot?.financial;
  const decisions = snapshot?.decisions?.items || [];
  const waitingSetups = snapshot?.waiting_setups || [];
  const blockedCategories = snapshot?.blocked_categories || [];

  // Helper for Engine state color
  const getEngineStateBadge = (state?: string) => {
    switch (state) {
      case 'RUNNING':
        return { label: 'ĐANG CHẠY (RUNNING)', color: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' };
      case 'WAITING_MARKET':
        return { label: 'CHỜ THỊ TRƯỜNG (WAITING)', color: 'bg-sky-500/20 text-sky-400 border-sky-500/30' };
      case 'DEGRADED':
        return { label: 'SUY GIẢM (DEGRADED)', color: 'bg-amber-500/20 text-amber-400 border-amber-500/30' };
      case 'STALLED':
        return { label: 'BỊ TREO (STALLED)', color: 'bg-rose-500/20 text-rose-400 border-rose-500/30' };
      case 'PAUSED':
      case 'STOPPED':
        return { label: 'TẠM DỪNG (PAUSED)', color: 'bg-gray-700/50 text-gray-300 border-gray-600' };
      default:
        return { label: 'CHƯA RÕ (UNKNOWN)', color: 'bg-charcoal-800 text-gray-400 border-charcoal-700' };
    }
  };

  // Helper for Feed Freshness color
  const getFeedFreshnessBadge = (freshness?: string) => {
    switch (freshness) {
      case 'CONNECTED_FRESH':
        return { label: 'TRỰC TIẾP & MỚI (LIVE)', color: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' };
      case 'CONNECTED_STALE':
        return { label: 'CŨ / CHẬM (STALE)', color: 'bg-amber-500/20 text-amber-400 border-amber-500/30' };
      case 'RECONNECTING':
        return { label: 'ĐANG KẾT NỐI LẠI', color: 'bg-sky-500/20 text-sky-400 border-sky-500/30' };
      case 'DISCONNECTED':
        return { label: 'MẤT KẾT NỐI', color: 'bg-rose-500/20 text-rose-400 border-rose-500/30' };
      default:
        return { label: 'CHƯA CÓ DỮ LIỆU', color: 'bg-charcoal-800 text-gray-400 border-charcoal-700' };
    }
  };

  const engineBadge = getEngineStateBadge(engine?.state);
  const feedBadge = getFeedFreshnessBadge(feed?.freshness_state);

  return (
    <div className="flex flex-col gap-6 p-4 md:p-6 bg-charcoal-950 text-gray-200 min-h-screen">
      {/* 1. Header Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-charcoal-900 border border-charcoal-750 p-4 rounded-xl shadow-lg">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2.5">
            <span className="w-3 h-3 rounded-full bg-aurum-500 animate-pulse" />
            <h2 className="text-xl font-bold tracking-wide text-aurum-400">
              Quan Sát & Trí Tuệ Quyết Định Bot
            </h2>
            <span className="text-xs font-mono bg-charcoal-800 text-gray-300 px-2 py-0.5 rounded border border-charcoal-700">
              V17.6 LIVE TELEMETRY
            </span>
          </div>
          <p className="text-xs text-gray-400">
            Hệ thống đo lường thực tế, truy nguyên quyết định SMC/ICT và theo dõi tài chính Paper Trading độc lập.
          </p>
        </div>

        {/* Controls: TimeRange, Timezone, Refresh */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Time Range Pills */}
          <div className="flex bg-charcoal-950 p-1 rounded-lg border border-charcoal-750 text-xs font-medium">
            {(['TODAY', '7D', '14D', '30D'] as ObservationTimeRange[]).map((r) => (
              <button
                key={r}
                onClick={() => setTimeRange(r)}
                className={`px-3 py-1 rounded transition ${
                  timeRange === r
                    ? 'bg-aurum-500 text-charcoal-950 font-bold shadow-xs'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {r === 'TODAY' ? 'Hôm Nay' : r}
              </button>
            ))}
          </div>

          {/* Timezone Toggle */}
          <div className="flex bg-charcoal-950 p-1 rounded-lg border border-charcoal-750 text-xs font-medium">
            <button
              onClick={() => setTimezone('VN')}
              className={`px-2.5 py-1 rounded transition ${
                timezone === 'VN'
                  ? 'bg-charcoal-800 text-aurum-400 font-bold border border-aurum-500/40'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              Giờ VN (ICT)
            </button>
            <button
              onClick={() => setTimezone('NY')}
              className={`px-2.5 py-1 rounded transition ${
                timezone === 'NY'
                  ? 'bg-charcoal-800 text-aurum-400 font-bold border border-aurum-500/40'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              Giờ NY (ET)
            </button>
          </div>

          {/* Refresh Button */}
          <button
            onClick={refresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-charcoal-800 hover:bg-charcoal-750 border border-charcoal-700 text-xs text-gray-300 font-medium transition"
            title="Tải lại dữ liệu quan sát mới nhất"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin text-aurum-400' : ''}`} />
            <span>{refreshing ? 'Đang cập nhật...' : 'Làm mới'}</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-3 p-3.5 rounded-lg bg-rose-950/40 border border-rose-800/80 text-rose-300 text-xs">
          <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
          <span>Lỗi tải dữ liệu quan sát: {error}</span>
        </div>
      )}

      {/* 2. Five Executive Health Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Card 1: Bot Engine */}
        <div className="flex flex-col gap-2 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400 font-medium flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-aurum-400" />
              1. Bot Engine Health
            </span>
            <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${engineBadge.color}`}>
              {engineBadge.label}
            </span>
          </div>
          <div className="mt-1 flex flex-col gap-1 text-xs">
            <div className="flex justify-between">
              <span className="text-gray-400">Heartbeat gần nhất:</span>
              <span className="font-mono text-gray-200">
                {engine?.last_heartbeat_age_sec != null ? `${engine.last_heartbeat_age_sec}s trước` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Chu kỳ quét (Cycle):</span>
              <span className="font-mono text-gray-200">
                {engine?.last_evaluation_age_sec != null ? `${engine.last_evaluation_age_sec}s trước` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Số chu kỳ đã quét:</span>
              <span className="font-mono text-aurum-400 font-bold">
                {engine?.cycles_count ?? 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Chu kỳ lỗi:</span>
              <span className={`font-mono ${engine?.failed_cycles_count ? 'text-rose-400 font-bold' : 'text-gray-400'}`}>
                {engine?.failed_cycles_count ?? 0}
              </span>
            </div>
          </div>
        </div>

        {/* Card 2: Market Feed & Freshness */}
        <div className="flex flex-col gap-2 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400 font-medium flex items-center gap-1.5">
              <Radio className="w-3.5 h-3.5 text-sky-400" />
              2. Báo Giá & Dữ Liệu
            </span>
            <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${feedBadge.color}`}>
              {feedBadge.label}
            </span>
          </div>
          <div className="mt-1 flex flex-col gap-1 text-xs">
            <div className="flex justify-between">
              <span className="text-gray-400">Độ trễ báo giá:</span>
              <span className="font-mono text-gray-200">
                {feed?.quote_age_sec != null ? `${feed.quote_age_sec}s` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Bid / Ask:</span>
              <span className="font-mono text-gray-200 font-semibold">
                {feed?.bid != null && feed?.ask != null ? `${feed.bid.toFixed(2)} / ${feed.ask.toFixed(2)}` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between items-center pt-1 border-t border-charcoal-800">
              <span className="text-gray-400">Độ mới nến (TF):</span>
              <div className="flex gap-1.5 font-mono text-[10px]">
                {['1M', '5M', '15M', '1H'].map((tf) => {
                  const tfObj = feed?.timeframe_health?.[tf];
                  const isStale = tfObj?.is_stale ?? true;
                  return (
                    <span
                      key={tf}
                      title={`${tf}: ${tfObj?.candle_age_sec != null ? tfObj.candle_age_sec + 's' : 'chưa có nến'}`}
                      className={`px-1 py-0.2 rounded border ${
                        !isStale
                          ? 'bg-emerald-950/60 text-emerald-400 border-emerald-800'
                          : 'bg-rose-950/60 text-rose-400 border-rose-800'
                      }`}
                    >
                      {tf}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* Card 3: SMC Evaluator */}
        <div className="flex flex-col gap-2 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400 font-medium flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-purple-400" />
              3. Đánh Giá SMC/ICT
            </span>
            <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded border bg-purple-950/50 text-purple-300 border-purple-800">
              {snapshot?.latest_cycle?.decision_status || 'NO_SETUP'}
            </span>
          </div>
          <div className="mt-1 flex flex-col gap-1 text-xs">
            <div className="flex justify-between">
              <span className="text-gray-400">Thời gian chu kỳ:</span>
              <span className="font-mono text-gray-200">
                {snapshot?.latest_cycle?.duration_ms != null ? `${snapshot.latest_cycle.duration_ms} ms` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Cơ hội phát hiện:</span>
              <span className="font-mono text-gray-200">
                {snapshot?.latest_cycle?.opportunities_detected ?? 0}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Market Regime:</span>
              <span className="font-mono text-aurum-400 font-semibold">
                {snapshot?.latest_cycle?.market_regime || 'NORMAL'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Chờ điều kiện:</span>
              <span className="font-mono text-sky-400 font-bold">
                {waitingSetups.length} setup
              </span>
            </div>
          </div>
        </div>

        {/* Card 4: Paper Execution */}
        <div className="flex flex-col gap-2 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400 font-medium flex items-center gap-1.5">
              <ShieldAlert className="w-3.5 h-3.5 text-emerald-400" />
              4. Thực Thi Paper Safe
            </span>
            <span
              className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${
                autoPaperActive
                  ? 'bg-emerald-950/60 text-emerald-400 border-emerald-800'
                  : 'bg-rose-950/60 text-rose-400 border-rose-800'
              }`}
            >
              {autoPaperActive ? 'AUTO ON' : 'AUTO PAUSED'}
            </span>
          </div>
          <div className="mt-1 flex flex-col gap-1 text-xs">
            <div className="flex justify-between">
              <span className="text-gray-400">Lệnh đang ARM:</span>
              <span className="font-mono text-aurum-400 font-bold">
                {financial?.armed_orders ?? 0}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Vị thế đang mở:</span>
              <span className="font-mono text-emerald-400 font-bold">
                {financial?.open_trades ?? 0}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Lệnh đã khớp hôm nay:</span>
              <span className="font-mono text-gray-200">
                {(financial?.closed_trades ?? 0) + (financial?.open_trades ?? 0)} / 3 lệnh
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Live Trading:</span>
              <span className="font-mono text-rose-400 font-bold">KHÓA TUYỆT ĐỐI</span>
            </div>
          </div>
        </div>

        {/* Card 5: Observation Run & Truthful Coverage */}
        <div className="flex flex-col gap-2 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400 font-medium flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5 text-amber-400" />
              5. Độ Bao Phủ Quan Sát
            </span>
            <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded border bg-amber-950/50 text-amber-400 border-amber-800">
              {coverage?.coverage_state || 'FULL'}
            </span>
          </div>
          <div className="mt-1 flex flex-col gap-1 text-xs">
            <div className="flex justify-between">
              <span className="text-gray-400">Tỷ lệ bao phủ (Uptime):</span>
              <span className="font-mono text-emerald-400 font-bold">
                {coverage?.coverage_pct != null ? `${coverage.coverage_pct}%` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Giờ quan sát tốt:</span>
              <span className="font-mono text-gray-200">
                {coverage?.observed_healthy_hours != null ? `${coverage.observed_healthy_hours}h` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Thời gian cửa sổ:</span>
              <span className="font-mono text-gray-200">
                {coverage?.total_span_hours != null ? `${coverage.total_span_hours}h` : 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Bằng chứng:</span>
              <span className="font-mono text-[10px] text-gray-300 truncate" title={coverage?.evidence_type}>
                {coverage?.evidence_type === 'LIVE_PAPER_FORWARD_OBSERVATION' ? 'Forward Paper Thật' : 'Mô phỏng'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* 3. Opportunity Funnel Section */}
      <div className="flex flex-col gap-3 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-aurum-400" />
            <h3 className="text-sm font-bold text-gray-200 tracking-wide">
              Phễu Chuyển Đổi Cơ Hội (Opportunity Funnel)
            </h3>
          </div>
          <span className="text-xs text-gray-400">
            Tổng cơ hội SMC đã ghi nhận: <strong className="text-aurum-400">{funnel?.total_opportunities ?? 0}</strong>
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 pt-1 text-center">
          {[
            { id: 'detected', label: '1. Đã Phát Hiện', count: funnel?.detected ?? funnel?.detected_zones_count ?? 0, color: 'text-gray-300' },
            { id: 'watched', label: '2. Đang Theo Dõi', count: funnel?.watched ?? funnel?.watched_zones_count ?? 0, color: 'text-sky-400' },
            { id: 'touched', label: '3. Đã Chạm Vùng', count: funnel?.touched ?? funnel?.touched_zones_count ?? 0, color: 'text-indigo-400' },
            { id: 'triggered', label: '4. Đã Trigger', count: funnel?.triggered ?? funnel?.confirmed_scenarios_count ?? 0, color: 'text-purple-400' },
            { id: 'qualified', label: '5. Hợp Lệ (Qual)', count: funnel?.qualified ?? 0, color: 'text-emerald-400' },
            { id: 'blocked', label: '6. Bị Chặn (Guard)', count: funnel?.blocked ?? 0, color: 'text-rose-400' },
            { id: 'armed', label: '7. Đã Đặt Lệnh (ARM)', count: funnel?.armed ?? 0, color: 'text-aurum-400' },
            { id: 'filled', label: '8. Đã Khớp (Fill)', count: funnel?.filled ?? 0, color: 'text-emerald-300' },
          ].map((step) => (
            <div key={step.id} className="flex flex-col items-center justify-center p-2.5 rounded-lg bg-charcoal-950 border border-charcoal-800">
              <span className="text-[10px] text-gray-400 font-medium truncate max-w-full">{step.label}</span>
              <span className={`text-lg font-bold font-mono mt-0.5 ${step.color}`}>
                {step.count}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* 4. Two-Column Intelligence Grid: Waiting Conditions & Blocked Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Left: Waiting Conditions Panel */}
        <div className="flex flex-col gap-3 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Eye className="w-4 h-4 text-sky-400" />
              <h3 className="text-sm font-bold text-gray-200">
                Điều Kiện SMC Đang Chờ (Waiting Conditions)
              </h3>
            </div>
            <span className="text-xs bg-sky-950/60 text-sky-400 border border-sky-800/80 px-2 py-0.5 rounded font-mono font-semibold">
              {waitingSetups.length} Setups
            </span>
          </div>

          {waitingSetups.length === 0 ? (
            <div className="p-6 text-center text-xs text-gray-500 bg-charcoal-950/50 rounded-lg border border-charcoal-800">
              Không có setup nào đang trong trạng thái chờ. Engine sẵn sàng quét các cấu trúc nến mới.
            </div>
          ) : (
            <div className="flex flex-col gap-3 max-h-80 overflow-y-auto pr-1">
              {waitingSetups.map((ws) => (
                <div key={ws.opportunity_id} className="p-3 rounded-lg bg-charcoal-950 border border-charcoal-800 flex flex-col gap-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-gray-300 flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${ws.direction === 'LONG' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800'}`}>
                        {ws.direction}
                      </span>
                      {ws.model_id}
                    </span>
                    <span className="text-[10px] font-mono text-gray-400">
                      {formatTimestamp(ws.occurred_at_ms)}
                    </span>
                  </div>

                  {/* Conditions checklist */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 pt-1 border-t border-charcoal-850 text-xs">
                    {ws.conditions.map((c) => (
                      <div key={c.id} className="flex items-center gap-1.5 text-[11px]">
                        {c.status === 'FULFILLED' ? (
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                        ) : c.status === 'BLOCKED' ? (
                          <XCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
                        ) : (
                          <Clock className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                        )}
                        <span className={c.status === 'FULFILLED' ? 'text-gray-300' : 'text-gray-400'}>
                          {c.name_vi}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Blocked Reasons Panel */}
        <div className="flex flex-col gap-3 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-rose-400" />
              <h3 className="text-sm font-bold text-gray-200">
                Lý Do Không Vào Lệnh (Blocked Reasons)
              </h3>
            </div>
            <span className="text-xs text-gray-400">
              Phân loại nguyên nhân theo Guard & SMC
            </span>
          </div>

          {blockedCategories.length === 0 ? (
            <div className="p-6 text-center text-xs text-gray-500 bg-charcoal-950/50 rounded-lg border border-charcoal-800">
              Chưa có trường hợp bị chặn nào trong khung thời gian được chọn.
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-80 overflow-y-auto pr-1">
              {blockedCategories.map((bc) => (
                <div key={bc.category} className="flex items-center justify-between p-3 rounded-lg bg-charcoal-950 border border-charcoal-800">
                  <div className="flex flex-col gap-0.5">
                    <span className="text-xs font-semibold text-gray-200">{bc.category_title_vi}</span>
                    <span className="text-[10px] font-mono text-gray-400">{bc.category}</span>
                  </div>
                  <span className="text-base font-bold font-mono text-rose-400 bg-rose-950/40 border border-rose-800/60 px-2 py-0.5 rounded">
                    {bc.count}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 5. Performance Panel (Committed Financial Ledger) */}
      <div className="flex flex-col gap-3 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Target className="w-4 h-4 text-emerald-400" />
            <h3 className="text-sm font-bold text-gray-200">
              Hiệu Suất Thực Tế Sau Phí (Committed Paper Ledger)
            </h3>
          </div>
          <span className="text-xs text-gray-400 font-mono">
            Vốn Khởi Điểm: ${financial?.initial_capital_usdt?.toFixed(2) || '1,000.00'} USDT
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-1 text-xs">
          <div className="p-3 rounded-lg bg-charcoal-950 border border-charcoal-800 flex flex-col gap-1">
            <span className="text-gray-400">Lợi Nhuận Ròng (Net PnL):</span>
            <span
              className={`text-lg font-bold font-mono ${
                (financial?.net_pnl_usd ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              ${financial?.net_pnl_usd != null ? financial.net_pnl_usd.toFixed(2) : 'N/A'} USD
            </span>
          </div>

          <div className="p-3 rounded-lg bg-charcoal-950 border border-charcoal-800 flex flex-col gap-1">
            <span className="text-gray-400">Lệnh Đã Đóng:</span>
            <span className="text-lg font-bold font-mono text-gray-200">
              {financial?.closed_trades ?? 0} Lệnh
            </span>
          </div>

          <div className="p-3 rounded-lg bg-charcoal-950 border border-charcoal-800 flex flex-col gap-1">
            <span className="text-gray-400">Tỷ Lệ Thắng (Winrate):</span>
            <span className="text-lg font-bold font-mono text-aurum-400">
              {financial?.closed_trades === 0 || financial?.winrate_pct == null
                ? 'N/A (0 lệnh)'
                : `${financial.winrate_pct.toFixed(1)}%`}
            </span>
          </div>

          <div className="p-3 rounded-lg bg-charcoal-950 border border-charcoal-800 flex flex-col gap-1">
            <span className="text-gray-400">Thắng / Thua / Hòa:</span>
            <span className="text-lg font-bold font-mono text-gray-300">
              {financial?.wins ?? 0} / {financial?.losses ?? 0} / {financial?.breakevens ?? 0}
            </span>
          </div>

          <div className="p-3 rounded-lg bg-charcoal-950 border border-charcoal-800 flex flex-col gap-1">
            <span className="text-gray-400">Drawdown Lớn Nhất:</span>
            <span className="text-lg font-bold font-mono text-rose-400">
              ${financial?.max_drawdown_usd?.toFixed(2) || '0.00'} ({financial?.max_drawdown_pct?.toFixed(2) || '0.00'}%)
            </span>
          </div>

          <div className="p-3 rounded-lg bg-charcoal-950 border border-charcoal-800 flex flex-col gap-1">
            <span className="text-gray-400">Vốn Hiện Tại (Equity):</span>
            <span className="text-lg font-bold font-mono text-emerald-400">
              ${financial?.current_equity_usdt != null ? financial.current_equity_usdt.toFixed(2) : 'N/A'} USDT
            </span>
          </div>
        </div>
      </div>

      {/* 6. Recent Decisions Timeline Table */}
      <div className="flex flex-col gap-3 p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 shadow-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileText className="w-4 h-4 text-aurum-400" />
            <h3 className="text-sm font-bold text-gray-200">
              Dòng Thời Gian Quyết Định Gần Nhất (Decision Timeline)
            </h3>
          </div>
          <span className="text-xs text-gray-400">
            Hiển thị tối đa 20 sự kiện quyết định mới nhất
          </span>
        </div>

        <div className="overflow-x-auto rounded-lg border border-charcoal-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-charcoal-950 text-gray-400 border-b border-charcoal-800 font-semibold uppercase text-[10px] tracking-wider">
              <tr>
                <th className="py-2.5 px-3">Thời Gian</th>
                <th className="py-2.5 px-3">Model</th>
                <th className="py-2.5 px-3">Hướng</th>
                <th className="py-2.5 px-3">Trạng Thái</th>
                <th className="py-2.5 px-3">Phân Loại</th>
                <th className="py-2.5 px-3">Lý Do Quyết Định</th>
                <th className="py-2.5 px-3 text-right">Thao Tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-charcoal-800/60 font-mono">
              {decisions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-gray-500 font-sans">
                    Chưa có sự kiện quyết định nào được ghi nhận.
                  </td>
                </tr>
              ) : (
                decisions.map((dec) => (
                  <tr key={dec.event_id} className="hover:bg-charcoal-850/50 transition">
                    <td className="py-2.5 px-3 text-gray-400 whitespace-nowrap">
                      {formatTimestamp(dec.occurred_at_ms)}
                    </td>
                    <td className="py-2.5 px-3 text-gray-200 font-semibold whitespace-nowrap">
                      {dec.model_id}
                    </td>
                    <td className="py-2.5 px-3 whitespace-nowrap">
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          dec.direction === 'LONG'
                            ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/80'
                            : 'bg-rose-950/80 text-rose-400 border border-rose-800/80'
                        }`}
                      >
                        {dec.direction}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 whitespace-nowrap font-sans">
                      <span className="text-gray-300 font-medium">
                        {dec.stage_title_vi}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 whitespace-nowrap font-sans">
                      <span className="text-[10px] bg-charcoal-800 px-2 py-0.5 rounded text-gray-300 border border-charcoal-700">
                        {dec.reason_category_title_vi}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-sans text-gray-300 max-w-md truncate" title={dec.short_message_vi}>
                      {dec.short_message_vi}
                    </td>
                    <td className="py-2.5 px-3 text-right whitespace-nowrap">
                      {dec.setup_id && onFocusSetupOnChart && (
                        <button
                          onClick={() => onFocusSetupOnChart(dec.setup_id!)}
                          className="px-2 py-1 text-[11px] font-sans font-medium rounded bg-charcoal-800 hover:bg-aurum-600 hover:text-charcoal-950 text-aurum-400 border border-aurum-500/30 transition ml-1"
                        >
                          Focus Chart
                        </button>
                      )}
                      {dec.order_id && onFocusTradeOnChart && (
                        <button
                          onClick={() => onFocusTradeOnChart(dec.order_id!)}
                          className="px-2 py-1 text-[11px] font-sans font-medium rounded bg-charcoal-800 hover:bg-emerald-600 hover:text-white text-emerald-400 border border-emerald-500/30 transition ml-1"
                        >
                          Xem Lệnh
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
