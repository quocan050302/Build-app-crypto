import React from 'react';
import type { CandidateScoreSummary, CandidateScoreDetail } from '../types/candidateScore';
import { translateDecision } from '../utils/candidateScore';
import { Award, ShieldAlert, CheckCircle, XCircle, Info, Lock } from 'lucide-react';

export interface CandidateScoreDetailsProps {
  summary?: CandidateScoreSummary | null;
  detail?: CandidateScoreDetail | null;
  orderId?: string;
  className?: string;
}

export const CandidateScoreDetails: React.FC<CandidateScoreDetailsProps> = ({
  summary,
  detail,
  orderId,
  className = '',
}) => {
  const activeData: CandidateScoreSummary | null = detail || summary || null;

  if (!activeData || activeData.status === 'NOT_ASSESSED' || activeData.status === 'UNAVAILABLE') {
    const isLegacy = activeData?.reason_code === 'LEGACY_NO_SCORE' || activeData?.status === 'UNAVAILABLE';
    return (
      <div className={`p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 text-xs text-gray-400 ${className}`}>
        <div className="flex items-center gap-2 text-gray-300 font-semibold mb-1">
          <Award className="w-4 h-4 text-gray-400" />
          <span>Điểm Chất Lượng CG-01</span>
        </div>
        <p className="text-[11px] text-gray-400 italic">
          {isLegacy
            ? 'Lệnh này chưa lưu điểm ở phiên bản cũ (snapshot legacy).'
            : 'Chưa có dữ liệu đánh giá điểm chất lượng cho setup/lệnh này.'}
        </p>
      </div>
    );
  }

  if (activeData.status === 'INVALID') {
    return (
      <div className={`p-3 rounded-lg bg-rose-950/40 border border-rose-800 text-xs text-rose-300 ${className}`}>
        <div className="flex items-center gap-2 font-semibold mb-1">
          <ShieldAlert className="w-4 h-4 text-rose-400" />
          <span>Điểm Chưa Xác Minh</span>
        </div>
        <p className="text-[11px] text-gray-300">
          Snapshot đánh giá bị thiếu hoặc sai lệch cấu trúc dữ liệu ({activeData.reason_code || 'INVALID_DATA'}).
        </p>
      </div>
    );
  }

  const finalScore = activeData.score;
  const baseScore = activeData.base_score;
  const lessonDelta = activeData.lesson_delta;
  const threshold = activeData.threshold;
  const decisionText = translateDecision(activeData.decision);
  const isEligible = activeData.decision === 'ELIGIBLE' || (threshold !== null && finalScore !== null && finalScore >= threshold);

  const factors = detail?.factors || [];
  const blockers = detail?.blockers || [];
  const missingData = detail?.missing_data || [];

  return (
    <div className={`p-3.5 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-3 text-xs ${className}`}>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-charcoal-750 pb-2">
        <div className="flex items-center gap-2">
          <Award className="w-4 h-4 text-cyan-400" />
          <span className="font-bold text-gray-200">
            {activeData.frozen ? 'Điểm Chất Lượng Lúc ARM (Bất Biến)' : 'Điểm Đánh Giá Chất Lượng CG-01'}
          </span>
          {activeData.frozen && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.2 rounded bg-purple-950 text-purple-300 border border-purple-800 font-mono text-[9px]">
              <Lock className="w-2.5 h-2.5" />
              FROZEN
            </span>
          )}
        </div>

        <span
          className={`px-2 py-0.5 rounded text-[10px] font-bold border ${
            isEligible
              ? 'bg-cyan-950 text-cyan-300 border-cyan-700'
              : 'bg-amber-950 text-amber-300 border-amber-700'
          }`}
        >
          {decisionText}
        </span>
      </div>

      {/* Primary Score Metrics Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-center">
        <div className="p-2 rounded bg-charcoal-900 border border-charcoal-750">
          <span className="text-[10px] text-gray-400 block font-sans">Điểm Gốc (Base):</span>
          <span className="text-sm font-bold text-gray-200">
            {baseScore !== null ? baseScore : '—'}
          </span>
        </div>

        <div className="p-2 rounded bg-charcoal-900 border border-charcoal-750">
          <span className="text-[10px] text-gray-400 block font-sans">Bài Học (Lesson):</span>
          <span
            className={`text-sm font-bold ${
              lessonDelta && lessonDelta > 0
                ? 'text-emerald-400'
                : lessonDelta && lessonDelta < 0
                ? 'text-rose-400'
                : 'text-gray-300'
            }`}
          >
            {lessonDelta !== null ? `${lessonDelta > 0 ? '+' : ''}${lessonDelta}` : '0'}
          </span>
        </div>

        <div className="p-2 rounded bg-charcoal-900 border border-cyan-900/60 bg-cyan-950/20">
          <span className="text-[10px] text-cyan-300 block font-sans font-semibold">Điểm Cuối (Final):</span>
          <span className="text-base font-bold text-cyan-200">
            {finalScore !== null ? `${finalScore}/100` : '—'}
          </span>
        </div>

        <div className="p-2 rounded bg-charcoal-900 border border-charcoal-750">
          <span className="text-[10px] text-gray-400 block font-sans">Ngưỡng Lúc Đánh Giá:</span>
          <span className="text-sm font-bold text-gray-300">
            {threshold !== null ? threshold : '—'}
          </span>
        </div>
      </div>

      {/* Factors list if available */}
      {factors.length > 0 && (
        <div className="space-y-1.5 pt-1">
          <span className="text-[11px] font-bold text-gray-300 uppercase tracking-wider block">
            Tiêu Chí Thành Phần ({factors.length}):
          </span>
          <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
            {factors.map((f, idx) => (
              <div
                key={idx}
                className="p-1.5 rounded bg-charcoal-900/80 border border-charcoal-750 flex items-center justify-between text-[11px]"
              >
                <div className="flex items-center gap-1.5">
                  {f.status === 'PASS' || f.score ? (
                    <CheckCircle className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                  ) : (
                    <XCircle className="w-3.5 h-3.5 text-gray-500 shrink-0" />
                  )}
                  <span className="text-gray-200">{f.name || f.code}</span>
                  {f.explanation_vi && (
                    <span className="text-gray-400 text-[10px]">({f.explanation_vi})</span>
                  )}
                </div>
                {f.score !== undefined && (
                  <span className="font-mono text-gray-300">
                    +{f.score}{f.max_score ? `/${f.max_score}` : ''}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Blockers / Warnings if any */}
      {blockers.length > 0 && (
        <div className="p-2 rounded bg-rose-950/40 border border-rose-800 text-[11px] text-rose-300 space-y-1">
          <div className="flex items-center gap-1 font-semibold">
            <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
            <span>Chốt Chặn (Blockers):</span>
          </div>
          <ul className="list-disc list-inside space-y-0.5 text-gray-300">
            {blockers.map((b, idx) => (
              <li key={idx}>{b}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Missing data warnings if any */}
      {missingData.length > 0 && (
        <div className="p-2 rounded bg-amber-950/40 border border-amber-800 text-[11px] text-amber-300 space-y-1">
          <span className="font-semibold block">Dữ liệu chưa đầy đủ:</span>
          <span className="text-gray-300">{missingData.join(', ')}</span>
        </div>
      )}

      {/* Assessment Meta & Timestamps */}
      <div className="text-[10px] text-gray-400 space-y-0.5 pt-1 border-t border-charcoal-750 font-mono">
        {activeData.as_of_ms && (
          <div>
            Thời điểm đánh giá: {new Date(activeData.as_of_ms).toLocaleString('vi-VN')}
          </div>
        )}
        {orderId && <div>Mã lệnh: {orderId}</div>}
        {activeData.assessment_id && <div>Mã đánh giá: {activeData.assessment_id}</div>}
        {activeData.profile_version && <div>Phiên bản profile: {activeData.profile_version}</div>}
      </div>

      {/* Mandatory Disclaimer */}
      <div className="p-2 rounded bg-charcoal-900 border border-charcoal-750 text-[10px] text-gray-400 flex items-start gap-1.5">
        <Info className="w-3.5 h-3.5 text-cyan-400 shrink-0 mt-0.5" />
        <span>
          <strong>Lưu ý quan trọng:</strong> Điểm {finalScore !== null ? `${finalScore}/100` : 'chất lượng'} là độ phù hợp tiêu chí setup CG-01, <strong>KHÔNG</strong> phải xác suất thắng {finalScore !== null ? `${finalScore}%` : ''}. Điểm cao không thể vượt qua các chốt chặn bảo vệ (Guards). Điểm lịch sử không đổi sau khi lệnh đóng.
        </span>
      </div>
    </div>
  );
};
