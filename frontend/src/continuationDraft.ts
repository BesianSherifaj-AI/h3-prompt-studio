import type { ContinuationRenderPreset } from "./quickPreview";

export const CONTINUATION_DRAFT_STORAGE_KEY = "h3-prompt-studio:continuation-drafts:v1";
export const MAX_CONTINUATION_DRAFTS = 20;
const MAX_STORAGE_CHARS = 100_000;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const DURATIONS = new Set([4, 5, 7, 10, 15]);
const PRESETS = new Set<ContinuationRenderPreset>(["inherit", "draft", "quality"]);

export type ContinuationDraftSuggestions = {
  suggestions: { title: string; idea: string }[];
  ending_image_url?: string;
  model?: string;
};

export type ContinuationDraft = {
  idea: string;
  duration: number;
  renderPreset: ContinuationRenderPreset;
  suggestions?: ContinuationDraftSuggestions;
};

export type ContinuationDraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
type StorageOptions = { storage?: ContinuationDraftStorage | null; now?: number };
type ReadOptions = StorageOptions & { duration?: number };
type Entry = { projectId: string; runId: string; updatedAt: number; draft: ContinuationDraft };
type RecordValue = Record<string, unknown>;

function object(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function scopedIds(projectId: string, runId: string) {
  return typeof projectId === "string" && typeof runId === "string" && UUID.test(projectId) && UUID.test(runId)
    ? { projectId: projectId.toLowerCase(), runId: runId.toLowerCase() }
    : null;
}

function storageFor(options: StorageOptions): ContinuationDraftStorage | null {
  try {
    return options.storage === undefined ? globalThis.localStorage ?? null : options.storage;
  } catch {
    return null;
  }
}

function cleanSuggestions(value: unknown): ContinuationDraftSuggestions | undefined {
  if (!object(value) || !Array.isArray(value.suggestions)) return undefined;
  const seen = new Set<string>();
  const suggestions: ContinuationDraftSuggestions["suggestions"] = [];
  for (const item of value.suggestions.slice(0, 3)) {
    if (!object(item) || typeof item.title !== "string" || !item.title.trim() || item.title.length > 48 ||
        typeof item.idea !== "string" || !item.idea.trim() || item.idea.length > 320 || seen.has(item.idea.trim())) continue;
    seen.add(item.idea.trim());
    suggestions.push({ title: item.title, idea: item.idea });
  }
  const result: ContinuationDraftSuggestions = { suggestions };
  if (typeof value.ending_image_url === "string" &&
      /^\/api\/assets\/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\/file$/i.test(value.ending_image_url)) {
    result.ending_image_url = value.ending_image_url;
  }
  // Store a display identifier only, never an absolute model path, URL, query,
  // credentials, full completion response or arbitrary response metadata.
  if (typeof value.model === "string" &&
      /^[\p{L}\p{N}][\p{L}\p{N}._@()+\- /]{0,199}$/u.test(value.model) && !value.model.includes("..")) {
    result.model = value.model;
  }
  return result;
}

function cleanDraft(value: unknown): ContinuationDraft | null {
  if (!object(value) || typeof value.idea !== "string" || value.idea.length > 1000 ||
      typeof value.duration !== "number" || !DURATIONS.has(value.duration)) return null;
  const renderPreset = value.renderPreset === undefined ? "inherit" : value.renderPreset;
  if (typeof renderPreset !== "string" || !PRESETS.has(renderPreset as ContinuationRenderPreset)) return null;
  const result: ContinuationDraft = { idea: value.idea, duration: value.duration, renderPreset: renderPreset as ContinuationRenderPreset };
  const suggestions = cleanSuggestions(value.suggestions);
  if (suggestions) result.suggestions = suggestions;
  return result;
}

function entryKey(entry: Entry) {
  return `${entry.projectId}:${entry.runId}:${entry.draft.duration}`;
}

function readEntries(storage: ContinuationDraftStorage): { entries: Entry[]; writable: boolean } | null {
  let raw: string | null;
  try { raw = storage.getItem(CONTINUATION_DRAFT_STORAGE_KEY); } catch { return null; }
  if (!raw) return { entries: [], writable: true };
  if (raw.length > MAX_STORAGE_CHARS) return { entries: [], writable: false };
  let stored: unknown;
  try { stored = JSON.parse(raw); } catch { return { entries: [], writable: true }; }
  if (!object(stored) || stored.version !== 1 || !Array.isArray(stored.entries)) return { entries: [], writable: false };
  const entries: Entry[] = [];
  for (const item of stored.entries.slice(0, 200)) {
    if (!object(item) || typeof item.projectId !== "string" || typeof item.runId !== "string" ||
        typeof item.updatedAt !== "number" || !Number.isSafeInteger(item.updatedAt) || item.updatedAt < 0) continue;
    const ids = scopedIds(item.projectId, item.runId), draft = cleanDraft(item.draft);
    if (ids && draft) entries.push({ ...ids, updatedAt: item.updatedAt, draft });
  }
  const seen = new Set<string>();
  return { writable: true, entries: entries.sort((a, b) => b.updatedAt - a.updatedAt)
    .filter(entry => { const key = entryKey(entry); if (seen.has(key)) return false; seen.add(key); return true; })
    .slice(0, MAX_CONTINUATION_DRAFTS) };
}

/** Read only. Restores the last-used length, or one explicit length variant. */
export function loadContinuationDraft(projectId: string, runId: string, options: ReadOptions = {}): ContinuationDraft | null {
  const ids = scopedIds(projectId, runId), storage = storageFor(options);
  if (!ids || !storage || (options.duration !== undefined && !DURATIONS.has(options.duration))) return null;
  const stored = readEntries(storage);
  return stored?.entries.find(entry => entry.projectId === ids.projectId && entry.runId === ids.runId &&
    (options.duration === undefined || entry.draft.duration === options.duration))?.draft ?? null;
}

function isQuotaError(error: unknown) {
  return object(error) && (error.name === "QuotaExceededError" || error.name === "NS_ERROR_DOM_QUOTA_REACHED" || error.code === 22 || error.code === 1014);
}

/** Save only bounded editable data. No expiry: a draft should survive next week. */
export function saveContinuationDraft(projectId: string, runId: string, value: ContinuationDraft, options: StorageOptions = {}): boolean {
  const ids = scopedIds(projectId, runId), draft = cleanDraft(value), storage = storageFor(options);
  const requestedAt = options.now ?? Date.now();
  if (!ids || !draft || !storage || !Number.isSafeInteger(requestedAt) || requestedAt < 0) return false;
  const stored = readEntries(storage);
  if (!stored?.writable) return false;
  // Preserve last-edited ordering even if the machine clock moves backward.
  const updatedAt = Math.max(requestedAt, Math.min(Number.MAX_SAFE_INTEGER, (stored.entries[0]?.updatedAt ?? -1) + 1));
  const next: Entry = { ...ids, draft, updatedAt };
  const entries = [next, ...stored.entries.filter(entry => entryKey(entry) !== entryKey(next))]
    .sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONTINUATION_DRAFTS);
  while (entries.length) {
    try {
      storage.setItem(CONTINUATION_DRAFT_STORAGE_KEY, JSON.stringify({ version: 1, entries }));
      return true;
    } catch (error) {
      if (!isQuotaError(error) || entries.length === 1) return false;
      // setItem is atomic: failed quota attempts leave the prior store intact.
      // Evict only old entries; never sacrifice the newly edited draft.
      let oldestOther = entries.length - 1;
      while (oldestOther >= 0 && entries[oldestOther] === next) oldestOther -= 1;
      if (oldestOther < 0) return false;
      entries.splice(oldestOther, 1);
    }
  }
  return false;
}

/** Clear one take's drafts, or only its requested length; other scopes remain. */
export function clearContinuationDraft(projectId: string, runId: string, options: ReadOptions = {}): boolean {
  const ids = scopedIds(projectId, runId), storage = storageFor(options);
  if (!ids || !storage || (options.duration !== undefined && !DURATIONS.has(options.duration))) return false;
  const stored = readEntries(storage);
  if (!stored?.writable) return false;
  const entries = stored.entries.filter(entry => entry.projectId !== ids.projectId || entry.runId !== ids.runId ||
    (options.duration !== undefined && entry.draft.duration !== options.duration));
  try {
    if (entries.length) storage.setItem(CONTINUATION_DRAFT_STORAGE_KEY, JSON.stringify({ version: 1, entries }));
    else storage.removeItem(CONTINUATION_DRAFT_STORAGE_KEY);
    return true;
  } catch { return false; }
}
