import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import FilmStudio, {filmBackupData,filmDraft,filmMissingShots,filmQueueChanged,filmQueueIds,filmReferenceTag,filmTiming,mergeFilmSave,parseFilmBackup,reorderFilmShot,seekFilmPreview,validateFilmReferenceSelection,type FilmProject} from './FilmStudio';
const film:FilmProject={id:'f',title:'Film',idea:'One story',target_minutes:1,clip_seconds:15,revision:2,status:'draft',style:'cinematic',continuity_notes:'Same lantern',aspect_ratio:'16:9',quality:'draft',references:[],shots:[{id:'a',title:'Opening',action:'Lantern moves',setting:'Garden',final_state:'Still'},{id:'b',title:'Next',action:'',setting:'Garden',final_state:''}],latest_batch_id:'queue'};
describe('Film workspace',()=>{
  it('divides selected runtime into fifteen-second clips',()=>{expect(filmTiming(1)).toEqual({clips:4,seconds:60});expect(filmTiming(10)).toEqual({clips:40,seconds:600});});
  it('rejects film lengths outside the supported whole-minute range',()=>{for(const n of [0,11,1.5,NaN])expect(()=>filmTiming(n)).toThrow('1 to 10');});
  it('finds unwritten clips without treating footage as approved',()=>{expect(filmMissingShots(film)).toEqual([2]);});
  it('moves whole clip direction including its ending without mutating the original',()=>{const next=reorderFilmShot(film.shots,1,-1);expect(next.map(s=>s.id)).toEqual(['b','a']);expect(next[1].final_state).toBe('Still');expect(film.shots[0].id).toBe('a');expect(reorderFilmShot(film.shots,0,-1)).toEqual(film.shots);});
  it('saves editable direction separately from render receipts and revisions',()=>{const draft=filmDraft(film);expect(draft.shots).toEqual(film.shots);expect(draft).not.toHaveProperty('latest_batch_id');expect(draft).not.toHaveProperty('revision');});
  it('validates the whole photo selection before uploading or exceeding nine references',()=>{
    expect(()=>validateFilmReferenceSelection(8,[{type:'image/png'},{type:'image/jpeg'}])).toThrow('at most 1 more');
    expect(()=>validateFilmReferenceSelection(0,[{type:'image/png'},{type:'video/mp4'}])).toThrow('No files were uploaded');
    expect(()=>validateFilmReferenceSelection(8,[{type:'image/jpeg'}])).not.toThrow();
  });
  it('does not rebind reference tags after removing or adding photos',()=>{
    const a=filmReferenceTag('c3e29029-e780-40bf-aad6-2b6d607e871b'),b=filmReferenceTag('b06bf559-92b1-4e7d-9c38-fb7788a6128d');
    expect(a).not.toBe(b);expect(a).toMatch(/^filmref[a-f0-9]+$/);expect(a.length).toBeLessThanOrEqual(64);
  });
  it('seeks the matching film review player, pauses it and stays inside its duration',()=>{
    const clip={index:0,project_id:'project',title:'Clip',status:'succeeded',run_id:'run',video_url:'/run/video'};
    const player={dataset:{runId:'run'},duration:15,currentTime:0,pause:vi.fn(),scrollIntoView:vi.fn()};
    expect(seekFilmPreview(player,clip,clip,3.5)).toBe(true);expect(player.currentTime).toBe(3.5);
    expect(player.pause).toHaveBeenCalledOnce();expect(player.scrollIntoView).toHaveBeenCalledWith({block:'nearest',behavior:'smooth'});
    expect(seekFilmPreview(player,clip,clip,20)).toBe(true);expect(player.currentTime).toBe(15);
  });
  it('ignores stale review events after selecting or closing another clip',()=>{
    const old={index:0,project_id:'project',title:'Old',status:'succeeded',run_id:'old',video_url:'/old/video'};
    const current={...old,run_id:'new',video_url:'/new/video'},player={dataset:{runId:'new'},duration:15,currentTime:2,pause:vi.fn(),scrollIntoView:vi.fn()};
    expect(seekFilmPreview(player,current,old,5)).toBe(false);expect(seekFilmPreview(player,null,current,5)).toBe(false);
    expect(seekFilmPreview(player,current,current,NaN)).toBe(false);expect(seekFilmPreview(player,current,current,-1)).toBe(false);
    expect(player.currentTime).toBe(2);expect(player.pause).not.toHaveBeenCalled();expect(player.scrollIntoView).not.toHaveBeenCalled();
  });
  it('retains earlier queue identifiers in newest-first order',()=>{
    expect(filmQueueIds({...film,batch_history:['old','queue'],latest_batch_id:'queue'})).toEqual(['queue','old']);
    expect(filmQueueIds({...film,batch_history:['old'],latest_batch_id:'queue'})).toEqual(['queue','old']);
  });
  it('accounts for the receipt revision when reporting later edits to a frozen queue',()=>{
    const created={...film,revision:3,batch_revisions:{queue:2}};
    expect(filmQueueChanged(created,'queue',false)).toBe(false);
    expect(filmQueueChanged(created,'queue',true)).toBe(true);
    expect(filmQueueChanged({...created,revision:4},'queue',false)).toBe(true);
  });
  it('compares frozen direction so repeated identical queues do not appear outdated',()=>{
    const repeated={...film,revision:12,batch_revisions:{queue:2},batch_fingerprints:{queue:'same-direction'},render_fingerprint:'same-direction'};
    expect(filmQueueChanged(repeated,'queue',false)).toBe(false);
    expect(filmQueueChanged({...repeated,render_fingerprint:'new-direction'},'queue',false)).toBe(true);
    expect(filmQueueChanged(repeated,'queue',true)).toBe(true);
  });
  it('keeps text typed during an earlier save and accepts canonical saved text when no newer edit exists',()=>{
    const submitted=JSON.stringify(filmDraft(film)),saved={...film,title:'Canonical film',revision:3};
    const newer={...film,idea:'The user added an ending while saving'};
    const merged=mergeFilmSave(newer,submitted,saved);
    expect(merged.idea).toBe(newer.idea);expect(merged.revision).toBe(3);
    expect(mergeFilmSave(film,submitted,saved)).toBe(saved);
    expect(mergeFilmSave({...newer,id:'another-film'},submitted,saved).revision).toBe(2);
    expect(film.idea).toBe('One story');
  });
  it('preserves exact dialogue and language in editable backups',()=>{
    const next={...film,shots:[{...film.shots[0],dialogue:[{speaker:'Mira',text:'Mirëmbrëma.',language:'Albanian'}]}]};
    expect(filmDraft(next).shots[0].dialogue?.[0]).toEqual({speaker:'Mira',text:'Mirëmbrëma.',language:'Albanian'});
  });
  it('exports direction without film receipts, IDs or internal reference paths',()=>{
    const reference={id:'reference',name:'Face',media_type:'image',role:'reference_image',semantic_role:'face',enabled:true,locked_order:false,description:'Same person',observation:'',approved_observation:'',filename:'private/path.png'};
    const backup=filmBackupData({...film,references:[reference],batch_history:['queue']});
    expect(backup.film).not.toHaveProperty('id');expect(backup.film).not.toHaveProperty('batch_history');
    expect(backup.film.references[0]).not.toHaveProperty('filename');expect(backup.film.references[0].id).toBe('reference');
  });
  it('imports only a versioned, bounded storyboard with the expected clip count',()=>{
    const complete={...film,shots:Array.from({length:4},(_,i)=>({...film.shots[0],id:'clip'+i}))};
    const backup=filmBackupData(complete);const parsed=parseFilmBackup(JSON.stringify(backup));
    expect(parsed.title).toBe('Film');expect(parsed.shots).toHaveLength(4);expect(parsed).not.toHaveProperty('latest_batch_id');
    expect(()=>parseFilmBackup(JSON.stringify({...backup,version:2}))).toThrow('version 1');
    expect(()=>parseFilmBackup(JSON.stringify(filmBackupData(film)))).toThrow('four clips per minute');
    expect(()=>parseFilmBackup('x'.repeat(2_000_001))).toThrow('too large');
  });
  it('explains a dedicated film workflow with no Game action or GPU start on landing',()=>{const html=renderToStaticMarkup(<FilmStudio active={true} onConnections={()=>{}}/>);expect(html).toContain('My films');expect(html).toContain('15-second clips');expect(html).toContain('New film');expect(html).not.toContain('Play from here');expect(html).not.toContain('Start rendering');});
});
