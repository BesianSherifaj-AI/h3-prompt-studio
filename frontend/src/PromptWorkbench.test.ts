import { describe, expect, it } from 'vitest';
import { applyPromptDirection, directionText, promptReadiness } from './PromptWorkbench';
import { newShot, type Project } from './model';

const project = (): Project => ({schema_version:1,id:'guide',title:'Guide',mode:'t2va',duration:5,aspect_ratio:'16:9',profile:'director',authoring_mode:'manual',story:{text:'A person waves.',locked:true},style:{},assets:[],subjects:[],shots:[newShot(5)],soundscape:'',music:'',custom_instructions:''});
describe('Guided prompt writer', () => {
  it('keeps physical order and trims empty direction', () => {
    expect(directionText({opening:' holding a box ',action:'pass the box',ending:'receiver holds it',camera:'',preserve:''})).toBe('Opening: holding a box\nAction: pass the box\nEnding: receiver holds it');
  });
  it('updates previously inserted direction without accumulating contradictory endings', () => {
    const value=project(); value.story.text='A person waves. Exact speech: "Hello."';
    const direction={opening:'Standing',action:'wave',ending:'Hand raised',camera:'Static',preserve:''};
    applyPromptDirection(value,direction);
    applyPromptDirection(value,{...direction,ending:'Hand resting'});
    expect(value.story.text).toContain('Exact speech: "Hello."');
    expect(value.story.text).toContain('Ending: Hand resting');
    expect(value.story.text).not.toContain('Hand raised');
    expect(value.story.text.match(/Opening:/g)).toHaveLength(1);
  });
  it('identifies a missing ending without blocking an otherwise editable draft', () => {
    const checks=promptReadiness(project());
    expect(checks.find(check=>check.label==='Clear action')?.ready).toBe(true);
    expect(checks.find(check=>check.label==='Defined ending')?.ready).toBe(false);
  });
  it('flags dialogue which cannot fit comfortably and accepts a authored ending', () => {
    const value=project(); value.shots[0].dialogue=[{text:'one '.repeat(30)}]; value.shots[0].final_state='Hand rests at their side';
    expect(promptReadiness(value).find(check=>check.label==='Speakable dialogue')?.ready).toBe(false);
    expect(promptReadiness(value).find(check=>check.label==='Defined ending')?.ready).toBe(true);
  });
});
