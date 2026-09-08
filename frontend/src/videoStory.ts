import type { Project } from './model';

/** Carry story history as completed events, separate from the new clip's speech. */
export function completedVideoStory(source:Project) {
  const previous=source.simple?.continuation?.previous_story;
  const history=Array.isArray(previous?.earlier_clips) ? structuredClone(previous.earlier_clips) : [];
  if(previous?.brief || previous?.shots) history.push({
    brief:String(previous.brief||'').slice(0,1000),
    events:(previous.shots||[]).map((shot:any)=>({
      action:String(shot.action||'').slice(0,500), final_state:String(shot.final_state||'').slice(0,500),
      dialogue:(shot.dialogue||[]).map((line:any)=>({speaker_id:line.speaker_id,text:String(line.text||'').slice(0,500)})),
    })),
  });
  return {
    opening_story:previous?.opening_story || previous?.brief || source.story.text,
    earlier_clips:history.slice(-20),
    brief:source.story.text,
    shots:source.shots.map(shot=>({action:shot.action,setting:shot.setting,final_state:shot.final_state,
      dialogue:shot.dialogue.map(line=>({speaker_id:line.speaker_id,text:line.text}))})),
  };
}
