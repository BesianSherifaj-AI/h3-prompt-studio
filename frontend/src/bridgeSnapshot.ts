/** Only the graph-bound fields matter when returning text to an unchanged Comfy node. */
export type BridgeSnapshotProject = {
  mode: string;
  duration: number;
  assets: { id: string; role: string; enabled?: boolean; [key: string]: unknown }[];
};
export type BridgeSnapshot = {
  version: 1;
  mode: string;
  targetDuration: number;
  conditioning: { id: string; role: string }[];
};
const MODES = new Set(['ref2va', 'fl2va', 'i2va', 'l2va', 't2va']);
const ROLES = new Set(['reference_image', 'reference_video', 'reference_audio', 'first_frame', 'last_frame']);

export function makeBridgeSnapshot(project: BridgeSnapshotProject): BridgeSnapshot {
  if (!project || !MODES.has(project.mode)) throw new Error('The imported Comfy mode is invalid.');
  if (typeof project.duration !== 'number' || !Number.isFinite(project.duration) || project.duration <= 0 || Math.round(project.duration) < 1) throw new Error('The imported Comfy target duration is invalid.');
  if (!Array.isArray(project.assets)) throw new Error('The imported Comfy reference list is invalid.');
  const ids = new Set<string>();
  const conditioning: BridgeSnapshot['conditioning'] = [];
  for (const asset of project.assets) {
    if (!asset || asset.enabled === false || asset.role === 'context') continue;
    if (!ROLES.has(asset.role) || typeof asset.id !== 'string' || !asset.id || ids.has(asset.id)) throw new Error('Active Comfy reference IDs and roles must be valid and unique.');
    ids.add(asset.id);
    conditioning.push({ id: asset.id, role: asset.role });
  }
  return { version: 1, mode: project.mode, targetDuration: Math.round(project.duration), conditioning };
}

export function matchesBridgeSnapshot(project: BridgeSnapshotProject, snapshot: BridgeSnapshot | null | undefined): boolean {
  if (!snapshot || snapshot.version !== 1 || !Array.isArray(snapshot.conditioning)) return false;
  try {
    const current = makeBridgeSnapshot(project);
    return current.mode === snapshot.mode && current.targetDuration === snapshot.targetDuration &&
      current.conditioning.length === snapshot.conditioning.length &&
      current.conditioning.every((asset, index) => asset.id === snapshot.conditioning[index].id && asset.role === snapshot.conditioning[index].role);
  } catch { return false; }
}
