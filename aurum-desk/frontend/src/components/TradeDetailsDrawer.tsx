import React, { useEffect, useRef } from 'react';
import type { SelectedTradeIntent } from '../App';
import type { DrawerSectionType } from './QuickDecisionSidebar';
import type { NYSessionStatus, TradingPolicy } from '../api/client';
import {
  X,
  CheckCircle2,
  XCircle,
  HelpCircle,
  BookOpen,
  ListOrdered,
  Shield,
  Clock,
  AlertTriangle,
  Info,
  Layers
} from 'lucide-react';
import { normalizeLessonItem, type NormalizedLessonItem } from '../types/lesson';

export interface TradeDetailsDrawerProps {
  isOpen: boolean;
  activeSection: DrawerSectionType;
  onSectionChange: (section: DrawerSectionType) => void;
  onClose: () => void;
  selectedIntent: SelectedTradeIntent | null;
  activePosition: any;
  upcomingSetups: any[];
  onFocusSetupOnChart: (setup: any) => void;
  onRefreshUpcoming?: () => void;
  nySession: NYSessionStatus | null;
  policy: TradingPolicy | null;
  leverage: number;
  marginMode: string;
  analysis: any;
}

export const TradeDetailsDrawer: React.FC<TradeDetailsDrawerProps> = ({
  isOpen,
  activeSection,
  onSectionChange,
  onClose,
  selectedIntent,
  activePosition,
  upcomingSetups,
  onFocusSetupOnChart,
  onRefreshUpcoming,
  nySession,
  policy,
  leverage,
  marginMode,
  analysis,
}) => {
  const drawerRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  // Focus trap / initial focus on open
  useEffect(() => {
    if (isOpen) {
      closeBtnRef.current?.focus();
    }
  }, [isOpen]);

  // Escape key handler
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  // Matching watch setup from list
  const currentSetup = selectedIntent
    ? upcomingSetups.find((s: any) => s.id === selectedIntent.setup_id)
    : null;

  const eligibility = selectedIntent?.eligibility || currentSetup?.eligibility;

  // Lessons normalized
  const normalizedBlockers: NormalizedLessonItem[] = (eligibility?.lesson_blockers || []).map(normalizeLessonItem);
  const normalizedWarnings: NormalizedLessonItem[] = (eligibility?.lesson_warnings || []).map(normalizeLessonItem);
  const normalizedAdvisories: NormalizedLessonItem[] = (eligibility?.lesson_advisories || []).map(normalizeLessonItem);

  // Conditions
  const conditionsMet = selectedIntent?.conditions_met || currentSetup?.conditions_met || [];
  const conditionsRemaining = selectedIntent?.conditions_remaining || currentSetup?.conditions_remaining || [];

  // Margin and sizing
  const qty = selectedIntent?.quantity || 0.01;
  const entry = selectedIntent?.plannedEntry || 0;
  const lev = selectedIntent?.leverage || leverage || 50;
  const marginReq = ((qty * entry) / lev).toFixed(2);
  const estimatedNotional = (qty * entry).toFixed(2);

  // Est liquidation buffer
  const liqEst = selectedIntent?.estimatedLiquidation || currentSetup?.estimated_liquidation;

  const sections: Array<{ id: DrawerSectionType; label: string; icon: any; count?: number }> = [
    { id: 'conditions', label: 'Điều Kiện', icon: CheckCircle2, count: conditionsRemaining.length },
    { id: 'lessons', label: 'Bài Học', icon: BookOpen, count: normalizedBlockers.length + normalizedWarnings.length },
    { id: 'upcoming', label: 'Lệnh Dự Kiến', icon: ListOrdered, count: upcomingSetups.length },
    { id: 'risk', label: 'Rủi Ro', icon: Shield },
    { id: 'session', label: 'Phiên & Chính Sách', icon: Clock },
  ];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Bảng chi tiết giao dịch và phân tích"
      className="fixed inset-0 z-40 overflow-hidden bg-black/60 backdrop-blur-xs flex justify-end animate-in fade-in duration-200"
    >
      {/* Click backdrop to close */}
      <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />

      <div
        ref={drawerRef}
        className="relative w-full max-w-[480px] bg-charcoal-900 border-l border-charcoal-750 h-full flex flex-col shadow-2xl z-10 animate-in slide-in-from-right duration-200"
      >
        {/* 1. Header (shrink-0) */}
        <div className="p-3.5 bg-charcoal-850 border-b border-charcoal-750 shrink-0 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-aurum-400" />
            <h3 className="font-bold text-sm text-white">Chi Tiết Giao Dịch & Phân Tích</h3>
            {selectedIntent && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-charcoal-750 text-aurum-300">
                {selectedIntent.direction} {selectedIntent.symbol || 'XAUUSDT'}
              </span>
            )}
          </div>

          <button
            ref={closeBtnRef}
            onClick={onClose}
            className="text-gray-400 hover:text-white p-1 rounded-lg hover:bg-charcoal-750 transition"
            title="Đóng bảng chi tiết (Esc)"
            aria-label="Đóng bảng chi tiết"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 2. Section Navigation Tabs (shrink-0) */}
        <div className="flex items-center bg-charcoal-900 border-b border-charcoal-800 text-xs px-2 overflow-x-auto shrink-0">
          {sections.map((sec) => {
            const Icon = sec.icon;
            const isActive = activeSection === sec.id;
            return (
              <button
                key={sec.id}
                type="button"
                onClick={() => onSectionChange(sec.id)}
                className={`flex items-center gap-1.5 px-3 py-2 border-b-2 font-medium transition whitespace-nowrap ${
                  isActive
                    ? 'border-aurum-500 text-aurum-400 font-bold bg-charcoal-850/50'
                    : 'border-transparent text-gray-400 hover:text-gray-200 hover:bg-charcoal-850/20'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{sec.label}</span>
                {sec.count != null && sec.count > 0 && (
                  <span className="px-1.5 py-0.2 text-[9px] rounded-full bg-charcoal-750 text-gray-300 font-mono">
                    {sec.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* 3. Section Content Body (flex-1 min-h-0 overflow-y-auto) */}
        <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 text-xs text-gray-300">
          {/* SECTION: CONDITIONS */}
          {activeSection === 'conditions' && (
            <div className="space-y-3.5">
              <div>
                <h4 className="font-bold text-white text-xs uppercase tracking-wider mb-1 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-aurum-400" />
                  Tiêu Chí SMC & Điều Kiện Kích Hoạt
                </h4>
                <p className="text-[11px] text-gray-400">
                  Biết trước các điều kiện cần thị trường đáp ứng trước khi chuyển trạng thái READY / ARMED.
                </p>
              </div>

              {/* Conditions Met */}
              <div className="space-y-1.5">
                <span className="text-[11px] font-semibold text-emerald-400 block">
                  ✓ Điều kiện đã đạt ({conditionsMet.length}):
                </span>
                {conditionsMet.length > 0 ? (
                  <div className="space-y-1">
                    {conditionsMet.map((c: string, idx: number) => (
                      <div key={idx} className="p-2 rounded bg-emerald-950/30 border border-emerald-800/40 text-[11px] flex items-start gap-2">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                        <span className="text-emerald-200">{c}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[11px] text-gray-500 italic pl-2">Chưa có điều kiện nào đạt.</p>
                )}
              </div>

              {/* Conditions Remaining */}
              <div className="space-y-1.5">
                <span className="text-[11px] font-semibold text-amber-400 block">
                  ⏳ Điều kiện đang chờ ({conditionsRemaining.length}):
                </span>
                {conditionsRemaining.length > 0 ? (
                  <div className="space-y-1">
                    {conditionsRemaining.map((c: string, idx: number) => (
                      <div key={idx} className="p-2 rounded bg-amber-950/30 border border-amber-800/40 text-[11px] flex items-start gap-2">
                        <Clock className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
                        <span className="text-amber-200">{c}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[11px] text-gray-500 italic pl-2">Đã thỏa mãn đầy đủ điều kiện.</p>
                )}
              </div>

              {/* Checklist from SMC Analysis */}
              {analysis?.checklist?.length > 0 && (
                <div className="space-y-1.5 pt-2 border-t border-charcoal-750">
                  <span className="text-[11px] font-semibold text-gray-300 block">
                    Bằng chứng phân tích nến ({analysis?.timeframe || '15M'}):
                  </span>
                  <div className="space-y-1">
                    {analysis.checklist.map((chk: any) => (
                      <div key={chk.id} className="p-2 rounded bg-charcoal-850 border border-charcoal-750 flex items-start gap-2 text-[11px]">
                        {chk.status === 'PASS' ? (
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                        ) : chk.status === 'FAIL' ? (
                          <XCircle className="w-3.5 h-3.5 text-rose-400 shrink-0 mt-0.5" />
                        ) : (
                          <HelpCircle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
                        )}
                        <div>
                          <span className="font-medium text-gray-200 block">{chk.label}</span>
                          <span className="text-gray-400 text-[10px]">{chk.detail}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* SECTION: LESSONS */}
          {activeSection === 'lessons' && (
            <div className="space-y-3.5">
              <div>
                <h4 className="font-bold text-white text-xs uppercase tracking-wider mb-1 flex items-center gap-1.5">
                  <BookOpen className="w-4 h-4 text-indigo-400" />
                  Quy Tắc Bài Học Đã Áp Dụng (Governed Lessons)
                </h4>
                <p className="text-[11px] text-gray-400">
                  Rào chắn kỷ luật được thiết lập từ các bài học kinh nghiệm trước đây để bảo vệ tài khoản.
                </p>
              </div>

              {/* Red Blockers */}
              {normalizedBlockers.length > 0 && (
                <div className="space-y-2">
                  <span className="text-[11px] font-bold text-rose-400 flex items-center gap-1.5">
                    <XCircle className="w-3.5 h-3.5 text-rose-400" />
                    Quy tắc chặn nghiêm ngặt (Màu Đỏ) — {normalizedBlockers.length} quy tắc
                  </span>
                  {normalizedBlockers.map((lb, idx) => (
                    <div key={idx} className="p-2.5 rounded bg-rose-950/50 border border-rose-800 text-[11px] space-y-1">
                      <div className="font-bold text-rose-300">
                        {lb.rule_id ? `Quy tắc #${lb.rule_id}: ` : ''}{lb.title}
                      </div>
                      <p className="text-rose-200/90 leading-snug">{lb.message}</p>
                      {lb.next_step && (
                        <p className="text-rose-400 text-[10px] italic">Gợi ý: {lb.next_step}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Yellow Warnings */}
              {normalizedWarnings.length > 0 && (
                <div className="space-y-2">
                  <span className="text-[11px] font-bold text-amber-400 flex items-center gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                    Cảnh báo kỷ luật (Màu Vàng) — {normalizedWarnings.length} quy tắc
                  </span>
                  {normalizedWarnings.map((lw, idx) => (
                    <div key={idx} className="p-2.5 rounded bg-amber-950/50 border border-amber-800 text-[11px] space-y-1">
                      <div className="font-bold text-amber-300">
                        {lw.rule_id ? `Quy tắc #${lw.rule_id}: ` : ''}{lw.title}
                      </div>
                      <p className="text-amber-200/90 leading-snug">{lw.message}</p>
                      {lw.next_step && (
                        <p className="text-amber-400 text-[10px] italic">Gợi ý: {lw.next_step}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Green Advisories */}
              {normalizedAdvisories.length > 0 && (
                <div className="space-y-2">
                  <span className="text-[11px] font-bold text-emerald-400 flex items-center gap-1.5">
                    <Info className="w-3.5 h-3.5 text-emerald-400" />
                    Khuyến nghị bài học (Màu Xanh) — {normalizedAdvisories.length} quy tắc
                  </span>
                  {normalizedAdvisories.map((la, idx) => (
                    <div key={idx} className="p-2.5 rounded bg-emerald-950/40 border border-emerald-800 text-[11px] space-y-1">
                      <div className="font-bold text-emerald-300">
                        {la.rule_id ? `Quy tắc #${la.rule_id}: ` : ''}{la.title}
                      </div>
                      <p className="text-emerald-200/90 leading-snug">{la.message}</p>
                    </div>
                  ))}
                </div>
              )}

              {normalizedBlockers.length === 0 && normalizedWarnings.length === 0 && normalizedAdvisories.length === 0 && (
                <div className="py-8 text-center text-gray-500 text-xs italic bg-charcoal-850 rounded border border-charcoal-750 p-4">
                  Không có quy tắc bài học nào bị vi phạm cho thiết lập này.
                </div>
              )}
            </div>
          )}

          {/* SECTION: UPCOMING SETUPS */}
          {activeSection === 'upcoming' && (
            <div className="space-y-3.5">
              <div className="flex items-center justify-between">
                <div>
                  <h4 className="font-bold text-white text-xs uppercase tracking-wider mb-0.5 flex items-center gap-1.5">
                    <ListOrdered className="w-4 h-4 text-emerald-400" />
                    Danh Sách Lệnh Dự Kiến ({upcomingSetups.length})
                  </h4>
                  <p className="text-[11px] text-gray-400">
                    Bấm vào từng lệnh để xem thước đo R:R tương ứng trên biểu đồ.
                  </p>
                </div>
                {onRefreshUpcoming && (
                  <button
                    type="button"
                    onClick={onRefreshUpcoming}
                    className="p-1 rounded bg-charcoal-800 hover:bg-charcoal-750 text-gray-300 text-xs border border-charcoal-700"
                    title="Làm mới danh sách"
                  >
                    <Clock className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>

              {upcomingSetups.length > 0 ? (
                <div className="space-y-2">
                  {upcomingSetups.map((st) => {
                    const isSelected = selectedIntent?.setup_id === st.id;
                    return (
                      <div
                        key={st.id}
                        onClick={() => {
                          onFocusSetupOnChart(st);
                        }}
                        className={`p-3 rounded-lg border cursor-pointer transition flex flex-col gap-1.5 ${
                          isSelected
                            ? 'bg-charcoal-800 border-aurum-500 shadow-md'
                            : 'bg-charcoal-850 border-charcoal-700 hover:border-aurum-500/50'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span
                              className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                                st.direction === 'LONG'
                                  ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                                  : 'bg-rose-950 text-rose-300 border border-rose-800'
                              }`}
                            >
                              {st.direction} XAUUSDT
                            </span>
                            <span className="text-[10px] font-mono text-gray-400">
                              rev.{st.revision || 1}
                            </span>
                          </div>

                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
                              st.state === 'READY'
                                ? 'bg-emerald-500 text-charcoal-950'
                                : st.state === 'ARMED'
                                ? 'bg-amber-500 text-charcoal-950'
                                : 'bg-charcoal-750 text-gray-400'
                            }`}
                          >
                            {st.state}
                          </span>
                        </div>

                        {/* Price levels */}
                        <div className="flex items-center justify-between text-[11px] font-mono text-gray-300">
                          <span>Entry: ${st.confirmed_entry || st.provisional_entry}</span>
                          <span>SL: ${st.confirmed_sl || st.provisional_sl}</span>
                          <span>TP: ${st.confirmed_tp || st.provisional_tp || '—'}</span>
                        </div>

                        {st.reason && (
                          <p className="text-[10px] text-gray-400 line-clamp-1">{st.reason}</p>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="py-8 text-center text-gray-500 text-xs italic bg-charcoal-850 rounded border border-charcoal-750 p-4">
                  Chưa có kế hoạch dự kiến nào trong phiên.
                </div>
              )}
            </div>
          )}

          {/* SECTION: RISK */}
          {activeSection === 'risk' && (
            <div className="space-y-3.5">
              <div>
                <h4 className="font-bold text-white text-xs uppercase tracking-wider mb-1 flex items-center gap-1.5">
                  <Shield className="w-4 h-4 text-emerald-400" />
                  Quản Trị Rủi Ro & Thông Số Ký Quỹ
                </h4>
                <p className="text-[11px] text-gray-400">
                  Chi tiết tính toán khối lượng, mức rủi ro ban đầu và đệm an toàn giá thanh lý.
                </p>
              </div>

              <div className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-400">Khối lượng (Quantity):</span>
                  <span className="font-mono text-gray-200 font-bold">{qty} oz</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Giá trị danh nghĩa (Notional):</span>
                  <span className="font-mono text-gray-200">${estimatedNotional} USDT</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Ký quỹ ban đầu (Margin):</span>
                  <span className="font-mono text-gray-200">${marginReq} USDT ({lev}x {marginMode})</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Rủi ro ban đầu tại SL:</span>
                  <span className="font-mono text-rose-400 font-bold">${selectedIntent?.initialRiskUsdt?.toFixed(2) || '2.50'} USDT</span>
                </div>
                <div className="flex justify-between border-t border-charcoal-750 pt-2">
                  <span className="text-gray-400">Tỷ lệ Lợi nhuận/Rủi ro ròng (Net R:R):</span>
                  <span className="font-mono text-aurum-400 font-bold">1:{selectedIntent?.estimatedNetRR?.toFixed(2) || '2.00'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Tỷ lệ Lợi nhuận/Rủi ro gộp (Gross R:R):</span>
                  <span className="font-mono text-gray-300">1:{selectedIntent?.grossRR?.toFixed(2) || '2.20'}</span>
                </div>
                {liqEst && (
                  <div className="flex justify-between border-t border-charcoal-750 pt-2">
                    <span className="text-gray-400">Giá thanh lý ước tính (Isolated):</span>
                    <span className="font-mono text-amber-400 font-bold">${Number(liqEst).toFixed(2)}</span>
                  </div>
                )}
              </div>

              {activePosition?.has_active_position && (
                <div className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-2 text-xs">
                  <span className="font-bold text-gray-200 block">Vị thế đang chạy:</span>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Entry khớp:</span>
                    <span className="font-mono text-gray-200">${activePosition.position.entry_price}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">SL / TP:</span>
                    <span className="font-mono text-gray-200">${activePosition.position.stop_loss} / ${activePosition.position.take_profit}</span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* SECTION: SESSION */}
          {activeSection === 'session' && (
            <div className="space-y-3.5">
              <div>
                <h4 className="font-bold text-white text-xs uppercase tracking-wider mb-1 flex items-center gap-1.5">
                  <Clock className="w-4 h-4 text-aurum-400" />
                  Cửa Sổ Phiên Mỹ & Chính Sách Giao Dịch
                </h4>
                <p className="text-[11px] text-gray-400">
                  Quy định khung giờ, hạn ngạch lệnh trong ngày và chính sách phân bổ slot phiên Mỹ.
                </p>
              </div>

              {nySession && (
                <div className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-2 text-xs">
                  <div className="flex justify-between">
                    <span className="text-gray-400">Giờ New York (NY):</span>
                    <span className="font-mono text-gray-200 font-bold">{nySession.local_ny_time}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Giờ Việt Nam (VN UTC+7):</span>
                    <span className="font-mono text-aurum-300 font-bold">{nySession.local_vn_time}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Cửa sổ vào lệnh:</span>
                    <span className="font-semibold text-gray-200">{nySession.ny_window_display} ({nySession.vn_window_display})</span>
                  </div>
                  <div className="flex justify-between border-t border-charcoal-750 pt-2">
                    <span className="text-gray-400">Khớp hôm nay:</span>
                    <span className="font-mono text-gray-200 font-bold">{nySession.daily_fills} / {nySession.max_daily_fills} (Trần 3)</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Mục tiêu phiên Mỹ:</span>
                    <span className="font-mono text-gray-200 font-bold">{nySession.ny_fills} / {nySession.ny_min_fills}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Slot dự trữ cho Mỹ:</span>
                    <span className="font-mono text-amber-300 font-bold">{nySession.reserved_slots} slot</span>
                  </div>
                  <div className="flex justify-between border-t border-charcoal-750 pt-2">
                    <span className="text-gray-400">Chính sách phiên:</span>
                    <span className="text-emerald-400 font-medium">
                      {policy?.entry_session_policy === 'ALL_SESSIONS_WITH_NY_RESERVE'
                        ? 'Tất cả phiên (giữ slot Mỹ)'
                        : 'Chỉ Phiên Mỹ (NY_ONLY)'}
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
