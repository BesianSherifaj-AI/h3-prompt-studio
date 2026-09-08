import test from "node:test";
import assert from "node:assert/strict";
import { importStudioTransfer, transferTicketFromUrl, validateTransfer, registerLoadedTransferImage, prepareWorkflowImageChoices, finishWorkflowImageChoices } from "../web/transfer-core.mjs";

const ticket = "q".repeat(43), now = Date.parse("2026-09-07T21:00:00Z"), origin = "http://127.0.0.1:8010";
function transfer() {
  return { ticket, comfy_url: origin, expires_at: new Date(now + 15 * 60 * 1000).toISOString(), resource_token: "scoped-token-1234567890", manifest: { title: "My film", image_bytes_verified: true, images: [{ node_id: "2", comfy_image: "h3studio/start.png", bytes: 1, sha256: "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb" }] }, workflow: {
    id: "studio-project", version: 0.4,
    nodes: [{ id: 1, type: "MiniMaxH3ImageToVideo", widgets_values: ["The first image starts one continuous take.", 736, 416, 362] }, { id: 2, type: "LoadImage", widgets_values: ["h3studio/start.png"] }], links: [],
  } };
}
function fixture(value = transfer()) {
  const original = { path: "workflows/My unsaved edits.json", draft: { prompt: "KEEP ME", changed: true } };
  const workflows = [original], calls = [], markers = new Map();
  const store = { activeWorkflow: original, createNewTemporary() {} };
  const app = { extensionManager: { workflow: store }, configuringGraph: false, graph: { getNodeById: () => null }, async loadGraphData(data, clean, restore, filename) {
    calls.push(["load", data, clean, restore, filename]);
    const opened = { path: `workflows/${filename}`, data };
    workflows.push(opened); store.activeWorkflow = opened;
    for (const node of data.nodes) {
      if (node.type === "LoadImage") { node.widgets = [{ name: "image", value: node.widgets_values[0], options: { values: ["root.png"] } }]; registerLoadedTransferImage(node); }
    }
    this.graph = { getNodeById: id => data.nodes.find(node => node.id === id) };
  } };
  return { original, workflows, calls, markers, options: {
    app, origin, now: () => now,
    storage: { getItem: key => markers.get(key), setItem: (key, val) => markers.set(key, val) },
    guard: { pair: token => calls.push(["pair", token]), enable: () => calls.push(["guard"]) },
    fetchImpl: async (url, options) => { calls.push([url.startsWith(origin + "/view?") ? "image" : "fetch", url, options]); return url.startsWith(origin + "/view?") ? new Response("a") : new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } }); },
  } };
}

test("only an explicit strong transfer ticket is accepted from a Comfy URL", () => {
  assert.equal(transferTicketFromUrl(origin), null);
  assert.equal(transferTicketFromUrl(`${origin}/?h3studio_transfer=${ticket}`), ticket);
  for (const value of ["short", "../secret", "https://evil.test/data", "a".repeat(129)]) assert.throws(() => transferTicketFromUrl(`${origin}/?h3studio_transfer=${encodeURIComponent(value)}`), /link is incomplete/);
});

test("transfer preserves existing draft, opens a unique native workflow tab, pairs guard, and never queues", async () => {
  const { options, original, workflows, calls } = fixture();
  const result = await importStudioTransfer(ticket, options);
  assert.equal(result.duplicate, false);
  assert.equal(workflows.length, 2);
  assert.equal(workflows[0], original);
  assert.deepEqual(original.draft, { prompt: "KEEP ME", changed: true });
  assert.equal(calls[0][1], `http://127.0.0.1:8766/api/comfy/transfers/${ticket}`);
  assert.equal(calls[0][2].method, "GET");
  assert.equal(calls[0][2].credentials, "omit");
  assert.deepEqual(calls.map(call => call[0]), ["fetch", "image", "pair", "guard", "load"]);
  assert.deepEqual(calls.at(-1).slice(2, 4), [true, true]);
  assert.ok(calls.at(-1)[4].endsWith(`${ticket}.json`));
  assert.notEqual(workflows[1].data, transfer().workflow);
  assert.deepEqual(workflows[1].data.nodes[1].widgets[0].options.values, ["root.png", "h3studio/start.png"]);
  assert.equal(registerLoadedTransferImage(workflows[1].data.nodes[1]), false, "Hook expires after this import");
});

test("same-window retry or reload does not open the transfer a second time", async () => {
  const { options, calls, workflows } = fixture();
  await importStudioTransfer(ticket, options);
  assert.deepEqual(await importStudioTransfer(ticket, options), { duplicate: true });
  assert.equal(calls.filter(call => call[0] === "fetch").length, 1);
  assert.equal(workflows.length, 2);
});

test("expired, wrong-origin, mismatched, unsafe image, and invalid workflows fail before guard or graph changes", async () => {
  const variants = [
    value => { value.expires_at = new Date(now).toISOString(); },
    value => { value.comfy_url = "http://127.0.0.1:8000"; },
    value => { value.ticket = "x".repeat(43); },
    value => { value.resource_token = ""; },
    value => { value.workflow.nodes[1].widgets_values[0] = "../private.png"; },
    value => { value.workflow.nodes.push({ ...value.workflow.nodes[0] }); },
    value => { value.workflow.nodes[0].type = "OtherNode"; },
  ];
  for (const change of variants) {
    const value = transfer(); change(value);
    const { options, calls, workflows } = fixture(value);
    await assert.rejects(importStudioTransfer(ticket, options));
    assert.deepEqual(calls.map(call => call[0]), ["fetch"]);
    assert.equal(workflows.length, 1);
  }
});

test("unsupported or still-loading Comfy never fetches or clears a graph", async () => {
  const { options, calls } = fixture();
  options.app.configuringGraph = true;
  await assert.rejects(importStudioTransfer(ticket, options), /still opening/);
  options.app.configuringGraph = false;
  delete options.app.extensionManager.workflow.createNewTemporary;
  await assert.rejects(importStudioTransfer(ticket, options), /still opening/);
  assert.deepEqual(calls, []);
});

test("a failed fetch is retryable and an unsuccessful graph load is not marked applied", async () => {
  const { options, markers, calls } = fixture();
  options.fetchImpl = async () => new Response("gone", { status: 410 });
  await assert.rejects(importStudioTransfer(ticket, options), /expired/);
  assert.equal(markers.size, 0);
  assert.deepEqual(calls, []);
  options.fetchImpl = async url => new Response(url.startsWith(origin + "/view?") ? "a" : JSON.stringify(transfer()));
  options.app.loadGraphData = async () => {};
  await assert.rejects(importStudioTransfer(ticket, options), /could not open/);
  assert.equal(markers.size, 0);
});

test("invalid expiry formats and remote destination cannot pass validation", () => {
  for (const update of [{ expires_at: now + 15000 }, { expires_at: "invalid" }, { comfy_url: "https://evil.test" }, { comfy_url: "http://user:password@127.0.0.1:8010" }]) {
    assert.throws(() => validateTransfer({ ...transfer(), ...update }, ticket, origin, now));
  }
});

test("a prepared MMH3 continuation opens when its conditioning identity matches the manifest", async () => {
  const value = transfer();
  value.workflow.nodes[0].type = "MMH3H3ContinuationCondition";
  value.manifest.conditioning_node_id = "1";
  value.workflow.nodes.push({ id: 3, type: "MMH3Create", widgets_values: [] });
  const { options, workflows } = fixture(value);
  assert.equal((await importStudioTransfer(ticket, options)).duplicate, false);
  assert.equal(workflows[1].data.nodes[0].type, "MMH3H3ContinuationCondition");
  delete value.manifest.conditioning_node_id;
  assert.throws(() => validateTransfer(value, ticket, origin, now), /conditioning node/);
  value.manifest.conditioning_node_id = 99;
  assert.throws(() => validateTransfer(value, ticket, origin, now), /conditioning node/);
});

test("deleted or changed image bytes stop before guard or graph changes", async () => {
  for (const imageResponse of [() => new Response("gone", { status: 404 }), () => new Response("b")]) {
    const { options, calls, workflows } = fixture();
    options.fetchImpl = async url => url.startsWith(origin + "/view?") ? imageResponse() : new Response(JSON.stringify(transfer()));
    await assert.rejects(importStudioTransfer(ticket, options), /photo.*(no longer available|changed)/);
    assert.equal(workflows.length, 1);
    assert.deepEqual(calls, []);
  }
});

test("saved workflows reverify their manifest without a ticket and keep unrelated metadata and selectors intact", async () => {
  const value = transfer();
  value.workflow.extra = { arbitrary_user_data: { keep: true }, h3_prompt_studio: { ...value.manifest, transfer_id: "12345678-1234-4123-8123-123456789abc" } };
  const original = structuredClone(value.workflow);
  const sharedOptions = ["root.png"];
  const node = { id: 2, type: "LoadImage", widgets: [{ name: "image", value: "h3studio/start.png", options: { values: sharedOptions } }] };
  await prepareWorkflowImageChoices(value.workflow, origin, async () => new Response("a"));
  assert.equal(registerLoadedTransferImage(node), true);
  assert.deepEqual(node.widgets[0].options.values, ["root.png", "h3studio/start.png"]);
  assert.deepEqual(sharedOptions, ["root.png"]);
  assert.deepEqual(value.workflow, original);
  finishWorkflowImageChoices();
  assert.equal(registerLoadedTransferImage(node), false);
});

test("reopen refuses changed files and does not hydrate unknown workflows", async () => {
  const value = transfer();
  value.workflow.extra = { h3_prompt_studio: { ...value.manifest, transfer_id: "12345678-1234-4123-8123-123456789abc" } };
  await assert.rejects(prepareWorkflowImageChoices(value.workflow, origin, async () => new Response("b")), /photo changed/);
  const node = { id: 2, type: "LoadImage", widgets: [{ name: "image", value: "h3studio/start.png", options: { values: [] } }] };
  assert.equal(registerLoadedTransferImage(node), false);
  let fetched = false;
  await prepareWorkflowImageChoices({ nodes: [], extra: { user: "untouched" } }, origin, async () => { fetched = true; return new Response("a"); });
  assert.equal(fetched, false);
});
