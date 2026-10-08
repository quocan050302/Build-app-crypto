import type {
  ISeriesPrimitive,
  SeriesAttachedParameter,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  Time,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';

export interface SMCSwing {
  index: number;
  timestamp: number;
  price: number;
  type: 'HIGH' | 'LOW';
  confirmed_at?: number;
  label?: string; // HH, HL, LH, LL, SH, SL
}

export interface SMCStructureEvent {
  id: string;
  event_type: 'BOS' | 'CHoCH' | 'SWEEP';
  kind?: string;
  level: number;
  start_at?: number;
  break_at?: number;
  timestamp: number;
  confirmed_at?: number;
  direction?: 'BULLISH' | 'BEARISH';
  detail: string;
}

export interface SMCStructureData {
  swings: SMCSwing[];
  events: SMCStructureEvent[];
  showSwings: boolean;
  showEvents: boolean;
}

class SMCStructurePaneRenderer implements IPrimitivePaneRenderer {
  private _data: SMCStructureData | null = null;
  private _param: SeriesAttachedParameter<Time> | null = null;

  update(data: SMCStructureData, param: SeriesAttachedParameter<Time> | null) {
    this._data = data;
    this._param = param;
  }

  draw(target: CanvasRenderingTarget2D) {
    if (!this._data || !this._param) return;
    const series = this._param.series;
    const chart = this._param.chart;
    if (!series || !chart) return;
    const timeScale = chart.timeScale();
    if (!timeScale) return;

    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      ctx.save();

      // 1. Draw Swings (SH, SL, HH, HL, LH, LL)
      if (this._data?.showSwings && Array.isArray(this._data.swings)) {
        for (const swing of this._data.swings) {
          const coordX = timeScale.timeToCoordinate(Math.floor(swing.timestamp / 1000) as unknown as Time);
          const coordY = series.priceToCoordinate(swing.price);

          if (coordX === null || coordY === null) continue;
          const x = Number(coordX);
          const y = Number(coordY);

          const isHigh = swing.type === 'HIGH';
          const label = swing.label || (isHigh ? 'SH' : 'SL');
          const color = isHigh ? '#f43f5e' : '#10b981';

          // Small dot at pivot
          ctx.beginPath();
          ctx.arc(x, y, 3, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();

          // Label pill above/below
          ctx.font = 'bold 9px Inter, sans-serif';
          const textMetrics = ctx.measureText(label);
          const pillW = textMetrics.width + 6;
          const pillH = 13;
          const pillX = x - pillW / 2;
          const pillY = isHigh ? y - 16 : y + 6;

          ctx.fillStyle = 'rgba(24, 24, 27, 0.85)';
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.fillRect(pillX, pillY, pillW, pillH);
          ctx.strokeRect(pillX, pillY, pillW, pillH);

          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(label, x, pillY + pillH / 2);
        }
      }

      // 2. Draw Structure Events (BOS & CHoCH)
      if (this._data?.showEvents && Array.isArray(this._data.events)) {
        for (const ev of this._data.events) {
          if (ev.event_type !== 'BOS' && ev.event_type !== 'CHoCH') continue;

          const coordY = series.priceToCoordinate(ev.level);
          if (coordY === null) continue;
          const y = Number(coordY);

          const startTime = ev.start_at ? Math.floor(ev.start_at / 1000) : Math.floor(ev.timestamp / 1000);
          const endTime = Math.floor(ev.timestamp / 1000);

          const rawX1 = timeScale.timeToCoordinate(startTime as unknown as Time);
          const rawX2 = timeScale.timeToCoordinate(endTime as unknown as Time);

          if (rawX1 === null && rawX2 === null) continue;
          const x1 = rawX1 !== null ? Number(rawX1) : 0;
          const x2 = rawX2 !== null ? Number(rawX2) : 200;

          const isBull = ev.direction === 'BULLISH' || ev.kind?.includes('HIGH');
          const color = ev.event_type === 'CHoCH' ? '#a855f7' : (isBull ? '#06b6d4' : '#f97316');

          // Horizontal break line from broken level to confirmation
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(x2, y);
          ctx.stroke();
          ctx.setLineDash([]);

          // Break badge at x2
          const label = `${ev.event_type}`;
          ctx.font = 'bold 9px Inter, sans-serif';
          const textMetrics = ctx.measureText(label);
          const badgeW = textMetrics.width + 8;
          const badgeH = 14;
          const badgeX = x2 - badgeW / 2;
          const badgeY = y - badgeH / 2;

          ctx.fillStyle = '#18181b';
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.fillRect(badgeX, badgeY, badgeW, badgeH);
          ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);

          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(label, badgeX + badgeW / 2, y);
        }
      }

      ctx.restore();
    });
  }
}

class SMCStructurePaneView implements IPrimitivePaneView {
  private _renderer = new SMCStructurePaneRenderer();

  update(data: SMCStructureData, param: SeriesAttachedParameter<Time> | null) {
    this._renderer.update(data, param);
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }

  zOrder(): 'normal' {
    return 'normal';
  }
}

export class SMCStructurePrimitive implements ISeriesPrimitive<Time> {
  private _data: SMCStructureData;
  private _param: SeriesAttachedParameter<Time> | null = null;
  private _paneView = new SMCStructurePaneView();

  constructor(data?: SMCStructureData) {
    this._data = data || { swings: [], events: [], showSwings: true, showEvents: true };
  }

  setData(data: SMCStructureData) {
    this._data = data;
    this._paneView.update(this._data, this._param);
    if (this._param) {
      this._param.requestUpdate();
    }
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._param = param;
    this._paneView.update(this._data, this._param);
    this._param.requestUpdate();
  }

  detached(): void {
    this._param = null;
  }

  updateAllViews(): void {
    this._paneView.update(this._data, this._param);
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this._paneView];
  }

  priceAxisViews(): readonly [] {
    return [];
  }

  timeAxisViews(): readonly [] {
    return [];
  }
}
