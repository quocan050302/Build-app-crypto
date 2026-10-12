import { useEffect, useState } from 'react';
import type { ChartLayerSettings } from '../components/ChartLayerMenu';
import { defaultLayerSettings } from '../components/ChartLayerMenu';

const STORAGE_KEY = 'aurum.chart.layers.v1';
const KEYS = Object.keys(defaultLayerSettings) as (keyof ChartLayerSettings)[];

export function loadLayers(): ChartLayerSettings {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (stored?.schema !== 1 || !stored.layers || typeof stored.layers !== 'object') {
      return { ...defaultLayerSettings };
    }
    if (
      Array.isArray(stored.layers) ||
      Object.keys(stored.layers).some((key) => !KEYS.includes(key as keyof ChartLayerSettings))
    ) {
      return { ...defaultLayerSettings };
    }
    const next = { ...defaultLayerSettings };
    for (const key of KEYS) {
      if (typeof stored.layers[key] !== 'boolean') return { ...defaultLayerSettings };
      next[key] = stored.layers[key];
    }
    return next;
  } catch {
    return { ...defaultLayerSettings };
  }
}

export function useChartLayers() {
  const [layers, setLayers] = useState<ChartLayerSettings>(loadLayers);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ schema: 1, layers }));
    } catch {
      // Preferences unavailable must never stop chart/trading
    }
  }, [layers]);

  return [layers, setLayers] as const;
}
