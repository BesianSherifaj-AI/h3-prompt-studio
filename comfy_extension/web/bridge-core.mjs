export const STUDIO_ORIGIN = "http://127.0.0.1:8766";
export const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
export const MAX_TOTAL_IMAGE_BYTES = 32 * 1024 * 1024;
export const MAX_PROMPT_LENGTH = 100000;
const H3_TYPES = new Set(["MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"]);

export class BridgeError extends Error {
  constructor(message, code = "unsupported_context") {
    super(message);
    this.name = "BridgeError";
    this.code = code;
  }
}

export function nodeType(node) {
  return node?.comfyClass || node?.type;
}

function graphLink(graph, id) {
  let link = graph?.getLink?.(id) ?? graph?.links?.get?.(id) ?? graph?.links?.[id];
  if (Array.isArray(link)) {
    link = { id: link[0], origin_id: link[1], origin_slot: link[2], target_id: link[3], target_slot: link[4], type: link[5] };
  }
  return link;
}

function upstream(node, input) {
  if (input?.link == null) return null;
  const link = graphLink(node.graph, input.link);
  const source = link && node.graph?.getNodeById?.(link.origin_id);
  if (!source) throw new BridgeError(`The ${input.name} connection cannot be resolved.`);
  return { node: source, output: link.origin_slot };
}

export function resolveH3Node(selected) {
  if (nodeType(selected) === "MMH3H3ContinuationCondition") throw new BridgeError("For this saved-state continuation, use the MMH3 Save node’s Continue in Prompt Studio button. Native prompt-only import does not support continuation graphs.");
  if (H3_TYPES.has(nodeType(selected))) return selected;
  const queue = [{ node: selected, depth: 0 }], seen = new Set(), found = new Set();
  while (queue.length) {
    const { node, depth } = queue.shift();
    if (!node || seen.has(node)) continue;
    if (seen.size >= 80 || depth > 12) throw new BridgeError("Select the H3 prompt node directly; this connection path is too large.");
    seen.add(node);
    if (H3_TYPES.has(nodeType(node))) { found.add(node); continue; }
    for (const input of node.inputs || []) {
      if (input.link != null && ["CONDITIONING", "LATENT"].includes(input.type)) {
        queue.push({ node: upstream(node, input).node, depth: depth + 1 });
      }
    }
  }
  if (found.size !== 1) throw new BridgeError(found.size ? "Multiple H3 prompts feed this node. Select the intended H3 prompt node directly." : "Select a native MiniMax H3 Image to Video or Reference to Video prompt node.");
  return [...found][0];
}

export function localWidget(node, name) {
  const input = node.inputs?.find(item => item.name === name || item.widget?.name === name);
  if (input?.link != null) throw new BridgeError(`${name} is controlled by a connected node. Use a direct H3 widget before importing.`);
  const widget = node.widgets?.find(item => item.name === name);
  if (!widget) throw new BridgeError(`The selected H3 node has no editable ${name} widget.`);
  return widget;
}

/** Only Studio's declared Create → Inspect → native H3 prompt path is editable. */
export function promptTarget(h3) {
  const input = h3.inputs?.find(item => item.name === "prompt" || item.widget?.name === "prompt");
  if (input?.link == null) return { node: h3, widget: localWidget(h3, "prompt") };
  const manifest = h3.graph?.extra?.h3_prompt_studio;
  const invalid = () => new BridgeError("prompt is controlled by a connected node. Reopen its Studio source project or use a supported Studio Create → Inspect connection.");
  if (!H3_TYPES.has(nodeType(h3)) || String(manifest?.conditioning_node_id) !== String(h3.id) || manifest?.prompt_control_node_id == null) throw invalid();
  const inspect = upstream(h3, input);
  const port = inspect?.node.outputs?.[inspect.output];
  if (input.type !== "STRING" || nodeType(inspect?.node) !== "MMH3Inspect" || port?.name !== "prompt" || port?.type !== "STRING") throw invalid();
  const packetInput = inspect.node.inputs?.find(item => item.name === "packet");
  const create = packetInput?.link != null && upstream(inspect.node, packetInput);
  const packetPort = create?.node.outputs?.[create.output];
  if (packetInput?.type !== "MMH3_MEDIA" || nodeType(create?.node) !== "MMH3Create" || String(create.node.id) !== String(manifest.prompt_control_node_id) || packetPort?.name !== "packet" || packetPort?.type !== "MMH3_MEDIA") throw invalid();
  return { node: create.node, widget: localWidget(create.node, "prompt") };
}

export function parseImageName(value) {
  if (typeof value !== "string" || !value.trim()) throw new BridgeError("A connected Load Image node has no selected image.");
  const match = value.match(/^(.*?)(?: \[(input|output|temp)\])?$/);
  const path = match[1].replaceAll("\\", "/"), type = match[2] || "input";
  const segments = path.split("/");
  if (!path || path.startsWith("/") || /^[a-z]:/i.test(path) || segments.some(part => !part || part === "." || part === "..") || /[\u0000-\u001f]/.test(path)) {
    throw new BridgeError("Only relative Comfy image files can be imported.");
  }
  return { filename: segments.at(-1), subfolder: segments.slice(0, -1).join("/"), type };
}

function imageDescriptor(h3, input, role, referenceIndex) {
  let current = upstream(h3, input), seen = new Set();
  while (current && nodeType(current.node) === "Reroute") {
    if (seen.has(current.node) || seen.size >= 16) throw new BridgeError(`The ${input.name} image path contains a cycle or too many reroutes.`);
    seen.add(current.node);
    const connected = current.node.inputs?.filter(item => item.link != null) || [];
    if (connected.length !== 1) throw new BridgeError(`The ${input.name} reroute is not connected to one image source.`);
    current = upstream(current.node, connected[0]);
  }
  if (!current || nodeType(current.node) !== "LoadImage" || current.output !== 0) {
    throw new BridgeError(`${input.name} must come from Load Image directly or through a transparent reroute. Processed/generated images must be saved and loaded explicitly; the bridge will not run their graph.`);
  }
  const file = parseImageName(localWidget(current.node, "image").value);
  return {
    name: file.filename, role, semantic_role: "other", input_name: input.name,
    source_node_id: String(current.node.id), ...file,
    ...(referenceIndex ? { reference_index: referenceIndex, reference_token: `<Picture ${referenceIndex}>` } : {}),
  };
}

/** Read settings belonging to this conditioning/sampling branch, without evaluating nodes. */
export function inspectGenerationSettings(h3) {
  const graph = h3.graph;
  const nodes = graph?._nodes || graph?.nodes || [];
  const selected = new Set([h3]);
  const downstream = [h3];
  // Follow conditioning/latent output paths to this prompt's sampler(s). A shared
  // loader must not cause unrelated H3 branches to become part of the import.
  for (let cursor = 0; cursor < downstream.length && selected.size < 80; cursor++) {
    const current = downstream[cursor];
    for (const candidate of nodes) {
      if (selected.has(candidate)) continue;
      if ((candidate.inputs || []).some(input => {
        if (input.link == null || !["CONDITIONING", "LATENT", "GUIDER"].includes(input.type)) return false;
        return graphLink(graph, input.link)?.origin_id === current.id;
      })) {
        selected.add(candidate);
        downstream.push(candidate);
      }
    }
  }
  const ancestors = [...selected];
  for (let cursor = 0; cursor < ancestors.length && selected.size < 100; cursor++) {
    for (const input of ancestors[cursor].inputs || []) {
      if (input.link == null || ["IMAGE", "MASK", "AUDIO", "VIDEO"].includes(input.type)) continue;
      const link = graphLink(graph, input.link);
      const source = link && graph?.getNodeById?.(link.origin_id);
      if (source && !selected.has(source)) { selected.add(source); ancestors.push(source); }
    }
  }
  const allowed = new Set([
    "width", "height", "length", "ref_image_size", "steps", "denoise", "cfg",
    "seed", "noise_seed", "control_after_generate", "sampler_name", "scheduler",
    "unet_name", "weight_dtype", "clip_name", "vae_name", "lora_name", "strength_model", "strength_clip",
    "video_shift", "audio_shift", "shift", "shift_video", "shift_audio",
    "start_at_step", "end_at_step", "add_noise", "return_with_leftover_noise",
    "sparsity", "top_k", "block_size", "backend", "attention_backend", "attention_mode",
  ]);
  const records = [...selected].sort((a, b) => nodes.indexOf(a) - nodes.indexOf(b)).map(node => {
    const values = {};
    const connected = [];
    for (const widget of node.widgets || []) {
      if (!allowed.has(widget.name)) continue;
      if ((node.inputs || []).some(input => input.link != null && (input.name === widget.name || input.widget?.name === widget.name))) {
        connected.push(widget.name);
        continue;
      }
      const value = widget.value;
      if ((typeof value === "string" && value.length <= 512) || (typeof value === "number" && Number.isFinite(value)) || typeof value === "boolean") values[widget.name] = value;
    }
    return { node_id: String(node.id), node_type: nodeType(node), title: String(node.title || nodeType(node)).slice(0, 200), enabled: ![2, 4].includes(node.mode), values, ...(connected.length ? { connected } : {}) };
  }).filter(record => Object.keys(record.values).length || record.connected?.length);
  const summary = {};
  for (const key of ["steps", "sampler_name", "scheduler", "denoise", "cfg", "unet_name", "clip_name", "lora_name", "strength_model", "video_shift", "audio_shift"]) {
    const values = [...new Set(records.map(record => record.values[key]).filter(value => value !== undefined))];
    if (values.length === 1) summary[key] = values[0];
  }
  const seeds = [...new Set(records.flatMap(record => [record.values.noise_seed, record.values.seed]).filter(value => value !== undefined))];
  if (seeds.length === 1) summary.seed = seeds[0];
  const width = Number(localWidget(h3, "width").value), height = Number(localWidget(h3, "height").value), length = Number(localWidget(h3, "length").value);
  const loras = records.filter(record => record.values.lora_name !== undefined).map(record => ({
    node_id: record.node_id, name: record.values.lora_name, enabled: record.enabled,
    strength_model: record.values.strength_model,
    ...(record.values.strength_clip !== undefined ? { strength_clip: record.values.strength_clip } : {}),
  }));
  return { width, height, length, fps: 24, megapixels: width * height / 1e6, nodes: records, loras, summary };
}

export function inspectContext(h3) {
  if (!H3_TYPES.has(nodeType(h3))) throw new BridgeError("This is not a supported native H3 prompt node.");
  const editable = promptTarget(h3), prompt = editable.widget.value;
  if (typeof prompt !== "string" || prompt.length > MAX_PROMPT_LENGTH) throw new BridgeError("The H3 prompt must be text no longer than 100,000 characters.");
  const width = Number(localWidget(h3, "width").value), height = Number(localWidget(h3, "height").value), length = Number(localWidget(h3, "length").value);
  if (![width, height, length].every(Number.isInteger) || width < 32 || height < 32 || length < 5 || length > 3600) throw new BridgeError("The H3 dimensions or frame count are invalid.");
  const alignedLength = Math.ceil((length - 5) / 17) * 17 + 5;
  const connected = (h3.inputs || []).filter(input => input.link != null);
  let mode, assets;
  if (nodeType(h3) === "MiniMaxH3ReferenceToVideo") {
    if (connected.some(input => /^(?:ref_videos\.|ref_video_audios\.|ref_audios\.|ref_video_|ref_audio_)/.test(input.name))) {
      throw new BridgeError("This H3 node has video or audio references. The current bridge imports images only; it will not silently drop those conditioning inputs.");
    }
    const imageInputs = connected.filter(input => /^(?:ref_images\.)?ref_image_\d+$/.test(input.name));
    if (imageInputs.length > 9) throw new BridgeError("Ref2VA supports at most nine connected reference images.");
    assets = imageInputs.map((input, index) => imageDescriptor(h3, input, "reference_image", index + 1));
    mode = "ref2va";
  } else {
    const first = connected.find(input => input.name === "first_frame"), last = connected.find(input => input.name === "last_frame");
    assets = [first && imageDescriptor(h3, first, "first_frame"), last && imageDescriptor(h3, last, "last_frame")].filter(Boolean);
    mode = first && last ? "fl2va" : first ? "i2va" : last ? "l2va" : "t2va";
  }
  const source = {
    node_id: String(h3.id), node_type: nodeType(h3), graph_id: h3.graph?.id ?? null,
    prompt_control_node_id: String(editable.node.id),
    prompt, requested_length: length, aligned_length: alignedLength, fps: 24,
    width, height, native_duration_seconds: alignedLength / 24,
    generation_settings: inspectGenerationSettings(h3),
    image_representation: "Selected original LoadImage files; any internal H3 resizing is not evaluated by this bridge.",
  };
  return { mode, duration: alignedLength / 24, prompt, node_id: String(h3.id), assets, source };
}

export function contextFingerprint(context) {
  const { prompt, source, ...rest } = context;
  const { prompt: _sourcePrompt, ...sourceRest } = source;
  return JSON.stringify({ ...rest, source: sourceRest });
}

export function checkTarget(snapshot, app) {
  if (app.graph !== snapshot.rootGraph || snapshot.node.graph !== snapshot.graph || snapshot.graph?.getNodeById?.(snapshot.node.id) !== snapshot.node) {
    throw new BridgeError("The original workflow or H3 node changed. Reopen Studio from the intended current H3 node.", "stale_target");
  }
  const context = inspectContext(snapshot.node);
  if (contextFingerprint(context) !== contextFingerprint(snapshot.context)) throw new BridgeError("The H3 image inputs, mode, dimensions, duration or generation settings changed. Import the current node again before applying.", "changed_context");
  return { context, promptChanged: context.prompt !== snapshot.context.prompt };
}

export function validMessage(event, session, type) {
  return Boolean(session && event.origin === STUDIO_ORIGIN && event.source === session.frameWindow && event.data?.type === type && event.data.session === session.id);
}

export function validReturnedPrompt(value) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= MAX_PROMPT_LENGTH;
}

export async function imageDataUrl(descriptor, fetchApi) {
  const query = new URLSearchParams({ filename: descriptor.filename, subfolder: descriptor.subfolder, type: descriptor.type });
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetchApi(`/view?${query}`, { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new BridgeError(`Image ${descriptor.name} could not be read (${response.status}).`);
    const advertised = Number(response.headers.get("content-length") || 0);
    if (advertised > MAX_IMAGE_BYTES) throw new BridgeError(`Image ${descriptor.name} exceeds the 8 MiB bridge limit.`);
    const blob = await response.blob();
    if (!blob.size || blob.size > MAX_IMAGE_BYTES) throw new BridgeError(`Image ${descriptor.name} is empty or exceeds 8 MiB.`);
    const mime = blob.type.toLowerCase().split(";")[0];
    if (!["image/png", "image/jpeg", "image/webp"].includes(mime)) throw new BridgeError(`Image ${descriptor.name} must be a PNG, JPEG or WebP image.`);
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new BridgeError(`Image ${descriptor.name} could not be encoded.`));
      reader.readAsDataURL(blob);
    });
    return { dataUrl, bytes: blob.size };
  } finally { clearTimeout(timeout); }
}
