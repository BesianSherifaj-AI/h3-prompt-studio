import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, Clapperboard, Download, Film, ImagePlus, LoaderCircle, Plus, Save, Sparkles, Upload, X } from 'lucide-react';
import { api } from './api';
import type { Asset } from './model';
import { ProductionBatchView, type ProductionBatch, type ProductionItem, productionExportOptions } from './ProductionQueue';
import VideoReview from './VideoReview';
import './FilmStudio.css';

export type FilmShot = { id: string; title: string; action: string; setting: string; final_state: string; sound?: string; camera?: Record<string,string>; dialogue?: {speaker:string;text:string;language?:string}[] };
export type FilmProject = { id:string; title:string; idea:string; target_minutes:number; clip_seconds:number; revision:number; status:string; style:string; continuity_notes:string; aspect_ratio:string; quality:string; references:Asset[]; shots:FilmShot[]; latest_batch_id?:string|null; batch_history?:string[]; batch_revisions?:Record<string,number>; batch_fingerprints?:Record<string,string>; render_fingerprint?:string; created_at?:number; updated_at?:number };
export function filmDraft(film: FilmProject) {
  return {title:film.title,idea:film.idea,target_minutes:film.target_minutes,style:film.style,continuity_notes:film.continuity_notes,aspect_ratio:film.aspect_ratio,quality:film.quality,references:film.references,shots:film.shots};
}
export function filmTiming(minutes:number) { if(!Number.isInteger(minutes) || minutes<1 || minutes>10)throw new Error('Choose a whole film length from 1 to 10 minutes.');return {clips:minutes*4, seconds:minutes*60}; }
export function filmMissingShots(film:FilmProject) { return film.shots.map((shot,index)=>({shot,index})).filter(({shot})=>!shot.action.trim()).map(({index})=>index+1); }
export function reorderFilmShot(shots:FilmShot[], index:number, direction:number) {
  const result=[...shots], target=index+direction;
  if(index<0 || index>=result.length || target<0 || target>=result.length)return result;
  [result[index],result[target]]=[result[target],result[index]];return result;
}
export function validateFilmReferenceSelection(existing:number, files:Pick<File,'type'>[]) {
  if(existing + files.length > 9) throw new Error(`Choose at most ${Math.max(0,9-existing)} more reference photo${9-existing===1?'':'s'}. A film can use 9 shared photos.`);
  if(files.some(file=>!file.type.startsWith('image/'))) throw new Error('Choose image files for film references. No files were uploaded.');
}
export function filmReferenceTag(assetId:string) { return 'filmref'+assetId.replaceAll('-',''); }
export function filmQueueIds(film:FilmProject) {
  return [...new Set([...(film.batch_history || []),film.latest_batch_id].filter((id):id is string=>typeof id==='string' && !!id))].reverse();
}
export function filmQueueChanged(film:FilmProject, batchId:string, unsaved:boolean) {
  if(unsaved)return true;
  const sourceFingerprint=film.batch_fingerprints?.[batchId];
  if(sourceFingerprint && film.render_fingerprint)return sourceFingerprint!==film.render_fingerprint;
  const source=film.batch_revisions?.[batchId];
  return typeof source==='number' && film.revision > source+1;
}
export function mergeFilmSave(current:FilmProject, submittedKey:string, saved:FilmProject) {
  if(current.id!==saved.id)return current;
  return JSON.stringify(filmDraft(current))===submittedKey?saved:{...current,revision:saved.revision,updated_at:saved.updated_at};
}
export function filmBackupData(film:FilmProject) {
  const draft=filmDraft(film);
  return {format:'h3-film-storyboard',version:1,film:{...draft,references:draft.references.map(a=>({id:a.id,name:a.name,media_type:'image',role:'reference_image',semantic_role:a.semantic_role || 'other',enabled:a.enabled,prompt_tag:a.prompt_tag,description:a.description || ''}))}};
}
export function parseFilmBackup(text:string) {
  if(text.length>2_000_000)throw new Error('This storyboard file is too large. Choose a JSON backup up to 2 MB.');
  const value=JSON.parse(text);
  if(value?.format!=='h3-film-storyboard' || value.version!==1 || !value.film || typeof value.film!=='object' || Array.isArray(value.film))throw new Error('Choose a version 1 H3 film storyboard JSON backup.');
  const draft=filmDraft(value.film as FilmProject);
  if(typeof draft.title!=='string' || !draft.title.trim() || typeof draft.idea!=='string' || typeof draft.style!=='string' || typeof draft.continuity_notes!=='string' || !Array.isArray(draft.references) || !Array.isArray(draft.shots))throw new Error('This film backup is missing its name, direction or storyboard.');
  const timing=filmTiming(draft.target_minutes);
  if(draft.shots.length!==timing.clips || draft.references.length>9)throw new Error('The storyboard must contain four clips per minute and at most nine shared photos.');
  return draft;
}
type FilmReviewPlayer=Pick<HTMLVideoElement,'dataset'|'duration'|'currentTime'|'pause'|'scrollIntoView'>;
export function seekFilmPreview(player:FilmReviewPlayer|null,current:ProductionItem|null,requested:ProductionItem,seconds:number) {
  if(!player || !requested.run_id || current?.run_id!==requested.run_id || current.video_url!==requested.video_url || player.dataset.runId!==requested.run_id || !Number.isFinite(seconds) || seconds<0)return false;
  player.pause();player.currentTime=Math.min(seconds,Number.isFinite(player.duration)?Math.max(0,player.duration):seconds);
  player.scrollIntoView({block:'nearest',behavior:'smooth'});return true;
}
const minuteLabel=(seconds:number)=>`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
export default function FilmStudio({active,onConnections}:{active:boolean;onConnections:()=>void}) {
  const [films,setFilms]=useState<FilmProject[]>([]),[film,setFilm]=useState<FilmProject|null>(null),[creating,setCreating]=useState(false);
  const [newTitle,setNewTitle]=useState(''),[newIdea,setNewIdea]=useState(''),[newMinutes,setNewMinutes]=useState(1);
  const [search,setSearch]=useState(''),[selected,setSelected]=useState(0),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[saveState,setSaveState]=useState('All changes saved');
  const [batch,setBatch]=useState<ProductionBatch|null>(null),[preview,setPreviewState]=useState<ProductionItem|null>(null),[balanceAudio,setBalanceAudio]=useState(true);
  const [queueId,setQueueId]=useState('');
  const [undoPlan,setUndoPlan]=useState<FilmProject|null>(null);
  const filmRef=useRef<FilmProject|null>(null);
  const revision=useRef({id:'',value:0}),saveChain=useRef<Promise<unknown>>(Promise.resolve()),lastSaved=useRef(''),operation=useRef(false),timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),generation=useRef(0),upload=useRef<HTMLInputElement>(null);
  const queueRef=useRef(''),produceRequest=useRef<{filmId:string;revision:number;id:string}|null>(null);
  const backupUpload=useRef<HTMLInputElement>(null);
  const previewRef=useRef<ProductionItem|null>(null),reviewPlayer=useRef<HTMLVideoElement>(null);
  const setPreview=(value:ProductionItem|null)=>{previewRef.current=value;setPreviewState(value);};
  const key=film?JSON.stringify(filmDraft(film)):'';
  const markFilm=(value:FilmProject)=>{generation.current++;revision.current={id:value.id,value:value.revision};lastSaved.current=JSON.stringify(filmDraft(value));filmRef.current=value;queueRef.current=value.latest_batch_id || '';setQueueId(queueRef.current);produceRequest.current=null;setFilm(value);setSelected(0);setBatch(null);setPreview(null);setUndoPlan(null);setSaveState('All changes saved');};
  const refresh=async()=>{const result=await api('/films');setFilms(result.films || []);};
  const persist=(snapshot:FilmProject)=>{
    const editable=filmDraft(snapshot), draftKey=JSON.stringify(editable);
    const request=saveChain.current.catch(()=>{}).then(async()=>{
      if(filmRef.current?.id===snapshot.id && draftKey===lastSaved.current)return filmRef.current;
      const expected=revision.current.id===snapshot.id?revision.current.value:snapshot.revision;
      const value:FilmProject=await api(`/films/${snapshot.id}`,{...editable,expected_revision:expected},undefined,'PATCH');
      if(filmRef.current?.id===value.id){revision.current={id:value.id,value:value.revision};lastSaved.current=JSON.stringify(filmDraft(value));
        const current=filmRef.current;
        const next=mergeFilmSave(current,draftKey,value);
        filmRef.current=next;setFilm(next);setSaveState(JSON.stringify(filmDraft(next))===lastSaved.current?'All changes saved':'Saving…');
      }
      return value;
    });saveChain.current=request;return request;
  };
  const flush=async()=>{
    clearTimeout(timer.current);await saveChain.current.catch(()=>{});
    for(let i=0;i<20;i++){const current=filmRef.current;if(!current || JSON.stringify(filmDraft(current))===lastSaved.current)return current;if(i===19)throw new Error('Finish editing, then save again.');await persist(structuredClone(current));}
    return filmRef.current;
  };
  const perform=async(label:string,fn:()=>Promise<void>)=>{
    if(operation.current)return;operation.current=true;setBusy(label);setError('');setNotice('');
    try{await fn();}catch(e){setError((e as Error).message);}finally{operation.current=false;setBusy('');}
  };
  useEffect(()=>{if(active)void refresh().catch(e=>setError(e.message));},[active]);
  useEffect(()=>{
    if(!film || key===lastSaved.current)return;setSaveState('Saving…');clearTimeout(timer.current);
    timer.current=setTimeout(()=>{void persist(structuredClone(film)).catch(e=>{if(filmRef.current?.id===film.id){setSaveState('Not saved');setError(e.message);}});},750);
    return()=>clearTimeout(timer.current);
  },[key]);
  useEffect(()=>{const protect=(event:BeforeUnloadEvent)=>{if(filmRef.current && JSON.stringify(filmDraft(filmRef.current))!==lastSaved.current){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',protect);return()=>window.removeEventListener('beforeunload',protect);},[]);
  useEffect(()=>{
    if(!active || !film || !queueId)return;
    let alive=true;let polling:ReturnType<typeof setTimeout>;
    const batchId=queueId,filmId=film.id;
    const stillSelected=()=>alive && filmRef.current?.id===filmId && queueRef.current===batchId;
    const poll=async()=>{try{const result=await api(`/production/${batchId}`,undefined,undefined,undefined,{timeoutMs:20_000});if(stillSelected())setBatch(result);}catch(e){if(stillSelected())setError((e as Error).message);}if(alive)polling=setTimeout(poll,2500);};void poll();return()=>{alive=false;clearTimeout(polling);};
  },[active,film?.id,queueId]);
  const update=(fn:(draft:FilmProject)=>void)=>{const current=filmRef.current;if(!current)return;const next=structuredClone(current);fn(next);filmRef.current=next;setFilm(next);};
  const open=(id:string)=>void perform('Opening film',async()=>{await flush();const next=await api(`/films/${id}`);markFilm(next);setCreating(false);});
  const home=()=>void perform('Saving film',async()=>{await flush();setFilm(null);filmRef.current=null;setCreating(false);setBatch(null);setPreview(null);await refresh();});
  const create=()=>void perform('Creating film',async()=>{await flush();const next=await api('/films',{title:newTitle.trim(),idea:newIdea.trim(),target_minutes:newMinutes,aspect_ratio:'16:9',quality:'draft'});markFilm(next);setCreating(false);setNewTitle('');setNewIdea('');await refresh();});
  const duplicate=()=>void perform('Copying film',async()=>{const current=await flush();if(!current)return;const next=await api('/films',{...filmDraft(current),title:current.title.slice(0,152)+' · copy'});markFilm(next);await refresh();setNotice('Opened a separate film copy. The original storyboard and its takes are saved.');});
  const downloadBackup=()=>void perform('Saving storyboard backup',async()=>{const current=await flush();if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(filmBackupData(current),null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=(current.title.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').trim().slice(0,100) || 'film')+'.h3film.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setNotice('Storyboard downloaded. Reference photos must already exist on this computer when importing it.');});
  const importBackup=(file:File)=>void perform('Importing storyboard',async()=>{if(file.size>2_000_000)throw new Error('Choose a storyboard JSON backup up to 2 MB.');await flush();const draft=parseFilmBackup(await file.text());const next=await api('/films',draft);markFilm(next);setCreating(false);await refresh();setNotice('Storyboard imported as a separate saved film. Review its references before rendering.');});
  const plan=()=>void perform('Qwen 27B is writing the storyboard',async()=>{const current=await flush();if(!current)return;const before=structuredClone(current);const next=await api(`/films/${current.id}/plan`,{expected_revision:current.revision},undefined,undefined,{timeoutMs:600_000});markFilm(next);setUndoPlan(before);setNotice('Storyboard saved. Review every scene, then render when you are ready.');});
  const produce=()=>void perform('Preparing the film render queue',async()=>{const current=await flush();if(!current)return;if(produceRequest.current?.filmId!==current.id || produceRequest.current.revision!==current.revision)produceRequest.current={filmId:current.id,revision:current.revision,id:crypto.randomUUID()};const result=await api(`/films/${current.id}/produce`,{expected_revision:current.revision,request_id:produceRequest.current.id},undefined,undefined,{timeoutMs:30_000});markFilm(result.film);setBatch(result.batch);setNotice('Render queue created. Press Start batch below to use your local GPU.');});
  const importReferences=async(files:FileList|null)=>{if(!files?.length)return;const fileList=Array.from(files);const target=filmRef.current?.id;await perform('Adding reference photos',async()=>{if(!target)return;validateFilmReferenceSelection(filmRef.current!.references.length,fileList);const assets:Asset[]=[];for(const file of fileList){const form=new FormData();form.append('file',file);assets.push(await api('/assets',undefined,form));}if(filmRef.current?.id!==target)throw new Error('The film changed while photos were uploading.');update(d=>{d.references.push(...assets.map(asset=>({...asset,role:'reference_image',enabled:true,prompt_tag:filmReferenceTag(asset.id)})));});await flush();});};
  const selectedShot=film?.shots[selected], missing=film?filmMissingShots(film):[];
  const queueMatches=!!film && !!batch && batch.id===queueId;
  const queues=film?filmQueueIds(film):[];
  const queueChanged=film && queueId?filmQueueChanged(film,queueId,key!==lastSaved.current):false;
  return <main className="film-studio">
    {error && <div className="film-message is-error" role="alert"><span>{error}</span><button aria-label="Dismiss film message" onClick={()=>setError('')}><X size={16}/></button></div>}
    {notice && <p className="film-message" role="status">{notice}</p>}
    {!film && !creating && <>
      <header className="film-home-heading"><div><p className="home-eyebrow">STUDIO · YOUR COMPLETE FILM</p><h1>From scenes to a story.</h1><p>Choose 1–10 minutes. Direct connected 15-second clips, review the takes, and export one film.</p></div><button className="primary" disabled={!!busy} onClick={()=>setCreating(true)}><Plus size={19}/>New film</button></header>
      <div className="film-process"><span><b>1</b>Story & length</span><span><b>2</b>Storyboard</span><span><b>3</b>Render scenes</span><span><b>4</b>Review & export</span></div>
      <div className="film-library-import"><input hidden ref={backupUpload} type="file" accept=".json,application/json" aria-label="Import film storyboard file" onChange={e=>{const file=e.target.files?.[0];if(file)importBackup(file);e.target.value='';}}/><button disabled={!!busy} onClick={()=>backupUpload.current?.click()}><Upload size={16}/>Import storyboard</button><button disabled={!!busy} onClick={()=>void perform('Refreshing saved films',refresh)}>Refresh saved work</button><p>JSON backups restore film direction. Reference photos must already exist on this computer.</p></div><div className="film-library-heading"><h2>My films <small>{films.length}</small></h2><input type="search" aria-label="Find a film" value={search} placeholder="Find a film…" onChange={e=>setSearch(e.target.value)}/></div>
      <div className="film-library">{films.filter(f=>`${f.title} ${f.idea}`.toLowerCase().includes(search.toLowerCase())).map(f=><button key={f.id} disabled={!!busy} onClick={()=>open(f.id)}><span className="film-library-mark"><Clapperboard size={24}/></span><strong>{f.title}</strong><span>{f.idea || 'Open this film to develop its story.'}</span><small>{f.target_minutes} min · {filmTiming(f.target_minutes).clips} clips · {f.status?.replaceAll('_',' ') || 'Draft'}</small><b>Open film<ArrowRight size={16}/></b></button>)}</div>
      {!films.length && <div className="film-empty"><Film size={32}/><h2>Make your first film</h2><p>A one-minute film starts with four 15-second clips. Studio keeps the storyboard, footage and final export together.</p><button className="primary" onClick={()=>setCreating(true)}>New film</button></div>}
      <p className="film-footnote">Studio uses scene cuts and shared reference photos to tell one story. Review continuity between clips before publishing. Continuous motion is available in the Video workspace.</p>
    </>}
    {creating && <section className="film-create"><button className="film-back" onClick={()=>setCreating(false)} disabled={!!busy}><ArrowLeft size={16}/>My films</button><p className="home-eyebrow">START A FILM</p><h1>What story will you tell?</h1><form onSubmit={e=>{e.preventDefault();if(!busy && newTitle.trim())create();}}>
      <label>Film name<input required maxLength={160} autoFocus value={newTitle} onChange={e=>setNewTitle(e.target.value)} disabled={!!busy} placeholder="e.g. The last lantern"/></label>
      <label>Film length<select value={newMinutes} onChange={e=>setNewMinutes(Number(e.target.value))} disabled={!!busy}>{Array.from({length:10},(_,i)=>i+1).map(m=><option key={m} value={m}>{m} minute{m===1?'':'s'} · {m*4} clips × 15 seconds</option>)}</select></label>
      <label>Story idea<textarea rows={5} maxLength={20000} value={newIdea} onChange={e=>setNewIdea(e.target.value)} disabled={!!busy} placeholder="Describe the characters, setting, beginning, main event and ending. You can add it later."/></label>
      <p className="film-create-note"><Check size={16}/>Creates a saved storyboard with {newMinutes*4} editable clips. AI planning and rendering happen only when you choose them.</p><button className="primary" disabled={!!busy || !newTitle.trim()} type="submit"><Plus size={17}/>{busy || 'Create film'}</button>
    </form></section>}
    {film && !creating && <>
      <header className="film-project-bar"><button onClick={home} disabled={!!busy}><ArrowLeft size={16}/>My films</button><div><label htmlFor="film-name">Film name</label><input id="film-name" aria-label="Film name" maxLength={160} value={film.title} disabled={!!busy} onChange={e=>update(d=>{d.title=e.target.value;})}/></div><span className={saveState==='Not saved'?'film-save-error':''} role="status">{saveState}</span><button onClick={()=>void perform('Saving film',async()=>{await flush();})} disabled={!!busy}><Save size={16}/>{saveState==='Not saved'?'Retry save':'Save now'}</button><details className="film-backup-tools"><summary>Backup &amp; copy</summary><div><button onClick={downloadBackup} disabled={!!busy}><Download size={16}/>Download storyboard</button><button onClick={duplicate} disabled={!!busy}><Plus size={16}/>Duplicate film</button><small>Backups contain direction and local reference IDs. Original takes stay with their render queues.</small></div></details></header>
      <div className="film-process"><span><b>1</b>Story & length</span><span><b>2</b>Storyboard</span><span><b>3</b>Render scenes</span><span><b>4</b>Review & export</span></div>
      <section className="film-brief" aria-label="Film brief"><div><h2>Your story</h2><label>Film idea<textarea aria-label="Film idea" rows={3} value={film.idea} maxLength={20000} disabled={!!busy} onChange={e=>update(d=>{d.idea=e.target.value;})}/></label><div className="film-basics"><label>Length<output>{film.target_minutes} min · {film.shots.length} clips</output></label><label>Format<select aria-label="Film format" value={film.aspect_ratio} disabled={!!busy} onChange={e=>update(d=>{d.aspect_ratio=e.target.value;})}><option value="16:9">Landscape · 16:9</option><option value="9:16">Portrait · 9:16</option><option value="1:1">Square · 1:1</option></select></label><label>Quality<select aria-label="Film quality" value={film.quality} disabled={!!busy} onChange={e=>update(d=>{d.quality=e.target.value;})}><option value="draft">Draft · 0.3 MP / 4 steps</option><option value="quality">Quality · 0.3 MP / 8 steps</option></select></label></div></div>
        <div className="film-plan-actions"><p>Qwen 3.8 27B plans the whole story. Then you can edit each clip before rendering.</p><button className="primary" disabled={!!busy || !film.idea.trim()} onClick={plan}><Sparkles size={17}/>{film.shots.some(s=>s.action)?'Rewrite storyboard with AI':'Plan storyboard with AI'}</button>{undoPlan && <button disabled={!!busy} onClick={()=>{update(d=>Object.assign(d,filmDraft(undoPlan)));setUndoPlan(null);}}>Undo last AI plan</button>}<button onClick={onConnections}>Local AI settings</button><small>{busy || 'You can also write every clip yourself.'}</small></div>
      </section>
      <details className="film-shared"><summary>Shared style & references <span>{film.references.length} photos</span></summary><div className="film-shared-fields"><label>Visual style<input aria-label="Film visual style" maxLength={2000} value={film.style} disabled={!!busy} onChange={e=>update(d=>{d.style=e.target.value;})} placeholder="e.g. cinematic, natural dusk light"/></label><label>Continuity notes<textarea aria-label="Film continuity notes" rows={3} maxLength={6000} value={film.continuity_notes} disabled={!!busy} onChange={e=>update(d=>{d.continuity_notes=e.target.value;})} placeholder="Names, appearance, clothes, location and important objects to preserve across clips"/></label></div><input hidden ref={upload} type="file" accept="image/*" multiple onChange={e=>{void importReferences(e.target.files);e.target.value='';}}/><button disabled={!!busy || film.references.length>=9} onClick={()=>upload.current?.click()}><ImagePlus size={16}/>Add reference photos</button><p>Up to 9 shared references, used in every clip. Describe what each photo establishes.</p><div className="film-references">{film.references.map((a,index)=><div key={a.id}><img src={`/api/assets/${a.id}/file`} alt={a.name}/><label>Reference {index+1}<input aria-label={`Film reference ${index+1} description`} maxLength={2000} placeholder={a.name} value={a.description ?? ''} disabled={!!busy} onChange={e=>update(d=>{d.references[index].description=e.target.value;})}/></label><button aria-label={`Remove film reference ${index+1}`} disabled={!!busy} onClick={()=>update(d=>{d.references.splice(index,1);})}><X size={15}/></button></div>)}</div></details>
      <section className="film-storyboard" aria-label="Film storyboard"><div className="film-section-heading"><div><h2>Storyboard</h2><p>{film.shots.length} clips · {film.target_minutes} minute{film.target_minutes===1?'':'s'} · each clip is 15 seconds</p></div><button className="primary" disabled={!!busy || missing.length>0 || batch?.status==='running'} onClick={produce}><Clapperboard size={17}/>Create render queue</button></div>
        {missing.length>0 && <p className="film-missing">Write an action for clip{missing.length===1?'':'s'} {missing.join(', ')} or ask AI to plan the storyboard.</p>}
        <div className="film-editor-layout"><ol className="film-shot-list">{film.shots.map((s,i)=><li key={s.id}><button aria-current={i===selected?'step':undefined} aria-label={`Edit clip ${i+1}: ${s.title}`} onClick={()=>setSelected(i)}><span>{String(i+1).padStart(2,'0')}</span><div><strong>{s.title || `Clip ${i+1}`}</strong><small>{minuteLabel(i*15)}–{minuteLabel((i+1)*15)} · {s.action.trim()?'Written':'Needs action'}</small></div><ArrowRight size={14}/></button></li>)}</ol>
          {selectedShot && <div className="film-shot-editor" key={selectedShot.id}><header><span>CLIP {selected+1} · 15 SECONDS</span><div><button aria-label="Move clip earlier" disabled={!!busy || selected===0} onClick={()=>{update(d=>{d.shots=reorderFilmShot(d.shots,selected,-1);});setSelected(selected-1);}}><ArrowUp size={16}/></button><button aria-label="Move clip later" disabled={!!busy || selected===film.shots.length-1} onClick={()=>{update(d=>{d.shots=reorderFilmShot(d.shots,selected,1);});setSelected(selected+1);}}><ArrowDown size={16}/></button></div></header>
            <label>Clip name<input aria-label="Clip name" maxLength={160} value={selectedShot.title} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].title=e.target.value;})}/></label>
            <label>What happens in these 15 seconds?<textarea aria-label="Clip action" rows={5} maxLength={10000} value={selectedShot.action} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].action=e.target.value;})}/></label>
            <div className="film-shot-fields"><label>Place & opening state<textarea aria-label="Clip setting" rows={2} maxLength={4000} value={selectedShot.setting} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].setting=e.target.value;})}/></label><label>Ending state<textarea aria-label="Clip ending" rows={2} maxLength={4000} value={selectedShot.final_state} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].final_state=e.target.value;})}/></label></div>
            {selected>0 && <p className="film-continuity-hint">Previous ending: {film.shots[selected-1].final_state || 'Describe how the previous clip ends to keep continuity clear.'}</p>}
            <details><summary>Camera, sound & spoken lines</summary><div className="film-shot-fields"><label>Framing<input aria-label="Clip framing" maxLength={1000} value={selectedShot.camera?.framing || ''} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].camera={...d.shots[selected].camera,framing:e.target.value};})}/></label><label>Camera motion<input aria-label="Clip camera motion" maxLength={1000} value={selectedShot.camera?.movement || ''} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].camera={...d.shots[selected].camera,movement:e.target.value};})}/></label></div><label>Sound<textarea aria-label="Clip sound" rows={2} maxLength={2000} value={selectedShot.sound || ''} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].sound=e.target.value;})}/></label>{(selectedShot.dialogue || []).map((line,i)=><div className="film-dialogue" key={i}><label>Speaker<input aria-label={`Clip speaker ${i+1}`} maxLength={160} value={line.speaker} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].dialogue![i].speaker=e.target.value;})}/></label><label>Exact words<input aria-label={`Clip spoken line ${i+1}`} maxLength={2000} value={line.text} disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].dialogue![i].text=e.target.value;})}/></label><label>Language<input aria-label={`Clip dialogue language ${i+1}`} maxLength={80} value={line.language || ''} placeholder="English" disabled={!!busy} onChange={e=>update(d=>{d.shots[selected].dialogue![i].language=e.target.value;})}/></label><button aria-label={`Remove spoken line ${i+1}`} disabled={!!busy} onClick={()=>update(d=>{d.shots[selected].dialogue!.splice(i,1);})}><X size={16}/></button></div>)}<button disabled={!!busy || (selectedShot.dialogue?.length || 0)>=8} onClick={()=>update(d=>{(d.shots[selected].dialogue??=[]).push({speaker:'',text:'',language:'English'});})}><Plus size={15}/>Add spoken line</button></details>
          </div>}
        </div>
      </section>
      {queues.length>0 && <section className="film-production" aria-label="Film render and export"><div className="film-queue-heading"><h2>Render, review &amp; export</h2><label>Saved render queue<select aria-label="Saved film render queue" value={queueId} disabled={!!busy} onChange={e=>{queueRef.current=e.target.value;setQueueId(e.target.value);setBatch(null);setPreview(null);}}>{queues.map(id=><option key={id} value={id}>Queue {Math.max(1,(film.batch_history || []).indexOf(id)+1)}{id===film.latest_batch_id?' · latest':''}{film.batch_revisions?.[id]?' · revision '+film.batch_revisions[id]:''}</option>)}</select></label></div><p>Each queue keeps its own saved storyboard and original takes. Choose an earlier queue to review or export it.</p>{queueChanged && <p className="film-queue-warning" role="status">The current storyboard has newer edits or a later saved revision. This queue keeps its earlier direction. Create a new queue to render your current work.</p>}{queueMatches && batch ? <ProductionBatchView batch={batch} pending={!!busy} balanceAudio={balanceAudio} onBalanceAudioChange={setBalanceAudio}
        onAction={action=>void perform(action==='cancel'?'Stopping render queue':'Starting render queue',async()=>{setBatch(await api(`/production/${batch.id}/${action}`,{},undefined,undefined,{timeoutMs:30_000}));})}
        onRetry={index=>void perform('Retrying clip',async()=>{setBatch(await api(`/production/${batch.id}/items/${index}/retry`,{request_id:crypto.randomUUID()}));})}
        onPreview={setPreview}
        onExport={(kind,edits)=>void perform('Preparing film export',async()=>{const result=await api(`/production/${batch.id}/export`,productionExportOptions(kind,balanceAudio,edits),undefined,undefined,{timeoutMs:600_000});setBatch(current=>current?{...current,latest_export:{...result,kind}}:current);setNotice('Export saved. Download it below.');})}/> : <p role="status">Loading this saved queue…</p>}
      </section>}
      {preview?.video_url && <section className="film-preview" aria-label="Review film clip"><header><h2>{preview.title}</h2><button aria-label="Close film preview" onClick={()=>setPreview(null)}><X size={17}/></button></header><video key={preview.run_id || preview.video_url} ref={reviewPlayer} data-run-id={preview.run_id} controls playsInline preload="metadata" src={preview.video_url}/><a href={preview.video_url} download><Download size={16}/>Download clip</a>{preview.run_id && <VideoReview key={preview.run_id} kind="run" videoId={preview.run_id} title={preview.title} aiDisabled={!!busy || batch?.status==='running'} onSeek={seconds=>seekFilmPreview(reviewPlayer.current,previewRef.current,preview,seconds)}/>}</section>}
      <p className="film-footnote">Planned length: {film.target_minutes} minute{film.target_minutes===1?'':'s'}. H3 frame rounding and any export trims determine the final runtime. Review every clip and scene transition before approving the film.</p>
    </>}
    {busy && <p className="film-busy" role="status"><LoaderCircle size={17} className="spin"/>{busy}…</p>}
  </main>;
}
