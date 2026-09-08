import { describe, expect, it } from 'vitest';
import { makeBridgeSnapshot, matchesBridgeSnapshot, type BridgeSnapshotProject } from './bridgeSnapshot';

const project = (): BridgeSnapshotProject => ({
  mode: 'ref2va', duration: 124 / 24,
  assets: [
    { id: 'subject', role: 'reference_image', enabled: true, description: 'Person' },
    { id: 'unused', role: 'reference_image', enabled: false },
    { id: 'room', role: 'reference_image', enabled: true },
    { id: 'inspiration', role: 'context', enabled: true },
  ],
});

describe('Comfy return binding snapshot', () => {
  it('records actual conditioned IDs and roles in order, with nominal rounded duration', () => {
    expect(makeBridgeSnapshot(project())).toEqual({ version: 1, mode: 'ref2va', targetDuration: 5, conditioning: [{ id: 'subject', role: 'reference_image' }, { id: 'room', role: 'reference_image' }] });
  });
  it('allows unlimited context/disabled assets and descriptive editing without changing bindings', () => {
    const p = project(), snapshot = makeBridgeSnapshot(p);
    p.assets[0].description = 'Edited action, face observation and styling';
    p.assets.unshift(...Array.from({ length: 40 }, (_, i) => ({ id: `library-${i}`, role: i % 2 ? 'context' : 'reference_image', enabled: i % 2 === 1 })));
    expect(matchesBridgeSnapshot(p, snapshot)).toBe(true);
    p.assets = p.assets.filter(asset => asset.id !== 'unused' && asset.id !== 'inspiration');
    expect(matchesBridgeSnapshot(p, snapshot)).toBe(true);
  });
  it('rejects added, removed, disabled, reordered or reassigned conditioned images', () => {
    const snapshot = makeBridgeSnapshot(project());
    const mutations = [
      (p: BridgeSnapshotProject) => p.assets.push({ id: 'new', role: 'reference_image' }),
      (p: BridgeSnapshotProject) => { p.assets = p.assets.filter(asset => asset.id !== 'room'); },
      (p: BridgeSnapshotProject) => { p.assets[0].enabled = false; },
      (p: BridgeSnapshotProject) => { p.assets.reverse(); },
      (p: BridgeSnapshotProject) => { p.assets[0].role = 'first_frame'; },
      (p: BridgeSnapshotProject) => { p.assets[0].id = 'different-file-id'; },
    ];
    for (const mutate of mutations) { const p = project(); mutate(p); expect(matchesBridgeSnapshot(p, snapshot)).toBe(false); }
  });
  it('rejects different modes and rounded target durations', () => {
    const p = project(), snapshot = makeBridgeSnapshot(p);
    p.duration = 5; expect(matchesBridgeSnapshot(p, snapshot)).toBe(true);
    p.duration = 7; expect(matchesBridgeSnapshot(p, snapshot)).toBe(false);
    p.duration = 5; p.mode = 'i2va'; expect(matchesBridgeSnapshot(p, snapshot)).toBe(false);
  });
  it('rejects invalid or duplicate active identities and absent snapshots', () => {
    const p = project(); p.assets.push({ id: 'subject', role: 'reference_image' });
    expect(() => makeBridgeSnapshot(p)).toThrow(/unique/);
    expect(matchesBridgeSnapshot(p, makeBridgeSnapshot(project()))).toBe(false);
    expect(matchesBridgeSnapshot(project(), null)).toBe(false);
    const bad = project(); bad.duration = Number.NaN; expect(() => makeBridgeSnapshot(bad)).toThrow(/duration/);
  });
  it('keeps a detached snapshot that later project mutations cannot rewrite', () => {
    const p = project(), snapshot = makeBridgeSnapshot(p); p.assets[0].id = 'replaced';
    expect(snapshot.conditioning[0].id).toBe('subject');
    expect(matchesBridgeSnapshot(p, snapshot)).toBe(false);
  });
});
