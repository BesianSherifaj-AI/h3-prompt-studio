import { describe, expect, it, vi } from "vitest";
import {
  mergeVideoRuns,
  readVideoSelection,
  writeVideoSelection,
  sendVideoRun,
} from "./useVideoRuns";
import type { VideoJob } from "./VideoWorkspace";

describe("video run selection and history", () => {
  it("persists the exact run ID before the render request leaves the browser", async () => {
    const events: string[] = [];
    const send = vi.fn(async (path: string, body: any) => {
      events.push("POST");
      expect(body.request_id).toBe("saved-request");
      return { id: body.request_id };
    });
    const result = await sendVideoRun(
      "/video/runs",
      { prompt: "A scene" },
      "saved-request",
      (id) => {
        expect(id).toBe("saved-request");
        events.push("persist link");
      },
      send,
    );
    expect(events).toEqual(["persist link", "POST"]);
    expect(result.id).toBe("saved-request");
  });
  it("leaves the known ID available for reconciliation when the response is lost", async () => {
    let saved = "";
    const send = vi.fn().mockRejectedValue(new TypeError("connection lost"));
    await expect(
      sendVideoRun(
        "/video/runs/old/reroll",
        {},
        "saved-request",
        (id) => {
          saved = id;
        },
        send,
      ),
    ).rejects.toThrow("connection lost");
    expect(saved).toBe("saved-request");
    expect(send).toHaveBeenCalledTimes(1);
  });
  it("restores a selection within its session story or project without mixing their scopes", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) || null,
      setItem: (key: string, value: string) => {
        values.set(key, value);
      },
    };
    writeVideoSelection("story:a", "take-2", storage);
    writeVideoSelection("project:a", "take-1", storage);
    expect(readVideoSelection("story:a", storage)).toBe("take-2");
    expect(readVideoSelection("project:a", storage)).toBe("take-1");
    expect(readVideoSelection("story:b", storage)).toBe("");
    writeVideoSelection("story:a", "take-3", storage);
    expect(readVideoSelection("story:a", storage)).toBe("take-3");
  });
  it("tolerates unavailable browser storage and rejects oversized stored IDs", () => {
    const denied = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readVideoSelection("story:a", denied)).toBe("");
    expect(() =>
      writeVideoSelection("story:a", "take-1", denied),
    ).not.toThrow();
    expect(
      readVideoSelection("story:a", { getItem: () => "x".repeat(201) }),
    ).toBe("");
  });
  it("replaces stale run status while preserving clips from earlier projects", () => {
    const old = {
      id: "old",
      project_id: "opening",
      status: "succeeded",
    } as VideoJob;
    const cached = {
      id: "new",
      project_id: "next",
      status: "queued",
    } as VideoJob;
    const fresh = { ...cached, status: "succeeded" } as VideoJob;
    const merged = mergeVideoRuns([fresh], [old, cached], [old]);
    expect(merged).toEqual([fresh, old]);
    expect(cached.status).toBe("queued");
  });
});
