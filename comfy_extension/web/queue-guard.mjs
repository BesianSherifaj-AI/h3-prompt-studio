import { STUDIO_ORIGIN, BridgeError } from "./bridge-core.mjs";

export function validResourceToken(token) {
  return typeof token === "string" && /^[A-Za-z0-9_-]{16,256}$/.test(token);
}

export function createQueueGuard(api, { fetchImpl = fetch, timeoutMs = 60000, onChange = () => {} } = {}) {
  let enabled = false, token = null, original = null, wrapper = null, pending = null, installation = null;
  const status = () => ({ enabled, paired: validResourceToken(token), attached: api.queuePrompt === wrapper });

  async function prepare() {
    if (pending) return pending;
    const activeToken = token;
    pending = (async () => {
      const controller = new AbortController();
      let timer;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new BridgeError("Studio did not release the AI model within 60 seconds. Nothing was queued. Open Studio and prepare H3, or explicitly disable the GPU guard.", "prepare_timeout"));
        }, timeoutMs);
      });
      try {
        await Promise.race([
          (async () => {
            let response;
            try {
              response = await fetchImpl(`${STUDIO_ORIGIN}/api/gpu/prepare-h3`, {
                method: "POST", credentials: "omit", cache: "no-store", signal: controller.signal,
                headers: { "Content-Type": "application/json", "X-H3-Bridge": activeToken }, body: "{}",
              });
            } catch (error) {
              if (controller.signal.aborted) throw new BridgeError("Studio preparation timed out. Nothing was queued.", "prepare_timeout");
              throw new BridgeError("Studio is unavailable, so the GPU guard blocked this run. Start Studio or explicitly disable the guard.", "prepare_unavailable");
            }
            if (response.status === 403) throw new BridgeError("Studio pairing expired. Reopen Studio from an H3 node to pair again. Nothing was queued.", "pairing_expired");
            if (!response.ok) throw new BridgeError(`Studio could not prepare H3 (${response.status}). Nothing was queued.`, "prepare_failed");
            let result;
            try { result = await response.json(); } catch { throw new BridgeError("Studio returned an invalid readiness result. Nothing was queued.", "prepare_failed"); }
            if (result?.ready !== true) throw new BridgeError("Studio has not confirmed that H3 can use the GPU. Nothing was queued.", "prepare_not_ready");
          })(), timeout,
        ]);
      } finally { clearTimeout(timer); }
    })();
    try { await pending; } finally { pending = null; }
  }

  function pair(value) {
    if (!validResourceToken(value)) throw new BridgeError("Studio did not provide a valid scoped GPU-guard token.");
    token = value;
    onChange(status());
  }

  function enable() {
    if (!validResourceToken(token)) throw new BridgeError("Open Studio from an H3 node before enabling the GPU guard.");
    if (enabled) return;
    if (typeof api.queuePrompt !== "function") throw new BridgeError("This Comfy frontend has no compatible queue API.");
    original = api.queuePrompt;
    const capturedOriginal = original;
    const ownInstallation = {};
    installation = ownInstallation;
    wrapper = async function (...args) {
      if (enabled && installation === ownInstallation) await prepare();
      return await Reflect.apply(capturedOriginal, this, args);
    };
    api.queuePrompt = wrapper;
    enabled = true;
    onChange(status());
  }

  function disable() {
    enabled = false;
    // If another extension wrapped us later, leave its chain intact. Our function
    // becomes a pass-through; never discard another extension's replacement.
    if (api.queuePrompt === wrapper) api.queuePrompt = original;
    onChange(status());
  }

  return { status, pair, enable, disable };
}
