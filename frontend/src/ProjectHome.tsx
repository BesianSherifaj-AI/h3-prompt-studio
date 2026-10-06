import { useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Film, FolderOpen, LoaderCircle, Plus, Save, Search, Upload } from 'lucide-react';
import './ProjectHome.css';

export type ProjectSummary = {
  id: string; title: string; mode: string; duration: number; updated?: number;
  prompt_summary?: string; reference_count?: number; video_count?: number;
  last_review_verdict?: string | null;
};
export function visibleProjects(projects: ProjectSummary[], query: string, sort: string) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return projects.filter(p => words.every(word => `${p.title} ${p.prompt_summary || ''}`.toLocaleLowerCase().includes(word)))
    .sort((a, b) => sort === 'name' ? a.title.localeCompare(b.title) : (b.updated || 0) - (a.updated || 0));
}
export function projectStage(p: ProjectSummary) {
  if (p.video_count) return p.last_review_verdict === 'approved' || p.last_review_verdict === 'good' ? 'Approved take' : p.last_review_verdict === 'needs_changes' ? 'Review · needs changes' : 'Video ready to review';
  return p.prompt_summary?.trim() ? 'Draft · ready to write' : 'New draft';
}
function editedAt(timestamp?: number) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Saved locally';
}
export function ProjectBar({ title, status, busy, onTitle, onHome, onNew, onSave, onBackup }: {
  title: string; status: string; busy: boolean; onTitle: (value: string) => void;
  onHome: () => void; onNew: () => void; onSave: () => void; onBackup: () => void;
}) {
  return <div className="project-bar">
    <button className="project-back" onClick={onHome} disabled={busy}><ArrowLeft size={17}/>My videos</button>
    <div className="project-identity"><label htmlFor="active-project-name">Video name</label><input id="active-project-name" aria-label="Project name" maxLength={160} value={title} onChange={e => onTitle(e.target.value)} disabled={busy}/></div>
    <div className={`project-save-state ${status === 'Not saved' ? 'is-error' : ''}`} role="status" aria-live="polite">{status === 'Saved locally' ? <Check size={15}/> : status === 'Saving…' ? <LoaderCircle size={15} className="spin"/> : <Save size={15}/>}<span>{status === 'Saved locally' ? 'All changes saved' : status}</span></div>
    <div className="project-bar-actions"><button onClick={onSave} disabled={busy || status === 'Saving…'}><Save size={15}/>{status === 'Not saved' ? 'Retry save' : 'Save now'}</button><button onClick={onBackup} disabled={busy}>Backup & copy</button><button onClick={onNew} disabled={busy}><Plus size={16}/>New video</button></div>
  </div>;
}
export default function ProjectHome({ projects, currentId, loading, busy, error, onOpen, onNew, onImport, onReview, onRefresh }: {
  projects: ProjectSummary[]; currentId: string; loading: boolean; busy: boolean; error: string;
  onOpen: (id: string) => void; onNew: () => void; onImport: () => void; onReview: () => void; onRefresh: () => void;
}) {
  const [query, setQuery] = useState(''), [sort, setSort] = useState('recent');
  const visible = visibleProjects(projects, query, sort);
  return <main className="project-home">
    <header className="project-home-heading"><div><p className="home-eyebrow">VIDEO · ONE CLIP AT A TIME</p><h1>My videos</h1><p>Write, make and review a video up to 15 seconds. Pick up your work exactly where you left it.</p></div><button className="primary home-new" onClick={onNew} disabled={busy}><Plus size={19}/>New video</button></header>
    <div className="home-how"><span><b>1</b>Write your idea</span><ArrowRight size={15}/><span><b>2</b>Prepare the prompt</span><ArrowRight size={15}/><span><b>3</b>Make & review the video</span></div>
    <section className="home-library" aria-label="Saved video projects" aria-busy={loading}>
      <div className="home-library-heading"><div><h2>Saved work <span>{projects.length}</span></h2><p>Edits save automatically on this computer.</p></div><div className="home-library-actions"><button onClick={onImport} disabled={busy}><Upload size={16}/>Import backup</button><button onClick={onReview}>Review a local video</button></div></div>
      <div className="home-search"><label><Search size={17}/><input type="search" aria-label="Find a video project" placeholder="Find by name or idea…" value={query} onChange={e => setQuery(e.target.value)}/></label><label className="home-sort"><span>Sort</span><select aria-label="Sort video projects" value={sort} onChange={e => setSort(e.target.value)}><option value="recent">Recently edited</option><option value="name">Name</option></select></label></div>
      {error && <div className="home-error" role="alert">{error}<button onClick={onRefresh}>Try again</button></div>}
      {loading ? <p className="home-empty" role="status"><LoaderCircle size={20}/>Loading saved work…</p> : <div className="home-project-grid">{visible.map(p => <button className="home-project" key={p.id} onClick={() => onOpen(p.id)} disabled={busy}>
        <span className="home-project-top"><span className="home-project-icon"><Film size={23}/></span><small>{projectStage(p)}</small></span>
        <strong>{p.title || 'Untitled video'}</strong><span className="home-project-idea">{p.prompt_summary || 'Add an idea to begin this video.'}</span>
        <span className="home-project-meta">{p.duration}s · {p.reference_count || 0} photos · {p.video_count || 0} takes</span>
        <span className="home-project-bottom"><small>Edited {editedAt(p.updated)}</small><span>{p.id === currentId ? 'Resume' : 'Open'}<ArrowRight size={16}/></span></span>
      </button>)}</div>}
      {!loading && !visible.length && <div className="home-empty"><FolderOpen size={28}/><h3>{query ? 'No matching videos' : 'Start your first video'}</h3><p>{query ? 'Try another name or a word from your idea.' : 'Give it a name, write an idea, then prepare its prompt. You can return here any time.'}</p>{!query && <button className="primary" onClick={onNew}>New video</button>}</div>}
    </section>
    <p className="home-footnote">For a longer film, open Studio. For an interactive story, open Game. Each workspace keeps its own saved work.</p>
  </main>;
}
export function NewProjectForm({ busy, onCreate, onCancel }: { busy: boolean; onCreate: (value: {title: string; idea: string}) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(''), [idea, setIdea] = useState('');
  return <form className="new-project-form" onSubmit={e => { e.preventDefault(); if (!busy && title.trim()) onCreate({title: title.trim(), idea: idea.trim()}); }}>
    <label className="field"><span>Video name</span><input autoFocus required maxLength={160} placeholder="e.g. Lantern in the garden" value={title} disabled={busy} onChange={e => setTitle(e.target.value)}/></label>
    <label className="field"><span>Your idea <small>optional · you can write it later</small></span><textarea rows={4} maxLength={20000} value={idea} disabled={busy} placeholder="What happens at the start, what moves, and how does it end?" onChange={e => setIdea(e.target.value)}/></label>
    <p className="new-project-note">A separate video project, saved locally. Your other work stays in My videos. Start with a 5-second draft; choose up to 15 seconds in the editor.</p>
    <div className="modal-actions"><button type="button" onClick={onCancel} disabled={busy}>Cancel</button><button className="primary" type="submit" disabled={busy || !title.trim()}><Plus size={16}/>{busy ? 'Creating…' : 'Create video'}</button></div>
  </form>;
}
