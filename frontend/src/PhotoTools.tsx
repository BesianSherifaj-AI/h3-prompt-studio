import { useEffect, useRef, useState } from 'react';
import type { Asset, Project } from './model';
import { ensurePromptTags, movePhoto, renamePromptTag } from './tags';
import './PhotoTools.css';

export default function PhotoTools({project, asset, index, update, onReplace}: {
  project:Project; asset:Asset; index:number; update:(fn:(p:Project)=>void)=>void;
  onReplace:(id:string,file:File)=>Promise<void>;
}) {
  const [tag,setTag] = useState(asset.prompt_tag || '');
  const [error,setError] = useState('');
  const file = useRef<HTMLInputElement>(null);
  useEffect(()=>{setTag(asset.prompt_tag || '');setError('');},[asset.id,asset.prompt_tag]);
  const rename = () => {
    try {
      const preview = structuredClone(project); ensurePromptTags(preview); renamePromptTag(preview,asset.id,tag);
      update(p=>{ensurePromptTags(p);renamePromptTag(p,asset.id,tag);});setError('');
    } catch(e) { setError((e as Error).message); }
  };
  const move = (delta:number) => {
    try { const preview=structuredClone(project);movePhoto(preview,asset.id,delta);update(p=>movePhoto(p,asset.id,delta));setError(''); }
    catch(e) {setError((e as Error).message);}
  };
  return <details className="photo-tools">
    <summary>{asset.prompt_tag ? `@${asset.prompt_tag}` : 'Name, tag & replace'} <span>Edit photo</span></summary>
    <label className="simple-field"><span>Photo name</span><input aria-label={`Photo ${index+1} name`} value={asset.name} onChange={e=>update(p=>{const a=p.assets.find(a=>a.id===asset.id);if(a)a.name=e.target.value;})}/></label>
    <label className="simple-field"><span>Easy reference tag</span><input aria-label={`Photo ${index+1} tag`} value={tag} placeholder="green-dress" onChange={e=>setTag(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();rename();}}}/></label>
    <button type="button" onClick={rename}>Save tag</button>
    <small>Use @{tag || 'tag'} in your idea. Renaming updates written references; exact spoken words stay unchanged.</small>
    <div className="photo-tools-actions">
      <button type="button" onClick={()=>file.current?.click()}>Replace photo</button>
      <button type="button" aria-label={`Move photo ${index+1} earlier`} disabled={project.assets[0]?.id===asset.id||asset.locked_order} onClick={()=>move(-1)}>←</button>
      <button type="button" aria-label={`Move photo ${index+1} later`} disabled={project.assets.at(-1)?.id===asset.id||asset.locked_order} onClick={()=>move(1)}>→</button>
    </div>
    <input ref={file} className="photo-replace-input" type="file" accept="image/*" aria-label={`Replace photo ${index+1} file`} onChange={async e=>{const selected=e.target.files?.[0]; e.target.value='';if(selected)await onReplace(asset.id,selected);}}/>
    <small>Replacement keeps the tag, position and person assignment. AI reads the new photo next time.</small>
    {error&&<p role="alert" className="photo-tools-error">{error}</p>}
  </details>;
}

export function ReferenceInsert({project,onInsert,label='Insert photo reference'}:{project:Project;onInsert:(text:string)=>void;label?:string}) {
  const preview=structuredClone(project);ensurePromptTags(preview);
  return <div className="reference-insert"><select aria-label={label} value="" onChange={e=>{if(e.target.value)onInsert('@'+e.target.value);}}>
    <option value="">+ Insert a photo by name</option>
    {preview.assets.filter(a=>a.enabled&&a.media_type==='image').map(a=><option key={a.id} value={a.prompt_tag}>@{a.prompt_tag} · {a.name}{a.role==='context'?' · inspiration':''}</option>)}
  </select><small>Optional: normal names and sentences work too.</small></div>;
}
