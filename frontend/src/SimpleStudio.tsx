import React, { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  Copy,
  Download,
  FolderOpen,
  ImagePlus,
  LoaderCircle,
  MessageSquare,
  Plus,
  Settings2,
  Sparkles,
  Trash2,
  Undo2,
  Upload,
  UserRound,
  Video,
  X,
} from "lucide-react";
import { Asset, Project, Subject, retime, uid } from "./model";
import {
  getPeople,
  getAssetPerson,
  setAssetType,
  setAssetPerson,
  renamePerson,
  removeSimpleAsset,
  setSimpleMode,
  setKeyframe,
  setSimpleSceneCount,
  setPersonAction as setAction,
} from "./simple";
import "./SimpleStudio.css";
import SceneDirector from './SceneDirector';
import TemplateShelf from './TemplateShelf';
import PhotoTools, { ReferenceInsert } from './PhotoTools';
import IdeaBuilder from './IdeaBuilder';
import { TimelinePlanner, ContinuationPlanner } from './TimelinePlanner';
import { ensurePromptTags } from './tags';

export type SimpleStudioProps = {
  project: Project;
  update: (fn: (draft: Project) => void) => void;
  checkpointUpdate: (fn: (draft: Project) => void) => void;
  onRestore: (project:Project) => void;
  onReplacePhoto: (id:string,file:File) => Promise<void>;
  onAddFiles: (files: File[]) => Promise<void>;
  onGenerate: () => void;
  onBuild: () => void;
  busy: string;
  renderBusy?: boolean;
  progress: string;
  error: string;
  notice: string;
  result: any | null;
  currentPrompt: string;
  resultFresh: boolean;
  referenceMap: {asset_id:string;token:string;name:string;role:string}[];
  onCopy: () => void;
  onSave: () => void;
  onAdvanced: () => void;
  onProjects: () => void;
  onNew: () => void;
  onConnections: () => void;
  onFiles: () => void;
  onUndo: () => void;
  canUndo: boolean;
  connectionOnline: boolean;
  onSendToComfy: () => void;
  canReturn: boolean;
  onContinue: (next:Project)=>void;
  modelPicker?: React.ReactNode;
  comfyPanel?: React.ReactNode;
};

const PHOTO_TYPES = [
  ["other", "Choose a type…"],
  ["face", "Person · face / identity"],
  ["character", "Person · whole look"],
  ["wardrobe", "Clothes"],
  ["object", "Object"],
  ["background", "Place"],
  ["style", "Style"],
  ["palette", "Colors"],
  ["pose", "Pose / composition"],
];

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="simple-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export default function SimpleStudio(props: SimpleStudioProps) {
  const { project: p, update } = props;
  const inputRef = useRef<HTMLInputElement>(null);
  const storyRef = useRef<HTMLTextAreaElement>(null);
  const actionRefs = useRef<Record<string,HTMLTextAreaElement|null>>({});
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<Asset | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [showScenes, setShowScenes] = useState(
    p.shots.length > 1 || p.shots.some((s) => s.dialogue.length > 0),
  );
  useEffect(() => {
    setShowScenes(p.shots.length > 1 || p.shots.some((s) => s.dialogue.length > 0));
    setPreview(null);
  }, [p.id]);
  useEffect(()=>{if(p.shots.length>1)setShowScenes(true);},[p.shots.length]);
  const images = p.assets.filter((a) => a.media_type === "image");
  const activeImages = images.filter((a) => a.enabled);
  const people = getPeople(p);
  const videoImages = activeImages.filter((a) => a.role !== "context");
  const inspirationImages = activeImages.filter((a) => a.role === "context");
  const unavailable = !!props.busy;
  const editAsset = (id: string, values: Partial<Asset>) =>
    update((d) => {
      const a = d.assets.find((a) => a.id === id);
      if (a) Object.assign(a, values);
    });
  const editShot = (id: string, fn: (s: Project["shots"][number]) => void) =>
    update((d) => {
      const shot = d.shots.find((s) => s.id === id);
      if (shot) fn(shot);
    });
  const addFiles = async (files: File[]) => {
    setUploadError("");
    if (!files.length) return;
    try {
      await props.onAddFiles(files);
    } catch (e: any) {
      setUploadError(
        e.message || "Those photos could not be added. Please try again.",
      );
    }
  };

  const ownerOf = (a: Asset) => getAssetPerson(p, a.id);
  const typeChanged = (a: Asset, role: string) =>
    update((d) => setAssetType(d, a.id, role));
  const assignTo = (a: Asset, personId: string) =>
    update((d) => setAssetPerson(d, a.id, personId));
  const personAction = (person: Subject) =>
    p.simple?.person_actions?.[person.id] || "";
  const setPersonAction = (person: Subject, text: string) =>
    update((d) => setAction(d, person.id, text));
  const insertReference = (tag:string, shotId?:string) => {
    const input=shotId?actionRefs.current[shotId]:storyRef.current;
    const source=shotId?p.shots.find(s=>s.id===shotId)?.action||'':p.story.text;
    const start=input?.selectionStart ?? source.length,end=input?.selectionEnd ?? start;
    const added=(start>0&&!/\s/.test(source[start-1])?' ':'')+tag+' ';
    props.checkpointUpdate(d=>{ensurePromptTags(d);const value=source.slice(0,start)+added+source.slice(end);if(shotId){const s=d.shots.find(s=>s.id===shotId);if(s)s.action=value;}else d.story.text=value;});
    requestAnimationFrame(()=>{input?.focus();input?.setSelectionRange(start+added.length,start+added.length);});
  };

  return (
    <div className="simple-studio">
      <header className="simple-header">
        <div className="simple-brand">
          <span className="simple-brand-mark">
            <Video size={19} />
          </span>
          <div>
            <strong>H3 Prompt Studio</strong>
            <span>Simple mode</span>
          </div>
        </div>
        <nav aria-label="Project tools">
          {props.canUndo&&<button onClick={props.onUndo} disabled={unavailable}><Undo2 size={15}/> Undo last change</button>}
          <button onClick={props.onNew} disabled={unavailable}>
            <Plus size={15} /> New
          </button>
          <button onClick={props.onProjects} disabled={unavailable}>
            <FolderOpen size={15} /> Saved projects
          </button>
          <button onClick={props.onFiles}>
            <Download size={15} /> Outputs
          </button>
          <button onClick={() => document.querySelector('.video-workspace')?.scrollIntoView({behavior:'smooth',block:'start'})}>
            <Video size={15} /> Video
          </button>
          <button className="simple-quiet" onClick={props.onAdvanced}>
            <Settings2 size={15} /> Advanced
          </button>
        </nav>
      </header>

      <main className="simple-main">
        <div className="simple-intro">
          <div>
            <p className="simple-eyebrow">YOUR IMAGES. YOUR IDEA.</p>
            <h1>Make your scene.</h1>
            <p>
              Add photos, say who does what, and generate your video here.
            </p>
          </div>
          <button
            className={
              "simple-connection " + (props.connectionOnline ? "is-online" : "")
            }
            onClick={props.onConnections}
          >
            <span />
            {props.connectionOnline ? "Local AI connected" : "Connect local AI"}
            <ChevronDown size={14} />
          </button>
        </div>

        <div className="simple-project-line">
          <label htmlFor="simple-project-title">Project</label>
          <input
            id="simple-project-title"
            aria-label="Project name"
            value={p.title}
            onChange={(e) =>
              update((d) => {
                d.title = e.target.value;
              })
            }
            disabled={unavailable}
          />
          <span>Saved automatically</span>
        </div>

        <section
          className="simple-section"
          id="simple-photos"
          aria-labelledby="simple-photos-title"
        >
          <div className="simple-section-heading">
            <span className="simple-step">1</span>
            <div>
              <h2 id="simple-photos-title">Add your photos</h2>
              <p>
                Tell us what each photo is. Assign clothes and objects to the
                right person.
              </p>
            </div>
            <span className="simple-count">
              {inspirationImages.length
                ? `${videoImages.length} video reference${videoImages.length === 1 ? "" : "s"} · ${inspirationImages.length} inspiration`
                : `${videoImages.length} photo${videoImages.length === 1 ? "" : "s"} in use`}
            </span>
          </div>
          <div
            className={"simple-dropzone " + (dragging ? "is-dragging" : "")}
            onDragOver={(e) => {
              e.preventDefault();
              if (!unavailable) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (!unavailable) void addFiles(Array.from(e.dataTransfer.files));
            }}
          >
            <div>
              <ImagePlus size={25} />
              <span>
                <strong>Add people, clothes, objects or a place</strong>
                <small>Choose several photos at once, or drop them here.</small>
              </span>
            </div>
            <button
              className="simple-secondary"
              disabled={unavailable}
              onClick={() => inputRef.current?.click()}
            >
              <Upload size={16} /> Add photos
            </button>
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              multiple
              hidden
              aria-label="Upload reference photos"
              onChange={(e) => {
                void addFiles(Array.from(e.target.files || []));
                e.target.value = "";
              }}
            />
          </div>
          {uploadError && (
            <p className="simple-error" role="alert">
              {uploadError}
            </p>
          )}

          {p.mode === "ref2va" &&
            activeImages.some((a) =>
              ["first_frame", "last_frame"].includes(a.role),
            ) && (
              <div className="simple-mode-choice">
                <p>Choose how to use these photos.</p>
                <div>
                  <button
                    disabled={unavailable}
                    onClick={() => update((d) => setSimpleMode(d, "ref2va"))}
                  >
                    Use all as reference photos
                  </button>
                  {activeImages.some((a) => a.role === "first_frame") && (
                    <button
                      disabled={unavailable}
                      onClick={() => update((d) => setSimpleMode(d, "i2va"))}
                    >
                      Use first frame only
                    </button>
                  )}
                </div>
              </div>
            )}
          {!!images.length && (
            <div className="simple-photo-grid">
              {images.map((a, index) => {
                const owner = ownerOf(a);
                const isPerson = ["face", "character"].includes(
                  a.semantic_role,
                );
                const isClothes = a.semantic_role === "wardrobe";
                const isObject = a.semantic_role === "object";
                return (
                  <article
                    className={
                      "simple-photo " + (!a.enabled ? "is-excluded" : "")
                    }
                    key={a.id}
                  >
                    <button
                      className="simple-photo-preview"
                      aria-label={`View photo ${index + 1}: ${a.name}`}
                      onClick={() => setPreview(a)}
                    >
                      <img
                        src={`/api/assets/${a.id}/thumbnail`}
                        alt={a.name}
                        loading="lazy"
                      />
                      <span>Photo {index + 1}</span>
                    </button>
                    <fieldset
                      className="simple-photo-fields"
                      disabled={unavailable}
                    >
                      <Field label="What is this photo?">
                        <select
                          aria-label={`Photo ${index + 1} type`}
                          value={a.semantic_role}
                          onChange={(e) => typeChanged(a, e.target.value)}
                        >
                          {PHOTO_TYPES.map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </Field>
                      {isPerson && (
                        <>
                          <Field label="Which person?">
                            <select
                              aria-label={`Photo ${index + 1} person`}
                              value={owner?.id || ""}
                              onChange={(e) => assignTo(a, e.target.value)}
                            >
                              <option value="" disabled>
                                Choose a person…
                              </option>
                              {people.map((person) => (
                                <option key={person.id} value={person.id}>
                                  {person.name || "Unnamed person"}
                                </option>
                              ))}
                              <option value="new">+ A different person</option>
                            </select>
                          </Field>
                          <Field label="Person's name">
                            <input
                              aria-label={`Photo ${index + 1} person name`}
                              value={owner?.name || ""}
                              placeholder="e.g. Mira"
                              onChange={(e) => {
                                const name = e.target.value;
                                update((d) => {
                                  if (!getAssetPerson(d, a.id))
                                    setAssetPerson(d, a.id, "new");
                                  const person = getAssetPerson(d, a.id);
                                  if (person) renamePerson(d, person.id, name);
                                });
                              }}
                            />
                          </Field>
                        </>
                      )}
                      {(isClothes ||
                        isObject ||
                        a.semantic_role === "pose") && (
                        <Field
                          label={
                            isClothes
                              ? "Worn by"
                              : isObject
                                ? "Starts with"
                                : "Pose for"
                          }
                          hint={
                            isClothes && !people.length
                              ? "Add a person photo and name them first."
                              : undefined
                          }
                        >
                          <select
                            aria-label={`Photo ${index + 1} ${isClothes ? "worn by" : isObject ? "starts with" : "pose for"}`}
                            value={owner?.id || ""}
                            onChange={(e) => assignTo(a, e.target.value)}
                          >
                            <option value="">
                              {isClothes
                                ? "Choose a person…"
                                : isObject
                                  ? "In the scene · no owner"
                                  : "Whole scene"}
                            </option>
                            {people.map((person) => (
                              <option key={person.id} value={person.id}>
                                {person.name || "Unnamed person"}
                              </option>
                            ))}
                          </select>
                        </Field>
                      )}
                      {a.semantic_role === "background" && (
                        <p className="simple-photo-hint">
                          Sets the place. It does not add extra people.
                        </p>
                      )}
                      {["style", "palette"].includes(a.semantic_role) && (
                        <p className="simple-photo-hint">
                          Borrows the look and colors, not the people or
                          objects.
                        </p>
                      )}
                      {isPerson && (
                        <p className="simple-photo-hint">
                          {a.semantic_role === "face"
                            ? "Keeps this face. Assign an outfit separately below."
                            : "Keeps this person and their overall look."}
                        </p>
                      )}
                      <PhotoTools project={p} asset={a} index={index} update={update} onReplace={props.onReplacePhoto}/>
                      <details className="simple-photo-notes">
                        <summary>Photo notes</summary>
                        <Field label="Anything to keep or ignore?">
                          <textarea
                            rows={2}
                            value={a.description || ""}
                            placeholder="e.g. use the red dress only, ignore the mannequin"
                            onChange={(e) =>
                              editAsset(a.id, { description: e.target.value })
                            }
                          />
                        </Field>
                        {(a.approved_observation || a.observation) && (
                          <Field label="Description used in the prompt">
                            <textarea
                              rows={3}
                              value={a.approved_observation || ""}
                              placeholder="AI will describe this photo."
                              onChange={(e) =>
                                editAsset(a.id, {
                                  approved_observation: e.target.value,
                                })
                              }
                            />
                          </Field>
                        )}
                      </details>
                      {a.enabled && (
                        <span
                          className={
                            "simple-photo-use " +
                            (a.role === "context" ? "is-context" : "")
                          }
                        >
                          {a.role === "context"
                            ? "Prompt inspiration only"
                            : a.role === "first_frame"
                              ? "Starting image"
                              : a.role === "last_frame"
                                ? "Ending image"
                                : `Video reference ${videoImages.findIndex((item) => item.id === a.id) + 1}`}
                        </span>
                      )}
                      <div className="simple-photo-bottom">
                        <label className="simple-checkbox">
                          <input
                            type="checkbox"
                            checked={a.enabled}
                            onChange={(e) =>
                              update((d) => {
                                const item = d.assets.find(
                                  (item) => item.id === a.id,
                                );
                                if (item) item.enabled = e.target.checked;
                                setSimpleMode(d, d.mode);
                              })
                            }
                          />
                          <span>Use this photo</span>
                        </label>
                        <button
                          className="simple-icon"
                          title="Remove photo"
                          aria-label={`Remove photo ${index + 1}`}
                          onClick={() =>
                            update((d) => removeSimpleAsset(d, a.id))
                          }
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </fieldset>
                  </article>
                );
              })}
            </div>
          )}
          {!images.length && (
            <p className="simple-tip">
              For two people with different outfits: add two face photos and two
              clothes photos. Choose who wears each outfit.
            </p>
          )}
          {people
            .filter(
              (person) =>
                !person.asset_ids.some((id) =>
                  images.some(
                    (a) =>
                      a.id === id &&
                      ["face", "character"].includes(a.semantic_role),
                  ),
                ),
            )
            .map((person) => (
              <div className="simple-person-without-photo" key={person.id}>
                <UserRound size={17} />
                <Field label="Person without a photo">
                  <input
                    aria-label={`Name for ${person.name || "person"}`}
                    value={person.name}
                    placeholder="Person's name"
                    disabled={unavailable}
                    onChange={(e) =>
                      update((d) => renamePerson(d, person.id, e.target.value))
                    }
                  />
                </Field>
              </div>
            ))}
          <button
            className="simple-add-line"
            disabled={unavailable}
            onClick={() =>
              update((d) => {
                const n = getPeople(d).length + 1;
                d.subjects.push({
                  id: uid(),
                  name: `Person ${n}`,
                  asset_ids: [],
                  description: "",
                  simple_person: true,
                } as Subject);
              })
            }
          >
            <Plus size={13} /> Add a person without a photo
          </button>
        </section>

        <section
          className="simple-section"
          id="simple-idea"
          aria-labelledby="simple-idea-title"
        >
          <div className="simple-section-heading">
            <span className="simple-step">2</span>
            <div>
              <h2 id="simple-idea-title">Tell your story</h2>
              <p>
                Simple words are enough. You choose the people and dialogue; AI
                improves the scene.
              </p>
            </div>
          </div>
          <fieldset className="simple-story-fields" disabled={unavailable}>
            <IdeaBuilder project={p} update={props.checkpointUpdate}/>
            <Field label="What should happen?">
              <textarea
                ref={storyRef}
                className="simple-idea-input"
                rows={3}
                value={p.story.text}
                placeholder="e.g. Mira shows Nora a gift, gives it to her, and they smile together in the room."
                onChange={(e) =>
                  update((d) => {
                    d.story.text = e.target.value;
                  })
                }
              />
            </Field>
            {!!activeImages.length&&<ReferenceInsert project={p} onInsert={tag=>insertReference(tag)}/>}
            <div className="simple-options">
              <Field label="Video length">
                <select
                  value={p.duration}
                  onChange={(e) =>
                    update((d) => {
                      d.duration = Number(e.target.value);
                      d.shots = retime(d.shots, d.duration);
                    })
                  }
                >
                  {Array.from(new Set([5, 7, 10, 15, p.duration]))
                    .sort((a, b) => a - b)
                    .map((n) => (
                      <option key={n} value={n}>
                        {n} seconds
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Shape">
                <select
                  value={p.aspect_ratio}
                  onChange={(e) =>
                    update((d) => {
                      d.aspect_ratio = e.target.value;
                    })
                  }
                >
                  <option value="16:9">Wide · 16:9</option>
                  <option value="9:16">Vertical · 9:16</option>
                  <option value="1:1">Square · 1:1</option>
                  <option value="4:3">Classic · 4:3</option>
                  <option value="3:4">Portrait · 3:4</option>
                </select>
              </Field>
              <Field label="How should the video use your photos?">
                <select
                  value={p.mode}
                  onChange={(e) =>
                    update((d) => setSimpleMode(d, e.target.value))
                  }
                >
                  <option value="ref2va">Reference photos</option>
                  <option value="i2va">First frame only</option>
                  <option value="fl2va">First + last frame</option>
                  <option value="l2va">Last frame only</option>
                  <option value="t2va">Text only</option>
                </select>
              </Field>
            </div>
            {p.mode !== "ref2va" && (
              <div className="simple-keyframes">
                {["i2va", "fl2va"].includes(p.mode) && (
                  <Field label="Starting image">
                    <select
                      value={
                        activeImages.find((a) => a.role === "first_frame")
                          ?.id || ""
                      }
                      onChange={(e) =>
                        update((d) =>
                          setKeyframe(d, e.target.value, "first_frame"),
                        )
                      }
                    >
                      <option value="" disabled>
                        Choose the first frame…
                      </option>
                      {images.map((a, i) => (
                        <option key={a.id} value={a.id}>
                          Photo {i + 1} · {a.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                {["l2va", "fl2va"].includes(p.mode) && (
                  <Field label="Ending image">
                    <select
                      value={
                        activeImages.find((a) => a.role === "last_frame")?.id ||
                        ""
                      }
                      onChange={(e) =>
                        update((d) =>
                          setKeyframe(d, e.target.value, "last_frame"),
                        )
                      }
                    >
                      <option value="" disabled>
                        Choose the last frame…
                      </option>
                      {images.map((a, i) => (
                        <option key={a.id} value={a.id}>
                          Photo {i + 1} · {a.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                <p>
                  {p.mode === "i2va"
                    ? "The video starts from one image. No ending image is needed."
                    : p.mode === "l2va"
                      ? "The video ends at your chosen image. No starting image is needed."
                      : p.mode === "fl2va"
                        ? "Choose the image where the video starts and the image where it ends."
                        : "AI can use your photos for ideas; the video receives text only."}
                  {!!inspirationImages.length &&
                    " Other photos help AI describe the scene; they are not sent to H3 as separate image references."}
                </p>
              </div>
            )}
            {!!people.length && (
              <details className="simple-people-actions">
                <summary>
                  Who does what? <span>optional</span>
                </summary>
                <p className="simple-detail-help">
                  Your main idea can cover this. Add individual instructions
                  only if you want more control.
                </p>
                {people.map((person) => (
                  <div className="simple-person-action" key={person.id}>
                    <span className="simple-person-icon">
                      <UserRound size={17} />
                    </span>
                    <Field label={`${person.name || "This person"}'s action`}>
                      <input
                        value={personAction(person)}
                        placeholder={`e.g. ${person.name || "They"} holds the box, then hands it to the other person`}
                        onChange={(e) =>
                          setPersonAction(person, e.target.value)
                        }
                      />
                    </Field>
                  </div>
                ))}
              </details>
            )}

            <details
              className="simple-scenes"
              open={showScenes}
              onToggle={(e) => setShowScenes(e.currentTarget.open)}
            >
              <summary>
                Scenes, camera & spoken words <span>optional</span>
              </summary>
              <div className="simple-subheading">
                <div>
                  <p>
                    Each card can use a different shot size and camera move. Choose “Cut” to change the shot. Add exact words under their speaker.
                  </p>
                </div>
                <Field label="Number of scenes">
                  <select
                    value={p.shots.length}
                    onChange={(e) =>
                      update((d) =>
                        setSimpleSceneCount(d, Number(e.target.value)),
                      )
                    }
                  >
                    {[1, 2, 3, 4, 5, 6].map((n) => (
                      <option key={n} value={n}>
                        {n} scene{n === 1 ? "" : "s"}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {p.shots.map((shot, i) => (
                <article className="simple-scene" key={shot.id}>
                  <div className="simple-scene-heading">
                    <strong>
                      <span>{i + 1}</span>Scene {i + 1}
                    </strong>
                    <span>{Number(shot.duration.toFixed(2))} seconds</span>
                    {p.shots.length > 1 && (
                      <button
                        className="simple-icon"
                        aria-label={`Remove scene ${i + 1}`}
                        title="Remove this scene and its dialogue"
                        onClick={() =>
                          props.checkpointUpdate((d) => {
                            d.shots = retime(
                              d.shots.filter((s) => s.id !== shot.id),
                              d.duration,
                            );
                          })
                        }
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                  <Field label={`What happens in scene ${i + 1}?`}>
                    <textarea
                      ref={el=>{actionRefs.current[shot.id]=el;}}
                      rows={2}
                      value={shot.action}
                      placeholder="A short instruction, or leave blank for AI to plan this scene."
                      onChange={(e) =>
                        editShot(shot.id, (s) => {
                          s.action = e.target.value;
                        })
                      }
                    />
                  </Field>
                  {!!activeImages.length&&<ReferenceInsert project={p} label={`Scene ${i+1} insert photo reference`} onInsert={tag=>insertReference(tag,shot.id)}/>}
                  <SceneDirector project={p} shot={shot} index={i} update={props.checkpointUpdate}/>
                  <div className="simple-dialogue-list">
                    {shot.dialogue.map((line, lineIndex) => (
                      <div
                        className="simple-dialogue"
                        key={line.id || lineIndex}
                      >
                        <div className="simple-dialogue-top">
                          <MessageSquare size={15} />
                          <select
                            aria-label={`Scene ${i + 1} dialogue ${lineIndex + 1} speaker`}
                            value={line.speaker_id || ""}
                            onChange={(e) =>
                              editShot(shot.id, (s) => {
                                s.dialogue[lineIndex].speaker_id =
                                  e.target.value;
                                if (
                                  e.target.value &&
                                  !s.visible_subject_ids.includes(
                                    e.target.value,
                                  ) &&
                                  !s.offscreen_subject_ids.includes(
                                    e.target.value,
                                  )
                                )
                                  s.visible_subject_ids.push(e.target.value);
                              })
                            }
                          >
                            <option value="">Who says this?</option>
                            {people.map((person) => (
                              <option key={person.id} value={person.id}>
                                {person.name || "Unnamed person"}
                              </option>
                            ))}
                          </select>
                          <span>says</span>
                          <button
                            className="simple-icon"
                            aria-label={`Remove dialogue ${lineIndex + 1} from scene ${i + 1}`}
                            onClick={() =>
                              editShot(shot.id, (s) => {
                                s.dialogue.splice(lineIndex, 1);
                              })
                            }
                          >
                            <X size={14} />
                          </button>
                        </div>
                        <textarea
                          aria-label={`Scene ${i + 1} dialogue ${lineIndex + 1} exact words`}
                          rows={2}
                          value={line.text || ""}
                          placeholder="Type their exact words, in any language…"
                          onChange={(e) =>
                            editShot(shot.id, (s) => {
                              s.dialogue[lineIndex].text = e.target.value;
                              s.dialogue[lineIndex].locked = true;
                            })
                          }
                        />
                        <div className="simple-dialogue-options">
                          <Field label="Language">
                            <input
                              value={line.language || ""}
                              placeholder="e.g. Albanian"
                              onChange={(e) =>
                                editShot(shot.id, (s) => {
                                  s.dialogue[lineIndex].language =
                                    e.target.value;
                                })
                              }
                            />
                          </Field>
                          <Field label="How they say it">
                            <input
                              value={line.delivery || ""}
                              placeholder="e.g. softly, with a smile"
                              onChange={(e) =>
                                editShot(shot.id, (s) => {
                                  s.dialogue[lineIndex].delivery =
                                    e.target.value;
                                })
                              }
                            />
                          </Field>
                        </div>
                      </div>
                    ))}
                  </div>
                  <button
                    className="simple-add-line"
                    onClick={() =>
                      editShot(shot.id, (s) => {
                        const speaker = people[0]?.id || "";
                        s.dialogue.push({
                          id: uid(),
                          speaker_id: speaker,
                          text: "",
                          language: "",
                          delivery: "",
                          locked: true,
                        });
                        if (
                          speaker &&
                          !s.visible_subject_ids.includes(speaker) &&
                          !s.offscreen_subject_ids.includes(speaker)
                        )
                          s.visible_subject_ids.push(speaker);
                      })
                    }
                  >
                    <Plus size={14} /> Add spoken line
                  </button>
                </article>
              ))}
            </details>
            <details className="simple-extra">
              <summary>
                A little more direction <span>optional</span>
              </summary>
              <div className="simple-extra-grid">
                <Field label="Mood or style">
                  <input
                    value={p.style.vibe || ""}
                    placeholder="e.g. warm, playful, elegant"
                    onChange={(e) =>
                      update((d) => {
                        d.style.vibe = e.target.value;
                      })
                    }
                  />
                </Field>
                <Field label="Music or background sounds">
                  <input
                    value={p.soundscape || ""}
                    placeholder="e.g. quiet room, soft piano"
                    onChange={(e) =>
                      update((d) => {
                        d.soundscape = e.target.value;
                      })
                    }
                  />
                </Field>
                <Field label="Anything else for the AI?">
                  <textarea
                    rows={2}
                    value={p.assistant_instructions || ""}
                    placeholder="e.g. keep both women visible; only Nora holds the box at the end"
                    onChange={(e) =>
                      update((d) => {
                        d.assistant_instructions = e.target.value;
                      })
                    }
                  />
                </Field>
              </div>
            </details>
          </fieldset>
          <TimelinePlanner project={p} update={update} checkpointUpdate={props.checkpointUpdate}/>
          <ContinuationPlanner project={p} update={update} onContinue={props.onContinue} busy={unavailable}/>
          <TemplateShelf project={p} update={props.checkpointUpdate} onRestore={props.onRestore} currentPrompt={props.currentPrompt} resultFresh={props.resultFresh} busy={unavailable}/>
          {props.modelPicker}
          <div className="simple-generate-area">
            <button
              className="simple-generate"
              disabled={
                unavailable || props.renderBusy || (!p.story.text.trim() && !activeImages.length)
              }
              onClick={props.onGenerate}
            >
              {unavailable ? (
                <LoaderCircle size={19} className="simple-spinning" />
              ) : (
                <Sparkles size={19} />
              )}
              {unavailable ? "Working on your prompt…" : props.renderBusy ? "Video request in progress" : "Make my prompt"}
              {!unavailable && <ArrowRight size={18} />}
            </button>
            <button className="simple-build" disabled={unavailable||props.renderBusy||!p.story.text.trim()} onClick={props.onBuild}>Build without AI</button>
            <p>
              {unavailable
                ? props.progress || props.busy
                : props.renderBusy ? "You can edit your next idea while the current request finishes. Check its status in Video." : "AI looks at your photos and turns your idea into a ready-to-use H3 prompt."}
            </p>
          </div>
          {props.error && (
            <div className="simple-error" role="alert">
              {props.error}
            </div>
          )}
          {props.notice && (
            <div className="simple-notice" role="status">
              {props.notice}
            </div>
          )}
        </section>

        {props.comfyPanel}
        <section
          className="simple-section simple-result"
          id="simple-result"
          aria-labelledby="simple-result-title"
        >
          <div className="simple-section-heading">
            <span className="simple-step">3</span>
            <div>
              <h2 id="simple-result-title">Your prompt</h2>
              <p>
                {props.resultFresh
                  ? "Ready for Generate video. Your photos are connected automatically."
                  : props.currentPrompt
                    ? "Your current draft. Use Make my prompt to improve it with AI."
                    : "Your finished prompt will appear here."}
              </p>
            </div>
            {props.resultFresh && (
              <span className="simple-ready">
                <Check size={14} /> Ready
              </span>
            )}
          </div>
          {props.currentPrompt ? (
            <>
              <div className="simple-result-summary">
                <span>
                  <Video size={15} />
                  {p.duration}s · {p.aspect_ratio}
                </span>
                <span>
                  <ImagePlus size={15} />
                  {videoImages.length} video image
                  {videoImages.length === 1 ? "" : "s"}
                </span>
                {!!inspirationImages.length && (
                  <span>
                    {inspirationImages.length} inspiration photo
                    {inspirationImages.length === 1 ? "" : "s"}
                  </span>
                )}
                <span>
                  <MessageSquare size={15} />
                  {p.shots.reduce(
                    (n, s) =>
                      n + s.dialogue.filter((l) => l.text?.trim()).length,
                    0,
                  )}{" "}
                  spoken lines
                </span>
                {p.simple_generation?.method === 'manual' ? <span>Built from your choices · no AI</span> : props.result?.seconds != null && (
                  <span>Written in {Math.round(props.result.seconds)}s</span>
                )}
              </div>
              {props.resultFresh && (
                <div className="simple-plan-preview">
                  {p.shots.map((s, i) => (
                    <div key={s.id}>
                      <span>SCENE {i + 1}</span>
                      <p>
                        {s.action ||
                          "Scene direction is included in the prompt."}
                      </p>
                      {s.dialogue
                        .filter((l) => l.text?.trim())
                        .map((line, j) => (
                          <blockquote key={line.id || j}>
                            <b>
                              {people.find(
                                (person) => person.id === line.speaker_id,
                              )?.name || "Speaker"}
                              :
                            </b>{" "}
                            “{line.text}”
                          </blockquote>
                        ))}
                    </div>
                  ))}
                </div>
              )}
              {!!props.referenceMap.length&&<details className="result-reference-map">
                <summary>Your connected photo references</summary>
                <p>Generate video connects these photos in the correct order. Select a photo to review it.</p>
                <div>{props.referenceMap.map(r=>{
                  const asset=p.assets.find(a=>a.id===r.asset_id);
                  return <button key={r.asset_id} type="button" onClick={()=>asset?.media_type==='image'&&setPreview(asset)}>
                    {asset?.media_type==='image'&&<img src={`/api/assets/${r.asset_id}/thumbnail`} alt=""/>}
                    <span><b>{r.token}</b><small>{asset?.prompt_tag?'@'+asset.prompt_tag:r.name}</small><small>{r.name}</small></span>
                  </button>;
                })}</div>
              </details>}
              <div className="simple-result-buttons">
                <button
                  className="simple-copy"
                  onClick={props.onCopy}
                  disabled={!props.resultFresh}
                >
                  <Copy size={17} /> Copy prompt
                </button>
                <button onClick={props.onSave} disabled={!props.resultFresh}>
                  <Download size={16} /> Save prompt
                </button>
                {props.canReturn && (
                  <button
                    onClick={props.onSendToComfy}
                    disabled={unavailable || !props.resultFresh}
                  >
                    <ArrowRight size={16} /> Update connected prompt only
                  </button>
                )}
                {props.canUndo && (
                  <button
                    className="simple-quiet"
                    onClick={props.onUndo}
                    disabled={unavailable}
                  >
                    <Undo2 size={15} /> Undo AI changes
                  </button>
                )}
              </div>
              <details className="simple-full-prompt">
                <summary>See the full H3 prompt</summary>
                <textarea
                  aria-label="Finished H3 prompt"
                  readOnly
                  value={props.currentPrompt}
                  rows={14}
                />
                <p>
                  Generate video sends this prompt with its matching photos.
                  Inspiration photos help the AI plan; they are not extra video references.
                </p>
              </details>
            </>
          ) : (
            <div className="simple-result-empty">
              <Sparkles size={26} />
              <p>
                Add your idea above, then choose <strong>Make my prompt</strong>
                .
              </p>
            </div>
          )}
        </section>
        <footer className="simple-footer">
          <span>Made locally with your LM Studio model.</span>
          <button onClick={props.onAdvanced}>
            Need camera controls or the full editor? Open Advanced{" "}
            <ArrowRight size={13} />
          </button>
        </footer>
      </main>
      {preview && (
        <div
          className="simple-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`Photo preview: ${preview.name}`}
          onClick={() => setPreview(null)}
        >
          <button
            aria-label="Close photo preview"
            onClick={() => setPreview(null)}
          >
            <X size={21} />
          </button>
          <img
            src={`/api/assets/${preview.id}/thumbnail`}
            alt={preview.name}
            onClick={(e) => e.stopPropagation()}
          />
          <p>{preview.name}</p>
        </div>
      )}
    </div>
  );
}
