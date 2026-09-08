import { describe, expect, it } from "vitest";
import {
  CONTINUATION_DRAFT_STORAGE_KEY, MAX_CONTINUATION_DRAFTS,
  clearContinuationDraft, loadContinuationDraft, saveContinuationDraft,
  type ContinuationDraft, type ContinuationDraftStorage,
} from "./continuationDraft";

const projectId = "10000000-0000-0000-0000-000000000001";
const otherProject = "20000000-0000-0000-0000-000000000001";
const runId = "30000000-0000-0000-0000-000000000001";
const otherRun = "40000000-0000-0000-0000-000000000001";
const ending = "/api/assets/50000000-0000-0000-0000-000000000001/file";

class MemoryStorage implements ContinuationDraftStorage {
  values = new Map<string, string>();
  writes = 0;
  removals = 0;
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.writes++; this.values.set(key, value); }
  removeItem(key: string) { this.removals++; this.values.delete(key); }
}

function draft(changes: Partial<ContinuationDraft> = {}): ContinuationDraft {
  return {
    idea: "Nora keeps the closed box and takes one small step.", duration: 5, renderPreset: "inherit",
    suggestions: { suggestions: [
      { title: "Hold it closer", idea: "Nora draws the closed box closer to her body." },
      { title: "Hands settle", idea: "Mira lowers her empty hands to her sides." },
      { title: "One careful step", idea: "Nora takes one small step while holding the closed box." },
    ], ending_image_url: ending, model: "Qwen/Qwen3.5-27B-Q4_K_M" }, ...changes,
  };
}

describe("scoped continuation drafts", () => {
  it("restores exact editable text, length, preset and choices without a write", () => {
    const storage = new MemoryStorage();
    const value = draft({ idea: "  Nora pauses.\nThen she speaks: \"Thank you.\"  ", renderPreset: "quality" });
    expect(saveContinuationDraft(projectId, runId, value, { storage, now: 100 })).toBe(true);
    const writes = storage.writes;
    expect(loadContinuationDraft(projectId, runId, { storage })).toEqual(value);
    expect(storage.writes).toBe(writes);
    expect(storage.removals).toBe(0);
  });

  it("keeps duration variants and restores the most recently edited length", () => {
    const storage = new MemoryStorage();
    saveContinuationDraft(projectId, runId, draft(), { storage, now: 100 });
    saveContinuationDraft(projectId, runId, draft({ duration: 10, idea: "A longer action.", renderPreset: "draft" }), { storage, now: 200 });
    expect(loadContinuationDraft(projectId, runId, { storage })?.duration).toBe(10);
    expect(loadContinuationDraft(projectId, runId, { storage, duration: 5 })).toEqual(draft());
    expect(loadContinuationDraft(projectId, runId, { storage, duration: 7 })).toBeNull();
  });

  it("cannot leak a draft between projects or takes", () => {
    const storage = new MemoryStorage();
    saveContinuationDraft(projectId, runId, draft(), { storage });
    expect(loadContinuationDraft(otherProject, runId, { storage })).toBeNull();
    expect(loadContinuationDraft(projectId, otherRun, { storage })).toBeNull();
    saveContinuationDraft(otherProject, runId, draft({ idea: "Other project." }), { storage });
    saveContinuationDraft(projectId, otherRun, draft({ idea: "Other take." }), { storage });
    expect(loadContinuationDraft(projectId, runId, { storage })?.idea).toBe(draft().idea);
    expect(loadContinuationDraft(otherProject, runId, { storage })?.idea).toBe("Other project.");
    expect(loadContinuationDraft(projectId, otherRun, { storage })?.idea).toBe("Other take.");
  });

  it("normalizes UUID casing and returns detached editable objects", () => {
    const storage = new MemoryStorage(), id = "abcdef00-1234-abcd-9876-123456abcdef";
    const value = draft();
    saveContinuationDraft(id.toUpperCase(), runId, value, { storage });
    value.suggestions!.suggestions[0].idea = "Changed original input";
    const restored = loadContinuationDraft(id, runId, { storage })!;
    restored.suggestions!.suggestions[0].idea = "Changed loaded value";
    expect(loadContinuationDraft(id, runId, { storage })?.suggestions?.suggestions[0].idea).toBe(draft().suggestions!.suggestions[0].idea);
  });

  it("defaults a missing preset to inherited settings and keeps an empty draft", () => {
    const storage = new MemoryStorage();
    expect(saveContinuationDraft(projectId, runId, { idea: "", duration: 4 } as ContinuationDraft, { storage })).toBe(true);
    expect(loadContinuationDraft(projectId, runId, { storage })).toEqual({ idea: "", duration: 4, renderPreset: "inherit" });
  });

  it("keeps old drafts without a time-to-live cutoff", () => {
    const storage = new MemoryStorage();
    saveContinuationDraft(projectId, runId, draft(), { storage, now: 1 });
    expect(loadContinuationDraft(projectId, runId, { storage, now: 9_000_000_000_000 })).toEqual(draft());
  });

  it("handles a backward clock while keeping the actual last edit selected", () => {
    const storage = new MemoryStorage();
    saveContinuationDraft(projectId, runId, draft({ duration: 10 }), { storage, now: 1000 });
    saveContinuationDraft(projectId, runId, draft({ duration: 4, idea: "Most recent edit" }), { storage, now: 100 });
    expect(loadContinuationDraft(projectId, runId, { storage })?.idea).toBe("Most recent edit");
  });

  it("caps entries at twenty and evicts the oldest while retaining the newest", () => {
    const storage = new MemoryStorage();
    const id = (n: number) => `30000000-0000-0000-0000-${n.toString(16).padStart(12, "0")}`;
    for (let n = 0; n < 25; n++) saveContinuationDraft(projectId, id(n), draft({ idea: `Draft ${n}` }), { storage, now: n });
    expect(JSON.parse(storage.getItem(CONTINUATION_DRAFT_STORAGE_KEY)!).entries).toHaveLength(MAX_CONTINUATION_DRAFTS);
    expect(loadContinuationDraft(projectId, id(4), { storage })).toBeNull();
    expect(loadContinuationDraft(projectId, id(5), { storage })?.idea).toBe("Draft 5");
    expect(loadContinuationDraft(projectId, id(24), { storage })?.idea).toBe("Draft 24");
  });

  it("clears one length or one take without clearing other scopes or app storage", () => {
    const storage = new MemoryStorage(); storage.values.set("other-app", "keep");
    saveContinuationDraft(projectId, runId, draft(), { storage });
    saveContinuationDraft(projectId, runId, draft({ duration: 10 }), { storage });
    saveContinuationDraft(otherProject, runId, draft(), { storage });
    expect(clearContinuationDraft(projectId, runId, { storage, duration: 5 })).toBe(true);
    expect(loadContinuationDraft(projectId, runId, { storage, duration: 5 })).toBeNull();
    expect(loadContinuationDraft(projectId, runId, { storage })?.duration).toBe(10);
    expect(clearContinuationDraft(projectId, runId, { storage })).toBe(true);
    expect(loadContinuationDraft(otherProject, runId, { storage })).toEqual(draft());
    expect(storage.getItem("other-app")).toBe("keep");
    clearContinuationDraft(otherProject, runId, { storage });
    expect(storage.getItem(CONTINUATION_DRAFT_STORAGE_KEY)).toBeNull();
  });
});

describe("bounded persistence data", () => {
  it.each(["project-a", "../secret", "C:\\Users\\someone", "", null, 1])("rejects non-UUID scope %s", invalid => {
    const storage = new MemoryStorage();
    expect(saveContinuationDraft(invalid as string, runId, draft(), { storage })).toBe(false);
    expect(loadContinuationDraft(projectId, invalid as string, { storage })).toBeNull();
    expect(clearContinuationDraft(invalid as string, runId, { storage })).toBe(false);
    expect(storage.writes).toBe(0);
  });

  it.each([{ idea: "x".repeat(1001) }, { duration: 3 }, { duration: 6 }, { duration: 16 }, { duration: NaN },
    { renderPreset: "secret-mode" }])("rejects unsupported draft values %j", change => {
    const storage = new MemoryStorage();
    expect(saveContinuationDraft(projectId, runId, draft(change as Partial<ContinuationDraft>), { storage })).toBe(false);
    expect(storage.writes).toBe(0);
  });

  it("keeps only three bounded cards and safe display metadata", () => {
    const storage = new MemoryStorage();
    const value = { ...draft(), session_token: "DO_NOT_STORE_TOKEN", private_path: "C:\\PRIVATE\\file",
      suggestions: { ...draft().suggestions, completion_info: { api_key: "DO_NOT_STORE_KEY" },
        ending_asset: { filename: "C:\\PRIVATE\\image.png" }, suggestions: [...draft().suggestions!.suggestions, { title: "Fourth", idea: "Not retained" }] } };
    saveContinuationDraft(projectId, runId, value, { storage });
    const raw = storage.getItem(CONTINUATION_DRAFT_STORAGE_KEY)!;
    expect(raw).not.toContain("DO_NOT_STORE"); expect(raw).not.toContain("PRIVATE");
    expect(loadContinuationDraft(projectId, runId, { storage })?.suggestions?.suggestions).toHaveLength(3);
  });

  it.each(["https://example.com/end.png", "//evil.test/image", "data:image/png;base64,AA==", "file:///C:/private.png",
    "/api/assets/../secret/file", `${ending}?token=private`, `${ending}#secret`, "/api/video/runs/private/video"])("drops unsafe ending URL %s", url => {
    const storage = new MemoryStorage(), value = draft(); value.suggestions!.ending_image_url = url;
    saveContinuationDraft(projectId, runId, value, { storage });
    expect(loadContinuationDraft(projectId, runId, { storage })?.suggestions?.ending_image_url).toBeUndefined();
  });

  it.each(["C:\\Users\\someone\\model.gguf", "C:/Users/someone/model.gguf", "/home/private/model.gguf",
    "https://model.test?token=secret", "../model.gguf", "x".repeat(201)])("drops paths and overlong model display IDs %s", model => {
    const storage = new MemoryStorage(), value = draft(); value.suggestions!.model = model;
    saveContinuationDraft(projectId, runId, value, { storage });
    expect(loadContinuationDraft(projectId, runId, { storage })?.suggestions?.model).toBeUndefined();
  });

  it("discards malformed/duplicate/overlong cards without losing the edited idea", () => {
    const storage = new MemoryStorage(), value = draft();
    value.suggestions!.suggestions = [
      { title: "x".repeat(49), idea: "An otherwise valid idea." },
      { title: "Good title", idea: "x".repeat(321) },
      { title: "Valid title", idea: "Nora keeps holding the box." },
    ];
    saveContinuationDraft(projectId, runId, value, { storage });
    const restored = loadContinuationDraft(projectId, runId, { storage });
    expect(restored?.idea).toBe(value.idea);
    expect(restored?.suggestions?.suggestions).toEqual([value.suggestions!.suggestions[2]]);
    value.suggestions!.suggestions = [draft().suggestions!.suggestions[0], draft().suggestions!.suggestions[0]];
    saveContinuationDraft(projectId, runId, value, { storage });
    expect(loadContinuationDraft(projectId, runId, { storage })?.suggestions?.suggestions).toHaveLength(1);
  });
});

describe("storage recovery", () => {
  it("treats corrupt JSON as unavailable until an explicit save repairs its own key", () => {
    const storage = new MemoryStorage(); storage.values.set(CONTINUATION_DRAFT_STORAGE_KEY, "{");
    expect(loadContinuationDraft(projectId, runId, { storage })).toBeNull();
    expect(storage.writes).toBe(0);
    expect(saveContinuationDraft(projectId, runId, draft(), { storage })).toBe(true);
    expect(loadContinuationDraft(projectId, runId, { storage })).toEqual(draft());
  });

  it("does not overwrite a newer format or oversized unknown cache", () => {
    for (const raw of [JSON.stringify({ version: 2, entries: [] }), "x".repeat(100001)]) {
      const storage = new MemoryStorage(); storage.values.set(CONTINUATION_DRAFT_STORAGE_KEY, raw);
      expect(loadContinuationDraft(projectId, runId, { storage })).toBeNull();
      expect(saveContinuationDraft(projectId, runId, draft(), { storage })).toBe(false);
      expect(clearContinuationDraft(projectId, runId, { storage })).toBe(false);
      expect(storage.getItem(CONTINUATION_DRAFT_STORAGE_KEY)).toBe(raw);
    }
  });

  it("filters malformed entries and keeps valid data in a versioned cache", () => {
    const storage = new MemoryStorage();
    storage.values.set(CONTINUATION_DRAFT_STORAGE_KEY, JSON.stringify({ version: 1, entries: [
      null, { projectId, runId, updatedAt: -1, draft: draft() },
      { projectId: "wrong", runId, updatedAt: 5, draft: draft() },
      { projectId, runId, updatedAt: 10, draft: draft() },
      { projectId, runId, updatedAt: 9, draft: draft({ idea: "Older duplicate" }) },
    ] }));
    expect(loadContinuationDraft(projectId, runId, { storage })).toEqual(draft());
  });

  it("handles restricted reads and writes without throwing", () => {
    const denied = () => { throw Object.assign(new Error("blocked"), { name: "SecurityError" }); };
    const unreadable = { getItem: denied, setItem: denied, removeItem: denied };
    expect(loadContinuationDraft(projectId, runId, { storage: unreadable })).toBeNull();
    expect(saveContinuationDraft(projectId, runId, draft(), { storage: unreadable })).toBe(false);
    expect(clearContinuationDraft(projectId, runId, { storage: unreadable })).toBe(false);
    const unwritable = { getItem: () => null, setItem: denied, removeItem: denied };
    expect(saveContinuationDraft(projectId, runId, draft(), { storage: unwritable })).toBe(false);
    expect(clearContinuationDraft(projectId, runId, { storage: unwritable })).toBe(false);
  });

  it("handles a blocked default localStorage getter", () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("blocked"); } });
    try {
      expect(loadContinuationDraft(projectId, runId)).toBeNull();
      expect(saveContinuationDraft(projectId, runId, draft())).toBe(false);
      expect(clearContinuationDraft(projectId, runId)).toBe(false);
    } finally {
      if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
      else delete (globalThis as any).localStorage;
    }
  });

  it("retries a quota failure by evicting older drafts while preserving the newest", () => {
    class QuotaStorage extends MemoryStorage {
      cap = 20;
      setItem(key: string, value: string) {
        if (JSON.parse(value).entries.length > this.cap) throw Object.assign(new Error("full"), { name: "QuotaExceededError" });
        super.setItem(key, value);
      }
    }
    const storage = new QuotaStorage();
    saveContinuationDraft(projectId, runId, draft(), { storage });
    saveContinuationDraft(projectId, otherRun, draft(), { storage });
    storage.cap = 1;
    expect(saveContinuationDraft(otherProject, runId, draft({ idea: "Newest draft" }), { storage })).toBe(true);
    expect(loadContinuationDraft(otherProject, runId, { storage })?.idea).toBe("Newest draft");
    expect(JSON.parse(storage.getItem(CONTINUATION_DRAFT_STORAGE_KEY)!).entries).toHaveLength(1);
  });

  it("leaves the old cache intact if even one new draft exceeds quota", () => {
    const memory = new MemoryStorage(); saveContinuationDraft(projectId, runId, draft(), { storage: memory });
    const before = memory.getItem(CONTINUATION_DRAFT_STORAGE_KEY);
    const storage = { getItem: memory.getItem.bind(memory), removeItem: memory.removeItem.bind(memory),
      setItem() { throw Object.assign(new Error("full"), { name: "QuotaExceededError" }); } };
    expect(saveContinuationDraft(projectId, otherRun, draft(), { storage })).toBe(false);
    expect(memory.getItem(CONTINUATION_DRAFT_STORAGE_KEY)).toBe(before);
  });
});
