import type { Asset, Project } from './model';
import { setKeyframe, setSimpleMode } from './simple';
import { ensurePromptTags } from './tags';

export const MAGE_FLOW = 'mage_flow_edit_turbo_int8_convrot.safetensors';
export type KeyframeRole = 'first_frame' | 'last_frame' | 'reference_image';
export type KeyframeDraft = { prompt: string; name: string; model: string; width: number; height: number; reference_asset_ids: string[] };
export const defaultKeyframeDraft = (): KeyframeDraft => ({ prompt: '', name: 'Generated keyframe', model: 'z_image_turbo_bf16.safetensors', width: 576, height: 1024, reference_asset_ids: [] });
export function keyframeSpec(draft: KeyframeDraft, project: Project, seed: number) {
  if (!draft.prompt.trim() || draft.prompt.length > 6000) throw new Error('Describe the keyframe in 1–6000 characters.');
  if (!draft.name.trim() || draft.name.length > 100) throw new Error('Use an image name of 1–100 characters.');
  const mage = draft.model === MAGE_FLOW;
  for (const dimension of [draft.width, draft.height]) {
    if (!Number.isInteger(dimension) || dimension % 16 || dimension < (mage ? 512 : 128) || dimension > (mage ? 2048 : 1024)) {
      throw new Error(`Use image dimensions in multiples of 16 from ${mage ? '512 to 2048' : '128 to 1024'}.`);
    }
  }
  const refs = [...new Set(draft.reference_asset_ids)];
  if (mage && (!refs.length || refs.length > 4 || refs.some(id => !project.assets.some(asset => asset.id === id && asset.media_type === 'image')))) {
    throw new Error('MageFlow needs 1–4 existing project images. Select the references to preserve.');
  }
  return { prompt: draft.prompt.trim(), name: draft.name.trim(), model: draft.model, width: draft.width, height: draft.height,
    seed, semantic_role: 'other', prompt_tag: 'generated-keyframe', ...(mage ? { reference_asset_ids: refs } : {}) };
}
export function addGeneratedKeyframe(project: Project, generated: Asset, role: KeyframeRole) {
  if (generated.media_type !== 'image') throw new Error('This result is not an image.');
  if (!project.assets.some(asset => asset.id === generated.id)) {
    const incoming: Asset = { ...generated, enabled: true, locked_order: false, role: 'reference_image' };
    if (project.assets.some(asset => asset.prompt_tag === incoming.prompt_tag)) delete incoming.prompt_tag;
    project.assets.push(incoming);
  }
  const image = project.assets.find(asset => asset.id === generated.id)!; image.enabled = true;
  if (role === 'reference_image') { image.role = role; setSimpleMode(project, 'ref2va'); }
  else {
    const opposite = role === 'first_frame' ? 'last_frame' : 'first_frame';
    if (project.assets.some(asset => asset.id !== generated.id && asset.enabled && asset.role === opposite)) setSimpleMode(project, 'fl2va');
    setKeyframe(project, image.id, role);
  }
  ensurePromptTags(project);
}
export function keyframePending(status?: string) {
  return !!status && !['succeeded', 'failed', 'cancelled'].includes(status);
}
