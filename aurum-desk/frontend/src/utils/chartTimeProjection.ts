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
