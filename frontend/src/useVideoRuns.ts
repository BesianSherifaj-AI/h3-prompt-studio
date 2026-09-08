import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { VideoJob } from './VideoWorkspace';

const ACTIVE = new Set(['preparing', 'queued', 'running', 'uncertain']);
export type VideoRunScope = {storyId?: string; jobs?: VideoJob[]};
const selectionKey = (scope: string) => 'h3-video-selection:' + scope;
export function readVideoSelection(scope: string, storage?: Pick<Storage, 'getItem'>): string {
  try { const value=(storage || localStorage).getItem(selectionKey(scope)); return value && value.length <= 200 ? value : ''; } catch { return ''; }
}
export function writeVideoSelection(scope: string, id: string, storage?: Pick<Storage, 'setItem'>) {
  try { if(scope && id && id.length <= 200) (storage || localStorage).setItem(selectionKey(scope), id); } catch { /* Selection still works without browser storage. */ }
}
/** First occurrence wins, so a fresh poll can replace a cached run without losing other clips. */
export function mergeVideoRuns(...lists: VideoJob[][]) {
  const seen = new Set<string>();
  return lists.flat().filter(job => job?.id && !seen.has(job.id) && !!seen.add(job.id));
}
export async function sendVideoRun(path:string, body:any, requestId:string, onTicket?: (id:string)=>void, call:typeof api=api):Promise<VideoJob> {
  onTicket?.(requestId);
  return call(path,{...body,request_id:requestId});
}
export function useVideoRuns(projectId: string, scope: VideoRunScope = {}) {
  const scopeId = scope.storyId ? 'story:' + scope.storyId : 'project:' + projectId;
  const [jobs, setJobs] = useState<VideoJob[]>([]);
  const [selection, setSelection] = useState(() => ({scope:scopeId, id:readVideoSelection(scopeId)}));
  const currentScope = useRef(scopeId); currentScope.current = scopeId;
  const previousScope = useRef(scopeId);
  const suppliedJobs = useRef(scope.jobs || []); suppliedJobs.current = scope.jobs || [];
  const storyId = scope.storyId;
  const setSelectedId = (id:string) => {writeVideoSelection(scopeId,id); setSelection({scope:scopeId,id});};
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const currentProject = useRef(projectId); currentProject.current = projectId;
  const latestJobs = useRef<VideoJob[]>([]);
  const metadataOverrides = useRef(new Map<string, {title?:string;favorite?:boolean}>());
  const mutationRevision = useRef(0), listRevision = useRef(0);
  const sending = useRef(false);
  const request = useRef<{id:string;path:string;projectId:string}|null>(null);
  const reload = useCallback(async () => {
    const id = projectId, requestedScope = scopeId;
    if (!id) return [];
    const startedMutation = mutationRevision.current, startedList = ++listRevision.current;
    const result = await api('/video/runs?project_id=' + encodeURIComponent(id));
    if (currentProject.current !== id || currentScope.current !== requestedScope) return [];
    // A response begun before a submission (or a newer refresh) cannot remove
    // that submitted run, restore an older player, or re-enable Generate.
    if (startedMutation !== mutationRevision.current || startedList !== listRevision.current)
      return latestJobs.current.filter(job=>job.project_id===id);
    const incoming:VideoJob[] = Array.isArray(result) ? result : result.runs || [];
    const merged = storyId ? mergeVideoRuns(incoming, suppliedJobs.current.filter(job=>job.project_id!==id), latestJobs.current.filter(job=>job.project_id!==id)) : incoming;
    latestJobs.current=merged;
    setJobs(merged);
    setError('');
    const activeRequest = request.current;
    if (activeRequest && merged.some((job:any) => job.request_id === activeRequest.id)) {
      request.current = null;
      try { sessionStorage.removeItem('h3-video-request:' + activeRequest.projectId); } catch { /* Storage may be unavailable. */ }
    }
    return merged;
  }, [projectId, scopeId, storyId]);
  useEffect(() => {
    if (!projectId) return;
    mutationRevision.current++;listRevision.current++;
    if(previousScope.current !== scopeId) {
      previousScope.current=scopeId; latestJobs.current=[]; setJobs([]);
      metadataOverrides.current.clear();
      setSelection({scope:scopeId,id:readVideoSelection(scopeId)});
    }
    setError(''); request.current = null;
    try {
      const saved = sessionStorage.getItem('h3-video-request:' + projectId);
      if (saved) request.current = JSON.parse(saved);
    } catch { /* No pending action to restore. */ }
    let alive = true, timer:ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const incoming = await reload();
        if (alive) timer = setTimeout(poll, incoming.some(job=>ACTIVE.has(job.status)) ? 1500 : 6000);
      } catch (e) {
        if (alive) { setError((e as Error).message); timer = setTimeout(poll, 6000); }
      }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [projectId, scopeId, reload]);
  const submit = async (path:string, body:any, targetProject=projectId, onTicket?: (id:string)=>void) => {
    if (sending.current) return;
    const previous = request.current;
    if (previous && (previous.path !== path || previous.projectId !== targetProject))
      throw new Error('The previous request has not been confirmed. Check its result before starting another action.');
    const ticket = previous || { id:crypto.randomUUID(), path, projectId:targetProject };
    mutationRevision.current++;
    request.current = ticket;
    try { sessionStorage.setItem('h3-video-request:' + targetProject, JSON.stringify(ticket)); } catch { /* Backend still deduplicates. */ }
    sending.current = true; setSubmitting(true); setError('');
    try {
      const job=await sendVideoRun(path,body,ticket.id,onTicket);
      mutationRevision.current++;
      if(request.current?.id===ticket.id) request.current = null;
      try { sessionStorage.removeItem('h3-video-request:' + targetProject); } catch { /* Best effort cleanup. */ }
      if (currentScope.current === scopeId && (currentProject.current === targetProject || !!storyId)) {
        const incoming=mergeVideoRuns([job], latestJobs.current.filter(item=>storyId || item.project_id===targetProject), suppliedJobs.current);
        latestJobs.current=incoming;setJobs(incoming);
        setSelectedId(job.id);
      }
      return job;
    } catch (e) {
      mutationRevision.current++;
      // A definite rejection can be corrected with a fresh request. A lost
      // response keeps the same UUID so reconnecting cannot duplicate a render.
      if(e instanceof ApiError && e.status>=400 && e.status<500){
        if(request.current?.id===ticket.id) request.current=null;
        try { sessionStorage.removeItem('h3-video-request:' + targetProject); } catch { /* Best effort cleanup. */ }
      }
      setError((e as Error).message);
      await reload().catch(()=>{});
      throw e;
    } finally { sending.current = false; setSubmitting(false); }
  };
  const scopedJobs = previousScope.current === scopeId ? jobs : [];
  const visibleJobs = (storyId ? mergeVideoRuns(scopedJobs.filter(job=>job.project_id===projectId), suppliedJobs.current, scopedJobs) : scopedJobs)
    .filter(job=>storyId ? !job.story_id || job.story_id===storyId : job.project_id===projectId)
    .map(job=>metadataOverrides.current.has(job.id) ? {...job,...metadataOverrides.current.get(job.id)} : job);
  const selectedId = selection.scope === scopeId ? selection.id : readVideoSelection(scopeId);
  const currentJob = visibleJobs.find(job=>job.id===selectedId) || visibleJobs[0] || null;
  const updateMetadata = async(job:VideoJob, changes:{title?:string;favorite?:boolean}) => {
    const targetProject=job.project_id;
    mutationRevision.current++;
    try {
      const updated:VideoJob=await api('/video/runs/'+job.id,changes,undefined,'PATCH');
      mutationRevision.current++;
      if(currentScope.current===scopeId && (currentProject.current===targetProject || !!storyId)) {
        metadataOverrides.current.set(job.id, {title:updated.title,favorite:updated.favorite});
        // Merge the metadata only: a render can have advanced since PATCH.
        const incoming=latestJobs.current.map(item=>item.id===job.id?{...item,title:updated.title,favorite:updated.favorite}:item);
        latestJobs.current=incoming;setJobs(incoming);
      }
    }catch(error){mutationRevision.current++;throw error;}
  };
  return { jobs:visibleJobs, currentJob, onSelectJob:(job:VideoJob)=>{if(visibleJobs.some(item=>item.id===job.id))setSelectedId(job.id);}, reload, submit, submitting, error, updateMetadata,
    active:visibleJobs.some(job=>ACTIVE.has(job.status)) };
}
