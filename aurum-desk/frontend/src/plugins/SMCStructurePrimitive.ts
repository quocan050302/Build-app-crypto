import type {
  ISeriesPrimitive,
  SeriesAttachedParameter,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  Time,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import { clipSpanX, projectMsToBar } from '../utils/chartTimeProjection';
import type { TimeProjection } from '../utils/chartTimeProjection';
import { layoutAnnotationLabels } from '../utils/chartAnnotationLayout';
import type { LabelCandidate, Rect, PlacedLabel } from '../utils/chartAnnotationLayout';

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
  wick_extreme?: number;
  close_back?: number;
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
  /** Candle extremes are filled from the LOADED candle at candle_time; absent => marker at `level`. */
  high?: number;
  low?: number;
  level?: number;
  direction?: 'BULLISH' | 'BEARISH';
  body_size?: number;
  atr?: number;
  ratio?: number;
  is_qualified?: boolean;
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
  /** Source anchor time; undefined => time of the source level is unknown (drawn from the window edge). */
  start_at?: number;
  end_at?: number;
  state: 'UNTOUCHED' | 'APPROACHED' | 'SWEPT' | 'BROKEN' | 'CONSUMED' | string;
  is_target?: boolean;
  is_partial?: boolean;
}

export interface SMCConditionalPathPoint {
  xTime?: number;
  price: number;
  label?: string;
  /** Logical display projection (no timestamp): never claims a future time. */
  isProjection?: boolean;
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

/** Start times (ms) of the loaded candles + bar length: used to snap event times onto real bars. */
export interface SMCTimeAxis {
  startsMs: number[];
  durationMs: number;
}

export interface SMCStructureData {
  swings: SMCSwing[];
  events: SMCStructureEvent[];
  zones?: SMCZone[];
  liquidityLevels?: SMCLiquidityLevel[];
  displacements?: SMCDisplacement[];
  retests?: SMCRetest[];
  scenarioPath?: SMCConditionalPath;
  timeAxis?: SMCTimeAxis;
  showSwings: boolean;
  showEvents: boolean;
  showZones?: boolean;
  showLiquidity?: boolean;
  showDisplacements?: boolean;
  showRetests?: boolean;
  showScenarioPath?: boolean;
  annotationExclusions?: readonly Rect[];
}

const MAX_HIT_ITEMS = 400;
const FONT_SMALL = 'bold 10px Inter, sans-serif';

function fmtTime(ms: number | undefined): string {
  return ms ? new Date(ms).toLocaleString('vi-VN', { hour12: false }) : 'n/a';
}

class SMCStructurePaneRenderer implements IPrimitivePaneRenderer {
  private _data: SMCStructureData | null = null;
  private _param: SeriesAttachedParameter<Time> | null = null;
  private _hitRegistry: SMCHitItem[] = [];
  private _focusedEvidenceId: string | null = null;
  private _placedLabels: readonly PlacedLabel[] = [];

  update(data: SMCStructureData, param: SeriesAttachedParameter<Time> | null) {
    this._data = data;
    this._param = param;
  }

  setFocusedEvidenceId(id: string | null) {
    this._focusedEvidenceId = id;
  }

  clear() {
    this._hitRegistry = [];
    this._data = null;
    this._focusedEvidenceId = null;
    this._placedLabels = [];
  }

  getHitItems(): readonly SMCHitItem[] {
    return this._hitRegistry;
  }

  getPlacedLabels(): readonly PlacedLabel[] {
    return this._placedLabels;
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

  private _register(item: SMCHitItem) {
    if (this._hitRegistry.length < MAX_HIT_ITEMS) this._hitRegistry.push(item);
  }

  draw(target: CanvasRenderingTarget2D) {
    this._hitRegistry = [];
    if (!this._data || !this._param) return;
    const data = this._data;
    const series = this._param.series;
    const chart = this._param.chart;
    if (!series || !chart) return;
    const timeScale = chart.timeScale();
    if (!timeScale) return;

    const axis = data.timeAxis;
    const project = (ms: number | undefined): { proj: TimeProjection | null; x: number | null } => {
      if (ms === undefined || !Number.isSafeInteger(ms) || ms <= 0) return { proj: null, x: null };
      if (axis && axis.startsMs.length) {
        const proj = projectMsToBar(axis.startsMs, axis.durationMs, ms);
        if (!proj || proj.kind !== 'IN') return { proj, x: null };
        const raw = timeScale.timeToCoordinate((proj.barStartMs / 1000) as unknown as Time);
        return { proj, x: raw === null ? null : Number(raw) };
      }
      // No loaded-bar axis supplied: only an exact bar time can be located.
      const raw = timeScale.timeToCoordinate(Math.floor(ms / 1000) as unknown as Time);
      return raw === null ? { proj: null, x: null } : { proj: { kind: 'IN', barIndex: -1, barStartMs: ms }, x: Number(raw) };
    };
    const barSpacing = Math.max(2, timeScale.options().barSpacing || 6);

    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      ctx.save();
      const viewportWidth = scope.mediaSize.width;

      // 1. Zones (FVG / POI): real source times, clipped to the loaded window, drawn under the candles' labels.
      if (data.showZones !== false && Array.isArray(data.zones)) {
        for (const zone of data.zones) {
          if (!zone.top || !zone.bottom || zone.top <= 0 || zone.bottom <= 0 || !Number.isFinite(zone.top) || !Number.isFinite(zone.bottom)) continue;
          const topY = series.priceToCoordinate(zone.top);
          const bottomY = series.priceToCoordinate(zone.bottom);
          if (topY === null || bottomY === null) continue;
          const a = project(zone.start_at);
          const b = project(zone.end_at);
          const span = clipSpanX(a.proj, a.x, b.proj, b.x, viewportWidth);
          if (!span) continue;
          const { x1, x2 } = span;
          const y = Math.min(Number(topY), Number(bottomY));
          const h = Math.abs(Number(bottomY) - Number(topY));

          const isBull = zone.direction === 'BULLISH';
          const isNeutral = zone.direction === undefined;
          const isMitigated = zone.state === 'MITIGATED' || zone.state === 'INVALIDATED';
          const fillAlpha = isMitigated ? 0.05 : 0.1;
          const strokeAlpha = isMitigated ? 0.25 : 0.45;
          const rgb = isNeutral ? '148, 163, 184' : isBull ? '16, 185, 129' : '244, 63, 94';
          const strokeColor = `rgba(${rgb}, ${strokeAlpha})`;

          ctx.fillStyle = `rgba(${rgb}, ${fillAlpha})`;
          ctx.strokeStyle = strokeColor;
          ctx.lineWidth = 1;
          ctx.setLineDash(isMitigated ? [2, 2] : []);
          ctx.fillRect(x1, y, x2 - x1, Math.max(2, h));
          ctx.strokeRect(x1, y, x2 - x1, Math.max(2, h));
          ctx.setLineDash([]);

          const label = zone.label || zone.kind;
          ctx.font = FONT_SMALL;
          ctx.fillStyle = strokeColor;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'top';
          ctx.fillText(isMitigated ? `${label} (đã dùng)` : label, x1 + 4, y + 2);

          this._register({
            cursorStyle: 'pointer',
            externalId: zone.id,
            zOrder: 'normal',
            id: zone.id,
            title: `Vùng ${zone.kind} (${zone.direction || 'chưa rõ hướng'})`,
            category: 'ZONE',
            price: (zone.top + zone.bottom) / 2,
            timeMs: zone.start_at,
            detail: `Biên: [${zone.bottom.toFixed(2)} - ${zone.top.toFixed(2)}] · Trạng thái: ${zone.state || 'ACTIVE'} · Từ ${fmtTime(zone.start_at)}`,
            x: (x1 + x2) / 2,
            y: y + h / 2,
            radius: Math.max(12, h / 2),
          });
        }
      }

      // 2. Liquidity levels: from the source anchor (or window edge when unknown) to the right edge.
      if (data.showLiquidity !== false && Array.isArray(data.liquidityLevels)) {
        const badges: Array<LabelCandidate & { liqId: string; color: string; price: number; side: string; state: string }> = [];
        for (const liq of data.liquidityLevels) {
          const coordY = series.priceToCoordinate(liq.price);
          if (coordY === null) continue;
          const y = Number(coordY);
          const a = project(liq.start_at);
          if (a.proj && a.proj.kind === 'AFTER') continue;
          const x1 = a.proj && a.proj.kind === 'IN' && a.x !== null ? Math.max(0, a.x) : 0;
          const isTarget = liq.is_target === true;
          const isConsumed = liq.state === 'SWEPT' || liq.state === 'BROKEN' || liq.state === 'CONSUMED';
          const color = isTarget
            ? '#06b6d4'
            : isConsumed
            ? 'rgba(156, 163, 175, 0.55)'
            : liq.side === 'BUY_SIDE'
            ? '#f59e0b'
            : '#38bdf8';

          ctx.strokeStyle = color;
          ctx.lineWidth = isTarget ? 1.5 : 1;
          ctx.setLineDash(isTarget ? [5, 3] : [2, 3]);
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(viewportWidth, y);
          ctx.stroke();
          ctx.setLineDash([]);

          const text = `${isTarget ? 'Mục tiêu · ' : ''}${liq.label} ${liq.price.toFixed(2)}${isConsumed ? ' · đã lấy' : ''}${liq.is_partial ? ' · một phần' : ''}`;
          ctx.font = FONT_SMALL;
          const w = ctx.measureText(text).width + 10;
          badges.push({
            id: liq.id, liqId: liq.id, x: viewportWidth - w - 6, y: y - 7, width: w, height: 15,
            priority: isTarget ? 90 : isConsumed ? 20 : 50, text, color, price: liq.price, side: liq.side, state: liq.state,
          });
        }
        const exclusions = data.annotationExclusions ? [...data.annotationExclusions] : [];
        const placed = layoutAnnotationLabels(badges, { x: 0, y: 0, width: viewportWidth, height: scope.mediaSize.height }, exclusions);
        this._placedLabels = placed;
        for (const p of placed) {
          const src = badges.find((b) => b.id === p.id)!;
          // Labels that cannot be placed stay reachable through the hit registry (tooltip) without overlapping.
          if (p.visible) {
            ctx.fillStyle = 'rgba(24, 24, 27, 0.88)';
            ctx.strokeStyle = src.color;
            ctx.lineWidth = 1;
            ctx.fillRect(p.box.x, p.box.y, p.box.width, p.box.height);
            ctx.strokeRect(p.box.x, p.box.y, p.box.width, p.box.height);
            ctx.fillStyle = src.color;
            ctx.font = FONT_SMALL;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(src.text || '', p.box.x + 5, p.box.y + p.box.height / 2);
          }
          this._register({
            cursorStyle: 'pointer', externalId: src.liqId, zOrder: 'normal', id: src.liqId,
            title: `Thanh khoản: ${src.text}`, category: 'LIQUIDITY', price: src.price, timeMs: 0,
            detail: `Mức giá ${src.price.toFixed(2)} · ${src.side} · ${src.state}`,
            x: p.box.x + p.box.width / 2, y: p.box.y + p.box.height / 2, radius: 12,
          });
        }
      }

      // 3. Swings: dot at the pivot candle and pivot price.
      if (data.showSwings && Array.isArray(data.swings)) {
        for (const swing of data.swings) {
          const a = project(swing.timestamp);
          const coordY = series.priceToCoordinate(swing.price);
          if (a.x === null || coordY === null) continue;
          const x = a.x;
          const y = Number(coordY);
          const isHigh = swing.type === 'HIGH';
          const label = swing.label || (isHigh ? 'SH' : 'SL');
          const color = isHigh ? '#f43f5e' : '#10b981';

          ctx.beginPath();
          ctx.arc(x, y, 3.5, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();

          ctx.font = FONT_SMALL;
          const pillW = ctx.measureText(label).width + 8;
          const pillH = 15;
          const pillX = x - pillW / 2;
          const pillY = isHigh ? y - 20 : y + 6;
          ctx.fillStyle = 'rgba(24, 24, 27, 0.88)';
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.fillRect(pillX, pillY, pillW, pillH);
          ctx.strokeRect(pillX, pillY, pillW, pillH);
          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(label, x, pillY + pillH / 2);

          this._register({
            cursorStyle: 'pointer',
            externalId: swing.id || `swing-${swing.timestamp}`,
            zOrder: 'normal',
            id: swing.id || `swing-${swing.timestamp}`,
            title: `Swing ${label}`,
            category: 'SWING',
            price: swing.price,
            timeMs: swing.timestamp,
            detail: `Pivot ${swing.price.toFixed(2)} lúc ${fmtTime(swing.timestamp)}${swing.confirmed_at ? ` · biết lúc ${fmtTime(swing.confirmed_at)}` : ''}`,
            x, y, radius: 12,
          });
        }
      }

      // 4. Structure events (SWEEP, BOS, CHoCH, MSS).
      if (data.showEvents && Array.isArray(data.events)) {
        for (const ev of data.events) {
          const kind = ev.event_type;
          const coordY = series.priceToCoordinate(ev.level);
          if (coordY === null) continue;
          const y = Number(coordY);
          const at = project(ev.timestamp);
          if (at.x === null) continue; // the break/sweep candle is not on screen: nothing is pulled to an edge

          if (kind === 'SWEEP') {
            const sx = at.x;
            const sweepColor = '#eab308';
            const half = barSpacing * 1.5;
            ctx.strokeStyle = sweepColor;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([2, 2]);
            ctx.beginPath();
            ctx.moveTo(sx - half, y);
            ctx.lineTo(sx + half, y);
            ctx.stroke();
            ctx.setLineDash([]);

            // wick extreme: where the sweep really reached; the dotted connector exists only with real coordinates
            let wickY: number | null = null;
            if (ev.wick_extreme !== undefined) {
              const wy = series.priceToCoordinate(ev.wick_extreme);
              if (wy !== null) wickY = Number(wy);
            }
            if (wickY !== null) {
              ctx.setLineDash([1, 2]);
              ctx.beginPath();
              ctx.moveTo(sx, y);
              ctx.lineTo(sx, wickY);
              ctx.stroke();
              ctx.setLineDash([]);
              ctx.fillStyle = sweepColor;
              ctx.beginPath();
              ctx.arc(sx, wickY, 3, 0, Math.PI * 2);
              ctx.fill();
            }
            ctx.fillStyle = sweepColor;
            ctx.beginPath();
            ctx.moveTo(sx, y - 4);
            ctx.lineTo(sx + 4, y);
            ctx.lineTo(sx, y + 4);
            ctx.lineTo(sx - 4, y);
            ctx.closePath();
            ctx.fill();

            const labelText = ev.detail || 'Quét thanh khoản';
            ctx.font = FONT_SMALL;
            const badgeW = Math.max(44, ctx.measureText(labelText).width + 10);
            const badgeH = 15;
            const above = wickY === null || wickY <= y;
            const badgeX = Math.min(Math.max(2, sx - badgeW / 2), viewportWidth - badgeW - 2);
            const badgeY = above ? y - 26 : y + 10;
            ctx.fillStyle = 'rgba(24, 24, 27, 0.9)';
            ctx.strokeStyle = sweepColor;
            ctx.lineWidth = 1;
            ctx.fillRect(badgeX, badgeY, badgeW, badgeH);
            ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);
            ctx.fillStyle = sweepColor;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(labelText, badgeX + badgeW / 2, badgeY + badgeH / 2);

            this._register({
              cursorStyle: 'pointer', externalId: ev.id, zOrder: 'normal', id: ev.id,
              title: 'Quét thanh khoản (Sweep)', category: 'EVENT', price: ev.level, timeMs: ev.timestamp,
              detail: `Mức bị quét ${ev.level.toFixed(2)}${ev.wick_extreme !== undefined ? ` · wick tới ${ev.wick_extreme.toFixed(2)}` : ''}${ev.close_back !== undefined ? ` · đóng lại ${ev.close_back.toFixed(2)}` : ''} · nến ${fmtTime(ev.timestamp)}`,
              x: sx, y, radius: 14,
            });
            continue;
          }

          // BOS / CHoCH / MSS / unclassified break
          const isChoch = kind === 'CHOCH' || kind === 'CHoCH';
          const isMss = kind === 'MSS';
          const isBos = kind === 'BOS';
          const isBull = ev.direction === 'BULLISH';
          const isBear = ev.direction === 'BEARISH';
          const color = isMss ? '#8b5cf6' : isChoch ? '#a855f7' : isBull ? '#06b6d4' : isBear ? '#f97316' : '#94a3b8';
          const label = isMss ? 'MSS' : isChoch ? 'CHoCH' : isBos ? 'BOS' : 'BREAK';
          const x2 = at.x;

          // The line starts at the broken swing. Without a real anchor only a marker is drawn (no invented length).
          const start = ev.start_at !== undefined ? project(ev.start_at) : { proj: null, x: null };
          let x1: number | null = null;
          if (start.proj && start.proj.kind === 'IN' && start.x !== null) x1 = start.x;
          else if (start.proj && start.proj.kind === 'BEFORE') x1 = 0;
          if (x1 !== null && x1 < x2) {
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([3, 3]);
            ctx.beginPath();
            ctx.moveTo(x1, y);
            ctx.lineTo(x2, y);
            ctx.stroke();
            ctx.setLineDash([]);
          }

          ctx.font = FONT_SMALL;
          const badgeW = ctx.measureText(label).width + 10;
          const badgeH = 15;
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
          ctx.fillText(label, x2, y);

          this._register({
            cursorStyle: 'pointer', externalId: ev.id, zOrder: 'normal', id: ev.id,
            title: `Phá vỡ cấu trúc: ${label}`, category: 'EVENT', price: ev.level, timeMs: ev.timestamp,
            detail: `Mức phá ${ev.level.toFixed(2)} · ${ev.direction || 'chưa rõ hướng'}${x1 === null ? ' · thiếu neo swing bị phá' : ''} · nến ${fmtTime(ev.timestamp)}${ev.confirmed_at ? ` · xác nhận ${fmtTime(ev.confirmed_at)}` : ''}`,
            x: x2, y, radius: 14,
          });
        }
      }

      // 5. Displacement: outline of the measured candle (real high/low) or a level marker.
      if (data.showDisplacements !== false && Array.isArray(data.displacements)) {
        for (const disp of data.displacements) {
          const a = project(disp.candle_time);
          if (a.x === null) continue;
          const x = a.x;
          const qualified = disp.is_qualified !== false;
          const color = disp.direction === 'BEARISH' ? '#f87171' : disp.direction === 'BULLISH' ? '#34d399' : '#94a3b8';
          let yTop: number;
          let yBottom: number;
          if (disp.high !== undefined && disp.low !== undefined) {
            const hy = series.priceToCoordinate(disp.high);
            const ly = series.priceToCoordinate(disp.low);
            if (hy === null || ly === null) continue;
            yTop = Math.min(Number(hy), Number(ly));
            yBottom = Math.max(Number(hy), Number(ly));
          } else if (disp.level !== undefined) {
            const ly = series.priceToCoordinate(disp.level);
            if (ly === null) continue;
            yTop = Number(ly) - 6;
            yBottom = Number(ly) + 6;
          } else {
            continue;
          }
          const halfW = Math.max(4, Math.min(barSpacing * 0.6, 12));
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.setLineDash(qualified ? [] : [3, 2]);
          ctx.strokeRect(x - halfW, yTop, halfW * 2, Math.max(4, yBottom - yTop));
          ctx.setLineDash([]);
          ctx.font = FONT_SMALL;
          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'bottom';
          ctx.fillText(disp.label || (qualified ? 'D' : 'D?'), x, yTop - 2);

          this._register({
            cursorStyle: 'pointer', externalId: disp.id, zOrder: 'normal', id: disp.id,
            title: qualified ? 'Nến displacement (đạt ngưỡng)' : 'Nến phá vỡ — chưa đạt ngưỡng displacement',
            category: 'DISPLACEMENT', price: disp.level ?? (disp.high !== undefined && disp.low !== undefined ? (disp.high + disp.low) / 2 : 0),
            timeMs: disp.candle_time,
            detail: disp.ratio !== undefined ? `Thân/ATR = ${disp.ratio.toFixed(2)}` : 'Chưa đo được thân/ATR',
            x, y: (yTop + yBottom) / 2, radius: 12,
          });
        }
      }

      // 6. Retests
      if (data.showRetests !== false && Array.isArray(data.retests)) {
        for (const ret of data.retests) {
          const a = project(ret.timestamp);
          const rawY = series.priceToCoordinate(ret.price);
          if (a.x === null || rawY === null) continue;
          const x = a.x;
          const y = Number(rawY);
          const color = ret.confirmed ? '#10b981' : '#f59e0b';
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.setLineDash(ret.confirmed ? [] : [2, 2]);
          ctx.beginPath();
          ctx.arc(x, y, 4.5, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = FONT_SMALL;
          ctx.fillStyle = color;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText('R', x + 8, y);

          this._register({
            cursorStyle: 'pointer', externalId: ret.id, zOrder: 'normal', id: ret.id,
            title: `Retest ${ret.confirmed ? 'đã xác nhận' : 'đang chạm'}`, category: 'RETEST', price: ret.price, timeMs: ret.timestamp,
            detail: `Giá chạm ${ret.price.toFixed(2)} · ${fmtTime(ret.timestamp)}`,
            x, y, radius: 10,
          });
        }
      }

      // 7. Conditional path: real anchors by time; the projection is a logical offset in bars, never a timestamp.
      if (data.showScenarioPath !== false && data.scenarioPath?.points?.length) {
        const path = data.scenarioPath;
        const color = path.isConfirmed ? '#60a5fa' : '#94a3b8';
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        let started = false;
        let lastX = 0;
        let lastY = 0;
        for (const pt of path.points) {
          const rawY = series.priceToCoordinate(pt.price);
          if (rawY === null) continue;
          let x: number | null = null;
          if (pt.isProjection || pt.xTime === undefined) {
            if (!started) continue; // a projection needs a real starting point
            x = Math.min(viewportWidth - 4, lastX + barSpacing * 6);
          } else {
            x = project(pt.xTime).x;
          }
          if (x === null) continue;
          const y = Number(rawY);
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
          ctx.font = FONT_SMALL;
          ctx.fillStyle = color;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText(path.label || 'Kịch bản', Math.min(viewportWidth - 90, lastX + 5), lastY - 8);
        } else {
          ctx.setLineDash([]);
        }
      }

      // 7. Focus Highlight Ring if active
      if (this._focusedEvidenceId) {
        const targetHit = this._hitRegistry.find((h) => h.id === this._focusedEvidenceId || h.externalId === this._focusedEvidenceId);
        if (targetHit && Number.isFinite(targetHit.x) && Number.isFinite(targetHit.y)) {
          ctx.save();
          ctx.strokeStyle = '#38bdf8';
          ctx.lineWidth = 2.5;
          ctx.setLineDash([4, 2]);
          ctx.beginPath();
          ctx.arc(targetHit.x, targetHit.y, Math.max(16, targetHit.radius + 6), 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
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

  setFocusedEvidenceId(id: string | null) {
    this._renderer.setFocusedEvidenceId(id);
  }

  clear() {
    this._renderer.clear();
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

  getPlacedLabels(): readonly PlacedLabel[] {
    return this._renderer.getPlacedLabels();
  }
}

const EMPTY_DATA: SMCStructureData = {
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

export class SMCStructurePrimitive implements ISeriesPrimitive<Time> {
  private _data: SMCStructureData;
  private _param: SeriesAttachedParameter<Time> | null = null;
  private _paneView = new SMCStructurePaneView();
  private _focusedEvidenceId: string | null = null;

  constructor(data?: SMCStructureData) {
    this._data = data || EMPTY_DATA;
  }

  setData(data: SMCStructureData) {
    this._data = data;
    this._paneView.update(this._data, this._param);
    if (this._param) {
      this._param.requestUpdate();
    }
  }

  setExclusions(exclusions: readonly Rect[]) {
    this._data = {
      ...this._data,
      annotationExclusions: exclusions,
    };
    this.updateAllViews();
    this._param?.requestUpdate();
  }

  setFocusedEvidenceId(id: string | null) {
    if (this._focusedEvidenceId !== id) {
      this._focusedEvidenceId = id;
      this._paneView.setFocusedEvidenceId(id);
      this._param?.requestUpdate();
    }
  }

  getFocusedEvidenceId(): string | null {
    return this._focusedEvidenceId;
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._param = param;
    this._paneView.update(this._data, this._param);
    this._param.requestUpdate();
  }

  detached(): void {
    this._param = null;
    this._focusedEvidenceId = null;
    this._paneView.setFocusedEvidenceId(null);
    this._paneView.update(EMPTY_DATA, null);
    this._paneView.clear(); // no hover target of a previous selection may survive a detach
  }

  getPlacedLabels(): readonly PlacedLabel[] {
    return this._paneView.getPlacedLabels();
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
