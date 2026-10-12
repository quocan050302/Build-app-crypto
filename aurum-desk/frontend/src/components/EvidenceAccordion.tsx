import React, { useState } from 'react';
import type { EntryExplanationDTO, SnapshotProvenanceDTO } from '../types/smcScenario';
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
  loading?: boolean;
  error?: string | null;
  initialExpanded?: boolean;
}

export const EvidenceAccordion: React.FC<EvidenceAccordionProps> = ({
  explanation,
  provenance,
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

  const rows = [
    {
      key: 'context',
      title: 'Bối cảnh',
      status: context?.h4_bias && context?.h4_bias !== 'UNKNOWN' ? 'CONFIRMED' : 'MISSING',
      content: context
        ? `H4 Bias: ${context.h4_bias || 'Chưa rõ'} · H1: ${context.h1_alignment || 'Chưa rõ'} · Chế độ: ${context.regime || 'Chưa rõ'}`
        : 'Chưa có bằng chứng',
    },
    {
      key: 'liquidity',
      title: 'Thanh khoản',
      status: explanation?.tracked_levels && Object.keys(explanation.tracked_levels).length > 0 ? 'CONFIRMED' : 'MISSING',
      content:
        explanation?.tracked_levels && Object.keys(explanation.tracked_levels).length > 0
          ? `Mức theo dõi: ${Object.keys(explanation.tracked_levels).join(', ')}`
          : 'Chưa có bằng chứng',
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
      content: target ? `Mục tiêu giá: ${typeof target === 'object' ? JSON.stringify(target) : target}` : 'Chưa có bằng chứng',
    },
    {
      key: 'invalidation',
      title: 'Hủy khi (Invalidation)',
      status: invalidation ? 'CONFIRMED' : 'MISSING',
      content: invalidation
        ? `Mức hủy: ${typeof invalidation === 'object' ? JSON.stringify(invalidation) : invalidation}`
        : 'Chưa có bằng chứng',
    },
    {
      key: 'guards',
      title: 'Điều kiện thực thi (Guards)',
      status: guards.length > 0 ? (guards.every((g: any) => g.allowed !== false) ? 'CONFIRMED' : 'OBSERVED') : 'MISSING',
      content:
        guards.length > 0
          ? guards
              .map((g: any) => `${g.name || g.guard_name || 'Guard'}: ${g.allowed !== false ? 'ĐẠT' : 'CHẶN'}`)
              .join(' · ')
          : 'Chưa có bằng chứng',
    },
  ];

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
          {provenance && (
            <span
              className={`px-1.5 py-0.2 rounded text-[9px] font-mono border ${
                provenance.is_current
                  ? 'bg-emerald-950 text-emerald-400 border-emerald-800'
                  : 'bg-amber-950 text-amber-400 border-amber-800'
              }`}
            >
              {provenance.version_label || (provenance.is_current ? 'Phiên bản hiện tại' : 'Phiên bản lúc vào lệnh')}
            </span>
          )}
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
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
