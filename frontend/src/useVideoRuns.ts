import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { VideoJob } from './VideoWorkspace';

const ACTIVE = new Set(['preparing', 'queued', 'running', 'uncertain']);
export function useVideoRuns(projectId: string) {
  const [jobs, setJobs] = useState<VideoJob[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const currentProject = useRef(projectId); currentProject.current = projectId;
  const latestJobs = useRef<VideoJob[]>([]);
  const mutationRevision = useRef(0), listRevision = useRef(0);
  const sending = useRef(false);
  const request = useRef<{id:string;path:string;projectId:string}|null>(null);
  const reload = useCallback(async () => {
    const id = projectId;
    if (!id) return [];
    const startedMutation = mutationRevision.current, startedList = ++listRevision.current;
    const result = await api('/video/runs?project_id=' + encodeURIComponent(id));
    if (currentProject.current !== id) return [];
    // A response begun before a submission (or a newer refresh) cannot remove
    // that submitted run, restore an older player, or re-enable Generate.
    if (startedMutation !== mutationRevision.current || startedList !== listRevision.current)
      return latestJobs.current.filter(job=>job.project_id===id);
    const incoming:VideoJob[] = Array.isArray(result) ? result : result.runs || [];
    latestJobs.current=incoming;
    setJobs(incoming);
    setError('');
    const activeRequest = request.current;
    if (activeRequest && incoming.some((job:any) => job.request_id === activeRequest.id)) {
      request.current = null;
      try { sessionStorage.removeItem('h3-video-request:' + id); } catch { /* Storage may be unavailable. */ }
    }
    return incoming;
  }, [projectId]);
  useEffect(() => {
    if (!projectId) return;
    mutationRevision.current++;listRevision.current++;latestJobs.current=[];
    setJobs([]); setSelectedId(''); setError(''); request.current = null;
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
  }, [projectId, reload]);
  const submit = async (path:string, body:any, targetProject=projectId) => {
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
      const job:VideoJob = await api(path, { ...body, request_id:ticket.id });
      mutationRevision.current++;
      if(request.current?.id===ticket.id) request.current = null;
      try { sessionStorage.removeItem('h3-video-request:' + targetProject); } catch { /* Best effort cleanup. */ }
      if (currentProject.current === targetProject) {
        const incoming=[job,...latestJobs.current.filter(item=>item.project_id===targetProject&&item.id!==job.id)];
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
  const currentJob = jobs.find(job=>job.id===selectedId) || jobs[0] || null;
  const updateMetadata = async(job:VideoJob, changes:{title?:string;favorite?:boolean}) => {
    const targetProject=job.project_id;
    mutationRevision.current++;
    try {
      const updated:VideoJob=await api('/video/runs/'+job.id,changes,undefined,'PATCH');
      mutationRevision.current++;
      if(currentProject.current===targetProject) {
        // Merge the metadata only: a render can have advanced since PATCH.
        const incoming=latestJobs.current.map(item=>item.id===job.id?{...item,title:updated.title,favorite:updated.favorite}:item);
        latestJobs.current=incoming;setJobs(incoming);
      }
    }catch(error){mutationRevision.current++;throw error;}
  };
  return { jobs, currentJob, onSelectJob:(job:VideoJob)=>setSelectedId(job.id), reload, submit, submitting, error, updateMetadata,
    active:jobs.some(job=>ACTIVE.has(job.status)) };
}
