import type { Project } from './model';
import { continuationEnding, createContinuation } from './timelineHelpers';

export type ContinuationLink = { projectId: string; source: string; seed?: number };
export type ContinuationReviewValues = { request: string; ending: string; duration: number };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function normalizeContinuationSource(value: string): string {
  const path = value.replace(/\\/g, '/');
  if (!path || path !== path.trim() || path.length > 1024 ||
      /[\x00-\x1f\x7f:<>"|?*]/.test(path) ||
      path.split('/').some(part => !part || part === '.' || part === '..') ||
      !path.toLowerCase().endsWith('.mmh3'))
    throw new Error('This continuation link has an invalid saved-file path. Open it again from the saved result in ComfyUI.');
  return path;
}

export function parseContinuationLink(search: string): ContinuationLink | null {
  const params = new URLSearchParams(search);
  if (!params.has('continue_mmh3') && !params.has('continue_seed')) return null;
  for (const key of ['project', 'continue_mmh3', 'continue_seed']) {
    if (params.getAll(key).length > 1) throw new Error('This continuation link repeats a setting. Open it again from ComfyUI.');
  }
  const projectId = params.get('project') || '';
  if (!UUID.test(projectId)) throw new Error('This continuation link is missing a valid source project. Open it again from the original Studio workflow.');
  const source = normalizeContinuationSource(params.get('continue_mmh3') || '');
  const value = params.get('continue_seed');
  let seed: number | undefined;
  if (value !== null) {
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))
      throw new Error('This continuation link has an invalid seed. Open it again from ComfyUI.');
    seed = Number(value);
  }
  return { projectId, source, ...(seed === undefined ? {} : { seed }) };
}

export function continuationReviewDefaults(source: Project): ContinuationReviewValues {
  const draft = source.simple?.next_clip_draft;
  return {
    request: typeof draft?.request === 'string' ? draft.request : '',
    ending: typeof draft?.ending === 'string' ? draft.ending : continuationEnding(source),
    duration: Number.isInteger(draft?.duration) && draft.duration >= 4 && draft.duration <= 15 ? draft.duration : 15,
  };
}

export function validateContinuationAvailability(link: ContinuationLink, source: Project, catalog: any): void {
  if (!source || source.id !== link.projectId || !Array.isArray(source.assets) || !Array.isArray(source.shots))
    throw new Error('The original Studio project is unavailable. Reopen this result from its original project; no new video has been created.');
  if (catalog?.error || !catalog?.mmh3_continuation_available)
    throw new Error('ComfyUI saved-state continuation is unavailable. Open the H3 Desktop installation, then choose Check again.');
  if (!Array.isArray(catalog.mmh3_sources) || !catalog.mmh3_sources.some((item: any) => item?.value === link.source))
    throw new Error('This exact saved clip is unavailable in ComfyUI. Restore its .mmh3 file or reopen an available saved result, then choose Check again.');
}

export function createLinkedContinuation(source: Project, link: ContinuationLink, values: ContinuationReviewValues): Project {
  if (source.id !== link.projectId) throw new Error('The continuation source project changed. Open the original saved result again.');
  const path = normalizeContinuationSource(link.source);
  if (link.seed !== undefined && (!Number.isSafeInteger(link.seed) || link.seed < 0)) throw new Error('The continuation seed is invalid.');
  const next = createContinuation(source, values);
  next.comfy_render = {
    ...next.comfy_render, continuation_source: path, continuation_overlap_frames: 39, save_mmh3: true,
    ...(link.seed === undefined ? {} : { seed: link.seed }),
  };
  next.simple.continuation.continuity_basis = 'saved_joint_av_latent';
  next.simple.continuation.saved_source = path;
  delete next.simple_generation;
  return next;
}

/** Consume only this action's parameters; preserve other app links and hashes. */
export function clearContinuationLink(href: string): string {
  const url = new URL(href);
  for (const key of ['project', 'continue_mmh3', 'continue_seed']) url.searchParams.delete(key);
  return url.pathname + url.search + url.hash;
}
