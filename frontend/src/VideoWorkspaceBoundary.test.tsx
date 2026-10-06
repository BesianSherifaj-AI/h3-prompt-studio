import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import VideoWorkspace, { type VideoJob } from './VideoWorkspace';
import { newShot, type Project } from './model';
const project:Project={id:'video',schema_version:1,title:'One clip',mode:'t2va',duration:15,aspect_ratio:'16:9',profile:'concise',authoring_mode:'manual',story:{text:'A lantern moves',locked:true},style:{},assets:[],subjects:[],shots:[newShot(15)],soundscape:'',music:'',custom_instructions:''};
const take:VideoJob={id:'take',project_id:'video',status:'succeeded',video_url:'/video.mp4',can_continue:true,has_snapshot:true,continuation_source:'owned-state',can_reroll:true};
describe('Single video workspace boundary',()=>{
  it('keeps reroll, generation and review while excluding film and game navigation',()=>{
    const noop=()=>{};
    const html=renderToStaticMarkup(<VideoWorkspace singleClip project={project} promptReady busy={false} jobs={[take]} currentJob={take} storyId="legacy-story" activeEndpointId={take.id} storyClips={[take]} onSelectJob={noop} onGenerate={noop} onReroll={noop} onContinue={noop} onCombine={noop} onPlayGame={noop}/>);
    expect(html).toContain('Try another take');expect(html).toContain('Generate video');expect(html).toContain('Review this take');
    expect(html).not.toContain('Play from here');expect(html).not.toContain('Continue from this ending');expect(html).not.toContain('Save whole story');expect(html).not.toContain('Combine clips');
  });
});
