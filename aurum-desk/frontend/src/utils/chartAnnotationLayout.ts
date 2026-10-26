import type { Box } from './chartTimeProjection';

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function contained(rect: Rect, viewport: Rect): boolean {
  return (
    rect.width >= 0 &&
    rect.height >= 0 &&
    Number.isFinite(rect.x) &&
    Number.isFinite(rect.y) &&
    Number.isFinite(rect.width) &&
    Number.isFinite(rect.height) &&
    rect.x >= viewport.x &&
    rect.y >= viewport.y &&
    rect.x + rect.width <= viewport.x + viewport.width &&
    rect.y + rect.height <= viewport.y + viewport.height
  );
}

export function overlaps(a: Rect, b: Rect): boolean {
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  );
}

export function chooseLabelBox(
  candidates: readonly Rect[],
  viewport: Rect,
  occupied: readonly Rect[]
): Rect | null {
  for (const box of candidates) {
    if (contained(box, viewport) && !occupied.some((other) => overlaps(box, other))) {
      return box;
    }
  }
  return null;
}

export interface LabelCandidate {
  id: string;
  x: number;
  y: number;
  label?: string;
  text?: string;
  priority: number;
  width: number;
  height: number;
  color?: string;
}

export interface PlacedLabel extends LabelCandidate {
  box: Box;
  visible: boolean;
}

export function layoutAnnotationLabels(
  candidates: LabelCandidate[],
  viewport: Box,
  exclusions: Rect[] = []
): PlacedLabel[] {
  // If viewport is degenerate or invalid, no label can be visibly placed
  if (
    !Number.isFinite(viewport.width) ||
    !Number.isFinite(viewport.height) ||
    viewport.width <= 0 ||
    viewport.height <= 0
  ) {
    return candidates.map((c) => ({
      ...c,
      box: { x: c.x, y: c.y, width: c.width, height: c.height },
      visible: false,
    }));
  }

  const sorted = [...candidates].sort((a, b) => b.priority - a.priority);
  const occupied: Rect[] = [...exclusions];
  const results: PlacedLabel[] = [];

  for (const c of sorted) {
    // Validate finite coordinates
    if (
      !Number.isFinite(c.x) ||
      !Number.isFinite(c.y) ||
      !Number.isFinite(c.width) ||
      !Number.isFinite(c.height) ||
      c.width <= 0 ||
      c.height <= 0
    ) {
      results.push({
        ...c,
        box: { x: Number.isFinite(c.x) ? c.x : 0, y: Number.isFinite(c.y) ? c.y : 0, width: c.width || 0, height: c.height || 0 },
        visible: false,
      });
      continue;
    }

    // Clamp candidate dimensions to viewport if oversized
    const effectiveWidth = Math.min(c.width, Math.max(10, viewport.width - 4));
    const effectiveHeight = Math.min(c.height, Math.max(10, viewport.height - 4));

    // Candidate lanes around anchor point
    const baseBox: Rect = {
      x: Math.max(viewport.x, Math.min(c.x, viewport.x + viewport.width - effectiveWidth)),
      y: Math.max(viewport.y, Math.min(c.y, viewport.y + viewport.height - effectiveHeight)),
      width: effectiveWidth,
      height: effectiveHeight,
    };

    const candidateLanes: Rect[] = [
      // 1. Direct clamp
      baseBox,
      // 2. Above anchor
      {
        x: baseBox.x,
        y: Math.max(viewport.y, Math.min(c.y - (effectiveHeight + 4), viewport.y + viewport.height - effectiveHeight)),
        width: effectiveWidth,
        height: effectiveHeight,
      },
      // 3. Below anchor
      {
        x: baseBox.x,
        y: Math.max(viewport.y, Math.min(c.y + 4, viewport.y + viewport.height - effectiveHeight)),
        width: effectiveWidth,
        height: effectiveHeight,
      },
      // 4. Right of anchor
      {
        x: Math.max(viewport.x, Math.min(c.x + 8, viewport.x + viewport.width - effectiveWidth)),
        y: baseBox.y,
        width: effectiveWidth,
        height: effectiveHeight,
      },
      // 5. Left of anchor
      {
        x: Math.max(viewport.x, Math.min(c.x - effectiveWidth - 8, viewport.x + viewport.width - effectiveWidth)),
        y: baseBox.y,
        width: effectiveWidth,
        height: effectiveHeight,
      },
    ];

    const chosen = chooseLabelBox(candidateLanes, viewport, occupied);

    if (chosen) {
      occupied.push(chosen);
      results.push({ ...c, box: chosen, visible: true });
    } else {
      results.push({ ...c, box: baseBox, visible: false });
    }
  }

  return results;
}
