import { describe, expect, it } from 'vitest';
import { newShot, type Project } from './model';
import { clearContinuationLink, continuationReviewDefaults, createLinkedContinuation, normalizeContinuationSource, parseContinuationLink, validateContinuationAvailability } from './continuationLink';

const ID = '1f002533-c231-4538-b7dd-29cf47173d31';
const FILE = 'mmh3/my-film/result_00001.mmh3';
const query = (path = FILE, seed = '101') => `?project=${ID}&continue_mmh3=${encodeURIComponent(path)}&continue_seed=${encodeURIComponent(seed)}`;
function source(): Project {
  return {
    schema_version: 1, id: ID, title: 'Original clip', mode: 'ref2va', duration: 5,
    aspect_ratio: '16:9', profile: 'concise', authoring_mode: 'full', story: { text: 'She picks up the box.', locked: true },
    style: { vibe: 'warm' }, assets: [{ id: 'face', name: 'Face', media_type: 'image', role: 'reference_image', semantic_role: 'face', enabled: true, locked_order: false, description: '', observation: '', approved_observation: '', prompt_tag: 'mira' }],
    subjects: [{ id: 'person', name: 'Mira', asset_ids: ['face'], description: '' }],
    shots: [{ ...newShot(5), final_state: 'She holds the box by the door.', dialogue: [{ speaker_id: 'person', text: 'Old exact words.' }] }],
    soundscape: 'quiet room', music: '', custom_instructions: 'Original clip only.',
    simple_generation: { prompt: 'Old output', seconds: 4 },
    simple: { next_clip_draft: { request: 'Walk toward the window.', ending: 'The box is in her right hand.', duration: 7 } },
    comfy_render: { resolution: '0.3', steps: 8, seed: 100, continuation_source: 'mmh3/older.mmh3',
      loras: [{ name: 'Custom character.safetensors', strength: 0.35, enabled: true }, { name: 'Muted.safetensors', strength: 0.8, enabled: false }] },
  };
}

describe('Comfy saved-result continuation link', () => {
  it('parses only an explicit action and leaves ordinary project links alone', () => {
    expect(parseContinuationLink('?project=' + ID)).toBeNull();
    expect(parseContinuationLink(query())).toEqual({ projectId: ID, source: FILE, seed: 101 });
    expect(parseContinuationLink(`?project=${ID}&continue_mmh3=${encodeURIComponent(FILE)}`)).toEqual({ projectId: ID, source: FILE });
    expect(normalizeContinuationSource('mmh3\\my-film\\result.mmh3')).toBe('mmh3/my-film/result.mmh3');
  });
  it.each(['', '/root.mmh3', '../result.mmh3', 'mmh3/../result.mmh3', 'mmh3//result.mmh3', 'mmh3/./result.mmh3', 'C:\\file.mmh3', '\\\\server\\result.mmh3', 'output::mmh3/result.mmh3', 'https://host/result.mmh3', 'mmh3/result.mp4', 'mmh3/bad\x00.mmh3', 'mmh3/bad\n.mmh3', 'mmh3/bad?.mmh3', ' result.mmh3'])('rejects unsafe or wrong-format source %j', path => {
    expect(() => parseContinuationLink(query(path))).toThrow();
  });
  it.each(['-1', '', '+1', '1.5', 'NaN', 'Infinity', '9007199254740992', '1e3'])('rejects malformed or unsafe seed %j', seed => {
    expect(() => parseContinuationLink(query(FILE, seed))).toThrow('seed');
  });
  it('accepts the supported integer limits and rejects ambiguous repeated parameters', () => {
    expect(parseContinuationLink(query(FILE, '0'))?.seed).toBe(0);
    expect(parseContinuationLink(query(FILE, String(Number.MAX_SAFE_INTEGER)))?.seed).toBe(Number.MAX_SAFE_INTEGER);
    expect(() => parseContinuationLink(query() + '&project=' + ID)).toThrow('repeats');
    expect(() => parseContinuationLink('?continue_mmh3=' + FILE)).toThrow('project');
    expect(() => parseContinuationLink('?project=wrong&continue_mmh3=' + FILE)).toThrow('project');
    expect(() => parseContinuationLink('?project=' + ID + '&continue_seed=4')).toThrow('saved-file');
  });
  it('reads remembered next-clip notes without replacing them with the old story', () => {
    const original = source();
    expect(continuationReviewDefaults(original)).toEqual({ request: 'Walk toward the window.', ending: 'The box is in her right hand.', duration: 7 });
    original.simple = {};
    expect(continuationReviewDefaults(original)).toEqual({ request: '', ending: 'She holds the box by the door.', duration: 15 });
    original.simple.next_clip_draft = { duration: 100, request: 5, ending: null };
    expect(continuationReviewDefaults(original).duration).toBe(15);
  });
  it('requires the exact available file and project; never substitutes another clip', () => {
    const link = parseContinuationLink(query())!;
    const catalog = { mmh3_continuation_available: true, mmh3_sources: [{ value: FILE }] };
    expect(() => validateContinuationAvailability(link, source(), catalog)).not.toThrow();
    expect(() => validateContinuationAvailability(link, { ...source(), id: 'other' }, catalog)).toThrow('original');
    expect(() => validateContinuationAvailability(link, source(), { ...catalog, mmh3_sources: [{ value: 'mmh3/other.mmh3' }] })).toThrow('exact saved clip');
    expect(() => validateContinuationAvailability(link, source(), { ...catalog, mmh3_continuation_available: false })).toThrow('unavailable');
  });
  it('creates a distinct reviewed project with exact source, seed, references and live Studio settings', () => {
    const original = source(), before = structuredClone(original);
    const next = createLinkedContinuation(original, parseContinuationLink(query())!, { request: 'Set the box down.', ending: 'She holds the box.', duration: 10 });
    expect(original).toEqual(before);
    expect(next.id).not.toBe(original.id);
    expect(next.story.text).toBe('Set the box down.');
    expect(next.duration).toBe(10);
    expect(next.assets[0].prompt_tag).toBe('mira');
    expect(next.subjects).toEqual(original.subjects);
    expect(next.comfy_render).toEqual({ ...original.comfy_render, seed: 101, continuation_source: FILE, continuation_overlap_frames: 39, save_mmh3: true });
    expect(next.shots.flatMap(shot => shot.dialogue)).toEqual([]);
    expect(next.simple_generation).toBeUndefined();
    expect(next.simple.next_clip_draft).toBeUndefined();
    expect(next.simple.continuation).toMatchObject({ previous_project_id: ID, previous_ending: 'She holds the box.', continuity_basis: 'saved_joint_av_latent', saved_source: FILE });
  });
  it('keeps the saved seed when none was linked and validates programmatic inputs too', () => {
    const link = { projectId: ID, source: FILE };
    expect(createLinkedContinuation(source(), link, { request: '', ending: '', duration: 15 }).comfy_render.seed).toBe(100);
    expect(() => createLinkedContinuation(source(), { ...link, projectId: 'wrong' }, { request: '', ending: '', duration: 15 })).toThrow('source project');
    expect(() => createLinkedContinuation(source(), { ...link, seed: -1 }, { request: '', ending: '', duration: 15 })).toThrow('seed');
    expect(() => createLinkedContinuation(source(), link, { request: '', ending: '', duration: 100 })).toThrow('length');
  });
  it('consumes the action without deleting other URL settings or the hash', () => {
    expect(clearContinuationLink('http://127.0.0.1:8766/' + query() + '&theme=dark#photos')).toBe('/?theme=dark#photos');
  });
});
