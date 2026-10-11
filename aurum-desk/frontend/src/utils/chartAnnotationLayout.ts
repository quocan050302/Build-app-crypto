import { intersects } from './chartTimeProjection';
import type { Box } from './chartTimeProjection';

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
  _viewport: Box
): PlacedLabel[] {
  const sorted = [...candidates].sort((a, b) => b.priority - a.priority);
  const placed: Box[] = [];
  const results: PlacedLabel[] = [];

  for (const c of sorted) {
    let box: Box = { x: c.x, y: c.y, width: c.width, height: c.height };
    let collides = placed.some((p) => intersects(box, p));

    if (collides) {
      const tryUp: Box = { ...box, y: box.y - (box.height + 4) };
      if (!placed.some((p) => intersects(tryUp, p))) {
        box = tryUp;
        collides = false;
      } else {
        const tryDown: Box = { ...box, y: box.y + (box.height + 4) };
        if (!placed.some((p) => intersects(tryDown, p))) {
          box = tryDown;
          collides = false;
        }
      }
    }

    if (!collides) {
      placed.push(box);
      results.push({ ...c, box, visible: true });
    } else {
      results.push({ ...c, box, visible: false });
    }
  }

  return results;
}
