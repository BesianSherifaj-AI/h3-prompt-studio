import { describe, expect, it } from 'vitest';
import { switchMode, newShot } from './model';
import type { Asset, Project } from './model';

const asset = (id: string, role = 'reference_image', enabled = true, media_type = 'image'): Asset => ({
  id, name: id, media_type, role, semantic_role: 'other', enabled, locked_order: false,
  description: '', observation: '', approved_observation: '',
});
const project = (assets: Asset[]): Project => ({
  schema_version: 1, id: 'project', title: 'Test', mode: 'ref2va', duration: 5, aspect_ratio: '16:9',
  profile: 'official', authoring_mode: 'manual', story: { text: 'Exact story', locked: true },
  style: {}, assets, subjects: [{ id: 'subject', name: 'Subject', asset_ids: ['a', 'b'], description: '' }],
  shots: [newShot(5)], soundscape: '', music: '', custom_instructions: '',
});

describe('mode changes preserve deliberate reference choices', () => {
  it('keeps the matching recipe LoRA in Advanced mode without resetting the custom stack', () => {
    const p = project([asset('start'), asset('end')]);
    const ref = 'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors';
    const frames = 'minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors';
    p.comfy_render = { steps: 16, loras: [
      { name: 'My custom adapter.safetensors', strength: -0.2, enabled: false },
      { name: ref, strength: 0.9, enabled: true },
    ] };
    const before = structuredClone(p);
    const next = switchMode(p, 'i2va');
    expect(p).toEqual(before);
    expect(next.comfy_render).toEqual({ steps: 16, loras: [
      before.comfy_render.loras[0], { ...before.comfy_render.loras[1], name: frames },
    ] });
    expect(switchMode(next, 'i2va').comfy_render).toEqual(next.comfy_render);
    expect(switchMode(next, 'fl2va').comfy_render).toEqual(next.comfy_render);
    expect(switchMode(next, 'ref2va').comfy_render).toEqual(before.comfy_render);
  });

  it.each(['ref2va', 'fl2va', 'i2va', 'l2va', 't2va'])('does not promote context guides or disabled images in %s', mode => {
    const p = project([asset('guide', 'context', true), asset('off', 'reference_image', false), asset('a'), asset('b')]);
    const original = structuredClone(p), next = switchMode(p, mode);
    expect(p).toEqual(original);
    expect(next.assets[0]).toEqual(original.assets[0]); expect(next.assets[1]).toEqual(original.assets[1]);
    expect(next.subjects).toEqual(original.subjects); expect(next.shots).toEqual(original.shots);
    expect(next.story).toEqual(original.story);
  });
  it('assigns currently enabled reference images to first/last in their order', () => {
    const next = switchMode(project([asset('guide', 'context'), asset('off', 'reference_image', false), asset('a'), asset('b'), asset('c')]), 'fl2va');
    expect(next.assets.filter(a => a.enabled && a.role !== 'context').map(a => [a.id, a.role])).toEqual([['a', 'first_frame'], ['b', 'last_frame']]);
    expect(next.assets.find(a => a.id === 'c')?.enabled).toBe(false);
  });
  it('preserves established endpoint roles, including a last-only project entering FL2VA', () => {
    const p = project([asset('last', 'last_frame'), asset('first', 'first_frame')]);
    expect(switchMode(p, 'l2va').assets.filter(a => a.enabled).map(a => a.id)).toEqual(['last']);
    expect(switchMode(p, 'i2va').assets.filter(a => a.enabled).map(a => a.id)).toEqual(['first']);
    const lastOnly = switchMode(project([asset('guide', 'context'), asset('last', 'last_frame')]), 'fl2va');
    expect(lastOnly.assets.filter(a => a.enabled && a.role !== 'context').map(a => [a.id, a.role])).toEqual([['last', 'last_frame']]);
  });
  it('requires deliberate selection after T2VA instead of re-enabling stored references', () => {
    const t2va = switchMode(project([asset('a'), asset('guide', 'context')]), 't2va');
    const ref = switchMode(t2va, 'ref2va');
    expect(ref.assets.filter(a => a.enabled && a.role !== 'context')).toEqual([]);
    expect(ref.assets.find(a => a.id === 'guide')?.enabled).toBe(true);
  });
  it('converts active keyframes to references and disables non-images in keyframe modes', () => {
    const p = project([asset('first', 'first_frame'), asset('voice', 'reference_audio', true, 'audio')]);
    const ref = switchMode(p, 'ref2va');
    expect(ref.assets.map(a => a.role)).toEqual(['reference_image', 'reference_audio']);
    const keyframes = switchMode(ref, 'fl2va');
    expect(keyframes.assets[0].role).toBe('first_frame'); expect(keyframes.assets[1].enabled).toBe(false);
  });
  it('leaves missing inputs missing when only context or disabled assets are present', () => {
    for (const mode of ['ref2va', 'fl2va', 'i2va', 'l2va']) {
      const next = switchMode(project([asset('guide', 'context'), asset('off', 'reference_image', false)]), mode);
      expect(next.assets.filter(a => a.enabled && a.role !== 'context')).toEqual([]);
    }
  });
});
