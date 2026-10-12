/**
 * Pure layout and projection helpers for chart annotations (Phần 100 & Phụ lục D).
 */

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function intersects(a: Box, b: Box): boolean {
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  );
}

export function clipBox(box: Box, viewport: Box): Box | null {
  const x = Math.max(box.x, viewport.x);
  const y = Math.max(box.y, viewport.y);
  const right = Math.min(box.x + box.width, viewport.x + viewport.width);
  const bottom = Math.min(box.y + box.height, viewport.y + viewport.height);
  return right > x && bottom > y
    ? { x, y, width: right - x, height: bottom - y }
    : null;
}

export function containingBarIndex(
  startsMs: readonly number[],
  eventMs: number,
  durationMs: number
): number | null {
  if (!Number.isSafeInteger(eventMs) || durationMs <= 0 || !startsMs.length) return null;
  let lo = 0;
  let hi = startsMs.length - 1;
  let result = -1;

  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (startsMs[mid] <= eventMs) {
      result = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }

  if (result < 0 || eventMs >= startsMs[result] + durationMs) return null;
  return result;
}

export type TimeProjection =
  | { kind: 'IN'; barIndex: number; barStartMs: number }
  | { kind: 'BEFORE' }
  | { kind: 'AFTER' };

/**
 * Project an event time onto the LOADED bars. Times that fall in a data gap snap to the latest bar that started
 * at or before them; times before the first/after the last loaded bar are reported explicitly so a renderer can
 * clip (real geometry that continues outside the window) instead of inventing coordinates.
 */
export function projectMsToBar(
  startsMs: readonly number[],
  durationMs: number,
  eventMs: number
): TimeProjection | null {
  if (!Number.isSafeInteger(eventMs) || eventMs <= 0 || durationMs <= 0 || !startsMs.length) return null;
  if (eventMs < startsMs[0]) return { kind: 'BEFORE' };
  const last = startsMs.length - 1;
  if (eventMs >= startsMs[last] + durationMs) return { kind: 'AFTER' };
  let lo = 0;
  let hi = last;
  let idx = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (startsMs[mid] <= eventMs) {
      idx = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return { kind: 'IN', barIndex: idx, barStartMs: startsMs[idx] };
}

/**
 * Horizontal span of a zone/line in viewport pixels. A start BEFORE the loaded window clips to x=0 and an end
 * AFTER it clips to the viewport edge; a span entirely outside the window (or with a missing coordinate for a
 * time that IS inside the window) yields null: it is never stretched to the full pane.
 */
export function clipSpanX(
  p1: TimeProjection | null,
  x1: number | null,
  p2: TimeProjection | null,
  x2: number | null,
  viewportWidth: number
): { x1: number; x2: number } | null {
  if (!p1 || !p2 || !(viewportWidth > 0)) return null;
  if (p1.kind === 'AFTER' || p2.kind === 'BEFORE') return null;
  let left: number;
  let right: number;
  if (p1.kind === 'BEFORE') left = 0;
  else if (x1 === null) return null;
  else left = x1;
  if (p2.kind === 'AFTER') right = viewportWidth;
  else if (x2 === null) return null;
  else right = x2;
  left = Math.max(0, left);
  right = Math.min(viewportWidth, right);
  return right > left ? { x1: left, x2: right } : null;
}
