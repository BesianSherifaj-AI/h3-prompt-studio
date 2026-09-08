import React, { useEffect, useState } from "react";
import { Bookmark, Copy, FolderOpen, LoaderCircle, Save } from "lucide-react";
import { api, apiPatch } from "./api";
import type { Project } from "./model";
import {
  applyApproach,
  approachPreview,
  CAMERA_APPROACHES,
  projectSummary,
  snapshotProject,
  WRITING_APPROACHES,
} from "./templates";
import type { ApproachId, LibraryKind, LibraryRecord } from "./templates";
import "./TemplateShelf.css";

export type TemplateShelfProps = {
  project: Project;
  update: (fn: (draft: Project) => void) => void;
  onRestore: (project: Project) => void;
  currentPrompt: string;
  resultFresh: boolean;
  busy: boolean;
};

export default function TemplateShelf(props: TemplateShelfProps) {
  const [open, setOpen] = useState(false);
  const [library, setLibrary] = useState<{
    templates: LibraryRecord[];
    versions: LibraryRecord[];
  }>({ templates: [], versions: [] });
  const [tab, setTab] = useState<LibraryKind>("templates");
  const [starter, setStarter] = useState<ApproachId>("continuous");
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<{
    kind: LibraryKind;
    record: LibraryRecord;
  } | null>(null);
  const [editName, setEditName] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [editRating, setEditRating] = useState("0");
  const disabled = props.busy || !!working;
  const writing =
    WRITING_APPROACHES.find(
      (item) => item.id === props.project.simple?.approach,
    ) || WRITING_APPROACHES[0];

  useEffect(() => {
    setName("");
    setNotes("");
    setNotice("");
    setError("");
    setSelected(null);
  }, [props.project.id]);

  async function task(label: string, work: () => Promise<void>) {
    setWorking(label);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e: any) {
      setError(e.message || "This could not be saved. Please try again.");
    } finally {
      setWorking("");
    }
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setWorking("Loading saved items");
    api("/library")
      .then((data) => {
        if (!cancelled)
          setLibrary({
            templates: data.templates || [],
            versions: data.versions || [],
          });
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setWorking("");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  async function save(kind: LibraryKind) {
    if (!name.trim()) {
      setError("Give this setup or version a name so you can find it later.");
      return;
    }
    if (
      kind === "versions" &&
      (!props.resultFresh || !props.currentPrompt.trim())
    ) {
      setError(
        "Make your prompt first, then save this version with its exact setup.",
      );
      return;
    }
    const project = snapshotProject(props.project);
    const saveName = name.trim();
    const prompt = props.currentPrompt;
    const saveNotes = notes;
    await task(
      kind === "templates" ? "Saving setup" : "Saving version",
      async () => {
        const record = await api(`/library/${kind}`, {
          name: saveName,
          project,
          ...(kind === "versions"
            ? { prompt, notes: saveNotes, rating: 0 }
            : {}),
        });
        setLibrary((old) => ({
          ...old,
          [kind]: [
            record,
            ...old[kind].filter((item) => item.id !== record.id),
          ],
        }));
        setTab(kind);
        setNotice(
          kind === "templates"
            ? "Setup saved with its photos and assignments. Open it as a copy whenever you want."
            : "Version saved with the exact prompt and photo assignments. Add your test result after watching the video.",
        );
      },
    );
  }

  async function preview(kind: LibraryKind, id: string) {
    await task("Opening saved item", async () => {
      const record: LibraryRecord = await api(`/library/${kind}/${id}`);
      setSelected({ kind, record });
      setEditName(record.name);
      setEditNotes(record.notes || "");
      setEditRating(String(record.rating || 0));
    });
  }

  async function saveFeedback() {
    if (!selected || selected.kind !== "versions") return;
    const id = selected.record.id;
    if (!editName.trim()) {
      setError("Give this version a name.");
      return;
    }
    await task("Saving test result", async () => {
      const record: LibraryRecord = await apiPatch(`/library/versions/${id}`, {
        name: editName.trim(),
        notes: editNotes,
        rating: Number(editRating),
      });
      setLibrary((old) => ({
        ...old,
        versions: old.versions.map((item) =>
          item.id === id ? { ...item, ...record } : item,
        ),
      }));
      setSelected((old) =>
        old?.record.id === id
          ? { ...old, record: { ...old.record, ...record } }
          : old,
      );
      setNotice(
        "Test result saved. Your original prompt and photos stay together.",
      );
    });
  }

  return (
    <details
      className="template-shelf"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>
        <Bookmark size={18} />
        <span>
          Templates &amp; saved versions
          <small>
            Camera starters, reusable setups, and notes on what worked
          </small>
        </span>
      </summary>
      <div className="template-shelf-body">
        <div className="template-starters">
          <h3>Start with a camera approach</h3>
          <div className="template-select-action">
            <label>
              <span>Camera starter</span>
              <select
                aria-label="Camera starter"
                value={starter}
                disabled={disabled}
                onChange={(e) => setStarter(e.target.value as ApproachId)}
              >
                {CAMERA_APPROACHES.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                props.update((draft) => applyApproach(draft, starter));
                setNotice(
                  "Camera starter applied. Edit each scene card to choose its action, framing and people. Undo is available at the top.",
                );
              }}
            >
              Apply camera starter
            </button>
          </div>
          <p className="template-preview">
            {approachPreview(props.project, starter)}
          </p>
          <label className="template-writing">
            <span>How should the assistant improve your idea?</span>
            <select
              aria-label="Writing approach"
              value={writing.id}
              disabled={disabled}
              onChange={(e) =>
                props.update((draft) => {
                  draft.simple ??= {};
                  draft.simple.approach = e.target.value;
                })
              }
            >
              {WRITING_APPROACHES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <small>{writing.description}</small>
          </label>
        </div>

        <div className="template-save-area">
          <h3>Keep this setup to use again</h3>
          <p>
            A setup keeps your photos, tags, people, clothes, shots and
            instructions. A version also keeps the generated prompt and your
            test notes.
          </p>
          <label>
            <span>Save as</span>
            <input
              aria-label="Setup or version name"
              value={name}
              disabled={disabled}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Two people · close views · try 1"
              maxLength={120}
            />
          </label>
          <label>
            <span>
              Test notes <small>optional, saved with a prompt version</small>
            </span>
            <textarea
              aria-label="New version test notes"
              value={notes}
              disabled={disabled}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. 8 steps; good faces, try a wider final shot next time"
              rows={2}
            />
          </label>
          <div className="template-buttons">
            <button
              type="button"
              disabled={disabled || !name.trim()}
              onClick={() => save("templates")}
            >
              <Bookmark size={16} />
              Save setup
            </button>
            <button
              type="button"
              disabled={
                disabled ||
                !name.trim() ||
                !props.resultFresh ||
                !props.currentPrompt.trim()
              }
              onClick={() => save("versions")}
            >
              <Save size={16} />
              Save prompt version
            </button>
          </div>
          {!props.resultFresh && (
            <small>
              Make your prompt to save a version. You can save your setup at any
              time.
            </small>
          )}
        </div>

        <section
          className="template-library"
          aria-label="Saved setups and prompt versions"
        >
          <div
            className="template-tabs"
            role="tablist"
            aria-label="Saved item type"
          >
            <button
              type="button"
              role="tab"
              aria-selected={tab === "templates"}
              onClick={() => {
                setTab("templates");
                setSelected(null);
              }}
            >
              Saved setups ({library.templates.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "versions"}
              onClick={() => {
                setTab("versions");
                setSelected(null);
              }}
            >
              Prompt versions ({library.versions.length})
            </button>
          </div>
          {library[tab].length === 0 ? (
            <p className="template-empty">
              {tab === "templates"
                ? "Your saved setups will appear here."
                : "Your saved prompts and test results will appear here."}
            </p>
          ) : (
            <div className="template-record-list">
              {library[tab].map((item) => (
                <button
                  className="template-record"
                  type="button"
                  key={item.id}
                  disabled={disabled}
                  aria-pressed={
                    selected?.record.id === item.id && selected.kind === tab
                  }
                  onClick={() => preview(tab, item.id)}
                >
                  <span>
                    <strong>{item.name}</strong>
                    <small>{projectSummary(item)}</small>
                    {item.notes && (
                      <small className="template-record-note">
                        {item.notes}
                      </small>
                    )}
                  </span>
                  <span className="template-record-meta">
                    {item.rating
                      ? (
                          {
                            5: "Works well",
                            3: "Mixed result",
                            1: "Try again",
                          } as Record<number, string>
                        )[Number(item.rating)] || `${item.rating}/5`
                      : "Preview"}
                  </span>
                </button>
              ))}
            </div>
          )}

          {selected && (
            <div className="template-saved-preview">
              <div className="template-preview-heading">
                <h3>{selected.record.name}</h3>
                <button type="button" onClick={() => setSelected(null)}>
                  Close preview
                </button>
              </div>
              <p>{projectSummary(selected.record)}</p>
              <p className="template-restore-note">
                Open this as a new project copy, including its photos,
                assignments and shot settings. Your current project stays saved.
              </p>
              <div className="template-buttons">
                <button
                  type="button"
                  disabled={disabled || !selected.record.project}
                  onClick={() => {
                    if (selected.record.project)
                      props.onRestore({
                        ...snapshotProject(selected.record.project),
                        title: selected.record.name,
                      });
                  }}
                >
                  <FolderOpen size={16} />
                  Open as a copy
                </button>
                {selected.record.prompt && (
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() =>
                      task("Copying saved prompt", async () => {
                        await navigator.clipboard.writeText(
                          selected.record.prompt!,
                        );
                        setNotice("Saved prompt copied.");
                      })
                    }
                  >
                    <Copy size={16} />
                    Copy saved prompt
                  </button>
                )}
              </div>
              {selected.record.project && (
                <details className="template-snapshot-details">
                  <summary>Included photos and people</summary>
                  <div className="template-snapshot-photos">
                    {selected.record.project.assets.map((asset) => (
                      <div key={asset.id}>
                        {asset.media_type === "image" && (
                          <img
                            src={`/api/assets/${encodeURIComponent(asset.id)}/thumbnail`}
                            alt={asset.name}
                            loading="lazy"
                          />
                        )}
                        <span>
                          {asset.name}
                          <small>
                            {asset.semantic_role} ·{" "}
                            {asset.enabled
                              ? asset.role.replaceAll("_", " ")
                              : "not in use"}
                          </small>
                        </span>
                      </div>
                    ))}
                  </div>
                  <p>
                    {selected.record.project.subjects
                      .map((person) => person.name)
                      .join(", ") || "No named people"}
                  </p>
                </details>
              )}
              {selected.record.prompt && (
                <details className="template-snapshot-details">
                  <summary>Exact saved prompt</summary>
                  <pre>{selected.record.prompt}</pre>
                </details>
              )}
              {selected.kind === "versions" && (
                <div className="template-feedback">
                  <h4>After watching the video</h4>
                  <label>
                    <span>Version name</span>
                    <input
                      aria-label="Saved version name"
                      value={editName}
                      disabled={disabled}
                      onChange={(e) => setEditName(e.target.value)}
                      maxLength={120}
                    />
                  </label>
                  <label>
                    <span>How did it work?</span>
                    <select
                      aria-label="Version test result"
                      value={editRating}
                      disabled={disabled}
                      onChange={(e) => setEditRating(e.target.value)}
                    >
                      <option value="0">Not tested yet</option>
                      <option value="5">Works well — use again</option>
                      <option value="3">Some parts worked</option>
                      <option value="1">Needs another try</option>
                    </select>
                  </label>
                  <label>
                    <span>What worked, and what should change?</span>
                    <textarea
                      aria-label="Saved version test notes"
                      value={editNotes}
                      disabled={disabled}
                      onChange={(e) => setEditNotes(e.target.value)}
                      rows={3}
                      placeholder="e.g. Dress stayed correct. Dialogue good. Cut to close-up too early."
                    />
                  </label>
                  <button
                    type="button"
                    disabled={disabled || !editName.trim()}
                    onClick={saveFeedback}
                  >
                    <Save size={16} />
                    Save test result
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
        {working && (
          <p className="template-status" role="status">
            <LoaderCircle size={16} className="spin" />
            {working}…
          </p>
        )}
        {error && (
          <p className="template-error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="template-notice" role="status">
            {notice}
          </p>
        )}
      </div>
    </details>
  );
}
