import React, { useState, useEffect, useCallback } from 'react';
import {
  BookOpen,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Search,
  ExternalLink,
  Edit3,
  Archive,
  X,
  FileText,
  Brain,
  Sliders,
  Sparkles,
  ShieldAlert,
  AlertTriangle,
  Info,
  Power,
} from 'lucide-react';
import {
  api,
  extractErrorMessage,
  type PaginatedJournalResponse,
  type TradeReviewData,
  type TradeReviewPayload,
  type LessonItem,
  type JournalQueryParams,
} from './api/client';
import { wsClient } from './services/wsClient';

interface JournalTabProps {
  onFocusChart?: (trade: any) => void;
  showToast?: (title: string, message: string, type?: 'info' | 'success' | 'warn') => void;
}

const EMOTIONS_LIST = [
  'Bình tĩnh',
  'Tự tin',
  'FOMO',
  'Sợ bỏ lỡ',
  'Nóng vội',
  'Sợ thua lỗ',
  'Trả thù thị trường',
  'Thiếu tập trung',
  'Kỷ luật cao',
];

export const JournalTab: React.FC<JournalTabProps> = ({ onFocusChart, showToast }) => {
  // Filters state
  const [page, setPage] = useState(1);
  const pageSize = 15;
  const [stateFilter, setStateFilter] = useState('ALL');
  const [directionFilter, setDirectionFilter] = useState('ALL');
  const [outcomeFilter, setOutcomeFilter] = useState('ALL');
  const [strategyFilter, setStrategyFilter] = useState('ALL');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [reviewFilter, setReviewFilter] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const sortBy = 'created_at';
  const sortDir: 'asc' | 'desc' = 'desc';

  // Data state
  const [journalData, setJournalData] = useState<PaginatedJournalResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Selected trade for drawer
  const [selectedTrade, setSelectedTrade] = useState<any | null>(null);
  const [activeDrawerTab, setActiveDrawerTab] = useState<'facts' | 'hypotheses' | 'review'>('facts');

  // Trade review form state
  const [reviewData, setReviewData] = useState<TradeReviewData | null>(null);
  const [reviewDraft, setReviewDraft] = useState<TradeReviewPayload>({});
  const [isSavingReview, setIsSavingReview] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  // Lessons memory state
  const [lessons, setLessons] = useState<LessonItem[]>([]);
  const [lessonStatusFilter, setLessonStatusFilter] = useState<string>('PENDING_REVIEW');
  const [isLoadingLessons, setIsLoadingLessons] = useState(false);
  const [editingLessonId, setEditingLessonId] = useState<number | null>(null);
  const [editActionRule, setEditActionRule] = useState<string>('');
  const [isUpdatingLesson, setIsUpdatingLesson] = useState(false);

  // V10 Structured Rule Editor Modal State
  const [structuredModalLesson, setStructuredModalLesson] = useState<LessonItem | null>(null);
  const [ruleSeverity, setRuleSeverity] = useState<'INFO' | 'WARNING' | 'CRITICAL'>('INFO');
  const [ruleEffect, setRuleEffect] = useState<'ANNOTATE' | 'WARN_ENTRY' | 'BLOCK_ENTRY' | 'PROPOSE_PLAN_ADJUSTMENT'>('ANNOTATE');
  const [ruleMetric, setRuleMetric] = useState<string>('spread');
  const [ruleOperator, setRuleOperator] = useState<string>('>=');
  const [ruleThreshold, setRuleThreshold] = useState<string>('0.40');
  const [ruleActionRule, setRuleActionRule] = useState<string>('');
  const [ruleValidationResult, setRuleValidationResult] = useState<{ is_valid: boolean; status: string; message: string; report: any } | null>(null);
  const [isValidatingPredicate, setIsValidatingPredicate] = useState(false);
  const [isSavingRule, setIsSavingRule] = useState(false);
  const [ruleSaveError, setRuleSaveError] = useState<string | null>(null);

  // Load Journal paginated
  const loadJournal = useCallback(async () => {
    setIsLoading(true);
    try {
      const params: JournalQueryParams = {
        page,
        page_size: pageSize,
        sort_by: sortBy,
        sort_dir: sortDir,
      };
      if (stateFilter !== 'ALL') params.state = stateFilter;
      if (directionFilter !== 'ALL') params.direction = directionFilter;
      if (outcomeFilter !== 'ALL') params.outcome = outcomeFilter;
      if (strategyFilter !== 'ALL') params.strategy_family = strategyFilter;
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      if (reviewFilter === 'REVIEWED') params.has_review = true;
      if (reviewFilter === 'UNREVIEWED') params.has_review = false;
      if (searchQuery.trim()) params.search = searchQuery.trim();

      const res = await api.getJournalPaginated(params);
      setJournalData(res);
    } catch (err) {
      console.error('Failed to load journal', err);
    } finally {
      setIsLoading(false);
    }
  }, [
    page,
    pageSize,
    stateFilter,
    directionFilter,
    outcomeFilter,
    strategyFilter,
    startDate,
    endDate,
    reviewFilter,
    searchQuery,
    sortBy,
    sortDir,
  ]);

  // Load Lessons
  const loadLessons = useCallback(async () => {
    setIsLoadingLessons(true);
    try {
      const statusParam = lessonStatusFilter === 'ALL' ? undefined : lessonStatusFilter;
      const res = await api.getLessons(undefined, statusParam);
      setLessons(res || []);
    } catch (err) {
      console.error('Failed to load lessons', err);
    } finally {
      setIsLoadingLessons(false);
    }
  }, [lessonStatusFilter]);

  useEffect(() => {
    loadJournal();
  }, [loadJournal]);

  useEffect(() => {
    loadLessons();
  }, [loadLessons]);

  useEffect(() => {
    const unsub = wsClient.subscribe((msg: any) => {
      if (msg.type === 'DOMAIN_EVENT') {
        const evType = msg.event?.event_type;
        if (
          evType === 'trade.opened' ||
          evType === 'trade.closed' ||
          evType === 'order.armed' ||
          evType === 'order.cancelled' ||
          evType === 'order.rejected' ||
          evType === 'setup.cancelled' ||
          evType === 'setup.invalidated'
        ) {
          loadJournal();
          loadLessons();
        }
      }
    });
    return () => unsub();
  }, [loadJournal, loadLessons]);

  // Open Drawer for Trade
  const handleOpenDrawer = async (trade: any) => {
    setSelectedTrade(trade);
    setActiveDrawerTab('facts');
    setReviewError(null);
    try {
      const rev = await api.getTradeReview(trade.id);
      setReviewData(rev);
      if (rev) {
        setReviewDraft({
          user_notes: rev.user_notes || '',
          self_reported_entry_reason: rev.self_reported_entry_reason || '',
          psychology_before: rev.psychology_before || '',
          psychology_during: rev.psychology_during || '',
          psychology_after: rev.psychology_after || '',
          emotions: rev.emotions || [],
          confidence_score: rev.confidence_score ?? 3,
          discipline_score: rev.discipline_score ?? 3,
          user_loss_reason: rev.user_loss_reason || '',
          mistakes: rev.mistakes || '',
          what_went_well: rev.what_went_well || '',
          improvement_plan: rev.improvement_plan || '',
          execution_mode: rev.execution_mode || (trade.order_type === 'AUTO' ? 'AUTO' : 'MANUAL'),
          expected_revision: rev.revision,
        });
      } else {
        setReviewDraft({
          user_notes: '',
          self_reported_entry_reason: '',
          psychology_before: '',
          psychology_during: '',
          psychology_after: '',
          emotions: [],
          confidence_score: 3,
          discipline_score: 3,
          user_loss_reason: '',
          mistakes: '',
          what_went_well: '',
          improvement_plan: '',
          execution_mode: trade.order_type === 'AUTO' ? 'AUTO' : 'MANUAL',
          expected_revision: 0,
        });
      }
    } catch (err) {
      console.error('Failed to fetch trade review', err);
    }
  };

  // Save Trade Review
  const handleSaveReview = async () => {
    if (!selectedTrade) return;
    setIsSavingReview(true);
    setReviewError(null);
    try {
      const payload: TradeReviewPayload = {
        ...reviewDraft,
        expected_revision: reviewData ? reviewData.revision : 0,
      };
      const saved = await api.saveTradeReview(selectedTrade.id, payload);
      setReviewData(saved);
      setReviewDraft((prev) => ({ ...prev, expected_revision: saved.revision }));
      if (showToast) {
        showToast('Nhật Ký', `Đã lưu đánh giá cho lệnh #${selectedTrade.id}`, 'success');
      }
      // Refresh journal to update review badge
      loadJournal();
    } catch (err: any) {
      if (err.response?.status === 409) {
        setReviewError(
          'Xung đột phiên bản (409 Conflict): Đánh giá đã được chỉnh sửa ở một phiên khác. Vui lòng tải lại trang để xem dữ liệu mới nhất.'
        );
      } else {
        setReviewError(extractErrorMessage(err, 'Lỗi khi lưu đánh giá lệnh'));
      }
    } finally {
      setIsSavingReview(false);
    }
  };

  // Lesson actions: Approve / Reject / Archive
  const handleApproveLesson = async (lessonId: number) => {
    setIsUpdatingLesson(true);
    try {
      await api.approveLesson(lessonId);
      if (showToast) {
        showToast('Bài Học', 'Đã duyệt bài học. Bài học đủ điều kiện được chiến lược tham khảo khi phù hợp.', 'success');
      }
      loadLessons();
    } catch (err) {
      if (showToast) {
        showToast('Bài Học', extractErrorMessage(err, 'Lỗi khi duyệt bài học'), 'warn');
      }
    } finally {
      setIsUpdatingLesson(false);
    }
  };

  const handleRejectLesson = async (lessonId: number) => {
    setIsUpdatingLesson(true);
    try {
      await api.rejectLesson(lessonId);
      if (showToast) {
        showToast('Bài Học', 'Đã từ chối bài học.', 'info');
      }
      loadLessons();
    } catch (err) {
      if (showToast) {
        showToast('Bài Học', extractErrorMessage(err, 'Lỗi khi từ chối bài học'), 'warn');
      }
    } finally {
      setIsUpdatingLesson(false);
    }
  };

  const handleArchiveLesson = async (lessonId: number) => {
    setIsUpdatingLesson(true);
    try {
      await api.archiveLesson(lessonId);
      if (showToast) {
        showToast('Bài Học', 'Đã lưu trữ bài học.', 'info');
      }
      loadLessons();
    } catch (err) {
      if (showToast) {
        showToast('Bài Học', extractErrorMessage(err, 'Lỗi khi lưu trữ bài học'), 'warn');
      }
    } finally {
      setIsUpdatingLesson(false);
    }
  };

  const handleSaveEditActionRule = async (lessonId: number) => {
    if (!editActionRule.trim()) return;
    setIsUpdatingLesson(true);
    try {
      await api.updateLesson(lessonId, { action_rule: editActionRule.trim() });
      setEditingLessonId(null);
      loadLessons();
    } catch (err) {
      if (showToast) {
        showToast('Bài Học', extractErrorMessage(err, 'Lỗi khi cập nhật quy tắc'), 'warn');
      }
    } finally {
      setIsUpdatingLesson(false);
    }
  };

  // V10: Toggle Enable Lesson Rule
  const handleToggleEnable = async (lessonId: number) => {
    setIsUpdatingLesson(true);
    try {
      const updated = await api.toggleLessonEnable(lessonId);
      if (showToast) {
        showToast('Quy Tắc Bài Học', `Đã ${updated.enabled ? 'bật' : 'tắt'} áp dụng quy tắc #${lessonId}.`, 'info');
      }
      loadLessons();
    } catch (err) {
      if (showToast) {
        showToast('Lỗi', extractErrorMessage(err, 'Lỗi khi bật/tắt quy tắc'), 'warn');
      }
    } finally {
      setIsUpdatingLesson(false);
    }
  };

  // V10: Open Structured Rule Editor
  const handleOpenRuleEditor = (ls: LessonItem) => {
    setStructuredModalLesson(ls);
    setRuleSeverity(ls.severity || 'INFO');
    setRuleEffect(ls.effect || 'ANNOTATE');
    setRuleActionRule(ls.action_rule || '');
    setRuleValidationResult(null);
    setRuleSaveError(null);

    if (ls.predicate) {
      try {
        const pred = typeof ls.predicate === 'string' ? JSON.parse(ls.predicate) : ls.predicate;
        if (pred?.metric) setRuleMetric(pred.metric);
        if (pred?.operator) setRuleOperator(pred.operator);
        if (pred?.threshold !== undefined) setRuleThreshold(String(pred.threshold));
        else if (pred?.values) setRuleThreshold(pred.values.join(', '));
      } catch (e) {
        setRuleMetric('spread');
        setRuleOperator('>=');
        setRuleThreshold('0.40');
      }
    } else {
      setRuleMetric('spread');
      setRuleOperator('>=');
      setRuleThreshold('0.40');
    }
  };

  // V10: Live Validate Predicate
  const handleValidatePredicate = async () => {
    setIsValidatingPredicate(true);
    setRuleValidationResult(null);
    setRuleSaveError(null);
    try {
      let predObj: any = null;
      if (ruleSeverity !== 'INFO') {
        const val = parseFloat(ruleThreshold);
        predObj = {
          metric: ruleMetric,
          operator: ruleOperator,
          threshold: isNaN(val) ? 0.40 : val
        };
      }
      const res = await api.validatePredicate({
        predicate: predObj,
        severity: ruleSeverity,
        effect: ruleEffect
      });
      setRuleValidationResult(res);
    } catch (err: any) {
      setRuleValidationResult({
        is_valid: false,
        status: 'INVALID',
        message: extractErrorMessage(err, 'Lỗi kiểm tra điều kiện'),
        report: {}
      });
    } finally {
      setIsValidatingPredicate(false);
    }
  };

  // V10: Save Structured Rule with Optimistic Locking
  const handleSaveStructuredRule = async () => {
    if (!structuredModalLesson) return;
    setIsSavingRule(true);
    setRuleSaveError(null);
    try {
      let predObj: any = null;
      if (ruleSeverity !== 'INFO') {
        const val = parseFloat(ruleThreshold);
        predObj = {
          metric: ruleMetric,
          operator: ruleOperator,
          threshold: isNaN(val) ? 0.40 : val
        };
      }
      await api.updateLesson(structuredModalLesson.id, {
        severity: ruleSeverity,
        effect: ruleEffect,
        predicate: predObj ? JSON.stringify(predObj) : null,
        action_rule: ruleActionRule.trim() || structuredModalLesson.action_rule,
        revision: structuredModalLesson.revision
      });
      if (showToast) {
        showToast('Quy Tắc Bài Học', `Đã cập nhật quy tắc #${structuredModalLesson.id}. Phiên bản mới đã được lưu.`, 'success');
      }
      setStructuredModalLesson(null);
      loadLessons();
    } catch (err: any) {
      if (err.response?.status === 409) {
        setRuleSaveError('Xung đột phiên bản (409 Conflict): Quy tắc đã được chỉnh sửa ở phiên khác. Vui lòng đóng và mở lại.');
      } else {
        setRuleSaveError(extractErrorMessage(err, 'Lỗi khi lưu quy tắc bài học'));
      }
    } finally {
      setIsSavingRule(false);
    }
  };

  // Helper outcome labels
  const formatOutcomeBadge = (trade: any) => {
    if (trade.state === 'ARMED' || trade.state === 'PENDING') {
      return <span className="px-2 py-0.5 rounded font-bold bg-amber-950 text-amber-300 border border-amber-700 text-[10px]">CHỜ KHỚP</span>;
    }
    if (trade.state === 'OPEN') {
      return <span className="px-2 py-0.5 rounded font-bold bg-blue-950 text-blue-300 border border-blue-700 text-[10px] animate-pulse">ĐANG CHẠY</span>;
    }
    if (trade.state === 'CANCELLED' || trade.state === 'REJECTED' || trade.state === 'EXPIRED') {
      return <span className="px-2 py-0.5 rounded font-bold bg-charcoal-800 text-gray-400 border border-charcoal-700 text-[10px]">ĐÃ HỦY</span>;
    }

    // Terminal closed
    const netPnl = trade.realized_pnl_net;
    const cause = trade.exit_cause || '';

    if (cause === 'MANUAL_CLOSE') {
      if (netPnl > 0) {
        return <span className="px-2 py-0.5 rounded font-bold bg-emerald-950 text-emerald-300 border border-emerald-700 text-[10px]">ĐÓNG TAY · CÓ LÃI</span>;
      }
      return <span className="px-2 py-0.5 rounded font-bold bg-rose-950 text-rose-300 border border-rose-700 text-[10px]">ĐÓNG TAY · CẮT LỖ</span>;
    }

    if (cause === 'TP_HIT') {
      if (netPnl !== null && netPnl < 0) {
        return <span className="px-2 py-0.5 rounded font-bold bg-amber-950 text-amber-300 border border-amber-700 text-[10px]">TP · LỖ RÒNG</span>;
      }
      return <span className="px-2 py-0.5 rounded font-bold bg-emerald-950 text-emerald-300 border border-emerald-700 text-[10px]">CHỐT LỜI (TP)</span>;
    }

    if (cause === 'SL_HIT') {
      return <span className="px-2 py-0.5 rounded font-bold bg-rose-950 text-rose-300 border border-rose-700 text-[10px]">CẮT LỖ (SL)</span>;
    }

    if (cause === 'LIQUIDATION') {
      return <span className="px-2 py-0.5 rounded font-bold bg-rose-950 text-rose-200 border border-rose-600 text-[10px]">THANH LÝ</span>;
    }

    // General fallback
    if (netPnl > 0) {
      return <span className="px-2 py-0.5 rounded font-bold bg-emerald-950 text-emerald-300 border border-emerald-700 text-[10px]">THẮNG</span>;
    } else if (netPnl < 0) {
      return <span className="px-2 py-0.5 rounded font-bold bg-rose-950 text-rose-300 border border-rose-700 text-[10px]">THUA</span>;
    }
    return <span className="px-2 py-0.5 rounded font-bold bg-gray-800 text-gray-300 border border-gray-600 text-[10px]">HÒA VỐN</span>;
  };

  const summary = journalData?.summary;
  const trades = journalData?.items || [];
  const totalPages = Math.ceil((journalData?.total || 0) / pageSize) || 1;

  return (
    <div className="bg-charcoal-900 border border-charcoal-750 rounded-lg p-5 flex flex-col gap-5 overflow-y-auto max-h-[850px]">
      {/* Header */}
      <div className="flex flex-wrap justify-between items-center border-b border-charcoal-750 pb-3 gap-3">
        <div>
          <h2 className="text-base font-bold text-aurum-400 flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-aurum-400" />
            Nhật Ký Lệnh Giao Dịch & Bài Học Chiến Lược
          </h2>
          <p className="text-xs text-gray-400 mt-0.5">
            Mô hình lệnh chính xác, đối soát snapshot trước-sau, ghi chú tâm lý từng lệnh và cơ chế duyệt bài học có kiểm soát.
          </p>
        </div>

        <button
          type="button"
          onClick={() => {
            loadJournal();
            loadLessons();
          }}
          disabled={isLoading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-charcoal-800 hover:bg-charcoal-700 text-aurum-400 text-xs font-semibold transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          <span>Làm Mới</span>
        </button>
      </div>

      {/* Aggregate Summary Metrics Toolbar (Computed Server-Side across full filter set) */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3 bg-charcoal-850 p-3.5 rounded-lg border border-charcoal-750 text-xs">
        <div>
          <span className="text-gray-400 block text-[11px]">Đã Hoàn Thành</span>
          <span className="text-base font-bold text-gray-200 mt-0.5 block">
            {summary ? summary.completed_count : '—'} <span className="text-xs font-normal text-gray-500">lệnh</span>
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">Đang Mở (Active)</span>
          <span className="text-base font-bold text-blue-400 mt-0.5 block">
            {summary ? summary.open_count : '—'}
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">Tỷ Lệ Thắng (Winrate)</span>
          <span className="text-base font-bold text-aurum-300 mt-0.5 block">
            {summary ? `${summary.winrate_pct.toFixed(1)}%` : '—'}
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">Tổng PnL Ròng</span>
          <span
            className={`text-base font-bold mt-0.5 block ${
              summary && summary.net_pnl > 0
                ? 'text-emerald-400'
                : summary && summary.net_pnl < 0
                ? 'text-rose-400'
                : 'text-gray-300'
            }`}
          >
            {summary
              ? `${summary.net_pnl > 0 ? '+' : ''}${summary.net_pnl.toFixed(2)} USDT`
              : '—'}
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">Thắng / Thua / Hòa</span>
          <span className="text-xs font-semibold text-gray-300 mt-1 block">
            <span className="text-emerald-400 font-bold">{summary?.wins ?? 0}W</span> -{' '}
            <span className="text-rose-400 font-bold">{summary?.losses ?? 0}L</span> -{' '}
            <span className="text-gray-400 font-bold">{summary?.breakevens ?? 0}BE</span>
          </span>
        </div>
        <div>
          <span className="text-gray-400 block text-[11px]">R Realized Trung Bình</span>
          <span className="text-base font-bold text-indigo-300 mt-0.5 block">
            {summary ? `${summary.average_realized_r > 0 ? '+' : ''}${summary.average_realized_r.toFixed(2)}R` : '—'}
          </span>
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="bg-charcoal-850 p-3.5 rounded-lg border border-charcoal-750 flex flex-col gap-3 text-xs">
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Search Input */}
          <div className="relative flex-1 min-w-[200px]">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setPage(1);
              }}
              placeholder="Tìm theo Trade ID, Order ID, Setup ID hoặc ghi chú..."
              className="w-full bg-charcoal-900 border border-charcoal-700 rounded pl-8 pr-3 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
            />
          </div>

          {/* State Filter */}
          <select
            value={stateFilter}
            onChange={(e) => {
              setStateFilter(e.target.value);
              setPage(1);
            }}
            className="bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-300"
          >
            <option value="ALL">Tất cả trạng thái</option>
            <option value="CLOSED">Đã đóng (Closed)</option>
            <option value="OPEN">Đang mở (Open)</option>
            <option value="ARMED">Chờ khớp (Armed)</option>
            <option value="CANCELLED">Hủy / Từ chối</option>
          </select>

          {/* Direction Filter */}
          <select
            value={directionFilter}
            onChange={(e) => {
              setDirectionFilter(e.target.value);
              setPage(1);
            }}
            className="bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-300"
          >
            <option value="ALL">Cả 2 hướng</option>
            <option value="LONG">Chỉ LONG</option>
            <option value="SHORT">Chỉ SHORT</option>
          </select>

          {/* Outcome Filter */}
          <select
            value={outcomeFilter}
            onChange={(e) => {
              setOutcomeFilter(e.target.value);
              setPage(1);
            }}
            className="bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-300"
          >
            <option value="ALL">Tất cả kết quả</option>
            <option value="WIN">Lệnh Thắng (Win)</option>
            <option value="LOSS">Lệnh Thua (Loss)</option>
            <option value="BREAKEVEN">Hòa vốn (Breakeven)</option>
          </select>

          {/* Review Filter */}
          <select
            value={reviewFilter}
            onChange={(e) => {
              setReviewFilter(e.target.value);
              setPage(1);
            }}
            className="bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-300"
          >
            <option value="ALL">Đánh giá: Tất cả</option>
            <option value="REVIEWED">Đã có Review</option>
            <option value="UNREVIEWED">Chưa Review</option>
          </select>

          {/* Date range inputs */}
          <div className="flex items-center gap-1.5">
            <input
              type="date"
              value={startDate}
              onChange={(e) => {
                setStartDate(e.target.value);
                setPage(1);
              }}
              className="bg-charcoal-900 border border-charcoal-700 rounded px-2 py-1 text-[11px] text-gray-300"
              title="Từ ngày"
            />
            <span className="text-gray-500">→</span>
            <input
              type="date"
              value={endDate}
              onChange={(e) => {
                setEndDate(e.target.value);
                setPage(1);
              }}
              className="bg-charcoal-900 border border-charcoal-700 rounded px-2 py-1 text-[11px] text-gray-300"
              title="Đến ngày"
            />
          </div>

          {/* Reset button */}
          {(stateFilter !== 'ALL' ||
            directionFilter !== 'ALL' ||
            outcomeFilter !== 'ALL' ||
            reviewFilter !== 'ALL' ||
            startDate ||
            endDate ||
            searchQuery) && (
            <button
              type="button"
              onClick={() => {
                setStateFilter('ALL');
                setDirectionFilter('ALL');
                setOutcomeFilter('ALL');
                setStrategyFilter('ALL');
                setReviewFilter('ALL');
                setStartDate('');
                setEndDate('');
                setSearchQuery('');
                setPage(1);
              }}
              className="text-[11px] text-aurum-400 hover:text-aurum-300 underline"
            >
              Đặt lại lọc
            </button>
          )}
        </div>
      </div>

      {/* Trades Table */}
      <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 flex flex-col gap-3 text-xs">
        <div className="flex justify-between items-center border-b border-charcoal-750 pb-2">
          <h3 className="font-bold text-gray-200 flex items-center gap-2">
            <FileText className="w-4 h-4 text-aurum-400" />
            <span>Danh Sách Giao Dịch Đã Khớp & Lệnh Chờ</span>
          </h3>
          <span className="text-[11px] text-gray-400">
            Hiển thị {trades.length} / {journalData?.total || 0} lệnh
          </span>
        </div>

        {trades.length === 0 ? (
          <p className="text-xs text-gray-500 italic py-6 text-center">
            {isLoading ? 'Đang tải dữ liệu...' : 'Không tìm thấy giao dịch nào khớp với bộ lọc.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-charcoal-700 text-[10px] text-gray-400 uppercase tracking-wider">
                  <th className="py-2.5 px-2">ID Lệnh</th>
                  <th className="py-2.5 px-2">Thời Gian</th>
                  <th className="py-2.5 px-2">Hướng</th>
                  <th className="py-2.5 px-2">Trạng Thái / Kết Quả</th>
                  <th className="py-2.5 px-2">Entry / SL / TP</th>
                  <th className="py-2.5 px-2">PnL Ròng</th>
                  <th className="py-2.5 px-2">Realized R</th>
                  <th className="py-2.5 px-2">Lý Do Đóng</th>
                  <th className="py-2.5 px-2">Đánh Giá</th>
                  <th className="py-2.5 px-2 text-right">Thao Tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-charcoal-800 text-[11px]">
                {trades.map((t) => {
                  const hasReview = Boolean(t.has_review || t.user_notes);
                  return (
                    <tr key={t.id} className="hover:bg-charcoal-800/40 transition">
                      <td className="py-2.5 px-2 font-mono text-gray-300">
                        <span className="font-semibold block">{t.id}</span>
                        {t.order_type && (
                          <span className="text-[9px] text-gray-500 font-sans block">{t.order_type}</span>
                        )}
                      </td>
                      <td className="py-2.5 px-2 text-gray-300 whitespace-nowrap">
                        {new Date(t.created_at).toLocaleTimeString('vi-VN')}
                        <span className="text-[9px] text-gray-500 block">
                          {new Date(t.created_at).toLocaleDateString('vi-VN')}
                        </span>
                      </td>
                      <td className="py-2.5 px-2">
                        <span
                          className={`px-2 py-0.5 rounded font-bold text-[10px] ${
                            t.direction === 'LONG'
                              ? 'bg-emerald-950 text-emerald-300 border border-emerald-700'
                              : 'bg-rose-950 text-rose-300 border border-rose-700'
                          }`}
                        >
                          {t.direction}
                        </span>
                      </td>
                      <td className="py-2.5 px-2 whitespace-nowrap">
                        {formatOutcomeBadge(t)}
                      </td>
                      <td className="py-2.5 px-2 text-gray-300 font-mono text-[10px]">
                        <div>E: ${t.actual_entry || t.planned_entry}</div>
                        <div className="text-gray-400">SL: ${t.stop_loss} | TP: ${t.take_profit}</div>
                      </td>
                      <td className="py-2.5 px-2 font-bold whitespace-nowrap">
                        {t.realized_pnl_net !== null && t.realized_pnl_net !== undefined ? (
                          <span
                            className={
                              t.realized_pnl_net > 0
                                ? 'text-emerald-400'
                                : t.realized_pnl_net < 0
                                ? 'text-rose-400'
                                : 'text-gray-300'
                            }
                          >
                            {t.realized_pnl_net > 0 ? '+' : ''}${t.realized_pnl_net.toFixed(2)}
                          </span>
                        ) : t.state === 'OPEN' ? (
                          <span className="text-blue-400 italic">Đang chạy</span>
                        ) : (
                          <span className="text-gray-500">—</span>
                        )}
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        {t.realized_r !== null && t.realized_r !== undefined ? (
                          <span className={t.realized_r >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                            {t.realized_r > 0 ? '+' : ''}{t.realized_r.toFixed(2)}R
                          </span>
                        ) : (
                          <span className="text-gray-500">—</span>
                        )}
                      </td>
                      <td className="py-2.5 px-2 text-gray-400 text-[10px]">
                        {t.exit_cause || (t.state === 'OPEN' ? 'Chưa đóng' : '—')}
                      </td>
                      <td className="py-2.5 px-2">
                        {hasReview ? (
                          <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-indigo-950 text-indigo-300 border border-indigo-700">
                            ✓ Đã Note
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-charcoal-800 text-gray-500">
                            Chưa Note
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-2 text-right whitespace-nowrap">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            type="button"
                            onClick={() => handleOpenDrawer(t)}
                            className="px-2 py-1 rounded bg-charcoal-800 hover:bg-charcoal-700 text-aurum-300 text-[10px] font-semibold transition flex items-center gap-1"
                          >
                            <Edit3 className="w-3 h-3" />
                            <span>Chi Tiết & Note</span>
                          </button>
                          {onFocusChart && (
                            <button
                              type="button"
                              onClick={() => onFocusChart(t)}
                              className="px-2 py-1 rounded bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 text-[10px] font-bold transition flex items-center gap-1"
                              title="Xem snapshot trên chart"
                            >
                              <ExternalLink className="w-3 h-3" />
                              <span>Chart</span>
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex justify-between items-center pt-2 border-t border-charcoal-800 text-[11px] text-gray-400">
            <span>
              Trang {page} / {totalPages} (Tổng {journalData?.total || 0} lệnh)
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="px-2.5 py-1 rounded bg-charcoal-800 hover:bg-charcoal-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-300"
              >
                Trước
              </button>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="px-2.5 py-1 rounded bg-charcoal-800 hover:bg-charcoal-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-300"
              >
                Tiếp
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Lesson Memory & Governance Section */}
      <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-750 flex flex-col gap-3 text-xs">
        <div className="flex flex-wrap justify-between items-center border-b border-charcoal-750 pb-2 gap-2">
          <div>
            <h3 className="font-bold text-aurum-400 flex items-center gap-2">
              <Brain className="w-4 h-4 text-aurum-400" />
              Bộ Nhớ Bài Học & Quy Tắc Quản Trị Chiến Lược (V10 Lesson Governance)
            </h3>
            <p className="text-[11px] text-gray-400">
              Phân loại 3 màu: <span className="text-emerald-400 font-semibold">Xanh (Tham khảo)</span> ·{' '}
              <span className="text-amber-400 font-semibold">Vàng (Cảnh báo trước Entry)</span> ·{' '}
              <span className="text-rose-400 font-semibold">Đỏ (Chặn Entry có cấu trúc)</span>. Bài học cần được duyệt và bật mới có hiệu lực.
            </p>
          </div>

          {/* Status Tabs */}
          <div className="flex items-center gap-1 bg-charcoal-900 p-0.5 rounded border border-charcoal-700">
            {[
              { id: 'PENDING_REVIEW', label: 'Chờ Duyệt' },
              { id: 'APPROVED', label: 'Đã Duyệt' },
              { id: 'REJECTED', label: 'Từ Chối' },
              { id: 'ARCHIVED', label: 'Lưu Trữ' },
              { id: 'ALL', label: 'Tất Cả' },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setLessonStatusFilter(tab.id)}
                className={`px-2.5 py-1 rounded text-[11px] font-semibold transition ${
                  lessonStatusFilter === tab.id
                    ? 'bg-aurum-500 text-charcoal-950 font-bold'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Lessons List */}
        {lessons.length === 0 ? (
          <p className="text-xs text-gray-500 italic py-4 text-center">
            {isLoadingLessons ? 'Đang tải bài học...' : 'Không có bài học nào trong danh mục này.'}
          </p>
        ) : (
          <div className="space-y-3">
            {lessons.map((ls) => {
              const isCritical = ls.severity === 'CRITICAL';
              const isWarning = ls.severity === 'WARNING';
              const isApproved = ls.status === 'APPROVED';
              const isValid = ls.validation_status === 'VALID';

              return (
                <div
                  key={ls.id}
                  className="bg-charcoal-900 p-3.5 rounded-lg border border-charcoal-750 flex flex-col gap-2.5"
                >
                  <div className="flex flex-wrap justify-between items-start gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold text-gray-200 text-xs">{ls.title}</span>

                      {/* 3-Color Badge */}
                      {isCritical ? (
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold border flex items-center gap-1 ${
                            isApproved && ls.enabled && isValid
                              ? 'bg-rose-950 text-rose-300 border-rose-700'
                              : isApproved && ls.enabled && !isValid
                              ? 'bg-amber-950 text-amber-300 border-amber-800'
                              : isApproved && !ls.enabled
                              ? 'bg-charcoal-800 text-rose-300/60 border-charcoal-700'
                              : 'bg-rose-950/50 text-rose-300 border-rose-900'
                          }`}
                        >
                          <ShieldAlert className="w-3 h-3 text-rose-400" />
                          <span>
                            {isApproved && ls.enabled && isValid
                              ? 'ĐỎ: ĐANG CHẶN ENTRY'
                              : isApproved && ls.enabled && !isValid
                              ? 'ĐỎ: CHƯA THỂ ÁP DỤNG'
                              : isApproved && !ls.enabled
                              ? 'ĐỎ: ĐÃ TẮT CHẶN'
                              : 'ĐỎ: QUY TẮC HẠN CHẾ'}
                          </span>
                        </span>
                      ) : isWarning ? (
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold border flex items-center gap-1 ${
                            isApproved && ls.enabled
                              ? 'bg-amber-950 text-amber-300 border-amber-700'
                              : isApproved && !ls.enabled
                              ? 'bg-charcoal-800 text-amber-300/60 border-charcoal-700'
                              : 'bg-amber-950/50 text-amber-300 border-amber-900'
                          }`}
                        >
                          <AlertTriangle className="w-3 h-3 text-amber-400" />
                          <span>
                            {isApproved && ls.enabled
                              ? 'VÀNG: ĐANG CẢNH BÁO'
                              : isApproved && !ls.enabled
                              ? 'VÀNG: ĐÃ TẮT CẢNH BÁO'
                              : 'VÀNG: CẢNH BÁO ENTRY'}
                          </span>
                        </span>
                      ) : (
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold border flex items-center gap-1 ${
                            isApproved
                              ? 'bg-emerald-950 text-emerald-300 border-emerald-700'
                              : 'bg-charcoal-800 text-emerald-400/70 border-charcoal-700'
                          }`}
                        >
                          <Info className="w-3 h-3 text-emerald-400" />
                          <span>{isApproved ? 'XANH: CHỈ THAM KHẢO' : 'XANH: THAM KHẢO'}</span>
                        </span>
                      )}

                      {/* Approval Status Badge */}
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-bold border ${
                          ls.status === 'APPROVED'
                            ? 'bg-emerald-950/60 text-emerald-300 border-emerald-800'
                            : ls.status === 'PENDING_REVIEW'
                            ? 'bg-amber-950/60 text-amber-300 border-amber-800'
                            : ls.status === 'ARCHIVED'
                            ? 'bg-charcoal-800 text-gray-400 border-charcoal-700'
                            : 'bg-rose-950/60 text-rose-300 border-rose-800'
                        }`}
                      >
                        {ls.status === 'APPROVED'
                          ? 'ĐÃ DUYỆT'
                          : ls.status === 'PENDING_REVIEW'
                          ? 'CHỜ DUYỆT'
                          : ls.status === 'ARCHIVED'
                          ? 'LƯU TRỮ'
                          : 'TỪ CHỐI'}
                      </span>

                      {ls.category && (
                        <span className="px-1.5 py-0.5 rounded bg-charcoal-800 text-indigo-300 text-[10px] font-mono">
                          {ls.category}
                        </span>
                      )}

                      <span className="text-[10px] text-gray-500 font-mono">
                        v{ls.version || 1} (rev #{ls.revision || 0})
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      {isApproved && (
                        <button
                          type="button"
                          onClick={() => handleToggleEnable(ls.id)}
                          disabled={isUpdatingLesson}
                          className={`flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-bold border transition ${
                            ls.enabled
                              ? 'bg-aurum-500/20 text-aurum-300 border-aurum-600 hover:bg-aurum-500/30'
                              : 'bg-charcoal-800 text-gray-400 border-charcoal-700 hover:text-gray-200'
                          }`}
                          title={ls.enabled ? 'Nhấn để tắt quy tắc' : 'Nhấn để bật quy tắc'}
                        >
                          <Power className={`w-3 h-3 ${ls.enabled ? 'text-aurum-400' : 'text-gray-500'}`} />
                          <span>{ls.enabled ? '● Đang Bật' : '○ Đã Tắt'}</span>
                        </button>
                      )}

                      {ls.related_trade_id && (
                        <span className="text-[10px] text-gray-500 font-mono">
                          Lệnh #{ls.related_trade_id}
                        </span>
                      )}
                    </div>
                  </div>

                  {ls.hypothesis && (
                    <p className="text-[11px] text-gray-400">
                      <strong className="text-gray-300">Giả thuyết phân tích:</strong> {ls.hypothesis}
                    </p>
                  )}

                  {/* Predicate & Scope preview if configured */}
                  {ls.predicate && (
                    <div className="bg-charcoal-850 p-2 rounded border border-charcoal-750 flex flex-wrap items-center justify-between gap-2 text-[11px]">
                      <div className="flex items-center gap-2">
                        <span className="text-gray-400">Điều kiện kỹ thuật:</span>
                        <code className="text-aurum-300 font-mono bg-charcoal-900 px-1.5 py-0.5 rounded border border-charcoal-700">
                          {typeof ls.predicate === 'string' ? ls.predicate : JSON.stringify(ls.predicate)}
                        </code>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${
                            isValid
                              ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
                              : 'bg-rose-950 text-rose-300 border-rose-800'
                          }`}
                        >
                          {isValid ? 'HỢP LỆ' : 'CHƯA ĐỦ ĐIỀU KIỆN'}
                        </span>
                      </div>
                      <span className="text-[10px] text-gray-500">
                        Stage: {ls.stage || 'BEFORE_ARM'} · Scope: {typeof ls.scope === 'object' ? JSON.stringify(ls.scope) : ls.scope || 'ALL'}
                      </span>
                    </div>
                  )}

                  {/* Action rule text */}
                  <div className="bg-charcoal-850 p-2.5 rounded border border-charcoal-700 text-[11px]">
                    <div className="flex justify-between items-center mb-1">
                      <span className="font-bold text-aurum-400 flex items-center gap-1">
                        <Sparkles className="w-3.5 h-3.5 text-aurum-400" />
                        Nội Dung Quy Tắc (Action Rule):
                      </span>
                      {editingLessonId !== ls.id && (
                        <button
                          type="button"
                          onClick={() => {
                            setEditingLessonId(ls.id);
                            setEditActionRule(ls.action_rule);
                          }}
                          className="text-[10px] text-indigo-400 hover:text-indigo-300 underline"
                        >
                          Sửa ghi chú
                        </button>
                      )}
                    </div>

                    {editingLessonId === ls.id ? (
                      <div className="space-y-2 mt-1">
                        <textarea
                          value={editActionRule}
                          onChange={(e) => setEditActionRule(e.target.value)}
                          rows={2}
                          className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                        />
                        <div className="flex gap-2 justify-end">
                          <button
                            type="button"
                            onClick={() => setEditingLessonId(null)}
                            className="px-2 py-0.5 bg-charcoal-800 text-gray-400 rounded text-[10px]"
                          >
                            Hủy
                          </button>
                          <button
                            type="button"
                            onClick={() => handleSaveEditActionRule(ls.id)}
                            disabled={isUpdatingLesson}
                            className="px-2.5 py-0.5 bg-aurum-500 text-charcoal-950 font-bold rounded text-[10px]"
                          >
                            Lưu Ghi Chú
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p className="text-gray-200 font-medium italic">{ls.action_rule}</p>
                    )}
                  </div>

                  {/* Governance buttons */}
                  <div className="flex flex-wrap justify-between items-center gap-2 pt-1 border-t border-charcoal-800">
                    <button
                      type="button"
                      onClick={() => handleOpenRuleEditor(ls)}
                      className="flex items-center gap-1 px-3 py-1 bg-charcoal-800 hover:bg-charcoal-700 text-aurum-300 border border-charcoal-700 rounded text-[11px] font-semibold transition"
                    >
                      <Sliders className="w-3.5 h-3.5 text-aurum-400" />
                      <span>Cấu Hình Quy Tắc (V10)</span>
                    </button>

                    <div className="flex gap-2">
                      {ls.status !== 'APPROVED' && (
                        <button
                          type="button"
                          onClick={() => handleApproveLesson(ls.id)}
                          disabled={isUpdatingLesson}
                          className="flex items-center gap-1 px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[11px] font-semibold transition"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          <span>Duyệt Bài Học</span>
                        </button>
                      )}
                      {ls.status !== 'REJECTED' && (
                        <button
                          type="button"
                          onClick={() => handleRejectLesson(ls.id)}
                          disabled={isUpdatingLesson}
                          className="flex items-center gap-1 px-3 py-1 bg-charcoal-800 hover:bg-rose-900/60 text-gray-300 hover:text-rose-300 rounded text-[11px] font-semibold transition"
                        >
                          <XCircle className="w-3.5 h-3.5" />
                          <span>Từ Chối</span>
                        </button>
                      )}
                      {ls.status !== 'ARCHIVED' && (
                        <button
                          type="button"
                          onClick={() => handleArchiveLesson(ls.id)}
                          disabled={isUpdatingLesson}
                          className="flex items-center gap-1 px-2.5 py-1 bg-charcoal-800 hover:bg-charcoal-700 text-gray-400 rounded text-[11px] transition"
                          title="Lưu trữ bài học"
                        >
                          <Archive className="w-3.5 h-3.5" />
                          <span>Lưu Trữ</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Trade Detail & Psychology Review Drawer (Modal) */}
      {selectedTrade && (
        <div className="fixed inset-0 z-50 bg-black/75 flex justify-end backdrop-blur-sm transition-all animate-fadeIn">
          <div className="w-full max-w-2xl bg-charcoal-900 border-l border-charcoal-750 h-full flex flex-col shadow-2xl overflow-hidden">
            {/* Drawer Header */}
            <div className="p-4 bg-charcoal-850 border-b border-charcoal-750 flex justify-between items-center">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-gray-100 text-sm">
                    Chi Tiết Lệnh & Ghi Chú Tâm Lý
                  </h3>
                  <span className="font-mono text-xs px-2 py-0.5 rounded bg-charcoal-800 text-aurum-300">
                    #{selectedTrade.id}
                  </span>
                </div>
                <p className="text-[11px] text-gray-400 mt-0.5">
                  XAUUSDT ({selectedTrade.timeframe || '15M'}) · {selectedTrade.direction} · {formatOutcomeBadge(selectedTrade)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedTrade(null)}
                className="p-1 rounded hover:bg-charcoal-700 text-gray-400 hover:text-gray-200 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Drawer Tabs */}
            <div className="flex border-b border-charcoal-750 bg-charcoal-900 px-4 text-xs font-semibold">
              <button
                type="button"
                onClick={() => setActiveDrawerTab('facts')}
                className={`py-2.5 px-3 border-b-2 transition ${
                  activeDrawerTab === 'facts'
                    ? 'border-aurum-400 text-aurum-300 font-bold'
                    : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
              >
                A. Dữ Kiện Xác Minh (Facts)
              </button>
              <button
                type="button"
                onClick={() => setActiveDrawerTab('hypotheses')}
                className={`py-2.5 px-3 border-b-2 transition ${
                  activeDrawerTab === 'hypotheses'
                    ? 'border-aurum-400 text-aurum-300 font-bold'
                    : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
              >
                B. Giả Thuyết & SMC
              </button>
              <button
                type="button"
                onClick={() => setActiveDrawerTab('review')}
                className={`py-2.5 px-3 border-b-2 transition ${
                  activeDrawerTab === 'review'
                    ? 'border-aurum-400 text-aurum-300 font-bold'
                    : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
              >
                C. Tự Nhận Xét & Tâm Lý (Review)
              </button>
            </div>

            {/* Drawer Body */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
              {/* TAB A: VERIFIED FACTS */}
              {activeDrawerTab === 'facts' && (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-3 bg-charcoal-850 p-3 rounded border border-charcoal-750">
                    <div>
                      <span className="text-gray-400 block text-[11px]">Trạng Thái Lệnh:</span>
                      <span className="font-bold text-gray-200 block">{selectedTrade.state}</span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[11px]">Lý Do Đóng (Exit Cause):</span>
                      <span className="font-bold text-aurum-300 block">{selectedTrade.exit_cause || '—'}</span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[11px]">Entry Kế Hoạch vs Thực Tế:</span>
                      <span className="font-mono text-gray-200 block">
                        ${selectedTrade.planned_entry} → ${selectedTrade.actual_entry || 'Chưa khớp'}
                      </span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[11px]">Stop Loss & Take Profit:</span>
                      <span className="font-mono text-gray-200 block">
                        SL: ${selectedTrade.stop_loss} | TP: ${selectedTrade.take_profit}
                      </span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[11px]">Khối Lượng & Đòn Bẩy:</span>
                      <span className="text-gray-200 block">
                        {selectedTrade.quantity} oz ({selectedTrade.leverage || 30}x {selectedTrade.margin_mode || 'ISOLATED'})
                      </span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[11px]">Rủi Ro Ban Đầu (Initial Risk):</span>
                      <span className="text-gray-200 block">
                        ${selectedTrade.initial_risk_usdt} USDT ({selectedTrade.risk_pct || 1}%)
                      </span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[11px]">Phí (Fees) & Trượt Giá (Slippage):</span>
                      <span className="font-mono text-gray-200 block">
                        Fees: ${selectedTrade.fees || 0} | Slippage: ${selectedTrade.slippage || 0}
                      </span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[11px]">PnL Ròng & Realized R:</span>
                      <span
                        className={`font-bold block ${
                          (selectedTrade.realized_pnl_net ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        ${selectedTrade.realized_pnl_net?.toFixed(2) ?? '—'} (
                        {selectedTrade.realized_r !== null ? `${selectedTrade.realized_r.toFixed(2)}R` : '—'})
                      </span>
                    </div>
                  </div>

                  {/* Offline Recovery Badge */}
                  {selectedTrade.recovery_status && selectedTrade.recovery_status !== 'NONE' && (
                    <div className="bg-purple-950/60 border border-purple-800 p-3 rounded text-[11px] text-purple-200">
                      <strong>Đối Soát Ngoại Tuyến (Offline Recovery):</strong> Trạng thái{' '}
                      <code className="text-purple-300 font-bold">{selectedTrade.recovery_status}</code> (Độ tin cậy:{' '}
                      {selectedTrade.recovery_confidence || 'CONFIRMED'}). Không suy diễn từ tương lai.
                    </div>
                  )}

                  {/* Timestamps snapshot */}
                  <div className="bg-charcoal-850 p-3 rounded border border-charcoal-750 space-y-1 text-[11px] text-gray-400">
                    <div>
                      <span className="text-gray-300 font-medium">Tạo lệnh:</span>{' '}
                      {new Date(selectedTrade.created_at).toLocaleString('vi-VN')}
                    </div>
                    {selectedTrade.occurred_at && (
                      <div>
                        <span className="text-gray-300 font-medium">Thời điểm nến khớp:</span>{' '}
                        {new Date(selectedTrade.occurred_at).toLocaleString('vi-VN')}
                      </div>
                    )}
                    {selectedTrade.closed_at && (
                      <div>
                        <span className="text-gray-300 font-medium">Đóng lệnh lúc:</span>{' '}
                        {new Date(selectedTrade.closed_at).toLocaleString('vi-VN')}
                      </div>
                    )}
                  </div>

                  {/* V10: Lesson Rules Snapshot Linked to Trade */}
                  <div className="bg-charcoal-850 p-3 rounded border border-charcoal-750 space-y-2 text-[11px]">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-aurum-400 flex items-center gap-1.5">
                        <Brain className="w-3.5 h-3.5 text-aurum-400" />
                        Bài Học & Quy Tắc Quản Trị Tại Thời Điểm Vào Lệnh (Snapshot Trace):
                      </span>
                      <span className="text-[10px] text-gray-500">Bất biến lịch sử</span>
                    </div>

                    {selectedTrade.lessons_retrieved ? (
                      (() => {
                        let parsed: any[] = [];
                        try {
                          parsed = typeof selectedTrade.lessons_retrieved === 'string'
                            ? JSON.parse(selectedTrade.lessons_retrieved)
                            : selectedTrade.lessons_retrieved;
                        } catch (e) {
                          parsed = [];
                        }

                        if (!Array.isArray(parsed) || parsed.length === 0) {
                          return (
                            <p className="text-gray-400 italic">
                              Không có bài học nào được truy xuất tại thời điểm lệnh được tạo.
                            </p>
                          );
                        }

                        return (
                          <div className="space-y-1.5 mt-1">
                            {parsed.map((item: any, idx: number) => (
                              <div
                                key={idx}
                                className="p-2 rounded bg-charcoal-900 border border-charcoal-700 flex flex-wrap justify-between items-center gap-2"
                              >
                                <div className="flex items-center gap-1.5">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                                      item.severity === 'CRITICAL'
                                        ? 'bg-rose-950 text-rose-300 border border-rose-800'
                                        : item.severity === 'WARNING'
                                        ? 'bg-amber-950 text-amber-300 border border-amber-800'
                                        : 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                                    }`}
                                  >
                                    {item.severity || 'INFO'}
                                  </span>
                                  <span className="font-mono text-gray-200">
                                    Quy tắc #{item.lesson_id} (v{item.version || 1})
                                  </span>
                                  <span className="text-gray-400">· Tác động: {item.effect}</span>
                                </div>
                                <div className="flex items-center gap-2 text-[10px]">
                                  <span
                                    className={`font-semibold ${
                                      item.matched ? 'text-amber-400' : 'text-gray-500'
                                    }`}
                                  >
                                    {item.matched ? 'Kích hoạt khớp' : 'Không vi phạm'}
                                  </span>
                                  {item.reason_code && (
                                    <code className="text-gray-400 bg-charcoal-800 px-1 py-0.5 rounded">
                                      {item.reason_code}
                                    </code>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        );
                      })()
                    ) : (
                      <p className="text-gray-500 italic">
                        Lệnh legacy (trước V10) không có snapshot quy tắc bài học tại thời điểm vào lệnh.
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB B: HYPOTHESES */}
              {activeDrawerTab === 'hypotheses' && (
                <div className="space-y-3">
                  <div className="bg-charcoal-850 p-3.5 rounded border border-charcoal-750 space-y-2">
                    <h4 className="font-bold text-aurum-400 flex items-center gap-1.5">
                      <Sliders className="w-4 h-4 text-aurum-400" />
                      Phân Tích Cấu Trúc Smart Money Concept (SMC)
                    </h4>
                    <p className="text-gray-300 text-[11px]">
                      Kiểm định 4 bước: <strong>Liquidity Sweep</strong> → <strong>Displacement / MSS</strong> →{' '}
                      <strong>POI / FVG</strong> → <strong>Retest</strong>.
                    </p>
                    <div className="p-2.5 rounded bg-charcoal-900 border border-charcoal-700 text-[11px] text-gray-400">
                      Chiến lược: <strong className="text-gray-200">{selectedTrade.strategy_family || 'STANDARD_SMC'}</strong>
                      {selectedTrade.strategy_family === 'NY_QUOTA_PAPER' && (
                        <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 text-[10px]">
                          Phiên NY Quota Fallback
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="bg-charcoal-850 p-3.5 rounded border border-charcoal-750 text-[11px] text-gray-300 space-y-2">
                    <h4 className="font-bold text-gray-200">Nguyên Tắc Đánh Giá Khách Quan:</h4>
                    <ul className="list-disc list-inside space-y-1 text-gray-400">
                      <li>Lỗ không tự chứng minh là sai SMC; Thắng không tự chứng minh là đúng SMC.</li>
                      <li>Không quy kết "tin tức gây SL" hoặc "SL quá gần" nếu thiếu dữ liệu quote/ATR hỗ trợ.</li>
                      <li>Nếu dữ liệu thị trường ngoại tuyến chưa đủ bằng chứng: ghi nhận "Chưa đủ dữ kiện kết luận".</li>
                    </ul>
                  </div>
                </div>
              )}

              {/* TAB C: USER PSYCHOLOGY & NOTES */}
              {activeDrawerTab === 'review' && (
                <div className="space-y-4">
                  {/* Mode & Scores */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 bg-charcoal-850 p-3 rounded border border-charcoal-750">
                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Chế Độ Vào Lệnh:</label>
                      <select
                        value={reviewDraft.execution_mode || 'AUTO'}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, execution_mode: e.target.value })}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200"
                      >
                        <option value="AUTO">Hệ Thống Tự Động Khớp (AUTO)</option>
                        <option value="MANUAL">Người Dùng Quyết Định (MANUAL)</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Điểm Tự Tin (1-5):</label>
                      <input
                        type="number"
                        min="1"
                        max="5"
                        value={reviewDraft.confidence_score ?? 3}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, confidence_score: Number(e.target.value) })}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200 font-mono"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-gray-400 block mb-1">Điểm Kỷ Luật (1-5):</label>
                      <input
                        type="number"
                        min="1"
                        max="5"
                        value={reviewDraft.discipline_score ?? 3}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, discipline_score: Number(e.target.value) })}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-xs text-gray-200 font-mono"
                      />
                    </div>
                  </div>

                  {/* Emotions Selector */}
                  <div className="space-y-1.5">
                    <label className="font-semibold text-gray-300 block">
                      Trạng Thái Cảm Xúc / Tâm Lý Đi Kèm:
                    </label>
                    <div className="flex flex-wrap gap-2">
                      {EMOTIONS_LIST.map((emo) => {
                        const isSelected = (reviewDraft.emotions || []).includes(emo);
                        return (
                          <button
                            key={emo}
                            type="button"
                            onClick={() => {
                              const current = reviewDraft.emotions || [];
                              const next = isSelected
                                ? current.filter((x) => x !== emo)
                                : [...current, emo];
                              setReviewDraft({ ...reviewDraft, emotions: next });
                            }}
                            className={`px-2.5 py-1 rounded text-[11px] font-semibold transition border ${
                              isSelected
                                ? 'bg-indigo-600 border-indigo-400 text-white'
                                : 'bg-charcoal-850 border-charcoal-700 text-gray-400 hover:text-gray-200'
                            }`}
                          >
                            {isSelected ? '✓ ' : '+ '}
                            {emo}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Psychology before / during / after */}
                  <div className="space-y-3">
                    <div>
                      <label className="text-[11px] text-gray-300 font-semibold block mb-1">
                        Tâm Lý Trước Khi Vào Lệnh (Before Entry):
                      </label>
                      <textarea
                        value={reviewDraft.psychology_before || ''}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, psychology_before: e.target.value })}
                        placeholder="Có nóng vội? Có chờ đúng nến đóng cửa? Có bị ảnh hưởng bởi lệnh trước?..."
                        rows={2}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-gray-300 font-semibold block mb-1">
                        Tâm Lý Trong Khi Giữ Lệnh (During Trade):
                      </label>
                      <textarea
                        value={reviewDraft.psychology_during || ''}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, psychology_during: e.target.value })}
                        placeholder="Có bồn chồn muốn dời SL/TP? Có nhìn chart từng giây không?..."
                        rows={2}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-gray-300 font-semibold block mb-1">
                        Tâm Lý Sau Khi Lệnh Đóng (After Exit):
                      </label>
                      <textarea
                        value={reviewDraft.psychology_after || ''}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, psychology_after: e.target.value })}
                        placeholder="Cảm giác thế nào? Có muốn vào lệnh bù ngay (revenge trade)?..."
                        rows={2}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                      />
                    </div>
                  </div>

                  {/* Postmortem Analysis: Mistakes, Well, Plan */}
                  <div className="space-y-3 pt-2 border-t border-charcoal-750">
                    <div>
                      <label className="text-[11px] text-rose-300 font-semibold block mb-1">
                        Sai Lầm Mắc Phải (Nếu Có):
                      </label>
                      <textarea
                        value={reviewDraft.mistakes || ''}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, mistakes: e.target.value })}
                        placeholder="VD: Vào sớm khi chưa có nến xác nhận MSS; Dời SL ngược hướng..."
                        rows={2}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-emerald-300 font-semibold block mb-1">
                        Điều Đã Làm Tốt (What Went Well):
                      </label>
                      <textarea
                        value={reviewDraft.what_went_well || ''}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, what_went_well: e.target.value })}
                        placeholder="VD: Tuân thủ đúng khối lượng 1% rủi ro, không dời SL bừa bãi..."
                        rows={2}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-aurum-300 font-semibold block mb-1">
                        Kế Hoạch Cải Thiện Cho Lệnh Sau:
                      </label>
                      <textarea
                        value={reviewDraft.improvement_plan || ''}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, improvement_plan: e.target.value })}
                        placeholder="Hành động cụ thể sẽ áp dụng trong các setup kế tiếp..."
                        rows={2}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-gray-400 font-semibold block mb-1">
                        Ghi Chú Tự Do (Free Notes):
                      </label>
                      <textarea
                        value={reviewDraft.user_notes || ''}
                        onChange={(e) => setReviewDraft({ ...reviewDraft, user_notes: e.target.value })}
                        placeholder="Ghi chú thêm về bối cảnh thị trường, phiên giao dịch..."
                        rows={2}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                      />
                    </div>
                  </div>

                  {/* Review Error Banner */}
                  {reviewError && (
                    <div className="p-3 rounded bg-rose-950/90 border border-rose-700 text-rose-300 text-xs">
                      {reviewError}
                    </div>
                  )}

                  {/* Save Review Button */}
                  <div className="pt-2 flex justify-between items-center border-t border-charcoal-750">
                    <span className="text-[10px] text-gray-500">
                      Phiên bản đánh giá: rev #{reviewData?.revision ?? 0}
                    </span>
                    <button
                      type="button"
                      onClick={handleSaveReview}
                      disabled={isSavingReview}
                      className="flex items-center gap-1.5 px-4 py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition"
                    >
                      {isSavingReview ? (
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <CheckCircle2 className="w-3.5 h-3.5" />
                      )}
                      <span>{isSavingReview ? 'Đang lưu...' : 'Lưu Ghi Chú & Đánh Giá'}</span>
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* V10: Structured Rule Editor Modal */}
      {structuredModalLesson && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 backdrop-blur-sm animate-fadeIn">
          <div className="bg-charcoal-900 border border-charcoal-700 w-full max-w-xl rounded-xl shadow-2xl overflow-hidden flex flex-col text-xs">
            {/* Modal Header */}
            <div className="p-4 bg-charcoal-850 border-b border-charcoal-750 flex justify-between items-center">
              <div>
                <h3 className="font-bold text-gray-100 text-sm flex items-center gap-2">
                  <Sliders className="w-4 h-4 text-aurum-400" />
                  Cấu Hình Quy Tắc Bài Học #{structuredModalLesson.id}
                </h3>
                <p className="text-[11px] text-gray-400 mt-0.5 truncate max-w-md">
                  {structuredModalLesson.title}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setStructuredModalLesson(null)}
                className="p-1 rounded hover:bg-charcoal-750 text-gray-400 hover:text-gray-200 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-4 space-y-4 max-h-[75vh] overflow-y-auto">
              {/* 1. Phân Loại Màu Sắc (Severity & Effect) */}
              <div className="space-y-2">
                <label className="font-semibold text-gray-300 block">
                  1. Mức Độ Tác Động & Phân Loại Màu (V10):
                </label>
                <div className="grid grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setRuleSeverity('INFO');
                      setRuleEffect('ANNOTATE');
                    }}
                    className={`p-2.5 rounded-lg border text-left transition flex flex-col gap-1 ${
                      ruleSeverity === 'INFO'
                        ? 'bg-emerald-950/80 border-emerald-500 text-emerald-200'
                        : 'bg-charcoal-850 border-charcoal-750 text-gray-400 hover:border-charcoal-650'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-bold text-xs">
                      <Info className="w-3.5 h-3.5 text-emerald-400" />
                      <span>XANH (Tham Khảo)</span>
                    </div>
                    <span className="text-[10px] leading-tight opacity-80">
                      Ghi chú hiển thị trong kế hoạch, không bao giờ chặn hoặc sửa lệnh.
                    </span>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setRuleSeverity('WARNING');
                      setRuleEffect('WARN_ENTRY');
                    }}
                    className={`p-2.5 rounded-lg border text-left transition flex flex-col gap-1 ${
                      ruleSeverity === 'WARNING'
                        ? 'bg-amber-950/80 border-amber-500 text-amber-200'
                        : 'bg-charcoal-850 border-charcoal-750 text-gray-400 hover:border-charcoal-650'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-bold text-xs">
                      <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                      <span>VÀNG (Cảnh Báo)</span>
                    </div>
                    <span className="text-[10px] leading-tight opacity-80">
                      Cảnh báo số liệu thật trước entry, ghi trace nhưng không chặn Auto.
                    </span>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setRuleSeverity('CRITICAL');
                      setRuleEffect('BLOCK_ENTRY');
                    }}
                    className={`p-2.5 rounded-lg border text-left transition flex flex-col gap-1 ${
                      ruleSeverity === 'CRITICAL'
                        ? 'bg-rose-950/80 border-rose-500 text-rose-200'
                        : 'bg-charcoal-850 border-charcoal-750 text-gray-400 hover:border-charcoal-650'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-bold text-xs">
                      <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
                      <span>ĐỎ (Chặn Entry)</span>
                    </div>
                    <span className="text-[10px] leading-tight opacity-80">
                      Chặn Arm hoặc Fill mới khi điều kiện cấu trúc vi phạm.
                    </span>
                  </button>
                </div>
              </div>

              {/* 2. Structured Predicate (Only for WARNING and CRITICAL) */}
              {ruleSeverity !== 'INFO' ? (
                <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750 space-y-3">
                  <div className="flex justify-between items-center">
                    <label className="font-semibold text-gray-300">
                      2. Điều Kiện Kỹ Thuật Có Cấu Trúc (Predicate Whitelist):
                    </label>
                    <span className="text-[10px] text-gray-400">Không hỗ trợ nhập văn bản tự do</span>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <div>
                      <label className="text-[10px] text-gray-400 block mb-1">Chỉ Số Đánh Giá (Metric):</label>
                      <select
                        value={ruleMetric}
                        onChange={(e) => setRuleMetric(e.target.value)}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2 py-1.5 text-xs text-gray-200"
                      >
                        <option value="spread">Spread vàng (pts)</option>
                        <option value="net_rr">Net R:R tối thiểu (&gt;= 2.0)</option>
                        <option value="distance_to_entry_atr">Khoảng cách Entry (ATR)</option>
                        <option value="quote_age_ms">Độ trễ báo giá (ms)</option>
                        <option value="evidence.sweep_detected">Sweep thanh khoản (Boolean)</option>
                        <option value="evidence.fvg_found">FVG hợp lệ (Boolean)</option>
                      </select>
                    </div>

                    <div>
                      <label className="text-[10px] text-gray-400 block mb-1">Toán Tử (Operator):</label>
                      <select
                        value={ruleOperator}
                        onChange={(e) => setRuleOperator(e.target.value)}
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2 py-1.5 text-xs text-gray-200"
                      >
                        <option value=">=">&gt;= (Lớn hơn hoặc bằng)</option>
                        <option value="<=">&lt;= (Nhỏ hơn hoặc bằng)</option>
                        <option value="==">== (Bằng)</option>
                        <option value="!=">!= (Khác)</option>
                      </select>
                    </div>

                    <div>
                      <label className="text-[10px] text-gray-400 block mb-1">Ngưỡng (Threshold):</label>
                      <input
                        type="text"
                        value={ruleThreshold}
                        onChange={(e) => setRuleThreshold(e.target.value)}
                        placeholder="vd: 0.40 hoặc 2.0"
                        className="w-full bg-charcoal-900 border border-charcoal-700 rounded px-2 py-1.5 text-xs text-gray-200 font-mono"
                      />
                    </div>
                  </div>

                  {/* Live Validation Button */}
                  <div className="pt-2 flex justify-between items-center border-t border-charcoal-750">
                    <span className="text-[10px] text-gray-400">
                      Kiểm định tính hợp lệ và an toàn trước khi lưu
                    </span>
                    <button
                      type="button"
                      onClick={handleValidatePredicate}
                      disabled={isValidatingPredicate}
                      className="px-3 py-1 bg-charcoal-800 hover:bg-charcoal-700 text-gray-200 rounded text-[11px] font-medium border border-charcoal-700 transition"
                    >
                      {isValidatingPredicate ? 'Đang kiểm tra...' : 'Kiểm Định Quy Tắc'}
                    </button>
                  </div>

                  {/* Validation Result Banner */}
                  {ruleValidationResult && (
                    <div
                      className={`p-2.5 rounded text-[11px] border ${
                        ruleValidationResult.is_valid
                          ? 'bg-emerald-950/80 border-emerald-700 text-emerald-300'
                          : 'bg-rose-950/80 border-rose-700 text-rose-300'
                      }`}
                    >
                      <div className="font-bold mb-0.5">
                        {ruleValidationResult.is_valid ? '✓ Quy tắc hợp lệ' : '✗ Quy tắc không hợp lệ'}
                      </div>
                      <p>{ruleValidationResult.message}</p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-750 text-[11px] text-gray-400">
                  <span className="text-emerald-400 font-semibold">Quy tắc Xanh:</span> Không cần cấu hình điều kiện kỹ thuật. Quy tắc này đóng vai trò lưu ý bối cảnh và hiển thị tham khảo trong bảng kế hoạch lệnh.
                </div>
              )}

              {/* 3. Action Rule / Notes */}
              <div className="space-y-1.5">
                <label className="font-semibold text-gray-300 block">
                  3. Diễn Giải Quy Tắc (Action Rule Text):
                </label>
                <textarea
                  value={ruleActionRule}
                  onChange={(e) => setRuleActionRule(e.target.value)}
                  rows={2}
                  className="w-full bg-charcoal-850 border border-charcoal-700 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-aurum-500"
                  placeholder="Ghi chú giải thích cho nhà giao dịch khi quy tắc được kích hoạt..."
                />
              </div>

              {/* Error Message */}
              {ruleSaveError && (
                <div className="p-2.5 rounded bg-rose-950 border border-rose-700 text-rose-300 text-xs">
                  {ruleSaveError}
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="p-3 bg-charcoal-850 border-t border-charcoal-750 flex justify-between items-center">
              <span className="text-[10px] text-gray-500 font-mono">
                Revision #{structuredModalLesson.revision || 0}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setStructuredModalLesson(null)}
                  className="px-3 py-1.5 bg-charcoal-800 text-gray-400 rounded text-xs hover:text-gray-200"
                >
                  Hủy
                </button>
                <button
                  type="button"
                  onClick={handleSaveStructuredRule}
                  disabled={isSavingRule}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded text-xs transition"
                >
                  {isSavingRule ? (
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  )}
                  <span>{isSavingRule ? 'Đang lưu...' : 'Lưu Quy Tắc'}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

