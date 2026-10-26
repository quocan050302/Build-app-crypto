import React from 'react';
import type { SMCScenarioView } from '../types/smcScenario';

interface Props {
  scenario: SMCScenarioView;
  isPrimary?: boolean;
  isSelected?: boolean;
  onSelect?: (scenario: SMCScenarioView) => void;
}

export const SMCScenarioCard: React.FC<Props> = ({
  scenario,
  isSelected = false,
  onSelect,
}) => {
  const dirColor =
    scenario.direction === 'LONG'
      ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10'
      : scenario.direction === 'SHORT'
      ? 'text-rose-400 border-rose-500/30 bg-rose-500/10'
      : 'text-amber-400 border-amber-500/30 bg-amber-500/10';

  const stateBadgeColor =
    scenario.state === 'STRUCTURE_CONFIRMED'
      ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
      : scenario.state === 'BLOCKED'
      ? 'bg-rose-500/20 text-rose-300 border-rose-500/40'
      : 'bg-blue-500/20 text-blue-300 border-blue-500/40';

  return (
    <div
      data-testid="scenario-card"
      data-scenario-id={scenario.scenario_id}
      data-scenario-state={scenario.state}
      data-direction={scenario.direction ?? 'NONE'}
      onClick={() => onSelect && onSelect(scenario)}
      className={`p-3 rounded-lg border transition-all cursor-pointer select-none ${
        isSelected
          ? 'border-cyan-500/60 bg-gray-800/90 shadow-md ring-1 ring-cyan-500/40'
          : 'border-gray-800 bg-gray-900/60 hover:bg-gray-800/50 hover:border-gray-700'
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center space-x-2">
          <span
            className={`px-2 py-0.5 text-xs font-semibold rounded border ${dirColor}`}
          >
            {scenario.direction || 'QUAN SÁT'}
          </span>
          <span className="text-xs text-gray-400 font-mono">
            {scenario.timeframe || '5M'}
          </span>
        </div>
        <span
          className={`px-2 py-0.5 text-xs font-medium rounded border ${stateBadgeColor}`}
        >
          {scenario.state}
        </span>
      </div>

      <div className="text-xs text-gray-300 space-y-1 font-sans">
        {scenario.summary_vi ? (
          scenario.summary_vi
            .split('\n')
            .slice(0, 5)
            .map((line, idx) => (
              <div key={idx} className="truncate" title={line}>
                {line}
              </div>
            ))
        ) : (
          <div className="text-gray-400 italic">Đang theo dõi cấu trúc...</div>
        )}
      </div>

      <div className="mt-2 pt-2 border-t border-gray-800/80 flex items-center justify-between text-[11px] text-gray-400">
        <span>Rev {scenario.revision}</span>
        <span>
          {scenario.as_of_ms
            ? new Date(scenario.as_of_ms).toLocaleTimeString('vi-VN', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
              })
            : '--:--'}
        </span>
      </div>
    </div>
  );
};
