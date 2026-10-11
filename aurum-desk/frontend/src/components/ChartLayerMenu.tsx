import React, { useState, useRef, useEffect } from 'react';

export interface ChartLayerSettings {
  showStructure: boolean;
  showSwings: boolean;
  showLiquidity: boolean;
  showFvg: boolean;
  showBreaks: boolean;
  showScenarioPath: boolean;
  showRiskReward: boolean;
}

export const defaultLayerSettings: ChartLayerSettings = {
  showStructure: true,
  showSwings: true,
  showLiquidity: true,
  showFvg: true,
  showBreaks: true,
  showScenarioPath: true,
  showRiskReward: true,
};

interface Props {
  settings: ChartLayerSettings;
  onChange: (settings: ChartLayerSettings) => void;
  evidenceMode: 'ALL' | 'SELECTED' | 'NONE';
  onEvidenceModeChange?: (mode: 'ALL' | 'SELECTED' | 'NONE') => void;
}

export const ChartLayerMenu: React.FC<Props> = ({
  settings,
  onChange,
  evidenceMode,
  onEvidenceModeChange,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape' && isOpen) {
        setIsOpen(false);
      }
    }
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const toggle = (key: keyof ChartLayerSettings) => {
    onChange({
      ...settings,
      [key]: !settings[key],
    });
  };

  return (
    <div className="relative inline-block text-left" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-label="Cài đặt các lớp hiển thị trên biểu đồ"
        aria-expanded={isOpen}
        className="px-2.5 py-1 text-xs font-medium rounded bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 flex items-center space-x-1.5 transition-colors"
      >
        <span>📊 Lớp hiển thị</span>
        <span className="text-[10px] text-gray-400">▾</span>
      </button>

      {isOpen && (
        <div
          role="dialog"
          aria-label="Tùy chọn lớp hiển thị biểu đồ"
          className="absolute right-0 mt-1.5 w-60 rounded-lg bg-gray-900 border border-gray-700 shadow-xl z-50 p-2.5 space-y-2 text-xs"
        >
          {onEvidenceModeChange && (
            <div className="pb-2 border-b border-gray-800">
              <div className="text-[11px] font-semibold text-gray-400 mb-1.5 uppercase tracking-wide">
                Chế độ bằng chứng
              </div>
              <div className="grid grid-cols-3 gap-1">
                {(['SELECTED', 'ALL', 'NONE'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => onEvidenceModeChange(m)}
                    className={`py-1 text-[11px] font-medium rounded transition-colors ${
                      evidenceMode === m
                        ? 'bg-blue-600 text-white font-semibold'
                        : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                    }`}
                  >
                    {m === 'SELECTED' ? 'Đang chọn' : m === 'ALL' ? 'Tổng quan' : 'Tắt hết'}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <div className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">
              Các chi tiết cấu trúc
            </div>

            <label className="flex items-center justify-between text-gray-300 hover:text-white cursor-pointer py-0.5">
              <span>Đỉnh / Đáy (Swings)</span>
              <input
                type="checkbox"
                checked={settings.showSwings}
                onChange={() => toggle('showSwings')}
                className="rounded border-gray-700 text-blue-500 focus:ring-0 bg-gray-800"
              />
            </label>

            <label className="flex items-center justify-between text-gray-300 hover:text-white cursor-pointer py-0.5">
              <span>Thanh khoản & Mục tiêu</span>
              <input
                type="checkbox"
                checked={settings.showLiquidity}
                onChange={() => toggle('showLiquidity')}
                className="rounded border-gray-700 text-blue-500 focus:ring-0 bg-gray-800"
              />
            </label>

            <label className="flex items-center justify-between text-gray-300 hover:text-white cursor-pointer py-0.5">
              <span>Khoảng trống FVG</span>
              <input
                type="checkbox"
                checked={settings.showFvg}
                onChange={() => toggle('showFvg')}
                className="rounded border-gray-700 text-blue-500 focus:ring-0 bg-gray-800"
              />
            </label>

            <label className="flex items-center justify-between text-gray-300 hover:text-white cursor-pointer py-0.5">
              <span>Phá vỡ (BOS / CHoCH)</span>
              <input
                type="checkbox"
                checked={settings.showBreaks}
                onChange={() => toggle('showBreaks')}
                className="rounded border-gray-700 text-blue-500 focus:ring-0 bg-gray-800"
              />
            </label>

            <label className="flex items-center justify-between text-gray-300 hover:text-white cursor-pointer py-0.5">
              <span>Đường kịch bản dự kiến</span>
              <input
                type="checkbox"
                checked={settings.showScenarioPath}
                onChange={() => toggle('showScenarioPath')}
                className="rounded border-gray-700 text-blue-500 focus:ring-0 bg-gray-800"
              />
            </label>

            <label className="flex items-center justify-between text-gray-300 hover:text-white cursor-pointer py-0.5">
              <span>Khung Risk : Reward</span>
              <input
                type="checkbox"
                checked={settings.showRiskReward}
                onChange={() => toggle('showRiskReward')}
                className="rounded border-gray-700 text-blue-500 focus:ring-0 bg-gray-800"
              />
            </label>
          </div>
        </div>
      )}
    </div>
  );
};
