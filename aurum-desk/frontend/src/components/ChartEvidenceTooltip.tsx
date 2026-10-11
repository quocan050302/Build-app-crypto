import React from 'react';

export interface EvidenceTooltipData {
  kind: string;
  price?: number;
  occurredAtMs?: number;
  knownAtMs?: number;
  timeframe?: string;
  detail?: string;
  sourceIds?: string[];
  x: number;
  y: number;
}

interface Props {
  data: EvidenceTooltipData | null;
  onClose?: () => void;
}

export const ChartEvidenceTooltip: React.FC<Props> = ({ data, onClose: _onClose }) => {
  if (!data) return null;

  return (
    <div
      style={{ left: Math.max(10, data.x - 120), top: Math.max(10, data.y - 100) }}
      className="absolute pointer-events-auto bg-gray-900/95 border border-cyan-500/50 rounded-lg p-2.5 shadow-2xl text-xs text-gray-200 z-50 max-w-xs space-y-1 backdrop-blur-md"
    >
      <div className="flex items-center justify-between border-b border-gray-800 pb-1">
        <span className="font-semibold text-cyan-400 font-mono">{data.kind}</span>
        {data.timeframe && (
          <span className="px-1 py-0.2 rounded bg-gray-800 text-[10px] text-gray-400">
            {data.timeframe}
          </span>
        )}
      </div>

      {data.price !== undefined && (
        <div className="flex justify-between text-[11px]">
          <span className="text-gray-400">Mức giá:</span>
          <span className="font-mono font-medium text-white">{data.price.toFixed(2)}</span>
        </div>
      )}

      {data.occurredAtMs && (
        <div className="flex justify-between text-[11px]">
          <span className="text-gray-400">Thời điểm nến:</span>
          <span className="font-mono text-gray-300">
            {new Date(data.occurredAtMs).toLocaleTimeString('vi-VN', {
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
            })}
          </span>
        </div>
      )}

      {data.knownAtMs && (
        <div className="flex justify-between text-[11px]">
          <span className="text-gray-400">Đã biết lúc:</span>
          <span className="font-mono text-gray-300">
            {new Date(data.knownAtMs).toLocaleTimeString('vi-VN', {
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
            })}
          </span>
        </div>
      )}

      {data.detail && (
        <div className="text-[11px] text-gray-300 italic pt-1 border-t border-gray-800/80">
          {data.detail}
        </div>
      )}
    </div>
  );
};
