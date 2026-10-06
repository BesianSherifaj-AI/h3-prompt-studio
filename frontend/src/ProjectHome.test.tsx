import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ProjectHome, { NewProjectForm, ProjectBar, projectStage, visibleProjects, type ProjectSummary } from './ProjectHome';
const projects: ProjectSummary[] = [{id:'a',title:'Garden lantern',mode:'t2va',duration:5,updated:1,prompt_summary:'A red lantern moves'}, {id:'b',title:'Boat',mode:'t2va',duration:15,updated:2,video_count:1,last_review_verdict:'needs_changes'}];
describe('Video project home', () => {
  it('finds separate words across name and idea and returns a sorted copy', () => {
    expect(visibleProjects(projects,'GARDEN red','recent').map(p=>p.id)).toEqual(['a']);
    expect(visibleProjects(projects,'','recent').map(p=>p.id)).toEqual(['b','a']);
    expect(visibleProjects(projects,'','name').map(p=>p.id)).toEqual(['b','a']);
    expect(projects.map(p=>p.id)).toEqual(['a','b']);
  });
  it('distinguishes rendered footage from approval and a new draft', () => {
    expect(projectStage(projects[1])).toBe('Review · needs changes');
    expect(projectStage({...projects[1],last_review_verdict:null})).toBe('Video ready to review');
    expect(projectStage({...projects[0],prompt_summary:''})).toBe('New draft');
  });
  it('gives saved work a direct open action and explains workspace boundaries', () => {
    const noop=()=>{};
    const html=renderToStaticMarkup(<ProjectHome projects={projects} currentId="a" loading={false} busy={false} error="" onOpen={noop} onNew={noop} onImport={noop} onReview={noop} onRefresh={noop}/>);
    expect(html).toContain('My videos'); expect(html).toContain('Resume'); expect(html).toContain('Open'); expect(html).toContain('For a longer film');
    expect(html).not.toContain('T2VA');
  });
  it('does not claim a failed save succeeded and requires a named new project', () => {
    const noop=()=>{};
    const bar=renderToStaticMarkup(<ProjectBar title="A" status="Not saved" busy={false} onTitle={noop} onHome={noop} onNew={noop} onSave={noop} onBackup={noop}/>);
    expect(bar).toContain('Retry save'); expect(bar).not.toContain('All changes saved');
    const form=renderToStaticMarkup(<NewProjectForm busy={false} onCreate={noop} onCancel={noop}/>);
    expect(form).toContain('required=""'); expect(form).toContain('Create video'); expect(form).toContain('My videos');
  });
});
