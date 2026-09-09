import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Film,
  Gamepad2,
  GitBranch,
  ImagePlus,
  LoaderCircle,
  MessageSquare,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  Shuffle,
  Sparkles,
  X,
} from "lucide-react";
import type { Asset, Project } from "./model";
import {
  ensurePromptTags,
  renamePromptTag,
  replacePhoto,
  validTag,
} from "./tags";
import type { VideoJob } from "./VideoWorkspace";
import LiveRenderProgress from "./LiveRenderProgress";
import UpscaleButton from "./UpscaleButton";
import {
  DEFAULT_STORY_SETTINGS,
  PIXEL_STYLE,
  RUNNING_STORY_STATUSES,
  storyChoices,
  storyCurrentVideo,
  storyVideos,
  storyTurnLabel,
  storyTurnPending,
  type Story,
  type StoryPlan,
  type StorySettings,
  type StoryTurn,
} from "./storyTypes";
import { useStorySession } from "./useStorySession";
import { GameActionQueue, useGameActionQueue } from "./GameActionQueue";
import { GameTurnProgress } from "./GameTurnProgress";
import GameEditor from "./GameEditor";
import MotionLab from "./MotionLab";
import { GameSoundtrack, GameVoiceInput } from "./GameAudio";
import { focusGameInventory, GameActions, GameReceipt, GameStages, isInventoryCommand } from "./GameControls";
import { blankGameProject, configurationFromStory, emptyWorld, ensurePlayer, prepareGameConfiguration } from "./gameConfiguration";
import { api } from "./api";
import type { GameGuide, GameIntent, StoryConfiguration } from "./storyTypes";
import "./GameStudio.css";

export type GameStudioProps = {
  project: Project;
  modelPicker?: ReactNode;
  onAddFiles?: (files: File[]) => Promise<void>;
  onUploadFiles?: (files: File[]) => Promise<Asset[]>;
  onStudio: () => void;
  initialSourceRunId?: string;
  initialSourceKey?: string | number;
};
function hasVisibleObservation(turn: StoryTurn): boolean {
  const observation = turn.observation as { observed_state?: unknown } | null | undefined;
  return typeof observation?.observed_state === "string" && observation.observed_state.trim().length > 0;
}
export async function openCreatedGame(
  created: Story,
  sendTurn: (
    message: string,
    duration?: number,
    planned?: StoryPlan,
    storyId?: string,
  ) => Promise<unknown>,
) {
  // A recovered creation may already have an opening, a failed turn, or a Studio source.
  if (created.active_run_id || created.turns.length) return;
  await sendTurn(
    "Begin the story. Establish where I am, who is here, and a clear first moment I can react to.",
    created.settings.duration,
    undefined,
    created.id,
  );
}
const STARTER_MOVES = [
  {
    title: "Look around",
    message: "I take a careful look around. What do I notice?",
  },
  {
    title: "Start a conversation",
    message: "I speak to the person nearest me and ask what is happening.",
  },
  { title: "Surprise me", message: "Surprise me" },
];
const SETUP_DRAFT = "h3-game:setup-v1.2";
function readSetupDraft(): StoryConfiguration {
  try { const v = JSON.parse(localStorage.getItem(SETUP_DRAFT) || "null"); if (v?.project?.assets && v?.world?.characters && typeof v.premise === "string") return v; } catch { /* Optional browser storage. */ }
  return { project: blankGameProject(), world: emptyWorld(), guides: [], settings: { ...DEFAULT_STORY_SETTINGS }, premise: "", player_name: "", player_character_id: "" };
}
export type GameReferenceKind =
  | "person"
  | "wardrobe"
  | "object"
  | "place"
  | "style"
  | "inspiration"
  | "other";
export type GameReferenceEdit = {
  kind?: GameReferenceKind;
  personName?: string;
  tag?: string;
  enabled?: boolean;
  removed?: boolean;
  name?: string;
};
export const GAME_REFERENCE_TYPES: [GameReferenceKind, string][] = [
  ["person", "Person / face"],
  ["wardrobe", "Clothes / outfit"],
  ["object", "Object / prop"],
  ["place", "Place / background"],
  ["style", "Visual style"],
  ["inspiration", "Inspiration only"],
  ["other", "Other reference"],
];
export function gameReferenceKind(asset: Asset): GameReferenceKind {
  if (asset.role === "context") return "inspiration";
  return (
    (
      {
        face: "person",
        character: "person",
        wardrobe: "wardrobe",
        object: "object",
        background: "place",
        style: "style",
        palette: "style",
      } as Record<string, GameReferenceKind>
    )[asset.semantic_role] || "other"
  );
}
export function gameReferenceOwner(project: Project, asset: Asset): string {
  return (
    project.subjects.find(
      (person) =>
        person.id === asset.simple_owner_id ||
        person.asset_ids.includes(asset.id),
    )?.name ||
    asset.person_name ||
    ""
  );
}
export function gameProjectWithUploads(
  project: Project,
  uploads: Asset[],
  edits: Record<string, GameReferenceEdit> = {},
  replacements: Record<string, Asset> = {},
): Project {
  const result = structuredClone(project);
  const seen = new Set(result.assets.map((asset) => asset.id));
  result.assets.push(
    ...structuredClone(uploads).filter(
      (asset) => !seen.has(asset.id) && !!seen.add(asset.id),
    ),
  );
  for (const [id, uploaded] of Object.entries(replacements))
    if (result.assets.some((asset) => asset.id === id))
      replacePhoto(result, id, structuredClone(uploaded));
  result.assets = result.assets.filter((asset) => !edits[asset.id]?.removed);
  const kept = new Set(result.assets.map((asset) => asset.id));
  for (const person of result.subjects)
    person.asset_ids = person.asset_ids.filter((id) => kept.has(id));
  ensurePromptTags(result);
  for (const asset of result.assets) {
    const edit = edits[asset.id];
    if (!edit) continue;
    const previousOwner = gameReferenceOwner(result, asset);
    if (edit.name !== undefined) asset.name = edit.name;
    if (edit.enabled !== undefined) asset.enabled = edit.enabled;
    if (edit.kind) {
      asset.role = edit.kind === "inspiration" ? "context" : "reference_image";
      if (edit.kind !== "inspiration")
        asset.semantic_role = (
          {
            person: ["face", "character"].includes(asset.semantic_role)
              ? asset.semantic_role
              : "face",
            wardrobe: "wardrobe",
            object: "object",
            place: "background",
            style: "style",
            other: "other",
          } as Record<string, string>
        )[edit.kind];
    }
    if (
      edit.personName !== undefined ||
      (edit.kind && edit.kind !== "inspiration")
    ) {
      for (const person of result.subjects)
        person.asset_ids = person.asset_ids.filter((id) => id !== asset.id);
      delete asset.simple_owner_id;
      delete asset.person_name;
      const name = (edit.personName ?? previousOwner).trim();
      if (
        name &&
        ["face", "character", "wardrobe", "object"].includes(
          asset.semantic_role,
        )
      ) {
        let person = result.subjects.find(
          (person) =>
            person.name.toLocaleLowerCase() === name.toLocaleLowerCase(),
        );
        if (!person) {
          person = {
            id: `game-person-${asset.id}`,
            name,
            description: "",
            asset_ids: [],
          };
          result.subjects.push(person);
        }
        asset.person_name = person.name;
        if (asset.semantic_role === "object") asset.simple_owner_id = person.id;
        else person.asset_ids.push(asset.id);
      }
    }
    if (edit.tag !== undefined) {
      const tag = edit.tag.trim().replace(/^@/, "").toLowerCase();
      if (
        validTag(tag) &&
        !result.assets.some(
          (other) => other.id !== asset.id && other.prompt_tag === tag,
        )
      )
        renamePromptTag(result, asset.id, tag);
      else asset.prompt_tag = tag;
    }
  }
  for (const asset of result.assets) {
    const owner = gameReferenceOwner(result, asset);
    if (owner) asset.person_name = owner;
  }
  return result;
}
export function gameReferenceIssues(project: Project): string[] {
  const issues: string[] = [],
    tags = new Set<string>();
  for (const asset of project.assets) {
    if (!validTag(asset.prompt_tag || ""))
      issues.push(`${asset.name}: use a tag such as arin-face or blue-dress.`);
    else if (tags.has(asset.prompt_tag))
      issues.push(
        `@${asset.prompt_tag} is used twice. Give each image its own tag.`,
      );
    tags.add(asset.prompt_tag);
    if (
      asset.media_type === "image" && asset.enabled !== false &&
      asset.role !== "context" &&
      ["face", "character", "wardrobe"].includes(asset.semantic_role) &&
      !gameReferenceOwner(project, asset)
    )
      issues.push(
        `${asset.name}: choose a character name so the story knows ${asset.semantic_role === "wardrobe" ? "who wears these clothes" : "who this person is"}.`,
      );
  }
  if (
    project.assets.filter(
      (asset) => asset.media_type === "image" && asset.enabled !== false && asset.role !== "context",
    ).length > 9
  )
    issues.push(
      "Use up to 9 video references. Set extra photos to Inspiration only or switch them off.",
    );
  return issues;
}
export function gameMemoryText(value: unknown): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  const row = value as Record<string, unknown>;
  for (const key of ["summary", "description", "scene", "state"])
    if (typeof row[key] === "string") return row[key] as string;
  return Object.values(row)
    .filter((v) => typeof v === "string")
    .join(" · ");
}

export function GamePlanEditor({
  plan,
  onSave,
  onCancel,
  submitting,
  actionLabel,
}: {
  plan: StoryPlan;
  onSave: (plan: StoryPlan) => void;
  onCancel: () => void;
  submitting: boolean;
  actionLabel: string;
}) {
  const [draft, setDraft] = useState<StoryPlan>(() => structuredClone(plan));
  const set = (patch: Partial<StoryPlan>) =>
    setDraft((old) => ({ ...old, ...patch }));
  const dialogue = Array.isArray(draft.dialogue) ? draft.dialogue : [];
  const requests = Array.isArray(draft.asset_requests)
    ? draft.asset_requests
    : [];
  return (
    <form
      className="game-plan-editor"
      aria-label="Edit story response"
      onSubmit={(event) => {
        event.preventDefault();
        if (draft.action.trim()) onSave(draft);
      }}
    >
      <div className="game-section-title">
        <div>
          <span className="game-eyebrow">YOUR DIRECTION</span>
          <h3>Edit this response</h3>
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={onCancel}
          aria-label="Close response editor"
        >
          <X size={18} />
        </button>
      </div>
      <label>
        What happens on screen?
        <textarea
          autoFocus
          value={draft.action}
          maxLength={6000}
          rows={4}
          onChange={(event) => set({ action: event.target.value })}
          required
        />
      </label>
      <div className="game-field-row">
        <label>
          Scene change
          <select
            value={draft.transition}
            onChange={(event) =>
              set({ transition: event.target.value as "continue" | "cut" })
            }
          >
            <option value="continue">Keep filming from this ending</option>
            <option value="cut">Cut to a new scene</option>
          </select>
        </label>
        <label>
          Place
          <input
            value={draft.setting || ""}
            maxLength={1000}
            onChange={(event) => set({ setting: event.target.value })}
          />
        </label>
      </div>
      <div className="game-dialogue-editor">
        <span>Exact dialogue</span>
        {dialogue.map((line, index) => (
          <div key={index} className="game-dialogue-row">
            <input
              aria-label={`Speaker ${index + 1}`}
              placeholder="Who speaks?"
              value={line.speaker}
              maxLength={100}
              onChange={(event) =>
                set({
                  dialogue: dialogue.map((d, i) =>
                    i === index ? { ...d, speaker: event.target.value } : d,
                  ),
                })
              }
            />
            <textarea
              aria-label={`Spoken words ${index + 1}`}
              placeholder="Their exact words…"
              value={line.text}
              maxLength={1500}
              rows={2}
              onChange={(event) =>
                set({
                  dialogue: dialogue.map((d, i) =>
                    i === index ? { ...d, text: event.target.value } : d,
                  ),
                })
              }
            />
            <button
              type="button"
              className="icon-button"
              aria-label={`Remove spoken line ${index + 1}`}
              onClick={() =>
                set({ dialogue: dialogue.filter((_, i) => i !== index) })
              }
            >
              <X size={16} />
            </button>
          </div>
        ))}
        <button
          type="button"
          className="quiet"
          onClick={() =>
            set({ dialogue: [...dialogue, { speaker: "", text: "" }] })
          }
        >
          <Plus size={14} /> Add spoken line
        </button>
      </div>
      <label>
        Where should this moment end?
        <textarea
          rows={2}
          value={draft.final_state || ""}
          maxLength={2000}
          onChange={(event) => set({ final_state: event.target.value })}
        />
      </label>
      {!!requests.length && (
        <details className="game-plan-assets">
          <summary>New scene images · {requests.length}</summary>
          {requests.map((request, index) => (
            <label key={index}>
              {String(request.name || `Reference ${index + 1}`)}
              {request.person_name ? ` · ${String(request.person_name)}` : ""}
              <textarea
                rows={2}
                aria-label={`Reference image description ${index + 1}`}
                value={String(request.prompt || "")}
                onChange={(event) =>
                  set({
                    asset_requests: requests.map((item, i) =>
                      i === index
                        ? { ...item, prompt: event.target.value }
                        : item,
                    ),
                  })
                }
              />
            </label>
          ))}
        </details>
      )}
      <p className="game-help">
        Character identities, voices, image assignments, and the rest of the
        plan stay attached to your edits.
      </p>
      <div className="game-button-row">
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="primary"
          disabled={submitting || !draft.action.trim()}
        >
          {submitting ? (
            <LoaderCircle className="game-spin" size={16} />
          ) : (
            <Play size={16} />
          )}
          {actionLabel}
        </button>
      </div>
    </form>
  );
}

export default function GameStudio({
  project,
  modelPicker,
  onAddFiles,
  onUploadFiles,
  onStudio,
  initialSourceRunId,
  initialSourceKey,
}: GameStudioProps) {
  const session = useStorySession(!!initialSourceRunId);
  const {
    story,
    stories,
    loading,
    submitting,
    pendingTicket,
    pendingCreation,
  } = session;
  const [initialSetup] = useState(readSetupDraft);
  const [setupBase, setSetupBase] = useState(initialSetup.project);
  const [setupWorld, setSetupWorld] = useState(initialSetup.world);
  const [premise, setPremise] = useState(initialSetup.premise),
    [player, setPlayer] = useState(initialSetup.player_name);
  const [playerSelection, setPlayerSelection] = useState(initialSetup.player_name || "custom");
  const [settings, setSettings] = useState<StorySettings>({
    ...DEFAULT_STORY_SETTINGS,
    ...initialSetup.settings,
  });
  const [editorDraft, setEditorDraft] = useState<StoryConfiguration | null>(null), [editorDirty, setEditorDirty] = useState(false), [editorTab, setEditorTab] = useState("cast");
  const [editorDraftKey, setEditorDraftKey] = useState("");
  const editorRevision = useRef<number | undefined>(undefined);
  const [composerMode, setComposerMode] = useState<"play" | "guide">("play"), [guideScope, setGuideScope] = useState<"next" | "persistent">("next");
  const [editingGuideId, setEditingGuideId] = useState<string | null>(null);
  const [motionProject, setMotionProject] = useState<Project | null>(null);
  const conversationNearEnd = useRef(true);
  const [playView, setPlayView] = useState<"play" | "history">("play");
  const [settingsOpen, setSettingsOpen] = useState(false),
    [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState(""),
    [localError, setLocalError] = useState("");
  const [preview, setPreview] = useState<VideoJob | null>(null),
    [editing, setEditing] = useState<{
      turn: StoryTurn;
      action: "approve" | "reroll";
    } | null>(null);
  const [starting, setStarting] = useState(false);
  const [sourceRunId, setSourceRunId] = useState(initialSourceRunId);
  const lastSource = useRef<{ runId: string; key?: string | number } | null>(
    null,
  );
  const [setupAssets, setSetupAssets] = useState<Asset[]>([]);
  const [referenceEdits, setReferenceEdits] = useState<
    Record<string, GameReferenceEdit>
  >({});
  const [referenceReplacements, setReferenceReplacements] = useState<
    Record<string, Asset>
  >({});
  const replaceInput = useRef<HTMLInputElement>(null),
    replaceTarget = useRef("");
  const [playingFilm, setPlayingFilm] = useState(false),
    [filmIndex, setFilmIndex] = useState(0);
  const composer = useRef<HTMLTextAreaElement>(null),
    playerVideo = useRef<HTMLVideoElement>(null),
    moveFeedback = useRef<HTMLDivElement>(null),
    fileInput = useRef<HTMLInputElement>(null),
    conversationEnd = useRef<HTMLDivElement>(null);
  const settingsClose = useRef<HTMLButtonElement>(null),
    actionLock = useRef(false);
  const lastTurn = story?.turns?.at(-1),
    activeTurn = [...(story?.turns || [])].reverse().find(storyTurnPending);
  const busy =
    submitting ||
    !!pendingTicket ||
    !!pendingCreation ||
    !!activeTurn ||
    starting;
  const setupLocked = submitting || starting || !!pendingCreation;
  const setupPremise = pendingCreation?.body.premise ?? premise;
  const setupPlayer = pendingCreation?.body.player_name ?? player;
  const setupSettings = pendingCreation?.body.settings ?? settings;
  const setupSourceRunId =
    pendingCreation?.body.source_run_id ??
    (pendingCreation ? undefined : sourceRunId);
  const videos = storyVideos(story),
    currentVideo = storyCurrentVideo(story);
  const playerStatus = activeTurn
    ? storyTurnLabel(activeTurn)
    : pendingTicket
      ? "Checking your previous request"
      : submitting || starting
        ? "Sending your move"
        : currentVideo
          ? "Ready for your next move"
          : storyTurnLabel(lastTurn);
  const playlist = (story?.clips || []).filter(
    (clip) => clip.status === "succeeded" && !!clip.video_url,
  );
  const selectedVideo = playingFilm
    ? playlist[filmIndex] || currentVideo
    : videos.find((clip) => clip.id === preview?.id) || (activeTurn && ["observing", "inspection_failed", "awaiting_acceptance"].includes(activeTurn.status) ? videos.find(clip => clip.id === activeTurn.run_id) : undefined) || currentVideo;
  const choices = storyChoices(story),
    displayedChoices = choices.length ? choices : STARTER_MOVES;
  const setupProject =
    pendingCreation?.body.project ??
    gameProjectWithUploads(
      setupBase,
      setupAssets,
      referenceEdits,
      referenceReplacements,
    );
  const photos = setupProject.assets.filter(
    (asset) => asset.media_type === "image",
  );
  const referenceIssues = gameReferenceIssues(setupProject);
  const setupPlayerSelection = pendingCreation
    ? setupProject.subjects.some((person) => person.name === setupPlayer)
      ? setupPlayer
      : "custom"
    : playerSelection;
  const setupReferenceEdits = pendingCreation ? {} : referenceEdits;
  const removedReferences = Object.values(setupReferenceEdits).filter(
    (edit) => edit.removed,
  ).length;
  const memory = gameMemoryText(story?.observed_state);
  const configurationKey = story ? `h3-game:configuration:${story.id}:${story.active_branch_id}` : "";
  const config: StoryConfiguration = story ? (editorDraftKey === configurationKey ? editorDraft : null) || configurationFromStory(story, setupBase) : {
    project: setupProject, world: setupWorld, guides: [], settings, premise, player_name: player,
    player_character_id: setupWorld.characters.find(c => c.control === "player")?.id || "",
  };
  const changeConfiguration = (next: StoryConfiguration) => {
    if (story) { setEditorDraft(next); setEditorDraftKey(configurationKey); setEditorDirty(true); try { localStorage.setItem(configurationKey, JSON.stringify({ value: next, revision: editorRevision.current })); } catch {} }
    else { setSetupBase(next.project); setSetupWorld(next.world); setPremise(next.premise); setPlayer(next.player_name); setPlayerSelection(next.player_name || "custom"); setSettings(next.settings); setSetupAssets([]); setReferenceEdits({}); setReferenceReplacements({}); }
  };
  const saveConfiguration = async () => {
    if (!story) { const next = ensurePlayer(config); changeConfiguration(next); return; }
    if (!editorDirty || editorDraftKey !== configurationKey) return;
    const saved = await session.patch({ ...prepareGameConfiguration(config), expected_configuration_revision: editorRevision.current });
    if (saved) { setEditorDraft(configurationFromStory(saved, config.project)); editorRevision.current = saved.configuration_revision; }
    setEditorDirty(false); try { localStorage.removeItem(configurationKey); } catch {}
  };
  useEffect(() => {
    if (!story) return;
    let restored: { value: StoryConfiguration; revision?: number } | null = null;
    try { const r = JSON.parse(localStorage.getItem(configurationKey) || "null"); if (r?.value?.project?.assets && r?.value?.world?.characters) restored = r; } catch {}
    setEditorDraft(restored?.value || configurationFromStory(story, setupBase));
    setEditorDraftKey(configurationKey);
    setEditorDirty(!!restored); editorRevision.current = restored ? restored.revision : story.configuration_revision;
  }, [configurationKey]);
  useEffect(() => {
    if (story && !editorDirty) { setEditorDraft(configurationFromStory(story, setupBase)); editorRevision.current = story.configuration_revision; }
  }, [story?.configuration_revision, story?.active_run_id, editorDirty]);
  useEffect(() => { if (!story && !pendingCreation && !sourceRunId) try { localStorage.setItem(SETUP_DRAFT, JSON.stringify(config)); } catch {} }, [setupProject, setupWorld, premise, player, settings, story?.id]);
  const draftKey = story
    ? `h3-game:draft:${story.id}:${story.active_branch_id || "main"}`
    : "";

  useEffect(() => {
    if (
      !initialSourceRunId ||
      (lastSource.current?.runId === initialSourceRunId &&
        lastSource.current?.key === initialSourceKey)
    )
      return;
    lastSource.current = { runId: initialSourceRunId, key: initialSourceKey };
    setSourceRunId(initialSourceRunId);
    setSetupBase(structuredClone(project));
    setSetupWorld(emptyWorld());
    void session.selectStory("");
    setPremise(project.story?.text || "");
    setPlayer(project.subjects?.[0]?.name || "");
    setPlayerSelection(project.subjects?.[0]?.name || "custom");
    setPreview(null);
    setEditing(null);
    setPlayingFilm(false);
    setReferenceEdits({});
    setReferenceReplacements({});
    setSetupAssets([]);
  }, [initialSourceRunId, initialSourceKey]);

  useEffect(() => {
    if (!story) return;
    setPremise(story.premise || "");
    setPlayer(story.player_name || "");
    setSettings({ ...DEFAULT_STORY_SETTINGS, ...story.settings });
    setPreview(null);
    setEditing(null);
    setPlayingFilm(false);
    setFilmIndex(0);
    // Polling does not replace unsaved settings or edited responses.
  }, [story?.id]);
  useEffect(() => {
    let restored = "";
    try {
      if (draftKey) restored = localStorage.getItem(draftKey) || "";
    } catch {
      /* Browser storage is optional. */
    }
    setMessage(restored);
  }, [draftKey]);
  const changeMessage = (value: string) => {
    setMessage(value);
    try {
      if (draftKey) localStorage.setItem(draftKey, value);
    } catch {
      /* Keep the in-memory draft. */
    }
  };
  useEffect(() => {
    if (playView === "history" && conversationNearEnd.current) {
      const list = conversationEnd.current?.parentElement;
      if (list) list.scrollTop = list.scrollHeight;
    }
  }, [story?.turns.length, lastTurn?.status, playView]);
  useEffect(() => {
    if (!settingsOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    settingsClose.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSettingsOpen(false);
    };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [settingsOpen]);
  const perform = async (action: () => Promise<unknown>) => {
    if (actionLock.current) return;
    actionLock.current = true;
    setLocalError("");
    try {
      return await action();
    } catch (e) {
      setLocalError((e as Error).message);
    } finally {
      actionLock.current = false;
    }
  };
  const start = (event: FormEvent) => {
    event.preventDefault();
    if (!premise.trim() || !player.trim() || busy || referenceIssues.length)
      return;
    void perform(async () => {
      setStarting(true);
      try {
        const openingConfig = prepareGameConfiguration(config);
        const gameProject = openingConfig.project;
        const created = await session.create(gameProject, {
          premise: gameProject.story.text,
          player_name: player.trim(),
          settings,
          world: openingConfig.world,
          guides: openingConfig.guides,
          player_character_id: openingConfig.player_character_id,
          ...(sourceRunId ? { source_run_id: sourceRunId } : {}),
        });
        setSourceRunId(undefined);
        await openCreatedGame(created, session.sendTurn);
      } finally {
        setStarting(false);
      }
    });
  };
  const resumeCreation = () => {
    if (submitting || starting || !pendingCreation) return;
    void perform(async () => {
      setStarting(true);
      try {
        const created = await session.resumeCreation();
        setSourceRunId(undefined);
        await openCreatedGame(created, session.sendTurn);
      } finally {
        setStarting(false);
      }
    });
  };
  const moveQueue = useGameActionQueue(story, busy, async move => {
      try { await saveConfiguration(); }
      catch (error) { throw Object.assign(error as Error, { notSubmitted: true }); }
      const result = await session.sendTurn(move.message, config.settings.duration || 5, undefined, story?.id, move.intent, move.id);
      setPreview(null);
      setPlayingFilm(false);
      return result;
  });
  useEffect(() => {
    if (playView === "play" && activeTurn && ["awaiting_review", "awaiting_acceptance", "inspection_failed"].includes(activeTurn.status))
      moveFeedback.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeTurn?.id, activeTurn?.status, playView]);
  const send = (text: string, intent?: GameIntent) => {
    if (story && !intent && isInventoryCommand(text)) {
      focusGameInventory(story.id);
      changeMessage("");
      return;
    }
    if (moveQueue.enqueue(text, intent)) {
      changeMessage("");
      moveFeedback.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  };
  const takeAction = (
    turn: StoryTurn,
    action: "approve" | "retry" | "cancel" | "reroll" | "edit" | "resume" | "retry-inspection" | "accept-intended" | "accept-visible" | "stop-and-apply",
    plan?: StoryPlan,
  ) =>
    void perform(async () => {
      await session.turnAction(turn, action, plan);
      setEditing(null);
      setPreview(null);
      setPlayingFilm(false);
    });
  const addFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setLocalError("");
    try {
      if (onUploadFiles) {
        const added = await onUploadFiles(Array.from(files));
        setSetupAssets((old) => [...old, ...added]);
      } else if (onAddFiles) await onAddFiles(Array.from(files));
      else
        throw new Error(
          "Photo upload is not connected. Add photos in Studio before starting your story.",
        );
    } catch (e) {
      setLocalError((e as Error).message);
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };
  const editReference = (id: string, patch: GameReferenceEdit) =>
    setReferenceEdits((old) => ({ ...old, [id]: { ...old[id], ...patch } }));
  const replaceReference = async (files: FileList | null) => {
    const id = replaceTarget.current;
    if (!files?.length || !id || !onUploadFiles) return;
    setUploading(true);
    setLocalError("");
    try {
      const [uploaded] = await onUploadFiles([files[0]]);
      if (!uploaded || uploaded.media_type !== "image")
        throw new Error("Choose an image to replace this reference.");
      setReferenceReplacements((old) => ({ ...old, [id]: uploaded }));
      setReferenceEdits((old) => {
        const next = { ...old };
        if (next[id]) {
          next[uploaded.id] = next[id];
          delete next[id];
        }
        return next;
      });
    } catch (e) {
      setLocalError((e as Error).message);
    } finally {
      setUploading(false);
      replaceTarget.current = "";
      if (replaceInput.current) replaceInput.current.value = "";
    }
  };
  const saveGuide = () => {
    if (!story || !message.trim()) return;
    const oldGuide = config.guides.find(g => g.id === editingGuideId);
    const guide: GameGuide = { id: oldGuide?.id || crypto.randomUUID(), revision: (oldGuide?.revision || 0) + 1, text: message.trim(), scope: guideScope, enabled: true };
    const next = { ...config, guides: oldGuide ? config.guides.map(g => g.id === oldGuide.id ? guide : g) : [...config.guides, guide] };
    changeConfiguration(next);
    void perform(async () => {
      const saved = await session.patch({ ...prepareGameConfiguration(next), expected_configuration_revision: editorRevision.current });
      if (saved) { setEditorDraft(configurationFromStory(saved, next.project)); editorRevision.current = saved.configuration_revision; }
      setEditorDirty(false); try { localStorage.removeItem(configurationKey); } catch {}
      changeMessage("");
      setEditingGuideId(null);
    });
  };
  const submitComposer = () => composerMode === "guide" ? saveGuide() : send(message);
  const resetGame = () => {
    void session.selectStory("");
    setPreview(null);
    setEditing(null);
    setPlayingFilm(false);
    setSetupAssets([]);
    setReferenceEdits({});
    setReferenceReplacements({});
    setSourceRunId(undefined);
    setSetupBase(blankGameProject()); setSetupWorld(emptyWorld()); setEditorDraft(null); setEditorDirty(false);
    setPremise("");
    setPlayer("");
    setPlayerSelection("custom");
    setSettings({ ...DEFAULT_STORY_SETTINGS });
  };

  return (
    <main className={`game-studio${story ? " has-story" : ""}${settingsOpen ? " is-editor-open" : ""}`} aria-label="Story game">
      <header className="game-topbar">
        <div className="game-brand">
          <span className="game-brand-icon">
            <Gamepad2 size={23} />
          </span>
          <div>
            <span className="game-eyebrow">H3 PROMPT STUDIO / GAME</span>
            <h1>{story?.title || "Your story. Your next move."}</h1>
          </div>
        </div>
        <div className="game-top-actions">
          <button className="quiet" onClick={onStudio}>
            <ArrowLeft size={15} /> Studio
          </button>
          <button
            className="quiet"
            aria-label="Game settings"
            disabled={!!pendingCreation}
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 size={17} />
            <span>Edit game</span>
          </button>
        </div>
      </header>
      <div className="game-session-bar">
        <label>
          <span>Saved stories</span>
          <select
            aria-label="Saved game"
            value={session.selectedId}
            disabled={setupLocked}
            onChange={(event) => {
              setLocalError("");
              void session.selectStory(event.target.value);
            }}
          >
            <option value="">Start a new game</option>
            {stories.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title || item.premise?.slice(0, 55) || "Untitled story"}
              </option>
            ))}
          </select>
        </label>
        {story && (
          <button className="quiet" disabled={setupLocked} onClick={resetGame}>
            <Plus size={14} /> New game
          </button>
        )}
        <span className="game-session-note">
          {story
            ? `You play ${story.player_name || "your character"}`
            : "Local models · Your references · Your direction"}
        </span>
        {story && <button className="quiet" onClick={() => { setEditorTab("photos"); setSettingsOpen(true); }}><ImagePlus size={16}/> Add photo / sound</button>}
        {story && <button className="quiet" onClick={() => { setEditorTab("render"); setSettingsOpen(true); }}>{config.settings.resolution} MP · {config.settings.steps} steps · {String(config.settings.aspect_ratio || config.project.aspect_ratio)}{editorDirty ? " · unsaved changes" : ""}</button>}
        {story && <details className="game-session-tools"><summary>More tools</summary><button className="quiet" onClick={() => changeConfiguration({ ...config, settings: { ...config.settings, experimental_preview: true, resolution: "0.2", duration: 3, steps: 8, style: PIXEL_STYLE } })}>Pixel preview · 0.2 MP</button><button className="quiet" onClick={() => setMotionProject(prepareGameConfiguration(config).project)}>Compare motion prompts</button></details>}
      </div>
      {(localError || session.error) && (
        <div className="game-alert" role="alert">
          <span>{localError || session.error}</span>
          {!pendingCreation && (
            <button
              className="quiet"
              disabled={submitting}
              onClick={() => void perform(() => session.refresh())}
            >
              <RefreshCw size={14} /> Check connection
            </button>
          )}
        </div>
      )}
      {pendingCreation && (
        <div
          className="game-alert game-alert-reconnect"
          role="status"
          aria-label="Unconfirmed game creation"
        >
          <div>
            <strong>Your original game setup is saved.</strong>
            <p>
              Its creation was not confirmed. Resume the original request to
              check for the saved game and avoid creating another one. The setup
              below stays locked until that request is resolved.
            </p>
            <p>
              You play {pendingCreation.body.player_name} ·{" "}
              {pendingCreation.body.settings.duration} seconds ·{" "}
              {pendingCreation.body.settings.steps} steps
              {pendingCreation.body.source_run_id
                ? " · From your Studio video"
                : " · New opening"}
            </p>
            <p>{pendingCreation.body.premise}</p>
          </div>
          <button
            disabled={submitting || starting || loading}
            onClick={resumeCreation}
          >
            <RefreshCw size={15} />{" "}
            {submitting || starting
              ? "Checking original creation…"
              : "Resume original creation"}
          </button>
        </div>
      )}
      {pendingTicket && !submitting && (
        <div className="game-alert game-alert-reconnect" role="status">
          <div>
            <strong>Your previous request is being checked.</strong>
            <p>
              Its request ID is saved. Reconnect or resume that same request
              before making another move.
            </p>
          </div>
          <button
            disabled={submitting}
            onClick={() => void perform(() => session.resumePending())}
          >
            <RefreshCw size={15} /> Resume request
          </button>
        </div>
      )}
      {loading && (
        <div className="game-loading" role="status">
          <LoaderCircle size={20} className="game-spin" /> Restoring your story…
        </div>
      )}

      {!story && !loading && (
        <section className="game-setup" aria-label="New game setup">
          <fieldset
            disabled={setupLocked}
            style={{ display: "contents" }}
            aria-label="Opening setup"
          >
            <div className="game-setup-intro">
              <span className="game-eyebrow">PLAY THE MAIN CHARACTER</span>
              <h2>
                You act.
                <br />
                The story answers.
              </h2>
              <p>
                Describe a world, choose who you play, and make your first move.
                Each response can become a video you watch right here.
              </p>
              <div className="game-setup-steps">
                <span>
                  <MessageSquare size={17} /> Make a move
                </span>
                <span>
                  <Sparkles size={17} /> The story answers
                </span>
                <span>
                  <Film size={17} /> See what happens
                </span>
              </div>
              <div className="game-reference-heading">
                <h3>
                  {photos.length
                    ? `${photos.filter((asset) => asset.enabled !== false).length} reference photos ready`
                    : "Bring your characters"}
                </h3>
                <button
                  className="quiet"
                  disabled={uploading || starting}
                  type="button"
                  onClick={() => fileInput.current?.click()}
                >
                  {uploading ? (
                    <LoaderCircle size={14} className="game-spin" />
                  ) : (
                    <ImagePlus size={14} />
                  )}{" "}
                  Add photos
                </button>
              </div>
              <button type="button" className="quiet" onClick={() => {
                setSetupBase(structuredClone(project)); setPremise(project.story?.text || "");
                setSetupWorld(configurationFromStory({ project, settings, player_name: player, premise } as Story, project).world);
                setSetupAssets([]); setReferenceEdits({}); setReferenceReplacements({});
              }}>Import current Studio cast & photos</button>
              <input
                ref={fileInput}
                type="file"
                accept="image/*"
                multiple
                hidden
                onChange={(event) => void addFiles(event.target.files)}
              />
              <input
                ref={replaceInput}
                type="file"
                accept="image/*"
                hidden
                onChange={(event) => void replaceReference(event.target.files)}
              />
              <datalist id="game-reference-people">
                {setupProject.subjects
                  .filter((person) => person.name)
                  .map((person) => (
                    <option key={person.id} value={person.name} />
                  ))}
              </datalist>
              <div className="game-reference-grid">
                {photos.map((asset) => (
                  <figure
                    key={asset.id}
                    className={`game-reference-card ${asset.enabled === false ? "is-disabled" : ""}`}
                  >
                    <img
                      src={`/api/assets/${encodeURIComponent(asset.id)}/file`}
                      alt={asset.name || "Reference photo"}
                      loading="lazy"
                    />
                    <figcaption>
                      <strong>{asset.name}</strong>
                      <span>
                        {
                          GAME_REFERENCE_TYPES.find(
                            ([kind]) => kind === gameReferenceKind(asset),
                          )?.[1]
                        }
                        {gameReferenceOwner(setupProject, asset)
                          ? ` · ${gameReferenceOwner(setupProject, asset)}`
                          : ""}
                      </span>
                    </figcaption>
                    <div className="game-reference-use">
                      <label>
                        <input
                          type="checkbox"
                          checked={asset.enabled !== false}
                          disabled={uploading || starting}
                          onChange={(event) =>
                            editReference(asset.id, {
                              enabled: event.target.checked,
                            })
                          }
                        />
                        Use in game
                      </label>
                      <button
                        type="button"
                        className="text-button"
                        title="Insert this reference tag in the premise"
                        onClick={() =>
                          setPremise(
                            (old) =>
                              `${old}${old && !old.endsWith(" ") ? " " : ""}@${asset.prompt_tag}`,
                          )
                        }
                      >
                        @{asset.prompt_tag}
                      </button>
                    </div>
                    <details
                      open={setupAssets.some((photo) => photo.id === asset.id)}
                    >
                      <summary>Assign / change photo</summary>
                      <label>
                        Photo name
                        <input
                          aria-label={`Photo name for ${asset.name}`}
                          value={asset.name}
                          maxLength={100}
                          onChange={(event) =>
                            editReference(asset.id, {
                              name: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        Type
                        <select
                          aria-label={`Reference type for ${asset.name}`}
                          value={gameReferenceKind(asset)}
                          onChange={(event) =>
                            editReference(asset.id, {
                              kind: event.target.value as GameReferenceKind,
                            })
                          }
                        >
                          {GAME_REFERENCE_TYPES.map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {["person", "wardrobe", "object"].includes(
                        gameReferenceKind(asset),
                      ) && (
                        <label>
                          {gameReferenceKind(asset) === "person"
                            ? "Character name"
                            : gameReferenceKind(asset) === "wardrobe"
                              ? "Who wears this?"
                              : "Starts with (optional)"}
                          <input
                            list="game-reference-people"
                            aria-label={`Character or owner for ${asset.name}`}
                            value={
                              setupReferenceEdits[asset.id]?.personName ??
                              gameReferenceOwner(setupProject, asset)
                            }
                            placeholder="Choose or type a character name"
                            maxLength={100}
                            onChange={(event) =>
                              editReference(asset.id, {
                                personName: event.target.value,
                              })
                            }
                          />
                        </label>
                      )}
                      <label>
                        Prompt tag
                        <input
                          aria-label={`Prompt tag for ${asset.name}`}
                          value={
                            setupReferenceEdits[asset.id]?.tag ??
                            asset.prompt_tag ??
                            ""
                          }
                          maxLength={65}
                          placeholder="arin-face"
                          onChange={(event) =>
                            editReference(asset.id, { tag: event.target.value })
                          }
                        />
                      </label>
                      <div className="game-reference-buttons">
                        {onUploadFiles && (
                          <button
                            type="button"
                            className="quiet"
                            disabled={uploading || starting}
                            onClick={() => {
                              replaceTarget.current = asset.id;
                              replaceInput.current?.click();
                            }}
                          >
                            <RefreshCw size={12} /> Replace image
                          </button>
                        )}
                        <button
                          type="button"
                          className="quiet"
                          disabled={uploading || starting}
                          onClick={() =>
                            editReference(asset.id, { removed: true })
                          }
                        >
                          <X size={12} /> Remove
                        </button>
                      </div>
                    </details>
                  </figure>
                ))}
              </div>
              {removedReferences > 0 && (
                <button
                  type="button"
                  className="quiet"
                  onClick={() =>
                    setReferenceEdits((old) =>
                      Object.fromEntries(
                        Object.entries(old).map(([id, edit]) => [
                          id,
                          { ...edit, removed: false },
                        ]),
                      ),
                    )
                  }
                >
                  Restore removed photos ({removedReferences})
                </button>
              )}
              {referenceIssues.length > 0 && (
                <div className="game-reference-issues" role="status">
                  {referenceIssues.slice(0, 3).map((issue) => (
                    <p key={issue}>{issue}</p>
                  ))}
                </div>
              )}
              <p className="game-help">
                Assign each face and outfit to a character. Click a tag to
                mention that image in your premise. These changes belong to this
                Game; your Studio photos stay saved as they are.
              </p>
            </div>
            <form className="game-setup-form" onSubmit={start}>
              <h3>Set the opening</h3>
              {setupSourceRunId && (
                <div className="game-source-notice">
                  <Film size={18} />
                  <div>
                    <strong>Continue from your Studio video</strong>
                    <p>
                      The existing ending is your starting point. Your next move
                      decides what happens after it.
                    </p>
                  </div>
                </div>
              )}
              <label>
                What is this story about?
                <textarea
                  value={setupPremise}
                  onChange={(event) => setPremise(event.target.value)}
                  maxLength={5000}
                  rows={5}
                  placeholder="Describe the world you want to play in, the situation, and what matters to your character."
                  required
                />
              </label>
              <label>
                I play
                <select
                  value={setupPlayerSelection}
                  onChange={(event) => {
                    setPlayerSelection(event.target.value);
                    setPlayer(
                      event.target.value === "custom" ? "" : event.target.value,
                    );
                  }}
                >
                  <option value="custom">My own character</option>
                  {setupProject.subjects
                    ?.filter((person) => person.name)
                    .map((person) => (
                      <option key={person.id} value={person.name}>
                        {person.name}
                      </option>
                    ))}
                </select>
              </label>
              {setupPlayerSelection === "custom" && (
                <label>
                  Character name
                  <input
                    value={setupPlayer}
                    maxLength={100}
                    required
                    placeholder="Your character’s name"
                    onChange={(event) => setPlayer(event.target.value)}
                  />
                </label>
              )}
              <label>
                Story style
                <input
                  value={setupSettings.style || ""}
                  onChange={(event) =>
                    setSettings((old) => ({
                      ...old,
                      style: event.target.value,
                    }))
                  }
                  maxLength={160}
                  placeholder="Cinematic mystery, playful adventure, quiet and natural…"
                />
              </label>
              <label className="game-checkbox">
                <input
                  type="checkbox"
                  checked={setupSettings.review_before_render}
                  onChange={(event) =>
                    setSettings((old) => ({
                      ...old,
                      review_before_render: event.target.checked,
                    }))
                  }
                />
                <span>
                  <strong>Let me review each response first</strong>
                  <small>
                    Edit the action and dialogue before making a video.
                  </small>
                </span>
              </label>
              <div className="game-defaults">
                <span>{setupSettings.resolution} MP</span>
                <span>{setupSettings.steps} steps</span>
                <span>{setupSettings.duration} seconds</span>
                <button
                  className="text-button"
                  type="button"
                  onClick={() => setSettingsOpen(true)}
                >
                  Change settings
                </button>
              </div>
              <button type="button" className="quiet" onClick={() => {
                setSettings(old => ({ ...old, experimental_preview: true, resolution: "0.2", duration: 3, steps: 8, style: PIXEL_STYLE }));
              }}>Pixel preview · ~0.2 MP / 3 seconds</button>
              <button
                className="primary game-start"
                disabled={
                  starting ||
                  submitting ||
                  !!pendingCreation ||
                  uploading ||
                  referenceIssues.length > 0 ||
                  !premise.trim() ||
                  !player.trim()
                }
              >
                {starting ? (
                  <LoaderCircle className="game-spin" size={18} />
                ) : (
                  <Play size={18} />
                )}
                {pendingCreation
                  ? "Original setup saved"
                  : setupSourceRunId
                    ? "Play from this video"
                    : "Start game"}
                <ArrowRight size={18} />
              </button>
              <p className="game-help">
                The selected assistant is only used after you start. Opening
                Game does not load a model or generate a video.
              </p>
            </form>
          </fieldset>
        </section>
      )}

      {story && (
        <>
          <nav className="game-view-tabs" aria-label="Game views">
            <button aria-pressed={playView === "play" && !settingsOpen} onClick={() => { setPlayView("play"); setSettingsOpen(false); }}><Play size={17}/> Play</button>
            <button aria-pressed={settingsOpen} onClick={() => setSettingsOpen(true)}><Settings2 size={17}/> Edit</button>
            <button aria-pressed={playView === "history" && !settingsOpen} onClick={() => { setPlayView("history"); setSettingsOpen(false); }}><MessageSquare size={17}/> History <span>{story.turns.length}</span></button>
          </nav>
          <section className={`game-play-layout view-${playView}`}>

            <div className="game-stage">
              <div className="game-section-title">
                <div>
                  <span className="game-eyebrow">
                    {playingFilm
                      ? "YOUR WHOLE STORY"
                      : preview
                        ? "EARLIER TAKE"
                        : "YOUR STORY ON SCREEN"}
                  </span>
                  <h2>
                    {playingFilm
                      ? `Scene ${filmIndex + 1} of ${playlist.length}`
                      : preview
                        ? preview.title || "Previewing a take"
                        : "See the next moment."}
                  </h2>
                </div>
                <span
                  className={`game-status ${activeTurn ? "is-working" : ""}`}
                  role="status"
                >
                  {activeTurn && RUNNING_STORY_STATUSES.has(activeTurn.status) ? (
                    <LoaderCircle className="game-spin" size={13} />
                  ) : (
                    <Check size={13} />
                  )}
                  {playerStatus}
                </span>
              </div>
              <div ref={moveFeedback} className="game-feedback-anchor" hidden={playView !== "play"}>
                <GameTurnProgress turn={activeTurn || lastTurn} queue={moveQueue.queue} now={moveQueue.now} onReview={() => setPlayView("history")}>
                  {activeTurn && ["awaiting_acceptance", "inspection_failed"].includes(activeTurn.status) && <>
                    <button type="button" disabled={submitting || !!pendingTicket} onClick={() => takeAction(activeTurn, "retry-inspection")}>Retry ending inspection</button>
                    <button type="button" disabled={submitting || !!pendingTicket} onClick={() => takeAction(activeTurn, "accept-intended")}>Use intended story</button>
                    <button type="button" className="primary" disabled={submitting || !!pendingTicket || !hasVisibleObservation(activeTurn)} title="Keep the visible outcome and only changes supported by the inspected ending." onClick={() => takeAction(activeTurn, "accept-visible")}>Use visible result</button>
                  </>}
                  {activeTurn?.status === "awaiting_review" && <button type="button" className="primary" disabled={submitting || !!pendingTicket} onClick={() => takeAction(activeTurn, "approve")}>Render this scene</button>}
                </GameTurnProgress>
              </div>
              <div className="game-video-control-row">
              <div className="game-player" style={{ aspectRatio: selectedVideo?.width && selectedVideo.height ? `${selectedVideo.width} / ${selectedVideo.height}` : String(config.settings.aspect_ratio || config.project.aspect_ratio).replace(":", "/") }}>
                {selectedVideo?.video_url ? (
                  <video
                    ref={playerVideo}
                    key={`${playingFilm ? "film" : "take"}:${selectedVideo.id}`}
                    controls
                    playsInline
                    preload="metadata"
                    poster={selectedVideo.ending_image_url || undefined}
                    src={
                      selectedVideo.scene_video_url || selectedVideo.video_url
                    }
                    autoPlay={playingFilm}
                    onEnded={() => {
                      if (playingFilm && filmIndex + 1 < playlist.length)
                        setFilmIndex((index) => index + 1);
                    }}
                    aria-label="Game video"
                  />
                ) : (
                  <div className="game-player-empty">
                    <Film size={52} strokeWidth={1} />
                    <h3>
                      {activeTurn
                        ? storyTurnLabel(activeTurn)
                        : "Your first scene starts here"}
                    </h3>
                    <p>
                      {activeTurn?.status === "awaiting_review"
                        ? "Review the response beside the player, then render the scene."
                        : "Make a move below. Your finished video will appear here."}
                    </p>
                  </div>
                )}
              </div>
              <GameActions key={story.id} story={story} viewedRunId={selectedVideo?.id} disabled={false} onRefreshStory={() => session.refresh()} onAction={send}/>
              </div>
              {selectedVideo?.video_url && <div className="game-current-video-actions"><span>{activeTurn?.run_id === selectedVideo.id ? "New result" : preview || playingFilm ? "Selected take" : "Accepted ending"} · replay to watch the movement</span><button type="button" onClick={() => { const video = playerVideo.current; if (video) { video.currentTime = 0; void video.play().catch(error => setLocalError(`Playback could not start: ${error.message}`)); } }}><Play size={15}/> Replay this scene</button></div>}
            <form
              className="game-composer"
              onSubmit={(event) => {
                event.preventDefault();
                submitComposer();
              }}
            >
              <div className="game-composer-modes" aria-label="Message mode"><button type="button" aria-pressed={composerMode === "play"} onClick={() => setComposerMode("play")}>Play · my action or speech</button><button type="button" aria-pressed={composerMode === "guide"} onClick={() => setComposerMode("guide")}>Guide · direct the game</button>{composerMode === "guide" && <label>Apply guidance<select value={guideScope} onChange={e => setGuideScope(e.target.value as "next" | "persistent")}><option value="next">Next response</option><option value="persistent">From now on</option></select></label>}</div>
              {onUploadFiles && <GameVoiceInput key={story.id} onUpload={onUploadFiles} onAsset={(asset, reference) => changeConfiguration({ ...config, project: { ...config.project, assets: [...config.project.assets, { ...asset, role: reference ? "reference_audio" : "context", enabled: reference, audio_use: reference ? "reference" : "context", simple_owner_id: config.player_character_id }] } })} onText={text => changeMessage(message.trim() ? `${message.trim()}\n${text}` : text)}/>}
              <label className="game-composer-label" htmlFor="game-next-move">
                {composerMode === "play" ? "Or write your own move" : "Tell the assistant how to direct the game"}
              </label>
              <div>
                <textarea
                  id="game-next-move"
                  ref={composer}
                  value={message}
                  maxLength={5000}
                  rows={2}
                  placeholder={composerMode === "play" ? "I step forward and say, “…”" : "Change a character’s behavior, the mood, or where the story is going. This will not be spoken."}
                  onChange={(event) => changeMessage(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      (event.ctrlKey || event.metaKey)
                    ) {
                      event.preventDefault();
                      submitComposer();
                    }
                  }}
                />
                <button className="primary" disabled={(composerMode === "guide" && (submitting || !!pendingTicket)) || !message.trim()}>
                  {submitting ? (
                    <LoaderCircle size={18} className="game-spin" />
                  ) : (
                    <Send size={18} />
                  )}
                  <span>{composerMode === "play" ? "Queue my move" : "Save guidance · no video"}</span>
                </button>
              </div>
              <p className="game-help">
                {activeTurn?.status === "awaiting_review"
                  ? "Review or cancel the scene above before making another move."
                  : busy
                    ? "Keep adding or editing queued moves while this scene finishes."
                    : "A choice joins the queue. Edit or remove it during the 3-second window before it starts."}
              </p>
            </form>
              <GameActionQueue controls={moveQueue} busy={busy} story={story} onCheck={() => void perform(() => session.resumePending())}/>
              {lastTurn && ["failed", "uncertain", "awaiting_assistant"].includes(lastTurn.status) && <div className="game-attention-link" role="status"><span>{lastTurn.error || storyTurnLabel(lastTurn)}</span><div className="game-attention-actions">{lastTurn.status === "failed" && !lastTurn.plan && !lastTurn.run_id && <button className="primary" disabled={submitting || !!pendingTicket} onClick={() => takeAction(lastTurn, "retry")}><RefreshCw size={15}/> Retry AI response</button>}<button className="quiet" onClick={() => setPlayView("history")}>Review details</button></div></div>}
              {selectedVideo && (
                <div className="game-player-meta">
                  <span>
                    {selectedVideo.scene_video_url
                      ? "New scene"
                      : `${selectedVideo.duration?.toFixed(2)}s`}
                  </span>
                  {selectedVideo.width && (
                    <span>
                      {selectedVideo.width} × {selectedVideo.height}
                    </span>
                  )}
                  {selectedVideo.seed !== undefined && (
                    <span>Seed {selectedVideo.seed}</span>
                  )}
                  {selectedVideo.download_url && (
                    <a
                      href={
                        selectedVideo.scene_video_url ||
                        selectedVideo.download_url
                      }
                      download
                    >
                      Save scene
                    </a>
                  )}
                </div>
              )}
              {selectedVideo?.video_url && (
                <details className="game-clip-details">
                  <summary>Clip details &amp; original output</summary>
                  <UpscaleButton runId={selectedVideo.id}/>
                  <GameSoundtrack key={selectedVideo.id} runId={selectedVideo.id} project={config.project}/>
                  <p>
                    {selectedVideo.scene_video_url
                      ? "The player shows new footage. The original output can include preserved motion from the previous scene."
                      : "This is the full generated clip. A continuation can include a short preserved opening from the previous scene."}
                  </p>
                  {selectedVideo.duration && (
                    <p>
                      Original clip: {selectedVideo.duration.toFixed(2)}{" "}
                      seconds.
                    </p>
                  )}
                  <a
                    href={selectedVideo.video_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    View original generated clip
                  </a>
                  {selectedVideo.parent_run_id && (
                    <p>
                      Its parent ending is saved. Playing from a different take
                      creates its own story branch.
                    </p>
                  )}
                </details>
              )}
              {!!playlist.length && (
                <div className="game-film-actions">
                  <button
                    className="quiet"
                    onClick={() => {
                      setPlayingFilm(!playingFilm);
                      setFilmIndex(0);
                      setPreview(null);
                    }}
                  >
                    <Film size={14} />
                    {playingFilm
                      ? "Back to latest scene"
                      : `Play whole story · ${playlist.length} ${playlist.length === 1 ? "scene" : "scenes"}`}
                  </button>
                  <a
                    href={`/api/stories/${encodeURIComponent(story.id)}/video`}
                    download
                  >
                    Save whole film
                  </a>
                </div>
              )}
              {videos.length > 1 && (
                <div className="game-take-strip" aria-label="Saved takes">
                  {videos.map((video, index) => (
                    <button
                      key={video.id}
                      aria-pressed={
                        !playingFilm && selectedVideo?.id === video.id
                      }
                      onClick={() => {
                        setPlayingFilm(false);
                        setPreview(
                          video.id === currentVideo?.id ? null : video,
                        );
                      }}
                    >
                      <span>{video.title || `Take ${index + 1}`}</span>
                      <small>
                        {video.id === story.active_run_id
                          ? "Current ending"
                          : video.seed !== undefined
                            ? `Seed ${video.seed}`
                            : "Saved take"}
                      </small>
                    </button>
                  ))}
                </div>
              )}
              {preview && (
                <div className="game-preview-notice">
                  <p>
                    Watching an earlier take. Your current story stays where it
                    is.
                  </p>
                  <div>
                    <button className="quiet" onClick={() => setPreview(null)}>
                      Return to current scene
                    </button>
                    <button
                      disabled={busy || !preview.can_continue}
                      onClick={() =>
                        void perform(async () => {
                          await session.branch(preview.id);
                          setPreview(null);
                        })
                      }
                    >
                      <GitBranch size={14} /> Play from this take
                    </button>
                  </div>
                </div>
              )}
              {activeTurn && (
                <>
                <GameStages turn={activeTurn}/>
                {activeTurn.run_id && activeTurn.status === "rendering" && <LiveRenderProgress runId={activeTurn.run_id}/>}
                <div className="game-progress" aria-live="polite">
                  <div>
                    <strong>{storyTurnLabel(activeTurn)}</strong>
                    <p>
                      {activeTurn.stage ||
                        "Your scene is saved. You can stay here while it is prepared."}
                    </p>
                  </div>
                  {RUNNING_STORY_STATUSES.has(activeTurn.status) && (
                    <button
                      className="quiet"
                      disabled={submitting || !!pendingTicket}
                      onClick={() => takeAction(activeTurn, "cancel")}
                    >
                      Cancel turn
                    </button>
                  )}
                </div>
                </>
              )}
              {memory && (
                <details className="game-memory">
                  <summary>
                    What the story remembers
                    <ChevronDown size={15} />
                  </summary>
                  <p>{memory}</p>
                </details>
              )}
              <div className="game-stage-hint">
                <Sparkles size={16} />
                <p>
                  The assistant uses the story and the actual ending to plan the
                  next response. Review the action and dialogue whenever you
                  want.
                </p>
              </div>
            </div>
            <aside
              className="game-conversation"
              aria-label="Story conversation"
              hidden={playView !== "history"}
            >
              <div className="game-section-title">
                <div>
                  <span className="game-eyebrow">THE STORY SO FAR</span>
                  <h2>Action. Reaction.</h2>
                </div>
                <span className="game-count">
                  {story.turns.length}{" "}
                  {story.turns.length === 1 ? "turn" : "turns"}
                </span>
              </div>
              <div className="game-conversation-scroll" onScroll={e => { const el = e.currentTarget; conversationNearEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }}>
                {!story.turns.length && (
                  <div className="game-chat-opening">
                    <p>{story.premise}</p>
                    <strong>
                      You play {story.player_name}. What do you do?
                    </strong>
                  </div>
                )}
                {story.turns.map((turn) => (
                  <article className="game-turn" key={turn.id}>
                    <div className="game-player-message">
                      <span>{story.player_name || "You"}</span>
                      <p>{turn.message}</p>
                    </div>
                    <div className="game-story-message">
                      <span>
                        <Sparkles size={13} /> Story
                      </span>
                      {turn.plan?.action ? (
                        <>
                          <p>{turn.plan.action}</p>
                          {turn.plan.dialogue
                            ?.filter((line) => line.text)
                            .map((line, i) => (
                              <blockquote key={i}>
                                <strong>{line.speaker}</strong> “{line.text}”
                              </blockquote>
                            ))}
                        </>
                      ) : (
                        <p className="game-help">{storyTurnLabel(turn)}</p>
                      )}
                      {turn.status === "cancelled" && (
                        <p className="game-help">
                          {currentVideo
                            ? "Turn cancelled. Your last completed ending is kept."
                            : "Turn cancelled."}
                        </p>
                      )}
                      {turn.error && (turn.status === "failed" && turn.id !== lastTurn?.id && turn.parent_run_id !== story.active_run_id
                        ? <details className="game-help"><summary>Earlier failed attempt · the story has since continued</summary><p>{turn.error}</p></details>
                        : <p className="game-turn-error" role="alert">{turn.error}</p>)}
                      {turn.status === "awaiting_review" && Array.isArray(turn.created_assets) && turn.created_assets.length > 0 && <div className="game-appearance-review"><strong>Review the new appearances</strong><p>These images will condition the scene. Open a thumbnail to inspect it before continuing.</p><div>{(turn.created_assets as Asset[]).filter(asset => asset.media_type === "image").map(asset => <a key={asset.id} href={`/api/assets/${encodeURIComponent(asset.id)}/file`} target="_blank" rel="noreferrer"><img src={`/api/assets/${encodeURIComponent(asset.id)}/thumbnail`} alt={asset.name}/><span>{asset.name}</span></a>)}</div></div>}
                      <div className="game-turn-actions">
                        {["inspection_failed", "awaiting_acceptance"].includes(turn.status) && <>
                          <button disabled={submitting} onClick={() => takeAction(turn, "retry-inspection")}>Retry ending inspection</button>
                          <button disabled={submitting} onClick={() => takeAction(turn, "accept-intended")}>Use intended story</button>
                          <button disabled={submitting || !hasVisibleObservation(turn)} title={hasVisibleObservation(turn) ? "Keep the visible outcome and only the changes supported by the inspected image." : "Retry ending inspection first so there is a visible result to use."} onClick={() => takeAction(turn, "accept-visible")}>Use visible result</button>
                        </>}
                        {turn.status === "awaiting_assistant" && <button onClick={() => void perform(async () => { const requests = await api("/assistant/requests"); setLocalError(`Supervised assistant is waiting. ${Array.isArray(requests.requests) ? requests.requests.length : "Pending"} request(s) are available to the testing agent. This does not queue another video.`); })}>Check supervised requests</button>}
                        {turn.status === "awaiting_review" && (
                          <>
                            <button
                              className="primary"
                              disabled={submitting || !!pendingTicket}
                              onClick={() => takeAction(turn, "approve")}
                            >
                              <Play size={13} /> {Array.isArray(turn.created_assets) && turn.created_assets.length ? "Use these appearances" : "Render this scene"}
                            </button>
                            {turn.plan && (
                              <button
                                disabled={submitting}
                                onClick={() =>
                                  setEditing({ turn, action: "approve" })
                                }
                              >
                                <Pencil size={13} /> Edit response
                              </button>
                            )}
                            <button
                              className="quiet"
                              disabled={submitting || !!pendingTicket}
                              onClick={() => takeAction(turn, "cancel")}
                            >
                              Cancel
                            </button>
                          </>
                        )}
                        {turn.status === "succeeded" && (
                          <>
                            <button
                              disabled={
                                busy || turn.run_id !== story.active_run_id
                              }
                              onClick={() => takeAction(turn, "reroll")}
                            >
                              <Shuffle size={13} /> Another take
                            </button>
                            {turn.plan && (
                              <button
                                className="quiet"
                                disabled={
                                  busy || turn.run_id !== story.active_run_id
                                }
                                onClick={() =>
                                  setEditing({ turn, action: "reroll" })
                                }
                              >
                                <Pencil size={13} /> Edit response
                              </button>
                            )}
                            {(turn.video?.video_url ||
                              videos.find((clip) => clip.id === turn.run_id)
                                ?.video_url) && (
                              <button
                                className="quiet"
                                onClick={() => {
                                  setPlayingFilm(false);
                                  setPreview(
                                    turn.video ||
                                      videos.find(
                                        (clip) => clip.id === turn.run_id,
                                      ) ||
                                      null,
                                  );
                                }}
                              >
                                <Play size={13} /> Watch
                              </button>
                            )}
                          </>
                        )}
                        {["failed", "uncertain"].includes(turn.status) && (turn.status !== "failed" || turn.id === lastTurn?.id || turn.parent_run_id === story.active_run_id) && (
                          <>
                            <button
                              disabled={submitting || !!pendingTicket}
                              onClick={() => takeAction(turn, "retry")}
                            >
                              <RefreshCw size={13} /> {turn.run_id ? "Check saved render" : turn.plan ? "Resume saved turn" : "Retry AI response"}
                            </button>
                            <button
                              className="quiet"
                              disabled={submitting || !!pendingTicket}
                              onClick={() => takeAction(turn, "cancel")}
                            >
                              Cancel turn
                            </button>
                          </>
                        )}
                      </div>
                      <GameReceipt turn={turn}/>
                    </div>
                  </article>
                ))}
                <div ref={conversationEnd} />
              </div>
            </aside>
          <section className="game-move-panel" aria-label="Your next move" hidden={playView !== "play"}>
            {!!config.guides.length && <div className="game-guide-chips" aria-label="Active guidance">{config.guides.filter(g => g.enabled).map(g => <div key={g.id}><button className="quiet" onClick={() => { setComposerMode("guide"); setGuideScope(g.scope); setEditingGuideId(g.id); changeMessage(g.text); }}>{g.scope === "next" ? "Next response" : "From now on"}: {g.text}</button><button aria-label={`Remove guidance: ${g.text}`} className="icon-button" onClick={() => changeConfiguration({ ...config, guides: config.guides.filter(item => item.id !== g.id) })}><X size={16}/></button></div>)}</div>}
            <div className="game-move-heading">
              <div>
                <span className="game-eyebrow">
                  {choices.length ? "CHOOSE YOUR NEXT MOVE" : "TRY A MOVE"}
                </span>
                <h2>What do you do?</h2>
              </div>
              <button
                className="quiet"
                onClick={() => send("Surprise me")}
              >
                <Sparkles size={15} /> Surprise me
              </button>
            </div>
            <div className="game-choice-grid">
              {displayedChoices.map((choice, index) => (
                <button
                  key={`${index}:${choice.message}`}
                  onClick={() => send(choice.message)}
                >
                  <span className="game-choice-number">{index + 1}</span>
                  <strong>{choice.title}</strong>
                  <p>{choice.message}</p>
                  <ArrowRight size={17} />
                </button>
              ))}
            </div>

          </section>
          </section>
          {editing?.turn.plan && (
            <div
              className="game-plan-overlay"
              role="dialog"
              aria-modal="true"
              aria-label="Edit story response"
            >
              <GamePlanEditor
                key={`${editing.turn.id}:${editing.action}`}
                plan={editing.turn.plan}
                submitting={submitting}
                onCancel={() => setEditing(null)}
                onSave={(plan) =>
                  takeAction(editing.turn, editing.action, plan)
                }
                actionLabel={
                  editing.action === "approve"
                    ? "Save & render scene"
                    : "Render edited response"
                }
              />
            </div>
          )}

        </>
      )}

      {motionProject && <MotionLab project={motionProject} onClose={() => setMotionProject(null)}/>}
      {settingsOpen && !pendingCreation && <GameEditor key={`${story?.id || setupBase.id}:${story?.active_branch_id || "setup"}`} value={config} onChange={changeConfiguration}
        onSave={() => void perform(saveConfiguration)} onClose={() => setSettingsOpen(false)}
        dirty={editorDirty || !story} saving={submitting} busy={!!activeTurn}
        onUploadFiles={onUploadFiles} modelPicker={modelPicker} generators={session.generators}
        generatorsLoading={session.generatorsLoading} generatorsChecked={session.generatorsChecked} generatorErrors={session.generatorErrors} onRefreshGenerators={session.refreshGenerators}
        initialTab={editorTab}
        onStopApply={activeTurn ? () => void perform(async () => {
          await session.turnAction(activeTurn, "stop-and-apply", undefined, {
            ...prepareGameConfiguration(config), expected_configuration_revision: editorRevision.current,
          });
          setEditorDirty(false); try { localStorage.removeItem(configurationKey); } catch {}
        }) : undefined}/>}
    </main>
  );
}
