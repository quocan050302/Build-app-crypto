import type {
  ISeriesPrimitive,
  SeriesAttachedParameter,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  ISeriesPrimitiveAxisView,
  Time,
  Logical,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';

export interface RiskRewardData {
  id: string;
  direction: 'LONG' | 'SHORT';
  state: 'draft' | 'candidate' | 'armed' | 'paper_open' | 'closed' | 'invalidated';
  entryTime?: number; // epoch seconds
  plannedEntry: number;
  actualEntry?: number;
  stopLoss: number;
  takeProfit: number;
  quantity: number;
  initialRiskUsdt: number;
  riskPct: number;
  grossRR: number;
  estimatedNetRR: number;
  realizedPnlNet?: number;
  realizedR?: number;
  invalidationReason?: string;
  strategyVersion?: string;
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
 * Pane Renderer for drawing the Risk/Reward overlay boxes, lines, and badges
 */
class RiskRewardPaneRenderer implements IPrimitivePaneRenderer {
  private _data: RiskRewardData | null = null;
  private _entryY: number | null = null;
  private _slY: number | null = null;
  private _tpY: number | null = null;
  private _startX: number = 0;
  private _endX: number = 0;

  update(
    data: RiskRewardData,
    entryY: number | null,
    slY: number | null,
    tpY: number | null,
    startX: number,
    endX: number
  ) {
    this._data = data;
    this._entryY = entryY;
    this._slY = slY;
    this._tpY = tpY;
    this._startX = startX;
    this._endX = endX;
  }

  draw(target: CanvasRenderingTarget2D) {
    if (!this._data || this._entryY === null || this._slY === null || this._tpY === null) {
      return;
    }

    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      const d = this._data!;
      const startX = Math.max(0, this._startX);
      const width = Math.max(120, this._endX - startX);
      const endX = startX + width;

      const entryY = this._entryY!;
      const slY = this._slY!;
      const tpY = this._tpY!;

      ctx.save();

      // State opacity
      const isMuted = d.state === 'closed' || d.state === 'invalidated';
      const isDashed = d.state === 'candidate' || d.state === 'armed' || d.state === 'draft';

      // 1. Profit Box (Green Zone)
      const profitTop = Math.min(entryY, tpY);
      const profitHeight = Math.abs(tpY - entryY);
      ctx.fillStyle = isMuted ? 'rgba(38, 166, 154, 0.08)' : 'rgba(38, 166, 154, 0.22)';
      ctx.fillRect(startX, profitTop, width, profitHeight);

      // Profit Border
      ctx.strokeStyle = isMuted ? 'rgba(38, 166, 154, 0.4)' : '#26a69a';
      ctx.lineWidth = 1.5;
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
      ctx.lineWidth = 1.5;
      if (isDashed) ctx.setLineDash([4, 4]);
      else ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(startX, slY);
      ctx.lineTo(endX, slY);
      ctx.stroke();

      // 3. Entry Line
      ctx.strokeStyle = isMuted ? '#6b7280' : (d.direction === 'LONG' ? '#3b82f6' : '#f59e0b');
      ctx.lineWidth = 2;
      if (isDashed) ctx.setLineDash([6, 3]);
      else ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(startX, entryY);
      ctx.lineTo(endX, entryY);
      ctx.stroke();
      ctx.setLineDash([]);

      // Outer boundary vertical edges
      ctx.strokeStyle = 'rgba(156, 163, 175, 0.25)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(startX, Math.min(slY, tpY));
      ctx.lineTo(startX, Math.max(slY, tpY));
      ctx.moveTo(endX, Math.min(slY, tpY));
      ctx.lineTo(endX, Math.max(slY, tpY));
      ctx.stroke();

      // 4. Central Badge Pill
      const badgeY = entryY;
      const badgeText = `${d.direction} · R:R 1:${d.grossRR.toFixed(2)} (Net 1:${d.estimatedNetRR.toFixed(2)}) · ${d.state.toUpperCase()}`;
      ctx.font = 'bold 11px Inter, sans-serif';
      const textMetrics = ctx.measureText(badgeText);
      const badgeWidth = textMetrics.width + 24;
      const badgeHeight = 22;
      const badgeX = startX + (width - badgeWidth) / 2;

      // Badge background pill
      ctx.fillStyle = '#18181b';
      ctx.strokeStyle = d.direction === 'LONG' ? '#26a69a' : '#ef5350';
      ctx.lineWidth = 1.5;
      this._roundRect(ctx, badgeX, badgeY - badgeHeight / 2, badgeWidth, badgeHeight, 11);
      ctx.fill();
      ctx.stroke();

      // Badge text
      ctx.fillStyle = '#f4f4f5';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(badgeText, badgeX + badgeWidth / 2, badgeY);

      // 5. Target Box Text (Reward info)
      const tpTargetY = d.direction === 'LONG' ? profitTop + 14 : profitTop + profitHeight - 6;
      ctx.font = '10px Inter, sans-serif';
      ctx.fillStyle = '#2dd4bf';
      ctx.textAlign = 'left';
      const rewardText = `TP: ${d.takeProfit.toFixed(2)} (+${Math.abs(d.takeProfit - d.plannedEntry).toFixed(2)}) | Lời: +$${(d.quantity * Math.abs(d.takeProfit - d.plannedEntry)).toFixed(2)}`;
      ctx.fillText(rewardText, startX + 8, tpTargetY);

      // 6. Stop Box Text (Risk info)
      const slTargetY = d.direction === 'LONG' ? riskTop + riskHeight - 6 : riskTop + 14;
      ctx.fillStyle = '#f87171';
      const riskText = `SL: ${d.stopLoss.toFixed(2)} (-${Math.abs(d.plannedEntry - d.stopLoss).toFixed(2)}) | Rủi ro: -$${d.initialRiskUsdt.toFixed(2)} | KL: ${d.quantity} oz`;
      ctx.fillText(riskText, startX + 8, slTargetY);

      // 7. Handles for interactive draft mode
      if (d.state === 'draft') {
        ctx.fillStyle = '#ffffff';
        ctx.strokeStyle = '#18181b';
        ctx.lineWidth = 2;
        // Entry handle
        ctx.beginPath();
        ctx.arc(startX + 12, entryY, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        // TP handle
        ctx.beginPath();
        ctx.arc(startX + 12, tpY, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        // SL handle
        ctx.beginPath();
        ctx.arc(startX + 12, slY, 5, 0, Math.PI * 2);
        ctx.fill();
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
    startX: number,
    endX: number
  ) {
    this._renderer.update(data, entryY, slY, tpY, startX, endX);
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
  private _onUpdateCallback?: (updatedData: RiskRewardData) => void;

  constructor(data: RiskRewardData, onUpdate?: (updatedData: RiskRewardData) => void) {
    this._data = data;
    this._onUpdateCallback = onUpdate;
  }

  setData(data: RiskRewardData) {
    this._data = data;
    this.updateAllViews();
    if (this._onUpdateCallback) {
      this._onUpdateCallback(this._data);
    }
    if (this._param) {
      this._param.requestUpdate();
    }
  }

  getData(): RiskRewardData {
    return this._data;
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

    const entryPrice = this._data.actualEntry || this._data.plannedEntry;
    const entryY = series.priceToCoordinate(entryPrice);
    const slY = series.priceToCoordinate(this._data.stopLoss);
    const tpY = series.priceToCoordinate(this._data.takeProfit);

    // Calculate X span
    let startX = 50;
    if (this._data.entryTime) {
      const coord = timeScale.timeToCoordinate(this._data.entryTime as Time);
      if (coord !== null) {
        startX = coord;
      }
    } else {
      // Default to right side of visible range
      const visibleRange = timeScale.getVisibleLogicalRange();
      if (visibleRange) {
        const coord = timeScale.logicalToCoordinate((visibleRange.to - 15) as unknown as Logical);
        if (coord !== null) startX = Math.max(50, coord);
      }
    }

    const endX = startX + 220; // 220px projected width


    // Update Pane View
    this._paneView.update(this._data, entryY, slY, tpY, startX, endX);

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
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this._paneView];
  }

  priceAxisViews(): readonly ISeriesPrimitiveAxisView[] {
    return [this._entryAxisView, this._slAxisView, this._tpAxisView];
  }

  timeAxisViews(): readonly ISeriesPrimitiveAxisView[] {
    return [];
  }
}
