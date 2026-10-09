import type {
  ISeriesPrimitive,
  SeriesAttachedParameter,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  ISeriesPrimitiveAxisView,
  PrimitiveHoveredItem,
  Time,
  Logical,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';

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

      // 1. Profit Box (Green Zone)
      const profitTop = Math.min(entryY, tpY);
      const profitHeight = Math.abs(tpY - entryY);
      ctx.fillStyle = isMuted ? 'rgba(38, 166, 154, 0.08)' : 'rgba(38, 166, 154, 0.22)';
      ctx.fillRect(startX, profitTop, width, profitHeight);

      // Profit Border
      ctx.strokeStyle = isMuted ? 'rgba(38, 166, 154, 0.4)' : '#26a69a';
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
      if (d.state === 'draft') {
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
      const rrPassText = d.estimatedNetRR >= 2.0 ? '' : ' · [FAIL Net < 2.0]';
      const badgeText = `${d.direction} · R:R Gross 1:${d.grossRR.toFixed(2)} (Net 1:${d.estimatedNetRR.toFixed(2)})${rrPassText} · ${d.state.toUpperCase()}`;
      ctx.font = 'bold 11px Inter, sans-serif';
      const textMetrics = ctx.measureText(badgeText);
      const badgeWidth = textMetrics.width + 24;
      const badgeHeight = 22;
      const badgeX = startX + (width - badgeWidth) / 2;

      // Badge background pill
      ctx.fillStyle = '#18181b';
      ctx.strokeStyle = d.estimatedNetRR >= 2.0 ? (d.direction === 'LONG' ? '#26a69a' : '#ef5350') : '#eab308';
      ctx.lineWidth = 1.5;
      this._roundRect(ctx, badgeX, badgeY - badgeHeight / 2, badgeWidth, badgeHeight, 11);
      ctx.fill();
      ctx.stroke();

      // Badge text
      ctx.fillStyle = d.estimatedNetRR >= 2.0 ? '#f4f4f5' : '#fef08a';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(badgeText, badgeX + badgeWidth / 2, badgeY);

      // 6. Target Box Text (Reward info: explicit Gross vs Net)
      const tpTargetY = d.direction === 'LONG' ? profitTop + 14 : profitTop + profitHeight - 6;
      ctx.font = '10px Inter, sans-serif';
      ctx.fillStyle = '#2dd4bf';
      ctx.textAlign = 'left';
      const mult = d.multiplier || 1.0;
      const targetDist = Math.abs(d.takeProfit - effectiveEntry);
      const grossReward = d.grossRewardUsdt !== undefined ? d.grossRewardUsdt : (d.quantity * mult * targetDist);
      const netReward = d.netRewardUsdt !== undefined ? d.netRewardUsdt : (d.estimatedNetRR > 0 ? d.initialRiskUsdt * d.estimatedNetRR : grossReward);
      const netRewardSign = netReward >= 0 ? '+' : '-';
      const rewardText = `TP: ${d.takeProfit.toFixed(2)} | Lãi theo giá: +$${grossReward.toFixed(2)} | Lãi ròng dự kiến: ${netRewardSign}$${Math.abs(netReward).toFixed(2)}`;
      ctx.fillText(rewardText, startX + 8, tpTargetY);

      // 7. Stop Box Text (Risk info: explicit Gross vs Net)
      const slTargetY = d.direction === 'LONG' ? riskTop + riskHeight - 6 : riskTop + 14;
      ctx.fillStyle = '#f87171';
      const stopDist = Math.abs(effectiveEntry - d.stopLoss);
      const grossLoss = d.grossLossUsdt !== undefined ? d.grossLossUsdt : (d.quantity * mult * stopDist);
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

  constructor(data: RiskRewardData) {
    this._data = data;
  }

  setData(data: RiskRewardData) {
    this._data = data;
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
    if (entryY !== null) {
      const entryColor = this._data.direction === 'LONG' ? '#2563eb' : '#d97706';
      this._entryAxisView.update(entryY, `${entryPrice.toFixed(2)} [Entry]`, entryColor);
    }
    if (slY !== null) {
      this._slAxisView.update(slY, `${this._data.stopLoss.toFixed(2)} [SL]`, '#dc2626');
    }
    if (tpY !== null) {
      this._tpAxisView.update(tpY, `${this._data.takeProfit.toFixed(2)} [TP]`, '#16a34a');
    }
    if (lpY !== null && this._data.estimatedLiquidation && this._data.estimatedLiquidation > 0) {
      this._lpAxisView.update(lpY, `${this._data.estimatedLiquidation.toFixed(2)} [LP]`, '#e11d48');
    }
  }

  /**
   * Hit test against handles, borders, right edge, and box body.
   */
  hitTest(x: number, y: number): DragHitResult | null {
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
