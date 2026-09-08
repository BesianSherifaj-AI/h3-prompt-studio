import { STUDIO_ORIGIN, BridgeError, nodeType, parseImageName } from "./bridge-core.mjs";
import { validResourceToken } from "./queue-guard.mjs";

export const MAX_WORKFLOW_BYTES = 2 * 1024 * 1024;
const TICKET = /^[A-Za-z0-9_-]{32,128}$/;
const H3_TYPES = new Set(["MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo", "MMH3H3ContinuationCondition"]);
let loadingImages = null;
let loadingImageWorkflow = null;

export function finishWorkflowImageChoices() {
  loadingImages = null;
  loadingImageWorkflow = null;
}

/** Saved Studio workflows keep their manifest and can be reopened without a ticket. */
export async function prepareWorkflowImageChoices(workflow, origin, fetchImpl = fetch) {
  if (loadingImages && loadingImageWorkflow === workflow?.id) return;
  finishWorkflowImageChoices();
  const manifest = workflow?.extra?.h3_prompt_studio;
  if (manifest?.image_bytes_verified !== true || typeof manifest.transfer_id !== "string" || !/^[a-f0-9-]{36}$/.test(manifest.transfer_id)) return;
  const verified = await verifyTransferImages({ workflow, manifest }, origin, fetchImpl);
  loadingImages = verified;
  loadingImageWorkflow = workflow.id;
}

/** Match native upload behavior before Comfy's missing-media scan runs. */
export function registerLoadedTransferImage(node) {
  const filename = loadingImages?.get(String(node?.id));
  if (!filename || nodeType(node) !== "LoadImage") return false;
  const widget = node.widgets?.find(item => item.name === "image");
  if (!widget || widget.value !== filename) return false;
  const current = widget.options?.values;
  const choices = typeof current === "function" ? current() : current;
  if (!Array.isArray(choices)) throw new BridgeError("This ComfyUI image selector cannot receive the verified Studio photo.", "unsupported_image_widget");
  // Use a new array for this node only. Do not alter a shared node-definition
  // catalog, another workflow's selector, or the selected image value itself.
  widget.options = { ...widget.options, values: choices.includes(filename) ? [...choices] : [...choices, filename] };
  return true;
}

export async function verifyTransferImages(transfer, origin, fetchImpl = fetch, timeoutMs = 15000) {
  const nodes = transfer.workflow.nodes.filter(node => node.type === "LoadImage");
  const images = transfer.manifest?.images;
  if (!nodes.length) return new Map();
  if (nodes.length > 9 || transfer.manifest?.image_bytes_verified !== true || !Array.isArray(images) || images.length !== nodes.length) throw new BridgeError("Studio did not supply verification details for these photos. Send the project again.", "invalid_transfer_images");
  const verified = new Map();
  for (const node of nodes) {
    const value = node.widgets_values[0];
    const image = images.find(item => String(item.node_id) === String(node.id));
    const file = parseImageName(value);
    if (!image || image.comfy_image !== value || file.type !== "input" || !/^[a-f0-9]{64}$/.test(image.sha256) || !Number.isInteger(image.bytes) || image.bytes <= 0 || image.bytes > 64 * 1024 * 1024) throw new BridgeError("A prepared photo does not match its transfer manifest. Send the project again.", "invalid_transfer_images");
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const query = new URLSearchParams({ filename: file.filename, subfolder: file.subfolder, type: "input" });
      const response = await fetchImpl(`${origin}/view?${query}`, { method: "GET", credentials: "omit", cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new BridgeError("A transferred photo is no longer available in ComfyUI. Send the project again.", "transfer_image_missing");
      const advertised = Number(response.headers.get("content-length") || 0);
      if (advertised && advertised !== image.bytes) throw new BridgeError("A transferred photo changed in ComfyUI. Send the project again.", "transfer_image_changed");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== image.bytes) throw new BridgeError("A transferred photo changed in ComfyUI. Send the project again.", "transfer_image_changed");
      const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(value => value.toString(16).padStart(2, "0")).join("");
      if (hash !== image.sha256) throw new BridgeError("A transferred photo changed in ComfyUI. Send the project again.", "transfer_image_changed");
      verified.set(String(node.id), value);
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      throw new BridgeError("ComfyUI could not verify a transferred photo. Keep both apps running and retry.", "transfer_image_unavailable");
    } finally { clearTimeout(timer); }
  }
  return verified;
}

export function transferTicketFromUrl(url) {
  const value = new URL(url).searchParams.get("h3studio_transfer");
  if (value === null) return null;
  if (!TICKET.test(value)) throw new BridgeError("The Studio transfer link is incomplete. Use Send to ComfyUI in Studio again.", "invalid_transfer");
  return value;
}

export function validateTransfer(value, ticket, origin, now = Date.now()) {
  if (!value || typeof value !== "object" || value.ticket !== ticket || !TICKET.test(ticket)) throw new BridgeError("Studio returned a different transfer. Send your project again.", "invalid_transfer");
  let target;
  try { target = new URL(value.comfy_url); } catch { throw new BridgeError("This Studio transfer has no valid ComfyUI destination.", "invalid_transfer"); }
  if (target.origin !== origin || target.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) || target.username || target.password) {
    throw new BridgeError("This workflow was prepared for a different ComfyUI server. Send it from Studio to this server again.", "wrong_destination");
  }
  const expiry = typeof value.expires_at === "string" ? Date.parse(value.expires_at) : NaN;
  if (!Number.isFinite(expiry) || expiry <= now || expiry > now + 30 * 60 * 1000) throw new BridgeError("This Studio transfer expired. Use Send to ComfyUI again.", "expired_transfer");
  if (!validResourceToken(value.resource_token)) throw new BridgeError("Studio could not pair the GPU handoff. Send this project again.", "invalid_transfer");
  const workflow = value.workflow;
  if (!workflow || typeof workflow !== "object" || !Array.isArray(workflow.nodes) || !workflow.nodes.length || workflow.nodes.length > 300 || !Array.isArray(workflow.links) || workflow.links.length > 3000 || JSON.stringify(workflow).length > MAX_WORKFLOW_BYTES) {
    throw new BridgeError("Studio did not return a supported ComfyUI workflow.", "invalid_workflow");
  }
  const ids = new Set();
  let h3Count = 0;
  for (const node of workflow.nodes) {
    if (!node || !["number", "string"].includes(typeof node.id) || typeof node.type !== "string" || ids.has(String(node.id))) throw new BridgeError("The prepared workflow has an invalid node identity.", "invalid_workflow");
    ids.add(String(node.id));
    if (H3_TYPES.has(node.type)) h3Count++;
    if (node.type === "LoadImage") parseImageName(node.widgets_values?.[0]);
  }
  if (h3Count !== 1) throw new BridgeError("A Studio transfer must contain one H3 generation node.", "invalid_workflow");
  const condition = workflow.nodes.find(node => H3_TYPES.has(node.type));
  const conditionId = value.manifest?.conditioning_node_id;
  if ((conditionId != null && String(conditionId) !== String(condition.id)) || (condition.type === "MMH3H3ContinuationCondition" && conditionId == null)) {
    throw new BridgeError("The prepared workflow's H3 conditioning node does not match its transfer manifest.", "invalid_workflow");
  }
  return value;
}

/** Import a new native workflow tab; never submits a queue or executes a model. */
export async function importStudioTransfer(ticket, {
  app, guard, origin, storage, fetchImpl = fetch, now = () => Date.now(), timeoutMs = 15000,
}) {
  if (!TICKET.test(ticket)) throw new BridgeError("This Studio transfer link is invalid.", "invalid_transfer");
  const marker = `h3studio-transfer-applied:${ticket}`;
  if (storage?.getItem(marker)) return { duplicate: true };
  const workflowStore = app?.extensionManager?.workflow;
  if (typeof app?.loadGraphData !== "function" || typeof workflowStore?.createNewTemporary !== "function" || !workflowStore.activeWorkflow || app.configuringGraph) {
    throw new BridgeError("ComfyUI is still opening its workflow. Wait a moment and choose Retry Studio transfer.", "comfy_not_ready");
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let transfer;
  try {
    const response = await fetchImpl(`${STUDIO_ORIGIN}/api/comfy/transfers/${encodeURIComponent(ticket)}`, {
      method: "GET", credentials: "omit", cache: "no-store", signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new BridgeError(response.status === 404 || response.status === 410 ? "This Studio transfer expired. Use Send to ComfyUI again." : `Studio could not open this transfer (${response.status}). Keep Studio running and retry.`, "transfer_unavailable");
    if (Number(response.headers.get("content-length")) > MAX_WORKFLOW_BYTES) throw new BridgeError("The Studio transfer is too large.", "invalid_transfer");
    const body = await response.text();
    if (body.length > MAX_WORKFLOW_BYTES) throw new BridgeError("The Studio transfer is too large.", "invalid_transfer");
    let value;
    try { value = JSON.parse(body); } catch { throw new BridgeError("Studio returned an unreadable transfer. Send your project again.", "invalid_transfer"); }
    transfer = validateTransfer(value, ticket, origin, now());
  } catch (error) {
    if (error instanceof BridgeError) throw error;
    throw new BridgeError(controller.signal.aborted ? "Studio transfer timed out. Keep Studio running and retry." : "Studio is unavailable. Start Prompt Studio, then retry this transfer.", "transfer_unavailable");
  } finally { clearTimeout(timeout); }

  // The installed frontend persists the current draft in beforeLoadNewGraph and
  // creates a separate temporary workflow when a new unique filename is passed.
  // Do not use graph.configure(), graph.clear(), or a same-name workflow update.
  const title = String(transfer.manifest?.title || transfer.title || "H3 Prompt Studio").replace(/[\x00-\x1f<>:"/\\|?*]/g, " ").trim().slice(0, 90) || "H3 Prompt Studio";
  const filename = `${title} — Studio ${ticket}.json`;
  const data = structuredClone(transfer.workflow);
  const verifiedImages = await verifyTransferImages(transfer, origin, fetchImpl, timeoutMs);
  guard.pair(transfer.resource_token);
  guard.enable();
  loadingImages = verifiedImages;
  loadingImageWorkflow = data.id;
  try { await app.loadGraphData(data, true, true, filename); }
  finally { finishWorkflowImageChoices(); }
  const target = data.nodes.find(node => H3_TYPES.has(node.type));
  const loaded = app.graph?.getNodeById?.(target.id);
  if (!loaded || nodeType(loaded) !== target.type) throw new BridgeError("ComfyUI could not open the prepared H3 node. Check its missing-node message, then retry this transfer.", "workflow_not_loaded");
  storage?.setItem(marker, String(now()));
  return { duplicate: false, title, filename, manifest: transfer.manifest };
}
