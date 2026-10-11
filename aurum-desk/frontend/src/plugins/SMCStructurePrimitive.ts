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
  id?: string;
}

export interface SMCStructureEvent {
  id: string;
  event_type: 'BOS' | 'CHOCH' | 'CHoCH' | 'SWEEP' | 'MSS' | 'BREAK_UNCLASSIFIED';
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
  state?: 'ACTIVE' | 'PARTIAL' | 'MITIGATED' | 'INVALIDATED';
  label?: string;
  source_ids?: string[];
}

export interface SMCDisplacement {
  id: string;
  candle_time: number;
  high: number;
  low: number;
  direction: 'BULLISH' | 'BEARISH';
  body_size?: number;
  atr?: number;
  ratio?: number;
  label?: string;
  source_ids?: string[];
}

export interface SMCRetest {
  id: string;
  timestamp: number;
  price: number;
  direction?: 'BULLISH' | 'BEARISH';
  confirmed: boolean;
  label?: string;
  source_ids?: string[];
}

export interface SMCLiquidityLevel {
  id: string;
  price: number;
  label: string;
  side: 'BUY_SIDE' | 'SELL_SIDE';
  start_at: number;
  end_at?: number;
  state: 'UNTOUCHED' | 'APPROACHED' | 'SWEPT' | 'BROKEN' | 'CONSUMED';
  is_target?: boolean;
}

export interface SMCConditionalPathPoint {
  xTime?: number;
  price: number;
  label?: string;
}

export interface SMCConditionalPath {
  points: SMCConditionalPathPoint[];
  isConfirmed: boolean;
  label?: string;
}

export interface SMCHitItem {
  cursorStyle: string;
  externalId: string;
  zOrder: 'top' | 'bottom' | 'normal';
  id: string;
  title: string;
  category: 'ZONE' | 'SWING' | 'EVENT' | 'LIQUIDITY' | 'DISPLACEMENT' | 'RETEST' | 'PATH';
  price: number;
  timeMs: number;
  detail?: string;
  x: number;
  y: number;
  radius: number;
}

export interface SMCStructureData {
  swings: SMCSwing[];
  events: SMCStructureEvent[];
  zones?: SMCZone[];
  liquidityLevels?: SMCLiquidityLevel[];
  displacements?: SMCDisplacement[];
  retests?: SMCRetest[];
  scenarioPath?: SMCConditionalPath;
  showSwings: boolean;
  showEvents: boolean;
  showZones?: boolean;
  showLiquidity?: boolean;
  showDisplacements?: boolean;
  showRetests?: boolean;
  showScenarioPath?: boolean;
}

class SMCStructurePaneRenderer implements IPrimitivePaneRenderer {
  private _data: SMCStructureData | null = null;
  private _param: SeriesAttachedParameter<Time> | null = null;
  private _hitRegistry: SMCHitItem[] = [];

  update(data: SMCStructureData, param: SeriesAttachedParameter<Time> | null) {
    this._data = data;
    this._param = param;
  }

  getHitItems(): readonly SMCHitItem[] {
    return this._hitRegistry;
  }

  hitTest(px: number, py: number): SMCHitItem | null {
    let closest: SMCHitItem | null = null;
    let minDistance = Infinity;

    for (const item of this._hitRegistry) {
      const dx = item.x - px;
      const dy = item.y - py;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist <= item.radius && dist < minDistance) {
        minDistance = dist;
        closest = item;
      }
    }
    return closest;
  }

  draw(target: CanvasRenderingTarget2D) {
    this._hitRegistry = [];
    if (!this._data || !this._param) return;
    const series = this._param.series;
    const chart = this._param.chart;
    if (!series || !chart) return;
    const timeScale = chart.timeScale();
    if (!timeScale) return;

    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      ctx.save();
      const viewportWidth = scope.mediaSize.width;

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
          const x1 = rawX1 !== null ? Math.max(0, Number(rawX1)) : 0;
          const x2 = rawX2 !== null ? Math.min(viewportWidth, Number(rawX2)) : viewportWidth;
          if (x2 <= x1) continue;

          const y = Math.min(Number(topY), Number(bottomY));
          const h = Math.abs(Number(bottomY) - Number(topY));

          const isBull = zone.direction === 'BULLISH';
          const isMitigated = zone.state === 'MITIGATED' || zone.state === 'INVALIDATED';
          const fillAlpha = isMitigated ? 0.05 : 0.10;
          const strokeAlpha = isMitigated ? 0.25 : 0.45;

          const fillColor = isBull
            ? `rgba(16, 185, 129, ${fillAlpha})`
            : `rgba(244, 63, 94, ${fillAlpha})`;
          const strokeColor = isBull
            ? `rgba(16, 185, 129, ${strokeAlpha})`
            : `rgba(244, 63, 94, ${strokeAlpha})`;

          ctx.fillStyle = fillColor;
          ctx.strokeStyle = strokeColor;
          ctx.lineWidth = 1;
          if (isMitigated) {
            ctx.setLineDash([2, 2]);
          } else {
            ctx.setLineDash([]);
          }
          ctx.fillRect(x1, y, Math.max(10, x2 - x1), Math.max(2, h));
          ctx.strokeRect(x1, y, Math.max(10, x2 - x1), Math.max(2, h));
          ctx.setLineDash([]);

          // Zone label
          const label = zone.label || (isMitigated ? `${zone.kind} (Đã retest)` : zone.kind);
          ctx.font = 'bold 9px Inter, sans-serif';
          ctx.fillStyle = strokeColor;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'top';
          ctx.fillText(label, x1 + 4, y + 2);

          // Register hit
          this._hitRegistry.push({
            cursorStyle: 'pointer',
            externalId: zone.id,
            zOrder: 'normal' as const,
            id: zone.id,
            title: `Vùng ${zone.kind} (${zone.direction || 'Cấu trúc'})`,
            category: 'ZONE',
            price: (zone.top + zone.bottom) / 2,
            timeMs: zone.start_at,
            detail: `Biên: [${zone.bottom.toFixed(2)} - ${zone.top.toFixed(2)}] · Trạng thái: ${zone.state || 'ACTIVE'}`,
            x: (x1 + x2) / 2,
            y: y + h / 2,
            radius: Math.max(12, h / 2),
          });
        }
      }

      // 2. Draw Liquidity Levels (Asian / European / PDH / PDL / Targets)
      if (this._data?.showLiquidity !== false && Array.isArray(this._data?.liquidityLevels)) {
        for (const liq of this._data.liquidityLevels) {
          const coordY = series.priceToCoordinate(liq.price);
          if (coordY === null) continue;
          const y = Number(coordY);

          const startTime = Math.floor(liq.start_at / 1000);
          const rawX1 = timeScale.timeToCoordinate(startTime as unknown as Time);
          const x1 = rawX1 !== null ? Math.max(0, Number(rawX1)) : 0;
          const x2 = viewportWidth;

          const isTarget = liq.is_target;
          const isConsumed = liq.state === 'SWEPT' || liq.state === 'BROKEN' || liq.state === 'CONSUMED';

          const color = isTarget
            ? '#06b6d4' // Cyan for target
            : isConsumed
            ? 'rgba(156, 163, 175, 0.4)' // Muted gray
            : liq.side === 'BUY_SIDE'
            ? '#f59e0b' // Amber
            : '#38bdf8'; // Sky blue

          ctx.strokeStyle = color;
          ctx.lineWidth = isTarget ? 1.5 : 1;
          ctx.setLineDash(isTarget ? [4, 2] : [2, 2]);
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(x2, y);
          ctx.stroke();
          ctx.setLineDash([]);

          // Badge on right edge
          const label = isTarget ? `Target: ${liq.label}` : liq.label;
          ctx.font = 'bold 9px Inter, sans-serif';
          const textMetrics = ctx.measureText(label);
          const badgeW = textMetrics.width + 8;
          const badgeH = 14;
          const badgeX = Math.max(x1 + 10, x2 - badgeW - 6);
          const badgeY = y - badgeH / 2;

          ctx.fillStyle = 'rgba(24, 24, 27, 0.85)';
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.fillRect(badgeX, badgeY, badgeW, badgeH);
          ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);

          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(label, badgeX + badgeW / 2, y);

          this._hitRegistry.push({
            cursorStyle: 'pointer',
            externalId: liq.id,
            zOrder: 'normal' as const,
            id: liq.id,
            title: `Thanh khoản: ${liq.label}`,
            category: 'LIQUIDITY',
            price: liq.price,
            timeMs: liq.start_at,
            detail: `Mức giá: ${liq.price.toFixed(2)} · Phe: ${liq.side} · Trạng thái: ${liq.state}`,
            x: badgeX + badgeW / 2,
            y: y,
            radius: 12,
          });
        }
      }

      // 3. Draw Swings (SH, SL, HH, HL, LH, LL)
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

          // Dot at pivot
          ctx.beginPath();
          ctx.arc(x, y, 3.5, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();

          // Label pill
          ctx.font = 'bold 10px Inter, sans-serif';
          const textMetrics = ctx.measureText(label);
          const pillW = textMetrics.width + 6;
          const pillH = 14;
          const pillX = x - pillW / 2;
          const pillY = isHigh ? y - 18 : y + 6;

          ctx.fillStyle = 'rgba(24, 24, 27, 0.88)';
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.fillRect(pillX, pillY, pillW, pillH);
          ctx.strokeRect(pillX, pillY, pillW, pillH);

          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(label, x, pillY + pillH / 2);

          this._hitRegistry.push({
            cursorStyle: 'pointer',
            externalId: swing.id || `swing-${swing.timestamp}`,
            zOrder: 'normal' as const,
            id: swing.id || `swing-${swing.timestamp}`,
            title: `Đỉnh/Đáy Swing: ${label}`,
            category: 'SWING',
            price: swing.price,
            timeMs: swing.timestamp,
            detail: `Giá pivot: ${swing.price.toFixed(2)}${swing.confirmed_at ? ` · Xác nhận lúc ${new Date(swing.confirmed_at).toLocaleTimeString('vi-VN')}` : ''}`,
            x: x,
            y: y,
            radius: 12,
          });
        }
      }

      // 4. Draw Structure Events (BOS, CHOCH, SWEEP, MSS)
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

            ctx.strokeStyle = sweepColor;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([2, 2]);
            ctx.beginPath();
            ctx.moveTo(sx - 15, y);
            ctx.lineTo(sx + 15, y);
            ctx.stroke();
            ctx.setLineDash([]);

            // Marker glyph
            ctx.fillStyle = sweepColor;
            ctx.beginPath();
            if (isBullSweep) {
              ctx.moveTo(sx, y + 2);
              ctx.lineTo(sx - 4, y + 8);
              ctx.lineTo(sx + 4, y + 8);
            } else {
              ctx.moveTo(sx, y - 2);
              ctx.lineTo(sx - 4, y - 8);
              ctx.lineTo(sx + 4, y - 8);
            }
            ctx.closePath();
            ctx.fill();

            const labelText = ev.detail || 'SWEEP';
            ctx.font = 'bold 9px Inter, sans-serif';
            const textMetrics = ctx.measureText(labelText);
            const badgeW = Math.max(44, textMetrics.width + 8);
            const badgeH = 14;
            const badgeX = sx - badgeW / 2;
            const badgeY = isBullSweep ? y + 10 : y - 24;

            ctx.fillStyle = 'rgba(24, 24, 27, 0.9)';
            ctx.strokeStyle = sweepColor;
            ctx.lineWidth = 1;
            ctx.fillRect(badgeX, badgeY, badgeW, badgeH);
            ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);

            ctx.fillStyle = sweepColor;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(labelText, sx, badgeY + badgeH / 2);

            this._hitRegistry.push({
              cursorStyle: 'pointer',
              externalId: ev.id,
              zOrder: 'normal',
              id: ev.id,
              title: `Quét thanh khoản (Sweep)`,
              category: 'EVENT',
              price: ev.level,
              timeMs: ev.timestamp,
              detail: `Mức quét: ${ev.level.toFixed(2)} · Hướng: ${ev.direction || 'N/A'}${ev.detail ? ` (${ev.detail})` : ''}`,
              x: sx,
              y: y,
              radius: 14,
            });
            continue;
          }

          // Handle BOS, CHOCH, MSS, BREAK_UNCLASSIFIED
          const isChoch = kind === 'CHOCH' || kind === 'CHoCH';
          const isMss = kind === 'MSS';
          const isBos = kind === 'BOS';

          const startTime = ev.start_at ? Math.floor(ev.start_at / 1000) : eventTimeSec;
          const endTime = ev.break_at ? Math.floor(ev.break_at / 1000) : eventTimeSec;

          const rawX1 = timeScale.timeToCoordinate(startTime as unknown as Time);
          const rawX2 = timeScale.timeToCoordinate(endTime as unknown as Time);

          if (rawX1 === null && rawX2 === null) continue;
          const x1 = rawX1 !== null ? Number(rawX1) : (rawX2 !== null ? Math.max(0, Number(rawX2) - 40) : 0);
          const x2 = rawX2 !== null ? Number(rawX2) : (rawX1 !== null ? Number(rawX1) + 40 : viewportWidth);

          const isBull = ev.direction === 'BULLISH' || ev.kind?.includes('HIGH');
          const color = isMss ? '#8b5cf6' : isChoch ? '#a855f7' : (isBull ? '#06b6d4' : '#f97316');
          const label = isMss ? 'MSS' : isChoch ? 'CHoCH' : isBos ? 'BOS' : 'BREAK';

          // Horizontal break line from anchor to confirmation
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(x2, y);
          ctx.stroke();
          ctx.setLineDash([]);

          ctx.font = 'bold 10px Inter, sans-serif';
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

          this._hitRegistry.push({
            cursorStyle: 'pointer',
            externalId: ev.id,
            zOrder: 'normal' as const,
            id: ev.id,
            title: `Phá vỡ cấu trúc: ${label}`,
            category: 'EVENT',
            price: ev.level,
            timeMs: ev.timestamp,
            detail: `Mức phá: ${ev.level.toFixed(2)} · Hướng: ${ev.direction || 'N/A'}`,
            x: x2,
            y: y,
            radius: 14,
          });
        }
      }

      // 5. Draw Displacements
      if (this._data?.showDisplacements !== false && Array.isArray(this._data?.displacements)) {
        for (const disp of this._data.displacements) {
          const rawX = timeScale.timeToCoordinate(Math.floor(disp.candle_time / 1000) as unknown as Time);
          const rawHighY = series.priceToCoordinate(disp.high);
          const rawLowY = series.priceToCoordinate(disp.low);
          if (rawX === null || rawHighY === null || rawLowY === null) continue;

          const x = Number(rawX);
          const topY = Math.min(Number(rawHighY), Number(rawLowY));
          const height = Math.abs(Number(rawLowY) - Number(rawHighY));

          const isBull = disp.direction === 'BULLISH';
          const color = isBull ? '#34d399' : '#f87171';

          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.strokeRect(x - 6, topY, 12, Math.max(4, height));

          // Label 'D'
          ctx.font = 'bold 9px Inter, sans-serif';
          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'bottom';
          ctx.fillText('D', x, topY - 2);

          this._hitRegistry.push({
            cursorStyle: 'pointer',
            externalId: disp.id,
            zOrder: 'normal' as const,
            id: disp.id,
            title: `Nến Displacement (${disp.direction})`,
            category: 'DISPLACEMENT',
            price: (disp.high + disp.low) / 2,
            timeMs: disp.candle_time,
            detail: `Biên độ nến: ${(disp.body_size || 0).toFixed(2)}${disp.ratio ? ` (${(disp.ratio * 100).toFixed(0)}% ATR)` : ''}`,
            x: x,
            y: topY + height / 2,
            radius: 12,
          });
        }
      }

      // 6. Draw Retests
      if (this._data?.showRetests !== false && Array.isArray(this._data?.retests)) {
        for (const ret of this._data.retests) {
          const rawX = timeScale.timeToCoordinate(Math.floor(ret.timestamp / 1000) as unknown as Time);
          const rawY = series.priceToCoordinate(ret.price);
          if (rawX === null || rawY === null) continue;

          const x = Number(rawX);
          const y = Number(rawY);
          const color = ret.confirmed ? '#10b981' : '#f59e0b';

          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(x, y, 4, 0, Math.PI * 2);
          ctx.stroke();

          ctx.font = 'bold 9px Inter, sans-serif';
          ctx.fillStyle = color;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText('R', x + 6, y);

          this._hitRegistry.push({
            cursorStyle: 'pointer',
            externalId: ret.id,
            zOrder: 'normal' as const,
            id: ret.id,
            title: `Điểm Retest ${ret.confirmed ? 'Đã xác nhận' : 'Đang chạm'}`,
            category: 'RETEST',
            price: ret.price,
            timeMs: ret.timestamp,
            detail: `Giá chạm: ${ret.price.toFixed(2)}`,
            x: x,
            y: y,
            radius: 10,
          });
        }
      }

      // 7. Draw Conditional Scenario Path
      if (this._data?.showScenarioPath !== false && this._data?.scenarioPath?.points?.length) {
        const path = this._data.scenarioPath;
        const color = path.isConfirmed ? '#60a5fa' : '#94a3b8';

        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();

        let started = false;
        let lastX = 0;
        let lastY = 0;

        for (const pt of path.points) {
          let x: number | null = null;
          if (pt.xTime) {
            const rawX = timeScale.timeToCoordinate(Math.floor(pt.xTime / 1000) as unknown as Time);
            if (rawX !== null) x = Number(rawX);
          }
          const rawY = series.priceToCoordinate(pt.price);
          if (rawY === null) continue;
          const y = Number(rawY);

          if (x === null) {
            x = started ? lastX + 35 : viewportWidth - 60;
          }

          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
          lastX = x;
          lastY = y;
        }

        if (started) {
          ctx.stroke();
          ctx.setLineDash([]);

          // Path label
          const label = path.label || 'Kịch bản';
          ctx.font = 'bold 9px Inter, sans-serif';
          ctx.fillStyle = color;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText(label, lastX + 4, lastY);
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

  hitTest(px: number, py: number): SMCHitItem | null {
    return this._renderer.hitTest(px, py);
  }

  getHitItems(): readonly SMCHitItem[] {
    return this._renderer.getHitItems();
  }
}

export class SMCStructurePrimitive implements ISeriesPrimitive<Time> {
  private _data: SMCStructureData;
  private _param: SeriesAttachedParameter<Time> | null = null;
  private _paneView = new SMCStructurePaneView();

  constructor(data?: SMCStructureData) {
    this._data = data || {
      swings: [],
      events: [],
      zones: [],
      liquidityLevels: [],
      displacements: [],
      retests: [],
      showSwings: true,
      showEvents: true,
      showZones: true,
      showLiquidity: true,
      showDisplacements: true,
      showRetests: true,
      showScenarioPath: true,
    };
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

  hitTest(px: number, py: number): SMCHitItem | null {
    return this._paneView.hitTest(px, py);
  }

  getHitItems(): readonly SMCHitItem[] {
    return this._paneView.getHitItems();
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
