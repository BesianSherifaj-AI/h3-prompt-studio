import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
  STUDIO_ORIGIN, MAX_TOTAL_IMAGE_BYTES, BridgeError, resolveH3Node, inspectContext,
  checkTarget, localWidget, promptTarget, validMessage, validReturnedPrompt, imageDataUrl,
} from "./bridge-core.mjs";
import { createQueueGuard, validResourceToken } from "./queue-guard.mjs";
import { importStudioTransfer, transferTicketFromUrl, registerLoadedTransferImage, prepareWorkflowImageChoices, finishWorkflowImageChoices } from "./transfer-core.mjs";
import { installStudioContinuationButton } from "./result-continuation.mjs";

let active = null;
const controls = new Set();
const guard = createQueueGuard(api, { onChange: () => refreshGuardControls() });
let pendingTransfer = null, transferPromise = null;

function element(tag, text, className) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function notice(message, error = false) {
  const box = element("div", message, "h3studio-notice");
  box.setAttribute("role", error ? "alert" : "status");
  box.style.cssText = `position:fixed;right:24px;bottom:28px;max-width:520px;z-index:1000000;padding:16px 20px;border:1px solid ${error ? "#dd886b" : "#66c3b0"};border-radius:12px;background:#141b25;color:#f5f7fb;font:14px/1.5 system-ui;box-shadow:0 8px 35px #0008;white-space:pre-wrap`;
  document.body.append(box);
  setTimeout(() => box.remove(), error ? 14000 : 6000);
}

function refreshGuardControls() {
  const state = guard.status();
  for (const checkbox of controls) {
    checkbox.checked = state.enabled;
    checkbox.disabled = !state.paired;
  }
}

async function receivePreparedWorkflow(ticket, session = null) {
  if (transferPromise) throw new BridgeError("A Studio workflow is already opening. Wait for it to finish.");
  pendingTransfer = ticket;
  transferPromise = importStudioTransfer(ticket, {
    app, guard, origin: location.origin, storage: window.sessionStorage,
  });
  try {
    const result = await transferPromise;
    pendingTransfer = null;
    const url = new URL(location.href);
    if (url.searchParams.get("h3studio_transfer") === ticket) {
      url.searchParams.delete("h3studio_transfer");
      history.replaceState(history.state, "", url);
    }
    if (session && active === session) {
      reply(session, "h3studio.transfer_applied", { ticket, title: result.title || "H3 Prompt Studio" });
      setTimeout(() => closeSession(session), 200);
    }
    notice(result.duplicate ? "This Studio workflow has already been opened in this window." : "Studio prompt, images and settings opened in a new workflow tab. Press Run when ready; the GPU guard releases the AI model first.");
    return result;
  } finally { transferPromise = null; }
}

function receiveTransferFromUrl() {
  try { pendingTransfer = transferTicketFromUrl(location.href); }
  catch (error) { notice(error.message, true); return; }
  if (!pendingTransfer) return;
  const started = Date.now();
  const waitForStartup = () => {
    if (!pendingTransfer) return;
    // setup() runs before Comfy restores its initial workflow. Waiting for the
    // public workspace spinner avoids a later startup restore hiding this tab.
    if (app.isGraphReady && app.extensionManager?.workflow?.activeWorkflow && !app.extensionManager.spinner && !app.configuringGraph) {
      receivePreparedWorkflow(pendingTransfer).catch(error => notice(error.message, true));
    } else if (Date.now() - started < 120000) setTimeout(waitForStartup, 250);
    else notice("ComfyUI is taking longer to start. Choose H3 Prompt Studio → Retry Studio transfer once its workflow is ready.", true);
  };
  setTimeout(waitForStartup, 250);
}

function selectedH3() {
  const canvas = app.canvas;
  const selected = canvas?.selectedItems ? [...canvas.selectedItems].filter(item => item?.widgets) : Object.values(canvas?.selected_nodes || {});
  if (selected.length !== 1) throw new BridgeError("Select one H3 prompt node, then choose Open H3 Prompt Studio.");
  return resolveH3Node(selected[0]);
}

function closeSession(session) {
  if (!session || active !== session) return;
  active = null;
  clearTimeout(session.connectionTimer);
  clearTimeout(session.expiryTimer);
  session.cancelConflict?.();
  controls.delete(session.checkbox);
  session.overlay.remove();
  session.frameWindow = null;
  session.payloadPromise = null;
}

function reply(session, type, extra = {}) {
  session.frameWindow?.postMessage({ type, session: session.id, ...extra }, STUDIO_ORIGIN);
}

async function buildPayload(session) {
  checkTarget(session.snapshot, app);
  let bytes = 0;
  const assets = [];
  // Sequential reads keep the transfer bounded and avoid simultaneous full-size
  // image allocations. No graph execution or arbitrary URL fetch is involved.
  for (const descriptor of session.snapshot.context.assets) {
    const image = await imageDataUrl(descriptor, api.fetchApi.bind(api));
    bytes += image.bytes;
    if (bytes > MAX_TOTAL_IMAGE_BYTES) throw new BridgeError("Selected images exceed the 32 MiB total bridge limit. Use smaller loaded images.");
    const { filename, subfolder, type, ...publicFields } = descriptor;
    assets.push({ ...publicFields, data_url: image.dataUrl });
  }
  const current = checkTarget(session.snapshot, app);
  if (current.promptChanged) throw new BridgeError("The prompt changed while its images were being read. Import this H3 node again.");
  return { ...session.snapshot.context, assets };
}

function changedPromptDialog(session, currentPrompt, returnedPrompt) {
  return new Promise(resolve => {
    const sheet = element("div", null, "h3studio-conflict");
    sheet.style.cssText = "position:absolute;inset:62px 0 0;background:#141b25;z-index:2;padding:28px;overflow:auto;color:#eef4fa;font:14px/1.5 system-ui";
    sheet.append(element("h2", "The Comfy prompt changed while Studio was open"));
    sheet.append(element("p", "Review both versions before replacing the current prompt. Image inputs and generation settings will remain unchanged."));
    for (const [title, value] of [["Current Comfy prompt", currentPrompt], ["Returned Studio prompt", returnedPrompt]]) {
      sheet.append(element("h3", title));
      const text = element("textarea");
      text.value = value; text.readOnly = true;
      text.style.cssText = "width:100%;height:150px;box-sizing:border-box;padding:12px;background:#0d1420;color:#eef4fa;border:1px solid #4c596d;border-radius:8px;font:13px/1.5 system-ui";
      sheet.append(text);
    }
    const cancel = element("button", "Keep current prompt"), replace = element("button", "Replace with Studio prompt");
    for (const button of [cancel, replace]) button.style.cssText = "margin:18px 12px 0 0;padding:10px 16px;border-radius:8px;border:1px solid #6680a0;background:#26394d;color:white;cursor:pointer";
    const finish = (accepted) => { session.cancelConflict = null; sheet.remove(); resolve(accepted); };
    session.cancelConflict = () => finish(false);
    cancel.onclick = () => finish(false);
    replace.onclick = () => finish(true);
    sheet.append(cancel, replace); session.panel.append(sheet);
  });
}

async function acceptPrompt(session, prompt) {
  if (!session.imported) throw new BridgeError("Import the selected H3 inputs before returning a prompt.", "not_imported");
  if (!validReturnedPrompt(prompt)) throw new BridgeError("Studio returned an empty or oversized prompt.", "invalid_prompt");
  if (session.applying) throw new BridgeError("A prompt replacement is already being reviewed.");
  session.applying = true;
  try {
    const before = checkTarget(session.snapshot, app);
    if (before.promptChanged && !await changedPromptDialog(session, before.context.prompt, prompt)) {
      reply(session, "h3studio.error", { code: "replacement_declined", message: "The current Comfy prompt was kept." });
      return;
    }
    if (active !== session) return;
    const now = checkTarget(session.snapshot, app);
    if (now.context.prompt !== before.context.prompt) throw new BridgeError("The Comfy prompt changed again. Return the Studio prompt again to review the latest version.", "changed_prompt");
    const target = promptTarget(session.snapshot.node), node = target.node, graph = node.graph, widget = target.widget;
    graph.beforeChange?.();
    try {
      widget.value = prompt;
      if (widget.inputEl && "value" in widget.inputEl) widget.inputEl.value = prompt;
      widget.callback?.(prompt, app.canvas, node, app.canvas?.graph_mouse);
      graph.setDirtyCanvas?.(true, true);
    } finally { graph.afterChange?.(); }
    reply(session, "h3studio.applied", { node_id: String(session.snapshot.node.id) });
    notice("Studio prompt applied to the selected H3 node. Nothing was queued.");
    setTimeout(() => closeSession(session), 200);
  } finally { session.applying = false; }
}

function openStudio(node) {
  const target = resolveH3Node(node), context = inspectContext(target);
  if (!/^https?:$/.test(location.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(location.hostname)) {
    throw new BridgeError("This local bridge supports a Comfy frontend opened on a loopback address.");
  }
  if (active) closeSession(active);
  const id = crypto.randomUUID();
  const overlay = element("div"), panel = element("section"), header = element("header"), status = element("span", "Connecting to Studio…");
  overlay.style.cssText = "position:fixed;inset:0;background:#030810b8;z-index:999990;display:flex;align-items:center;justify-content:center;padding:12px;box-sizing:border-box";
  panel.style.cssText = "position:relative;width:min(1600px,100%);height:calc(100vh - 24px);background:#10151d;border:1px solid #43516a;border-radius:12px;overflow:hidden;box-shadow:0 20px 90px #000a;display:flex;flex-direction:column";
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true"); panel.setAttribute("aria-label", "H3 Prompt Studio");
  header.style.cssText = "display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:12px 16px;background:#141e2b;color:#eef5fb;font:13px/1.4 system-ui;flex-shrink:0";
  const title = element("strong", `${context.mode.toUpperCase()} · H3 node ${target.id}`);
  status.style.cssText = "flex:1;color:#afc1d5;min-width:150px";
  const label = element("label"), checkbox = element("input"), close = element("button", "Close Studio");
  checkbox.type = "checkbox"; checkbox.setAttribute("aria-label", "Guard Comfy runs by releasing the AI model first");
  label.style.cssText = "display:flex;gap:7px;align-items:center;cursor:pointer";
  label.title = "Opt-in protection for normal Run actions in this Comfy window. Requires Studio to remain running; does not guard direct HTTP submissions or other instances.";
  label.append(checkbox, document.createTextNode("Guard Comfy runs"));
  close.style.cssText = "padding:7px 11px;background:#26394d;border:1px solid #60748d;border-radius:7px;color:white;cursor:pointer";
  const frame = element("iframe");
  frame.title = "H3 Prompt Studio editor";
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-popups-to-escape-sandbox");
  frame.setAttribute("allow", "clipboard-read; clipboard-write");
  frame.referrerPolicy = "no-referrer";
  frame.style.cssText = "border:0;background:#10151d;width:100%;flex:1;min-height:0";
  const url = new URL(STUDIO_ORIGIN);
  url.search = new URLSearchParams({ embed: "1", bridge_session: id, comfy_origin: location.origin }).toString();
  frame.src = url.toString();
  header.append(title, status, label, close); panel.append(header, frame); overlay.append(panel);
  document.body.append(overlay);
  const session = { id, overlay, panel, frame, frameWindow: frame.contentWindow, checkbox, status, snapshot: { node: target, graph: target.graph, rootGraph: app.graph, context }, payloadPromise: null, imported: false, applying: false };
  active = session; controls.add(checkbox); refreshGuardControls();
  close.onclick = () => closeSession(session);
  checkbox.onchange = () => {
    try {
      if (checkbox.checked) guard.enable(); else guard.disable();
      notice(guard.status().enabled ? "GPU guard enabled for normal Comfy Run actions. Keep Studio running; use the H3 Prompt Studio menu to disable it." : "GPU guard disabled. Prepare H3 in Studio before running Comfy.");
    } catch (error) { notice(error.message, true); refreshGuardControls(); }
  };
  session.connectionTimer = setTimeout(() => {
    if (active === session && !session.imported) status.textContent = "Still connecting. Start H3 Prompt Studio on port 8766, then reopen this panel.";
  }, 12000);
  session.expiryTimer = setTimeout(() => {
    if (active === session) { notice("The Studio bridge session expired. Reopen it from the H3 node to apply a prompt.", true); closeSession(session); }
  }, 2 * 60 * 60 * 1000);
}

async function receive(event) {
  const session = active;
  if (validMessage(event, session, "h3studio.ready")) {
    try {
      if (validResourceToken(event.data.resource_token)) guard.pair(event.data.resource_token);
      session.status.textContent = "Reading the selected image inputs…";
      session.payloadPromise ??= buildPayload(session);
      const payload = await session.payloadPromise;
      if (active !== session) return;
      reply(session, "h3studio.import", { payload });
      session.imported = true;
      clearTimeout(session.connectionTimer);
      session.status.textContent = "Edit in Studio, then use Return to Comfy. No generation is queued.";
    } catch (error) {
      session.status.textContent = error.message;
      reply(session, "h3studio.error", { code: error.code || "import_failed", message: error.message });
    }
  } else if (validMessage(event, session, "h3studio.prompt")) {
    try { await acceptPrompt(session, event.data.prompt); }
    catch (error) { notice(error.message, true); reply(session, "h3studio.error", { code: error.code || "apply_failed", message: error.message }); }
  } else if (validMessage(event, session, "h3studio.transfer")) {
    try {
      if (!session.imported) throw new BridgeError("Import the selected H3 inputs before opening a Studio workflow.", "not_imported");
      await receivePreparedWorkflow(event.data.ticket, session);
    } catch (error) {
      notice(error.message, true);
      reply(session, "h3studio.error", { code: error.code || "transfer_failed", message: error.message });
    }
  } else if (validMessage(event, session, "h3studio.close")) closeSession(session);
}

function runSafely(callback) {
  try { callback(); } catch (error) { notice(error.message, true); }
}

app.registerExtension({
  name: "H3PromptStudio.Bridge",
  setup() { window.addEventListener("message", receive); receiveTransferFromUrl(); },
  async beforeConfigureGraph(data) {
    try { await prepareWorkflowImageChoices(data, location.origin); }
    catch (error) {
      // Keep the saved graph editable with Comfy's normal missing-media errors;
      // never register an unavailable or changed image as a verified choice.
      finishWorkflowImageChoices();
      notice(error.message, true);
    }
  },
  loadedGraphNode(node) { registerLoadedTransferImage(node); installStudioContinuationButton(node, { app, api, notify: notice }); },
  afterConfigureGraph() {
    finishWorkflowImageChoices();
    for (const node of app.graph?._nodes || []) installStudioContinuationButton(node, { app, api, notify: notice });
  },
  getNodeMenuItems(node) {
    try {
      const h3 = resolveH3Node(node);
      return [{ content: "Open in H3 Prompt Studio", callback: () => runSafely(() => openStudio(h3)) }];
    } catch { return []; }
  },
  commands: [
    { id: "H3PromptStudio.Open", label: "Open selected H3 node in Prompt Studio", function: () => runSafely(() => openStudio(selectedH3())) },
    { id: "H3PromptStudio.Guard.Enable", label: "Enable paired GPU guard", active: () => guard.status().enabled, function: () => runSafely(() => { guard.enable(); notice("GPU guard enabled for this Comfy frontend."); }) },
    { id: "H3PromptStudio.Guard.Disable", label: "Disable GPU guard", function: () => { guard.disable(); notice("GPU guard disabled. Prepare H3 in Studio before running Comfy."); } },
    { id: "H3PromptStudio.Guard.Status", label: "Show GPU guard status", function: () => notice(guard.status().enabled ? "GPU guard is enabled for normal Run actions in this frontend. Direct HTTP clients and other instances are outside its coverage." : "GPU guard is disabled. Open Studio from an H3 node to pair and enable it.") },
    { id: "H3PromptStudio.Transfer.Retry", label: "Retry Studio transfer", function: () => {
      if (!pendingTransfer) { notice("Send your project from Studio to open a new ComfyUI workflow."); return; }
      receivePreparedWorkflow(pendingTransfer).catch(error => notice(error.message, true));
    } },
  ],
  menuCommands: [{ path: ["H3 Prompt Studio"], commands: ["H3PromptStudio.Open", "H3PromptStudio.Transfer.Retry", "H3PromptStudio.Guard.Enable", "H3PromptStudio.Guard.Disable", "H3PromptStudio.Guard.Status"] }],
});
