import test from "node:test";
import assert from "node:assert/strict";
import { STUDIO_ORIGIN, inspectContext, inspectGenerationSettings, checkTarget, resolveH3Node, parseImageName, validMessage, validReturnedPrompt, contextFingerprint, promptTarget } from "../web/bridge-core.mjs";

function fixture(type = "MiniMaxH3ImageToVideo") {
  const graph = { id: "graph-a", links: {}, nodes: [], getNodeById(id) { return this.nodes.find(node => node.id === id); } };
  const h3 = { id: 10, comfyClass: type, graph, inputs: [], widgets: [
    { name: "prompt", value: "Exact original prompt with <Picture 1>." },
    { name: "width", value: 736 }, { name: "height", value: 416 }, { name: "length", value: 124 },
  ] };
  graph.nodes.push(h3);
  function image(slot, filename, id = graph.nodes.length + 20) {
    const node = { id, type: "LoadImage", graph, inputs: [], widgets: [{ name: "image", value: filename }] };
    graph.nodes.push(node);
    const link = Object.keys(graph.links).length + 1;
    graph.links[link] = { origin_id: id, origin_slot: 0 };
    h3.inputs.push({ name: slot, type: "IMAGE", link });
    return node;
  }
  return { graph, h3, image };
}

test("FL/I/L/T modes follow actual connected keyframes and preserve native duration", () => {
  const { h3, image } = fixture();
  assert.equal(inspectContext(h3).mode, "t2va");
  assert.equal(inspectContext(h3).duration, 124 / 24);
  image("last_frame", "end.png");
  assert.equal(inspectContext(h3).mode, "l2va");
  image("first_frame", "start.png");
  assert.deepEqual(inspectContext(h3).assets.map(asset => asset.role), ["first_frame", "last_frame"]);
  assert.equal(inspectContext(h3).mode, "fl2va");
  h3.inputs = h3.inputs.filter(input => input.name !== "last_frame");
  assert.equal(inspectContext(h3).mode, "i2va");
  h3.widgets.find(widget => widget.name === "length").value = 120;
  assert.equal(inspectContext(h3).source.aligned_length, 124);
});

test("Ref slots retain connected native input order, including gaps, without semantic guessing", () => {
  const { h3, image } = fixture("MiniMaxH3ReferenceToVideo");
  image("ref_images.ref_image_2", "person.png"); image("ref_images.ref_image_7", "scene.png");
  const context = inspectContext(h3);
  assert.equal(context.mode, "ref2va");
  assert.deepEqual(context.assets.map(asset => [asset.name, asset.reference_token, asset.semantic_role]), [["person.png", "<Picture 1>", "other"], ["scene.png", "<Picture 2>", "other"]]);
});

test("processed image nodes and MASK output are refused, not replaced with source images", () => {
  const { h3, image, graph } = fixture();
  const source = image("first_frame", "input.png");
  source.type = "ImageScale";
  assert.throws(() => inspectContext(h3), /Processed\/generated images/);
  source.type = "LoadImage"; graph.links[1].origin_slot = 1;
  assert.throws(() => inspectContext(h3), /Processed\/generated images/);
});

test("video/audio references and externally controlled prompt/geometry are refused", () => {
  const { h3 } = fixture("MiniMaxH3ReferenceToVideo");
  h3.inputs.push({ name: "ref_videos.ref_video_0", type: "IMAGE", link: 44 });
  assert.throws(() => inspectContext(h3), /video or audio/);
  h3.inputs = [{ name: "prompt", type: "STRING", link: 12 }];
  assert.throws(() => inspectContext(h3), /prompt is controlled/);
  h3.inputs = [{ name: "width", type: "INT", link: 12 }];
  assert.throws(() => inspectContext(h3), /width is controlled/);
});

test("relative Comfy names retain subfolder/type while traversal and absolute paths fail", () => {
  assert.deepEqual(parseImageName("portraits\\start.png [output]"), { filename: "start.png", subfolder: "portraits", type: "output" });
  for (const name of ["../secret.png", "C:\\private.png", "/absolute.png", "https://example.com/x.png", "one/../../x.png", "a\u0000.png", ""]) assert.throws(() => parseImageName(name));
});

test("declared Studio Inspect prompt resolves and edits its authoritative Create widget only", () => {
  const { h3, graph } = fixture();
  const create = { id: 24, type: "MMH3Create", graph, inputs: [], outputs: [{ name: "packet", type: "MMH3_MEDIA" }], widgets: [{ name: "prompt", value: "Actual Create prompt" }] };
  const inspect = { id: 25, type: "MMH3Inspect", graph, inputs: [{ name: "packet", type: "MMH3_MEDIA", link: 2 }], outputs: [{ name: "prompt", type: "STRING" }] };
  graph.nodes.push(create, inspect);
  graph.extra = { h3_prompt_studio: { conditioning_node_id: "10", prompt_control_node_id: "24" } };
  graph.links[1] = { origin_id: 25, origin_slot: 0 }; graph.links[2] = { origin_id: 24, origin_slot: 0 };
  h3.inputs.push({ name: "prompt", type: "STRING", link: 1 });
  assert.equal(inspectContext(h3).prompt, "Actual Create prompt");
  assert.equal(promptTarget(h3).node, create);
  promptTarget(h3).widget.value = "Updated prompt";
  assert.equal(inspectContext(h3).prompt, "Updated prompt");
  assert.equal(h3.widgets[0].value, "Exact original prompt with <Picture 1>.");
  for (const mutate of [() => { inspect.outputs[0].name = "name"; }, () => { create.outputs[0].type = "OTHER"; }, () => { graph.extra.h3_prompt_studio.prompt_control_node_id = 99; }]) {
    mutate(); assert.throws(() => promptTarget(h3), /prompt is controlled/);
    inspect.outputs[0].name = "prompt"; create.outputs[0].type = "MMH3_MEDIA"; graph.extra.h3_prompt_studio.prompt_control_node_id = "24";
  }
});

test("same original node is required; changed prompt is flagged independently from conditioning", () => {
  const { graph, h3, image } = fixture(); const source = image("first_frame", "first.png");
  const snapshot = { node: h3, graph, rootGraph: graph, context: inspectContext(h3) };
  assert.equal(checkTarget(snapshot, { graph }).promptChanged, false);
  h3.widgets[0].value = "Changed by user";
  assert.equal(checkTarget(snapshot, { graph }).promptChanged, true);
  assert.equal(contextFingerprint(inspectContext(h3)), contextFingerprint(snapshot.context));
  source.widgets[0].value = "different.png";
  assert.throws(() => checkTarget(snapshot, { graph }), /inputs, mode/);
  source.widgets[0].value = "first.png";
  assert.throws(() => checkTarget(snapshot, { graph: { ...graph } }), /original workflow/);
  graph.nodes[0] = { ...h3 };
  assert.throws(() => checkTarget(snapshot, { graph }), /original workflow/);
});

test("conditioning ancestors resolve uniquely and ambiguous sources require a direct choice", () => {
  const { graph, h3 } = fixture();
  const guider = { id: 30, type: "BasicGuider", graph, inputs: [{ name: "conditioning", type: "CONDITIONING", link: 1 }] };
  graph.links[1] = { origin_id: h3.id, origin_slot: 0 };
  assert.equal(resolveH3Node(guider), h3);
  const second = { ...h3, id: 11 }; graph.nodes.push(second);
  guider.inputs.push({ name: "other", type: "CONDITIONING", link: 2 }); graph.links[2] = { origin_id: 11, origin_slot: 0 };
  assert.throws(() => resolveH3Node(guider), /Multiple H3/);
});

test("messages require exact origin, frame identity, session and expected type", () => {
  const frame = {}, session = { id: "random-session", frameWindow: frame };
  const correct = { origin: STUDIO_ORIGIN, source: frame, data: { type: "h3studio.prompt", session: session.id } };
  assert.equal(validMessage(correct, session, "h3studio.prompt"), true);
  for (const changed of [{ origin: "https://example.com" }, { origin: "http://localhost:8766" }, { source: {} }, { data: { type: "h3studio.prompt", session: "wrong" } }, { data: { type: "h3studio.ready", session: session.id } }]) assert.equal(validMessage({ ...correct, ...changed }, session, "h3studio.prompt"), false);
  assert.equal(validMessage(correct, null, "h3studio.prompt"), false);
  assert.equal(validReturnedPrompt("Exact dialogue: <script> is just text."), true);
  for (const value of ["", "  ", null, {}, "x".repeat(100001)]) assert.equal(validReturnedPrompt(value), false);
});

test("generation settings follow this H3 sampler branch and exclude another prompt sharing a loader", () => {
  const { graph, h3 } = fixture();
  function add(id, type, inputs, widgets) {
    const node = { id, type, graph, inputs, widgets: Object.entries(widgets).map(([name, value]) => ({ name, value })) };
    graph.nodes.push(node); return node;
  }
  const loader = add(2, "UNETLoader", [], { unet_name: "h3.safetensors" });
  add(30, "BasicGuider", [{ name: "conditioning", type: "CONDITIONING", link: 1 }, { name: "model", type: "MODEL", link: 2 }], {});
  add(31, "SamplerCustomAdvanced", [{ name: "guider", type: "GUIDER", link: 3 }, { name: "sigmas", type: "SIGMAS", link: 4 }, { name: "noise", type: "NOISE", link: 5 }], {});
  add(32, "BasicScheduler", [{ name: "model", type: "MODEL", link: 6 }], { steps: 8, scheduler: "simple", denoise: 1 });
  add(33, "RandomNoise", [], { noise_seed: 1338 });
  add(90, "KSampler", [{ name: "model", type: "MODEL", link: 7 }], { steps: 99, seed: 999 });
  for (const [id, origin] of [[1,h3.id],[2,loader.id],[3,30],[4,32],[5,33],[6,loader.id],[7,loader.id]]) graph.links[id] = { origin_id: origin, origin_slot: 0 };
  const settings = inspectGenerationSettings(h3);
  assert.deepEqual(settings.summary, { steps: 8, scheduler: "simple", denoise: 1, unet_name: "h3.safetensors", seed: 1338 });
  assert.equal(settings.nodes.some(node => node.node_id === "90"), false);
  assert.equal(settings.nodes.some(node => "prompt" in node.values), false);
  assert.equal(inspectContext(h3).source.generation_settings.summary.steps, 8);
});

test("connected sampler widgets are labelled as connected, never reported with their stale widget value", () => {
  const { graph, h3 } = fixture();
  h3.widgets.push({ name: "ref_image_size", value: "match" });
  h3.inputs.push({ name: "ref_image_size", type: "COMBO", link: 1 });
  graph.links[1] = { origin_id: 99, origin_slot: 0 };
  const record = inspectGenerationSettings(h3).nodes.find(node => node.node_id === String(h3.id));
  assert.deepEqual(record.connected, ["ref_image_size"]);
  assert.equal(record.values.ref_image_size, undefined);
});

test("all connected LoRAs retain their names and separate strengths in graph order", () => {
  const { graph, h3 } = fixture();
  const first = { id: 40, type: "LoraLoaderModelOnly", graph, inputs: [], widgets: [{ name: "lora_name", value: "turbo.safetensors" }, { name: "strength_model", value: 1 }] };
  const second = { id: 41, type: "LoraLoader", graph, inputs: [{ name: "model", type: "MODEL", link: 1 }], widgets: [{ name: "lora_name", value: "style.safetensors" }, { name: "strength_model", value: 0.65 }, { name: "strength_clip", value: 0.5 }] };
  graph.nodes.push(first, second);
  h3.inputs.push({ name: "clip", type: "CLIP", link: 2 });
  graph.links[1] = { origin_id: 40, origin_slot: 0 };
  graph.links[2] = { origin_id: 41, origin_slot: 1 };
  const result = inspectGenerationSettings(h3);
  assert.deepEqual(result.loras, [
    { node_id: "40", name: "turbo.safetensors", enabled: true, strength_model: 1 },
    { node_id: "41", name: "style.safetensors", enabled: true, strength_model: 0.65, strength_clip: 0.5 },
  ]);
  assert.equal(result.summary.lora_name, undefined);
});
