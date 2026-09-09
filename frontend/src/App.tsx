import React, { useEffect, useRef, useState, useId } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  Check,
  ChevronDown,
  Clapperboard,
  Copy,
  Download,
  Eye,
  Film,
  FolderOpen,
  ImagePlus,
  Layers,
  Link2,
  LoaderCircle,
  Lock,
  MessageSquare,
  Mic,
  Monitor,
  MoreHorizontal,
  Music,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Video,
  WandSparkles,
  X,
  AlertCircle,
  SlidersHorizontal,
  Camera,
  BookOpen,
  Undo2,
  PanelRightClose,
} from "lucide-react";
import { api, setToken, downloadText } from "./api";
import {
  Project,
  Asset,
  Shot,
  uid,
  newShot,
  retime,
  switchMode,
  MODES,
  ROLES,
  PROFILE_OPTIONS,
} from "./model";
import { createBridge } from "./bridge";
import { makeBridgeSnapshot, matchesBridgeSnapshot } from "./bridgeSnapshot";
import MotionTools from "./MotionTools";
import MotionLab from "./MotionLab";
import FilesOutputs from "./FilesOutputs";
import SimpleStudio from "./SimpleStudio";
import SceneContinuity from "./SceneContinuity";
import { pruneSceneActors } from "./sceneContinuityState";
import ComfyPanel from './ComfyPanel';
import ModelPicker, { residentModelOptions } from './ModelPicker';
import ContinuationLinkReview from './ContinuationLinkReview';
import VideoWorkspace, { type VideoJob, type ContinuationSuggestions } from './VideoWorkspace';
import { useVideoRuns } from './useVideoRuns';
import { useStudioStoryLinks, studioLinkDefinitive } from './useStudioStoryLinks';
import GameStudio from './GameStudio';
import { ContinuationPlanner } from './TimelinePlanner';
import './WorkspaceModes.css';
import { completedVideoStory } from './videoStory';
import { continuationRenderSettings, type ContinuationRenderPreset } from './quickPreview';
import { createContinuation } from './timelineHelpers';
import { clearContinuationLink, createLinkedContinuation, parseContinuationLink, validateContinuationAvailability, type ContinuationLink, type ContinuationReviewValues } from './continuationLink';
import { ensurePromptTags, replacePhoto } from './tags';
import { prepareSimpleProject, simpleIssues, simpleInstructions, setSimpleMode, setKeyframe } from "./simple";

const EMPTY_COMPILE = {
  prompt: "",
  valid: false,
  issues: [],
  references: [],
  timeline: [],
};
function promptDraftKey(project:Project) {
  const value=structuredClone(project);
  delete value.comfy_render;delete value.simple_generation;
  if(value.simple)delete value.simple.next_clip_draft;
  return JSON.stringify(value);
}
const choices = (values: string[]) => values.map((v) => [v, v]);
function Select({
  label,
  value,
  onChange,
  options,
  className = "",
  title,
  ariaLabel,
}: any) {
  return (
    <label className={"field " + className} title={title}>
      {label && <span>{label}</span>}
      <select
        aria-label={ariaLabel}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
      >
        {value != null &&
          !options.some(([v]: any) => String(v) === String(value)) && (
            <option value={value}>{value || "Not specified"}</option>
          )}
        {options.map(([v, t]: any) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </select>
    </label>
  );
}
function Input({
  label,
  value,
  onChange,
  placeholder = "",
  type = "text",
  ...props
}: any) {
  return (
    <label className="field">
      {label && <span>{label}</span>}
      <input
        type={type}
        value={value ?? ""}
        onChange={(e) =>
          onChange(type === "number" ? Number(e.target.value) : e.target.value)
        }
        placeholder={placeholder}
        {...props}
      />
    </label>
  );
}
function Area({
  label,
  value,
  onChange,
  placeholder = "",
  rows = 3,
  children,
}: any) {
  const fieldId = useId();
  return (
    <div className="field">
      {label && (
        <div className="field-heading">
          <label htmlFor={fieldId}>{label}</label>
          {children}
        </div>
      )}
      <textarea
        id={fieldId}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
      />
    </div>
  );
}
function IconButton({
  icon: Icon,
  title,
  onClick,
  disabled = false,
  className = "",
}: any) {
  return (
    <button
      className={"icon-button " + className}
      aria-label={title}
      title={title}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon size={15} />
    </button>
  );
}
function Modal({ title, subtitle, onClose, children, wide = false }: any) {
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <section
        className={"modal " + (wide ? "wide" : "")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <IconButton icon={X} title="Close dialog" onClick={onClose} />
        </header>
        {children}
      </section>
    </div>
  );
}

export default function App() {
  const [workspaceMode, setWorkspaceMode] = useState<'studio'|'game'>(()=>{try{return new URLSearchParams(window.location.search).has('game') || localStorage.getItem('h3-workspace-mode')==='game'?'game':'studio';}catch{return 'studio';}});
  const [gameSource, setGameSource] = useState<string|undefined>();
  const [gameSourceKey, setGameSourceKey] = useState(0);
  const [studioStoryId,setStudioStoryId] = useState('');
  const [studioStory,setStudioStory] = useState<any>(null);
  const studioStoryRevision = useRef(0);
  const studioStoryIdRef = useRef(studioStoryId); studioStoryIdRef.current=studioStoryId;
  const [view, setView] = useState<"simple" | "advanced">(() => {
    try { return localStorage.getItem("h3-studio-view") === "advanced" ? "advanced" : "simple"; }
    catch { return "simple"; }
  });
  const [simpleResult, setSimpleResult] = useState<any>(null);
  const videoAction = useRef(false);
  const [p, setP] = useState<Project | null>(null),
    [saved, setSaved] = useState("Saved locally"),
    [projects, setProjects] = useState<any[]>([]),
    [settings, setSettings] = useState<any>({}),
    [personas, setPersonas] = useState<any[]>([]);
  const [compileResult, setCompileResult] = useState<any>(null),
    [connection, setConnection] = useState<any>({}),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [selectedAsset, setSelectedAsset] = useState(""),
    [selectedShot, setSelectedShot] = useState(""),
    [inspector, setInspector] = useState("camera"),
    [modal, setModal] = useState(""),
    [proposal, setProposal] = useState<any>(null),
    [outputTab, setOutputTab] = useState("prompt"),
    [showOutput, setShowOutput] = useState(true);
  const [showAI, setShowAI] = useState(false),
    [showRefs, setShowRefs] = useState(false);
  const [continuationReview, setContinuationReview] = useState<{link:ContinuationLink|null;source:Project|null;loading:boolean;error:string}|null>(null);
  const continuationRequest = useRef(0);
  const bridgeSnapshot = useRef<any>(null),
    bridgeProject = useRef(""),
    bridgePending = useRef<any>(null);
  const [instructions, setInstructions] = useState(""),
    [vision, setVision] = useState(true),
    [aiPersona, setAiPersona] = useState("universal"),
    [bridgeImport, setBridgeImport] = useState<any>(null),
    [bridgeConnected, setBridgeConnected] = useState(false),
    [systemPrompt, setSystemPrompt] = useState(""),
    [history, setHistory] = useState<Project[]>([]);
  const fileInput = useRef<HTMLInputElement>(null),
    projectInput = useRef<HTMLInputElement>(null),
    bridge = useRef<any>(null),
    projectRef = useRef<Project | null>(null),
    init = useRef(false),
    saveTimer = useRef<any>(null),
    saveInFlight = useRef<Promise<any>|null>(null);
  projectRef.current = p;
  const videos = useVideoRuns(p?.id || '', {storyId:studioStoryId || undefined,jobs:studioStory?.id===studioStoryId?studioStory.jobs:[]});
  const studioLinks = useStudioStoryLinks(story=>{
    if(studioStoryIdRef.current===story.id){studioStoryRevision.current++;setStudioStory(story);}
    setError(current=>current.startsWith('Your video is saved. Reconnecting its story link')?'':current);
  },setError);
  useEffect(()=>{try{localStorage.setItem('h3-workspace-mode',workspaceMode);}catch{/* Optional storage. */}},[workspaceMode]);
  useEffect(()=>{
    if(!p)return;
    let storyId=p.story_session_id || '';
    try{storyId ||= localStorage.getItem('h3-studio-story:'+p.id)||'';
    }catch{/* Recoverable without browser storage. */}
    studioLinks.restore(p.id);
    setStudioStoryId(storyId);
    if(!storyId)setStudioStory(null);
  },[p?.id]);
  useEffect(()=>{
    if(!studioStoryId)return;
    let alive=true;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{const revision=studioStoryRevision.current;try{const value=await api('/stories/'+studioStoryId);if(alive&&revision===studioStoryRevision.current)setStudioStory(value);}catch{/* Preserve last known story while reconnecting. */}
      if(alive)timer=setTimeout(poll,2500);};void poll();return()=>{alive=false;clearTimeout(timer);};
  },[studioStoryId]);
  const queueProjectSave = (value:Project) => {
    const snapshot=structuredClone(value);
    const next=(saveInFlight.current||Promise.resolve()).catch(()=>{}).then(()=>api('/projects',snapshot));
    saveInFlight.current=next;
    return next;
  };
  const draftKey = p ? JSON.stringify(p) : "";
  const compileFresh = compileResult?.draft === draftKey;
  const compiled = compileFresh ? compileResult.result : EMPTY_COMPILE;
  const update = (fn: (draft: Project) => void) =>
    setP((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      fn(next);
      ensurePromptTags(next);
      if (promptDraftKey(next) !== promptDraftKey(current)) delete next.simple_generation;
      return next;
    });
  const updateShot = (field: string, value: any) =>
    update((d) => {
      const s = d.shots.find((s) => s.id === selectedShot) || d.shots[0];
      if (s) (s as any)[field] = value;
    });
  const toast = (s: string) => {
    setNotice(s);
    setTimeout(() => setNotice(""), 6500);
  };
  const refresh = async () => {
    try {
      setConnection(await api("/connections"));
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const run = async (label: string, fn: () => Promise<any>, rethrow = false) => {
    if (busy) return;
    setBusy(label);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
      if(rethrow) throw e;
    } finally {
      setBusy("");
      refresh();
    }
  };
  const loadContinuationReview = async (link:ContinuationLink) => {
    const request = ++continuationRequest.current;
    setContinuationReview(current=>({link,source:current?.link?.projectId===link.projectId?current.source:null,loading:true,error:''}));
    const [projectResult,catalogResult] = await Promise.allSettled([api('/projects/'+link.projectId),api('/comfy/options')]);
    if (request !== continuationRequest.current) return;
    try {
      if(projectResult.status==='rejected') throw new Error('The source Studio project is unavailable. Open the saved result from its original project, then try again.');
      if(catalogResult.status==='rejected') throw new Error('The saved clip could not be checked. Open ComfyUI Desktop and choose Check again.');
      validateContinuationAvailability(link,projectResult.value,catalogResult.value);
      setContinuationReview({link,source:projectResult.value,loading:false,error:''});
    } catch(e) {
      setContinuationReview(current=>({link,source:current?.source?.id===link.projectId?current.source:null,loading:false,error:(e as Error).message}));
    }
  };
  const closeContinuationReview = () => {
    continuationRequest.current++;
    setContinuationReview(null);
    window.history.replaceState(window.history.state,'',clearContinuationLink(window.location.href));
  };

  useEffect(() => {
    try { localStorage.setItem("h3-studio-view", view); } catch { /* Private browsing can disable storage. */ }
  }, [view]);
  useEffect(() => {
    api("/bootstrap")
      .then((data) => {
        setToken(data.token);
        ensurePromptTags(data.project);
        setP(data.project);
        setProjects(data.projects);
        setSettings(data.settings);
        setPersonas(data.personas);
        setAiPersona(data.settings.persona || "universal");
        setSelectedShot(data.project.shots[0]?.id || "");
        setSelectedAsset(data.project.assets[0]?.id || "");
        init.current = true;
        bridge.current = createBridge({
          resourceToken: data.resource_token,
          onImport: (payload: any) =>
            new Promise<void>((resolve, reject) => {
              bridgePending.current = { resolve, reject };
              setBridgeImport(payload);
              setBridgeConnected(true);
            }),
          onApplied: () => toast("Prompt applied to your ComfyUI node."),
          onTransferApplied: () => toast('Opened a new workflow in ComfyUI. Review it, then press Run.'),
          onError: (message: any) =>
            setError(
              typeof message === "string"
                ? message
                : message.message || "ComfyUI bridge error.",
            ),
        });
        if (bridge.current?.context) setBridgeConnected(true);
        try {
          const link=parseContinuationLink(window.location.search);
          if(link)void loadContinuationReview(link);
        } catch(e) {
          setContinuationReview({link:null,source:null,loading:false,error:(e as Error).message});
        }
        refresh();
      })
      .catch((e) => setError(e.message));
    return () => bridge.current?.dispose?.();
  }, []);
  useEffect(() => {
    if (!p || !init.current) return;
    let active = true;
    const draft = JSON.stringify(p);
    setSaved("Saving…");
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(
      () => {
        const request=queueProjectSave(p);
        request
          .then(() => { if(active && JSON.stringify(projectRef.current)===draft) setSaved("Saved locally"); })
          .catch((e) => {
            if(!active || JSON.stringify(projectRef.current)!==draft) return;
            setSaved("Not saved");
            setError(e.message);
          });
      },
      800,
    );
    const compilerTimer = setTimeout(
      () =>
        api("/compile", { project: p })
          .then((result) => {
            if (active && JSON.stringify(projectRef.current) === draft)
              setCompileResult({ draft, result });
          })
          .catch((e) => {
            if (active) setError(e.message);
          }),
      250,
    );
    return () => {
      active = false;
      clearTimeout(saveTimer.current);
      clearTimeout(compilerTimer);
    };
  }, [p]);
  useEffect(() => {
    const t = setInterval(() => {
      if (busy || videos.active || modal === "connections") refresh();
    }, 3000);
    return () => clearInterval(t);
  }, [busy, videos.active, modal]);
  useEffect(() => {
    if (p && !p.shots.some((s) => s.id === selectedShot))
      setSelectedShot(p.shots[0]?.id || "");
  }, [p?.shots, selectedShot]);
  useEffect(() => {
    setHistory([]);
    setProposal(null);
    setSimpleResult(null);
    const persisted =
      p?.assistant_instructions ?? p?.demo_metadata?.assistant_instructions;
    setInstructions(typeof persisted === "string" ? persisted : "");
  }, [p?.id]);

  if (!p)
    return (
      <div className="boot">
        <div className="logo-mark">H3</div>
        <h2>Prompt Studio</h2>
        <p>{error || "Opening your local workspace…"}</p>
        {error && <button onClick={() => location.reload()}>Try again</button>}
      </div>
    );
  const active = p.assets.find((a) => a.id === selectedAsset),
    shot = p.shots.find((s) => s.id === selectedShot) || p.shots[0];
  const enabled = p.assets.filter((a) => a.enabled && a.role !== "context"),
    errors = compiled.issues.filter((i: any) => i.severity === "error"),
    warnings = compiled.issues.filter((i: any) => i.severity === "warning");
  const total = p.shots.reduce((n, s) => n + (s.duration || 0), 0),
    isManual = p.authoring_mode === "manual";
  const canAI = !!connection.lm?.online && !busy && !isManual;
  const canReturn =
    bridgeConnected &&
    !bridgeImport &&
    bridgeProject.current === p.id &&
    matchesBridgeSnapshot(p, bridgeSnapshot.current);
  const remember = () =>
    setHistory((h) => [...h.slice(-11), structuredClone(p)]);
  const checkpointUpdate = (fn: (draft:Project)=>void) => { remember(); update(fn); };
  const restoreLibraryCopy = (source:Project) => run('Opening a saved copy',async()=>{
    await saveNow();
    const next=structuredClone(source);
    next.id=uid(); next.title=(source.title || 'Saved setup')+' · copy';
    delete next.simple_generation;
    ensurePromptTags(next);
    bridgeProject.current=''; bridgeSnapshot.current=null;
    setP(next); setSimpleResult(null);setProposal(null);
    setSelectedShot(next.shots[0]?.id||'');setSelectedAsset(next.assets[0]?.id||'');
    toast('Opened as a new project. The saved setup and previous project are still available.');
  });
  const commit = (next: Project) => {
    remember();
    delete next.simple_generation;
    setP(next);
  };
  const continueProject = (next:Project) => run('Saving this clip and preparing the next',async()=>{
    await saveNow();
    ensurePromptTags(next);
    await queueProjectSave(next);
    bridgeProject.current=''; bridgeSnapshot.current=null;
    setP(next);setSimpleResult(null);setProposal(null);setHistory([]);
    setSelectedShot(next.shots[0]?.id||'');setSelectedAsset(next.assets[0]?.id||'');
    toast('Next clip saved separately. Review its opening and choose Make my prompt.');
    window.scrollTo({top:0,behavior:'smooth'});
  });
  const confirmLinkedContinuation = (values:ContinuationReviewValues) => {
    const review=continuationReview;
    if(!review?.link||!review.source||review.loading||review.error)return;
    return run('Saving your draft and creating the continuation',async()=>{
      try {
        const catalog=await api('/comfy/options');
        validateContinuationAvailability(review.link!,review.source!,catalog);
        const next=createLinkedContinuation(review.source!,review.link!,values);
        ensurePromptTags(next);
        await saveNow();
        await queueProjectSave(next);
        bridgeProject.current='';bridgeSnapshot.current=null;
        setP(next);setSimpleResult(null);setProposal(null);setCompileResult(null);setHistory([]);
        setSelectedShot(next.shots[0]?.id||'');setSelectedAsset(next.assets[0]?.id||'');
        setView('simple');closeContinuationReview();
        toast('Continuation saved as a separate clip. Review its action and render settings, then make your prompt.');
        window.scrollTo({top:0,behavior:'smooth'});
      } catch(e) {
        setContinuationReview(current=>current?{...current,loading:false,error:(e as Error).message}:current);
        throw e;
      }
    });
  };
  const setMode = (mode: string) => {
    commit(switchMode(p, mode));
    toast(
      "Mode changed. Review the highlighted conditioning roles; your library is retained.",
    );
  };
  const saveNow = async () => {
    clearTimeout(saveTimer.current);
    // Finish an earlier autosave before setting the next active project.
    if(projectRef.current) return queueProjectSave(projectRef.current);
  };
  const loadProject = async (id: string) => {
    await saveNow();
    const next = await api("/projects/" + id);
    setP(next);
    setSelectedShot(next.shots[0]?.id || "");
    setSelectedAsset(next.assets[0]?.id || "");
    setModal("");
    setProposal(null);
  };
  const createNew = () =>
    run("Creating project", async () => {
      await saveNow();
      const next = await api("/projects/new", {});
      if (view === "simple") next.profile = "concise";
      setP(next);
      setSelectedAsset("");
      setSelectedShot(next.shots[0].id);
      setModal("");
      setProposal(null);
    });

  async function replaceReference(id:string,file:File) {
    await run('Replacing photo',async()=>{
      if (!file.type.startsWith('image/')) throw new Error('Choose an image to replace this photo.');
      const targetProject=p.id;
      const form=new FormData(); form.append('file',file);
      const uploaded=await api('/assets',undefined,form);
      if (projectRef.current?.id!==targetProject || !projectRef.current.assets.some(a=>a.id===id)) throw new Error('The photo changed during upload. Choose its replacement again.');
      remember();update(d=>replacePhoto(d,id,uploaded));
      toast('Photo replaced. Its tag and assignments are kept; AI will read the new image.');
    });
  }
  async function addFiles(
    files: FileList | File[] | null,
    contextOnly = false,
  ) {
    if (!files?.length) return;
    if (p.assets.length + files.length > 200) {
      setError(
        "A project can hold 200 references. Remove unused files or start another project before adding these.",
      );
      return;
    }
    await run("Adding references", async () => {
      const targetProject = p.id;
      const additions: Asset[] = [];
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        additions.push(await api("/assets", undefined, form));
      }
      if (projectRef.current?.id !== targetProject)
        throw new Error(
          "The project changed during upload. Add these references to the intended project again.",
        );
      update((d) => {
        for (const a of additions) {
          if (view === "simple" && !contextOnly) {
            a.role = "reference_" + a.media_type;
            a.semantic_role = "other";
            a.enabled = true;
            d.assets.push(a);
            continue;
          }
          if (d.mode !== "ref2va") {
            a.role = "context";
            a.enabled = false;
            if (a.media_type === "image") {
              if (
                ["fl2va", "i2va"].includes(d.mode) &&
                !d.assets.some((x) => x.enabled && x.role === "first_frame")
              ) {
                a.role = "first_frame";
                a.enabled = true;
              } else if (
                ["fl2va", "l2va"].includes(d.mode) &&
                !d.assets.some((x) => x.enabled && x.role === "last_frame")
              ) {
                a.role = "last_frame";
                a.enabled = true;
              }
            }
          }
          if (contextOnly) {
            a.role = "context";
            a.enabled = true;
            a.semantic_role = "pose";
            a.description =
              "Layout/movement sketch only; use as AI planning context, not as a photorealistic H3 input.";
          }
          d.assets.push(a);
          if (d.mode === "ref2va" && !contextOnly && d.subjects.length < 32)
            d.subjects.push({
              id: uid(),
              name: a.name,
              asset_ids: [a.id],
              description: "",
            });
        }
        if (view === "simple") setSimpleMode(d, d.mode);
      });
      setSelectedAsset(additions[0].id);
      toast(
        `${additions.length} reference${additions.length > 1 ? "s" : ""} added.`,
      );
    });
  }
  function editAsset(field: string, value: any) {
    if (!active) return;
    update((d) => {
      const a = d.assets.find((x) => x.id === active.id)!;
      if (field === "role" && (value === "first_frame" || value === "last_frame")) {
        setKeyframe(d, a.id, value);
      } else if (field === "role" && value.startsWith("reference_")) {
        a.role = value;
        setSimpleMode(d, "ref2va");
      } else (a as any)[field] = value;
    });
  }
  function moveAsset(dir: number) {
    if (!active) return;
    const index = p.assets.indexOf(active),
      other = p.assets[index + dir];
    if (!other) return;
    if (active.locked_order || other.locked_order) {
      toast("Unlock both references before changing their order.");
      return;
    }
    update((d) => {
      [d.assets[index], d.assets[index + dir]] = [
        d.assets[index + dir],
        d.assets[index],
      ];
    });
    toast("Reference order updated; the prompt labels now reflect this order.");
  }
  function removeAsset() {
    if (!active) return;
    update((d) => {
      d.assets = d.assets.filter((a) => a.id !== active.id);
      for (const s of d.subjects)
        s.asset_ids = s.asset_ids.filter((id) => id !== active.id);
    });
    setSelectedAsset("");
  }
  function bindAsset(subjectId: string) {
    if (!active) return;
    if (subjectId === "new" && p.subjects.length >= 32) {
      setError(
        "A project supports 32 named subjects. Bind this image to an existing subject or remove an unused one.",
      );
      return;
    }
    update((d) => {
      for (const s of d.subjects)
        s.asset_ids = s.asset_ids.filter((id) => id !== active.id);
      if (subjectId === "new") {
        d.subjects.push({
          id: uid(),
          name: active.name,
          asset_ids: [active.id],
          description: "",
        });
      } else if (subjectId) {
        d.subjects.find((s) => s.id === subjectId)?.asset_ids.push(active.id);
      }
    });
  }
  async function analyse() {
    if (!active) return;
    await run("Reading reference", async () => {
      const result = await api("/ai/analyse", { asset: active });
      update((d) => {
        const a = d.assets.find((a) => a.id === active.id);
        if (a) {
          a.observation = result.observation.observation;
          a.observation_details = result.observation;
        }
      });
      toast(
        `Image read in ${result.seconds.toFixed(1)} seconds. Review the observation before using it.`,
      );
    });
  }
  async function buildPlan() {
    await run(
      vision ? "Reading images and building plan" : "Building scene plan",
      async () => {
        const snapshot = JSON.stringify(projectRef.current);
        const result = await api("/ai/plan", {
          project: projectRef.current,
          instructions,
          persona: aiPersona,
          vision,
        });
        setProposal({ ...result, base: snapshot, kind: "plan" });
        setModal("proposal");
      },
    );
  }
  async function generateSimplePrompt(useAI = true, scrollToPrompt = true) {
    if (busy || videos.active || !projectRef.current) return;
    const original = structuredClone(projectRef.current);
    const prepared = prepareSimpleProject(original);
    const problems = simpleIssues(prepared);
    if (problems.length) {
      if(!scrollToPrompt) throw new Error(problems.join("\n"));
      setError(problems.join("\n"));
      return;
    }
    return await run(useAI ? "Making your prompt" : "Building your prompt", async () => {
      const originalKey = JSON.stringify(original);
      const result = useAI ? await api("/ai/plan", {
        project: prepared,
        instructions: simpleInstructions(prepared) + "\nKeep each scene action to one coherent beat. Use scene_contract for precise actor starting positions, who acts or stays in place, object identity and counts, and ending positions. Retain detailed staging when it matters; avoid repeated descriptions and extra camera moves. Keep speaking faces visible." + (prepared.assistant_instructions?.trim() ? "\nAdditional user direction: " + prepared.assistant_instructions : ""),
        persona: aiPersona,
        vision: connection.lm?.models?.find((m:any)=>m.id===settings.model)?.vision !== false,
      }) : {candidate:prepared,compiled:await api('/compile',{project:prepared}),seconds:0,observations:[]};
      if (JSON.stringify(projectRef.current) !== originalKey) {
        throw new Error("Your photos or instructions changed while the prompt was being made. Click Generate prompt again to use the latest version.");
      }
      if (!result.compiled?.valid) {
        const problems = result.compiled?.issues?.filter((i: any) => i.severity === "error").map((i: any) => i.message) || [];
        throw new Error(problems.join("\n") || "The assistant could not finish a usable prompt. Your photos and instructions are saved; try Generate prompt again.");
      }
      const next: Project = result.candidate;
      next.simple_generation = { seconds: result.seconds, generated_at: new Date().toISOString(),method:useAI?'ai':'manual' };
      setHistory((h) => [...h.slice(-11), original]);
      setP(next);
      projectRef.current=next;
      const key = JSON.stringify(next);
      setCompileResult({ draft: key, result: result.compiled });
      setSimpleResult({ ...result, candidate: next, source: key });
      setSelectedShot(next.shots[0]?.id || "");
      setError("");
      if(scrollToPrompt){
        toast("Your prompt is ready. You can copy it below, or adjust your idea and generate again.");
        requestAnimationFrame(() => document.getElementById("simple-result")?.scrollIntoView({ behavior: "smooth", block: "start" }));
      }
      return {project:next,prompt:result.compiled.prompt};
    }, !scrollToPrompt);
  }
  async function enhance(field: string) {
    await run("Improving " + field.replace("_", " "), async () => {
      const snapshot = JSON.stringify(projectRef.current);
      const result = await api("/ai/assist", {
        project: projectRef.current,
        shot_id: shot.id,
        field,
        instructions,
        persona: aiPersona,
      });
      setProposal({ ...result, base: snapshot, kind: "assist" });
      setModal("proposal");
    });
  }
  function acceptProposal() {
    if (JSON.stringify(p) !== proposal.base) {
      setError(
        "Your project changed while AI was working. Review or regenerate from the current project; the old proposal was not applied.",
      );
      return;
    }
    if (proposal.compiled?.valid === false) {
      setError(
        "The AI proposal has validation errors. Regenerate or edit the existing scene manually.",
      );
      return;
    }
    commit(proposal.candidate);
    setSelectedShot(proposal.candidate.shots[0]?.id);
    setModal("");
    setProposal(null);
    toast("Suggestion applied. Your original version is available with Undo.");
  }
  async function applyBridgeImport() {
    if (!bridgeImport) return;
    await run("Importing ComfyUI references", async () => {
      const data = bridgeImport;
      const next: Project = await api("/projects/new", {});
      next.title = "ComfyUI · " + (data.mode || "H3").toUpperCase();
      next.mode = data.mode || "ref2va";
      next.duration = Math.max(4, Math.min(15, Math.round(data.duration || 5)));
      next.shots = [newShot(next.duration)];
      next.comfy_source = data.source;
      next.imported_prompt = data.prompt || "";
      for (const a of data.assets || []) {
        const stored = await api("/assets/from-data", a);
        next.assets.push({
          ...stored,
          role: a.role || "reference_image",
          semantic_role: a.semantic_role || "other",
        });
        if (next.mode === "ref2va")
          next.subjects.push({
            id: uid(),
            name: stored.name,
            asset_ids: [stored.id],
            description: "",
          });
      }
      if (data.source?.width && data.source?.height) {
        const r = data.source.width / data.source.height;
        next.aspect_ratio = r > 1.5 ? "16:9" : r < 0.8 ? "9:16" : "1:1";
      }
      await saveNow();
      setP(next);
      setSelectedShot(next.shots[0].id);
      setSelectedAsset(next.assets[0]?.id || "");
      bridgeSnapshot.current = makeBridgeSnapshot(next);
      bridgeProject.current = next.id;
      bridgePending.current?.resolve();
      bridgePending.current = null;
      setBridgeImport(null);
      toast(
        "Imported mode and image references. The original prompt is retained in the Source tab; add a plain-language brief to direct AI.",
      );
    });
  }
  async function sendToComfy() {
    if (!compiled.valid || !canReturn) {
      setError(
        "Mode, duration or active references differ from the connected workflow. Update ComfyUI and import again, or copy the prompt manually.",
      );
      return;
    }
    await run("Preparing H3 and returning prompt", async () => {
      const sendingDraft = draftKey;
      const sendingProject = bridgeProject.current;
      const result = await api("/gpu/prepare-h3", {});
      if (result.ready !== true)
        throw new Error("H3 memory hand-off has not completed.");
      if (
        JSON.stringify(projectRef.current) !== sendingDraft ||
        bridgeProject.current !== sendingProject ||
        bridgePending.current ||
        !matchesBridgeSnapshot(projectRef.current!, bridgeSnapshot.current)
      )
        throw new Error(
          "The project changed during memory hand-off. Review the updated prompt and return it again.",
        );
      if (!bridge.current?.sendPrompt(compiled.prompt))
        throw new Error(
          "The ComfyUI connection is not ready. Import the workflow again.",
        );
      toast("H3 is ready. Sending this prompt to the connected node…");
    });
  }
  async function copyPrompt() {
    if (!compiled.valid || !compileFresh) return;
    try {
      await navigator.clipboard.writeText(compiled.prompt);
      toast("H3 prompt copied.");
    } catch {
      downloadText("h3-prompt.txt", compiled.prompt);
    }
  }
  const aiButton = (field: string) => (
    <button
      type="button"
      className="text-button small"
      title="Suggest a change to this field only"
      disabled={!canAI}
      onClick={(e) => {
        e.preventDefault();
        enhance(field);
      }}
    >
      <Sparkles size={12} /> Improve
    </button>
  );
  const shownSimpleResult = simpleResult?.candidate?.id === p.id && simpleResult.source===draftKey ? simpleResult : p.simple_generation ? { candidate: p, compiled, seconds: p.simple_generation.seconds, source: draftKey } : simpleResult?.candidate?.id===p.id?simpleResult:null;
  const simpleResultFresh = !!shownSimpleResult && shownSimpleResult.source === draftKey && compiled.valid;

  const videoTask = async (action:()=>Promise<void>) => {
    if(videoAction.current || busy || videos.active) return;
    videoAction.current=true;setError('');
    try { await action(); }
    catch(e){setError((e as Error).message);throw e;}
    finally {videoAction.current=false;setBusy('');}
  };
  const queueVideo = async (exact:{project:Project;prompt:string}, parentRunId?:string, storyParent?:string) => {
    if(JSON.stringify(projectRef.current)!==JSON.stringify(exact.project)) throw new Error('The story or settings changed. Review them and generate the updated version.');
    setBusy('Sending your video to ComfyUI');
    await queueProjectSave(exact.project);
    if(JSON.stringify(projectRef.current)!==JSON.stringify(exact.project)) throw new Error('The project changed before submission. Generate again when your edits are ready.');
    const continuation=exact.project.simple?.continuation;
    const sourceMatches=continuation?.previous_video_source===exact.project.comfy_render?.continuation_source;
    const parent=parentRunId || (sourceMatches?continuation?.previous_video_run_id:undefined);
    let requestId:string|undefined;
    try {
      await videos.submit('/video/runs',{project:exact.project,prompt:exact.prompt,...(parent?{parent_run_id:parent}:{})},exact.project.id,id=>{
        requestId=id;
        if(exact.project.story_session_id&&(storyParent||parent)) studioLinks.register({kind:'continuation',storyId:exact.project.story_session_id,runId:id,projectId:exact.project.id,parent:storyParent||parent!});
      });
    } catch(error) { if(requestId&&studioLinkDefinitive(error))studioLinks.forget(requestId); throw error; }
    if(!parent&&!storyParent) requestAnimationFrame(()=>document.querySelector('.video-workspace')?.scrollIntoView({behavior:'smooth',block:'start'}));
  };
  const generateVideo = (preset:ContinuationRenderPreset='inherit')=>videoTask(async()=>{
    const source=structuredClone(projectRef.current!);
    if(preset!=='inherit') {
      source.comfy_render=continuationRenderSettings(source.comfy_render,source.mode,preset);
      projectRef.current=source;setP(source);
    }
    let exact:{project:Project;prompt:string}|undefined;
    if(source.simple_generation){
      const checked=await api('/compile',{project:source});
      if(!checked.valid)throw new Error('Review the highlighted prompt issues before generating.');
      exact={project:source,prompt:checked.prompt};
    // Connection discovery may still be in flight immediately after page load.
    // Only the explicit Build without AI action selects manual compilation.
    }else exact=await generateSimplePrompt(true,false);
    if(!exact)throw new Error('The prompt is not ready yet. Review the message above and try again.');
    await queueVideo(exact);
  });
  const rerollVideo = (job:VideoJob)=>videoTask(async()=>{
    // Even the first alternate belongs to an explicit Studio story. It stays a
    // preview until the user chooses its branch; it is never appended as a scene.
    const source:Project=await api('/video/runs/'+job.id+'/project');
    let story=studioStoryId?await api('/stories/'+studioStoryId):null;
    if(!story||story.mode!=='studio'||(story.jobs||story.clips||[]).every((item:VideoJob)=>item.id!==job.id)) story=await api('/stories',{request_id:uid(),project:source,mode:'studio',source_run_id:job.id});
    studioStoryRevision.current++;setStudioStoryId(story.id);setStudioStory(story);
    if(projectRef.current&&projectRef.current.story_session_id!==story.id){const linked={...projectRef.current,story_session_id:story.id};projectRef.current=linked;setP(linked);}
    try{localStorage.setItem('h3-studio-story:'+source.id,story.id);if(projectRef.current)localStorage.setItem('h3-studio-story:'+projectRef.current.id,story.id);}catch{/* Optional storage. */}
    setBusy('Trying another seed · keeping this take’s prompt and settings');
    let requestId:string|undefined,next:VideoJob|undefined;
    try { next=await videos.submit('/video/runs/'+job.id+'/reroll',{},job.project_id,id=>{
      requestId=id;studioLinks.register({kind:'alternate',storyId:story.id,runId:id,projectId:job.project_id,originalRunId:job.id});
    }); } catch(error) {if(requestId&&studioLinkDefinitive(error))studioLinks.forget(requestId);throw error;}
    if(next&&projectRef.current?.id===job.project_id&&Number.isSafeInteger(next.seed)){
      setP(current=>current?{...current,comfy_render:{...current.comfy_render,seed:next.seed}}:current);
    }
  });
  const continueVideo = (selectedJob:VideoJob, idea:string, duration:number, preset:ContinuationRenderPreset='inherit', options?:{planned?:boolean})=>videoTask(async()=>{
    const job:VideoJob=selectedJob.operation==='combine'&&selectedJob.continue_from_run_id
      ? await api('/video/runs/'+selectedJob.continue_from_run_id) : selectedJob;
    if(!job.continuation_source)throw new Error('This take has no saved motion state. Choose a completed take with continuation enabled.');
    setBusy('Reading this video’s ending and story');
    const source:Project=await api('/video/runs/'+job.id+'/project');
    const ending:Asset=await api('/video/runs/'+job.id+'/ending-image',{});
    const story=studioStoryId?await api('/stories/'+studioStoryId):await api('/stories',{request_id:uid(),project:source,mode:'studio',source_run_id:job.id});
    if(story.active_run_id!==job.id)throw new Error('This is an earlier ending. Choose Branch from here to continue it.');
    studioStoryRevision.current++;setStudioStoryId(story.id);setStudioStory(story);
    try{localStorage.setItem('h3-studio-story:'+source.id,story.id);}catch{/* Optional storage. */}
    let plan:any=null;
    if(!options?.planned){
      setBusy('Writing what happens next');
      plan=(await api('/video/runs/'+job.id+'/plan-continuation',{message:idea,duration})).plan;
    }
    const next=createContinuation(source,{request:plan?.action||idea,duration});
    next.story_session_id=story.id;
    next.assets=next.assets.filter((asset:any)=>!asset.video_run_ending);
    next.assets.push(ending);
    next.comfy_render={...next.comfy_render,continuation_source:job.continuation_source,continuation_overlap_frames:39,duration_basis:'new_footage',save_mmh3:true,
      seed:Number.isSafeInteger(job.seed)&&job.seed!<Number.MAX_SAFE_INTEGER?job.seed!+1:0};
    next.comfy_render=continuationRenderSettings(next.comfy_render,next.mode,preset);
    next.simple.continuation={...next.simple.continuation,continuity_basis:'saved_joint_av_latent_and_ending_image',ending_image_asset_id:ending.id,
      previous_video_run_id:job.id,previous_video_source:job.continuation_source,
      previous_story:completedVideoStory(source)};
    if(plan){
      next.shots[0].action=plan.action;next.shots[0].setting=plan.setting;next.shots[0].final_state=plan.final_state;
      next.shots[0].dialogue=(plan.dialogue||[]).map((line:any)=>{
        const speaker=next.subjects.find(s=>s.name.toLowerCase()===line.speaker.toLowerCase());
        if(!speaker)throw new Error('The assistant named an unknown speaker. Add that character or revise the request.');
        return{id:uid(),speaker_id:speaker.id,text:line.text,language:'English',delivery:'natural and clear'};
      });
      if(plan.transition==='cut'){delete next.comfy_render.continuation_source;delete next.comfy_render.duration_basis;delete next.simple.continuation;}
    }
    next.custom_instructions='Only the NEW action happens now. Do not replay completed actions or previous dialogue. Scene timings describe new footage after any saved motion context.';
    ensurePromptTags(next);
    await saveNow();
    setP(next);projectRef.current=next;setSimpleResult(null);setCompileResult(null);setHistory([]);setProposal(null);
    setSelectedShot(next.shots[0]?.id||'');setSelectedAsset(ending.id);bridgeProject.current='';bridgeSnapshot.current=null;
    await queueProjectSave(next);
    // The assistant already supplied an editable next action. Compile the chosen
    // wording faithfully; another planner pass can change that choice.
    const exact=await generateSimplePrompt(false,false);
    if(!exact)throw new Error('The next clip is saved. Make its prompt to finish planning before rendering.');
    await queueVideo(exact,plan?.transition==='cut'?undefined:job.id,job.id);
  });
  const suggestVideo = async(job:VideoJob,duration:number,direction?:string):Promise<ContinuationSuggestions>=>{
    if(videoAction.current || busy || videos.active) throw new Error('Wait for the current operation to finish, then refresh ideas.');
    videoAction.current=true;setBusy('Reading the ending and suggesting next scenes');
    try { return await api('/video/runs/'+job.id+'/suggest',{duration,direction:direction||''}); }
    finally {videoAction.current=false;setBusy('');refresh();}
  };
  const combineVideo = (job:VideoJob)=>videoTask(async()=>{
    setBusy('Combining the selected continuation with its earlier clips');
    await videos.submit('/video/runs/'+job.id+'/combine',{},job.project_id);
  });
  const resolveVideo = async(job:VideoJob)=>{
    if(videoAction.current||busy)return;
    await api('/video/runs/'+job.id+'/resolve',{});
    await videos.reload();
  };

  const branchVideo = async(job:VideoJob)=>{
    if(!studioStoryId)throw new Error('Continue a story first before choosing another branch.');
    studioStoryRevision.current++;
    const story=await api('/stories/'+studioStoryId+'/branch',{request_id:uid(),run_id:job.id});studioStoryRevision.current++;setStudioStory(story);
  };
  const playGame = (job:VideoJob)=>{setGameSource(job.id);setGameSourceKey(value=>value+1);setWorkspaceMode('game');};
  const promptModelPicker=<ModelPicker settings={settings} models={connection.lm?.models} online={!!connection.lm?.online} busy={busy||videos.active}
    onRefresh={refresh} onConnections={()=>{refresh();setModal('connections');}}
    onLoad={()=>run('Preparing the assistant',async()=>{await api('/gpu/prepare-ai',{});await refresh();})}
    onResident={model=>run('Preparing the 0.8B assistant',async()=>{setSettings(await api('/settings',{model,ai_memory_mode:'resident_small',context_length:4096}));await api('/gpu/prepare-ai',{});await refresh();})}
    onSelect={model=>run('Saving prompt model',async()=>{const small=residentModelOptions(connection.lm?.models).some(m=>m.id===model);
      setSettings(await api('/settings',{model,ai_memory_mode:small&&settings.ai_memory_mode==='resident_small'?'resident_small':'exclusive',context_length:small?4096:8192}));})}/>;

  return (
    <div
      className={
        "studio " + (view === "simple" ? "simple-view " : "") + (showAI ? "ai-open " : "") + (showRefs ? "refs-open" : "")
      }
    >
      <nav className="workspace-mode-bar" aria-label="Workspace mode"><strong>H3 Prompt Studio</strong><div><button aria-pressed={workspaceMode==='studio'} onClick={()=>setWorkspaceMode('studio')}>Studio</button><button aria-pressed={workspaceMode==='game'} onClick={()=>setWorkspaceMode('game')}>Game</button></div><span>{workspaceMode==='studio'?'Direct your scenes':'Play a character. Let the story respond.'}</span></nav>
      <div className="workspace-pane" hidden={workspaceMode!=='game'}><GameStudio project={p} modelPicker={promptModelPicker} onAddFiles={addFiles} onUploadFiles={async files=>{const assets:Asset[]=[];for(const file of files){const form=new FormData();form.append('file',file);assets.push(await api('/assets',undefined,form));}return assets;}} onStudio={()=>setWorkspaceMode('studio')} initialSourceRunId={gameSource} initialSourceKey={gameSourceKey}/></div>
      <div className="workspace-pane" hidden={workspaceMode!=='studio'}>
      {view === "simple" && <SimpleStudio
        project={p} update={update} checkpointUpdate={checkpointUpdate} onRestore={restoreLibraryCopy} onReplacePhoto={replaceReference} onAddFiles={(files) => addFiles(files)} onGenerate={()=>generateSimplePrompt()} onBuild={()=>generateSimplePrompt(false)}
        busy={busy} renderBusy={videos.active} progress={connection.stage && connection.stage !== "idle" ? connection.stage : busy}
        error={error} notice={notice} result={shownSimpleResult} currentPrompt={compiled.prompt} resultFresh={simpleResultFresh}
        referenceMap={compiled.references}
        onCopy={copyPrompt} onSave={() => compiled.valid && downloadText("h3-prompt.txt", compiled.prompt)}
        onAdvanced={() => { setView("advanced"); setError(""); }}
        onProjects={() => { api("/projects").then(setProjects); setModal("projects"); }}
        onNew={createNew} onConnections={() => { refresh(); setModal("connections"); }} onFiles={() => setModal("files")}
        onUndo={() => { if (history.length) { setP(history[history.length - 1]); setHistory((h) => h.slice(0, -1)); setSimpleResult(null); } }}
        canUndo={history.length > 0} connectionOnline={!!connection.lm?.online} onSendToComfy={sendToComfy} canReturn={canReturn}
        onContinue={continueProject}
        modelPicker={promptModelPicker}
        comfyPanel={<><VideoWorkspace project={p} promptReady={simpleResultFresh} busy={busy||videos.submitting} jobs={videos.jobs} currentJob={videos.currentJob}
          storyId={studioStoryId||undefined} activeEndpointId={studioStory?.id===studioStoryId?studioStory.active_run_id:undefined} storyClips={studioStory?.id===studioStoryId?studioStory.clips:undefined} onBranch={branchVideo} onPlayGame={playGame}
          onSelectJob={videos.onSelectJob} onGenerate={generateVideo} onReroll={rerollVideo} onContinue={continueVideo} onSuggest={suggestVideo} onCombine={combineVideo} onResolve={resolveVideo} onUpdateTake={videos.updateMetadata}
          />{videos.error&&<p className="comfy-error" role="alert">{videos.error}</p>}</>}
        settingsPanel={<ComfyPanel project={p} prompt={compiled.prompt} ready={simpleResultFresh} busy={!!busy||videos.active}
          onSettings={value=>setP(current=>current?{...current,comfy_render:value}:current)}
          onContinuationSource={value=>update(d=>{if(value&&!['ref2va','t2va'].includes(d.mode))setSimpleMode(d,'t2va');d.comfy_render={...d.comfy_render,continuation_source:value};})}
          embedded={!!bridge.current?.context?.embedded} sendEmbedded={ticket=>bridge.current?.sendTransfer(ticket)===true}/>}
      />}
      {view === "advanced" && <>
      <header className="topbar">
        <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
          <span className="logo-mark">H3</span>
          <div>
            Prompt Studio<small>LOCAL AUTHORING WORKSPACE</small>
          </div>
        </a>
        <span className="top-divider" />
        <div className="project-title">
          <input
            aria-label="Project title"
            value={p.title}
            onChange={(e) =>
              update((d) => {
                d.title = e.target.value;
              })
            }
          />
          <span>
            <Check size={11} />
            {saved}
          </span>
        </div>
        <div className="top-actions">
          <button className="quiet" onClick={() => { setView("simple"); setError(""); }}>Simple view</button>
          <button
            className="reference-toggle quiet"
            aria-label="References"
            onClick={() => {
              setShowRefs(!showRefs);
              setShowAI(false);
            }}
          >
            <Layers size={15} />
            <span>References</span>
          </button>
          <button
            className="quiet tools-toggle"
            aria-label="Tools"
            onClick={() => setModal("tools")}
          >
            <SlidersHorizontal size={15} />
            <span>Tools</span>
          </button>
          <button
            className="assistant-toggle quiet"
            aria-label="Assistant"
            onClick={() => {
              setShowAI(!showAI);
              setShowRefs(false);
            }}
          >
            <Sparkles size={15} />
            <span>Assistant</span>
          </button>
          <IconButton
            icon={Undo2}
            title="Undo last plan or mode change"
            disabled={!history.length}
            onClick={() => {
              setP(history[history.length - 1]);
              setHistory((h) => h.slice(0, -1));
            }}
          />
          <button
            className="quiet"
            onClick={() => {
              api("/projects").then(setProjects);
              setModal("projects");
            }}
          >
            <FolderOpen size={15} /> Projects
          </button>
          <button
            className="connection-button"
            onClick={() => {
              refresh();
              setModal("connections");
            }}
          >
            <span
              className={
                "status-dot " + (connection.lm?.online ? "online" : "")
              }
            />
            {connection.lm?.online ? "LM Studio" : "Connect LM Studio"}
            <Settings2 size={14} />
          </button>
        </div>
      </header>
      <div className="projectbar">
        <div className="mode-group">
          <span className="eyebrow">GENERATION</span>
          <Select
            ariaLabel="Generation mode"
            value={p.mode}
            onChange={setMode}
            options={MODES}
          />
        </div>
        <div className="compact-controls">
          <Select
            label="Length"
            value={p.duration}
            onChange={(v: string) =>
              update((d) => {
                d.duration = Number(v);
                d.shots = retime(d.shots, d.duration);
              })
            }
            options={Array.from({ length: 12 }, (_, i) => [
              i + 4,
              `${i + 4} seconds`,
            ])}
          />
          <Select
            label="Frame"
            value={p.aspect_ratio}
            onChange={(v: string) =>
              update((d) => {
                d.aspect_ratio = v;
              })
            }
            options={choices(["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"])}
          />
          <Select
            label="Prompt format"
            value={p.profile}
            onChange={(v: string) =>
              update((d) => {
                d.profile = v;
              })
            }
            options={PROFILE_OPTIONS}
          />
        </div>
        <div className="authoring-switch" aria-label="Authoring mode">
          {[
            ["manual", "Manual"],
            ["assisted", "Assisted"],
            ["full", "Full AI"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={p.authoring_mode === id ? "active" : ""}
              onClick={() =>
                update((d) => {
                  d.authoring_mode = id;
                })
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {error && (
        <div className="banner error">
          <AlertCircle size={16} />
          <span>{error}</span>
          <IconButton
            icon={X}
            title="Dismiss error"
            onClick={() => setError("")}
          />
        </div>
      )}
      {notice && (
        <div className="toast" role="status">
          <Check size={15} />
          {notice}
        </div>
      )}
      {bridgeImport && (
        <div className="banner bridge-banner">
          <Link2 size={16} />
          <span>
            ComfyUI sent a {(bridgeImport.mode || "").toUpperCase()} workflow
            with {bridgeImport.assets?.length || 0} image references. Import as
            a new project?
          </span>
          <button onClick={applyBridgeImport} disabled={!!busy}>
            Import workflow
          </button>
          <button
            className="quiet"
            onClick={() => {
              bridgePending.current?.reject(
                new Error(
                  "Import dismissed. Reopen Studio from the ComfyUI node to reconnect.",
                ),
              );
              bridgePending.current = null;
              setBridgeImport(null);
            }}
          >
            Dismiss
          </button>
        </div>
      )}
      <div className="workspace">
        <aside className="reference-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">01 / REFERENCES</span>
              <h2>
                Your visual ingredients <span>{enabled.length}</span>
              </h2>
            </div>
            <IconButton
              icon={Plus}
              title="Add reference files"
              onClick={() => fileInput.current?.click()}
            />
          </div>
          <input
            ref={fileInput}
            type="file"
            hidden
            multiple
            accept="image/png,image/jpeg,image/webp,video/*,audio/*"
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <div
            className="drop-zone"
            role="button"
            tabIndex={0}
            onClick={() => fileInput.current?.click()}
            onKeyDown={(e) => e.key === "Enter" && fileInput.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              e.currentTarget.classList.add("drag");
            }}
            onDragLeave={(e) => e.currentTarget.classList.remove("drag")}
            onDrop={(e) => {
              e.preventDefault();
              e.currentTarget.classList.remove("drag");
              addFiles(e.dataTransfer.files);
            }}
          >
            <ImagePlus size={22} />
            <strong>Drop references here</strong>
            <span>Multiple photos, video or audio</span>
          </div>
          <p className="help library-help">
            {p.mode === "ref2va"
              ? "Up to 9 active images. Keep extra options in your library."
              : p.mode === "t2va"
                ? "Text only. Library images remain available as optional AI context."
                : "Assign first and last frames below. Other images can remain in your library."}
          </p>
          <div className="reference-list">
            {p.assets.map((a, index) => {
              const binding = compiled.references.find(
                (r: any) => r.asset_id === a.id,
              );
              return (
                <button
                  key={a.id}
                  className={
                    "asset-card " +
                    (selectedAsset === a.id ? "selected " : "") +
                    (!a.enabled ? "muted" : "")
                  }
                  onClick={() => setSelectedAsset(a.id)}
                >
                  <div className="asset-thumb">
                    {a.media_type === "image" ? (
                      <img
                        src={"/api/assets/" + a.id + "/thumbnail"}
                        alt={a.name}
                      />
                    ) : a.media_type === "video" ? (
                      <Film />
                    ) : (
                      <Music />
                    )}
                    <span className="asset-number">
                      {binding?.token ||
                        (a.role === "context"
                          ? "CTX"
                          : String(index + 1).padStart(2, "0"))}
                    </span>
                  </div>
                  <div className="asset-caption">
                    <strong>{a.name}</strong>
                    <span>
                      {ROLES.find((r) => r[0] === a.semantic_role)?.[1]}
                      {a.role.includes("frame")
                        ? " · " + a.role.replace("_", " ")
                        : ""}
                    </span>
                  </div>
                  <span
                    className={"asset-check " + (a.enabled ? "checked" : "")}
                  >
                    {a.enabled ? <Check size={12} /> : null}
                  </span>
                </button>
              );
            })}
          </div>
          {!p.assets.length && (
            <div className="empty-reference">
              <Layers size={28} />
              <p>
                A face. A place. An object.
                <br />
                Give each reference a clear job.
              </p>
            </div>
          )}
          {active && (
            <div className="asset-inspector">
              <div className="section-label">
                <span>REFERENCE DETAILS</span>
                <div>
                  <IconButton
                    icon={ArrowUp}
                    title="Move reference earlier"
                    onClick={() => moveAsset(-1)}
                  />
                  <IconButton
                    icon={ArrowDown}
                    title="Move reference later"
                    onClick={() => moveAsset(1)}
                  />
                  <IconButton
                    icon={Trash2}
                    title="Remove from this project"
                    onClick={removeAsset}
                  />
                </div>
              </div>
              <Input
                label="Reference name"
                value={active.name}
                onChange={(v: string) => editAsset("name", v)}
              />
              <div className="two-col">
                <Select
                  label="Use this for"
                  value={active.semantic_role}
                  onChange={(v: string) => editAsset("semantic_role", v)}
                  options={ROLES}
                />
                <Select
                  label="H3 input role"
                  value={active.role}
                  onChange={(v: string) => editAsset("role", v)}
                  options={[
                    [
                      "reference_" + active.media_type,
                      "Reference " + active.media_type,
                    ],
                    ...(active.media_type === "image"
                      ? [
                          ["first_frame", "First frame"],
                          ["last_frame", "Last frame"],
                        ]
                      : []),
                    ["context", "AI context only"],
                  ]}
                />
              </div>
              <div className="check-row">
                <label>
                  <input
                    type="checkbox"
                    checked={active.enabled}
                    onChange={(e) => editAsset("enabled", e.target.checked)}
                  />{" "}
                  Use in this project
                </label>
                <label title="Prevent this card being reordered">
                  <input
                    type="checkbox"
                    checked={active.locked_order}
                    onChange={(e) =>
                      editAsset("locked_order", e.target.checked)
                    }
                  />
                  <Lock size={11} /> Order
                </label>
              </div>
              <Select
                label="Subject binding"
                value={
                  p.subjects.find((s) => s.asset_ids.includes(active.id))?.id ||
                  ""
                }
                onChange={bindAsset}
                options={[
                  ["", "No named subject"],
                  ...p.subjects.map((s) => [s.id, s.name]),
                  ["new", "+ Create subject from this reference"],
                ]}
              />
              <Area
                label="Your facts / what to preserve"
                value={active.description}
                onChange={(v: string) => editAsset("description", v)}
                rows={3}
                placeholder="Identity only; use a different outfit. Or: keep this background, without its people."
              />
              {active.media_type === "image" ? (
                <button
                  className="quiet full-width"
                  onClick={analyse}
                  disabled={!canAI}
                >
                  <Eye size={15} />
                  {active.observation
                    ? "Read image again"
                    : "Read image with vision model"}
                </button>
              ) : (
                <p className="callout">
                  <Mic size={14} /> Describe dialogue, voice and sounds
                  yourself. The vision model does not listen to this clip.
                </p>
              )}
              {active.observation && (
                <div className="observation">
                  <span className="eyebrow">
                    <Eye size={11} /> AI OBSERVATION · REVIEW
                  </span>
                  <p>{active.observation}</p>
                  {active.approved_observation === active.observation ? (
                    <span className="accepted">
                      <Check size={12} /> Approved for prompts
                    </span>
                  ) : (
                    <button
                      className="text-button"
                      onClick={() =>
                        editAsset("approved_observation", active.observation)
                      }
                    >
                      <Check size={13} />{" "}
                      {active.approved_observation
                        ? "Replace with original observation"
                        : "Use this observation"}
                    </button>
                  )}
                </div>
              )}
              {(active.observation || active.approved_observation) && (
                <>
                  <Area
                    label="Approved reference description"
                    value={active.approved_observation}
                    onChange={(v: string) =>
                      editAsset("approved_observation", v)
                    }
                    rows={5}
                    placeholder="Keep only verified details relevant to this image's role."
                  />
                  <p className="help">
                    Only this approved description enters prompts. Correct or
                    trim it here; the original AI observation stays above.
                  </p>
                </>
              )}
              {active.approved_observation && (
                <button
                  className="text-button small"
                  onClick={() => editAsset("approved_observation", "")}
                >
                  Remove approved AI description
                </button>
              )}
            </div>
          )}
        </aside>
        <main className="editor">
          <div className="editor-scroll">
            <div className="editor-heading">
              <div>
                <span className="eyebrow">02 / DIRECTION</span>
                <h1>Shape the scene.</h1>
                <p>
                  Start with what happens. Give AI only the parts you want help
                  with.
                </p>
              </div>
              <div className="scene-number">
                {String(p.shots.length).padStart(2, "0")}
                <span>SHOT{p.shots.length !== 1 ? "S" : ""}</span>
              </div>
            </div>
            <Area
              label={
                <>
                  <Lock size={12} /> Story & non-negotiable details
                </>
              }
              value={p.story.text}
              onChange={(v: string) =>
                update((d) => {
                  d.story.text = v;
                })
              }
              rows={4}
              placeholder="A person walks into a sunlit studio, places the product on a table, and looks toward the camera. Preserve the identity in the face reference and the exact product design."
            />
            <div className="direction-grid">
              <Select
                label="Visual language"
                value={p.style.genre}
                onChange={(v: string) =>
                  update((d) => {
                    d.style.genre = v;
                  })
                }
                options={choices([
                  "cinematic",
                  "photorealistic",
                  "commercial",
                  "animation",
                  "documentary",
                  "motion design",
                  "editorial",
                  "fantasy",
                  "minimal",
                ])}
              />
              <Input
                label="Vibe"
                value={p.style.vibe}
                onChange={(v: string) =>
                  update((d) => {
                    d.style.vibe = v;
                  })
                }
                placeholder="Quiet confidence, playful, tense…"
              />
              <Input
                label="Light"
                value={p.style.lighting}
                onChange={(v: string) =>
                  update((d) => {
                    d.style.lighting = v;
                  })
                }
                placeholder="Soft window light, blue hour…"
              />
              <Input
                label="Color direction"
                value={p.style.color}
                onChange={(v: string) =>
                  update((d) => {
                    d.style.color = v;
                  })
                }
                placeholder="Warm neutrals, rich cyan…"
              />
            </div>
            <details className="subject-details">
              <summary>
                <Layers size={14} /> Subjects & identities{" "}
                <span>{p.subjects.length}</span>
              </summary>
              <p className="help">
                Bind multiple photos to the same subject in Reference Details.
                Named subjects can be people, objects or locations.
              </p>
              {p.subjects.map((s) => (
                <div className="subject-row" key={s.id}>
                  <input
                    aria-label="Subject name"
                    value={s.name}
                    onChange={(e) =>
                      update((d) => {
                        d.subjects.find((x) => x.id === s.id)!.name =
                          e.target.value;
                      })
                    }
                  />
                  <input
                    aria-label="Subject facts"
                    value={s.description}
                    placeholder="Identity / role facts"
                    onChange={(e) =>
                      update((d) => {
                        d.subjects.find((x) => x.id === s.id)!.description =
                          e.target.value;
                      })
                    }
                  />
                  <IconButton
                    icon={Trash2}
                    title={"Remove subject " + s.name}
                    onClick={() =>
                      update((d) => {
                        d.subjects = d.subjects.filter((x) => x.id !== s.id);
                        for (const target of d.shots) {
                          target.visible_subject_ids = target.visible_subject_ids.filter((id) => id !== s.id);
                          target.offscreen_subject_ids = target.offscreen_subject_ids.filter((id) => id !== s.id);
                          pruneSceneActors(d, target.id);
                        }
                      })
                    }
                  />
                </div>
              ))}
              <button
                className="text-button"
                disabled={p.subjects.length >= 32}
                onClick={() =>
                  update((d) => {
                    d.subjects.push({
                      id: uid(),
                      name: "New subject",
                      asset_ids: [],
                      description: "",
                    });
                  })
                }
              >
                <Plus size={13} /> Add named subject
              </button>
            </details>
            <div className="timeline-section">
              <div className="section-label">
                <span>SHOT TIMELINE</span>
                <div>
                  <span
                    className={
                      Math.abs(total - p.duration) > 0.01
                        ? "warning-text"
                        : "subtle"
                    }
                  >
                    {total.toFixed(1)} / {p.duration}s
                  </span>
                  <button
                    className="text-button small"
                    onClick={() =>
                      update((d) => {
                        d.shots = retime(d.shots, d.duration);
                      })
                    }
                  >
                    Fit to length
                  </button>
                  <IconButton
                    icon={Plus}
                    title="Add shot and redistribute time"
                    disabled={p.shots.length >= 8}
                    onClick={() =>
                      update((d) => {
                        d.shots = retime(
                          [...d.shots, newShot(d.duration / d.shots.length)],
                          d.duration,
                        );
                      })
                    }
                  />
                </div>
              </div>
              <div className="time-ruler">
                <span>0s</span>
                <span>{(p.duration / 2).toFixed(1)}s</span>
                <span>{p.duration}s</span>
              </div>
              <div className="timeline">
                {p.shots.map((s, i) => (
                  <button
                    key={s.id}
                    className={s.id === selectedShot ? "selected" : ""}
                    style={{ flex: Math.max(0.1, s.duration) }}
                    onClick={() => setSelectedShot(s.id)}
                  >
                    <span>SHOT {String(i + 1).padStart(2, "0")}</span>
                    <strong>{s.duration.toFixed(1)}s</strong>
                    <small>{s.action || "Describe an action"}</small>
                  </button>
                ))}
              </div>
            </div>
            {shot && (
              <section className="shot-editor">
                <div className="shot-heading">
                  <div className="shot-label">
                    <Clapperboard size={16} />
                    <h2>
                      Shot {String(p.shots.indexOf(shot) + 1).padStart(2, "0")}
                    </h2>
                    <span>
                      {compiled.timeline
                        .find((s: any) => s.id === shot.id)
                        ?.start?.toFixed(2) || "0.00"}
                      s →{" "}
                      {compiled.timeline
                        .find((s: any) => s.id === shot.id)
                        ?.end?.toFixed(2) || shot.duration.toFixed(2)}
                      s
                    </span>
                  </div>
                  <div className="shot-utilities">
                    <input
                      type="number"
                      aria-label="Shot duration"
                      min="0.1"
                      max="15"
                      step="0.1"
                      value={shot.duration}
                      onChange={(e) =>
                        updateShot("duration", Number(e.target.value))
                      }
                    />
                    <span>s</span>
                    <IconButton
                      icon={Trash2}
                      title="Delete shot"
                      disabled={p.shots.length < 2 || !!shot.dialogue.length}
                      onClick={() =>
                        update((d) => {
                          d.shots = retime(
                            d.shots.filter((x) => x.id !== shot.id),
                            d.duration,
                          );
                        })
                      }
                    />
                  </div>
                </div>
                <Area
                  label="Action / movement"
                  value={shot.action}
                  onChange={(v: string) => updateShot("action", v)}
                  rows={4}
                  placeholder="Describe the sequence: starting position → movement → how it settles."
                >
                  {aiButton("action")}
                </Area>
                <div className="inspector-tabs">
                  {[
                    ["camera", Camera, "Camera"],
                    ["subjects", Layers, "Subjects"],
                    ["dialogue", MessageSquare, "Dialogue"],
                    ["sound", Mic, "Sound"],
                    ["continuity", Film, "Continuity"],
                  ].map(([id, Icon, label]: any) => (
                    <button
                      key={id}
                      className={inspector === id ? "active" : ""}
                      onClick={() => setInspector(id)}
                    >
                      <Icon size={14} />
                      {label}
                      {id === "dialogue" && shot.dialogue.length > 0 && (
                        <b>{shot.dialogue.length}</b>
                      )}
                    </button>
                  ))}
                </div>
                <div className="inspector-body">
                  {inspector === "camera" && (
                    <>
                      <div className="section-label">
                        <span>CAMERA DIRECTION</span>
                        {aiButton("camera")}
                      </div>
                      <div className="direction-grid">
                        <Select
                          label="Framing"
                          value={shot.camera.framing}
                          onChange={(v: string) =>
                            updateShot("camera", { ...shot.camera, framing: v })
                          }
                          options={choices([
                            "extreme wide",
                            "wide",
                            "medium wide",
                            "medium",
                            "medium close-up",
                            "close-up",
                            "extreme close-up",
                            "over the shoulder",
                            "point of view",
                          ])}
                        />
                        <Select
                          label="Movement"
                          value={shot.camera.movement}
                          onChange={(v: string) =>
                            updateShot("camera", {
                              ...shot.camera,
                              movement: v,
                            })
                          }
                          options={choices([
                            "static",
                            "push in",
                            "pull out",
                            "track left",
                            "track right",
                            "pan left",
                            "pan right",
                            "tilt up",
                            "tilt down",
                            "follow subject",
                            "gentle arc",
                            "rack focus",
                          ])}
                        />
                        <Select
                          label="Height"
                          value={shot.camera.height}
                          onChange={(v: string) =>
                            updateShot("camera", { ...shot.camera, height: v })
                          }
                          options={choices([
                            "ground level",
                            "knee level",
                            "waist level",
                            "eye level",
                            "above subject",
                            "overhead",
                          ])}
                        />
                        <Select
                          label="Pace"
                          value={shot.camera.speed}
                          onChange={(v: string) =>
                            updateShot("camera", { ...shot.camera, speed: v })
                          }
                          options={choices([
                            "very slow",
                            "slow",
                            "natural",
                            "fast",
                          ])}
                        />
                      </div>
                      <Input
                        label="Focus / camera anchor"
                        value={shot.camera.focus}
                        onChange={(v: string) =>
                          updateShot("camera", { ...shot.camera, focus: v })
                        }
                        placeholder="Keep the product sharp; the background falls softly out of focus."
                      />
                      <div className="quick-palette">
                        <span>QUICK CHOICES</span>
                        {[
                          "static",
                          "push in",
                          "follow subject",
                          "gentle arc",
                          "rack focus",
                        ].map((v) => (
                          <button
                            className={
                              shot.camera.movement === v ? "chosen" : ""
                            }
                            key={v}
                            onClick={() =>
                              updateShot("camera", {
                                ...shot.camera,
                                movement: v,
                              })
                            }
                          >
                            {v}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                  {inspector === "subjects" && (
                    <>
                      <Area
                        label="Location & spatial positions"
                        value={shot.setting}
                        onChange={(v: string) => updateShot("setting", v)}
                        placeholder="Subject A at left foreground; the object centered on the table; background reference sets the room."
                      >
                        {aiButton("setting")}
                      </Area>
                      <p className="help">
                        Choose who is visible and who is off-screen in this
                        shot.
                      </p>
                      {p.subjects.map((s) => (
                        <div className="roster-row" key={s.id}>
                          <strong>{s.name}</strong>
                          <Select
                            value={
                              shot.visible_subject_ids.includes(s.id)
                                ? "visible"
                                : shot.offscreen_subject_ids.includes(s.id)
                                  ? "offscreen"
                                  : "unused"
                            }
                            onChange={(v: string) =>
                              update((d) => {
                                const target = d.shots.find(
                                  (x) => x.id === shot.id,
                                )!;
                                target.visible_subject_ids =
                                  target.visible_subject_ids.filter(
                                    (id) => id !== s.id,
                                  );
                                target.offscreen_subject_ids =
                                  target.offscreen_subject_ids.filter(
                                    (id) => id !== s.id,
                                  );
                                if (v === "visible")
                                  target.visible_subject_ids.push(s.id);
                                if (v === "offscreen")
                                  target.offscreen_subject_ids.push(s.id);
                                pruneSceneActors(d, target.id);
                              })
                            }
                            options={[
                              ["unused", "Not in this shot"],
                              ["visible", "Visible"],
                              ["offscreen", "Off-screen"],
                            ]}
                          />
                        </div>
                      ))}
                      {!p.subjects.length && (
                        <p className="help">
                          Add a named subject above or bind a reference to one.
                        </p>
                      )}
                    </>
                  )}
                  {inspector === "dialogue" && (
                    <>
                      <div className="section-label">
                        <span>EXACT WORDS, YOUR CHOICE</span>
                        <button
                          className="text-button"
                          onClick={() =>
                            updateShot("dialogue", [
                              ...shot.dialogue,
                              {
                                id: uid(),
                                speaker_id: p.subjects[0]?.id || "",
                                language: "English",
                                text: "",
                                delivery: "",
                                locked: true,
                              },
                            ])
                          }
                        >
                          <Plus size={13} /> Add dialogue
                        </button>
                      </div>
                      <p className="help">
                        <Lock size={11} /> AI can direct the performance. It
                        cannot rewrite these words.
                      </p>
                      {shot.dialogue.map((line, i) => (
                        <div className="dialogue-card" key={line.id}>
                          <div className="two-col">
                            <Select
                              label="Speaker"
                              value={line.speaker_id}
                              options={[
                                ["", "Choose a subject"],
                                ...p.subjects.map((s) => [s.id, s.name]),
                              ]}
                              onChange={(v: string) => {
                                const lines = structuredClone(shot.dialogue);
                                lines[i].speaker_id = v;
                                updateShot("dialogue", lines);
                              }}
                            />
                            <Input
                              label="Language"
                              value={line.language}
                              onChange={(v: string) => {
                                const lines = structuredClone(shot.dialogue);
                                lines[i].language = v;
                                updateShot("dialogue", lines);
                              }}
                            />
                          </div>
                          <Area
                            label="Verbatim dialogue"
                            value={line.text}
                            onChange={(v: string) => {
                              const lines = structuredClone(shot.dialogue);
                              lines[i].text = v;
                              updateShot("dialogue", lines);
                            }}
                            placeholder="Type exactly what should be spoken."
                            rows={2}
                          />
                          <Input
                            label="Voice / delivery"
                            value={line.delivery}
                            onChange={(v: string) => {
                              const lines = structuredClone(shot.dialogue);
                              lines[i].delivery = v;
                              updateShot("dialogue", lines);
                            }}
                            placeholder="Warm low voice, softly spoken, slight pause…"
                          />
                          <button
                            className="text-button small"
                            onClick={() =>
                              updateShot(
                                "dialogue",
                                shot.dialogue.filter((_, j) => j !== i),
                              )
                            }
                          >
                            Remove line
                          </button>
                        </div>
                      ))}
                      {!shot.dialogue.length && (
                        <div className="inline-empty">
                          <MessageSquare size={24} />
                          <span>
                            No dialogue in this shot.
                            <br />
                            Leave it empty for a visual sequence.
                          </span>
                        </div>
                      )}
                      <Area
                        label="Performance / expression"
                        value={shot.performance}
                        onChange={(v: string) => updateShot("performance", v)}
                        placeholder="A restrained smile; a measured pause before speaking."
                      >
                        {aiButton("performance")}
                      </Area>
                    </>
                  )}
                  {inspector === "sound" && (
                    <>
                      <p className="callout">
                        <Mic size={15} /> Describe what should be heard. Image
                        analysis never claims to hear audio.
                      </p>
                      <Area
                        label="Sounds in this shot"
                        value={shot.sound}
                        onChange={(v: string) => updateShot("sound", v)}
                        placeholder="Footsteps approach from the left, then a gentle click as the object meets the table."
                      >
                        {aiButton("sound")}
                      </Area>
                      <Area
                        label="Overall ambience"
                        value={p.soundscape}
                        onChange={(v: string) =>
                          update((d) => {
                            d.soundscape = v;
                          })
                        }
                        placeholder="Quiet interior room tone; distant traffic, no speech beyond the written dialogue."
                      />
                      <Area
                        label="Music direction"
                        value={p.music}
                        onChange={(v: string) =>
                          update((d) => {
                            d.music = v;
                          })
                        }
                        placeholder="Leave blank for no added music. Or: sparse warm piano with a soft ending."
                      />
                    </>
                  )}
                  {inspector === "continuity" && (
                    <>
                      <Select
                        label="Transition into this shot"
                        value={shot.transition}
                        onChange={(v: string) => updateShot("transition", v)}
                        options={[
                          ["continuous", "Continuous motion"],
                          ["cut", "Cut"],
                        ]}
                      />
                      <Area
                        label="Required final state"
                        value={shot.final_state}
                        onChange={(v: string) => updateShot("final_state", v)}
                        placeholder="The object remains centered and motionless; the subject holds their gaze. For FL2VA, settle into the last frame."
                      >
                        {aiButton("final_state")}
                      </Area>
                      <p className="help">
                        Describe the landing. A stop should include
                        deceleration; a turn should include its path. Prompt
                        timing guides the model but does not trim the rendered
                        video.
                      </p>
                      <SceneContinuity project={p} shot={shot} index={p.shots.findIndex((item) => item.id === shot.id)} update={update} />
                    </>
                  )}
                </div>
              </section>
            )}
          </div>
          <section
            className={"output-panel " + (!showOutput ? "collapsed" : "")}
          >
            <div className="output-heading">
              <div>
                <span className="eyebrow">04 / YOUR H3 PROMPT</span>
                <span
                  className={
                    "validation-badge " + (errors.length ? "invalid" : "")
                  }
                >
                  <span className="status-dot online" />
                  {!compileFresh
                    ? "Compiling…"
                    : errors.length
                      ? `${errors.length} issue${errors.length > 1 ? "s" : ""}`
                      : "Ready to copy"}
                </span>
              </div>
              <div>
                <button
                  className="quiet files-button"
                  onClick={() => setModal("files")}
                >
                  <FolderOpen size={14} /> Files & outputs
                </button>
                <button
                  className="quiet files-button"
                  aria-label="Copy compiled H3 prompt"
                  title="Copy the finished prompt to your clipboard"
                  disabled={!compiled.valid}
                  onClick={copyPrompt}
                >
                  <Copy size={14} /> Copy prompt
                </button>
                <button
                  className="quiet files-button"
                  aria-label="Export H3 prompt text"
                  title="Save the finished prompt as a text file"
                  disabled={!compiled.valid}
                  onClick={() => downloadText("h3-prompt.txt", compiled.prompt)}
                >
                  <Download size={14} /> Save prompt
                </button>
                <IconButton
                  icon={ChevronDown}
                  title={
                    showOutput
                      ? "Collapse prompt preview"
                      : "Expand prompt preview"
                  }
                  onClick={() => setShowOutput(!showOutput)}
                  className={!showOutput ? "flip" : ""}
                />
              </div>
            </div>
            {showOutput && (
              <>
                <div className="output-tabs">
                  {[
                    ["prompt", "H3 prompt"],
                    ["bindings", "Reference map"],
                    [
                      "validation",
                      `Checks ${compiled.issues.length ? `(${compiled.issues.length})` : ""}`,
                    ],
                    ...(p.imported_prompt
                      ? [["source", "ComfyUI source"]]
                      : []),
                  ].map(([id, label]) => (
                    <button
                      className={outputTab === id ? "active" : ""}
                      key={id}
                      onClick={() => setOutputTab(id)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="output-content">
                  {outputTab === "prompt" &&
                    (compiled.prompt ? (
                      <pre>{compiled.prompt}</pre>
                    ) : (
                      <div className="prompt-placeholder">
                        <Clapperboard size={24} />
                        <p>
                          {!compileFresh
                            ? "Compiling the current draft…"
                            : errors.length
                              ? "Resolve the validation issues to compile your H3 prompt."
                              : "Add your direction and references. Your prompt appears here as you edit."}
                        </p>
                        {errors.map((i: any) => (
                          <span key={i.code + i.path}>{i.message}</span>
                        ))}
                      </div>
                    ))}
                  {outputTab === "bindings" && (
                    <>
                      {compiled.references.map((r: any, i: number) => (
                        <div className="binding-row" key={r.asset_id + i}>
                          <code>{r.token}</code>
                          <span>
                            {r.name ||
                              p.assets.find((a) => a.id === r.asset_id)?.name}
                          </span>
                          <small>
                            {r.role} · {r.semantic_role}
                          </small>
                        </div>
                      ))}
                      {!compiled.references.length && (
                        <p className="help">
                          No conditioned reference labels in this mode.
                        </p>
                      )}
                    </>
                  )}
                  {outputTab === "validation" && (
                    <>
                      {!compiled.issues.length && (
                        <p className="accepted">
                          <ShieldCheck size={16} /> Structure, timing and
                          reference bindings passed.
                        </p>
                      )}
                      {compiled.issues.map((i: any, index: number) => (
                        <div className={"issue " + i.severity} key={index}>
                          <AlertCircle size={15} />
                          <div>
                            <strong>
                              {i.severity === "error" ? "Fix required" : "Note"}
                            </strong>
                            <p>{i.message}</p>
                          </div>
                        </div>
                      ))}
                    </>
                  )}
                  {outputTab === "source" && (
                    <>
                      <p className="help">
                        Original ComfyUI text, kept unchanged for comparison.
                        Add your plain-language direction above; this text is
                        not silently treated as a new story.
                      </p>
                      <pre>{p.imported_prompt}</pre>
                    </>
                  )}
                </div>
              </>
            )}
          </section>
        </main>
        <aside className="assistant-panel">
          <div className="drawer-close">
            <IconButton
              icon={PanelRightClose}
              title="Close assistant"
              onClick={() => setShowAI(false)}
            />
          </div>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">03 / LOCAL ASSISTANT</span>
              <h2>
                A little direction.
                <br />A better prompt.
              </h2>
            </div>
            <Sparkles size={20} />
          </div>
          <p className="help">
            {isManual
              ? "Manual mode is fully local and deterministic. Change to Assisted when you want AI suggestions."
              : "The vision model reads the actual photos. AI proposes a scene; you decide what becomes final."}
          </p>
          <Select
            label="System prompt / creative role"
            value={aiPersona}
            onChange={setAiPersona}
            options={personas.map((x) => [x.id, x.name])}
          />
          <p className="persona-detail">
            {personas.find((x) => x.id === aiPersona)?.description}
          </p>
          <Area
            label="Ask the assistant"
            value={instructions}
            onChange={(v: string) => {
              setInstructions(v);
              update((d) => {
                d.assistant_instructions = v;
              });
            }}
            rows={4}
            placeholder="Keep it to one shot. Use image 1 only for identity, image 2 for the room. Make the camera movement subtle."
          />
          <label className="vision-toggle">
            <input
              type="checkbox"
              checked={vision}
              onChange={(e) => setVision(e.target.checked)}
            />
            <span>
              <strong>Read selected images</strong>
              <small>Small-model friendly: one image at a time.</small>
            </span>
            <Eye size={16} />
          </label>
          <button
            className="primary full-width generate-button"
            disabled={!canAI || !p.story.text.trim()}
            onClick={buildPlan}
          >
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <WandSparkles size={17} />
            )}{" "}
            {p.authoring_mode === "full"
              ? "Build the full scene plan"
              : "Suggest a scene plan"}
            <ArrowUpRight size={15} />
          </button>
          {busy && (
            <div className="working">
              <LoaderCircle className="spin" size={15} />
              <div>
                <strong>
                  {connection.stage && connection.stage !== "idle"
                    ? connection.stage
                    : busy}
                </strong>
                <small>Working locally. Your current scene stays intact.</small>
              </div>
            </div>
          )}
          {!connection.lm?.online && (
            <button
              className="text-button"
              onClick={() => setModal("connections")}
            >
              Connect your local model <ArrowUpRight size={12} />
            </button>
          )}
          <div className="assistant-notes">
            <div>
              <Lock size={15} />
              <p>
                <strong>Your words stay yours.</strong>
                <span>
                  Story facts and exact dialogue are preserved. Review meaning
                  before accepting.
                </span>
              </p>
            </div>
            <div>
              <Layers size={15} />
              <p>
                <strong>Each image has a purpose.</strong>
                <span>
                  Faces, backgrounds, objects and style stay separately labeled.
                </span>
              </p>
            </div>
            <div>
              <Monitor size={15} />
              <p>
                <strong>One model on the GPU.</strong>
                <span>
                  H3 releases memory before AI loads. Prepare H3 unloads the
                  assistant.
                </span>
              </p>
            </div>
          </div>
          <details className="advanced-direction">
            <summary>
              <SlidersHorizontal size={14} /> Custom instructions
            </summary>
            <Area
              label="Reusable custom instructions"
              value={p.custom_instructions}
              onChange={(v: string) =>
                update((d) => {
                  d.custom_instructions = v;
                })
              }
              rows={5}
              placeholder="Your reusable rules: preserve product branding, no camera shake, no extra people…"
            />
            <p className="help">
              Select Custom direction as the prompt format to include these
              instructions in the compiled prompt.
            </p>
          </details>
          <button
            className="quiet full-width"
            onClick={() =>
              run("Opening system prompt", async () => {
                const result = await api(
                  `/system-prompt?persona=${encodeURIComponent(aiPersona)}&mode=${encodeURIComponent(p.mode)}`,
                );
                setSystemPrompt(result.prompt);
                setModal("system-prompt");
              })
            }
            disabled={!!busy}
          >
            <BookOpen size={15} /> View reusable system prompt
          </button>
          <div className="gpu-card">
            <div>
              <span className="status-dot online" />
              <strong>
                {connection.stage === "idle"
                  ? "Local workspace"
                  : connection.stage || "Checking connections"}
              </strong>
              <button
                className="text-button small"
                onClick={() => {
                  refresh();
                  setModal("connections");
                }}
              >
                Manage
              </button>
            </div>
            {connection.gpu && (
              <>
                <span className="gpu-name">{connection.gpu.name}</span>
                <div className="gpu-meter">
                  <i
                    style={{
                      width: `${Math.min(100, (connection.gpu.used_mib / connection.gpu.total_mib) * 100)}%`,
                    }}
                  />
                </div>
                <small>
                  {(connection.gpu.used_mib / 1024).toFixed(1)} GB used ·{" "}
                  {(connection.gpu.free_mib / 1024).toFixed(1)} GB free
                </small>
              </>
            )}
            <button
              className="quiet full-width"
              disabled={!!busy}
              onClick={() =>
                run("Preparing H3", async () => {
                  await api("/gpu/prepare-h3", {});
                  toast("LM Studio unloaded. H3 is ready.");
                })
              }
            >
              <Video size={15} /> Prepare H3
            </button>
          </div>
          {bridgeConnected ? (
            <>
              <button
                className="primary full-width return-button"
                disabled={!!busy || !compiled.valid || !canReturn}
                onClick={sendToComfy}
              >
                <Link2 size={16} /> Return prompt to ComfyUI
              </button>
              {!canReturn && (
                <p className="help">
                  Import the connected workflow first. Return is available only
                  while its mode, length and active image order still match.
                </p>
              )}
            </>
          ) : (
            <div className="bridge-tip">
              <Link2 size={16} />
              <p>
                Open Studio from an H3 node in ComfyUI to import images and
                return this prompt.
              </p>
            </div>
          )}
        </aside>
      </div>
      </>}
      </div>
      {view === "simple" && bridgeImport && <div className="banner bridge-banner">
        <span>ComfyUI sent photos and a prompt. Open them as a new project?</span>
        <button onClick={applyBridgeImport} disabled={!!busy}>Open from ComfyUI</button>
        <button onClick={() => { bridgePending.current?.reject(new Error("Import dismissed.")); bridgePending.current = null; setBridgeImport(null); }}>Dismiss</button>
      </div>}
      {continuationReview && <ContinuationLinkReview {...continuationReview} busy={!!busy}
        onClose={closeContinuationReview}
        onRetry={()=>{if(continuationReview.link)void loadContinuationReview(continuationReview.link);}}
        onCreate={confirmLinkedContinuation} />}
      {modal === "tools" && (
        <Modal
          wide
          title="Sketch & movement tools"
          subtitle="Make a simple visual guide, then turn it into clear direction."
          onClose={() => setModal("")}
        >
          <ContinuationPlanner project={p} update={update} onContinue={continueProject} busy={!!busy}/>
          <button type="button" onClick={() => setModal('motion-lab')}>Compare motion prompts · Pixel preview</button>
          <MotionTools
            duration={shot?.duration || p.duration}
            subjects={p.subjects}
            onInstructions={(text: string) => {
              updateShot(
                "action",
                [shot.action, text].filter(Boolean).join("\n"),
              );
              toast("Movement direction added to this shot.");
            }}
            onReference={(file: File) => addFiles([file], true)}
          />
        </Modal>
      )}
      {modal === 'motion-lab' && <MotionLab project={p} onClose={() => setModal('')} />}
      {modal === "system-prompt" && (
        <Modal
          wide
          title="Reusable system prompt"
          subtitle={`${aiPersona} · ${p.mode.toUpperCase()} · for a separate LM Studio chat`}
          onClose={() => setModal("")}
        >
          <p className="help">
            Paste this into your chat's system instructions. Attach images in
            the same order and describe each image's role. Studio's built-in
            assistant uses structured planning instructions instead.
          </p>
          <pre className="system-prompt-preview">{systemPrompt}</pre>
          <button
            className="primary"
            onClick={() =>
              downloadText(
                `h3-${aiPersona}-${p.mode}-system-prompt.txt`,
                systemPrompt,
              )
            }
          >
            <Download size={15} /> Download system prompt
          </button>
        </Modal>
      )}
      {modal === "connections" && (
        <Modal
          title="Connections & GPU"
          subtitle="Switch models without closing your workflow."
          onClose={() => setModal("")}
        >
          <div className="connection-status">
            <span
              className={
                "status-dot " + (connection.lm?.online ? "online" : "")
              }
            />
            <strong>
              {connection.lm?.online
                ? "LM Studio is online"
                : "LM Studio is offline"}
            </strong>
            <button className="text-button" onClick={refresh}>
              <RefreshCw size={13} /> Refresh
            </button>
          </div>
          <Input
            label="LM Studio endpoint"
            value={settings.lm_url}
            onChange={(v: string) => setSettings({ ...settings, lm_url: v })}
          />
          <Select
            label="Prompt assistant model"
            value={settings.model}
            onChange={(v: string) => setSettings({ ...settings, model: v })}
            options={
              connection.lm?.models
                ?.map((m: any) => [
                  m.id,
                  (m.name || m.id) + (m.vision === true ? ' · reads photos' : m.vision === false ? ' · text only' : ' · photo support unknown') + (m.loaded ? " · loaded" : ""),
                ]) || [[settings.model, settings.model]]
            }
          />
          <Select
            label="Context length"
            value={settings.context_length}
            onChange={(v: string) =>
              setSettings({ ...settings, context_length: Number(v) })
            }
            options={[
              [4096, "4,096 tokens"],
              [8192, "8,192 tokens · recommended for small models"],
              [12288, "12,288 tokens"],
              [16384, "16,384 tokens"],
            ]}
          />
          <Area
            label="ComfyUI instances (one local URL per line)"
            value={(settings.comfy_urls || []).join("\n")}
            onChange={(v: string) =>
              setSettings({
                ...settings,
                comfy_urls: v.split("\n").filter(Boolean),
              })
            }
            rows={2}
          />
          <div className="service-list">
            {connection.comfy?.map((c: any) => (
              <div key={c.url}>
                <span className={"status-dot " + (c.online ? "online" : "")} />
                <code>{c.url}</code>
                <span>
                  {!c.online
                    ? "Closed"
                    : c.running || c.pending
                      ? `${c.running} running · ${c.pending} queued`
                      : "Idle"}
                </span>
              </div>
            ))}
          </div>
          <p className="callout">
            <ShieldCheck size={16} /> Studio refuses to interrupt running or
            queued ComfyUI jobs. Enable the optional GPU guard in the ComfyUI
            panel to protect normal Run-button submissions too.
          </p>
          <div className="modal-actions">
            <button
              className="quiet"
              onClick={() =>
                run("Saving connections", async () => {
                  setSettings(await api("/settings", settings));
                  toast("Connections saved.");
                  refresh();
                })
              }
            >
              Save connection
            </button>
            <button
              className="primary"
              disabled={!!busy}
              onClick={() =>
                run("Preparing vision model", async () => {
                  setSettings(await api("/settings", settings));
                  const result = await api("/gpu/prepare-ai", {});
                  toast("Vision model loaded. H3 is out of GPU memory.");
                  refresh();
                })
              }
            >
              <Sparkles size={14} /> Prepare AI
            </button>
            <button
              disabled={!!busy}
              onClick={() =>
                run("Preparing H3", async () => {
                  await api("/gpu/prepare-h3", {});
                  toast("LM Studio unloaded. Ready for H3.");
                  refresh();
                })
              }
            >
              Prepare H3
            </button>
          </div>
        </Modal>
      )}
      {modal === "files" && (
        <Modal
          title="Files & outputs"
          subtitle="Find your prompts, projects and videos on this computer."
          onClose={() => setModal("")}
        >
          <FilesOutputs />
        </Modal>
      )}
      {modal === "projects" && (
        <Modal
          title="Your projects"
          subtitle="Saved on this computer, including references and exact dialogue."
          onClose={() => setModal("")}
        >
          <button className="quiet" onClick={() => setModal("files")}>
            <FolderOpen size={14} /> Files & outputs
          </button>
          <div className="project-list">
            {projects.map((item) => (
              <button key={item.id} onClick={() => loadProject(item.id)}>
                <Clapperboard size={17} />
                <span>
                  <strong>{item.title}</strong>
                  <small>
                    {item.mode.toUpperCase()} · {item.duration}s
                  </small>
                </span>
                <ArrowUpRight size={14} />
              </button>
            ))}
            {!projects.length && (
              <p className="help">
                Your first project will appear here as you edit.
              </p>
            )}
          </div>
          <input
            ref={projectInput}
            hidden
            type="file"
            accept=".zip,.json"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file)
                run("Importing project", async () => {
                  const form = new FormData();
                  form.append("file", file);
                  await saveNow();
                  const next = await api("/projects/import", undefined, form);
                  setP(next);
                  setSelectedShot(next.shots[0]?.id);
                  setModal("");
                  toast("Project imported.");
                });
              e.target.value = "";
            }}
          />
          <div className="modal-actions">
            <button className="primary" onClick={createNew}>
              <Plus size={14} /> New project
            </button>
            <button onClick={() => projectInput.current?.click()}>
              <Upload size={14} /> Import project
            </button>
            <button
              onClick={() =>
                run("Exporting project", async () => {
                  await saveNow();
                  window.location.href = "/api/projects/" + p.id + "/export";
                })
              }
            >
              <Download size={14} /> Export with images
            </button>
          </div>
        </Modal>
      )}
      {modal === "proposal" && proposal && (
        <Modal
          wide
          title="Review the AI suggestion"
          subtitle={`${proposal.seconds?.toFixed(1) || "—"} seconds · ${proposal.kind === "assist" ? "A focused field change" : "A proposed scene plan"} · Your current project has not changed.`}
          onClose={() => setModal("")}
        >
          <div className="proposal-scroll">
            <p className="callout">
              <Lock size={15} />
              {proposal.notice ||
                "Source facts, reference bindings and dialogue were preserved. Review the suggested wording before applying."}
            </p>
            {proposal.observations?.length > 0 && (
              <details open>
                <summary>
                  Image observations to approve ({proposal.observations.length})
                </summary>
                {proposal.observations.map((o: any) => (
                  <div className="proposal-observation" key={o.asset_id}>
                    <img
                      src={"/api/assets/" + o.asset_id + "/thumbnail"}
                      alt={o.name}
                    />
                    <div>
                      <strong>{o.name}</strong>
                      <p>{o.observation}</p>
                      {o.uncertainties && (
                        <small>
                          {Array.isArray(o.uncertainties)
                            ? o.uncertainties.join("; ")
                            : o.uncertainties}
                        </small>
                      )}
                    </div>
                  </div>
                ))}
              </details>
            )}
            {proposal.kind === "assist" ? (
              <div className="diff-grid">
                <div>
                  <span className="eyebrow">CURRENT</span>
                  <pre>
                    {typeof (shot as any)[proposal.proposal.field] === "string"
                      ? (shot as any)[proposal.proposal.field]
                      : JSON.stringify(
                          (shot as any)[proposal.proposal.field],
                          null,
                          2,
                        )}
                  </pre>
                </div>
                <div>
                  <span className="eyebrow">SUGGESTED</span>
                  <pre>
                    {typeof proposal.proposal.value === "string"
                      ? proposal.proposal.value
                      : JSON.stringify(proposal.proposal.value, null, 2)}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="proposed-shots">
                {proposal.candidate.shots.map((s: any, i: number) => (
                  <article key={s.id}>
                    <span className="eyebrow">
                      SHOT {i + 1} · {s.duration.toFixed(2)}s
                    </span>
                    <h3>{s.action}</h3>
                    <p>
                      {s.camera.framing} · {s.camera.movement} ·{" "}
                      {s.camera.speed}
                    </p>
                    {s.setting && <p>{s.setting}</p>}
                    {s.sound && (
                      <p>
                        <Mic size={12} /> {s.sound}
                      </p>
                    )}
                    {s.final_state && (
                      <p>
                        <strong>Final state:</strong> {s.final_state}
                      </p>
                    )}
                  </article>
                ))}
              </div>
            )}
            {proposal.compiled?.issues
              ?.filter((x: any) => x.severity === "error")
              .map((i: any, index: number) => (
                <div className="issue error" key={index}>
                  {i.message}
                </div>
              ))}
            <details>
              <summary>Preview compiled H3 prompt</summary>
              <pre className="proposal-prompt">
                {proposal.compiled?.prompt ||
                  "Resolve the proposal errors before compiling."}
              </pre>
            </details>
          </div>
          <div className="modal-actions">
            <button className="quiet" onClick={() => setModal("")}>
              Keep current version
            </button>
            <button
              className="primary"
              onClick={acceptProposal}
              disabled={proposal.compiled?.valid === false}
            >
              <Check size={14} /> Accept suggestion
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
