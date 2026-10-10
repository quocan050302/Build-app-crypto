import React, { useState, useEffect, useCallback } from 'react';
import { api, extractErrorMessage, type ResearchReportItem } from './api/client';
import {
  Calendar,
  Clock,
  Compass,
  Layers,
  TrendingUp,
  TrendingDown,
  ShieldCheck,
  FileText,
  RotateCw,
  Eye,
  Award,
  BookOpen,
  Filter
} from 'lucide-react';

export interface ResearchTabProps {
  onNotify?: (title: string, msg: string, type: 'info' | 'warn' | 'success') => void;
  onSessionInfoChange?: (info: any) => void;
}

export const ResearchTab: React.FC<ResearchTabProps> = ({ onNotify, onSessionInfoChange }) => {
  // Filter States
  const [selectedDate, setSelectedDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [timeOfDay, setTimeOfDay] = useState<string>('08:30');
  const [selectedSession, setSelectedSession] = useState<string>('NEW_YORK');
  const [dateBasis, setDateBasis] = useState<'VN_DATE' | 'NY_SESSION_DATE'>('VN_DATE');

  // Data States
  const [reports, setReports] = useState<ResearchReportItem[]>([]);
  const [selectedReport, setSelectedReport] = useState<ResearchReportItem | null>(null);
  const [sessionInfo, setSessionInfo] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [generating, setGenerating] = useState<boolean>(false);

  // Post-session review state
  const [reviewData, setReviewData] = useState<any | null>(null);
  const [loadingReview, setLoadingReview] = useState<boolean>(false);

  // Load reports for current filter
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
      setSessionInfo(res.session_info);
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

  // Handle generation of new report at chosen date & time
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
        onNotify('Khởi Tạo Thành Công', `Đã tạo báo cáo nghiên cứu cho ngày ${selectedDate} (${selectedSession})`, 'success');
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

  // Handle post-session review
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

  // Quick Date Setters
  const setQuickDate = (daysAgo: number) => {
    const d = new Date();
    d.setDate(d.getDate() - daysAgo);
    setSelectedDate(d.toISOString().slice(0, 10));
  };

  const isHistorical = selectedDate < todayStr;
  const scenarios = selectedReport?.structured_scenarios || selectedReport?.scenarios || {};
  const bullish = scenarios?.bullish;
  const bearish = scenarios?.bearish;
  const noTrade = scenarios?.no_trade;

  return (
    <div className="flex-1 flex flex-col gap-4 overflow-y-auto pr-1">
      {/* 1. Header & Filter Bar */}
      <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow-md flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-aurum-500/10 border border-aurum-500/30 rounded-lg text-aurum-400">
              <Compass className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-base font-bold text-gray-100 flex items-center gap-2">
                Nghiên Cứu Phiên & Ngày (Daily & Session Research)
                <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold ${
                  isHistorical
                    ? 'bg-purple-500/10 text-purple-400 border-purple-500/30'
                    : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                }`}>
                  {isHistorical ? 'PHÂN TÍCH QUÁ KHỨ (HISTORICAL)' : 'HIỆN TẠI (CURRENT LIVE)'}
                </span>
              </h2>
              <p className="text-xs text-gray-400">
                Khảo sát thị trường XAUUSDT theo thời điểm (As-Of), nhận diện cấu trúc SMC, bản đồ thanh khoản và kịch bản Net R:R hợp lệ.
              </p>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleGenerateReport}
              disabled={generating}
              className="px-4 py-2 bg-aurum-500 hover:bg-aurum-400 text-charcoal-950 font-bold rounded-lg shadow text-xs flex items-center gap-1.5 transition disabled:opacity-50"
            >
              <RotateCw className={`w-3.5 h-3.5 ${generating ? 'animate-spin' : ''}`} />
              {generating ? 'Đang phân tích...' : 'Phân Tích Tại Thời Điểm Đã Chọn'}
            </button>
            {selectedReport && (
              <button
                onClick={handleLoadReview}
                disabled={loadingReview}
                className="px-3 py-2 bg-charcoal-750 hover:bg-charcoal-700 text-aurum-300 font-medium rounded-lg border border-aurum-500/30 text-xs flex items-center gap-1.5 transition"
              >
                <Eye className="w-3.5 h-3.5" />
                {loadingReview ? 'Đang đánh giá...' : 'Xem Lại Sau Phiên (MFE/MAE)'}
              </button>
            )}
          </div>
        </div>

        {/* Filters Controls */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 pt-2 border-t border-charcoal-750 text-xs">
          {/* Date Picker & Quick Buttons */}
          <div className="flex flex-col gap-1.5">
            <span className="text-gray-400 font-medium flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5 text-aurum-400" />
              Ngày nghiên cứu:
            </span>
            <input
              type="date"
              value={selectedDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-gray-200 focus:outline-none focus:border-aurum-500 font-mono"
            />
            <div className="flex items-center gap-1 mt-0.5">
              <button onClick={() => setQuickDate(0)} className="px-2 py-0.5 rounded bg-charcoal-750 hover:bg-charcoal-700 text-gray-300 text-[10px]">Hôm nay</button>
              <button onClick={() => setQuickDate(1)} className="px-2 py-0.5 rounded bg-charcoal-750 hover:bg-charcoal-700 text-gray-300 text-[10px]">Hôm qua</button>
              <button onClick={() => setQuickDate(7)} className="px-2 py-0.5 rounded bg-charcoal-750 hover:bg-charcoal-700 text-gray-300 text-[10px]">7 ngày</button>
              <button onClick={() => setQuickDate(30)} className="px-2 py-0.5 rounded bg-charcoal-750 hover:bg-charcoal-700 text-gray-300 text-[10px]">30 ngày</button>
            </div>
          </div>

          {/* Time of Day */}
          <div className="flex flex-col gap-1.5">
            <span className="text-gray-400 font-medium flex items-center gap-1">
              <Clock className="w-3.5 h-3.5 text-aurum-400" />
              Thời điểm phân tích (As-Of):
            </span>
            <input
              type="time"
              value={timeOfDay}
              onChange={(e) => setTimeOfDay(e.target.value)}
              className="bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-gray-200 focus:outline-none focus:border-aurum-500 font-mono"
            />
            <span className="text-[10px] text-gray-400">
              Khóa dữ liệu: Chỉ dùng nến và cấu trúc đã đóng trước giờ này.
            </span>
          </div>

          {/* Session Selector */}
          <div className="flex flex-col gap-1.5">
            <span className="text-gray-400 font-medium flex items-center gap-1">
              <Layers className="w-3.5 h-3.5 text-aurum-400" />
              Phiên giao dịch:
            </span>
            <select
              value={selectedSession}
              onChange={(e) => setSelectedSession(e.target.value)}
              className="bg-charcoal-900 border border-charcoal-700 rounded px-2.5 py-1.5 text-gray-200 focus:outline-none focus:border-aurum-500"
            >
              <option value="NEW_YORK">Phiên Mỹ (New York 08:00 - 17:00 NY)</option>
              <option value="LONDON">Phiên Âu (London 08:00 - 17:00 LN)</option>
              <option value="TOKYO">Phiên Á (Tokyo 09:00 - 18:00 TK)</option>
              <option value="ALL">Tất Cả Các Phiên</option>
            </select>
            <span className="text-[10px] text-gray-400">
              Ưu tiên phiên Mỹ: Khung giờ biến động vàng thanh khoản cao nhất.
            </span>
          </div>

          {/* Date Basis Toggle */}
          <div className="flex flex-col gap-1.5">
            <span className="text-gray-400 font-medium flex items-center gap-1">
              <Filter className="w-3.5 h-3.5 text-aurum-400" />
              Quy chuẩn ngày:
            </span>
            <div className="flex rounded bg-charcoal-900 border border-charcoal-700 p-0.5">
              <button
                onClick={() => setDateBasis('VN_DATE')}
                className={`flex-1 py-1 rounded text-[11px] font-medium transition ${
                  dateBasis === 'VN_DATE' ? 'bg-aurum-500/20 text-aurum-300 font-bold' : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                Giờ VN (UTC+7)
              </button>
              <button
                onClick={() => setDateBasis('NY_SESSION_DATE')}
                className={`flex-1 py-1 rounded text-[11px] font-medium transition ${
                  dateBasis === 'NY_SESSION_DATE' ? 'bg-aurum-500/20 text-aurum-300 font-bold' : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                Phiên NY (Trading Day)
              </button>
            </div>
            <span className="text-[10px] text-gray-400 font-mono">
              {sessionInfo ? `Hiện tại VN: ${sessionInfo.vn_time}` : ''}
            </span>
          </div>
        </div>
      </div>

      {/* Loading Indicator */}
      {loading && (
        <div className="text-center py-2 text-xs text-aurum-400 flex items-center justify-center gap-2 bg-charcoal-850 p-2 rounded-lg border border-charcoal-750">
          <RotateCw className="w-3.5 h-3.5 animate-spin" />
          Đang tải dữ liệu báo cáo nghiên cứu...
        </div>
      )}

      {/* Reports History Tabs */}
      {reports.length > 1 && (
        <div className="bg-charcoal-850 p-2.5 rounded-lg border border-charcoal-750 flex items-center gap-2 text-xs overflow-x-auto">
          <span className="text-gray-400 shrink-0 font-medium">Báo cáo đã lưu ({reports.length}):</span>
          {reports.map((rep) => (
            <button
              key={rep.id}
              onClick={() => { setSelectedReport(rep); setReviewData(null); }}
              className={`px-2.5 py-1 rounded text-[11px] font-mono transition shrink-0 ${
                selectedReport?.id === rep.id
                  ? 'bg-aurum-500/20 text-aurum-300 border border-aurum-500/40 font-bold'
                  : 'bg-charcoal-900 text-gray-400 hover:text-gray-200 border border-charcoal-750'
              }`}
            >
              #{rep.id} · {rep.session_name} ({new Date(rep.created_at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })})
            </button>
          ))}
        </div>
      )}

      {/* 2. Main Content Cards */}
      {selectedReport ? (
        <div className="flex flex-col gap-4">
          {/* Card 1: Market Regime & Executive Summary */}
          <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-charcoal-750 pb-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-400">Trạng thái thị trường:</span>
                <span className="px-2.5 py-1 rounded-md text-xs font-bold bg-aurum-500/20 text-aurum-300 border border-aurum-500/40">
                  {selectedReport.market_regime || 'XU HƯỚNG BÌNH THƯỜNG'}
                </span>
                <span className="text-[11px] text-gray-400 font-mono">
                  (Độ tin cậy Heuristic: {((selectedReport.quality_score ?? 0.8) * 100).toFixed(0)}% — Không phải xác suất thắng)
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-gray-400">Thời điểm As-Of:</span>
                <span className="font-mono text-gray-200">
                  {selectedReport.as_of_ms ? new Date(selectedReport.as_of_ms).toLocaleString('vi-VN') : 'Hiện tại'}
                </span>
                <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 text-[10px]">
                  {selectedReport.data_coverage_status || 'PROVIDED_VALIDATED'}
                </span>
              </div>
            </div>

            {/* Beginner Explanation Banner */}
            <div className="bg-charcoal-900 p-3 rounded-lg border border-charcoal-750 text-xs text-gray-300 flex items-start gap-2.5">
              <BookOpen className="w-4 h-4 text-aurum-400 shrink-0 mt-0.5" />
              <div>
                <strong className="text-aurum-400">Hướng dẫn cho người mới:</strong> Báo cáo này giúp bạn nắm bắt bối cảnh vàng tại thời điểm bạn chọn. Bạn sẽ biết ngay xu hướng lớn (D/4H), vùng cản cần lưu ý và điều kiện kỹ thuật cụ thể cần xuất hiện trước khi vào lệnh. Nếu thị trường chưa đủ điều kiện, hệ thống sẽ đề xuất <strong>Chờ đợi (No-Trade)</strong> để bảo vệ vốn.
              </div>
            </div>

            {/* Timeframe Matrix */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
              <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                <span className="text-[10px] text-gray-400">Khung Ngày (Daily)</span>
                <div className="text-xs font-bold text-gray-200 mt-0.5">{selectedReport.d_4h_bias}</div>
              </div>
              <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                <span className="text-[10px] text-gray-400">Đồng Thuận 1 Giờ (H1)</span>
                <div className="text-xs font-bold text-gray-200 mt-0.5">{selectedReport.h1_alignment}</div>
              </div>
              <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                <span className="text-[10px] text-gray-400">Phiên Giao Dịch</span>
                <div className="text-xs font-bold text-aurum-400 mt-0.5">{selectedReport.session_name}</div>
              </div>
              <div className="bg-charcoal-900 p-2.5 rounded-lg border border-charcoal-750">
                <span className="text-[10px] text-gray-400">Phiên Bản Chiến Lược</span>
                <div className="text-xs font-bold text-gray-200 mt-0.5">{selectedReport.strategy_version}</div>
              </div>
            </div>
          </div>

          {/* Card 2: Validated Scenarios (LONG / SHORT / NO-TRADE) */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {/* Bullish Scenario */}
            <div className={`p-4 rounded-xl border flex flex-col justify-between gap-3 ${
              bullish?.is_valid ? 'bg-charcoal-850 border-emerald-500/40' : 'bg-charcoal-850/60 border-charcoal-750 opacity-80'
            }`}>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold text-emerald-400 flex items-center gap-1.5">
                    <TrendingUp className="w-4 h-4" />
                    Kịch Bản Mua (LONG)
                  </span>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    bullish?.is_valid ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'bg-rose-500/10 text-rose-400'
                  }`}>
                    {bullish?.is_valid ? 'ĐỦ ĐIỀU KIỆN' : 'CHƯA ĐẠT CHUẨN'}
                  </span>
                </div>

                <div className="space-y-1.5 text-xs text-gray-300">
                  <div>Vùng vào lệnh: <strong className="text-gray-100">{bullish?.entry_zone || 'Đang cập nhật'}</strong></div>
                  <div>Cắt lỗ (SL): <strong className="text-rose-400">${bullish?.stop_loss ?? 'N/A'}</strong></div>
                  <div>Chốt lời (TP): <strong className="text-emerald-400">${bullish?.take_profit ?? 'N/A'}</strong></div>
                  <div>Tỷ lệ Net R:R: <strong className="text-aurum-400">{bullish?.net_rr ?? 0}R</strong> (Gross: {bullish?.gross_rr ?? 0}R)</div>
                </div>

                <div className="mt-3 p-2 bg-charcoal-900 rounded border border-charcoal-750 text-[11px] text-gray-300">
                  <strong className="text-gray-200">Điều kiện kích hoạt:</strong> {bullish?.trigger_condition}
                </div>
              </div>

              {bullish?.invalidation_reasons && bullish.invalidation_reasons.length > 0 && (
                <div className="text-[10px] text-rose-400 bg-rose-500/10 p-2 rounded border border-rose-500/20">
                  {bullish.invalidation_reasons[0]}
                </div>
              )}
            </div>

            {/* Bearish Scenario */}
            <div className={`p-4 rounded-xl border flex flex-col justify-between gap-3 ${
              bearish?.is_valid ? 'bg-charcoal-850 border-rose-500/40' : 'bg-charcoal-850/60 border-charcoal-750 opacity-80'
            }`}>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold text-rose-400 flex items-center gap-1.5">
                    <TrendingDown className="w-4 h-4" />
                    Kịch Bản Bán (SHORT)
                  </span>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    bearish?.is_valid ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40' : 'bg-rose-500/10 text-rose-400'
                  }`}>
                    {bearish?.is_valid ? 'ĐỦ ĐIỀU KIỆN' : 'CHƯA ĐẠT CHUẨN'}
                  </span>
                </div>

                <div className="space-y-1.5 text-xs text-gray-300">
                  <div>Vùng vào lệnh: <strong className="text-gray-100">{bearish?.entry_zone || 'Đang cập nhật'}</strong></div>
                  <div>Cắt lỗ (SL): <strong className="text-rose-400">${bearish?.stop_loss ?? 'N/A'}</strong></div>
                  <div>Chốt lời (TP): <strong className="text-emerald-400">${bearish?.take_profit ?? 'N/A'}</strong></div>
                  <div>Tỷ lệ Net R:R: <strong className="text-aurum-400">{bearish?.net_rr ?? 0}R</strong> (Gross: {bearish?.gross_rr ?? 0}R)</div>
                </div>

                <div className="mt-3 p-2 bg-charcoal-900 rounded border border-charcoal-750 text-[11px] text-gray-300">
                  <strong className="text-gray-200">Điều kiện kích hoạt:</strong> {bearish?.trigger_condition}
                </div>
              </div>

              {bearish?.invalidation_reasons && bearish.invalidation_reasons.length > 0 && (
                <div className="text-[10px] text-rose-400 bg-rose-500/10 p-2 rounded border border-rose-500/20">
                  {bearish.invalidation_reasons[0]}
                </div>
              )}
            </div>

            {/* No-Trade Default Scenario */}
            <div className="bg-charcoal-850 p-4 rounded-xl border border-aurum-500/40 flex flex-col justify-between gap-3">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold text-aurum-400 flex items-center gap-1.5">
                    <ShieldCheck className="w-4 h-4" />
                    Chờ Đợi (No-Trade Default)
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-aurum-500/20 text-aurum-300 border border-aurum-500/40">
                    BẢO VỆ VỐN
                  </span>
                </div>

                <div className="space-y-2 text-xs text-gray-300">
                  <p>{noTrade?.capital_preservation_message || 'Bảo toàn vốn giả lập là ưu tiên hàng đầu.'}</p>
                  <div className="p-2 bg-charcoal-900 rounded border border-charcoal-750 text-[11px]">
                    <strong className="text-gray-200">Khi nào nên đứng ngoài?</strong>
                    <ul className="list-disc list-inside mt-1 text-gray-400 space-y-1">
                      {noTrade?.reasons?.map((r: string, idx: number) => (
                        <li key={idx}>{r}</li>
                      )) || <li>Thị trường chưa có tín hiệu quét thanh khoản và nến xác nhận rõ ràng.</li>}
                    </ul>
                  </div>
                </div>
              </div>

              <div className="text-[10px] text-aurum-300 bg-aurum-500/10 p-2 rounded border border-aurum-500/20 font-medium">
                {noTrade?.quota_compliance || 'Mục tiêu: 1 cơ hội chất lượng/ngày trong phiên Mỹ.'}
              </div>
            </div>
          </div>

          {/* Card 3: Post-Session Outcome & MFE/MAE (When user clicks 'Xem Lại Sau Phiên') */}
          {reviewData && (
            <div className="bg-charcoal-850 p-4 rounded-xl border border-aurum-500/50 shadow flex flex-col gap-3">
              <div className="flex items-center justify-between border-b border-charcoal-750 pb-2.5">
                <div className="flex items-center gap-2">
                  <Award className="w-4 h-4 text-aurum-400" />
                  <h3 className="text-xs font-bold text-gray-100">
                    Đánh Giá Sau Phiên & Biên Độ MFE/MAE (Post-Session Review)
                  </h3>
                </div>
                <span className="text-[10px] text-gray-400 font-mono">
                  Đã đánh giá qua {reviewData.eval_candles_count} nến tiếp theo
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                {/* Bullish Review */}
                <div className="bg-charcoal-900 p-3 rounded-lg border border-charcoal-750">
                  <span className="font-bold text-emerald-400 block mb-1">Kết quả Kịch Bản Tăng (Bullish):</span>
                  <div className="space-y-1 text-gray-300">
                    <div>Kết quả quan sát: <strong className="text-gray-100">{reviewData.bullish_review?.outcome}</strong></div>
                    <div>Biên độ thuận lợi cực đại (MFE): <strong className="text-emerald-400">+{reviewData.bullish_review?.mfe_usd} USD (+{reviewData.bullish_review?.mfe_r}R)</strong></div>
                    <div>Biên độ bất lợi cực đại (MAE): <strong className="text-rose-400">-{reviewData.bullish_review?.mae_usd} USD (-{reviewData.bullish_review?.mae_r}R)</strong></div>
                  </div>
                </div>

                {/* Bearish Review */}
                <div className="bg-charcoal-900 p-3 rounded-lg border border-charcoal-750">
                  <span className="font-bold text-rose-400 block mb-1">Kết quả Kịch Bản Giảm (Bearish):</span>
                  <div className="space-y-1 text-gray-300">
                    <div>Kết quả quan sát: <strong className="text-gray-100">{reviewData.bearish_review?.outcome}</strong></div>
                    <div>Biên độ thuận lợi cực đại (MFE): <strong className="text-emerald-400">+{reviewData.bearish_review?.mfe_usd} USD (+{reviewData.bearish_review?.mfe_r}R)</strong></div>
                    <div>Biên độ bất lợi cực đại (MAE): <strong className="text-rose-400">-{reviewData.bearish_review?.mae_usd} USD (-{reviewData.bearish_review?.mae_r}R)</strong></div>
                  </div>
                </div>
              </div>

              {/* Attribution & Candidate Lesson */}
              {reviewData.bullish_review?.candidate_lesson && (
                <div className="bg-charcoal-900 p-3 rounded-lg border border-charcoal-750 text-xs text-gray-300">
                  <strong className="text-aurum-400 block mb-1">Đề xuất bài học kinh nghiệm (Chờ duyệt):</strong>
                  <p>{reviewData.bullish_review.candidate_lesson.proposed_reflection}</p>
                  <p className="mt-1 text-gray-400 italic">Hành động gợi ý: {reviewData.bullish_review.candidate_lesson.proposed_action}</p>
                  <span className="text-[10px] text-gray-400 block mt-2">
                    (Lưu ý: Bài học ở trạng thái CHỜ DUYỆT trong tab Nhật Ký & Bài Học; không tự động can thiệp vào bot trading live).
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Card 4: Full Narrative Analysis (Markdown Content) */}
          <div className="bg-charcoal-850 p-4 rounded-xl border border-charcoal-700 shadow flex flex-col gap-2">
            <h3 className="text-xs font-bold text-gray-300 flex items-center gap-1.5 border-b border-charcoal-750 pb-2">
              <FileText className="w-3.5 h-3.5 text-aurum-400" />
              Nội Dung Báo Cáo Phân Tích Chi Tiết
            </h3>
            <div className="text-xs text-gray-300 whitespace-pre-wrap font-mono leading-relaxed bg-charcoal-900 p-3 rounded-lg border border-charcoal-750 max-h-96 overflow-y-auto">
              {selectedReport.content_markdown}
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-charcoal-850 p-8 rounded-xl border border-charcoal-700 text-center text-gray-400 text-xs">
          <p>Không tìm thấy báo cáo phân tích nào cho ngày <strong>{selectedDate}</strong> ({selectedSession}).</p>
          <p className="mt-1 text-gray-400">Bấm nút <strong>"Phân Tích Tại Thời Điểm Đã Chọn"</strong> ở trên để tạo báo cáo nghiên cứu phiên này.</p>
        </div>
      )}
    </div>
  );
};
