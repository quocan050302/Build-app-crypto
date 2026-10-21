import type {
  ISeriesPrimitive,
  SeriesAttachedParameter,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  ISeriesPrimitiveAxisView,
  PrimitiveHoveredItem,
  AutoscaleInfo,
  Time,
  Logical,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import { formatRR } from '../utils/planLevels';
import type { Rect } from '../utils/chartAnnotationLayout';

export interface RiskRewardData {
  id: string;
  direction: 'LONG' | 'SHORT';
  state: 'draft' | 'candidate' | 'armed' | 'paper_open' | 'closed' | 'invalidated';
  entryTime?: number; // epoch seconds
  projectedBars?: number; // width in bar count
  plannedEntry: number;
  actualEntry?: number;
  stopLoss: number;
  takeProfit: number;
  quantity: number;
  initialRiskUsdt: number;
  riskPct: number;
  grossRR: number;
  estimatedNetRR: number;
  isValid?: boolean;
  invalidReason?: string;
  readOnly?: boolean;
  minNetRR?: number;
  rrPolicySource?: string;
  rrPolicyFingerprint?: string;
  canExecute?: boolean;
  executionReason?: string;
  realizedPnlNet?: number;
  realizedR?: number;
  invalidationReason?: string;
  strategyVersion?: string;

  // Leverage & Margin
  leverage?: number;
  marginMode?: 'ISOLATED' | 'CROSS';
  estimatedLiquidation?: number | null;
  initialMargin?: number;
  tier?: number;

  // Canonical amounts & multiplier (V10.4)
  grossRewardUsdt?: number;
  grossLossUsdt?: number;
  netRewardUsdt?: number;
  multiplier?: number;
  entryFeeUsdt?: number;
  slExitFeeUsdt?: number;
  tpExitFeeUsdt?: number;
  entrySlippageUsdt?: number;
  slExitSlippageUsdt?: number;
  tpExitSlippageUsdt?: number;
}

export interface PrimitiveRrStatus {
  hasKnownThreshold: boolean;
  meetsMinRR: boolean;
  rrPassText: string;
  isPass: boolean;
  strokeColor: string;
  textColor: string;
}

export function resolvePrimitiveRrStatus(d: RiskRewardData): PrimitiveRrStatus {
  const effectiveEntry = d.actualEntry ?? d.plannedEntry;
  const nonPositiveReward = d.direction === 'LONG' ? d.takeProfit <= effectiveEntry : d.takeProfit >= effectiveEntry;
  if (nonPositiveReward) {
    return {
      hasKnownThreshold: true,
      meetsMinRR: false,
      rrPassText: ' · TP không bù chi phí',
      isPass: false,
      strokeColor: '#eab308',
      textColor: '#fef08a',
    };
  }
  if (d.minNetRR === undefined || d.minNetRR === null || !Number.isFinite(d.minNetRR) || d.minNetRR <= 0) {
    return {
      hasKnownThreshold: false,
      meetsMinRR: false,
      rrPassText: ' · Ngưỡng chưa xác định',
      isPass: false,
      strokeColor: '#a1a1aa',
      textColor: '#d4d4d8',
    };
  }
  const meets = d.estimatedNetRR >= d.minNetRR;
  return {
    hasKnownThreshold: true,
    meetsMinRR: meets,
    rrPassText: meets ? '' : ' · Chưa đạt R:R',
    isPass: meets,
    strokeColor: meets ? (d.direction === 'LONG' ? '#26a69a' : '#ef5350') : '#eab308',
    textColor: meets ? '#f4f4f5' : '#fef08a',
  };
}

export type DragTargetPart = 'entry' | 'sl' | 'tp' | 'body' | 'right_edge';

export interface DragHitResult extends PrimitiveHoveredItem {
  externalId: string;
  zOrder: 'normal';
  part: DragTargetPart;
  entryY: number;
  slY: number;
  tpY: number;
  startX: number;
  endX: number;
}

/**
 * Axis View for displaying price labels on the right price scale
 */
class PriceAxisView implements ISeriesPrimitiveAxisView {
  private _coordinate: number = -9999;
  private _text: string = '';
  private _backColor: string = '#2563eb';
  private _textColor: string = '#ffffff';
  private _visible: boolean = true;

  update(coordinate: number, text: string, backColor: string, textColor: string = '#ffffff', visible: boolean = true) {
    this._coordinate = coordinate;
    this._text = text;
    this._backColor = backColor;
    this._textColor = textColor;
    this._visible = visible;
  }

  coordinate(): number {
    return this._coordinate;
  }

  text(): string {
    return this._text;
  }

  textColor(): string {
    return this._textColor;
  }

  backColor(): string {
    return this._backColor;
  }

  visible(): boolean {
    return this._visible;
  }

  tickVisible(): boolean {
    return true;
  }
}

/**
 * Pane Renderer for drawing the Risk/Reward overlay boxes, lines, badges, and handles
 */
class RiskRewardPaneRenderer implements IPrimitivePaneRenderer {
  private _data: RiskRewardData | null = null;
  private _entryY: number | null = null;
  private _slY: number | null = null;
  private _tpY: number | null = null;
  private _lpY: number | null = null;
  private _startX: number = 0;
  private _endX: number = 0;
  private _hoverPart: DragTargetPart | null = null;

  update(
    data: RiskRewardData,
    entryY: number | null,
    slY: number | null,
    tpY: number | null,
    lpY: number | null,
    startX: number,
    endX: number,
    hoverPart: DragTargetPart | null = null
  ) {
    this._data = data;
    this._entryY = entryY;
    this._slY = slY;
    this._tpY = tpY;
    this._lpY = lpY;
    this._startX = startX;
    this._endX = endX;
    this._hoverPart = hoverPart;
  }

  draw(target: CanvasRenderingTarget2D) {
    if (!this._data || this._entryY === null || this._slY === null || this._tpY === null) {
      return;
    }

    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      const d = this._data!;
      const startX = Math.max(0, this._startX);
      const width = Math.max(80, this._endX - startX);
      const endX = startX + width;

      const entryY = this._entryY!;
      const slY = this._slY!;
      const tpY = this._tpY!;
      const lpY = this._lpY;

      const effectiveEntry = d.actualEntry ?? d.plannedEntry;
      const isFiniteNumbers = Number.isFinite(effectiveEntry) && Number.isFinite(d.stopLoss) && Number.isFinite(d.takeProfit) &&
        effectiveEntry > 0 && d.stopLoss > 0 && d.takeProfit > 0;
      const isGeomValid = isFiniteNumbers && (
        d.direction === 'LONG'
          ? (d.stopLoss < effectiveEntry && effectiveEntry < d.takeProfit)
          : (d.takeProfit < effectiveEntry && effectiveEntry < d.stopLoss)
      );
      const isInvalid = (d.isValid === false) || !isGeomValid;
      const isMuted = d.state === 'closed' || d.state === 'invalidated';
      const isDashed = d.state === 'candidate' || d.state === 'armed' || d.state === 'draft';

      ctx.save();

      if (isInvalid) {
        // Draw red/amber warning error boundary without fake profit box
        const boxTop = Math.min(entryY, slY, tpY);
        const boxHeight = Math.max(24, Math.max(entryY, slY, tpY) - boxTop);
        ctx.fillStyle = 'rgba(239, 68, 68, 0.12)';
        ctx.fillRect(startX, boxTop, width, boxHeight);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.strokeRect(startX, boxTop, width, boxHeight);
        ctx.setLineDash([]);

        // Invalid badge with clear message
        ctx.fillStyle = '#ef4444';
        ctx.font = 'bold 12px Inter, sans-serif';
        ctx.textAlign = 'center';
        let invalidMsg = d.invalidReason;
        if (!invalidMsg) {
          if (d.direction === 'SHORT') {
            invalidMsg = 'SHORT chưa hợp lệ: TP phải dưới Entry, SL phải trên Entry';
          } else {
            invalidMsg = 'LONG chưa hợp lệ: SL phải dưới Entry, TP phải trên Entry';
          }
        }
        ctx.fillText(invalidMsg, startX + width / 2, boxTop + boxHeight / 2 + 4);
        ctx.restore();
        return;
      }

      // Price-target geometry stays unchanged; a net loss at TP is an amber warning.
      const netTargetReward = d.netRewardUsdt ?? (d.initialRiskUsdt * d.estimatedNetRR);
      const nonPositiveReward = Number.isFinite(netTargetReward) && netTargetReward <= 0;
      const targetColor = nonPositiveReward ? '#eab308' : '#26a69a';
      // 1. Price Target Box
      const profitTop = Math.min(entryY, tpY);
      const profitHeight = Math.abs(tpY - entryY);
      ctx.fillStyle = nonPositiveReward ? 'rgba(234, 179, 8, 0.16)' : (isMuted ? 'rgba(38, 166, 154, 0.08)' : 'rgba(38, 166, 154, 0.22)');
      ctx.fillRect(startX, profitTop, width, profitHeight);

      // Profit Border
      ctx.strokeStyle = isMuted ? 'rgba(38, 166, 154, 0.4)' : targetColor;
      ctx.lineWidth = this._hoverPart === 'tp' ? 3 : 1.5;
      if (isDashed) ctx.setLineDash([4, 4]);
      else ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(startX, tpY);
      ctx.lineTo(endX, tpY);
      ctx.stroke();

      // 2. Risk Box (Red Zone)
      const riskTop = Math.min(entryY, slY);
      const riskHeight = Math.abs(slY - entryY);
      ctx.fillStyle = isMuted ? 'rgba(239, 83, 80, 0.08)' : 'rgba(239, 83, 80, 0.22)';
      ctx.fillRect(startX, riskTop, width, riskHeight);

      // Stop Loss Border
      ctx.strokeStyle = isMuted ? 'rgba(239, 83, 80, 0.4)' : '#ef5350';
      ctx.lineWidth = this._hoverPart === 'sl' ? 3 : 1.5;
      if (isDashed) ctx.setLineDash([4, 4]);
      else ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(startX, slY);
      ctx.lineTo(endX, slY);
      ctx.stroke();

      // 3. Entry Line
      ctx.strokeStyle = isMuted ? '#6b7280' : (d.direction === 'LONG' ? '#3b82f6' : '#f59e0b');
      ctx.lineWidth = this._hoverPart === 'entry' ? 3.5 : 2;
      if (isDashed) ctx.setLineDash([6, 3]);
      else ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(startX, entryY);
      ctx.lineTo(endX, entryY);
      ctx.stroke();
      ctx.setLineDash([]);

      // Outer boundary vertical edges
      ctx.strokeStyle = this._hoverPart === 'right_edge' ? '#6366f1' : 'rgba(156, 163, 175, 0.3)';
      ctx.lineWidth = this._hoverPart === 'right_edge' ? 3 : 1.2;
      ctx.beginPath();
      ctx.moveTo(startX, Math.min(slY, tpY));
      ctx.lineTo(startX, Math.max(slY, tpY));
      ctx.moveTo(endX, Math.min(slY, tpY));
      ctx.lineTo(endX, Math.max(slY, tpY));
      ctx.stroke();

      // 4. Estimated Liquidation Price Line (Section 4 requirement)
      if (lpY !== null && d.estimatedLiquidation && d.estimatedLiquidation > 0) {
        ctx.save();
        ctx.strokeStyle = '#f43f5e'; // Rose / crimson line
        ctx.lineWidth = 1.8;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.moveTo(startX, lpY);
        ctx.lineTo(endX + 30, lpY);
        ctx.stroke();
        ctx.setLineDash([]);

        // Label on chart for liquidation
        ctx.font = '10px Inter, sans-serif';
        ctx.fillStyle = '#f43f5e';
        ctx.textAlign = 'left';
        ctx.fillText(`Ước tính LP: $${d.estimatedLiquidation.toFixed(2)} (${d.leverage || 5}x Isolated)`, startX + 8, lpY - 4);
        ctx.restore();
      }

      // Watermark for DRAFT state
      if (!d.readOnly && (d.state === 'draft' || d.state === 'candidate')) {
        ctx.save();
        ctx.font = '900 13px Inter, sans-serif';
        ctx.fillStyle = 'rgba(245, 158, 11, 0.22)';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const boxMidY = (profitTop + riskTop + profitHeight + riskHeight) / 2;
        ctx.fillText('BẢN NHÁP — CHƯA ĐẶT LỆNH', startX + width / 2, boxMidY);
        ctx.restore();
      }

      // 5. Central Badge Pill
      const badgeY = entryY;
      const rrStatus = resolvePrimitiveRrStatus(d);
      const badgeText = `${d.direction} · Gross ${formatRR(d.grossRR)} · Net ${formatRR(d.estimatedNetRR)}${rrStatus.rrPassText} · ${d.state.toUpperCase()}`;
      ctx.font = 'bold 11px Inter, sans-serif';
      const textMetrics = ctx.measureText(badgeText);
      const badgeWidth = textMetrics.width + 24;
      const badgeHeight = 22;
      const badgeX = startX + (width - badgeWidth) / 2;

      // Badge background pill
      ctx.fillStyle = '#18181b';
      ctx.strokeStyle = rrStatus.strokeColor;
      ctx.lineWidth = 1.5;
      this._roundRect(ctx, badgeX, badgeY - badgeHeight / 2, badgeWidth, badgeHeight, 11);
      ctx.fill();
      ctx.stroke();

      // Badge text
      ctx.fillStyle = rrStatus.textColor;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(badgeText, badgeX + badgeWidth / 2, badgeY);

      // 6. Target Box Text (Reward info: explicit Gross vs Net)
      const tpTargetY = d.direction === 'LONG' ? profitTop + 14 : profitTop + profitHeight - 6;
      ctx.font = '10px Inter, sans-serif';
      ctx.fillStyle = nonPositiveReward ? '#facc15' : '#2dd4bf';
      ctx.textAlign = 'left';
      const mult = d.multiplier || 1.0;
      const targetDist = Math.abs(d.takeProfit - effectiveEntry);
      const grossReward = d.grossRewardUsdt != null && Number.isFinite(d.grossRewardUsdt) ? d.grossRewardUsdt : (d.quantity * mult * targetDist);
      const netReward = netTargetReward;
      const netRewardSign = netReward >= 0 ? '+' : '-';
      const rewardText = `TP: ${d.takeProfit.toFixed(2)} | Lãi theo giá: +$${grossReward.toFixed(2)} | Lãi ròng dự kiến: ${netRewardSign}$${Math.abs(netReward).toFixed(2)}`;
      ctx.fillText(rewardText, startX + 8, tpTargetY);

      // 7. Stop Box Text (Risk info: explicit Gross vs Net)
      const slTargetY = d.direction === 'LONG' ? riskTop + riskHeight - 6 : riskTop + 14;
      ctx.fillStyle = '#f87171';
      const stopDist = Math.abs(effectiveEntry - d.stopLoss);
      const grossLoss = d.grossLossUsdt != null && Number.isFinite(d.grossLossUsdt) ? d.grossLossUsdt : (d.quantity * mult * stopDist);
      const netRisk = d.initialRiskUsdt;
      const riskText = `SL: ${d.stopLoss.toFixed(2)} | Lỗ theo giá: -$${grossLoss.toFixed(2)} | Lỗ ròng dự kiến: -$${netRisk.toFixed(2)} | KL: ${d.quantity} oz`;
      ctx.fillText(riskText, startX + 8, slTargetY);

      // 8. Draggable Handles for Draft Mode
      if (d.state === 'draft') {
        const handleX = startX + 16;
        const radius = 6;

        const drawHandle = (y: number, color: string, isHovered: boolean) => {
          ctx.beginPath();
          ctx.arc(handleX, y, isHovered ? radius + 2 : radius, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 2;
          ctx.stroke();
        };

        // Entry handle
        drawHandle(entryY, d.direction === 'LONG' ? '#3b82f6' : '#f59e0b', this._hoverPart === 'entry');
        // TP handle
        drawHandle(tpY, '#10b981', this._hoverPart === 'tp');
        // SL handle
        drawHandle(slY, '#ef4444', this._hoverPart === 'sl');

        // Right edge drag handle
        ctx.beginPath();
        ctx.arc(endX, entryY, this._hoverPart === 'right_edge' ? 7 : 5, 0, Math.PI * 2);
        ctx.fillStyle = '#6366f1';
        ctx.fill();
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      ctx.restore();
    });
  }

  private _roundRect(
    ctx: CanvasRenderingContext2D,
    x: number,
    y: number,
    w: number,
    h: number,
    r: number
  ) {
    if (w < 2 * r) r = w / 2;
    if (h < 2 * r) r = h / 2;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
}

/**
 * Pane View wrapper implementing IPrimitivePaneView
 */
class RiskRewardPaneView implements IPrimitivePaneView {
  private _renderer = new RiskRewardPaneRenderer();

  update(
    data: RiskRewardData,
    entryY: number | null,
    slY: number | null,
    tpY: number | null,
    lpY: number | null,
    startX: number,
    endX: number,
    hoverPart: DragTargetPart | null = null
  ) {
    this._renderer.update(data, entryY, slY, tpY, lpY, startX, endX, hoverPart);
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }

  zOrder(): 'normal' {
    return 'normal';
  }
}

let _measureCtx: CanvasRenderingContext2D | null = null;
function measureBadgeTextWidth(text: string): number {
  if (typeof document !== 'undefined') {
    if (!_measureCtx) {
      const c = document.createElement('canvas');
      _measureCtx = c.getContext('2d');
    }
    if (_measureCtx) {
      _measureCtx.font = 'bold 11px Inter, sans-serif';
      return _measureCtx.measureText(text).width;
    }
  }
  return text.length * 6.8;
}

/**
 * Main RiskRewardPrimitive implementing ISeriesPrimitive<Time>
 */
export class RiskRewardPrimitive implements ISeriesPrimitive<Time> {
  private _data: RiskRewardData;
  private _param: SeriesAttachedParameter<Time> | null = null;
  private _paneView = new RiskRewardPaneView();
  private _entryAxisView = new PriceAxisView();
  private _slAxisView = new PriceAxisView();
  private _tpAxisView = new PriceAxisView();
  private _lpAxisView = new PriceAxisView();
  private _currentGeometry: {
    entryY: number | null;
    slY: number | null;
    tpY: number | null;
    lpY: number | null;
    startX: number;
    endX: number;
  } = { entryY: null, slY: null, tpY: null, lpY: null, startX: 0, endX: 0 };
  private _hoverPart: DragTargetPart | null = null;

  private _normalizeData(data: RiskRewardData): RiskRewardData {
    const isOrderState = ['armed', 'paper_open', 'closed', 'invalidated'].includes(data.state);
    return {
      ...data,
      readOnly: data.readOnly ?? (isOrderState ? true : false),
    };
  }

  constructor(data: RiskRewardData) {
    this._data = this._normalizeData(data);
  }

  setData(data: RiskRewardData) {
    this._data = this._normalizeData(data);
    this.updateAllViews();
    if (this._param) {
      this._param.requestUpdate();
    }
  }

  setHoverPart(part: DragTargetPart | null) {
    if (this._hoverPart !== part) {
      this._hoverPart = part;
      this.updateAllViews();
      if (this._param) {
        this._param.requestUpdate();
      }
    }
  }

  getData(): RiskRewardData {
    return this._data;
  }

  getGeometry() {
    return this._currentGeometry;
  }

  getReservedRects(): readonly Rect[] {
    const { entryY, slY, tpY, startX, endX } = this._currentGeometry;
    if (entryY === null || slY === null || tpY === null || !Number.isFinite(startX) || !Number.isFinite(endX)) {
      return [];
    }

    const rects: Rect[] = [];
    const handleX = startX + 16;

    // 1. Entry Badge / Handle (width 28, height 20)
    rects.push({
      x: handleX - 14,
      y: entryY - 10,
      width: 28,
      height: 20,
    });

    // 2. Stop Loss Badge / Handle
    rects.push({
      x: handleX - 14,
      y: slY - 10,
      width: 28,
      height: 20,
    });

    // 3. Take Profit Badge / Handle
    rects.push({
      x: handleX - 14,
      y: tpY - 10,
      width: 28,
      height: 20,
    });

    // 4. Right Edge Handle
    const minY = Math.min(entryY, slY, tpY);
    const maxY = Math.max(entryY, slY, tpY);
    rects.push({
      x: endX - 6,
      y: minY - 5,
      width: 12,
      height: Math.abs(maxY - minY) + 10,
    });

    // 5. Central Badge Pill at entryY
    const d = this._data;
    const width = Math.max(endX - startX, 40);
    const rrStatus = resolvePrimitiveRrStatus(d);
    const badgeText = `${d.direction} · Gross ${formatRR(d.grossRR)} · Net ${formatRR(d.estimatedNetRR)}${rrStatus.rrPassText} · ${d.state.toUpperCase()}`;
    const badgeWidth = measureBadgeTextWidth(badgeText) + 24;
    const badgeHeight = 22;
    const badgeX = startX + (width - badgeWidth) / 2;
    const badgeY = entryY;

    rects.push({
      x: badgeX,
      y: badgeY - badgeHeight / 2,
      width: badgeWidth,
      height: badgeHeight,
    });

    return rects;
  }

  autoscaleInfo(): AutoscaleInfo | null {
    // Only the attached R:R contributes. Liquidation is deliberately excluded.
    const prices = [this._data.actualEntry ?? this._data.plannedEntry, this._data.stopLoss, this._data.takeProfit];
    if (prices.some(price => !Number.isFinite(price) || price <= 0)) return null;
    const low = Math.min(...prices);
    const high = Math.max(...prices);
    const padding = Math.max((high - low) * 0.08, 0.02);
    return { priceRange: { minValue: low - padding, maxValue: high + padding } };
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._param = param;
    this.updateAllViews();
    this._param.requestUpdate();
  }

  detached(): void {
    this._param = null;
  }

  updateAllViews(): void {
    if (!this._param) return;

    const series = this._param.series;
    const timeScale = this._param.chart.timeScale();

    const entryPrice = this._data.actualEntry ?? this._data.plannedEntry;
    const entryY = series.priceToCoordinate(entryPrice);
    const slY = series.priceToCoordinate(this._data.stopLoss);
    const tpY = series.priceToCoordinate(this._data.takeProfit);
    const lpY = (this._data.estimatedLiquidation && this._data.estimatedLiquidation > 0)
      ? series.priceToCoordinate(this._data.estimatedLiquidation)
      : null;

    // Calculate X span anchored to time or logical bars
    let startX = 60;
    if (this._data.entryTime) {
      const coord = timeScale.timeToCoordinate(this._data.entryTime as Time);
      if (coord !== null) {
        startX = coord;
      } else {
        // Entry happened inside the still-forming bar (after the last loaded closed bar): project it onto the
        // logical axis by bar count instead of falling back to a fake left-edge start.
        const bars = series.data();
        const n = bars.length;
        if (n >= 2) {
          const last = bars[n - 1].time as number;
          const step = last - (bars[n - 2].time as number);
          const t = this._data.entryTime as number;
          if (step > 0 && t > last) {
            const projected = timeScale.logicalToCoordinate((n - 1 + (t - last) / step) as unknown as Logical);
            if (projected !== null) startX = projected;
          }
        }
      }
    } else {
      const visibleRange = timeScale.getVisibleLogicalRange();
      if (visibleRange) {
        const coord = timeScale.logicalToCoordinate((visibleRange.to - 20) as unknown as Logical);
        if (coord !== null) startX = Math.max(50, coord);
      }
    }

    const barCount = this._data.projectedBars || 25;
    let endX = startX + 220;
    const visibleRange = timeScale.getVisibleLogicalRange();
    if (visibleRange) {
      const c1 = timeScale.logicalToCoordinate(visibleRange.from as unknown as Logical);
      const c2 = timeScale.logicalToCoordinate((visibleRange.from + 1) as unknown as Logical);
      if (c1 !== null && c2 !== null) {
        const barSpacing = Math.abs(c2 - c1);
        endX = startX + Math.max(100, barSpacing * barCount);
      }
    }

    this._currentGeometry = { entryY, slY, tpY, lpY, startX, endX };

    // Update Pane View
    this._paneView.update(this._data, entryY, slY, tpY, lpY, startX, endX, this._hoverPart);

    // Update Price Axis Views
    // Axis badges: a null coordinate (price outside the scale) or invalid data hides the badge instead of
    // keeping the previous order's label alive.
    const finiteData = [entryPrice, this._data.stopLoss, this._data.takeProfit].every((v) => Number.isFinite(v) && v > 0);
    const entryColor = this._data.direction === 'LONG' ? '#2563eb' : '#d97706';
    this._entryAxisView.update(entryY ?? -9999, `${entryPrice.toFixed(2)} [Entry]`, entryColor, '#ffffff', finiteData && entryY !== null);
    this._slAxisView.update(slY ?? -9999, `${this._data.stopLoss.toFixed(2)} [SL]`, '#dc2626', '#ffffff', finiteData && slY !== null);
    this._tpAxisView.update(tpY ?? -9999, `${this._data.takeProfit.toFixed(2)} [TP]`, '#16a34a', '#ffffff', finiteData && tpY !== null);
    const hasLp = !!(this._data.estimatedLiquidation && this._data.estimatedLiquidation > 0);
    this._lpAxisView.update(lpY ?? -9999, `${(this._data.estimatedLiquidation ?? 0).toFixed(2)} [LP]`, '#e11d48', '#ffffff', hasLp && lpY !== null);
  }

  /**
   * Hit test against handles, borders, right edge, and box body.
   */
  hitTest(x: number, y: number): DragHitResult | null {
    if (this._data.readOnly || ['armed', 'paper_open', 'closed', 'invalidated'].includes(this._data.state)) return null;
    const { entryY, slY, tpY, startX, endX } = this._currentGeometry;
    if (entryY === null || slY === null || tpY === null) return null;

    const handleX = startX + 16;
    const handleRadius = 12; // 12px touch tolerance

    const extId = `rr-${this._data.id}`;
    // 1. Handles
    if (Math.hypot(x - handleX, y - entryY) <= handleRadius) {
      return { externalId: extId, zOrder: 'normal', part: 'entry', entryY, slY, tpY, startX, endX };
    }
    if (Math.hypot(x - handleX, y - slY) <= handleRadius) {
      return { externalId: extId, zOrder: 'normal', part: 'sl', entryY, slY, tpY, startX, endX };
    }
    if (Math.hypot(x - handleX, y - tpY) <= handleRadius) {
      return { externalId: extId, zOrder: 'normal', part: 'tp', entryY, slY, tpY, startX, endX };
    }

    // 2. Right Edge
    if (Math.abs(x - endX) <= 10 && y >= Math.min(entryY, slY, tpY) - 5 && y <= Math.max(entryY, slY, tpY) + 5) {
      return { externalId: extId, zOrder: 'normal', part: 'right_edge', entryY, slY, tpY, startX, endX };
    }

    // 3. Lines inside horizontal span
    if (x >= startX - 5 && x <= endX + 5) {
      if (Math.abs(y - entryY) <= 7) return { externalId: extId, zOrder: 'normal', part: 'entry', entryY, slY, tpY, startX, endX };
      if (Math.abs(y - slY) <= 7) return { externalId: extId, zOrder: 'normal', part: 'sl', entryY, slY, tpY, startX, endX };
      if (Math.abs(y - tpY) <= 7) return { externalId: extId, zOrder: 'normal', part: 'tp', entryY, slY, tpY, startX, endX };
    }

    // 4. Box Body
    const minY = Math.min(entryY, slY, tpY);
    const maxY = Math.max(entryY, slY, tpY);
    if (x >= startX && x <= endX && y >= minY && y <= maxY) {
      return { externalId: extId, zOrder: 'normal', part: 'body', entryY, slY, tpY, startX, endX };
    }

    return null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this._paneView];
  }

  priceAxisViews(): readonly ISeriesPrimitiveAxisView[] {
    const views = [this._entryAxisView, this._slAxisView, this._tpAxisView];
    if (this._data.estimatedLiquidation && this._data.estimatedLiquidation > 0 && this._currentGeometry.lpY !== null) {
      views.push(this._lpAxisView);
    }
    return views;
  }

  timeAxisViews(): readonly ISeriesPrimitiveAxisView[] {
    return [];
  }
}
