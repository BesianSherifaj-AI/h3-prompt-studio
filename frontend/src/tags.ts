import type { Asset, Project } from './model';

const TAG = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
export const validTag = (s: string) => s.length <= 64 && TAG.test(s);

export function ensurePromptTags(p: Project): void {
  const used = new Set(p.assets.map(a => a.prompt_tag).filter(t => typeof t === 'string' && validTag(t)));
  for (const [i, a] of p.assets.entries()) {
    if (typeof a.prompt_tag === 'string' && validTag(a.prompt_tag)) continue;
    let base = a.name.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48).replace(/-$/g, '');
    if (!/^[a-z]/.test(base)) base = `photo-${i + 1}`;
    let tag = base, n = 2;
    while (used.has(tag)) tag = `${base}-${n++}`;
    a.prompt_tag = tag; used.add(tag);
  }
}

/** Change authored references, never the literal words a person says. */
export function renamePromptTag(p: Project, id: string, value: string): void {
  const tag = value.trim().replace(/^@/, '').toLowerCase();
  if (!validTag(tag)) throw new Error('Use a short tag such as mira-face or green-dress: letters, numbers and single hyphens.');
  if (p.assets.some(a => a.id !== id && a.prompt_tag === tag)) throw new Error('That tag is already used by another photo. Choose a different name.');
  const asset = p.assets.find(a => a.id === id);
  if (!asset) throw new Error('This photo is no longer in the project.');
  const old = asset.prompt_tag;
  asset.prompt_tag = tag;
  if (!old || old === tag || !validTag(old)) return;
  const pattern = new RegExp(`(?<![\\w@/.:+\\\\-])@${old}(?![\\w@-])`, 'gi');
  const change = (v: string) => v.replace(pattern, '@' + tag);
  const strings = (obj: Record<string, any>) => Object.keys(obj).forEach(k => { if (typeof obj[k] === 'string') obj[k] = change(obj[k]); });
  p.story.text = change(p.story.text); strings(p.style);
  for (const key of ['soundscape','music','custom_instructions','assistant_instructions']) if (typeof p[key] === 'string') p[key] = change(p[key]);
  for (const a of p.assets) for (const key of ['description','approved_observation','observation']) if (typeof a[key] === 'string') a[key] = change(a[key]);
  for (const s of p.subjects) s.description = change(s.description);
  for (const s of p.shots) {
    for (const key of ['action','setting','performance','final_state','sound']) (s as any)[key] = change((s as any)[key] || '');
    strings(s.camera);
    if (s.scene_contract) {
      for (const key of ['environment', 'background_activity'] as const)
        if (typeof s.scene_contract[key] === 'string') s.scene_contract[key] = change(s.scene_contract[key]);
      for (const actor of s.scene_contract.actors || [])
        for (const key of ['start', 'action', 'end'] as const) actor[key] = change(actor[key]);
      for (const object of s.scene_contract.objects || [])
        for (const key of ['name', 'description', 'start', 'end'] as const) object[key] = change(object[key]);
    }
  }
  if (p.simple?.person_actions) strings(p.simple.person_actions);
}

export function replacePhoto(p: Project, oldId: string, uploaded: Asset): void {
  const index = p.assets.findIndex(a => a.id === oldId);
  if (index < 0 || uploaded.media_type !== 'image') throw new Error('Choose a replacement image for an existing photo.');
  const old = p.assets[index];
  p.assets[index] = {...uploaded, name:old.name, role:old.role, semantic_role:old.semantic_role,
    enabled:old.enabled, locked_order:old.locked_order, prompt_tag:old.prompt_tag,
    description:old.description, simple_owner_id:old.simple_owner_id, observation:'',approved_observation:''};
  p.subjects.forEach(s => { s.asset_ids = s.asset_ids.map(id => id === oldId ? uploaded.id : id); });
  for (const field of ['previous_image_roles','previous_media_roles']) {
    const roles = p.simple?.[field];
    if (roles && oldId in roles) { roles[uploaded.id] = roles[oldId]; delete roles[oldId]; }
  }
}

export function movePhoto(p: Project, id: string, delta: number): void {
  const index = p.assets.findIndex(a => a.id === id), target = index + delta;
  if (index < 0 || target < 0 || target >= p.assets.length) return;
  if (p.assets[index].locked_order || p.assets[target].locked_order) throw new Error('Unlock photo order in Advanced before moving this reference.');
  [p.assets[index],p.assets[target]] = [p.assets[target],p.assets[index]];
}
