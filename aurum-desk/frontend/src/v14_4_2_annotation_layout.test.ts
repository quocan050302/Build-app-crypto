import { describe, it, expect } from 'vitest';
import {
  layoutAnnotationLabels,
  contained,
  overlaps,
  chooseLabelBox,
} from './utils/chartAnnotationLayout';
import type { LabelCandidate, Rect } from './utils/chartAnnotationLayout';
import type { Box } from './utils/chartTimeProjection';

describe('V14.4.2 Annotation Layout Unit Tests (Part 121)', () => {
  const defaultViewport: Box = { x: 0, y: 0, width: 800, height: 600 };

  it('contained helper returns true only when rect is strictly within viewport', () => {
    const vp: Rect = { x: 0, y: 0, width: 100, height: 100 };
    expect(contained({ x: 10, y: 10, width: 20, height: 20 }, vp)).toBe(true);
    expect(contained({ x: 0, y: 0, width: 100, height: 100 }, vp)).toBe(true);
    expect(contained({ x: -1, y: 0, width: 20, height: 20 }, vp)).toBe(false);
    expect(contained({ x: 90, y: 90, width: 20, height: 20 }, vp)).toBe(false);
    expect(contained({ x: NaN, y: 10, width: 20, height: 20 }, vp)).toBe(false);
  });

  it('overlaps helper detects intersection properly', () => {
    const a: Rect = { x: 10, y: 10, width: 50, height: 20 };
    const b: Rect = { x: 30, y: 20, width: 50, height: 20 };
    const c: Rect = { x: 100, y: 100, width: 20, height: 20 };
    expect(overlaps(a, b)).toBe(true);
    expect(overlaps(a, c)).toBe(false);
  });

  it('chooseLabelBox selects first valid candidate not colliding with occupied', () => {
    const vp: Rect = { x: 0, y: 0, width: 500, height: 500 };
    const occupied: Rect[] = [{ x: 50, y: 50, width: 40, height: 20 }];
    const candidates: Rect[] = [
      { x: 55, y: 55, width: 40, height: 20 }, // collides with occupied
      { x: 150, y: 150, width: 40, height: 20 }, // valid
    ];
    const chosen = chooseLabelBox(candidates, vp, occupied);
    expect(chosen).toEqual({ x: 150, y: 150, width: 40, height: 20 });
  });

  it('handles zero pane / degenerate viewport safely without exceptions', () => {
    const candidates: LabelCandidate[] = [
      { id: '1', x: 50, y: 50, width: 40, height: 20, priority: 1 },
    ];
    const zeroVp: Box = { x: 0, y: 0, width: 0, height: 0 };
    const results = layoutAnnotationLabels(candidates, zeroVp);
    expect(results).toHaveLength(1);
    expect(results[0].visible).toBe(false);
  });

  it('respects RR exclusions and avoids reserved areas', () => {
    const rrExclusion: Rect = { x: 100, y: 100, width: 120, height: 40 };
    const candidates: LabelCandidate[] = [
      { id: 'sweep', x: 110, y: 105, width: 60, height: 20, priority: 10 },
    ];
    const results = layoutAnnotationLabels(candidates, defaultViewport, [rrExclusion]);
    expect(results).toHaveLength(1);
    // Should either shift away from the exclusion or remain visible outside
    if (results[0].visible) {
      expect(overlaps(results[0].box, rrExclusion)).toBe(false);
    }
  });

  it('deterministic priority sorting: higher priority occupies space first', () => {
    const candidates: LabelCandidate[] = [
      { id: 'low-pri', x: 200, y: 200, width: 60, height: 25, priority: 1 },
      { id: 'high-pri', x: 200, y: 200, width: 60, height: 25, priority: 100 },
    ];
    const results = layoutAnnotationLabels(candidates, defaultViewport);
    const high = results.find((r) => r.id === 'high-pri');
    const low = results.find((r) => r.id === 'low-pri');

    expect(high?.visible).toBe(true);
    // High priority gets first pick; low priority should either find lane or be hidden
    if (low?.visible) {
      expect(overlaps(high!.box, low.box)).toBe(false);
    }
  });

  it('preserves anchor coordinates (x, y) on candidates without modification', () => {
    const candidate: LabelCandidate = {
      id: 'sweep-1',
      x: 350.5,
      y: 120.25,
      width: 50,
      height: 20,
      priority: 5,
    };
    const results = layoutAnnotationLabels([candidate], defaultViewport);
    expect(results[0].x).toBe(350.5);
    expect(results[0].y).toBe(120.25);
  });

  it('clamps oversized labels to viewport dimensions', () => {
    const candidate: LabelCandidate = {
      id: 'giant',
      x: 10,
      y: 10,
      width: 1500, // wider than viewport
      height: 20,
      priority: 5,
    };
    const results = layoutAnnotationLabels([candidate], defaultViewport);
    expect(results[0].visible).toBe(true);
    expect(results[0].box.x + results[0].box.width).toBeLessThanOrEqual(defaultViewport.width);
  });

  describe('L01-L05: Layout Exclusions and Viewport Constraints (PHẦN 066)', () => {
    it('L01: single RR label rect exclusion prevents any label overlap', () => {
      const rrBadge: Rect = { x: 200, y: 150, width: 80, height: 25 };
      const candidates: LabelCandidate[] = [
        { id: 'c1', x: 210, y: 155, width: 60, height: 18, priority: 50 },
      ];
      const placed = layoutAnnotationLabels(candidates, defaultViewport, [rrBadge]);
      expect(placed[0]).toBeDefined();
      if (placed[0].visible) {
        expect(contained(placed[0].box, defaultViewport)).toBe(true);
        expect(overlaps(placed[0].box, rrBadge)).toBe(false);
      }
    });

    it('L02: multiple dense RR exclusions (entry, sl, tp, right edge) are strictly avoided by all visible labels', () => {
      const exclusions: Rect[] = [
        { x: 150, y: 200, width: 28, height: 20 }, // entry handle
        { x: 150, y: 260, width: 28, height: 20 }, // sl handle
        { x: 150, y: 100, width: 28, height: 20 }, // tp handle
        { x: 350, y: 100, width: 12, height: 180 }, // right edge
      ];
      const candidates: LabelCandidate[] = [
        { id: 'sweep', x: 152, y: 202, width: 70, height: 16, priority: 90 },
        { id: 'fvg', x: 152, y: 258, width: 80, height: 16, priority: 80 },
        { id: 'bos', x: 152, y: 102, width: 65, height: 16, priority: 70 },
        { id: 'liq', x: 348, y: 150, width: 60, height: 16, priority: 60 },
      ];
      const placed = layoutAnnotationLabels(candidates, defaultViewport, exclusions);
      const visibleLabels = placed.filter((p) => p.visible);
      expect(visibleLabels.length).toBeGreaterThan(0);

      for (const p of visibleLabels) {
        expect(contained(p.box, defaultViewport)).toBe(true);
        for (const exc of exclusions) {
          expect(overlaps(p.box, exc)).toBe(false);
        }
      }
    });

    it('L03: oversized text is clamped to pane media width without negative coords', () => {
      const candidate: LabelCandidate = {
        id: 'long-ann',
        x: 50,
        y: 50,
        width: 2000,
        height: 800,
        priority: 10,
      };
      const placed = layoutAnnotationLabels([candidate], defaultViewport);
      expect(placed[0].visible).toBe(true);
      expect(placed[0].box.width).toBeLessThanOrEqual(defaultViewport.width);
      expect(placed[0].box.height).toBeLessThanOrEqual(defaultViewport.height);
      expect(contained(placed[0].box, defaultViewport)).toBe(true);
    });

    it('L04: non-zero viewport offset (e.g. padding / header) maintains containment', () => {
      const offsetViewport: Box = { x: 50, y: 40, width: 500, height: 400 };
      const candidate: LabelCandidate = {
        id: 'offset-c',
        x: 20, // outside left edge
        y: 20, // outside top edge
        width: 100,
        height: 20,
        priority: 20,
      };
      const placed = layoutAnnotationLabels([candidate], offsetViewport);
      expect(placed[0].visible).toBe(true);
      expect(contained(placed[0].box, offsetViewport)).toBe(true);
      expect(placed[0].box.x).toBeGreaterThanOrEqual(50);
      expect(placed[0].box.y).toBeGreaterThanOrEqual(40);
    });

    it('L05: RR hidden / empty exclusions list allows placing labels in previously blocked zone', () => {
      const blockedZone: Rect = { x: 200, y: 200, width: 100, height: 50 };
      const candidate: LabelCandidate = {
        id: 'target',
        x: 205,
        y: 205,
        width: 80,
        height: 20,
        priority: 80,
      };

      // When exclusions are empty (RR hidden / detached)
      const placed = layoutAnnotationLabels([candidate], defaultViewport, []);
      expect(placed[0].visible).toBe(true);
      expect(contained(placed[0].box, defaultViewport)).toBe(true);
      // In base lane it sits right inside the area that would have been blocked
      expect(overlaps(placed[0].box, blockedZone)).toBe(true);
    });
  });
});
