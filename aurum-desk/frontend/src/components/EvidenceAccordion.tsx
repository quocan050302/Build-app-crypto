import React, { useState } from 'react';
import type {
  EntryExplanationDTO,
  HtfContextDTO,
  SnapshotProvenanceDTO,
} from '../types/smcScenario';
import { readStructuralReferencePrice } from '../types/smcScenario';
import {
  ChevronDown,
  ChevronRight,
  AlertCircle,
  Clock,
  Layers,
  CheckCircle2,
  XCircle,
  HelpCircle,
} from 'lucide-react';

export interface EvidenceAccordionProps {
  explanation?: EntryExplanationDTO | null;
  provenance?: SnapshotProvenanceDTO | null;
  htfContext?: HtfContextDTO | null;
  loading?: boolean;
  error?: string | null;
  initialExpanded?: boolean;
}

export const EvidenceAccordion: React.FC<EvidenceAccordionProps> = ({
  explanation,
  provenance,
  htfContext,
  loading = false,
  error = null,
  initialExpanded = true,
}) => {
  const [isOpen, setIsOpen] = useState(initialExpanded);

  if (loading) {
    return (
      <div className="p-3 bg-charcoal-900 border border-charcoal-750 rounded-lg text-xs text-gray-400 flex items-center gap-2">
        <Clock className="w-3.5 h-3.5 animate-spin text-aurum-400" />
        <span>Đang tải bằng chứng và giải thích vào lệnh…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-3 bg-rose-950/40 border border-rose-800 rounded-lg text-xs text-rose-300 flex items-center gap-2">
        <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
        <span>{error}</span>
      </div>
    );
  }

  const nodes = explanation?.nodes || {};
  const context = explanation?.context;
  const target = explanation?.target;
  const invalidation = explanation?.invalidation;
  const guards = explanation?.guard_audit || [];

  const renderStatusBadge = (status?: string) => {
    if (status === 'CONFIRMED' || status === 'CONFIRMED_EVIDENCE') {
      return (
        <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center gap-0.5">
          <CheckCircle2 className="w-2.5 h-2.5" /> Đã xác nhận
        </span>
      );
    }
    if (status === 'OBSERVED') {
      return (
        <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-blue-950 text-blue-400 border border-blue-800 flex items-center gap-0.5">
          <HelpCircle className="w-2.5 h-2.5" /> Quan sát
        </span>
      );
    }
    return (
      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-charcoal-800 text-gray-400 border border-charcoal-700 flex items-center gap-0.5">
        <XCircle className="w-2.5 h-2.5 text-gray-500" /> Chưa có bằng chứng
      </span>
    );
  };

  const formatTarget = (t: any): string => {
    if (!t) return 'Chưa có bằng chứng';
    if (typeof t === 'number') return `Mức giá: $${t.toFixed(2)}`;
    if (typeof t === 'string') return t;
    if (typeof t === 'object') {
      const name = t.name || t.level_name || t.label || t.type || 'Mục tiêu thanh khoản';
      const price = t.price ? ` · $${Number(t.price).toFixed(2)}` : '';
      const reason = t.reason || t.detail ? ` (${t.reason || t.detail})` : '';
      return `${name}${price}${reason}`;
    }
    return 'Chưa có bằng chứng';
  };

  const formatInvalidation = (inv: any): string => {
    if (!inv) return 'Chưa có bằng chứng';
    if (typeof inv === 'number') return `Hủy khi giá chạm $${inv.toFixed(2)}`;
    if (typeof inv === 'string') return inv;
    if (typeof inv === 'object') {
      const price = inv.price ? `Giá chạm: $${Number(inv.price).toFixed(2)}` : '';
      const condition = inv.condition || inv.reason || inv.detail || 'Điều kiện hủy kế hoạch';
      return price ? `${condition} (${price})` : condition;
    }
    return 'Chưa có bằng chứng';
  };

  const formatTrackedLevels = (levels: any): string => {
    if (!levels) return 'Chưa có bằng chứng';
    if (Array.isArray(levels)) {
      if (levels.length === 0) return 'Chưa có bằng chứng';
      return levels.map((l) => (typeof l === 'object' ? `${l.name || l.label || 'Mức'}: $${l.price}` : String(l))).join(' · ');
    }
    if (typeof levels === 'object') {
      const keys = Object.keys(levels);
      if (keys.length === 0) return 'Chưa có bằng chứng';
      return keys.map((k) => {
        const val = levels[k];
        if (typeof val === 'object' && val?.price) {
          return `${val.name || k}: $${Number(val.price).toFixed(2)}`;
        }
        if (typeof val === 'number') {
          return `${k}: $${val.toFixed(2)}`;
        }
        return k;
      }).join(' · ');
    }
    return String(levels);
  };

  const formatHtfContext = (
    htf: HtfContextDTO | null | undefined,
    legacyContext: any
  ): { status: string; content: string; detail?: string } => {
    if (!htf) {
      if (!legacyContext) {
        return { status: 'MISSING', content: 'Lệnh cũ chưa lưu bằng chứng HTF' };
      }
      return {
        status:
          legacyContext?.h4_bias && legacyContext?.h4_bias !== 'UNKNOWN'
            ? 'CONFIRMED'
            : 'OBSERVED',
        content: `H4: ${legacyContext.h4_bias || 'Chưa rõ'} · H1: ${
          legacyContext.h1_alignment || 'Chưa rõ'
        } · Chế độ: ${legacyContext.regime || 'Chưa rõ'}`,
      };
    }

    const h4 = htf.h4_snapshot;
    const h1 = htf.h1_snapshot;
    const dir = htf.direction_context;

    const translateBias = (bias?: string, phase?: string) => {
      if (phase === 'TRANSITION') return 'đang chuyển cấu trúc (chờ xác nhận mới)';
      if (phase === 'INSUFFICIENT_DATA') return 'chưa đủ nến đóng để đánh giá';
      if (bias === 'BULLISH') return 'tăng';
      if (bias === 'BEARISH') return 'giảm';
      if (bias === 'RANGE') return 'đi ngang (Range)';
      return 'chưa rõ';
    };

    const h4Text = `H4 ${translateBias(h4?.bias, h4?.phase)}`;
    const h1Text = `H1 ${translateBias(h1?.bias, h1?.phase)}`;
    const relText = dir?.relation ? ` · Quan hệ: ${dir.relation}` : '';
    const mainContent = `${h4Text} · ${h1Text}${relText}`;

    let detail =
      'HTF là bối cảnh; lệnh còn cần quét thanh khoản → MSS/displacement → FVG → retest/entry hợp lệ.';
    const pLow = readStructuralReferencePrice(h1?.protected_low);
    const pHigh = readStructuralReferencePrice(h1?.protected_high);

    if (pLow != null) {
      detail += ` · H1 đáy bảo vệ: $${pLow.toFixed(2)}`;
    } else if (pHigh != null) {
      detail += ` · H1 đỉnh bảo vệ: $${pHigh.toFixed(2)}`;
    } else if (
      h1?.phase === 'TRANSITION' ||
      h1?.phase === 'UNDETERMINED' ||
      h1?.phase != null
    ) {
      detail += ' · Chưa có mức bảo vệ được xác nhận';
    }

    const isConfirmed =
      h1?.phase === 'ESTABLISHED' ||
      h1?.phase === 'PULLBACK' ||
      h1?.phase === 'RANGE';
    return {
      status: isConfirmed ? 'CONFIRMED' : 'OBSERVED',
      content: mainContent,
      detail,
    };
  };

  const htfInfo = formatHtfContext(htfContext, context);

  const rows = [
    {
      key: 'context',
      title: 'Bối cảnh HTF (H4 / H1)',
      status: htfInfo.status,
      content: htfInfo.content,
      detail: htfInfo.detail,
    },
    {
      key: 'liquidity',
      title: 'Thanh khoản',
      status: explanation?.tracked_levels && Object.keys(explanation.tracked_levels).length > 0 ? 'CONFIRMED' : 'MISSING',
      content: formatTrackedLevels(explanation?.tracked_levels),
    },
    {
      key: 'sweep',
      title: 'Quét thanh khoản (Sweep)',
      status: nodes.sweep?.status || 'MISSING',
      content: nodes.sweep?.detail || nodes.sweep?.reason || 'Chưa có bằng chứng',
      price: nodes.sweep?.price,
      nodeId: nodes.sweep?.id,
    },
    {
      key: 'break',
      title: 'Phá vỡ cấu trúc (MSS / CHoCH)',
      status: nodes.break?.status || 'MISSING',
      content: nodes.break?.detail || nodes.break?.reason || 'Chưa có bằng chứng',
      price: nodes.break?.price,
      nodeId: nodes.break?.id,
    },
    {
      key: 'fvg',
      title: 'Khối mất cân bằng (FVG)',
      status: nodes.fvg?.status || 'MISSING',
      content: nodes.fvg?.detail || nodes.fvg?.reason || 'Chưa có bằng chứng',
      price: nodes.fvg?.price,
      nodeId: nodes.fvg?.id,
    },
    {
      key: 'retest',
      title: 'Kiểm tra lại (Retest)',
      status: nodes.retest?.status || 'MISSING',
      content: nodes.retest?.detail || nodes.retest?.reason || 'Chưa có bằng chứng',
      price: nodes.retest?.price,
      nodeId: nodes.retest?.id,
    },
    {
      key: 'target',
      title: 'Mục tiêu (Target)',
      status: target ? 'CONFIRMED' : 'MISSING',
      content: formatTarget(target),
    },
    {
      key: 'invalidation',
      title: 'Hủy khi (Invalidation)',
      status: invalidation ? 'CONFIRMED' : 'MISSING',
      content: formatInvalidation(invalidation),
    },
    {
      key: 'guards',
      title: 'Điều kiện thực thi (Guards)',
      status: guards.length > 0 ? (guards.every((g: any) => g.allowed === true) ? 'CONFIRMED' : 'OBSERVED') : 'MISSING',
      content:
        guards.length > 0
          ? guards
              .map((g: any) => {
                const label = g.allowed === true ? 'ĐẠT' : g.allowed === false ? 'CHẶN' : 'CHƯA XÁC ĐỊNH';
                return `${g.name || g.guard_name || 'Guard'}: ${label}`;
              })
              .join(' · ')
          : 'Chưa lưu kiểm tra thời điểm vào lệnh',
    },
  ];

  const provenanceBadge = () => {
    if (!provenance) return null;
    const isCurrent = provenance.is_current;
    let colorClass = 'bg-charcoal-800 text-gray-400 border-charcoal-700';
    let text = provenance.version_label || 'Chưa đủ thông tin phiên bản';

    if (isCurrent === true) {
      colorClass = 'bg-emerald-950 text-emerald-400 border-emerald-800';
      text = provenance.version_label || 'Phiên bản hiện tại';
    } else if (isCurrent === false) {
      colorClass = 'bg-amber-950 text-amber-400 border-amber-800';
      text = provenance.version_label || 'Phiên bản lúc vào lệnh';
    }

    return (
      <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono border ${colorClass}`} title={provenance.warning || undefined}>
        {text}
      </span>
    );
  };

  return (
    <div className="border border-charcoal-750 bg-charcoal-900 rounded-lg overflow-hidden text-xs">
      {/* Header */}
      <button
        type="button"
        data-testid="evidence-accordion-toggle"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full px-3 py-2.5 flex items-center justify-between bg-charcoal-850 hover:bg-charcoal-800 transition text-left"
      >
        <div className="flex items-center gap-2">
          <Layers className="w-3.5 h-3.5 text-aurum-400" />
          <span className="font-bold text-gray-200">Kịch bản & Bằng chứng vào lệnh</span>
          {provenanceBadge()}
        </div>
        {isOpen ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
      </button>

      {/* Accordion Content */}
      {isOpen && (
        <div className="p-3 divide-y divide-charcoal-800 flex flex-col gap-2">
          {rows.map((row) => (
            <div key={row.key} className="pt-2 first:pt-0 flex flex-col gap-1">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-gray-300">{row.title}</span>
                {renderStatusBadge(row.status)}
              </div>
              <div className="text-[11px] text-gray-400 pl-1 border-l-2 border-charcoal-750">
                {row.content}
                {row.price && <span className="ml-1 text-aurum-400 font-mono">(${row.price})</span>}
                {row.nodeId && <span className="ml-1 text-[10px] text-gray-500 font-mono">[{row.nodeId}]</span>}
                {'detail' in row && row.detail && (
                  <p className="mt-1 text-[10px] text-gray-500 italic">{row.detail}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
