import React from 'react';
import type { CandidateScoreSummary } from '../types/candidateScore';
import { formatCandidateScore } from '../utils/candidateScore';
import { Award, AlertCircle } from 'lucide-react';

export interface CandidateScoreBadgeProps {
  summary?: CandidateScoreSummary | null;
  compact?: boolean;
  onOpenDetails?: () => void;
  labelPrefix?: string;
  className?: string;
  showIcon?: boolean;
}

export const CandidateScoreBadge: React.FC<CandidateScoreBadgeProps> = ({
  summary,
  compact = false,
  onOpenDetails,
  labelPrefix,
  className = '',
  showIcon = true,
}) => {
  const formatted = formatCandidateScore(summary, { compact, labelPrefix });
  const isClickable = Boolean(onOpenDetails);

  const handleClick = (e: React.MouseEvent) => {
    if (onOpenDetails) {
      e.stopPropagation();
      onOpenDetails();
    }
  };

  const badgeContent = (
    <span
      className={`inline-flex items-center gap-1 font-mono tabular-nums text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
        formatted.badgeStyle
      } ${isClickable ? 'cursor-pointer hover:brightness-110' : ''} ${className}`}
      title={formatted.tooltipText}
      data-testid="candidate-score-badge"
      data-score={formatted.scoreNumber ?? 'none'}
    >
      {showIcon && (
        formatted.isAvailable ? (
          <Award className="w-3 h-3 shrink-0 text-current opacity-80" aria-hidden="true" />
        ) : (
          <AlertCircle className="w-3 h-3 shrink-0 text-current opacity-60" aria-hidden="true" />
        )
      )}
      <span className="font-bold">{formatted.displayText}</span>
      {summary?.frozen && !compact && (
        <span className="text-[9px] font-sans opacity-75 font-normal ml-0.5">· Đã chốt</span>
      )}
    </span>
  );

  if (isClickable) {
    return (
      <button
        type="button"
        onClick={handleClick}
        aria-label={formatted.accessibleLabel}
        className="inline-flex items-center p-0 m-0 bg-transparent border-0 text-left focus:outline-none focus:ring-1 focus:ring-aurum-400 rounded"
      >
        {badgeContent}
      </button>
    );
  }

  return (
    <span aria-label={formatted.accessibleLabel} className="inline-flex items-center">
      {badgeContent}
    </span>
  );
};
