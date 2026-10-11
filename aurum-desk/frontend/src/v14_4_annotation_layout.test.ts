import { describe, it, expect } from 'vitest';
import {
  intersects,
  clipBox,
  containingBarIndex,
  type Box,
} from './utils/chartTimeProjection';
import { layoutAnnotationLabels } from './utils/chartAnnotationLayout';
import { chartAnnotationTheme } from './utils/chartAnnotationTheme';

describe('V14.4 Annotation Layout & Viewport Projection Tests', () => {
  it('correctly clips boxes against viewport without expanding outside', () => {
    const viewport: Box = { x: 0, y: 0, width: 800, height: 600 };

    const insideBox: Box = { x: 100, y: 100, width: 200, height: 150 };
    const clippedInside = clipBox(insideBox, viewport);
    expect(clippedInside).toEqual(insideBox);

    const leftOverflowBox: Box = { x: -50, y: 100, width: 100, height: 100 };
    const clippedLeft = clipBox(leftOverflowBox, viewport);
    expect(clippedLeft).toEqual({ x: 0, y: 100, width: 50, height: 100 });

    const rightOverflowBox: Box = { x: 750, y: 100, width: 100, height: 100 };
    const clippedRight = clipBox(rightOverflowBox, viewport);
    expect(clippedRight).toEqual({ x: 750, y: 100, width: 50, height: 100 });

    const completelyOutsideBox: Box = { x: 900, y: 100, width: 50, height: 50 };
    const clippedOutside = clipBox(completelyOutsideBox, viewport);
    expect(clippedOutside).toBeNull();
  });

  it('locates containing bar index in sorted loaded bars with binary search', () => {
    // 5-minute bars (300,000 ms duration)
    const durationMs = 300000;
    const barStarts = [
      1710000000000,
      1710000300000,
      1710000600000,
      1710000900000,
      1710001200000,
    ];

    // Inside bar 0
    expect(containingBarIndex(barStarts, 1710000100000, durationMs)).toBe(0);
    // At boundary of bar 2
    expect(containingBarIndex(barStarts, 1710000600000, durationMs)).toBe(2);
    // Inside bar 4
    expect(containingBarIndex(barStarts, 1710001400000, durationMs)).toBe(4);
    // Before first bar -> null
    expect(containingBarIndex(barStarts, 1709999000000, durationMs)).toBeNull();
    // After last bar duration -> null
    expect(containingBarIndex(barStarts, 1710001600000, durationMs)).toBeNull();
  });

  it('resolves priority label layout without overlapping essential entry/SL/TP badges', () => {
    const anchors = [
      { id: 'entry', x: 200, y: 300, width: 50, height: 16, priority: 1, label: 'Entry' },
      { id: 'sl', x: 200, y: 340, width: 50, height: 16, priority: 1, label: 'SL' },
      { id: 'tp', x: 200, y: 220, width: 50, height: 16, priority: 1, label: 'TP' },
      { id: 'fvg', x: 205, y: 302, width: 60, height: 14, priority: 5, label: 'FVG' }, // Near entry
    ];

    const viewport: Box = { x: 0, y: 0, width: 800, height: 600 };
    const layout = layoutAnnotationLabels(anchors, viewport);

    expect(layout.length).toBe(4);
    // Entry priority badge must be visible
    const entryLayout = layout.find((l) => l.id === 'entry');
    expect(entryLayout?.visible).toBe(true);

    // No two visible labels should overlap
    const visibleBoxes = layout.filter((l) => l.visible).map((l) => l.box);
    for (let i = 0; i < visibleBoxes.length; i++) {
      for (let j = i + 1; j < visibleBoxes.length; j++) {
        expect(intersects(visibleBoxes[i], visibleBoxes[j])).toBe(false);
      }
    }
  });

  it('provides consistent color scheme and fonts across SMC layers', () => {
    expect(chartAnnotationTheme.reward).toBe('#10b981');
    expect(chartAnnotationTheme.risk).toBe('#f43f5e');
    expect(chartAnnotationTheme.sweep).toBe('#eab308');
    expect(chartAnnotationTheme.choch).toBe('#a855f7');
    expect(chartAnnotationTheme.liquidityCyan).toBe('#06b6d4');
  });
});
