/**
 * frontend/src/utils/evidenceFocus.ts
 * V14.4.2 — Evidence Anchor Indexing and Pure Focus Range Geometry (Appendix E).
 *
 * Flattens all structural and execution evidence from canonical bundles (swings,
 * breaks, sweeps, zones, displacements, retests, liquidity, targets, invalidations)
 * into unified EvidenceAnchor records with finite millisecond timestamps.
 * Focus range computation converts to seconds strictly at the chart boundary.
 */

export interface EvidenceAnchor {
  id: string;
  kind: string;
  label?: string;
  occurredAtMs: number | null;
  knownAtMs: number | null;
  startAtMs: number | null;
  endAtMs: number | null;
  price: number | null;
  sourceIds?: string[];
  hasCoordinates: boolean;
  missingReason?: string;
}

export interface FocusRangeSec {
  fromSec: number;
  toSec: number;
}

/**
 * Pure function to calculate visible time range for focusing an evidence anchor.
 * Converts milliseconds to seconds strictly at the return boundary.
 */
export function computeEvidenceFocusRange(
  anchor: EvidenceAnchor,
  stepMs: number,
  paddingBars = 16
): FocusRangeSec | null {
  if (!Number.isFinite(stepMs) || stepMs <= 0) return null;
  const start = anchor.startAtMs ?? anchor.occurredAtMs;
  const end = anchor.endAtMs ?? anchor.occurredAtMs ?? start;

  if (start === null || end === null || !Number.isFinite(start) || !Number.isFinite(end)) {
    return null;
  }
  if (start <= 0 || end < start || paddingBars < 0 || !Number.isFinite(paddingBars)) {
    return null;
  }

  const pad = paddingBars * stepMs;
  return {
    fromSec: (start - pad) / 1000,
    toSec: (end + pad) / 1000,
  };
}

function validMs(v: any): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.floor(v) : null;
}

function validPrice(v: any): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null;
}

/**
 * Extracts and flattens all evidence types from the raw selected bundle.
 * Preserves exact identities and reports whether valid time/price anchors exist.
 */
export function flattenEvidenceBundle(rawBundle: any): EvidenceAnchor[] {
  if (!rawBundle || typeof rawBundle !== 'object') return [];
  const results: EvidenceAnchor[] = [];
  const seenIds = new Set<string>();

  const add = (
    id: string | undefined,
    kind: string,
    label: string,
    occurredAtMs: number | null,
    knownAtMs: number | null,
    startAtMs: number | null,
    endAtMs: number | null,
    price: number | null,
    sourceIds?: string[]
  ) => {
    if (!id || seenIds.has(id)) return;
    seenIds.add(id);
    const hasTime = (startAtMs !== null && startAtMs > 0) || (occurredAtMs !== null && occurredAtMs > 0);
    const hasPrice = price !== null && price > 0;
    const hasCoords = hasTime && hasPrice;

    results.push({
      id,
      kind,
      label,
      occurredAtMs,
      knownAtMs,
      startAtMs,
      endAtMs,
      price,
      sourceIds,
      hasCoordinates: hasCoords,
      missingReason: !hasTime && !hasPrice
        ? 'Thiếu thời gian và giá gốc'
        : !hasTime
        ? 'Thiếu thời gian gốc'
        : !hasPrice
        ? 'Thiếu tọa độ giá gốc'
        : undefined,
    });
  };

  // 1. Structure Events (BOS, CHOCH, SWEEP, MSS)
  const events = Array.isArray(rawBundle.structure_events)
    ? rawBundle.structure_events
    : Array.isArray(rawBundle.events)
    ? rawBundle.events
    : [];
  for (const ev of events) {
    const occurred = validMs(ev.occurred_at_ms ?? ev.confirmed_at_ms ?? ev.confirmed_at ?? ev.timestamp ?? ev.timeMs ?? ev.start_at_ms ?? ev.start_at);
    const known = validMs(ev.known_at_ms ?? ev.confirmed_at_ms ?? ev.confirmed_at ?? occurred);
    const price = validPrice(ev.level ?? ev.price ?? ev.wick_extreme);
    const kind = ev.event_type || ev.kind || 'EVENT';
    add(ev.id, kind, `${kind} (${ev.direction || ''})`, occurred, known, occurred, null, price, ev.source_ids);
  }

  // 2. Zones (FVG, POI, OB)
  const zones = Array.isArray(rawBundle.poi_zones)
    ? rawBundle.poi_zones
    : Array.isArray(rawBundle.zones)
    ? rawBundle.zones
    : [];
  for (const z of zones) {
    const start = validMs(z.start_at_ms ?? z.occurred_at_ms ?? z.start_at ?? z.startAtMs ?? z.timestamp ?? z.confirmed_at ?? z.timeMs);
    const end = validMs(z.end_at_ms ?? z.display_end_ms ?? z.end_at ?? z.endAtMs ?? z.mitigated_at ?? z.timestamp ?? start);
    const top = validPrice(z.top);
    const bottom = validPrice(z.bottom);
    const midPrice = top !== null && bottom !== null ? (top + bottom) / 2 : (top ?? bottom);
    const kind = z.kind || z.type || 'ZONE';
    add(z.id, kind, `Vùng ${kind} ${z.direction || ''}`, start, end, start, end, midPrice, z.source_ids);
  }

  // 3. Liquidity Levels
  const liqs = Array.isArray(rawBundle.liquidity_levels)
    ? rawBundle.liquidity_levels
    : Array.isArray(rawBundle.liquidity)
    ? rawBundle.liquidity
    : [];
  for (const l of liqs) {
    const start = validMs(l.start_at_ms ?? l.occurred_at_ms ?? l.start_at ?? l.timeMs ?? l.timestamp ?? l.created_at);
    const price = validPrice(l.price);
    const label = l.label || l.name || 'Thanh khoản';
    add(l.id, 'LIQUIDITY', label, start, null, start, null, price, l.source_ids);
  }

  // 4. Swings
  const swings = Array.isArray(rawBundle.swings) ? rawBundle.swings : [];
  for (const s of swings) {
    const t = validMs(s.occurred_at_ms ?? s.timestamp ?? s.timeMs);
    const price = validPrice(s.price);
    const label = s.label || (s.type === 'HIGH' ? 'Swing High' : 'Swing Low');
    add(s.id || `swing-${t}-${price}`, 'SWING', label, t, null, t, null, price);
  }

  // 5. Displacements
  const displacements = Array.isArray(rawBundle.displacements)
    ? rawBundle.displacements
    : rawBundle.displacement_annotation
    ? [rawBundle.displacement_annotation]
    : [];
  for (const d of displacements) {
    const t = validMs(d.candle_time_ms ?? d.candle_time ?? d.occurred_at_ms ?? d.timestamp ?? d.timeMs);
    const price = validPrice(d.level ?? d.high ?? d.low);
    add(d.id || `disp-${t}`, 'DISPLACEMENT', 'Nến dịch chuyển mạnh', t, null, t, null, price);
  }

  // 6. Retests
  const retests = Array.isArray(rawBundle.retests) ? rawBundle.retests : [];
  for (const r of retests) {
    const t = validMs(r.occurred_at_ms ?? r.touch_time_ms ?? r.start_at ?? r.timeMs);
    const price = validPrice(r.touch_price ?? r.level ?? r.price);
    add(r.id || `retest-${t}`, 'RETEST', 'Kiểm tra lại vùng', t, null, t, null, price);
  }

  // 7. Target & Invalidation
  if (rawBundle.target && typeof rawBundle.target === 'object') {
    const t = rawBundle.target;
    const price = validPrice(t.price);
    add(t.id || 'target-level', 'TARGET', t.name || 'Mục tiêu giá', validMs(t.timeMs), null, validMs(t.timeMs), null, price);
  }
  if (rawBundle.invalidation && typeof rawBundle.invalidation === 'object') {
    const inv = rawBundle.invalidation;
    const price = validPrice(inv.price);
    add(inv.id || 'invalidation-level', 'INVALIDATION', inv.condition || 'Mức hủy', validMs(inv.timeMs), null, validMs(inv.timeMs), null, price);
  }

  return results;
}

/**
 * Builds an index of all anchors from a raw bundle by ID and externalId.
 */
export function buildEvidenceAnchorIndex(rawBundle: any): ReadonlyMap<string, EvidenceAnchor> {
  const anchors = flattenEvidenceBundle(rawBundle);
  const index = new Map<string, EvidenceAnchor>();
  for (const a of anchors) {
    index.set(a.id, a);
  }
  return index;
}
