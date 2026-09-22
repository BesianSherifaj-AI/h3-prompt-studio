import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { addGeneratedKeyframe, defaultKeyframeDraft, keyframePending, keyframeSpec, MAGE_FLOW } from './keyframeGeneration';
import KeyframeGenerator from './KeyframeGenerator';
import { newShot, type Asset, type Project } from './model';
const asset = (id: string, role = 'reference_image'): Asset => ({ id, name: id, media_type: 'image', role, semantic_role: 'other', enabled: true, locked_order: false, description: '', observation: '', approved_observation: '' });
function project(): Project { return { schema_version: 1, id: 'fixture', title: 'Test', mode: 'ref2va', duration: 8, aspect_ratio: '9:16', profile: 'director', authoring_mode: 'manual', story: { text: '', locked: false }, assets: [asset('first')], subjects: [], shots: [newShot(8)], style: {}, music: '', soundscape: '', custom_instructions: '' }; }
describe('Keyframe generation contracts', () => {
  it('requires a real project reference for MageFlow and supports its larger canvas', () => {
    const draft = { ...defaultKeyframeDraft(), prompt: 'Move the hand.', model: MAGE_FLOW, width: 1024, height: 1792, reference_asset_ids: ['first'] };
    expect(keyframeSpec(draft, project(), 3)).toMatchObject({ model: MAGE_FLOW, reference_asset_ids: ['first'], width: 1024, height: 1792, seed: 3 });
    for (const refs of [[], ['missing'], ['a', 'b', 'c', 'd', 'e']]) expect(() => keyframeSpec({ ...draft, reference_asset_ids: refs }, project(), 3)).toThrow('1–4');
  });
  it('keeps model switches explicit and sends references only to the edit model', () => {
    const draft = { ...defaultKeyframeDraft(), prompt: 'A character.', reference_asset_ids: ['first'] };
    expect(keyframeSpec(draft, project(), 3)).not.toHaveProperty('reference_asset_ids');
    expect(draft.reference_asset_ids).toEqual(['first']);
    expect(() => keyframeSpec({ ...draft, width: 1792 }, project(), 3)).toThrow('128 to 1024');
  });
  it('rejects empty prompts and unsupported dimensions before submission', () => {
    expect(() => keyframeSpec(defaultKeyframeDraft(), project(), 1)).toThrow('Describe');
    expect(() => keyframeSpec({ ...defaultKeyframeDraft(), prompt: 'Frame', height: 1001 }, project(), 1)).toThrow('multiples');
  });
  it('adds a last frame while preserving the existing first endpoint', () => {
    const p = project(); p.mode = 'i2va'; p.assets[0].role = 'first_frame';
    addGeneratedKeyframe(p, asset('ending'), 'last_frame');
    expect(p.mode).toBe('fl2va'); expect(p.assets.find(a => a.id === 'first')?.role).toBe('first_frame');
    expect(p.assets.find(a => a.id === 'ending')?.role).toBe('last_frame');
    addGeneratedKeyframe(p, asset('ending'), 'last_frame'); expect(p.assets).toHaveLength(2);
  });
  it('keeps existing images when changing to references and allocates unique tags', () => {
    const p = project(); p.mode = 'i2va'; p.assets[0].role = 'first_frame'; p.assets[0].prompt_tag = 'generated-keyframe';
    addGeneratedKeyframe(p, { ...asset('new'), prompt_tag: 'generated-keyframe' }, 'reference_image');
    expect(p.mode).toBe('ref2va'); expect(p.assets).toHaveLength(2);
    expect(p.assets.every(a => a.role === 'reference_image')).toBe(true);
    expect(new Set(p.assets.map(a => a.prompt_tag)).size).toBe(2);
  });
  it('protects uncertain and paused requests from new generation', () => {
    for (const status of ['unknown', 'uncertain', 'running', 'paused', 'queued']) expect(keyframePending(status)).toBe(true);
    for (const status of ['succeeded', 'failed', 'cancelled']) expect(keyframePending(status)).toBe(false);
  });
  it('renders an accessible collapsed workflow without automatically generating', () => {
    const html = renderToStaticMarkup(<KeyframeGenerator project={project()} update={() => {}}/>);
    expect(html).toContain('Create or edit a keyframe'); expect(html).toContain('Keyframe prompt');
    expect(html).toContain('Keyframe model'); expect(html).toContain('disabled="">Generate keyframe');
  });
});
