import React, { useState, useEffect } from 'react';
import { api, extractErrorMessage } from './api/client';
import type {
  ScenarioRunResponse,
  ReplayRunResponse,
  StressTestResponse
} from './api/client';
import {
  FlaskConical,
  Play,
  CheckCircle2,
  Clock,
  ShieldCheck,
  ChevronDown,
  ChevronRight,
  History as HistoryIcon,
  Copy,
  Check,
  AlertTriangle
} from 'lucide-react';

interface TestingLabComponentProps {
  onNotify?: (title: string, msg: string, type: 'info' | 'warn' | 'success') => void;
}

export const TestingLabComponent: React.FC<TestingLabComponentProps> = ({ onNotify }) => {
  const [activeLabMode, setActiveLabMode] = useState<'scenarios' | 'replay' | 'stress'>('scenarios');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const handleCopyReport = (result: ScenarioRunResponse) => {
    // Deep copy and scrub sensitive tokens/secrets
    const sanitized = JSON.parse(JSON.stringify(result), (key, value) => {
      const lower = key.toLowerCase();
      if (lower.includes('token') || lower.includes('secret') || lower.includes('password') || lower.includes('api_key') || lower.includes('auth')) {
        return '[REDACTED]';
      }
      return value;
    });
    navigator.clipboard.writeText(JSON.stringify(sanitized, null, 2)).then(() => {
      setCopiedId(result.scenario_id);
      if (onNotify) {
        onNotify('Sao Chép Báo Cáo', `Đã sao chép báo cáo kịch bản ${result.scenario_id} (đã làm sạch secret)`, 'info');
      }
      setTimeout(() => setCopiedId(null), 3000);
    });
  };

  // ==================== SCENARIOS STATE ====================
  const [scenariosList, setScenariosList] = useState<{ id: string; name: string; description: string }[]>([]);
  const [scenarioResults, setScenarioResults] = useState<Record<string, ScenarioRunResponse>>({});
  const [runningScenarioId, setRunningScenarioId] = useState<string | null>(null);
  const [runningAllScenarios, setRunningAllScenarios] = useState<boolean>(false);
  const [expandedScenarioId, setExpandedScenarioId] = useState<string | null>(null);

  // ==================== REPLAY STATE ====================
  const [replayParams, setReplayParams] = useState({
    run_name: 'Backtest XAUUSDT SMC V5',
    initial_equity: 1000,
    risk_pct: 0.25,
    leverage: 5,
    spread_multiplier: 1.0,
    slippage_multiplier: 1.0,
    fee_rate: 0.0004,
    seed: 42,
    custom_dataset: false,
    custom_json: ''
  });
  const [replayResult, setReplayResult] = useState<ReplayRunResponse | null>(null);
  const [runningReplay, setRunningReplay] = useState<boolean>(false);

  // ==================== STRESS TEST STATE ====================
  const [stressResult, setStressResult] = useState<StressTestResponse | null>(null);
  const [runningStress, setRunningStress] = useState<boolean>(false);

  useEffect(() => {
    // Fetch scenario definitions
    api.getLabScenarios()
      .then((data: any) => {
        if (data?.scenarios) {
          setScenariosList(data.scenarios);
        }
      })
      .catch((err) => console.error('Failed to load lab scenarios:', err));
  }, []);

  // Run single scenario
  const handleRunScenario = async (scenarioId: string) => {
    setRunningScenarioId(scenarioId);
    try {
      const res = await api.runLabScenario(scenarioId);
      setScenarioResults((prev) => ({ ...prev, [scenarioId]: res }));
      setExpandedScenarioId(scenarioId);
      if (onNotify) {
        onNotify('Kịch Bản Hoàn Tất', `${res.name}: ${res.status}`, res.status === 'PASS' ? 'success' : 'warn');
      }
    } catch (err: any) {
      alert(extractErrorMessage(err, 'Lỗi khi chạy kịch bản'));
    } finally {
      setRunningScenarioId(null);
    }
  };

  // Run all scenarios
  const handleRunAllScenarios = async () => {
    setRunningAllScenarios(true);
    try {
      const res = await api.runAllLabScenarios();
      const mapped: Record<string, ScenarioRunResponse> = {};
      res.results.forEach((r) => {
        mapped[r.scenario_id] = r;
      });
      setScenarioResults(mapped);
      if (onNotify) {
        onNotify(
          'Đã Chạy Toàn Bộ Kịch Bản',
          `Kết quả: ${res.passed_count}/${res.total_count} kịch bản đạt yêu cầu (${res.status})`,
          res.status === 'PASS' ? 'success' : 'warn'
        );
      }
    } catch (err: any) {
      alert(extractErrorMessage(err, 'Lỗi khi chạy toàn bộ kịch bản'));
    } finally {
      setRunningAllScenarios(false);
    }
  };

  // Run historical replay
  const handleRunReplay = async () => {
    setRunningReplay(true);
    try {
      const res = await api.runLabReplay({
        run_name: replayParams.run_name,
        initial_equity: Number(replayParams.initial_equity),
        risk_pct: Number(replayParams.risk_pct),
        leverage: Number(replayParams.leverage),
        spread_multiplier: Number(replayParams.spread_multiplier),
        slippage_multiplier: Number(replayParams.slippage_multiplier),
        fee_rate: Number(replayParams.fee_rate),
        seed: Number(replayParams.seed),
        custom_candles_json: replayParams.custom_dataset && replayParams.custom_json ? replayParams.custom_json : undefined
      });
      setReplayResult(res);
      if (onNotify) {
        onNotify('Replay Hoàn Tất', `Tổng lệnh: ${res.total_trades} | Net PnL: $${res.total_net_pnl.toFixed(2)}`, res.total_net_pnl >= 0 ? 'success' : 'info');
      }
    } catch (err: any) {
      alert(extractErrorMessage(err, 'Lỗi khi chạy historical replay'));
    } finally {
      setRunningReplay(false);
    }
  };

  // Run stress test
  const handleRunStress = async () => {
    setRunningStress(true);
    try {
      const res = await api.runLabStress({
        run_name: 'Adverse Cost Matrix XAUUSDT',
        spread_multipliers: [1.0, 2.0, 3.0],
        slippage_multipliers: [1.0, 2.0, 3.0],
        fee_multipliers: [1.0, 2.0],
        latency_ms_list: [0, 500]
      });
      setStressResult(res);
      if (onNotify) {
        onNotify('Stress Test Hoàn Tất', `Đã đánh giá ${res.stress_matrix.length} ma trận rủi ro chi phí`, 'success');
      }
    } catch (err: any) {
      alert(extractErrorMessage(err, 'Lỗi khi chạy stress test'));
    } finally {
      setRunningStress(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col gap-4 overflow-y-auto pr-1">
      {/* 1. Header & Lab Mode Navigation */}
      <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow-md">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-aurum-500/10 border border-aurum-500/30 rounded-lg text-aurum-400">
              <FlaskConical className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-base font-bold text-gray-100 flex items-center gap-2">
                Phòng Kiểm Thử Rủi Ro (Isolated Risk Lab)
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                  V5 Authoritative Core
                </span>
              </h2>
              <p className="text-xs text-gray-400">
                Môi trường sandbox cô lập hoàn toàn: ReplayClock, Mock Transport, không ảnh hưởng vốn Paper hay Telegram thật.
              </p>
            </div>
          </div>

          {/* Mode Switcher Tabs */}
          <div className="flex bg-charcoal-900 p-1 rounded-lg border border-charcoal-750 text-xs">
            <button
              onClick={() => setActiveLabMode('scenarios')}
              className={`px-3 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 ${
                activeLabMode === 'scenarios'
                  ? 'bg-aurum-500 text-charcoal-950 font-bold shadow'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              <CheckCircle2 className="w-3.5 h-3.5" />
              Kịch Bản (13 Scenarios)
            </button>
            <button
              onClick={() => setActiveLabMode('replay')}
              className={`px-3 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 ${
                activeLabMode === 'replay'
                  ? 'bg-aurum-500 text-charcoal-950 font-bold shadow'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              <HistoryIcon className="w-3.5 h-3.5" />
              Replay Lịch Sử
            </button>
            <button
              onClick={() => setActiveLabMode('stress')}
              className={`px-3 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 ${
                activeLabMode === 'stress'
                  ? 'bg-aurum-500 text-charcoal-950 font-bold shadow'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              <ShieldCheck className="w-3.5 h-3.5" />
              Stress Test Ma Trận
            </button>
          </div>
        </div>
      </div>

      {/* ==================== SUB-VIEW 1: SCENARIOS ==================== */}
      {activeLabMode === 'scenarios' && (
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between bg-charcoal-850 p-3 rounded-lg border border-charcoal-750">
            <div>
              <span className="text-xs font-semibold text-gray-200">
                13 Kịch Bản Kiểm Thử Xác Định (Deterministic Scenarios):
              </span>
              <p className="text-[11px] text-gray-400">
                Kiểm chứng trực tiếp implementation thật (SHORT/LONG identity, 409 conflict, quote freshness, executable exit sides, 2-session concurrency, rollback).
              </p>
            </div>
            <button
              onClick={handleRunAllScenarios}
              disabled={runningAllScenarios}
              className="px-4 py-2 bg-gradient-to-r from-aurum-500 to-aurum-600 hover:from-aurum-400 hover:to-aurum-500 text-charcoal-950 font-bold text-xs rounded-lg shadow flex items-center gap-2 transition disabled:opacity-50"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              {runningAllScenarios ? 'Đang Chạy Kiểm Thử...' : 'Chạy Toàn Bộ 13 Kịch Bản'}
            </button>
          </div>

          <div className="grid grid-cols-1 gap-2.5">
            {scenariosList.map((sc, idx) => {
              const result = scenarioResults[sc.id];
              const isRunning = runningScenarioId === sc.id;
              const isExpanded = expandedScenarioId === sc.id;

              return (
                <div
                  key={sc.id}
                  className={`bg-charcoal-850 rounded-lg border transition ${
                    result?.status === 'PASS'
                      ? 'border-emerald-500/40 bg-emerald-950/10'
                      : result?.status === 'FAIL'
                      ? 'border-rose-500/40 bg-rose-950/10'
                      : 'border-charcoal-750'
                  }`}
                >
                  <div className="p-3 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3 flex-1">
                      <span className="w-6 h-6 rounded-full bg-charcoal-800 text-[11px] font-bold text-gray-300 flex items-center justify-center border border-charcoal-700">
                        {idx + 1}
                      </span>
                      <div>
                        <div className="text-xs font-bold text-gray-200 flex items-center gap-2">
                          {sc.name}
                          {result && (
                            <span
                              className={`text-[10px] px-2 py-0.5 rounded font-bold ${
                                result.status === 'PASS'
                                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                  : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                              }`}
                            >
                              {result.status} ({result.duration_ms}ms)
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-gray-400 mt-0.5">{sc.description}</div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleRunScenario(sc.id)}
                        disabled={isRunning}
                        className="px-3 py-1.5 bg-charcoal-800 hover:bg-charcoal-750 text-gray-200 border border-charcoal-700 text-xs rounded font-medium flex items-center gap-1.5 transition disabled:opacity-50"
                      >
                        <Play className="w-3 h-3 text-aurum-400" />
                        {isRunning ? 'Đang chạy...' : 'Chạy kịch bản'}
                      </button>
                      {result && (
                        <button
                          onClick={() => setExpandedScenarioId(isExpanded ? null : sc.id)}
                          className="p-1.5 text-gray-400 hover:text-gray-200 rounded hover:bg-charcoal-750"
                          title="Xem chi tiết các bước"
                        >
                          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Expanded Step Timeline */}
                  {isExpanded && result && (
                    <div className="p-3 border-t border-charcoal-750 bg-charcoal-900/60 text-xs flex flex-col gap-2.5">
                      <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-charcoal-750/70">
                        <div className="flex items-center gap-2 text-gray-300 text-[11px]">
                          <span className="font-semibold text-aurum-400">ID: {result.scenario_id}</span>
                          <span className="text-charcoal-600">|</span>
                          <span>Config: v{result.config_version ?? 1}</span>
                          <span className="text-charcoal-600">|</span>
                          <span>Meta: v{result.metadata_version ?? 1}</span>
                          <span className="text-charcoal-600">|</span>
                          <span>Thời gian: {result.duration_ms}ms</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleCopyReport(result)}
                          className="px-2.5 py-1 bg-charcoal-800 hover:bg-charcoal-750 text-gray-200 border border-charcoal-700 rounded text-[10px] font-medium flex items-center gap-1.5 transition"
                          title="Sao chép báo cáo JSON đã làm sạch secret"
                        >
                          {copiedId === result.scenario_id ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-400" />
                              <span className="text-emerald-400">Đã chép JSON</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3 text-aurum-400" />
                              <span>Sao Chép Báo Cáo JSON</span>
                            </>
                          )}
                        </button>
                      </div>

                      {result.status === 'FAIL' && (() => {
                        const firstFail = result.steps.find((s) => s.status === 'FAIL');
                        return (
                          <div className="p-2.5 rounded bg-rose-950/40 border border-rose-500/40 text-rose-300 text-[11px] space-y-1">
                            <div className="font-bold flex items-center gap-1.5 text-rose-400">
                              <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
                              Bước thất bại đầu tiên: Bước {firstFail?.step_index ?? '?'}: {firstFail?.name ?? 'Execution Error'}
                            </div>
                            <div className="text-gray-300">
                              <span className="font-semibold text-gray-400">Kỳ vọng:</span> {firstFail?.expected || 'N/A'}
                            </div>
                            <div className="text-rose-200">
                              <span className="font-semibold text-gray-400">Thực tế:</span> {firstFail?.actual || result.error || 'N/A'}
                            </div>
                            {result.error && (
                              <div className="text-[10px] text-gray-400 font-mono mt-1 pt-1 border-t border-rose-900/60">
                                Mã lỗi / Exception: {result.error}
                              </div>
                            )}
                          </div>
                        );
                      })()}

                      <div className="font-semibold text-gray-300 text-[11px] flex items-center gap-1.5">
                        <Clock className="w-3.5 h-3.5 text-aurum-400" />
                        Timeline Sự Kiện (Quote → Decision → Transition → Event → Outbox):
                      </div>
                      <div className="space-y-1.5 mt-0.5">
                        {result.steps.map((st) => (
                          <div
                            key={st.step_index}
                            className="p-2 rounded bg-charcoal-850/80 border border-charcoal-750/60 flex items-start justify-between gap-3 text-[11px]"
                          >
                            <div className="flex items-start gap-2">
                              <span
                                className={`px-1.5 py-0.5 rounded font-bold text-[10px] whitespace-nowrap ${
                                  st.status === 'PASS'
                                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                    : st.status === 'FAIL'
                                    ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                                    : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                                }`}
                              >
                                {st.status}
                              </span>
                              <div>
                                <span className="font-semibold text-gray-200">B{st.step_index}. {st.name}:</span>{' '}
                                <span className="text-gray-300">{st.actual}</span>
                                {st.detail && <div className="text-[10px] text-gray-400 mt-0.5">{st.detail}</div>}
                              </div>
                            </div>
                            <div className="text-right shrink-0">
                              <span className="text-[10px] text-gray-400 block">
                                Kỳ vọng: {st.expected}
                              </span>
                              {st.timestamp > 0 && (
                                <span className="text-[9px] text-gray-500 font-mono">
                                  {new Date(st.timestamp).toLocaleTimeString('vi-VN')}
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ==================== SUB-VIEW 2: HISTORICAL REPLAY ==================== */}
      {activeLabMode === 'replay' && (
        <div className="flex flex-col gap-4">
          {/* Controls & Inputs */}
          <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-750 grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
            <div>
              <label className="text-gray-400 block mb-1">Vốn Ban Đầu (USDT)</label>
              <input
                type="number"
                value={replayParams.initial_equity}
                onChange={(e) => setReplayParams({ ...replayParams, initial_equity: Number(e.target.value) })}
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-gray-100"
              />
            </div>
            <div>
              <label className="text-gray-400 block mb-1">Rủi Ro Mỗi Lệnh (%)</label>
              <input
                type="number"
                step="0.05"
                value={replayParams.risk_pct}
                onChange={(e) => setReplayParams({ ...replayParams, risk_pct: Number(e.target.value) })}
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-gray-100"
              />
            </div>
            <div>
              <label className="text-gray-400 block mb-1">Hệ Số Spread (x Spread chuẩn)</label>
              <input
                type="number"
                step="0.5"
                value={replayParams.spread_multiplier}
                onChange={(e) => setReplayParams({ ...replayParams, spread_multiplier: Number(e.target.value) })}
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-gray-100"
              />
            </div>
            <div>
              <label className="text-gray-400 block mb-1">Hệ Số Slippage (x Trượt giá)</label>
              <input
                type="number"
                step="0.5"
                value={replayParams.slippage_multiplier}
                onChange={(e) => setReplayParams({ ...replayParams, slippage_multiplier: Number(e.target.value) })}
                className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-gray-100"
              />
            </div>

            <div className="md:col-span-4 flex items-center justify-between pt-2 border-t border-charcoal-750">
              <div className="flex items-center gap-4 text-gray-300">
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={replayParams.custom_dataset}
                    onChange={(e) => setReplayParams({ ...replayParams, custom_dataset: e.target.checked })}
                    className="rounded text-aurum-500"
                  />
                  <span>Tự nạp Dataset JSON/CSV</span>
                </label>
                <span className="text-gray-400 text-[11px]">
                  Mặc định: 350 nến XAUUSDT 15M tổng hợp đa phiên (Á, Âu, Mỹ) với biến động thực tế.
                </span>
              </div>

              <button
                onClick={handleRunReplay}
                disabled={runningReplay}
                className="px-5 py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded-lg shadow flex items-center gap-2 transition disabled:opacity-50"
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                {runningReplay ? 'Đang chạy backtest...' : 'Bắt Đầu Historical Backtest'}
              </button>
            </div>

            {replayParams.custom_dataset && (
              <div className="md:col-span-4 mt-2">
                <textarea
                  value={replayParams.custom_json}
                  onChange={(e) => setReplayParams({ ...replayParams, custom_json: e.target.value })}
                  placeholder='Dán mảng nến JSON [ { "timestamp": 1788220800000, "open": 2650, "high": 2655, "low": 2648, "close": 2652, "volume": 120 } ]'
                  rows={4}
                  className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs font-mono text-gray-200"
                />
              </div>
            )}
          </div>

          {/* Replay Results Dashboard */}
          {replayResult && (
            <div className="flex flex-col gap-4">
              {/* Metrics Grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750">
                  <span className="text-[11px] text-gray-400">Net Realized PnL</span>
                  <div
                    className={`text-lg font-bold ${
                      replayResult.total_net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    ${replayResult.total_net_pnl.toFixed(2)}
                  </div>
                  <div className="text-[10px] text-gray-400 mt-1">
                    Vốn cuối: ${replayResult.final_equity.toFixed(2)}
                  </div>
                </div>

                <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750">
                  <span className="text-[11px] text-gray-400">Win Rate / Tổng Lệnh</span>
                  <div className="text-lg font-bold text-gray-100">
                    {replayResult.win_rate_pct}% ({replayResult.wins}W / {replayResult.losses}L)
                  </div>
                  <div className="text-[10px] text-gray-400 mt-1">
                    Tổng giao dịch: {replayResult.total_trades}
                  </div>
                </div>

                <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750">
                  <span className="text-[11px] text-gray-400">Profit Factor / Expectancy</span>
                  <div className="text-lg font-bold text-aurum-400">
                    {replayResult.profit_factor.toFixed(2)} / {replayResult.expectancy_r.toFixed(2)}R
                  </div>
                  <div className="text-[10px] text-gray-400 mt-1">
                    Lỗ liên tiếp max: {replayResult.max_consecutive_losses}
                  </div>
                </div>

                <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750">
                  <span className="text-[11px] text-gray-400">Max Drawdown</span>
                  <div className="text-lg font-bold text-rose-400">
                    {replayResult.max_drawdown_pct.toFixed(2)}% (${replayResult.max_drawdown_usdt.toFixed(2)})
                  </div>
                  <div className="text-[10px] text-gray-400 mt-1">
                    Vi phạm hạn mức ngày: {replayResult.loss_budget_breaches}
                  </div>
                </div>
              </div>

              {/* Session Breakdown */}
              <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750 text-xs flex items-center justify-between">
                <span className="font-semibold text-gray-300">Phân bố lệnh theo phiên giao dịch:</span>
                <div className="flex gap-4">
                  {Object.entries(replayResult.session_breakdown).map(([sess, count]) => (
                    <span key={sess} className="text-gray-400">
                      <strong className="text-gray-200">{sess}:</strong> {count} lệnh
                    </span>
                  ))}
                </div>
              </div>

              {/* Trades Log Table */}
              <div className="bg-charcoal-850 rounded-xl border border-charcoal-750 overflow-hidden shadow">
                <div className="p-3 border-b border-charcoal-750 font-bold text-xs text-gray-200 flex items-center justify-between">
                  <span>Nhật Ký Lệnh Replay ({replayResult.trades.length} lệnh đã thực thi)</span>
                  <span className="text-[11px] text-gray-400 font-normal">
                    Phí Taker: ${(replayResult.total_fees || 0).toFixed(2)} | Trượt giá: ${(replayResult.total_slippage || 0).toFixed(2)}
                  </span>
                </div>
                <div className="overflow-x-auto max-h-80">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead className="bg-charcoal-900 text-gray-400 text-[11px] uppercase tracking-wider sticky top-0">
                      <tr>
                        <th className="p-2.5">Hướng</th>
                        <th className="p-2.5">Loại</th>
                        <th className="p-2.5">Entry</th>
                        <th className="p-2.5">Exit</th>
                        <th className="p-2.5">SL / TP</th>
                        <th className="p-2.5">Nguyên Nhân Đóng</th>
                        <th className="p-2.5">Phiên</th>
                        <th className="p-2.5 text-right">Net PnL</th>
                        <th className="p-2.5 text-right">Realized R</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-charcoal-750 text-gray-300">
                      {replayResult.trades.map((t) => (
                        <tr key={t.id} className="hover:bg-charcoal-800/40">
                          <td className="p-2.5 font-bold">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] ${
                                t.direction === 'LONG'
                                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                  : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                              }`}
                            >
                              {t.direction}
                            </span>
                          </td>
                          <td className="p-2.5">{t.order_type}</td>
                          <td className="p-2.5">${t.entry_price.toFixed(2)}</td>
                          <td className="p-2.5">{t.exit_price ? `$${t.exit_price.toFixed(2)}` : '—'}</td>
                          <td className="p-2.5 text-gray-400">
                            ${t.stop_loss.toFixed(1)} / ${t.take_profit.toFixed(1)}
                          </td>
                          <td className="p-2.5">
                            <span className="text-[11px] text-gray-300 font-mono">{t.exit_cause || 'OPEN'}</span>
                            {t.is_ambiguous && (
                              <span className="ml-1 text-[9px] px-1 py-0.2 bg-amber-500/20 text-amber-400 rounded">
                                AMBIGUOUS
                              </span>
                            )}
                          </td>
                          <td className="p-2.5 text-gray-400">{t.session}</td>
                          <td
                            className={`p-2.5 text-right font-bold ${
                              t.net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                            }`}
                          >
                            ${t.net_pnl.toFixed(2)}
                          </td>
                          <td
                            className={`p-2.5 text-right font-bold ${
                              t.realized_r >= 0 ? 'text-emerald-400' : 'text-rose-400'
                            }`}
                          >
                            {t.realized_r >= 0 ? `+${t.realized_r.toFixed(2)}R` : `${t.realized_r.toFixed(2)}R`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ==================== SUB-VIEW 3: STRESS TEST ==================== */}
      {activeLabMode === 'stress' && (
        <div className="flex flex-col gap-4">
          <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-750 flex items-center justify-between">
            <div>
              <h3 className="text-xs font-bold text-gray-200">
                Đánh Giá Độ Nhạy Ma Trận Rủi Ro Chi Phí (Cost Degradation Matrix)
              </h3>
              <p className="text-[11px] text-gray-400 mt-0.5">
                Chạy kiểm thử tham số tự động: Spread (1x, 2x, 3x), Slippage (1x, 2x, 3x), Phí (1x, 2x) và Độ trễ mạng (0ms, 500ms).
              </p>
            </div>
            <button
              onClick={handleRunStress}
              disabled={runningStress}
              className="px-4 py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold text-xs rounded-lg shadow flex items-center gap-2 transition disabled:opacity-50"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              {runningStress ? 'Đang chạy stress matrix...' : 'Chạy Stress Test Ma Trận'}
            </button>
          </div>

          {stressResult && (
            <div className="bg-charcoal-850 rounded-xl border border-charcoal-750 overflow-hidden shadow">
              <div className="p-3 border-b border-charcoal-750 flex items-center justify-between text-xs font-bold text-gray-200">
                <span>Kết Quả Ma Trận Stress ({stressResult.stress_matrix.length} kịch bản kiểm thử)</span>
                <span className="text-[11px] text-emerald-400 font-normal">
                  Baseline Net PnL: ${stressResult.baseline.net_pnl.toFixed(2)} (WR: {stressResult.baseline.win_rate_pct}%)
                </span>
              </div>
              <div className="overflow-x-auto max-h-96">
                <table className="w-full text-left text-xs border-collapse">
                  <thead className="bg-charcoal-900 text-gray-400 text-[11px] uppercase tracking-wider sticky top-0">
                    <tr>
                      <th className="p-2.5">Spread</th>
                      <th className="p-2.5">Slippage</th>
                      <th className="p-2.5">Phí Taker</th>
                      <th className="p-2.5">Độ Trễ</th>
                      <th className="p-2.5 text-center">Số Lệnh</th>
                      <th className="p-2.5 text-right">Net PnL</th>
                      <th className="p-2.5 text-right">Win Rate</th>
                      <th className="p-2.5 text-right">Profit Factor</th>
                      <th className="p-2.5 text-right">Max DD</th>
                      <th className="p-2.5 text-right">Expectancy</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-charcoal-750 text-gray-300">
                    {stressResult.stress_matrix.map((row, i) => (
                      <tr key={i} className="hover:bg-charcoal-800/40">
                        <td className="p-2.5 font-bold text-aurum-400">{row.spread_mult}x</td>
                        <td className="p-2.5">{row.slippage_mult}x</td>
                        <td className="p-2.5">{row.fee_mult}x</td>
                        <td className="p-2.5 text-gray-400">{row.latency_ms}ms</td>
                        <td className="p-2.5 text-center font-semibold">{row.trades_count}</td>
                        <td
                          className={`p-2.5 text-right font-bold ${
                            row.net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          ${row.net_pnl.toFixed(2)}
                        </td>
                        <td className="p-2.5 text-right">{row.win_rate_pct}%</td>
                        <td className="p-2.5 text-right">{row.profit_factor.toFixed(2)}</td>
                        <td className="p-2.5 text-right text-rose-400">{row.max_drawdown_pct.toFixed(2)}%</td>
                        <td className="p-2.5 text-right">{row.expectancy_r.toFixed(2)}R</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
