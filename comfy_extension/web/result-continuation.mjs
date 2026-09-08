import { STUDIO_ORIGIN, BridgeError, nodeType } from "./bridge-core.mjs";

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const installed = new WeakMap();

function relativePath(value, extension = false) {
  if (typeof value !== "string" || value.length > 2048) return null;
  const path = value.replaceAll("\\", "/").replace(/^output::/, "");
  if (!path || /[\x00-\x1f:?#]/.test(path) || path.split("/").some(part => !part || part === "." || part === "..") || (extension && !path.endsWith(".mmh3"))) return null;
  return path;
}

export function studioSaveContext(node) {
  const graph = node?.graph, manifest = graph?.extra?.h3_prompt_studio;
  if (nodeType(node) !== "MMH3Save" || graph?.getNodeById?.(node.id) !== node || !UUID.test(manifest?.project_id || "") || !UUID.test(manifest?.transfer_id || "") || String(manifest?.mmh3?.save_node_id) !== String(node.id)) return null;
  const prefix = relativePath(manifest.mmh3.output_prefix)?.replace(/\.mmh3$/, "");
  if (!prefix) return null;
  return { project: manifest.project_id, transfer: manifest.transfer_id, saveId: String(node.id), prefix, manifest };
}

export function savedResultFile(context, message) {
  const saved = message?.mmh3_saved;
  if (!context || !Array.isArray(saved) || saved.length !== 1) return null;
  const file = relativePath(saved[0]?.file, true);
  if (!file) return null;
  const stem = file.slice(0, -5);
  return stem === context.prefix || (stem.startsWith(context.prefix) && /^_\d{5}$/.test(stem.slice(context.prefix.length))) ? file : null;
}

export function continuationResultUrl(node, file) {
  const context = studioSaveContext(node);
  if (!savedResultFile(context, { mmh3_saved: [{ file }] })) throw new BridgeError("This saved result does not belong to the selected Studio workflow.", "wrong_saved_result");
  const url = new URL(STUDIO_ORIGIN);
  url.searchParams.set("project", context.project);
  url.searchParams.set("continue_mmh3", relativePath(file, true));
  const control = node.graph.getNodeById(context.manifest.seed_control_node_id);
  const seedInput = control?.inputs?.find(input => input.name === "seed" || input.widget?.name === "seed");
  const seed = control?.widgets?.find(widget => widget.name === "seed")?.value;
  if (nodeType(control) === "MMH3Create" && seedInput?.link == null && Number.isSafeInteger(seed) && seed >= 0 && seed < Number.MAX_SAFE_INTEGER) url.searchParams.set("continue_seed", String(seed + 1));
  return url.toString();
}

/** Read only records for this exact saved workflow, never a global latest result. */
export function matchingHistoryResult(node, history) {
  const context = studioSaveContext(node);
  if (!context) return null;
  const historyTransfer = context.manifest.continuation_history_transfer_id;
  const transfers = new Set([context.transfer, ...(UUID.test(historyTransfer || "") ? [historyTransfer] : [])]);
  const matches = [];
  for (const entry of Object.values(history || {})) {
    if (entry?.status?.status_str !== "success" || entry.status.completed !== true) continue;
    const prompt = entry.prompt, metadata = prompt?.[3]?.extra_pnginfo?.workflow?.extra?.h3_prompt_studio;
    if (metadata?.project_id !== context.project || !transfers.has(metadata?.transfer_id) || String(metadata?.mmh3?.save_node_id) !== context.saveId || relativePath(metadata?.mmh3?.output_prefix)?.replace(/\.mmh3$/, "") !== context.prefix) continue;
    const saver = prompt?.[2]?.[context.saveId];
    if (saver?.class_type !== "MMH3Save" || relativePath(saver.inputs?.filename_prefix)?.replace(/\.mmh3$/, "") !== context.prefix || saver.inputs?.target !== "output") continue;
    const file = savedResultFile(context, entry.outputs?.[context.saveId]);
    if (file && Number.isSafeInteger(prompt[0])) matches.push({ order: prompt[0], file });
  }
  matches.sort((a, b) => b.order - a.order);
  if (matches.length > 1 && matches[0].order === matches[1].order && matches[0].file !== matches[1].file) return null;
  return matches[0]?.file || null;
}

export function installStudioContinuationButton(node, { app, api, document = globalThis.document, open = (...args) => window.open(...args), notify = () => {} }) {
  if (!studioSaveContext(node)) return false;
  if (installed.has(node)) { installed.get(node).recover(); return true; }
  let saved = null, fetching = false;
  const button = document.createElement("button");
  button.type = "button";
  button.style.cssText = "width:100%;padding:9px;font-size:13px;cursor:pointer";
  const hideUnrelatedButton = () => {
    const native = node.widgets?.find(widget => widget.name === "mmh3_continue");
    if (native?.element) {
      native.element.hidden = true;
      native.element.style.display = "none";
      native.element.title = "This Studio workflow continues with the Prompt Studio button below. The original button requires MMH3's segment-review workflow.";
    }
  };
  const refresh = () => {
    hideUnrelatedButton();
    button.textContent = fetching ? "Finding this workflow's saved result…" : saved ? "Continue this result in Prompt Studio →" : "Find saved result & continue in Studio →";
    button.title = saved ? "Review the next clip in Studio. The current video and workflow are preserved; nothing runs automatically." : "After rendering, find the latest completed result from this exact Studio workflow. No generation starts.";
    button.disabled = fetching;
    node.graph?.setDirtyCanvas?.(true, true);
  };
  const recover = () => {
    saved = savedResultFile(studioSaveContext(node), app.nodeOutputs?.[String(node.id)]) || saved;
    refresh();
  };
  node.addDOMWidget("h3studio_continue_result", "button", button, { serialize: false, getMinHeight: () => 40, getMaxHeight: () => 40 });
  const previous = node.onExecuted;
  node.onExecuted = function(message, ...args) {
    const result = previous?.call(this, message, ...args);
    saved = savedResultFile(studioSaveContext(node), message);
    refresh();
    return result;
  };
  button.addEventListener("click", async () => {
    if (fetching) return;
    // Reserve the tab during the click so an explicit history lookup does not
    // lose the browser's user-gesture popup permission. It contains no data.
    const popup = open("about:blank", "_blank");
    if (popup) popup.opener = null;
    try {
      if (!saved) {
        fetching = true; refresh();
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
        try {
          const response = await api.fetchApi("/history?max_items=100", { method: "GET", signal: controller.signal });
          if (!response.ok) throw new Error("ComfyUI could not read its completed results.");
          saved = matchingHistoryResult(node, await response.json());
        } finally { clearTimeout(timer); }
      }
      if (!saved) throw new Error("No completed saved result was found for this workflow. Render with Save continuation state enabled, or open its completed workflow from ComfyUI history.");
      const url = continuationResultUrl(node, saved);
      if (!popup) throw new Error("Allow this local ComfyUI page to open Prompt Studio, then click Continue again.");
      popup.location.href = url;
    } catch (error) { popup?.close(); notify(error.message, true); }
    finally { fetching = false; refresh(); }
  });
  installed.set(node, { recover });
  recover();
  return true;
}
