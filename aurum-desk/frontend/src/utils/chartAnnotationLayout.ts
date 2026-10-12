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
  viewport: Box
): PlacedLabel[] {
  const sorted = [...candidates].sort((a, b) => b.priority - a.priority);
  const placed: Box[] = [];
  const results: PlacedLabel[] = [];

  for (const c of sorted) {
    let box: Box = { x: c.x, y: c.y, width: c.width, height: c.height };

    // Initial clamp
    box.x = Math.max(viewport.x, Math.min(box.x, viewport.x + viewport.width - box.width));
    box.y = Math.max(viewport.y, Math.min(box.y, viewport.y + viewport.height - box.height));

    let collides = placed.some((p) => intersects(box, p));

    if (collides) {
      let tryUp: Box = { ...box, y: box.y - (box.height + 4) };
      tryUp.y = Math.max(viewport.y, Math.min(tryUp.y, viewport.y + viewport.height - tryUp.height));
      
      if (!placed.some((p) => intersects(tryUp, p))) {
        box = tryUp;
        collides = false;
      } else {
        let tryDown: Box = { ...box, y: box.y + (box.height + 4) };
        tryDown.y = Math.max(viewport.y, Math.min(tryDown.y, viewport.y + viewport.height - tryDown.height));
        
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
