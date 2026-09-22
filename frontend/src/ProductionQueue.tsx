import { useEffect, useRef, useState } from 'react';
import { Clapperboard, Download, LoaderCircle, Play, RefreshCw, Square, X } from 'lucide-react';
import { api } from './api';
import './ProductionQueue.css';

export type ProductionItem = {
  index: number; project_id: string; title: string; status: string; stage?: string;
  error?: string; run_id?: string; video_url?: string; duration?: number;
};
export type ProductionBatch = {
  id: string; name: string; status: string; error?: string; items: ProductionItem[];
  completed: number; total: number; created_at?: number;
};
type SavedProject = { id: string; title: string };
const TIMEOUT = { timeoutMs: 20_000 };
export function productionProgress(batch: ProductionBatch) {
  const total = Math.max(0, batch.total || batch.items?.length || 0);
  const completed = Math.min(total, Math.max(0, batch.completed || 0));
  return { total, completed, percent: total ? Math.round(100 * completed / total) : 0 };
}
export function productionActions(status: string) {
  return { start: status === 'draft', resume: ['paused', 'needs_attention'].includes(status), cancel: status === 'running' };
}
export function productionStatus(status: string) {
  return ({ draft: 'Ready to start', running: 'In production', paused: 'Paused', needs_attention: 'Needs attention',
    cancelled: 'Stopped', succeeded: 'Rendered · review takes', queued: 'Waiting', failed: 'Failed',
    preparing: 'Preparing', uncertain: 'Checking submission' } as Record<string, string>)[status] || status.replaceAll('_', ' ');
}

export function ProductionBatchView({ batch, pending = false, onAction, onRetry, onPreview, onOpenProject, onExport }: {
  batch: ProductionBatch; pending?: boolean; onAction: (action: string) => void;
  onRetry: (index: number) => void; onPreview: (item: ProductionItem) => void; onOpenProject: (id: string) => void;
  onExport?: (kind: 'film' | 'clips') => void;
}) {
  const progress = productionProgress(batch), actions = productionActions(batch.status);
  return <section className="production-batch" aria-label={batch.name}>
    <div className="production-batch-heading"><div><h3>{batch.name}</h3><p role="status">{productionStatus(batch.status)} · {progress.completed}/{progress.total} rendered</p></div>
      <div className="production-buttons">
        {actions.start && <button disabled={pending} onClick={() => onAction('start')}><Play size={15}/>Start batch</button>}
        {actions.resume && <button disabled={pending} onClick={() => onAction('resume')}><Play size={15}/>Resume remaining</button>}
        {actions.cancel && <button disabled={pending} onClick={() => onAction('cancel')}><Square size={15}/>Stop queue</button>}
        {progress.completed > 0 && <a href={`/api/production/${batch.id}/playlist`} download><Download size={15}/>Playlist</a>}
        {batch.status === 'succeeded' && progress.completed === progress.total && progress.total > 0 && onExport && <>
          <button disabled={pending} onClick={() => onExport('film')}><Clapperboard size={15}/>Export film</button>
          <button disabled={pending} onClick={() => onExport('clips')}><Download size={15}/>Export clips ZIP</button>
        </>}
      </div>
    </div>
    <progress max={progress.total || 1} value={progress.completed} aria-label="Rendered videos"/>
    {batch.error && <p className="production-error" role="alert">{batch.error}</p>}
    <ol className="production-items">{batch.items?.map(item => <li key={item.index} className={`production-item is-${item.status}`}>
      <span className="production-index">{item.index + 1}</span>
      <div className="production-item-copy"><button className="production-project-link" onClick={() => onOpenProject(item.project_id)}>{item.title || `Video ${item.index + 1}`}</button>
        <small>{productionStatus(item.status)}{item.stage && item.stage !== item.status ? ` · ${item.stage}` : ''}{item.duration ? ` · ${item.duration}s` : ''}</small>
        {item.error && <p className="production-error">{item.error}</p>}</div>
      {item.video_url && <button aria-label={`Play ${item.title || `video ${item.index + 1}`}`} onClick={() => onPreview(item)}><Play size={15}/><span>Review</span></button>}
      {['failed', 'cancelled'].includes(item.status) && <button disabled={pending || batch.status === 'running'} onClick={() => onRetry(item.index)}>Retry</button>}
    </li>)}</ol>
    <p className="production-hint">Rendered videos remain takes for review. The queue does not accept Game actions or change story state.</p>
  </section>;
}

export default function ProductionQueue({ active, currentProjectId, currentProjectTitle, onSaveCurrent, onOpenProject }: {
  active: boolean; currentProjectId: string; currentProjectTitle: string;
  onSaveCurrent: () => Promise<unknown>; onOpenProject: (id: string) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false), [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [selectedId, setSelectedId] = useState(''), [batch, setBatch] = useState<ProductionBatch | null>(null);
  const [projects, setProjects] = useState<SavedProject[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState(''), [search, setSearch] = useState(''), [error, setError] = useState('');
  const [pending, setPending] = useState(false), [preview, setPreview] = useState<ProductionItem | null>(null);
  const [pendingLabel, setPendingLabel] = useState('Saving queue changes…');
  const [exported, setExported] = useState<{ batchId: string; url: string; filename: string; review_status: string } | null>(null);
  const refreshBusy = useRef(false), createRequest = useRef({ signature: '', id: '' });
  const activeId = useRef(selectedId); activeId.current = selectedId;

  const refresh = async () => {
    if (refreshBusy.current) return;
    refreshBusy.current = true;
    try {
      const value = await api('/production', undefined, undefined, undefined, TIMEOUT);
      setBatches(value.batches || []);
      const id = activeId.current || value.batches?.[0]?.id;
      if (id) {
        const current = await api(`/production/${id}`, undefined, undefined, undefined, TIMEOUT);
        if (!activeId.current || activeId.current === id) { setSelectedId(id); setBatch(current); }
      }
    } finally { refreshBusy.current = false; }
  };
  useEffect(() => {
    if (!active) return;
    let alive = true;
    const poll = () => {
      if (document.visibilityState !== 'hidden') void refresh().catch(e => { if (alive) setError(e.message); });
    };
    poll(); const timer = setInterval(poll, open ? 5000 : 15000);
    return () => { alive = false; clearInterval(timer); };
  }, [active, open]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    api('/projects?workspace=studio', undefined, undefined, undefined, TIMEOUT)
      .then(value => { if (alive) setProjects(value); }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [open, currentProjectId]);
  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    api(`/production/${selectedId}`, undefined, undefined, undefined, TIMEOUT)
      .then(value => { if (alive) setBatch(value); }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [selectedId]);
  const perform = async (fn: () => Promise<unknown>, label = 'Saving queue changes…') => {
    if (pending) return;
    setPending(true); setPendingLabel(label); setError('');
    try { await fn(); await refresh(); }
    catch (e) { setError((e as Error).message); await refresh().catch(() => {}); }
    finally { setPending(false); }
  };
  const create = (ids: string[]) => perform(async () => {
    if (ids.includes(currentProjectId)) await onSaveCurrent();
    const payload = { name: name.trim() || (ids.length === 1 ? currentProjectTitle || 'Studio video' : 'Studio batch'), project_ids: ids };
    const signature = JSON.stringify(payload);
    if (createRequest.current.signature !== signature) createRequest.current = { signature, id: crypto.randomUUID() };
    const created = await api('/production', { ...payload, request_id: createRequest.current.id }, undefined, undefined, TIMEOUT);
    activeId.current = created.id; setSelectedId(created.id); setBatch(created); setSelected([]); setName('');
    createRequest.current = { signature: '', id: '' };
  });
  const visible = projects.filter(item => item.title?.toLowerCase().includes(search.toLowerCase()));
  const running = batches.find(item => item.status === 'running');
  return <details className="production-queue" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><Clapperboard size={17}/><strong>Production queue</strong><span>{running ? `${running.completed}/${running.total} rendered` : `${batches.length} saved batches`}</span></summary>
    <div className="production-content">
      <div className="production-toolbar"><p>Make multiple videos with your saved projects and their render settings. Work runs one video at a time.</p>
        <button disabled={pending} aria-label="Refresh production queue" onClick={() => void perform(refresh)}><RefreshCw size={16}/></button></div>
      {error && <p className="production-error" role="alert">{error}</p>}
      {pending && <p className="production-hint" role="status"><LoaderCircle size={15}/>{pendingLabel}</p>}
      <details className="production-create"><summary>Create a batch</summary>
        <label>Batch name<input value={name} onChange={event => setName(event.target.value)} placeholder="Film scenes or short videos" maxLength={120}/></label>
        <button disabled={pending} onClick={() => void create([currentProjectId])}>Queue current project</button>
        <label>Find saved projects<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search by title"/></label>
        <div className="production-selection"><button disabled={pending || !visible.length} onClick={() => setSelected(current => [...new Set([...current, ...visible.map(item => item.id)])])}>Select matching ({visible.length})</button><button disabled={pending || !selected.length} onClick={() => setSelected([])}>Clear</button></div>
        <div className="production-projects">{visible.map(item => <label key={item.id}><input type="checkbox" checked={selected.includes(item.id)} disabled={pending} onChange={event => setSelected(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))}/><span>{item.title || 'Untitled project'}</span></label>)}</div>
        <button disabled={pending || !selected.length} onClick={() => void create(selected)}>Create batch · {selected.length} selected</button>
      </details>
      {batches.length > 0 && <label className="production-picker">Saved batch<select value={selectedId} onChange={event => { setBatch(null); setPreview(null); setSelectedId(event.target.value); }}>
        {batches.map(item => <option key={item.id} value={item.id}>{item.name} · {productionStatus(item.status)}</option>)}</select></label>}
      {batch && batch.id === selectedId && <ProductionBatchView batch={batch} pending={pending}
        onAction={action => void perform(async () => { await api(`/production/${batch.id}/${action}`, {}, undefined, undefined, TIMEOUT); })}
        onRetry={index => void perform(async () => { await api(`/production/${batch.id}/items/${index}/retry`, { request_id: crypto.randomUUID() }, undefined, undefined, TIMEOUT); })}
        onExport={kind => void perform(async () => {
          const result = await api(`/production/${batch.id}/export`, { kind }, undefined, undefined, { timeoutMs: 600_000 });
          setExported({ ...result, batchId: batch.id });
        }, 'Preparing your export. Large batches can take a few minutes…')}
        onPreview={setPreview} onOpenProject={id => void perform(() => onOpenProject(id))}/>}
      {exported?.batchId === selectedId && <p className="production-export" role="status"><a href={exported.url} download={exported.filename}><Download size={15}/>{exported.filename}</a><small>Export ready. Review the video before publishing.</small></p>}
      {preview?.video_url && <section className="production-preview" aria-label="Review rendered video"><div><strong>{preview.title}</strong><button aria-label="Close video preview" onClick={() => setPreview(null)}><X size={16}/></button></div><video controls playsInline preload="metadata" src={preview.video_url}/><a href={preview.video_url} download>Save video</a></section>}
      {!batches.length && <p className="production-hint">Create a batch from saved Studio projects. Starting a batch uses your local model and ComfyUI.</p>}
    </div>
  </details>;
}
