import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import MotionTools, { arcControl, buildMovementInstructions, clampPoint, defaultPlan, dragFloorPoint, movementPath, projectPoint, unprojectPoint, validDuration } from './MotionTools';
import { creativeResources } from './resources';

describe('movement floor geometry', () => {
  it.each(['top', 'iso'] as const)('keeps the same scene positions through the %s projection', view => {
    for (const point of [{ x: 0, z: 0 }, { x: 10, z: 10 }, { x: 2.3, z: 7.6 }, { x: 10, z: 0 }, { x: 0, z: 10 }]) {
      const actual = unprojectPoint(projectPoint(point, view), view);
      expect(actual.x).toBeCloseTo(point.x, 10); expect(actual.z).toBeCloseTo(point.z, 10);
    }
  });
  it('does not jump when grabbing the elevated isometric subject marker', () => {
    const start = { x: 2, z: 6 }, center = projectPoint(start, 'iso'), offset = { x: 3, y: -37 };
    expect(dragFloorPoint({ x: center.x + 3, y: center.y - 37 }, offset, 'iso')).toEqual(start);
    const destination = projectPoint({ x: 6, z: 8 }, 'iso');
    expect(dragFloorPoint({ x: destination.x + 3, y: destination.y - 37 }, offset, 'iso')).toEqual({ x: 6, z: 8 });
  });
  it('clamps a drag outside the floor and preserves tenths for keyboard movement', () => {
    expect(clampPoint({ x: -2, z: 13 })).toEqual({ x: 0, z: 10 });
    expect(clampPoint({ x: 2.299999999, z: 7.61 })).toEqual({ x: 2.3, z: 7.6 });
    expect(clampPoint({ x: NaN, z: Infinity })).toEqual({ x: 0, z: 0 });
    expect(dragFloorPoint(projectPoint({ x: -5, z: 16 }, 'top'), { x: 0, y: 0 }, 'top')).toEqual({ x: 0, z: 10 });
  });
  it('keeps curved paths inside the floor, with identical start and end points in either view', () => {
    const a = { x: 0, z: 10 }, b = { x: 10, z: 10 }, c = arcControl(a, b);
    expect(c.x).toBeGreaterThanOrEqual(0); expect(c.x).toBeLessThanOrEqual(10);
    expect(c.z).toBeGreaterThanOrEqual(0); expect(c.z).toBeLessThanOrEqual(10);
    for (const view of ['top', 'iso'] as const) {
      const start = projectPoint(a, view), end = projectPoint(b, view);
      expect(movementPath(a, b, 'straight', view)).toBe(`M ${start.x} ${start.y} L ${end.x} ${end.y}`);
      const arc = movementPath(a, b, 'arc', view);
      expect(arc.startsWith(`M ${start.x} ${start.y} Q `)).toBe(true);
      expect(arc.endsWith(`${end.x} ${end.y}`)).toBe(true);
    }
  });
});

describe('mechanical movement instructions', () => {
  it('preserves the chosen subject, independent camera move and duration without promising keyframe conditioning', () => {
    const plan = defaultPlan(7), text = buildMovementInstructions(plan, 'Mira & Leo');
    expect(text).toContain('one 7-second continuous shot');
    expect(text).toContain('Mira & Leo starts at (X 2, Z 6)');
    expect(text).toContain('The camera starts at (X 3, Z 1)');
    expect(text).toContain('ends at (X 6, Z 2)');
    expect(text).toContain('not screen directions or measured metres');
    expect(text).not.toMatch(/guarantee|keyframe|simulat|<Picture|<Subject/);
    expect(plan).toEqual(defaultPlan(7));
  });
  it('describes a static camera and curved subject separately', () => {
    const plan = defaultPlan(5); plan.path = 'arc'; plan.cameraEnd = { ...plan.cameraStart };
    const text = buildMovementInstructions(plan, '');
    expect(text).toContain('The subject starts'); expect(text).toContain('curved path bending toward');
    expect(text).toContain('The camera stays at (X 3, Z 1) throughout.');
  });
  it('makes quick pace a move-and-hold timing instruction, not incompatible speed/distance promises', () => {
    const plan = defaultPlan(10); plan.pace = 'quick';
    expect(buildMovementInstructions(plan, 'Subject')).toContain('Complete the movement by 6.5 seconds, then hold');
    plan.pace = 'steady'; expect(buildMovementInstructions(plan, 'Subject')).toContain('steady pace over the full shot duration');
  });
  it('does not ask stationary subjects and cameras to travel', () => {
    const plan = defaultPlan(8); plan.subjectEnd = { ...plan.subjectStart }; plan.cameraEnd = { ...plan.cameraStart };
    expect(buildMovementInstructions(plan, 'Subject')).toContain('Hold these subject and camera positions');
    expect(buildMovementInstructions(plan, 'Subject')).not.toContain('Travel at');
  });
  it('handles invalid initial durations safely and retains fractional shot durations', () => {
    expect(validDuration(NaN)).toBe(5); expect(validDuration(Infinity)).toBe(5);
    expect(validDuration(0)).toBe(5); expect(validDuration(-2)).toBe(5); expect(validDuration(25)).toBe(15); expect(validDuration(7.5)).toBe(7.5);
    expect(buildMovementInstructions(defaultPlan(7.5), 'Subject')).toContain('7.5-second');
  });
  it('uses a short selected shot duration instead of imposing the whole-video minimum', () => {
    for (const seconds of [.001, .5, 2, 2.875]) {
      expect(validDuration(seconds)).toBe(seconds);
      const plan = defaultPlan(seconds); plan.pace = 'quick';
      const text = buildMovementInstructions(plan, 'Subject');
      expect(text).toContain(`one ${seconds}-second continuous shot`);
      expect(text).not.toContain('by 0 seconds');
    }
  });
});

describe('creative tools surfaces', () => {
  it('renders the sketch controls without server, GPU or image dependencies', () => {
    const html = renderToStaticMarkup(<MotionTools duration={5} subjects={[]} onInstructions={() => {}} onReference={() => {}} />);
    for (const label of ['Sketch reference', 'Movement layout', 'Free resources', 'Arrow', 'Rectangle', 'Undo', 'Clear', 'Export PNG', 'Add PNG to references']) expect(html).toContain(label);
    expect(html).toContain('width="736" height="416"');
    expect(html).toContain('aria-label="Drawing color"');
    expect(html).not.toMatch(/<iframe|<img|<video|<script/);
  });
  it('lists five primary resources with explicit asset/software license distinctions', () => {
    expect(creativeResources).toHaveLength(5);
    expect(creativeResources.map(r => r.name)).toEqual(['Poly Haven', 'Kenney', 'ambientCG', 'Blender', 'Krita']);
    for (const resource of creativeResources) {
      expect(new URL(resource.url).protocol).toBe('https:'); expect(new URL(resource.licenseUrl).protocol).toBe('https:');
      expect(resource.note.length).toBeGreaterThan(30);
      if (resource.kind === 'Assets') expect(resource.license).toContain('CC0');
      else expect(resource.license).toContain('GPL');
    }
  });
});
