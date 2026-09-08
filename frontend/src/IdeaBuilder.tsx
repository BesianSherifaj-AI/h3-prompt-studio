import { useEffect,useState } from 'react';
import type { Project } from './model';
import { getPeople } from './simple';
import { ensurePromptTags } from './tags';

export default function IdeaBuilder({project:p,update}:{project:Project;update:(fn:(p:Project)=>void)=>void}) {
  const [who,setWho]=useState(''),[action,setAction]=useState('walks into the scene'),[target,setTarget]=useState(''),[object,setObject]=useState(''),[place,setPlace]=useState(''),[ending,setEnding]=useState('');
  useEffect(()=>{setWho('');setTarget('');setObject('');setPlace('');setEnding('');},[p.id]);
  const people=getPeople(p), preview=structuredClone(p);ensurePromptTags(preview);
  const objects=preview.assets.filter(a=>a.enabled&&a.semantic_role==='object');
  const places=preview.assets.filter(a=>a.enabled&&a.semantic_role==='background');
  const person=people.find(s=>s.id===who), other=people.find(s=>s.id===target), prop=objects.find(a=>a.id===object), room=places.find(a=>a.id===place);
  const sentence=[person?.name || (people.length?'Choose a person':'The subject'), action,
    prop?`the object from @${prop.prompt_tag}`:'',other?`with ${other.name}`:'',room?`in the place from @${room.prompt_tag}`:''].filter(Boolean).join(' ')+'.'+(ending.trim()?` End with ${ending.trim().replace(/[.!?]$/,'')}.`:'');
  return <details className="idea-builder"><summary>Build my idea with choices <span>optional</span></summary>
    <p>Choose a few things below, then add the sentence to your idea. You can edit every word.</p>
    <div className="idea-builder-grid">
      <label className="simple-field"><span>Who?</span><select aria-label="Idea main person" value={who} onChange={e=>setWho(e.target.value)}><option value="">{people.length?'Choose a person':'The subject'}</option>{people.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label className="simple-field"><span>Does what?</span><select aria-label="Idea action" value={action} onChange={e=>setAction(e.target.value)}>{['walks into the scene','smiles at the camera','turns toward the camera','shows','holds','picks up','puts down','shares','talks'].map(v=><option key={v}>{v}</option>)}</select></label>
      <label className="simple-field"><span>Which object?</span><select aria-label="Idea object" value={object} onChange={e=>setObject(e.target.value)}><option value="">No object</option>{objects.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="simple-field"><span>With whom?</span><select aria-label="Idea other person" value={target} onChange={e=>setTarget(e.target.value)}><option value="">Nobody else</option>{people.filter(s=>s.id!==who).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label className="simple-field"><span>Where?</span><select aria-label="Idea place" value={place} onChange={e=>setPlace(e.target.value)}><option value="">I'll describe the place</option>{places.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="simple-field"><span>How should it end?</span><input aria-label="Idea ending" value={ending} onChange={e=>setEnding(e.target.value)} placeholder="both smiling; the box stays closed"/></label>
    </div>
    <p className="idea-builder-preview">{sentence}</p>
    <button type="button" disabled={!!people.length&&!person} onClick={()=>update(d=>{ensurePromptTags(d);d.story.text=[d.story.text.trim(),sentence].filter(Boolean).join('\n');})}>Add sentence to my idea</button>
  </details>;
}
