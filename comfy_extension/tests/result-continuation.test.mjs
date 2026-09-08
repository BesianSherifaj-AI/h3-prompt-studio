import test from "node:test";
import assert from "node:assert/strict";
import { studioSaveContext, savedResultFile, continuationResultUrl, matchingHistoryResult, installStudioContinuationButton } from "../web/result-continuation.mjs";

function fixture() {
  const manifest = { project_id: "11111111-1111-1111-1111-111111111111", transfer_id: "22222222-2222-2222-2222-222222222222", seed_control_node_id: 24, mmh3: { save_node_id: "26", output_prefix: "mmh3/studio/film" } };
  const create = { id: 24, type: "MMH3Create", inputs: [], widgets: [{ name: "seed", value: 42 }, { name: "prompt", value: "PRIVATE PROMPT MUST STAY OUT OF URL" }] };
  const node = { id: 26, type: "MMH3Save", widgets: [{ name: "mmh3_continue", element: { hidden: false, style: {} } }], addDOMWidget(name, type, element) { this.widgets.push({ name, type, element }); } };
  const graph = { extra: { h3_prompt_studio: manifest }, getNodeById: id => String(id) === "26" ? node : String(id) === "24" ? create : null, setDirtyCanvas() {} };
  node.graph = graph; create.graph = graph;
  const output = { mmh3_saved: [{ file: "output::mmh3/studio/film_00001.mmh3", path: "C:/private/output/path" }] };
  const record = (order = 1, file = output.mmh3_saved[0].file) => ({ status: { completed: true, status_str: "success" }, prompt: [order, "prompt-id", { 26: { class_type: "MMH3Save", inputs: { filename_prefix: manifest.mmh3.output_prefix, target: "output" } } }, { extra_pnginfo: { workflow: { extra: { h3_prompt_studio: structuredClone(manifest) } } } }], outputs: { 26: { mmh3_saved: [{ file }] } } });
  return { node, create, graph, manifest, output, record };
}

test("only the declared Studio Save node accepts exact generated names and safe relative paths", () => {
  const { node, graph, output } = fixture(), context = studioSaveContext(node);
  assert.equal(savedResultFile(context, output), "mmh3/studio/film_00001.mmh3");
  for (const file of ["mmh3/studio/film.mmh3", "output::mmh3\\studio\\film_12345.mmh3"]) assert.ok(savedResultFile(context, { mmh3_saved: [{ file }] }));
  for (const file of ["../film.mmh3", "C:/film.mmh3", "input::mmh3/studio/film.mmh3", "mmh3/studio/filmOTHER.mmh3", "mmh3/studio/film_1.mmh3", "mmh3/studio/film.mmh3?x", "mmh3/studio/film/../other.mmh3"]) assert.equal(savedResultFile(context, { mmh3_saved: [{ file }] }), null);
  assert.equal(studioSaveContext({ ...node }), null);
  graph.extra.h3_prompt_studio.mmh3.save_node_id = 99;
  assert.equal(studioSaveContext(node), null);
});

test("continuation links contain only project, exact result and a safe next authoritative seed", () => {
  const { node, create, output } = fixture();
  const url = new URL(continuationResultUrl(node, output.mmh3_saved[0].file));
  assert.equal(url.origin, "http://127.0.0.1:8766");
  assert.deepEqual([...url.searchParams.keys()], ["project", "continue_mmh3", "continue_seed"]);
  assert.equal(url.searchParams.get("continue_seed"), "43");
  assert.equal(url.searchParams.get("continue_mmh3"), "mmh3/studio/film_00001.mmh3");
  assert.ok(!url.toString().includes("PRIVATE"));
  for (const seed of [-1, Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER + 1, "42"]) { create.widgets[0].value = seed; assert.equal(new URL(continuationResultUrl(node, output.mmh3_saved[0].file)).searchParams.has("continue_seed"), false); }
  create.widgets[0].value = 42; create.inputs.push({ name: "seed", link: 7 });
  assert.equal(new URL(continuationResultUrl(node, output.mmh3_saved[0].file)).searchParams.has("continue_seed"), false);
});

test("history recovery chooses latest successful exact workflow and ignores unrelated or failed results", () => {
  const { node, record } = fixture();
  const good = record(1), later = record(2, "output::mmh3/studio/film_00002.mmh3"), other = record(999, "output::mmh3/studio/film_99999.mmh3");
  other.prompt[3].extra_pnginfo.workflow.extra.h3_prompt_studio.transfer_id = "other";
  assert.equal(matchingHistoryResult(node, { good, later, other }), "mmh3/studio/film_00002.mmh3");
  later.status.status_str = "error";
  assert.equal(matchingHistoryResult(node, { good, later, other }), "mmh3/studio/film_00001.mmh3");
  good.prompt[2][26].inputs.filename_prefix = "other";
  assert.equal(matchingHistoryResult(node, { good, later, other }), null);
});

test("Save button chains native callback, restores cached output, hides F04-only action and never queues", async () => {
  const { node, output } = fixture(); let nativeCalls = 0, fetchCalls = 0;
  node.onExecuted = () => { nativeCalls++; };
  const clicks = {}, popup = { opener: {}, location: {}, close() { this.closed = true; } };
  const document = { createElement: () => ({ style: {}, addEventListener(name, handler) { clicks[name] = handler; } }) };
  const deps = { app: { nodeOutputs: { 26: output } }, api: { fetchApi() { fetchCalls++; throw new Error("Should not fetch"); } }, document, open: () => popup, notify: message => assert.fail(message) };
  assert.equal(installStudioContinuationButton(node, deps), true);
  assert.equal(installStudioContinuationButton(node, deps), true);
  assert.equal(node.widgets.filter(w => w.name === "h3studio_continue_result").length, 1);
  assert.equal(node.widgets[0].element.hidden, true);
  node.onExecuted(output); assert.equal(nativeCalls, 1);
  await clicks.click();
  assert.equal(fetchCalls, 0); assert.equal(popup.opener, null);
  assert.equal(new URL(popup.location.href).searchParams.get("continue_seed"), "43");
});

test("a repaired copy can name one verified prior transfer without weakening other history checks", () => {
  const { node, manifest, record } = fixture(), previous = record();
  const original = manifest.transfer_id;
  manifest.transfer_id = "33333333-3333-3333-3333-333333333333";
  assert.equal(matchingHistoryResult(node, { previous }), null);
  manifest.continuation_history_transfer_id = original;
  assert.equal(matchingHistoryResult(node, { previous }), "mmh3/studio/film_00001.mmh3");
  previous.prompt[3].extra_pnginfo.workflow.extra.h3_prompt_studio.project_id = "other";
  assert.equal(matchingHistoryResult(node, { previous }), null);
});

test("reloaded Save button recovers only on explicit click through GET history", async () => {
  const { node, record } = fixture(); const calls = [], clicks = {};
  const popup = { location: {}, close() {} };
  installStudioContinuationButton(node, { app: { nodeOutputs: {} }, document: { createElement: () => ({ style: {}, addEventListener(name, handler) { clicks[name] = handler; } }) }, open: () => popup,
    api: { async fetchApi(url, options) { calls.push([url, options.method]); return new Response(JSON.stringify({ exact: record() })); } }, notify: message => assert.fail(message) });
  assert.equal(calls.length, 0);
  await clicks.click();
  assert.deepEqual(calls, [["/history?max_items=100", "GET"]]);
  assert.equal(new URL(popup.location.href).searchParams.get("continue_mmh3"), "mmh3/studio/film_00001.mmh3");
});
