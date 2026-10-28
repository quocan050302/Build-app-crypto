import React, { useState, useEffect, useCallback } from 'react';
import {
  getOrderStopEvidence,
  getShadowMarketStatus,
  getShadowOpportunities,
  getShadowBacktests,
} from '../api/client';

export interface SLTPIntelligenceProps {
  orderId?: string;
  evidenceData?: any;
  showShadowSection?: boolean;
  shadowStatusData?: any;
  shadowOpportunitiesData?: any[];
  shadowBacktestData?: any;
}

export interface MarketShadowPanelProps {
  initialStatus?: any;
  initialOpportunities?: any[];
  initialBacktest?: any;
  autoFetch?: boolean;
}

export const MarketShadowPanel: React.FC<MarketShadowPanelProps> = ({
  initialStatus = null,
  initialOpportunities = undefined,
  initialBacktest = undefined,
  autoFetch = true,
}) => {
  const [statusData, setStatusData] = useState<any>(initialStatus);
  const [opportunities, setOpportunities] = useState<any[]>(initialOpportunities ?? []);
  const [backtestSummary, setBacktestSummary] = useState<any>(initialBacktest ?? null);
  const [loading, setLoading] = useState<boolean>(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const loadShadowData = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const [stRes, oppRes, btRes] = await Promise.all([
        getShadowMarketStatus(),
        getShadowOpportunities({ limit: 20 }),
        getShadowBacktests(5),
      ]);
      setStatusData(stRes);
      setOpportunities(Array.isArray(oppRes?.opportunities) ? oppRes.opportunities : []);
      setBacktestSummary(btRes?.latest_backtest || (Array.isArray(btRes?.backtests) && btRes.backtests[0]) || null);
    } catch (err: any) {
      setFetchError(err?.message || 'Unable to load Market Shadow telemetry');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initialStatus !== null) setStatusData(initialStatus);
  }, [initialStatus]);

  useEffect(() => {
    if (initialOpportunities !== undefined) setOpportunities(initialOpportunities);
  }, [initialOpportunities]);

  useEffect(() => {
    if (initialBacktest !== undefined) setBacktestSummary(initialBacktest);
  }, [initialBacktest]);

  useEffect(() => {
    if (!autoFetch || initialStatus !== null) return;
    loadShadowData();
  }, [autoFetch, initialStatus, loadShadowData]);

  const runtimeProfile =
    statusData?.runtime_profile?.profile ||
    statusData?.runtime_profile ||
    'MARKET_SHADOW';
  const feedStatus = statusData?.feed_status || 'DISCONNECTED';
  const lastQuoteTs = statusData?.last_quote_timestamp;
  const quoteFreshnessSec = statusData?.quote_freshness_sec;
  const spread = statusData?.spread;
  const m1Status = statusData?.m1_status?.status || statusData?.m1_status || 'EMPTY';
  const m5Status = statusData?.m5_status?.status || statusData?.m5_status || 'EMPTY';
  const m15Status = statusData?.m15_status?.status || statusData?.m15_status || 'EMPTY';
  const lastCycle = statusData?.last_completed_analysis_cycle;
  const counts = statusData?.summary_counts || {};
  const detectedSetups = counts.detected_setups ?? opportunities.length ?? 0;
  const confirmedUnicorns = counts.confirmed_unicorn_opportunities ?? 0;
  const eligibleCount = counts.eligible_opportunities ?? 0;
  const rejectedCount = counts.rejected_opportunities ?? 0;

  const btAvailable = Boolean(backtestSummary && backtestSummary.available !== false && backtestSummary.backtest_verdict === 'REAL_HISTORICAL_BACKTEST_COMPLETED');
  const btVerdict = backtestSummary?.backtest_verdict || 'REAL_HISTORICAL_BACKTEST_NOT_RUN';
  const btCoverage = backtestSummary?.dataset_coverage || null;

  return (
    <section
      data-testid="market-shadow-panel"
      style={{
        contentVisibility: 'auto',
        containIntrinsicSize: 'auto 320px auto 480px',
      }}
      className="rounded-xl border border-indigo-900/50 bg-slate-950/90 p-4 text-xs text-slate-200 space-y-4 shadow-md"
    >
      {/* Top Banner & Disclosure */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-bold text-indigo-300">
            V17.8.0.3 Market Shadow & Continuous Unicorn Intelligence
          </h3>
          <span
            data-testid="shadow-runtime-profile"
            className="rounded bg-indigo-500/20 px-2 py-0.5 text-[10px] font-mono font-bold text-indigo-300 border border-indigo-500/40"
          >
            {String(runtimeProfile)}
          </span>
          <span
            data-testid="shadow-research-only-badge"
            className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-mono font-bold text-amber-300 border border-amber-500/40"
          >
            RESEARCH ONLY — NO ORDER EXECUTED
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span
            data-testid="shadow-trading-controls-badge"
            className="rounded bg-rose-950/70 px-2 py-0.5 text-[10px] font-mono text-rose-300 border border-rose-800/60"
          >
            TRADING_CONTROLS_DISABLED (ZERO ORDER EXECUTION)
          </span>
          {autoFetch && (
            <button
              type="button"
              data-testid="shadow-refresh-btn"
              onClick={loadShadowData}
              disabled={loading}
              className="rounded bg-slate-800 hover:bg-slate-700 px-2.5 py-1 text-[10px] font-medium text-slate-200 border border-slate-700"
            >
              {loading ? 'Refreshing...' : 'Refresh Shadow'}
            </button>
          )}
        </div>
      </div>

      {fetchError && (
        <div
          data-testid="shadow-fetch-error"
          className="rounded border border-rose-800/60 bg-rose-950/30 p-2.5 text-[11px] text-rose-300"
        >
          {fetchError}
        </div>
      )}

      {/* Feed Connection, Freshness, Spread & Multi-TF Status Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2.5">
        <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5">
          <div className="text-[10px] text-slate-400">Feed Status</div>
          <div
            data-testid="shadow-feed-status"
            className={`font-mono text-xs font-bold mt-0.5 ${
              feedStatus === 'CONNECTED_FRESH'
                ? 'text-emerald-400'
                : feedStatus === 'DISCONNECTED' || feedStatus === 'DATA_GAP'
                ? 'text-rose-400'
                : 'text-amber-300'
            }`}
          >
            {feedStatus}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5">
          <div className="text-[10px] text-slate-400">Last Quote Time</div>
          <div data-testid="shadow-last-quote-time" className="font-mono text-xs text-slate-200 mt-0.5">
            {lastQuoteTs != null ? String(lastQuoteTs) : 'NO_QUOTE'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5">
          <div className="text-[10px] text-slate-400">Quote Freshness</div>
          <div data-testid="shadow-quote-freshness" className="font-mono text-xs text-slate-200 mt-0.5">
            {quoteFreshnessSec != null ? `${Number(quoteFreshnessSec).toFixed(2)}s` : 'STALE'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5">
          <div className="text-[10px] text-slate-400">Executable Spread</div>
          <div data-testid="shadow-spread" className="font-mono text-xs text-slate-200 mt-0.5">
            {spread != null ? `${Number(spread).toFixed(2)} USD` : 'N/A'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5">
          <div className="text-[10px] text-slate-400">M1 Candle Status</div>
          <div data-testid="shadow-m1-status" className="font-mono text-xs font-semibold text-slate-200 mt-0.5">
            {String(m1Status)}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5">
          <div className="text-[10px] text-slate-400">M5 Candle Status</div>
          <div data-testid="shadow-m5-status" className="font-mono text-xs font-semibold text-slate-200 mt-0.5">
            {String(m5Status)}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5">
          <div className="text-[10px] text-slate-400">M15 Candle Status</div>
          <div data-testid="shadow-m15-status" className="font-mono text-xs font-semibold text-slate-200 mt-0.5">
            {String(m15Status)}
          </div>
        </div>
      </div>

      {/* Analysis Cycle & Opportunity Funnel Counts */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div>
          <span className="text-[10px] text-slate-400 block">Last Analysis Cycle</span>
          <span data-testid="shadow-last-cycle" className="font-mono text-xs text-indigo-300 font-semibold">
            {lastCycle?.cycle_id || 'NO_CYCLE_COMPLETED'}
          </span>
        </div>
        <div>
          <span className="text-[10px] text-slate-400 block">Detected Setups</span>
          <span data-testid="shadow-detected-setups" className="font-mono text-xs text-slate-100 font-bold">
            {detectedSetups}
          </span>
        </div>
        <div>
          <span className="text-[10px] text-slate-400 block">Confirmed Unicorns</span>
          <span data-testid="shadow-confirmed-unicorns" className="font-mono text-xs text-emerald-400 font-bold">
            {confirmedUnicorns}
          </span>
        </div>
        <div>
          <span className="text-[10px] text-slate-400 block">Shadow Eligible</span>
          <span data-testid="shadow-eligible-opportunities" className="font-mono text-xs text-sky-400 font-bold">
            {eligibleCount}
          </span>
        </div>
        <div>
          <span className="text-[10px] text-slate-400 block">Shadow Rejected</span>
          <span data-testid="shadow-rejected-opportunities" className="font-mono text-xs text-amber-400 font-bold">
            {rejectedCount}
          </span>
        </div>
      </div>

      {/* Shadow Opportunities Table (Accessible region + sticky headers per modern-web-guidance responsive-table) */}
      <div
        tabIndex={0}
        role="region"
        aria-labelledby="shadow-opportunities-caption"
        className="overflow-x-auto rounded-lg border border-slate-800 max-h-72"
      >
        <table className="w-full text-left text-[11px] border-separate border-spacing-0">
          <caption
            id="shadow-opportunities-caption"
            className="bg-slate-900/90 px-3 py-2 text-left text-xs font-semibold text-slate-200 border-b border-slate-800"
          >
            Shadow Opportunities & Unicorn Structural SL/TP Candidates (Research Only)
          </caption>
          <thead className="sticky top-0 z-10 bg-slate-950 text-slate-400 uppercase text-[10px]">
            <tr>
              <th scope="col" className="py-2 px-2.5 border-b border-slate-800">Opportunity</th>
              <th scope="col" className="py-2 px-2.5 border-b border-slate-800">Model / Dir</th>
              <th scope="col" className="py-2 px-2.5 border-b border-slate-800">Unicorn Status</th>
              <th scope="col" className="py-2 px-2.5 border-b border-slate-800">Structural SL</th>
              <th scope="col" className="py-2 px-2.5 border-b border-slate-800">Independent TP</th>
              <th scope="col" className="py-2 px-2.5 border-b border-slate-800">Net R:R</th>
              <th scope="col" className="py-2 px-2.5 border-b border-slate-800">Research Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-mono">
            {opportunities.length === 0 ? (
              <tr>
                <td colSpan={7} data-testid="shadow-opportunities-empty" className="py-4 px-3 text-center text-slate-400 font-sans">
                  No shadow opportunities recorded yet. Waiting for closed M1/M5/M15 candles.
                </td>
              </tr>
            ) : (
              opportunities.map((opp) => {
                const effSl = opp.unicorn_sl_candidate ?? opp.structural_sl ?? opp.legacy_sl;
                return (
                  <tr
                    key={opp.opportunity_id}
                    data-testid={`shadow-opportunity-row-${opp.opportunity_id}`}
                    className="hover:bg-slate-900/60"
                  >
                    <th scope="row" className="sticky left-0 bg-slate-950 py-2 px-2.5 font-semibold text-slate-200">
                      {opp.opportunity_id}
                    </th>
                    <td className="py-2 px-2.5 text-slate-300">
                      {opp.model_id} · <span className="font-bold">{opp.direction}</span>
                    </td>
                    <td data-testid="shadow-opp-unicorn-validity" className="py-2 px-2.5 text-emerald-300">
                      {opp.unicorn_validity || 'NOT_EVALUATED'}
                      {opp.source_breaker_id ? ` (${opp.source_breaker_id})` : ''}
                    </td>
                    <td data-testid="shadow-opp-structural-sl" className="py-2 px-2.5 text-rose-300">
                      {effSl != null ? Number(effSl).toFixed(2) : 'NONE'}
                    </td>
                    <td data-testid="shadow-opp-tp" className="py-2 px-2.5 text-emerald-400">
                      {opp.target_price != null ? Number(opp.target_price).toFixed(2) : 'NO_VALID_TARGET'}
                    </td>
                    <td data-testid="shadow-opp-net-rr" className="py-2 px-2.5 text-sky-300">
                      {opp.net_rr != null ? `${Number(opp.net_rr).toFixed(2)}R` : 'N/A'}
                    </td>
                    <td className="py-2 px-2.5">
                      <span
                        data-testid="shadow-opp-eligibility"
                        className="mr-1.5 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-200"
                      >
                        {opp.shadow_eligibility_status}
                      </span>
                      <span
                        data-testid="shadow-opp-research-badge"
                        className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[9px] text-amber-300 border border-amber-500/30"
                      >
                        {opp.research_disclosure || 'RESEARCH ONLY — NO ORDER EXECUTED'}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Real Historical Backtest Summary Section */}
      <div
        data-testid="shadow-backtest-section"
        className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 space-y-2"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-slate-200">Historical Backtest Evidence (A / B / C / D)</span>
            <span
              data-testid="shadow-backtest-availability"
              className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-bold ${
                btAvailable
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                  : 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
              }`}
            >
              {btAvailable ? 'BACKTEST_AVAILABLE' : 'MISSING_BACKTEST_DATA'}
            </span>
          </div>
          <span
            data-testid="shadow-backtest-verdict"
            className="font-mono text-[11px] font-bold text-indigo-300"
          >
            {btVerdict}
          </span>
        </div>

        {btAvailable && btCoverage ? (
          <div data-testid="shadow-dataset-coverage" className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] font-mono text-slate-300">
            <div>M1 Bars: <span className="text-slate-100 font-bold">{btCoverage.m1_bars_count ?? 0}</span></div>
            <div>M5 Bars: <span className="text-slate-100 font-bold">{btCoverage.m5_bars_count ?? 0}</span></div>
            <div>M15 Bars: <span className="text-slate-100 font-bold">{btCoverage.m15_bars_count ?? 0}</span></div>
            <div>Missing Intervals: <span className="text-amber-300 font-bold">{btCoverage.missing_intervals_count ?? 0}</span></div>
          </div>
        ) : (
          <div data-testid="shadow-backtest-missing-msg" className="text-[11px] text-slate-400">
            No completed historical backtest dataset loaded ({btVerdict}).
          </div>
        )}
      </div>
    </section>
  );
};

const extractMetricValue = (raw: any): number | null => {
  if (raw == null) return null;
  if (typeof raw === 'number' && !Number.isNaN(raw)) return raw;
  if (typeof raw === 'object' && raw.value != null && !Number.isNaN(Number(raw.value))) {
    return Number(raw.value);
  }
  const parsed = Number(raw);
  return Number.isNaN(parsed) ? null : parsed;
};

export const SLTPIntelligenceView: React.FC<SLTPIntelligenceProps> = ({
  orderId,
  evidenceData,
  showShadowSection = false,
  shadowStatusData = null,
  shadowOpportunitiesData = undefined,
  shadowBacktestData = undefined,
}) => {
  const [data, setData] = useState<any>(evidenceData || null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (evidenceData) {
      setData(evidenceData);
      return;
    }
    if (!orderId) {
      return;
    }
    let isMounted = true;
    setIsLoading(true);
    setError(null);
    getOrderStopEvidence(orderId)
      .then((res) => {
        if (isMounted) {
          setData(res);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (isMounted) {
          setError(err?.message || 'Không thể tải bằng chứng dừng lỗ');
          setIsLoading(false);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [orderId, evidenceData]);

  if (isLoading) {
    return (
      <div
        data-testid="sltp-intelligence-loading"
        className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400"
      >
        Đang tải thông tin SL/TP Intelligence...
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="sltp-intelligence-error"
        className="rounded-xl border border-rose-900/40 bg-rose-950/20 p-4 text-xs text-rose-300"
      >
        <div className="font-semibold mb-1">Lỗi tải dữ liệu</div>
        <div>{error}</div>
      </div>
    );
  }

  if (!data) {
    if (showShadowSection || shadowStatusData != null) {
      return (
        <MarketShadowPanel
          initialStatus={shadowStatusData}
          initialOpportunities={shadowOpportunitiesData}
          initialBacktest={shadowBacktestData}
        />
      );
    }
    return (
      <div
        data-testid="sltp-intelligence-empty"
        className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-xs text-slate-500"
      >
        Chọn lệnh hoặc cơ hội để xem chi tiết SL/TP Intelligence.
      </div>
    );
  }

  if (data.status === 'UNAVAILABLE_LEGACY') {
    return (
      <div
        data-testid="sltp-intelligence-legacy"
        className="rounded-xl border border-amber-900/30 bg-amber-950/10 p-4 text-xs text-amber-300/80"
      >
        <div className="flex items-center gap-2 font-medium">
          <span
            data-testid="sltp-legacy-status-badge"
            className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-mono text-amber-300"
          >
            UNAVAILABLE_LEGACY
          </span>
          <span
            data-testid="sltp-not-recorded-badge"
            className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-300"
          >
            NOT_RECORDED
          </span>
          <span>Lệnh lịch sử không có bản ghi SL cấu trúc (Legacy FVG).</span>
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          Lệnh {data.order_id || orderId} không có dữ liệu V17.8 Stop Intelligence (Chế độ: {data.stop_mode_at_arm || 'NOT_RECORDED'} · Biến thể: {data.applied_variant || 'NOT_RECORDED'}).
        </p>
      </div>
    );
  }

  const stopPlacement = data.stop_placement || {};
  const volCtx = stopPlacement.volatility_context || {};
  const anchor = stopPlacement.anchor || {};
  const buffer = stopPlacement.buffer || {};
  const metrics = stopPlacement.metrics || {};
  const unicorn = data.unicorn_evidence || stopPlacement.unicorn_evidence || null;
  const isUnicornValid = Boolean(
    unicorn &&
      unicorn.is_valid === true &&
      (unicorn.validity_status === 'CONFIRMED' || unicorn.validity_status === 'VALID')
  );

  // Never fabricate applied_variant or stop_mode_at_arm when missing
  const stopModeAtArm = data.stop_mode_at_arm || stopPlacement.mode || 'NOT_RECORDED';
  const appliedVariant = data.applied_variant || stopPlacement.applied_variant || 'NOT_RECORDED';
  const direction = data.direction || 'UNKNOWN';
  const executedSl = data.executed_sl ?? data.actual_sl ?? null;
  const actualEntry = data.actual_entry ?? data.planned_entry ?? null;
  const takeProfit = data.take_profit ?? null;
  const minProtectiveDist =
    data.min_protective_distance ??
    buffer.buffer_price ??
    null;

  const atrM1Val = extractMetricValue(volCtx.atr_m1);
  const atrM5Val = extractMetricValue(volCtx.atr_m5);
  const spreadVal =
    extractMetricValue(volCtx.spread) ??
    extractMetricValue(volCtx.spread_snapshot?.spread);

  const quantityVal = extractMetricValue(data.quantity);
  const initialRiskVal =
    extractMetricValue(data.initial_risk_usdt) ??
    extractMetricValue(metrics.risk_usdt);
  const netRrVal =
    extractMetricValue(data.estimated_net_rr) ??
    extractMetricValue(metrics.net_rr);

  const invalidationLevel =
    (isUnicornValid ? extractMetricValue(unicorn?.invalidation_price) : null) ??
    extractMetricValue(anchor?.price);

  const isStructuralExecuted =
    appliedVariant === 'C' && stopModeAtArm === 'PAPER_STRUCTURAL';

  return (
    <div
      data-testid="sltp-intelligence-view"
      className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 shadow-sm text-xs space-y-4"
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-slate-800 pb-3 gap-2">
        <div className="flex items-center gap-2">
          <h4 className="font-semibold text-slate-100 flex items-center gap-1.5">
            <span>SL/TP Intelligence</span>
            <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-mono text-indigo-300 border border-indigo-500/30">
              V17.8.0.1
            </span>
          </h4>
          <span
            data-testid="sltp-direction-badge"
            className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-300"
          >
            {direction}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-400">Chế độ tại ARM:</span>
          <span
            data-testid="sltp-mode-badge"
            className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-200"
          >
            {stopModeAtArm}
          </span>
          <span className="text-[11px] text-slate-400">Biến thể thực thi:</span>
          <span
            data-testid="sltp-applied-variant-badge"
            className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-amber-300"
          >
            {appliedVariant}
          </span>
        </div>
      </div>

      {/* Primary Execution vs Protection Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Giá vào (Entry)</div>
          <div
            data-testid="sltp-entry-price"
            className="font-mono text-sm font-semibold text-slate-100 mt-0.5"
          >
            {actualEntry != null ? Number(actualEntry).toFixed(2) : 'NOT_RECORDED'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Stop Loss thực thi (Executed SL)</div>
          <div
            data-testid="sltp-executed-sl"
            className="font-mono text-sm font-semibold text-rose-400 mt-0.5"
          >
            {executedSl != null ? Number(executedSl).toFixed(2) : 'NOT_RECORDED'}
          </div>
          <div className="text-[9px] text-slate-400 mt-0.5">
            Biến thể áp dụng: <span className="font-mono">{appliedVariant}</span>
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Take Profit (Mục tiêu)</div>
          <div
            data-testid="sltp-take-profit"
            className="font-mono text-sm font-semibold text-emerald-400 mt-0.5"
          >
            {takeProfit != null ? Number(takeProfit).toFixed(2) : 'NOT_RECORDED'}
          </div>
          <div className="text-[9px] text-slate-500 mt-0.5">
            {stopPlacement.tp_rationale || data.tp_rationale || 'Target thanh khoản / cấu trúc'}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
          <div className="text-[10px] text-slate-400">Khoảng cách bảo vệ tối thiểu</div>
          <div
            data-testid="sltp-protective-distance"
            className="font-mono text-sm font-semibold text-amber-300 mt-0.5"
          >
            {minProtectiveDist != null ? `${Number(minProtectiveDist).toFixed(2)} USD` : 'NOT_RECORDED'}
          </div>
          {data.protective_expansion_applied && (
            <div className="text-[9px] text-amber-400 font-medium">★ Đã mở rộng bảo vệ</div>
          )}
        </div>
      </div>

      {/* Financial Authorization Summary (Quantity, Net Risk USDT, Net R:R) */}
      <div
        data-testid="sltp-financial-summary"
        className="grid grid-cols-3 gap-3 rounded-lg border border-slate-800/80 bg-slate-950/40 p-2.5 text-[11px]"
      >
        <div>
          <span className="text-slate-400 block text-[10px]">Khối lượng được duyệt (Quantity)</span>
          <span data-testid="sltp-authorized-qty" className="font-mono font-semibold text-slate-200">
            {quantityVal != null ? `${quantityVal.toFixed(2)} oz` : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          <span className="text-slate-400 block text-[10px]">Rủi ro ròng (Net Risk USDT)</span>
          <span data-testid="sltp-net-risk-usdt" className="font-mono font-semibold text-rose-300">
            {initialRiskVal != null ? `${initialRiskVal.toFixed(2)} USDT` : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          <span className="text-slate-400 block text-[10px]">Tỷ lệ R:R Ròng (Net R:R)</span>
          <span data-testid="sltp-net-rr" className="font-mono font-semibold text-emerald-300">
            {netRrVal != null ? `${netRrVal.toFixed(2)}R` : 'NOT_RECORDED'}
          </span>
        </div>
      </div>

      {/* Stop Loss Comparison Table: Executed vs Research Candidate SL */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-semibold text-slate-300">
            Đối chiếu Stop Loss Thực Thi vs Ứng Viên Nghiên Cứu (Shadow / Research)
          </span>
          <span className="text-[10px] font-mono text-slate-400">
            {isStructuralExecuted ? 'ĐANG THỰC THI CẤU TRÚC (C)' : 'CHẾ ĐỘ ĐỐI CHIẾU / LEGACY'}
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">
              A (Legacy FVG) {appliedVariant === 'A' ? '· [Đã thực thi]' : '· [Đối chiếu]'}
            </div>
            <div data-testid="sltp-variant-a-sl" className="font-mono text-xs text-slate-300 mt-1">
              {(data.legacy_sl ?? data.stop_comparison?.variant_a?.stop_loss) != null
                ? Number(data.legacy_sl ?? data.stop_comparison?.variant_a?.stop_loss).toFixed(2)
                : 'NOT_RECORDED'}
            </div>
          </div>
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">
              B/C (Cấu trúc V17.7) {isStructuralExecuted ? '· [Đã thực thi]' : '· [Ứng viên nghiên cứu]'}
            </div>
            <div data-testid="sltp-variant-b-sl" className="font-mono text-xs text-indigo-300 mt-1">
              {(data.structural_sl ?? data.stop_comparison?.variant_b?.stop_loss) != null
                ? Number(data.structural_sl ?? data.stop_comparison?.variant_b?.stop_loss).toFixed(2)
                : anchor.price != null
                ? Number(anchor.price).toFixed(2)
                : 'NOT_RECORDED'}
            </div>
          </div>
          <div className="rounded bg-slate-900/60 p-2 border border-slate-800/50">
            <div className="text-[10px] text-slate-400">
              C (Unicorn SL) {isStructuralExecuted && isUnicornValid ? '· [Đã thực thi]' : '· [Ứng viên nghiên cứu]'}
            </div>
            <div data-testid="sltp-variant-c-sl" className="font-mono text-xs text-emerald-300 mt-1">
              {(data.unicorn_sl_candidate ?? data.stop_comparison?.variant_c?.stop_loss) != null
                ? Number(data.unicorn_sl_candidate ?? data.stop_comparison?.variant_c?.stop_loss).toFixed(2)
                : 'NOT_RECORDED'}
            </div>
          </div>
        </div>
      </div>

      {/* ICT Unicorn & Provenance Status */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[11px] font-semibold text-slate-300">Cấu trúc ICT Unicorn & Nguồn Gốc Nhân Quả</span>
          <span
            data-testid="sltp-unicorn-status-badge"
            className={`rounded px-1.5 py-0.5 text-[9px] font-mono font-medium ${
              isUnicornValid
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                : 'bg-slate-800 text-slate-400'
            }`}
          >
            {isUnicornValid ? 'UNICORN_CONFIRMED' : unicorn?.validity_status || 'NO_UNICORN_CONFLUENCE'}
          </span>
        </div>
        {unicorn ? (
          <div className="grid grid-cols-2 gap-2 text-[10px] text-slate-400 mt-2">
            <div>
              Breaker ID:{' '}
              <span data-testid="sltp-breaker-id" className="font-mono text-slate-200">
                {unicorn.breaker_id || 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Selected FVG ID:{' '}
              <span data-testid="sltp-fvg-id" className="font-mono text-slate-200">
                {unicorn.selected_fvg_id || unicorn.fvg_id || 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Mức Invalidation Cấu Trúc:{' '}
              <span data-testid="sltp-invalidation-price" className="font-mono text-slate-200">
                {invalidationLevel != null ? Number(invalidationLevel).toFixed(2) : 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Vùng Giao Thoa:{' '}
              <span className="font-mono text-slate-200">
                {unicorn.overlap_low != null && unicorn.overlap_high != null
                  ? `[${Number(unicorn.overlap_low).toFixed(2)} - ${Number(unicorn.overlap_high).toFixed(2)}]`
                  : 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Episode ID:{' '}
              <span className="font-mono text-slate-200">
                {unicorn.setup_episode_id || 'NOT_RECORDED'}
              </span>
            </div>
            <div>
              Trạng thái kiểm định:{' '}
              <span className="font-mono text-slate-200">
                {unicorn.validity_status || 'NOT_RECORDED'}
              </span>
            </div>
          </div>
        ) : (
          <div className="text-[10px] text-slate-400 space-y-1">
            <div>
              Anchor Cấu Trúc:{' '}
              <span data-testid="sltp-anchor-summary" className="font-mono text-slate-200">
                {anchor.kind
                  ? `${anchor.kind} (${anchor.id || 'no-id'}) @ ${
                      invalidationLevel != null ? Number(invalidationLevel).toFixed(2) : 'NOT_RECORDED'
                    }`
                  : 'NOT_RECORDED'}
              </span>
            </div>
            <p className="text-slate-500">
              Setup không có hội tụ Breaker Block và FVG hợp lệ theo quy chuẩn Unicorn.
            </p>
          </div>
        )}
      </div>

      {/* Microstructure Noise & Volatility Context */}
      <div
        data-testid="sltp-volatility-context"
        className="grid grid-cols-3 gap-2 text-[10px] text-slate-400"
      >
        <div>
          ATR M1:{' '}
          <span data-testid="sltp-atr-m1" className="font-mono text-slate-200">
            {atrM1Val != null ? atrM1Val.toFixed(4) : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          ATR M5:{' '}
          <span data-testid="sltp-atr-m5" className="font-mono text-slate-200">
            {atrM5Val != null ? atrM5Val.toFixed(4) : 'NOT_RECORDED'}
          </span>
        </div>
        <div>
          Spread:{' '}
          <span data-testid="sltp-spread" className="font-mono text-slate-200">
            {spreadVal != null ? spreadVal.toFixed(4) : 'NOT_RECORDED'}
          </span>
        </div>
      </div>

      {/* Explanation */}
      {data.ui_explanation_vi && (
        <div
          data-testid="sltp-explanation"
          className="rounded-lg bg-indigo-950/20 border border-indigo-900/30 p-2.5 text-[11px] text-indigo-300"
        >
          {data.ui_explanation_vi}
        </div>
      )}

      {(showShadowSection || shadowStatusData != null) && (
        <MarketShadowPanel
          initialStatus={shadowStatusData}
          initialOpportunities={shadowOpportunitiesData}
          initialBacktest={shadowBacktestData}
        />
      )}
    </div>
  );
};
