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
import {
  DEFAULT_STORY_SETTINGS,
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
      asset.enabled !== false &&
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
      (asset) => asset.enabled !== false && asset.role !== "context",
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
  const [premise, setPremise] = useState(project.story?.text || ""),
    [player, setPlayer] = useState(project.subjects?.[0]?.name || "");
  const [playerSelection, setPlayerSelection] = useState(
    project.subjects?.[0]?.name || "custom",
  );
  const [settings, setSettings] = useState<StorySettings>({
    ...DEFAULT_STORY_SETTINGS,
  });
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
    : videos.find((clip) => clip.id === preview?.id) || currentVideo;
  const choices = storyChoices(story),
    displayedChoices = choices.length ? choices : STARTER_MOVES;
  const setupProject =
    pendingCreation?.body.project ??
    gameProjectWithUploads(
      project,
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
    if (session.selectedId || story) return;
    setPremise(project.story?.text || "");
    setPlayer(project.subjects?.[0]?.name || "");
    setPlayerSelection(project.subjects?.[0]?.name || "custom");
    setSetupAssets([]);
    setReferenceEdits({});
    setReferenceReplacements({});
  }, [project.id]);

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
    conversationEnd.current?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  }, [story?.turns.length, lastTurn?.status]);
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
        const gameProject = gameProjectWithUploads(
          { ...project, story: { ...project.story, text: premise.trim() } },
          setupAssets,
          referenceEdits,
          referenceReplacements,
        );
        const created = await session.create(gameProject, {
          premise: gameProject.story.text,
          player_name: player.trim(),
          settings,
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
  const send = (text: string) => {
    if (busy || !text.trim()) return;
    void perform(async () => {
      await session.sendTurn(text.trim(), story?.settings.duration || 5);
      changeMessage("");
      setPreview(null);
      setPlayingFilm(false);
    });
  };
  const takeAction = (
    turn: StoryTurn,
    action: "approve" | "retry" | "cancel" | "reroll",
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
  const saveSettings = () =>
    void perform(async () => {
      if (story)
        await session.patch({ settings, player_name: player, premise });
      setSettingsOpen(false);
    });
  const resetGame = () => {
    void session.selectStory("");
    setPreview(null);
    setEditing(null);
    setPlayingFilm(false);
    setSetupAssets([]);
    setReferenceEdits({});
    setReferenceReplacements({});
    setSourceRunId(undefined);
    setPremise(project.story?.text || "");
    setPlayer(project.subjects?.[0]?.name || "");
    setPlayerSelection(project.subjects?.[0]?.name || "custom");
    setSettings({ ...DEFAULT_STORY_SETTINGS });
  };

  return (
    <main className="game-studio" aria-label="Story game">
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
            <span>Settings</span>
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
                  placeholder="A quiet café. A handwritten note. Someone across the table knows more than they are saying…"
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
                <span>0.3 MP</span>
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
          <section className="game-play-layout">
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
                  {activeTurn && activeTurn.status !== "awaiting_review" ? (
                    <LoaderCircle className="game-spin" size={13} />
                  ) : (
                    <Check size={13} />
                  )}
                  {playerStatus}
                </span>
              </div>
              <div className="game-player">
                {selectedVideo?.video_url ? (
                  <video
                    key={`${playingFilm ? "film" : "take"}:${selectedVideo.id}`}
                    controls
                    playsInline
                    preload="metadata"
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
              <div className="game-conversation-scroll">
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
                      {turn.error && (
                        <p className="game-turn-error" role="alert">
                          {turn.error}
                        </p>
                      )}
                      <div className="game-turn-actions">
                        {turn.status === "awaiting_review" && (
                          <>
                            <button
                              className="primary"
                              disabled={submitting || !!pendingTicket}
                              onClick={() => takeAction(turn, "approve")}
                            >
                              <Play size={13} /> Render this scene
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
                        {["failed", "uncertain"].includes(turn.status) && (
                          <>
                            <button
                              disabled={submitting || !!pendingTicket}
                              onClick={() => takeAction(turn, "retry")}
                            >
                              <RefreshCw size={13} /> Resume safely
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
                    </div>
                  </article>
                ))}
                <div ref={conversationEnd} />
              </div>
            </aside>
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
          <section className="game-move-panel" aria-label="Your next move">
            <div className="game-move-heading">
              <div>
                <span className="game-eyebrow">
                  {choices.length ? "CHOOSE YOUR NEXT MOVE" : "TRY A MOVE"}
                </span>
                <h2>What do you do?</h2>
              </div>
              <button
                className="quiet"
                disabled={busy}
                onClick={() => send("Surprise me")}
              >
                <Sparkles size={15} /> Surprise me
              </button>
            </div>
            <div className="game-choice-grid">
              {displayedChoices.map((choice, index) => (
                <button
                  key={`${index}:${choice.message}`}
                  disabled={busy}
                  onClick={() => send(choice.message)}
                >
                  <span className="game-choice-number">{index + 1}</span>
                  <strong>{choice.title}</strong>
                  <p>{choice.message}</p>
                  <ArrowRight size={17} />
                </button>
              ))}
            </div>
            <form
              className="game-composer"
              onSubmit={(event) => {
                event.preventDefault();
                send(message);
              }}
            >
              <label className="game-composer-label" htmlFor="game-next-move">
                Or write your own move
              </label>
              <div>
                <textarea
                  id="game-next-move"
                  ref={composer}
                  value={message}
                  maxLength={5000}
                  rows={2}
                  placeholder={`I ${story.player_name ? "look at the note and ask what it means…" : "step forward and…"}`}
                  onChange={(event) => changeMessage(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      (event.ctrlKey || event.metaKey)
                    ) {
                      event.preventDefault();
                      send(message);
                    }
                  }}
                />
                <button className="primary" disabled={busy || !message.trim()}>
                  {submitting ? (
                    <LoaderCircle size={18} className="game-spin" />
                  ) : (
                    <Send size={18} />
                  )}
                  <span>Make my move</span>
                </button>
              </div>
              <p className="game-help">
                {activeTurn?.status === "awaiting_review"
                  ? "Review or cancel the scene above before making another move."
                  : busy
                    ? "Your next move can be drafted while this turn finishes."
                    : "A choice sends your action. Turn on review in Settings to approve each response before rendering."}
              </p>
            </form>
          </section>
        </>
      )}

      {settingsOpen && !pendingCreation && (
        <div
          className="game-settings-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSettingsOpen(false);
          }}
        >
          <aside
            className="game-settings"
            role="dialog"
            aria-modal="true"
            aria-labelledby="game-settings-title"
          >
            <header>
              <div>
                <span className="game-eyebrow">MAKE IT YOURS</span>
                <h2 id="game-settings-title">Game settings</h2>
              </div>
              <button
                ref={settingsClose}
                className="icon-button"
                aria-label="Close game settings"
                onClick={() => setSettingsOpen(false)}
              >
                <X size={20} />
              </button>
            </header>
            <section>
              <h3>Your story</h3>
              <label>
                You play
                <input
                  value={player}
                  maxLength={100}
                  onChange={(event) => setPlayer(event.target.value)}
                />
              </label>
              <label>
                Story style
                <input
                  value={settings.style || ""}
                  maxLength={160}
                  onChange={(event) =>
                    setSettings((old) => ({
                      ...old,
                      style: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="game-checkbox">
                <input
                  type="checkbox"
                  checked={settings.review_before_render}
                  onChange={(event) =>
                    setSettings((old) => ({
                      ...old,
                      review_before_render: event.target.checked,
                    }))
                  }
                />
                <span>
                  <strong>Review before rendering</strong>
                  <small>Approve or edit each planned scene first.</small>
                </span>
              </label>
            </section>
            <section>
              <h3>Video</h3>
              <div className="game-field-row">
                <label>
                  New scene length
                  <select
                    value={settings.duration}
                    onChange={(event) =>
                      setSettings((old) => ({
                        ...old,
                        duration: Number(event.target.value),
                      }))
                    }
                  >
                    {[4, 5, 7, 10, 13].map((n) => (
                      <option key={n} value={n}>
                        {n} seconds
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Preview quality
                  <select
                    value={settings.steps}
                    onChange={(event) =>
                      setSettings((old) => ({
                        ...old,
                        steps: Number(event.target.value),
                        resolution: "0.3",
                      }))
                    }
                  >
                    <option value={8}>Quality · 0.3 MP / 8 steps</option>
                    <option value={4}>Quick draft · 0.3 MP / 4 steps</option>
                  </select>
                </label>
              </div>
              <p className="game-help">
                This is new action after the previous ending. Motion context is
                added automatically. Continuations keep their source dimensions.
              </p>
            </section>
            <section>
              <h3>Prompt assistant</h3>
              {modelPicker || (
                <p className="game-help">
                  Use the Studio model selector to choose your local assistant.
                </p>
              )}
              <p className="game-help">
                Opening these settings does not start a model or a video.
              </p>
            </section>
            <section>
              <h3>New scene images</h3>
              <label>
                Image generator
                <select
                  value={settings.image_model || ""}
                  onChange={(event) =>
                    setSettings((old) => ({
                      ...old,
                      image_model: event.target.value || undefined,
                    }))
                  }
                >
                  <option value="">
                    Use the configured default
                    {session.defaultGenerator
                      ? ` · ${session.defaultGenerator}`
                      : ""}
                  </option>
                  {session.generators.map((model) => (
                    <option
                      key={model.id}
                      value={model.id}
                      disabled={
                        model.available === false || model.compatible === false
                      }
                    >
                      {model.name}
                      {model.available === false
                        ? " · unavailable"
                        : model.compatible === false
                          ? " · incompatible"
                          : ""}
                    </option>
                  ))}
                </select>
              </label>
              <p className="game-help">
                The story can request an image when a new scene needs one.
                Compatible installed generators appear here.
              </p>
              {!session.generators.length && (
                <p className="game-help">
                  No image generator is listed yet. Existing reference photos
                  remain available.
                </p>
              )}
            </section>
            <footer>
              <button className="quiet" onClick={() => setSettingsOpen(false)}>
                Close
              </button>
              <button
                className="primary"
                disabled={busy || !player.trim()}
                onClick={saveSettings}
              >
                <Check size={16} /> Save settings
              </button>
            </footer>
          </aside>
        </div>
      )}
    </main>
  );
}
