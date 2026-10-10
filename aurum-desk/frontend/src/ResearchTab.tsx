import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { api, extractErrorMessage, type ResearchReportItem, type ReplayRunResponse } from './api/client';
import { LabJobManager } from './utils/labJobManager';
import {
  Calendar,
  Clock,
  Compass,
  TrendingUp,
  TrendingDown,
  ShieldCheck,
  FileText,
  RotateCw,
  Eye,
  Award,
  BookOpen,
  Sliders,
  ChevronDown,
  ChevronUp,
  X,
  CheckCircle2,
  HelpCircle,
  Activity,
  BarChart2,
  LayoutList
} from 'lucide-react';

const getTodayStr = () => new Date().toISOString().slice(0, 10);

function getDualTimezoneString(dateStr: string, timeStr: string, base: 'NY' | 'VN'): string {
  try {
    const [year, month, day] = dateStr.split('-').map(Number);
    const [hour, minute] = timeStr.split(':').map(Number);

    const testUtc = new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
    const nyFormatter = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hour12: false });
    const vnFormatter = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Ho_Chi_Minh', hour: 'numeric', hour12: false });
    const nyHour = parseInt(nyFormatter.format(testUtc), 10);
    const vnHour = parseInt(vnFormatter.format(testUtc), 10);
    const diffHours = (vnHour - nyHour + 24) % 24;

    const pad = (n: number) => n.toString().padStart(2, '0');
    if (base === 'NY') {
      const vnConvertedHour = (hour + diffHours) % 24;
      const vnConvertedDay = (hour + diffHours) >= 24 ? ' (hôm sau)' : '';
      return `${pad(hour)}:${pad(minute)} New York (Mỹ) · ${pad(vnConvertedHour)}:${pad(minute)} Hà Nội (VN)${vnConvertedDay}`;
    } else {
      const nyConvertedHour = (hour - diffHours + 24) % 24;
      const nyConvertedDay = (hour - diffHours) < 0 ? ' (hôm trước)' : '';
      return `${pad(hour)}:${pad(minute)} Hà Nội (VN) · ${pad(nyConvertedHour)}:${pad(minute)} New York (Mỹ)${nyConvertedDay}`;
    }
  } catch {
    return `${timeStr} (Giờ chọn)`;
  }
}

// Simple, secure text renderer for Markdown content without dangerouslySetInnerHTML
const SafeMarkdownViewer: React.FC<{ content: string }> = ({ content }) => {
  const lines = useMemo(() => content.split('\n'), [content]);

  return (
    <div className="space-y-2 text-xs text-gray-300 leading-relaxed font-sans">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={idx} className="h-1" />;
        if (trimmed.startsWith('# ')) {
          return <h2 key={idx} className="text-sm font-bold text-aurum-400 mt-3 mb-1">{trimmed.replace(/^#\s+/, '')}</h2>;
        }
        if (trimmed.startsWith('## ') || trimmed.startsWith('### ')) {
          return <h3 key={idx} className="text-xs font-bold text-gray-200 mt-2 mb-1">{trimmed.replace(/^#{2,3}\s+/, '')}</h3>;
        }
        if (trimmed.startsWith('#### ')) {
          return <h4 key={idx} className="text-xs font-semibold text-aurum-300 mt-1">{trimmed.replace(/^####\s+/, '')}</h4>;
        }
        if (trimmed === '---') {
          return <hr key={idx} className="border-charcoal-750 my-2" />;
        }
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={idx} className="flex items-start gap-1.5 ml-2">
              <span className="text-aurum-400 mt-0.5">•</span>
              <span>{trimmed.replace(/^[-*]\s+/, '')}</span>
            </div>
          );
        }
        return <p key={idx} className="text-gray-300">{trimmed}</p>;
      })}
    </div>
  );
};

interface ErrorBoundaryProps {
  children: React.ReactNode;
}
interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}
export class ResearchErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('ResearchTab ErrorBoundary caught error:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="bg-charcoal-850 p-6 rounded-xl border border-rose-500/40 text-center flex flex-col items-center gap-3 m-4">
          <p className="text-sm font-bold text-rose-400">Đã xảy ra lỗi khi hiển thị Tab Nghiên Cứu Phiên & Ngày</p>
          <p className="text-xs text-gray-400 font-mono">{this.state.error?.message}</p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="px-4 py-2 bg-charcoal-750 hover:bg-charcoal-700 text-gray-200 text-xs rounded-lg border border-charcoal-600 transition"
          >
            Thử tải lại tab
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export interface ResearchTabProps {
  onNotify?: (title: string, msg: string, type: 'info' | 'warn' | 'success') => void;
  onSessionInfoChange?: (info: any) => void;
  onOpenPlanDrawer?: () => void;
  armedPlansCount?: number;
}

const ResearchTabInner: React.FC<ResearchTabProps> = ({ onNotify, onSessionInfoChange, onOpenPlanDrawer, armedPlansCount }) => {
  // Navigation: 2 sub-views within the tab
  const [activeSubView, setActiveSubView] = useState<'daily' | 'method'>('daily');

  // ===================== 1. DAILY RESEARCH STATE =====================
  const todayStr = useMemo(() => getTodayStr(), []);
  const [selectedDate, setSelectedDate] = useState<string>(todayStr);
  const [timeOfDay, setTimeOfDay] = useState<string>('08:30');
  const [selectedSession, setSelectedSession] = useState<string>('NEW_YORK');
  const [dateBasis, setDateBasis] = useState<'VN_DATE' | 'NY_SESSION_DATE'>('VN_DATE');
  const [optionsOpen, setOptionsOpen] = useState<boolean>(false);
  const [reportsDropdownOpen, setReportsDropdownOpen] = useState<boolean>(false);

  // Data states
  const [reports, setReports] = useState<ResearchReportItem[]>([]);
  const [selectedReport, setSelectedReport] = useState<ResearchReportItem | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [generating, setGenerating] = useState<boolean>(false);

  // Post-session review state
  const [reviewData, setReviewData] = useState<any | null>(null);
  const [loadingReview, setLoadingReview] = useState<boolean>(false);

  // Detailed Analysis Drawer (default closed)
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);

  // ===================== 2. METHOD EVALUATION STATE =====================
  const [methodStartDate, setMethodStartDate] = useState<string>(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [methodEndDate, setMethodEndDate] = useState<string>(todayStr);
  const [evalInitialCapital, setEvalInitialCapital] = useState<number>(10000);
  const [evalMaxRiskPct, setEvalMaxRiskPct] = useState<number>(0.5);
  const [configPopoverOpen, setConfigPopoverOpen] = useState<boolean>(false);

  const [currentJobId, setCurrentJobId] = useState<string | null>(() => LabJobManager.getPersistedJobId());
  const [runningEval, setRunningEval] = useState<boolean>(false);
  const [jobProgressMsg, setJobProgressMsg] = useState<string | null>(null);
  const [evalResult, setEvalResult] = useState<ReplayRunResponse | null>(null);
  const [tradesDrawerOpen, setTradesDrawerOpen] = useState<boolean>(false);
  const [evalDetailsOpen, setEvalDetailsOpen] = useState<boolean>(false);

  // Load Daily Reports
  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getReports({
        research_date: selectedDate,
        date_basis: dateBasis,
        session: selectedSession !== 'ALL' ? selectedSession : undefined,
        limit: 20
      });
      setReports(res.reports || []);
            if (res.session_info) {
        onSessionInfoChange?.(res.session_info);
      }
      if (res.reports && res.reports.length > 0) {
        setSelectedReport(res.reports[0]);
      } else {
        setSelectedReport(null);
      }
      setReviewData(null);
    } catch (err) {
      if (onNotify) {
        onNotify('Lỗi Tải Báo Cáo', extractErrorMessage(err, 'Không thể tải danh sách báo cáo'), 'warn');
      }
    } finally {
      setLoading(false);
    }
  }, [selectedDate, dateBasis, selectedSession, onSessionInfoChange, onNotify]);

  useEffect(() => {
    loadReports();
  }, [loadReports]);

  // Handle generating new report
  const handleGenerateReport = async () => {
    setGenerating(true);
    setReviewData(null);
    try {
      const isToday = selectedDate === todayStr;
      const payload = {
        report_type: 'SESSION_REPORT',
        selected_date: selectedDate,
        time_of_day: timeOfDay,
        session: selectedSession,
        date_basis: dateBasis,
        mode: isToday ? 'CURRENT_ASOF' : 'HISTORICAL_ASOF'
      };

      await api.generateReport(payload);
      if (onNotify) {
        onNotify('Phân Tích Hoàn Tất', `Đã tạo báo cáo nghiên cứu cho ngày ${selectedDate} (${selectedSession})`, 'success');
      }
      await loadReports();
    } catch (err) {
      if (onNotify) {
        onNotify('Lỗi Phân Tích', extractErrorMessage(err, 'Không thể sinh báo cáo phân tích'), 'warn');
      }
    } finally {
      setGenerating(false);
    }
  };

  // Handle Post-session Review
  const handleLoadReview = async () => {
    if (!selectedReport) return;
    setLoadingReview(true);
    try {
      const review = await api.getReportReview(selectedReport.id);
      setReviewData(review);
      if (onNotify) {
        onNotify('Đánh Giá Sau Phiên', 'Đã phân tích biên độ MFE/MAE và kết quả thực tế sau phiên.', 'info');
      }
    } catch (err) {
      if (onNotify) {
        onNotify('Lỗi Đánh Giá', extractErrorMessage(err, 'Chưa thể đánh giá kết quả sau phiên'), 'warn');
      }
    } finally {
      setLoadingReview(false);
    }
  };

  // Replay Evaluation Job Tracker (Method Evaluation)
  useEffect(() => {
    const savedJobId = LabJobManager.getPersistedJobId();
    if (savedJobId && !evalResult) {
      setCurrentJobId(savedJobId);
      api.getLabJobStatus(savedJobId).then(status => {
        if (status.status === 'SUCCEEDED') {
          api.getLabJobResult(savedJobId).then(res => setEvalResult(res));
          LabJobManager.clearPersistedJobId();
          setCurrentJobId(null);
        } else if (status.status === 'FAILED' || status.status === 'CANCELLED') {
          LabJobManager.clearPersistedJobId();
          setCurrentJobId(null);
        } else {
          setJobProgressMsg(`Đang chạy: ${status.current_phase} (${status.progress_pct.toFixed(0)}%)`);
        }
      }).catch(() => {
        LabJobManager.clearPersistedJobId();
        setCurrentJobId(null);
      });
    }
  }, [evalResult]);

  const handleStartMethodEvaluation = async () => {
    setRunningEval(true);
    setJobProgressMsg('Đang khởi tạo tác vụ đánh giá...');
    try {
      const payload: any = {
        run_name: `eval_${methodStartDate}_${methodEndDate}`,
        symbol: 'XAUUSDT',
        start_date: methodStartDate,
        end_date: methodEndDate,
        initial_equity: evalInitialCapital,
        leverage: 20,
        max_risk_pct: evalMaxRiskPct,
        selected_session: 'NEW_YORK'
      };

      const job = await api.createLabJob(payload);
      const jobId = job.job_id;
      setCurrentJobId(jobId);
      LabJobManager.persistJobId(jobId);

      // Poll loop
      let completed = false;
      let polls = 0;
      while (!completed && polls < 120) {
        polls++;
        await new Promise(r => setTimeout(r, 1500));
        try {
          const status = await api.getLabJobStatus(jobId);
          setJobProgressMsg(`Giai đoạn: ${status.current_phase} (${status.progress_pct.toFixed(0)}%)`);
          if (status.status === 'SUCCEEDED') {
            completed = true;
            LabJobManager.clearPersistedJobId();
            setCurrentJobId(null);
            const res = await api.getLabJobResult(jobId);
            setEvalResult(res);
            if (onNotify) {
              onNotify('Đánh Giá Hoàn Tất', `Đã kiểm tra ${res.total_trades} lệnh | Net PnL: $${res.total_net_pnl.toFixed(2)}`, res.total_net_pnl >= 0 ? 'success' : 'info');
            }
          } else if (status.status === 'FAILED' || status.status === 'CANCELLED') {
            completed = true;
            LabJobManager.clearPersistedJobId();
            setCurrentJobId(null);
            if (onNotify) {
              onNotify('Tác Vụ Dừng', status.error_message || 'Đánh giá đã kết thúc hoặc bị hủy', 'info');
            }
          }
        } catch {
          // Retry
        }
      }
    } catch (err) {
      if (onNotify) {
        onNotify('Lỗi Đánh Giá', extractErrorMessage(err, 'Không thể khởi động đánh giá phương pháp'), 'warn');
      }
    } finally {
      setRunningEval(false);
      setJobProgressMsg(null);
    }
  };

  const handleCancelMethodEvaluation = async () => {
    if (!currentJobId) return;
    try {
      await api.cancelLabJob(currentJobId);
      LabJobManager.clearPersistedJobId();
      setCurrentJobId(null);
      setRunningEval(false);
      setJobProgressMsg(null);
      if (onNotify) onNotify('Đã Hủy Tác Vụ', 'Tác vụ đánh giá đã dừng.', 'info');
    } catch (err) {
      if (onNotify) onNotify('Lỗi Hủy', extractErrorMessage(err, 'Không thể hủy tác vụ'), 'warn');
    }
  };

  const setMethodPreset = (days: number) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - days);
    setMethodStartDate(start.toISOString().slice(0, 10));
    setMethodEndDate(end.toISOString().slice(0, 10));
  };

  // Safe Scenarios parsing for Daily View
  const scenarios = useMemo(() => {
    const raw = selectedReport?.structured_scenarios || selectedReport?.scenarios;
    if (!raw) return {};
    if (typeof raw === 'string') {
      try {
        return JSON.parse(raw);
      } catch {
        return {};
      }
    }
    return raw;
  }, [selectedReport]);

  const bullish = scenarios?.bullish;
  const bearish = scenarios?.bearish;
  const noTrade = scenarios?.no_trade;

  // Active candidate: prefer valid scenario with valid net RR
  const activeCandidate = useMemo(() => {
    if (bullish?.is_valid && (bullish?.net_rr ?? 0) >= 1.80) return bullish;
    if (bearish?.is_valid && (bearish?.net_rr ?? 0) >= 1.80) return bearish;
    return null;
  }, [bullish, bearish]);

  const isLegacyReport = Boolean(selectedReport && !selectedReport.structured_scenarios && !selectedReport.market_regime);

  // Time conversion badge
  const dualTimeDisplay = useMemo(() => {
    return getDualTimezoneString(selectedDate, timeOfDay, dateBasis === 'NY_SESSION_DATE' ? 'NY' : 'VN');
  }, [selectedDate, timeOfDay, dateBasis]);

  // Method Evaluation Conclusion Computation
  const methodConclusion = useMemo(() => {
    if (!evalResult) return null;
    const { total_trades, total_net_pnl, win_rate_pct, max_drawdown_pct } = evalResult;

    if (total_trades < 5) {
      return {
        badge: 'CHƯA ĐỦ SỐ LỆNH (MẪU QUÁ NHỎ)',
        color: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
        summary: `Chỉ ghi nhận ${total_trades} lệnh trong giai đoạn chọn. Mẫu quá nhỏ để đưa ra kết luận thống kê tin cậy.`,
        improvements: [
          {
            issue: 'Số lượng cơ hội khớp lệnh thấp',
            evidence: `Chỉ có ${total_trades} lệnh được kích hoạt trong khoảng thời gian đã chọn.`,
            suggestion: 'Mở rộng khung thời gian kiểm tra (ví dụ 3 tháng) hoặc kiểm tra các điều kiện lọc entry.'
          }
        ]
      };
    }

    const isProfitable = total_net_pnl > 0;
    const isWinRateGood = win_rate_pct >= 50;
    const isDdControlled = (max_drawdown_pct ?? 0) <= 12;

    if (isProfitable && isWinRateGood && isDdControlled) {
      return {
        badge: 'ĐẠT TIÊU CHÍ KIỂM TRA',
        color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
        summary: 'Phương pháp duy trì lợi nhuận sau phí và kiểm soát mức sụt giảm vốn (drawdown) đạt ngưỡng an toàn.',
        improvements: [
          {
            issue: 'Chi phí trượt giá & phí sàn',
            evidence: `Tổng phí và trượt giá chiếm ${(evalResult.total_fees + evalResult.total_slippage).toFixed(2)} USD.`,
            suggestion: 'Ưu tiên các cơ hội có tỷ lệ Net R:R >= 2.0R để bù đắp chi phí khớp lệnh.'
          }
        ]
      };
    }

    return {
      badge: 'CẦN CẢI THIỆN',
      color: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
      summary: 'Phương pháp chưa đạt hiệu quả tối ưu sau khi khấu trừ toàn bộ chi phí thực tế.',
      improvements: [
        {
          issue: total_net_pnl <= 0 ? 'Lợi nhuận ròng chưa dương sau phí' : 'Mức sụt giảm vốn vượt ngưỡng khuyến nghị',
          evidence: `Net PnL: ${total_net_pnl > 0 ? '+' : ''}$${total_net_pnl.toFixed(2)} | Drawdown: -${max_drawdown_pct.toFixed(1)}% | Win Rate: ${win_rate_pct.toFixed(1)}%`,
          suggestion: 'Thử nghiệm bổ sung bộ lọc xu hướng đa khung D/H1 hoặc chỉ giao dịch trong phiên Mỹ.'
        },
        {
          issue: 'Tín hiệu bị từ chối hoặc bỏ lỡ',
          evidence: `Ghi nhận ${evalResult.rejected_count ?? 0} tín hiệu bị chặn bởi các quy tắc an toàn.`,
          suggestion: 'Tránh vào lệnh sát các khung giờ công bố tin tức vĩ mô (CPI, FOMC, NFP).'
        }
      ]
    };
  }, [evalResult]);

  return (
    <div className="flex-1 flex flex-col gap-3 overflow-y-auto pr-1">
      {/* Top Header & Sub-View Switcher */}
      <div className="bg-charcoal-850 px-4 py-3 rounded-xl border border-charcoal-700 shadow-sm flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-aurum-500/10 border border-aurum-500/30 rounded-lg text-aurum-400">
            <Compass className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-gray-100 flex items-center gap-2">
              Nghiên Cứu Phiên & Ngày
              <span className="text-[10px] text-gray-400 font-mono font-normal">XAUUSDT</span>
            </h2>
            <p className="text-[11px] text-gray-400">
              {activeSubView === 'daily'
                ? 'Khảo sát bối cảnh thị trường tại thời điểm chọn, kiểm tra điều kiện vào lệnh và tỷ lệ Net R:R sau phí.'
                : 'Đánh giá hiệu quả phương pháp giao dịch sau toàn bộ chi phí thực tế qua lịch sử nhiều ngày.'}
            </p>
          </div>
        </div>

        {/* Sub-view Navigation & Plan Drawer Button */}
        <div className="flex items-center gap-2">
          {/* 2 Sub-view Pills */}
          <div className="flex items-center bg-charcoal-900 border border-charcoal-750 p-1 rounded-lg">
            <button
              onClick={() => setActiveSubView('daily')}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold flex items-center gap-1.5 transition ${
                activeSubView === 'daily'
                  ? 'bg-aurum-500 text-charcoal-950 shadow-sm'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              <Calendar className="w-3.5 h-3.5" />
              Nghiên Cứu Ngày
            </button>
            <button
              onClick={() => setActiveSubView('method')}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold flex items-center gap-1.5 transition ${
                activeSubView === 'method'
                  ? 'bg-aurum-500 text-charcoal-950 shadow-sm'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              <BarChart2 className="w-3.5 h-3.5" />
              Đánh Giá Phương Pháp
            </button>
          </div>

          {onOpenPlanDrawer && (
            <button
              id="btn-open-plans-drawer"
              onClick={onOpenPlanDrawer}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-charcoal-700 bg-charcoal-850 hover:bg-charcoal-750 text-xs font-medium text-gray-200 transition shadow-xs cursor-pointer"
              title="Mở bảng kế hoạch và quyết định lệnh"
            >
              <LayoutList className="w-3.5 h-3.5 text-aurum-400" />
              <span className="hidden sm:inline">Xem kế hoạch</span>
              {armedPlansCount !== undefined && armedPlansCount > 0 && (
                <span className="px-1.5 py-0.2 rounded-full bg-aurum-500 text-charcoal-950 font-bold text-[10px]">
                  {armedPlansCount}
                </span>
              )}
            </button>
          )}
        </div>
      </div>

      {/* ========================================================================= */}
      {/* SUB-VIEW 1: NGHIÊN CỨU NGÀY (DAILY RESEARCH)                              */}
      {/* ========================================================================= */}
      {activeSubView === 'daily' && (
        <div className="flex flex-col gap-3">
          {/* Compact Control Bar */}
          <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-700 flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              {/* Date Input */}
              <div className="flex items-center gap-1.5 bg-charcoal-900 px-2.5 py-1.5 rounded-lg border border-charcoal-750">
                <Calendar className="w-3.5 h-3.5 text-aurum-400" />
                <input
                  type="date"
                  value={selectedDate}
                  onChange={(e) => setSelectedDate(e.target.value)}
                  className="bg-transparent text-gray-200 font-mono text-xs focus:outline-none"
                />
              </div>

              {/* Quick Today/Yesterday */}
              <button
                onClick={() => setSelectedDate(todayStr)}
                className={`px-2 py-1.5 rounded text-[11px] font-medium transition ${
                  selectedDate === todayStr ? 'bg-aurum-500/20 text-aurum-300 border border-aurum-500/30' : 'bg-charcoal-900 text-gray-400 hover:text-gray-200 border border-charcoal-750'
                }`}
              >
                Hôm nay
              </button>
              <button
                onClick={() => {
                  const d = new Date();
                  d.setDate(d.getDate() - 1);
                  setSelectedDate(d.toISOString().slice(0, 10));
                }}
                className="px-2 py-1.5 rounded text-[11px] font-medium bg-charcoal-900 text-gray-400 hover:text-gray-200 border border-charcoal-750"
              >
                Hôm qua
              </button>

              {/* Session Selector (Default: Mỹ) */}
              <div className="flex items-center gap-1 bg-charcoal-900 px-2 py-1 rounded-lg border border-charcoal-750">
                <span className="text-gray-400 text-[11px]">Phiên:</span>
                <select
                  value={selectedSession}
                  onChange={(e) => setSelectedSession(e.target.value)}
                  className="bg-transparent text-gray-200 text-xs focus:outline-none font-medium"
                >
                  <option value="NEW_YORK">Phiên Mỹ (New York)</option>
                  <option value="LONDON">Phiên Âu (London)</option>
                  <option value="TOKYO">Phiên Á (Tokyo)</option>
                  <option value="ALL">Tất Cả Phiên</option>
                </select>
              </div>

              {/* Time of Day */}
              <div className="flex items-center gap-1.5 bg-charcoal-900 px-2.5 py-1.5 rounded-lg border border-charcoal-750">
                <Clock className="w-3.5 h-3.5 text-aurum-400" />
                <span className="text-gray-400 text-[11px]">Tại giờ:</span>
                <input
                  type="time"
                  value={timeOfDay}
                  onChange={(e) => setTimeOfDay(e.target.value)}
                  className="bg-transparent text-gray-200 font-mono text-xs focus:outline-none"
                />
              </div>

              {/* Converted Time Display (DST-aware) */}
              <span className="text-[11px] text-gray-400 font-mono bg-charcoal-900/60 px-2.5 py-1 rounded border border-charcoal-800 hidden md:inline">
                {dualTimeDisplay}
              </span>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center gap-2">
              {/* Options Popover Button */}
              <div className="relative">
                <button
                  onClick={() => setOptionsOpen(!optionsOpen)}
                  className="px-2.5 py-1.5 bg-charcoal-900 hover:bg-charcoal-750 text-gray-300 rounded-lg border border-charcoal-750 text-xs flex items-center gap-1 transition"
                  title="Tùy chọn nâng cao"
                >
                  <Sliders className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Tùy Chọn</span>
                </button>

                {optionsOpen && (
                  <div className="absolute right-0 top-9 w-64 bg-charcoal-900 border border-charcoal-700 rounded-xl p-3 shadow-2xl z-30 space-y-2 text-xs">
                    <div className="flex items-center justify-between border-b border-charcoal-750 pb-1.5">
                      <span className="font-bold text-gray-200">Tùy Chọn Phân Tích</span>
                      <button onClick={() => setOptionsOpen(false)} className="text-gray-400 hover:text-gray-200">
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <div>
                      <span className="text-gray-400 text-[11px] block mb-1">Quy chuẩn ngày:</span>
                      <div className="flex gap-1">
                        <button
                          onClick={() => { setDateBasis('VN_DATE'); setOptionsOpen(false); }}
                          className={`flex-1 py-1 rounded text-[11px] font-medium ${
                            dateBasis === 'VN_DATE' ? 'bg-aurum-500/20 text-aurum-300 border border-aurum-500/40 font-bold' : 'bg-charcoal-800 text-gray-400'
                          }`}
                        >
                          Giờ VN (UTC+7)
                        </button>
                        <button
                          onClick={() => { setDateBasis('NY_SESSION_DATE'); setOptionsOpen(false); }}
                          className={`flex-1 py-1 rounded text-[11px] font-medium ${
                            dateBasis === 'NY_SESSION_DATE' ? 'bg-aurum-500/20 text-aurum-300 border border-aurum-500/40 font-bold' : 'bg-charcoal-800 text-gray-400'
                          }`}
                        >
                          Phiên NY (DST)
                        </button>
                      </div>
                    </div>
                    <div className="text-[10px] text-gray-500 pt-1 border-t border-charcoal-800">
                      Khóa nhân quả: Chỉ sử dụng nến đã đóng trước thời điểm As-Of đã chọn.
                    </div>
                  </div>
                )}
              </div>

              {/* Saved Reports Dropdown */}
              {reports.length > 0 && (
                <div className="relative">
                  <button
                    onClick={() => setReportsDropdownOpen(!reportsDropdownOpen)}
                    className="px-2.5 py-1.5 bg-charcoal-900 hover:bg-charcoal-750 text-aurum-300 rounded-lg border border-charcoal-750 text-xs flex items-center gap-1 transition"
                  >
                    <BookOpen className="w-3.5 h-3.5 text-aurum-400" />
                    <span>Đã Lưu ({reports.length})</span>
                    <ChevronDown className="w-3 h-3 text-gray-400" />
                  </button>

                  {reportsDropdownOpen && (
                    <div className="absolute right-0 top-9 w-72 bg-charcoal-900 border border-charcoal-700 rounded-xl p-2 shadow-2xl z-30 max-h-60 overflow-y-auto space-y-1 text-xs">
                      <span className="text-[10px] text-gray-400 font-semibold px-2 block mb-1">Danh Sách Báo Cáo Trong Ngày:</span>
                      {reports.map((rep) => (
                        <button
                          key={rep.id}
                          onClick={() => {
                            setSelectedReport(rep);
                            setReviewData(null);
                            setReportsDropdownOpen(false);
                          }}
                          className={`w-full text-left px-2.5 py-1.5 rounded text-[11px] transition flex items-center justify-between ${
                            selectedReport?.id === rep.id
                              ? 'bg-aurum-500/20 text-aurum-300 font-bold'
                              : 'text-gray-300 hover:bg-charcoal-800'
                          }`}
                        >
                          <span>#{rep.id} · {rep.session_name}</span>
                          <span className="text-gray-500 font-mono">
                            {new Date(rep.created_at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Main Phân Tích Button */}
              <button
                onClick={handleGenerateReport}
                disabled={generating}
                className="px-4 py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded-lg shadow text-xs flex items-center gap-1.5 transition disabled:opacity-50"
              >
                <RotateCw className={`w-3.5 h-3.5 ${generating ? 'animate-spin' : ''}`} />
                {generating ? 'Đang phân tích...' : 'Phân Tích'}
              </button>
            </div>
          </div>

          {/* Loading Indicator */}
          {loading && (
            <div className="py-2 px-3 text-xs text-aurum-400 flex items-center justify-center gap-2 bg-charcoal-850 rounded-lg border border-charcoal-750">
              <RotateCw className="w-3.5 h-3.5 animate-spin" />
              Đang tải dữ liệu nghiên cứu...
            </div>
          )}

          {/* Legacy Report Notice if applicable */}
          {isLegacyReport && (
            <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-750 text-xs text-gray-400 flex items-center justify-between gap-3">
              <span>Báo cáo cũ chưa có đủ dữ liệu cho bản tóm tắt này. Bạn có thể xem nội dung cũ bên dưới hoặc bấm <strong>"Phân Tích"</strong> để lập báo cáo mới.</span>
              <button
                onClick={handleGenerateReport}
                className="px-3 py-1 bg-aurum-500/20 text-aurum-300 rounded border border-aurum-500/40 text-[11px] font-bold"
              >
                Phân tích lại
              </button>
            </div>
          )}

          {/* 3 Main Result Cards */}
          {selectedReport && !isLegacyReport ? (
            <div className="flex flex-col gap-3">
              {/* CARD A: THẺ KẾT LUẬN THỊ TRƯỜNG */}
              <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow-sm flex flex-col gap-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-charcoal-750 pb-2.5">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400">Xu hướng thị trường:</span>
                    <span className={`px-2.5 py-1 rounded text-xs font-bold ${
                      selectedReport.market_regime === 'TREND_UP'
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                        : selectedReport.market_regime === 'TREND_DOWN'
                        ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                        : selectedReport.market_regime === 'RANGE'
                        ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40'
                        : 'bg-charcoal-900 text-gray-300 border border-charcoal-700'
                    }`}>
                      {selectedReport.market_regime === 'TREND_UP' ? 'XU HƯỚNG TĂNG (BULLISH)' :
                       selectedReport.market_regime === 'TREND_DOWN' ? 'XU HƯỚNG GIẢM (BEARISH)' :
                       selectedReport.market_regime === 'RANGE' ? 'ĐI NGANG TÍCH LŨY (RANGE)' :
                       selectedReport.market_regime || 'CHƯA RÕ (CẦN XÁC NHẬN)'}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-gray-400">Dữ liệu tại As-Of:</span>
                    <span className="font-mono text-gray-200">
                      {selectedReport.as_of_ms ? new Date(selectedReport.as_of_ms).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' }) : 'Hiện tại'}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 text-[10px]">
                      {selectedReport.data_coverage_status || 'CHƯA_KIỂM_ĐỊNH'}
                    </span>
                  </div>
                </div>

                {/* Beginner Action Guidance */}
                <div className="flex items-start gap-2.5 text-xs text-gray-300 bg-charcoal-900 p-3 rounded-lg border border-charcoal-750">
                  <CheckCircle2 className="w-4 h-4 text-aurum-400 shrink-0 mt-0.5" />
                  <div>
                    <strong className="text-aurum-300 block mb-0.5">Khuyến nghị hành động:</strong>
                    <span>
                      {selectedReport.market_regime === 'TREND_UP'
                        ? 'Ưu tiên theo dõi cơ hội mua khi giá hồi về vùng hỗ trợ. Tuyệt đối không mua đuổi ở đỉnh.'
                        : selectedReport.market_regime === 'TREND_DOWN'
                        ? 'Ưu tiên theo dõi cơ hội bán khi giá kiểm định lại vùng cản trên. Tuyệt đối không bán tháo tại đáy.'
                        : selectedReport.market_regime === 'RANGE'
                        ? 'Giá đang đi ngang tích lũy. Kiên nhẫn chờ giá bứt phá và kiểm tra lại trước khi vào lệnh.'
                        : 'Thị trường chưa có tín hiệu rõ ràng. Đứng ngoài chờ đợi là quyết định bảo vệ vốn tốt nhất.'}
                    </span>
                  </div>
                </div>

                {/* 3 Core Bullet Reasons */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px] text-gray-400 pt-1">
                  <div className="bg-charcoal-900 px-2.5 py-1.5 rounded border border-charcoal-750">
                    <span className="text-gray-500 block text-[10px]">1. Xu hướng đa khung:</span>
                    <span className="font-medium text-gray-200">D: {selectedReport.d_4h_bias || 'Chưa rõ'} · H1: {selectedReport.h1_alignment || 'Chưa rõ'}</span>
                  </div>
                  <div className="bg-charcoal-900 px-2.5 py-1.5 rounded border border-charcoal-750">
                    <span className="text-gray-500 block text-[10px]">2. Vùng cân bằng:</span>
                    <span className="font-medium text-gray-200">Tôn trọng cấu trúc Swing High/Low</span>
                  </div>
                  <div className="bg-charcoal-900 px-2.5 py-1.5 rounded border border-charcoal-750">
                    <span className="text-gray-500 block text-[10px]">3. Kiểm định chi phí:</span>
                    <span className="font-medium text-gray-200">Đã trừ spread $0.20 & trượt giá $0.10</span>
                  </div>
                </div>
              </div>

              {/* CARD B: KỊCH BẢN CÓ DỮ LIỆU HOẶC EMPTY STATE */}
              {activeCandidate ? (
                <div className="bg-charcoal-850 p-4 rounded-xl border border-aurum-500/40 shadow-sm flex flex-col gap-3">
                  <div className="flex items-center justify-between border-b border-charcoal-750 pb-2.5">
                    <span className="text-xs font-bold text-gray-100 flex items-center gap-1.5">
                      {activeCandidate.direction === 'LONG' ? <TrendingUp className="w-4 h-4 text-emerald-400" /> : <TrendingDown className="w-4 h-4 text-rose-400" />}
                      {activeCandidate.title || `Kịch Bản ${activeCandidate.direction}`}
                    </span>
                    <span className="px-2.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">
                      {activeCandidate.setup_state_text || 'Đang chờ điều kiện vào lệnh'}
                    </span>
                  </div>

                  {/* 4 Numbers Grid */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                    <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                      <span className="text-[10px] text-gray-500 block">Vùng Vào Lệnh (Entry):</span>
                      <strong className="text-gray-100 text-sm font-mono mt-0.5 block">{activeCandidate.entry_zone || `$${activeCandidate.planned_entry}`}</strong>
                    </div>
                    <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                      <span className="text-[10px] text-gray-500 block">Dừng Lỗ (SL):</span>
                      <strong className="text-rose-400 text-sm font-mono mt-0.5 block">${activeCandidate.stop_loss}</strong>
                    </div>
                    <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                      <span className="text-[10px] text-gray-500 block">Chốt Lời (TP):</span>
                      <strong className="text-emerald-400 text-sm font-mono mt-0.5 block">
                        {activeCandidate.take_profit ? `$${activeCandidate.take_profit}` : 'Chưa có'}
                      </strong>
                    </div>
                    <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                      <span className="text-[10px] text-gray-500 block">Net R:R Sau Phí:</span>
                      <strong className="text-aurum-400 text-sm font-mono mt-0.5 block">
                        {activeCandidate.net_rr ? `${activeCandidate.net_rr}R` : 'Chưa đủ'}
                      </strong>
                    </div>
                  </div>

                  {/* Missing Condition Notice */}
                  <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750 text-xs text-gray-300 flex items-start gap-2">
                    <HelpCircle className="w-4 h-4 text-aurum-400 shrink-0 mt-0.5" />
                    <div>
                      <strong className="text-gray-200">Điều kiện còn thiếu để vào lệnh: </strong>
                      <span>{activeCandidate.missing_condition || activeCandidate.trigger_condition}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="bg-charcoal-850 p-6 rounded-xl border border-charcoal-700 shadow-sm text-center flex flex-col items-center gap-2">
                  <ShieldCheck className="w-8 h-8 text-aurum-400" />
                  <h3 className="text-xs font-bold text-gray-200">Chưa có cơ hội đủ điều kiện tại thời điểm này</h3>
                  <p className="text-xs text-gray-400 max-w-md">
                    {noTrade?.capital_preservation_message || 'Cả kịch bản Mua và Bán đều chưa đạt Net R:R tối thiểu 1.80R hoặc thị trường chưa có tín hiệu nến xác nhận. Bảo toàn vốn là ưu tiên số một.'}
                  </p>
                </div>
              )}

              {/* CARD C: POST-SESSION OUTCOME (OPT-IN) */}
              <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-750 flex flex-col gap-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-gray-300 flex items-center gap-1.5">
                    <Award className="w-4 h-4 text-aurum-400" />
                    Đánh Giá Diễn Biến Sau Phiên (MFE / MAE)
                  </span>
                  <button
                    onClick={handleLoadReview}
                    disabled={loadingReview}
                    className="px-3 py-1 bg-charcoal-750 hover:bg-charcoal-700 text-aurum-300 rounded border border-charcoal-650 text-xs flex items-center gap-1.5 transition"
                  >
                    <Eye className="w-3.5 h-3.5" />
                    {loadingReview ? 'Đang đánh giá...' : reviewData ? 'Cập Nhật Lại' : 'Xem Lại Diễn Biến Sau Phiên'}
                  </button>
                </div>

                {reviewData && (
                  <div className="space-y-2 pt-2 border-t border-charcoal-750 text-xs">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <div className="bg-charcoal-900 p-2.5 rounded border border-charcoal-750">
                        <span className="text-[10px] text-gray-500 block">Kịch bản Mua (Long) sau phiên:</span>
                        <div className="text-gray-200 font-medium mt-0.5">{reviewData.bullish_review?.outcome}</div>
                        {reviewData.bullish_review?.is_filled && (
                          <div className="text-[11px] text-gray-400 mt-1 font-mono">
                            MFE: +${reviewData.bullish_review?.mfe_usd} (+{reviewData.bullish_review?.mfe_r}R) · MAE: -${reviewData.bullish_review?.mae_usd} (-{reviewData.bullish_review?.mae_r}R)
                          </div>
                        )}
                      </div>
                      <div className="bg-charcoal-900 p-2.5 rounded border border-charcoal-750">
                        <span className="text-[10px] text-gray-500 block">Kịch bản Bán (Short) sau phiên:</span>
                        <div className="text-gray-200 font-medium mt-0.5">{reviewData.bearish_review?.outcome}</div>
                        {reviewData.bearish_review?.is_filled && (
                          <div className="text-[11px] text-gray-400 mt-1 font-mono">
                            MFE: +${reviewData.bearish_review?.mfe_usd} (+{reviewData.bearish_review?.mfe_r}R) · MAE: -${reviewData.bearish_review?.mae_usd} (-{reviewData.bearish_review?.mae_r}R)
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* CARD D: NÚT XEM PHÂN TÍCH CHI TIẾT (DRAWER/ACCORDION - MẶC ĐỊNH ĐÓNG) */}
              <div className="border border-charcoal-750 rounded-xl overflow-hidden bg-charcoal-850">
                <button
                  onClick={() => setDetailsOpen(!detailsOpen)}
                  className="w-full px-4 py-2.5 text-left flex items-center justify-between text-xs font-semibold text-gray-300 hover:text-white hover:bg-charcoal-800 transition"
                >
                  <span className="flex items-center gap-2">
                    <FileText className="w-4 h-4 text-aurum-400" />
                    Xem Toàn Bộ Phân Tích Kỹ Thuật Chi Tiết (Đa Khung, Cấu Trúc, Báo Cáo Gốc)
                  </span>
                  {detailsOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                </button>

                {detailsOpen && (
                  <div className="p-4 border-t border-charcoal-750 bg-charcoal-900/50 space-y-4">
                    {/* Timeframe Matrix */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750 text-xs">
                        <span className="text-[10px] text-gray-500 block">Khung Ngày (D)</span>
                        <span className="font-bold text-gray-200">{selectedReport.d_4h_bias || 'Chưa rõ'}</span>
                      </div>
                      <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750 text-xs">
                        <span className="text-[10px] text-gray-500 block">Đồng Thuận 1H</span>
                        <span className="font-bold text-gray-200">{selectedReport.h1_alignment || 'Chưa rõ'}</span>
                      </div>
                      <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750 text-xs">
                        <span className="text-[10px] text-gray-500 block">Phiên Giao Dịch</span>
                        <span className="font-bold text-aurum-400">{selectedReport.session_name}</span>
                      </div>
                      <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750 text-xs">
                        <span className="text-[10px] text-gray-500 block">Phiên Bản</span>
                        <span className="font-bold text-gray-200">{selectedReport.strategy_version}</span>
                      </div>
                    </div>

                    {/* Markdown Report Narrative */}
                    <div className="bg-charcoal-900 p-3 rounded-lg border border-charcoal-750">
                      <SafeMarkdownViewer content={selectedReport.content_markdown || ''} />
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            !loading && (
              <div className="bg-charcoal-850 p-8 rounded-xl border border-charcoal-700 text-center text-gray-400 text-xs">
                <Compass className="w-8 h-8 text-gray-500 mx-auto mb-2" />
                <p>Chưa có báo cáo nghiên cứu cho ngày <strong>{selectedDate}</strong> ({selectedSession}).</p>
                <p className="mt-1 text-gray-500">Bấm nút <strong>"Phân Tích"</strong> ở trên để tạo báo cáo tại thời điểm đã chọn.</p>
              </div>
            )
          )}
        </div>
      )}

      {/* ========================================================================= */}
      {/* SUB-VIEW 2: ĐÁNH GIÁ PHƯƠNG PHÁP (METHOD EVALUATION)                      */}
      {/* ========================================================================= */}
      {activeSubView === 'method' && (
        <div className="flex flex-col gap-3">
          {/* Method Evaluation Control Bar */}
          <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-700 flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1.5 bg-charcoal-900 px-2.5 py-1.5 rounded-lg border border-charcoal-750">
                <span className="text-gray-400 text-[11px]">Từ ngày:</span>
                <input
                  type="date"
                  value={methodStartDate}
                  onChange={(e) => setMethodStartDate(e.target.value)}
                  className="bg-transparent text-gray-200 font-mono text-xs focus:outline-none"
                />
              </div>

              <div className="flex items-center gap-1.5 bg-charcoal-900 px-2.5 py-1.5 rounded-lg border border-charcoal-750">
                <span className="text-gray-400 text-[11px]">Đến ngày:</span>
                <input
                  type="date"
                  value={methodEndDate}
                  onChange={(e) => setMethodEndDate(e.target.value)}
                  className="bg-transparent text-gray-200 font-mono text-xs focus:outline-none"
                />
              </div>

              {/* Presets */}
              <button
                onClick={() => setMethodPreset(7)}
                className="px-2 py-1.5 rounded text-[11px] font-medium bg-charcoal-900 hover:bg-charcoal-750 text-gray-300 border border-charcoal-750"
              >
                7 ngày
              </button>
              <button
                onClick={() => setMethodPreset(30)}
                className="px-2 py-1.5 rounded text-[11px] font-medium bg-charcoal-900 hover:bg-charcoal-750 text-gray-300 border border-charcoal-750"
              >
                30 ngày
              </button>
              <button
                onClick={() => setMethodPreset(90)}
                className="px-2 py-1.5 rounded text-[11px] font-medium bg-charcoal-900 hover:bg-charcoal-750 text-gray-300 border border-charcoal-750"
              >
                3 tháng
              </button>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2">
              {/* Config Popover */}
              <div className="relative">
                <button
                  onClick={() => setConfigPopoverOpen(!configPopoverOpen)}
                  className="px-2.5 py-1.5 bg-charcoal-900 hover:bg-charcoal-750 text-gray-300 rounded-lg border border-charcoal-750 text-xs flex items-center gap-1 transition"
                >
                  <Sliders className="w-3.5 h-3.5" />
                  <span>Cấu Hình</span>
                </button>

                {configPopoverOpen && (
                  <div className="absolute right-0 top-9 w-64 bg-charcoal-900 border border-charcoal-700 rounded-xl p-3 shadow-2xl z-30 space-y-2 text-xs">
                    <span className="font-bold text-gray-200 block border-b border-charcoal-750 pb-1">Cấu Hình Đánh Giá</span>
                    <div>
                      <span className="text-gray-400 text-[10px] block">Vốn ban đầu ($):</span>
                      <input
                        type="number"
                        value={evalInitialCapital}
                        onChange={(e) => setEvalInitialCapital(Number(e.target.value))}
                        className="w-full bg-charcoal-800 border border-charcoal-700 rounded px-2 py-1 text-gray-100 text-xs mt-0.5"
                      />
                    </div>
                    <div>
                      <span className="text-gray-400 text-[10px] block">Rủi ro / lệnh (%):</span>
                      <input
                        type="number"
                        step="0.1"
                        value={evalMaxRiskPct}
                        onChange={(e) => setEvalMaxRiskPct(Number(e.target.value))}
                        className="w-full bg-charcoal-800 border border-charcoal-700 rounded px-2 py-1 text-gray-100 text-xs mt-0.5"
                      />
                    </div>
                    <div className="text-[10px] text-gray-500 pt-1 border-t border-charcoal-800">
                      *Cấu hình này độc lập trong môi trường nghiên cứu, không ghi đè cài đặt giao dịch live.
                    </div>
                  </div>
                )}
              </div>

              {runningEval ? (
                <button
                  onClick={handleCancelMethodEvaluation}
                  className="px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white font-bold rounded-lg text-xs transition"
                >
                  Hủy Tác Vụ
                </button>
              ) : (
                <button
                  onClick={handleStartMethodEvaluation}
                  className="px-4 py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded-lg shadow text-xs flex items-center gap-1.5 transition"
                >
                  <Activity className="w-3.5 h-3.5" />
                  Chạy Đánh Giá
                </button>
              )}
            </div>
          </div>

          {/* Progress / Status Tracker */}
          {runningEval && (
            <div className="bg-charcoal-850 p-3 rounded-xl border border-aurum-500/40 text-xs text-aurum-300 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <RotateCw className="w-4 h-4 animate-spin text-aurum-400" />
                <span>{jobProgressMsg || 'Đang thực thi mô phỏng replay trên dữ liệu lịch sử...'}</span>
              </div>
              <button
                onClick={handleCancelMethodEvaluation}
                className="text-gray-400 hover:text-rose-400 text-xs underline"
              >
                Dừng lại
              </button>
            </div>
          )}

          {/* Evaluation Results */}
          {evalResult ? (
            <div className="flex flex-col gap-3">
              {/* CARD A: THẺ KẾT LUẬN PHƯƠNG PHÁP */}
              {methodConclusion && (
                <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow-sm flex flex-col gap-2.5">
                  <div className="flex items-center justify-between border-b border-charcoal-750 pb-2.5">
                    <span className="text-xs text-gray-400">Kết luận kiểm tra phương pháp:</span>
                    <span className={`px-2.5 py-1 rounded text-xs font-bold border ${methodConclusion.color}`}>
                      {methodConclusion.badge}
                    </span>
                  </div>

                  <p className="text-xs text-gray-200">{methodConclusion.summary}</p>

                  <div className="text-[11px] text-gray-400 font-mono pt-1">
                    Đã kiểm tra khoảng {methodStartDate} đến {methodEndDate} · Có giao dịch: {evalResult.total_trades} lệnh.
                  </div>
                </div>
              )}

              {/* CARD B: 4 SỐ LIỆU CỐT LÕI (SAU CHI PHÍ) */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
                <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-700 shadow-sm">
                  <span className="text-[10px] text-gray-500 uppercase tracking-wider block">Tổng Lệnh Đã Đóng</span>
                  <div className="text-lg font-bold text-gray-100 font-mono mt-0.5">{evalResult.total_trades} lệnh</div>
                  <span className="text-[10px] text-gray-400">Thắng: {evalResult.wins} · Thua: {evalResult.losses}</span>
                </div>

                <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-700 shadow-sm">
                  <span className="text-[10px] text-gray-500 uppercase tracking-wider block">Lời / Lỗ Sau Toàn Bộ Phí</span>
                  <div className={`text-lg font-bold font-mono mt-0.5 ${
                    evalResult.total_net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  }`}>
                    {evalResult.total_net_pnl >= 0 ? '+' : ''}${evalResult.total_net_pnl.toFixed(2)}
                  </div>
                  <span className="text-[10px] text-gray-400">
                    Phí & Trượt giá: -${(evalResult.total_fees + evalResult.total_slippage).toFixed(2)}
                  </span>
                </div>

                <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-700 shadow-sm">
                  <span className="text-[10px] text-gray-500 uppercase tracking-wider block">Tỷ Lệ Thắng (Win Rate)</span>
                  <div className="text-lg font-bold text-aurum-400 font-mono mt-0.5">{evalResult.win_rate_pct.toFixed(1)}%</div>
                  <span className="text-[10px] text-gray-400">Profit Factor: {evalResult.profit_factor ? evalResult.profit_factor.toFixed(2) : 'N/A'}</span>
                </div>

                <div className="bg-charcoal-850 p-3 rounded-xl border border-charcoal-700 shadow-sm">
                  <span className="text-[10px] text-gray-500 uppercase tracking-wider block">Mức Giảm Vốn Lớn Nhất</span>
                  <div className="text-lg font-bold text-rose-400 font-mono mt-0.5">-{evalResult.max_drawdown_pct.toFixed(1)}%</div>
                  <span className="text-[10px] text-gray-400">-${evalResult.max_drawdown_usdt.toFixed(2)} USD</span>
                </div>
              </div>

              {/* CARD C: ĐIỂM CẦN CẢI THIỆN */}
              {methodConclusion?.improvements && methodConclusion.improvements.length > 0 && (
                <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow-sm space-y-3">
                  <span className="text-xs font-bold text-gray-200 block border-b border-charcoal-750 pb-2">
                    Điểm Cần Cải Thiện & Bước Đề Xuất Tiếp Theo (Có Bằng Chứng Định Lượng)
                  </span>
                  <div className="space-y-2">
                    {methodConclusion.improvements.map((imp, idx) => (
                      <div key={idx} className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750 text-xs space-y-1">
                        <div className="font-semibold text-aurum-300">• {imp.issue}</div>
                        <div className="text-gray-400 text-[11px]"><strong className="text-gray-300">Bằng chứng:</strong> {imp.evidence}</div>
                        <div className="text-gray-300 text-[11px]"><strong className="text-emerald-400">Đề xuất:</strong> {imp.suggestion}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* CARD D: CÁC NÚT HÀNH ĐỘNG */}
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => setTradesDrawerOpen(!tradesDrawerOpen)}
                  className="px-3 py-2 bg-charcoal-850 hover:bg-charcoal-800 text-gray-200 rounded-lg border border-charcoal-700 text-xs font-semibold flex items-center gap-1.5 transition"
                >
                  <FileText className="w-3.5 h-3.5 text-aurum-400" />
                  Xem Danh Sách Lệnh ({evalResult.trades?.length || 0})
                </button>
                <button
                  onClick={() => setEvalDetailsOpen(!evalDetailsOpen)}
                  className="px-3 py-2 bg-charcoal-850 hover:bg-charcoal-800 text-gray-200 rounded-lg border border-charcoal-700 text-xs font-semibold flex items-center gap-1.5 transition"
                >
                  <Activity className="w-3.5 h-3.5 text-aurum-400" />
                  Chi Tiết Kiểm Tra & Cấu Hình
                </button>
              </div>

              {/* Trades List Drawer */}
              {tradesDrawerOpen && evalResult.trades && (
                <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow-sm space-y-3">
                  <div className="flex items-center justify-between border-b border-charcoal-750 pb-2">
                    <span className="font-bold text-xs text-gray-200">Danh Sách Lệnh Trong Đợt Kiểm Tra</span>
                    <button onClick={() => setTradesDrawerOpen(false)} className="text-gray-400 hover:text-white">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="max-h-72 overflow-y-auto">
                    <table className="w-full text-left text-[11px]">
                      <thead className="text-gray-500 border-b border-charcoal-750 font-mono">
                        <tr>
                          <th className="py-1">Hướng</th>
                          <th>Entry</th>
                          <th>Exit</th>
                          <th>Net PnL ($)</th>
                          <th>R-Net</th>
                          <th>Thời gian</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-charcoal-800 font-mono">
                        {evalResult.trades.map((tr: any, idx: number) => (
                          <tr key={idx} className="hover:bg-charcoal-800/40">
                            <td className={`py-1 font-bold ${tr.direction === 'LONG' ? 'text-emerald-400' : 'text-rose-400'}`}>{tr.direction}</td>
                            <td>${tr.entry_price?.toFixed(2)}</td>
                            <td>${tr.exit_price?.toFixed(2)}</td>
                            <td className={tr.net_pnl >= 0 ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
                              {tr.net_pnl >= 0 ? '+' : ''}${tr.net_pnl?.toFixed(2)}
                            </td>
                            <td>{tr.r_multiple ? `${tr.r_multiple.toFixed(2)}R` : '-'}</td>
                            <td className="text-gray-500">{new Date(tr.entry_time).toLocaleDateString('vi-VN')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Audit Details Drawer */}
              {evalDetailsOpen && (
                <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow-sm space-y-3 text-xs text-gray-300">
                  <div className="flex items-center justify-between border-b border-charcoal-750 pb-2">
                    <span className="font-bold text-xs text-gray-200">Chi Tiết Kiểm Tra & Giả Định Chi Phí</span>
                    <button onClick={() => setEvalDetailsOpen(false)} className="text-gray-400 hover:text-white">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
                    <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750">
                      <span className="text-gray-500 block">Expectancy (R):</span>
                      <span className="font-bold text-gray-200">{evalResult.expectancy_r ? `${evalResult.expectancy_r.toFixed(2)}R` : 'N/A'}</span>
                    </div>
                    <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750">
                      <span className="text-gray-500 block">Ngày lỗ nhất:</span>
                      <span className="font-bold text-rose-400">${evalResult.worst_day_pnl?.toFixed(2)}</span>
                    </div>
                    <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750">
                      <span className="text-gray-500 block">Lỗ liên tiếp max:</span>
                      <span className="font-bold text-gray-200">{evalResult.max_consecutive_losses} lệnh</span>
                    </div>
                    <div className="bg-charcoal-900 p-2 rounded border border-charcoal-750">
                      <span className="text-gray-500 block">Vi phạm Loss Budget:</span>
                      <span className="font-bold text-gray-200">{evalResult.loss_budget_breaches} lần</span>
                    </div>
                  </div>
                  <div className="text-[10px] text-gray-400 italic">
                    *Mô hình chi phí áp dụng: Bitget Classic Taker 0.06%, Maker 0.02%, trượt giá $0.10/lệnh, spread $0.20/oz.
                  </div>
                </div>
              )}
            </div>
          ) : (
            !runningEval && (
              <div className="bg-charcoal-850 p-8 rounded-xl border border-charcoal-700 text-center text-gray-400 text-xs">
                <BarChart2 className="w-8 h-8 text-gray-500 mx-auto mb-2" />
                <p>Chưa có kết quả đánh giá phương pháp.</p>
                <p className="mt-1 text-gray-500">Bấm nút <strong>"Chạy Đánh Giá"</strong> để kiểm định chiến lược qua giai đoạn bạn chọn.</p>
              </div>
            )
          )}
        </div>
      )}
    </div>
  );
};

export const ResearchTab: React.FC<ResearchTabProps> = (props) => (
  <ResearchErrorBoundary>
    <ResearchTabInner {...props} />
  </ResearchErrorBoundary>
);
