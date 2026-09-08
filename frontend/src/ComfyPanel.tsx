import { useEffect, useRef, useState } from 'react';
import { api, downloadText } from './api';
import type { Project } from './model';
import './ComfyPanel.css';

type Props = { project:Project; prompt:string; ready:boolean; busy:boolean; onSettings:(settings:any)=>void; onContinuationSource?:(value:string)=>void; sendEmbedded?:(ticket:string)=>boolean; embedded?:boolean };
const defaults = { resolution:'0.3', quality:'fast', steps:'auto', seed:9072026 };
export default function ComfyPanel({project:p,prompt,ready,busy,onSettings,onContinuationSource,sendEmbedded,embedded}:Props) {
  const [catalog,setCatalog]=useState<any>(null), [sending,setSending]=useState(false), [error,setError]=useState(''),[sent,setSent]=useState<any>(null);
  const [overlapNotice,setOverlapNotice]=useState('');
  const catalogRequest=useRef(0);
  const current=useRef({p,prompt}); current.current={p,prompt};
  const draftKey=JSON.stringify(p);
  const config={...defaults,...p.comfy_render};
  const sourceValue=String(config.continuation_source??'').replaceAll('\\','/').replace(/^output::/,'');
  const sourceOptions=(catalog?.mmh3_sources??[]).map((item:any)=>({...item,value:String(item.value??item.selector??'').replaceAll('\\','/').replace(/^output::/,'')}));
  const refresh=async()=>{
    const request=++catalogRequest.current, projectId=p.id, source=sourceValue;
    const stillCurrent=()=>request===catalogRequest.current&&current.current.p.id===projectId&&
      String(current.current.p.comfy_render?.continuation_source??'').replaceAll('\\','/').replace(/^output::/,'')===source;
    try {const next=await api('/comfy/options');if(stillCurrent()){setCatalog(next);setError('');}}
    catch(e){if(stillCurrent())setError((e as Error).message);}
  };
  useEffect(()=>{setCatalog(null);void refresh();return()=>{catalogRequest.current++;};},[p.id,sourceValue]);
  useEffect(()=>{setSent(null);setError('');},[p.id]);
  useEffect(()=>{setSent(null);},[draftKey,prompt]);
  const change=(key:string,value:any)=>{onSettings({...config,[key]:value});setSent(null);};
  const recipe=p.mode==='ref2va'?'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors':'minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors';
  const loras:any[]=config.loras??[{name:recipe,strength:1,enabled:true}];
  const names:string[]=(catalog?.available_loras??catalog?.loras??[]).map((x:any)=>typeof x==='string'?x:x.name).filter(Boolean);
  const editLora=(index:number,key:string,value:any)=>change('loras',loras.map((l,i)=>i===index?{...l,[key]:value}:l));
  const imported=p.comfy_source?.generation_settings;
  const generatedFrames=Math.ceil((p.duration*24-5)/17)*17+5;
  const overlap=config.continuation_overlap_frames??39;
  const overlapChoices:number[]=(catalog?.mmh3_overlap_frames??[39]).filter((frames:number)=>frames<generatedFrames);
  const overlapChoicesKey=overlapChoices.join(',');
  const overlapValid=overlapChoices.includes(overlap);
  const missingSource=!!sourceValue&&!!catalog&&!sourceOptions.some((item:any)=>item.value===sourceValue);
  const continuationReady=!config.continuation_source||(!!catalog&&!missingSource&&overlapValid);
  useEffect(()=>{
    if(catalog&&config.continuation_source&&!overlapValid&&overlapChoices.length){
      const next=overlapChoices.includes(39)?39:overlapChoices[0];
      onSettings({...config,continuation_overlap_frames:next});
      setOverlapNotice(`Context adjusted to ${(next/24).toFixed(3)} seconds so it fits this clip.`);
      setSent(null);
    }
  },[p.id,config.continuation_source,overlap,generatedFrames,overlapChoicesKey,!!catalog]);
  const prepare=async()=>{
    if(!ready||busy||sending||!continuationReady)return;
    // Open during the click so browser popup protection does not swallow the handoff.
    const tab=embedded?null:window.open('about:blank','_blank');
    if(tab){tab.opener=null;tab.document.title='Preparing ComfyUI workflow';tab.document.body.textContent='Preparing your prompt, photos and render settings…';}
    setSending(true);setError('');setSent(null);
    const draft=JSON.stringify(current.current.p),text=current.current.prompt;
    try {
      const result=await api('/comfy/prepare',{project:current.current.p,prompt:text});
      if(JSON.stringify(current.current.p)!==draft||current.current.prompt!==text)throw new Error('The project changed during transfer. Review it and send the updated version.');
      setSent({...result,draftKey:draft,prompt:text});
      if(embedded&&sendEmbedded?.(result.ticket))return;
      if(tab)tab.location.replace(result.open_url);
    }catch(e){tab?.close();setError(e instanceof Error?e.message:String(e));}
    finally{setSending(false);}
  };
  const download=async()=>{try{const value=await api('/comfy/transfers/'+sent.ticket);downloadText('H3-Prompt-Studio-workflow.json',JSON.stringify(value.workflow,null,2),'application/json');}catch(e){setError((e as Error).message);}};
  return <section className="comfy-panel" aria-label="ComfyUI video settings">
    <div className="comfy-heading"><div><h3>Make the video in ComfyUI</h3><p>Send this prompt with its photos and settings. Review the new workflow, then press Run.</p></div><span>{p.duration}s · {config.resolution} MP</span></div>
    <details><summary>Video settings & LoRAs</summary>
      <div className="comfy-fields">
        <label><span>Resolution</span><select aria-label="Comfy resolution" value={config.resolution} onChange={e=>change('resolution',e.target.value)}>{['0.3','0.5','0.7','1.0'].map(v=><option key={v} value={v}>{v} MP{v==='0.3'?' · quick preview':''}</option>)}</select></label>
        <label><span>Recipe</span><select aria-label="Comfy recipe" value={config.quality} onChange={e=>change('quality',e.target.value)}><option value="fast">Fast tested recipe</option><option value="detailed">Double steps · compare quality</option></select></label>
        <label><span>Sampling steps</span><select aria-label="Comfy steps" value={config.steps} onChange={e=>change('steps',e.target.value==='auto'?'auto':Number(e.target.value))}><option value="auto">Recipe default</option>{[4,8,16].map(v=><option key={v} value={v}>{v} steps</option>)}</select></label>
        <label><span>Seed</span><input aria-label="Comfy seed" type="number" min={0} max={9007199254740991} step={1} value={config.seed} onChange={e=>change('seed',Number(e.target.value))}/></label>
      </div>
      <p className="comfy-hint">Video size follows your selected shape. The H3 text encoder stays Qwen 32B Heretic. More steps or extra LoRAs can change quality and speed.</p>
      <div className="comfy-lora-heading"><strong>LoRAs · applied from top to bottom</strong><button type="button" onClick={refresh}>Refresh installed files</button></div>
      <div className="comfy-loras">{loras.map((l,i)=><div key={i} className="comfy-lora">
        <input aria-label={`Enable LoRA ${i+1}`} type="checkbox" checked={l.enabled!==false} onChange={e=>editLora(i,'enabled',e.target.checked)}/>
        <label><span>LoRA {i+1}</span><select aria-label={`LoRA ${i+1} file`} title={l.name} value={l.name} onChange={e=>editLora(i,'name',e.target.value)}><option value="">Choose an installed LoRA…</option>{[...new Set([l.name,...names].filter(Boolean))].map(name=><option key={name} value={name}>{name}</option>)}</select></label>
        <label className="comfy-strength"><span>Strength</span><input aria-label={`LoRA ${i+1} strength`} type="number" min={-4} max={4} step={0.05} value={l.strength} onChange={e=>editLora(i,'strength',Number(e.target.value))}/></label>
        <button type="button" aria-label={`Move LoRA ${i+1} up`} disabled={i===0} onClick={()=>{const next=[...loras];[next[i-1],next[i]]=[next[i],next[i-1]];change('loras',next);}}>↑</button>
        <button type="button" aria-label={`Remove LoRA ${i+1}`} onClick={()=>change('loras',loras.filter((_,j)=>j!==i))}>×</button>
      </div>)}</div>
      <div className="comfy-actions"><button type="button" disabled={loras.length>=8} onClick={()=>change('loras',[...loras,{name:'',strength:1,enabled:true}])}>+ Add another LoRA</button><button type="button" onClick={()=>change('loras',undefined)}>Reset to recipe LoRA</button><span className="comfy-hint">{loras.length}/8 rows</span></div>
      <p className="comfy-hint">Choose LoRAs made for your H3 model. Installed files from other model families are not necessarily compatible. The recipe includes its speed LoRA; add style or character LoRAs as extra rows.</p>
      {imported&&<details className="comfy-imported"><summary>Settings received from ComfyUI</summary><p>{imported.width} × {imported.height} · {imported.summary?.steps??'—'} steps · seed {imported.summary?.seed??'—'}</p>{imported.loras?.map((l:any,i:number)=><p key={i}>{i+1}. {l.name} · strength {l.strength_model}{l.enabled===false?' · disabled':''}</p>)}<p className="comfy-hint">This is the snapshot sent from the selected workflow. The settings above control the new workflow.</p><button type="button" onClick={()=>{onSettings({...config,...(Number.isInteger(imported.summary?.seed)?{seed:imported.summary.seed}:{}),...([4,8,16].includes(imported.summary?.steps)?{steps:imported.summary.steps}:{}),...(imported.loras?.length?{loras:imported.loras.map((l:any)=>({name:l.name,strength:l.strength_model??1,enabled:l.enabled!==false}))}:{})});setSent(null);}}>Use its seed, supported steps & LoRAs</button></details>}
      {catalog?.error&&<p className="comfy-hint">{catalog.error}</p>}
    </details>
    <details className="comfy-continuation"><summary>Save & continue actual video motion</summary>
      <p className="comfy-hint">MMH3 stores a working file alongside the MP4, with sampled video/audio state, references and LoRA settings. Choose a saved clip below when you want to carry its ending motion into this one.</p>
      <label className="comfy-checkbox"><input type="checkbox" aria-label="Save continuation state" disabled={!catalog?.mmh3_save_available} checked={config.save_mmh3??!!catalog?.mmh3_save_available} onChange={e=>change('save_mmh3',e.target.checked)}/><span>Save continuation state (.mmh3) with this video</span></label>
      <label><span>Continue from a saved clip · optional</span><select aria-label="Continue from saved video" disabled={!catalog?.mmh3_continuation_available&&!sourceValue} value={sourceValue} onChange={e=>{setSent(null);setOverlapNotice('');onContinuationSource?onContinuationSource(e.target.value):change('continuation_source',e.target.value);}}><option value="">New video · use my photos and story</option>{sourceValue&&!sourceOptions.some((item:any)=>item.value===sourceValue)&&<option value={sourceValue}>{catalog?'Unavailable saved clip':'Checking saved clip'} · {sourceValue}</option>}{sourceOptions.map((item:any)=><option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      {missingSource&&<p className="comfy-error" role="alert">The selected saved clip is unavailable. Refresh saved clips, choose another file, or choose New video.</p>}
      <button type="button" onClick={refresh}>Refresh saved clips</button>
      {!catalog?.mmh3_available&&<p className="comfy-hint">{catalog?.mmh3_note||'Open ComfyUI Desktop and refresh to detect the MMH3 nodes.'}</p>}
      {catalog?.mmh3_available&&!catalog?.mmh3_sources?.length&&<p className="comfy-hint">After your first render with state saving enabled, refresh to choose its working file here. An older MP4 alone does not contain the sampled motion state.</p>}
      {config.continuation_source&&<><p className="comfy-hint">The saved clip supplies starting motion and sound at its original resolution. Reference photos, if selected, direct the new footage. A starting-frame photo is replaced by this saved context.</p>
        <label><span>How much ending motion to carry over</span><select aria-label="Continuation context" value={overlapValid?overlap:''} onChange={e=>{setOverlapNotice('');change('continuation_overlap_frames',Number(e.target.value));}}>{!overlapValid&&<option value="">Adjusting context to fit…</option>}{overlapChoices.map((frames:number)=><option key={frames} value={frames}>{(frames/24).toFixed(3)} seconds{frames===39?' · recommended':''}</option>)}</select></label>
        {overlapNotice&&<p className="comfy-hint" role="status">{overlapNotice}</p>}
        {overlapValid&&<p className="comfy-timing">{(generatedFrames/24).toFixed(3)}s generated = {(overlap/24).toFixed(3)}s carried context + {((generatedFrames-overlap)/24).toFixed(3)}s new footage</p>}
        <p className="comfy-hint">Use the MMH3 Latent Stitch workflow for compatible clips generated in sequence to remove repeated context. The story planner's times describe your intended story; final film length depends on these overlaps.</p></>}
    </details>
    {sourceValue&&<p className="comfy-source comfy-hint">Continuing saved clip: <strong>{sourceValue.split('/').at(-1)}</strong>. The new workflow starts from its saved motion and audio.</p>}
    <div className="comfy-actions"><button className="comfy-send" type="button" onClick={prepare} disabled={!ready||busy||sending||!continuationReady}>{sending?'Preparing photos & workflow…':'Send prompt + photos to ComfyUI →'}</button>{!ready&&<span className="comfy-hint">Make or build your prompt first.</span>}</div>
    <p className="comfy-hint">In ComfyUI, edit the prompt and seed in MMH3 Create. To extend the finished video, use Continue this result in Studio on its Save node. Changing a seed makes another variation; it does not advance the story by itself.</p>
    {error&&<p className="comfy-error" role="alert">{error}</p>}
    {sent&&sent.draftKey===draftKey&&sent.prompt===prompt&&<div className="comfy-sent"><strong>Workflow prepared · no video queued</strong><p>{sent.manifest.width} × {sent.manifest.height} · {sent.manifest.steps} steps · {sent.manifest.images.length} photos verified</p><p>{sent.manifest.duration_note}</p>{sent.manifest.lora_warnings?.map((warning:string)=><p className="comfy-hint" key={warning}>{warning}</p>)}<div className="comfy-actions"><a href={sent.open_url} target="_blank" rel="noreferrer">Open prepared ComfyUI workflow</a><button type="button" onClick={download}>Download workflow</button></div><p className="comfy-hint">Workflow copy: {sent.export_folder}<br/>Video after Run: ComfyUI output/{sent.manifest.output_prefix}{sent.manifest.mmh3?.save_enabled&&<><br/>Working state: ComfyUI output/{sent.manifest.mmh3.output_prefix}*.mmh3</>}</p></div>}
  </section>;
}
