import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import LiveRenderProgress, { renderProgressEvent } from "./LiveRenderProgress";
const labels = { "7": "Rendering the scene" };
const event = (type: string, data: any) => JSON.stringify({ type, data });
describe("read-only native render progress", () => {
  it("accepts only the chosen prompt and finite current-node measurements", () => {
    expect(renderProgressEvent(event("progress", { prompt_id: "own", node: "7", value: 2, max: 8 }), "own", labels)).toEqual({ label: "Rendering the scene", value: 2, maximum: 8, percent: 25 });
    expect(renderProgressEvent(event("progress", { prompt_id: "other", node: "7", value: 2, max: 8 }), "own", labels)).toBeNull();
    expect(renderProgressEvent(event("progress", { prompt_id: "own", value: 2, max: 0 }), "own", labels)).toBeNull();
    expect(renderProgressEvent(new ArrayBuffer(10), "own", labels)).toBeNull();
  });
  it("reads native progress_state without exposing other jobs or treating completed nodes as active", () => {
    const nodes = { "6": { state: "finished", prompt_id: "own", value: 1, max: 1 }, "7": { state: "running", prompt_id: "own", value: 3, max: 4 } };
    expect(renderProgressEvent(event("progress_state", { prompt_id: "own", nodes }), "own", labels)?.percent).toBe(75);
    expect(renderProgressEvent(event("executing", { node: "7" }), "own", labels)).toBeNull();
  });
  it("explains that progress is per-operation and does not invent an image preview", () => {
    const html = renderToStaticMarkup(<LiveRenderProgress runId="owned-run" />);
    expect(html).toContain("Live render progress");
    expect(html).toContain("not the whole render");
    expect(html).toContain("Intermediate image previews are not shown");
    expect(html).not.toContain("<img");
  });
});
