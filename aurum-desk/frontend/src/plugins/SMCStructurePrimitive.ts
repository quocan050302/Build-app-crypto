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
  event_type: 'BOS' | 'CHOCH' | 'CHoCH' | 'SWEEP';
  kind?: string;
  level: number;
  start_at?: number;
  break_at?: number;
  timestamp: number;
  confirmed_at?: number;
  direction?: 'BULLISH' | 'BEARISH';
  detail?: string;
}

export interface SMCZone {
  id: string;
  kind: 'FVG' | 'POI' | 'OB';
  top: number;
  bottom: number;
  start_at: number;
  end_at: number;
  direction?: 'BULLISH' | 'BEARISH';
  label?: string;
}

export interface SMCStructureData {
  swings: SMCSwing[];
  events: SMCStructureEvent[];
  zones?: SMCZone[];
  showSwings: boolean;
  showEvents: boolean;
  showZones?: boolean;
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

      // 1. Draw Zones (FVG / POI)
      if (this._data?.showZones !== false && Array.isArray(this._data?.zones)) {
        for (const zone of this._data.zones) {
          const topY = series.priceToCoordinate(zone.top);
          const bottomY = series.priceToCoordinate(zone.bottom);
          if (topY === null || bottomY === null) continue;

          const startTime = Math.floor(zone.start_at / 1000);
          const endTime = Math.floor(zone.end_at / 1000);
          const rawX1 = timeScale.timeToCoordinate(startTime as unknown as Time);
          const rawX2 = timeScale.timeToCoordinate(endTime as unknown as Time);

          if (rawX1 === null && rawX2 === null) continue;
          const x1 = rawX1 !== null ? Number(rawX1) : 0;
          const x2 = rawX2 !== null ? Number(rawX2) : scope.mediaSize.width;
          const y = Math.min(Number(topY), Number(bottomY));
          const h = Math.abs(Number(bottomY) - Number(topY));

          const isBull = zone.direction === 'BULLISH';
          const fillColor = isBull ? 'rgba(16, 185, 129, 0.12)' : 'rgba(244, 63, 94, 0.12)';
          const strokeColor = isBull ? 'rgba(16, 185, 129, 0.45)' : 'rgba(244, 63, 94, 0.45)';

          ctx.fillStyle = fillColor;
          ctx.strokeStyle = strokeColor;
          ctx.lineWidth = 1;
          ctx.setLineDash([2, 2]);
          ctx.fillRect(x1, y, Math.max(10, x2 - x1), Math.max(2, h));
          ctx.strokeRect(x1, y, Math.max(10, x2 - x1), Math.max(2, h));
          ctx.setLineDash([]);

          // Zone label
          const label = zone.label || zone.kind;
          ctx.font = 'bold 8px Inter, sans-serif';
          ctx.fillStyle = strokeColor;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'top';
          ctx.fillText(label, x1 + 4, y + 2);
        }
      }

      // 2. Draw Swings (SH, SL, HH, HL, LH, LL)
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

      // 3. Draw Structure Events (BOS, CHOCH, SWEEP)
      if (this._data?.showEvents && Array.isArray(this._data.events)) {
        for (const ev of this._data.events) {
          const kind = ev.event_type;
          const coordY = series.priceToCoordinate(ev.level);
          if (coordY === null) continue;
          const y = Number(coordY);

          const eventTimeSec = Math.floor(ev.timestamp / 1000);
          const rawEventX = timeScale.timeToCoordinate(eventTimeSec as unknown as Time);

          // Handle SWEEP
          if (kind === 'SWEEP') {
            if (rawEventX === null) continue;
            const sx = Number(rawEventX);
            const isBullSweep = ev.direction === 'BULLISH' || ev.kind?.includes('LOW');
            const sweepColor = '#eab308'; // Amber

            // Small horizontal tick line
            ctx.strokeStyle = sweepColor;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([2, 2]);
            ctx.beginPath();
            ctx.moveTo(sx - 15, y);
            ctx.lineTo(sx + 15, y);
            ctx.stroke();
            ctx.setLineDash([]);

            // Marker arrow / glyph
            ctx.fillStyle = sweepColor;
            ctx.beginPath();
            if (isBullSweep) {
              // Arrow pointing up
              ctx.moveTo(sx, y + 2);
              ctx.lineTo(sx - 4, y + 8);
              ctx.lineTo(sx + 4, y + 8);
            } else {
              // Arrow pointing down
              ctx.moveTo(sx, y - 2);
              ctx.lineTo(sx - 4, y - 8);
              ctx.lineTo(sx + 4, y - 8);
            }
            ctx.closePath();
            ctx.fill();

            // SWEEP badge
            const badgeW = 42;
            const badgeH = 13;
            const badgeX = sx - badgeW / 2;
            const badgeY = isBullSweep ? y + 10 : y - 23;

            ctx.fillStyle = 'rgba(24, 24, 27, 0.9)';
            ctx.strokeStyle = sweepColor;
            ctx.lineWidth = 1;
            ctx.fillRect(badgeX, badgeY, badgeW, badgeH);
            ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);

            ctx.font = 'bold 8px Inter, sans-serif';
            ctx.fillStyle = sweepColor;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('SWEEP', sx, badgeY + badgeH / 2);
            continue;
          }

          // Handle BOS & CHOCH (support both uppercase CHOCH and legacy CHoCH)
          const isChoch = kind === 'CHOCH' || kind === 'CHoCH';
          const isBos = kind === 'BOS';
          if (!isChoch && !isBos) continue;

          const startTime = ev.start_at ? Math.floor(ev.start_at / 1000) : eventTimeSec;
          const endTime = eventTimeSec;

          const rawX1 = timeScale.timeToCoordinate(startTime as unknown as Time);
          const rawX2 = timeScale.timeToCoordinate(endTime as unknown as Time);

          if (rawX1 === null && rawX2 === null) continue;
          const x1 = rawX1 !== null ? Number(rawX1) : (rawX2 !== null ? Math.max(0, Number(rawX2) - 60) : 0);
          const x2 = rawX2 !== null ? Number(rawX2) : (rawX1 !== null ? Number(rawX1) + 60 : scope.mediaSize.width);

          const isBull = ev.direction === 'BULLISH' || ev.kind?.includes('HIGH');
          const color = isChoch ? '#a855f7' : (isBull ? '#06b6d4' : '#f97316');
          const label = isChoch ? 'CHoCH' : 'BOS';

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
    this._data = data || { swings: [], events: [], zones: [], showSwings: true, showEvents: true, showZones: true };
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
