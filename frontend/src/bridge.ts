/** Local, explicit Comfy ↔ Studio messages. This module never queues or mutates a project. */
export const COMFY_ORIGINS = new Set([
  'http://127.0.0.1:8188', 'http://localhost:8188',
  'http://127.0.0.1:8000', 'http://127.0.0.1:8010',
  'http://localhost:8000', 'http://localhost:8010',
]);

export type BridgeMode = 'ref2va' | 'fl2va' | 'i2va' | 'l2va' | 't2va';
export type BridgeAsset = {
  name: string;
  role: 'reference_image' | 'first_frame' | 'last_frame';
  semantic_role: string;
  data_url: string;
  input_name?: string;
  source_node_id?: string;
  reference_index?: number;
  reference_token?: string;
};
export type BridgePayload = {
  mode: BridgeMode;
  duration: number;
  prompt: string;
  node_id: string;
  assets: BridgeAsset[];
  source: { node_id: string; prompt: string; [key: string]: unknown };
};
export type BridgeContext = {
  session: string;
  origin: string;
  targetWindow: Window;
  embedded: boolean;
};
export type BridgeErrorMessage = { code: string; message: string };
export type BridgeOptions = {
  onImport: (payload: BridgePayload) => void | Promise<void>;
  onApplied?: (message: { node_id: string }) => void;
  onTransferApplied?: (message: {ticket:string;title:string}) => void;
  onError?: (message: BridgeErrorMessage) => void;
  resourceToken?: string;
  /** Dependency injection for CPU tests; app code normally omits this. */
  window?: Window;
};
export type BridgeConnection = {
  context: BridgeContext | null;
  sendPrompt: (prompt: string) => boolean;
  sendTransfer: (ticket:string) => boolean;
  close: () => void;
  dispose: () => void;
};

function plainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseBridgeContext(win: Window = window): BridgeContext | null {
  const params = new URLSearchParams(win.location.search);
  const session = params.get('bridge_session'), origin = params.get('comfy_origin');
  if (!session || !/^[A-Za-z0-9_-]{16,128}$/.test(session) || !origin || !COMFY_ORIGINS.has(origin)) return null;
  const embedded = win.parent !== win;
  if (embedded !== (params.get('embed') === '1')) return null;
  const targetWindow = embedded ? win.parent : win.opener;
  if (!targetWindow || targetWindow === win) return null;
  return { session, origin, targetWindow, embedded };
}

/** Validate the wire payload before app callbacks see media or prompt content. */
export function validateBridgePayload(value: unknown): BridgePayload {
  if (!plainObject(value)) throw new Error('Comfy sent an invalid import object.');
  const modes = ['ref2va', 'fl2va', 'i2va', 'l2va', 't2va'];
  if (typeof value.mode !== 'string' || !modes.includes(value.mode)) throw new Error('The imported H3 mode is unsupported.');
  if (typeof value.duration !== 'number' || !Number.isFinite(value.duration) || value.duration <= 0 || value.duration > 3600) throw new Error('The imported duration is invalid.');
  if (typeof value.prompt !== 'string' || value.prompt.length > 100000) throw new Error('The imported prompt is invalid or too long.');
  if (typeof value.node_id !== 'string' || !value.node_id || value.node_id.length > 200) throw new Error('The imported H3 node identity is missing.');
  if (!Array.isArray(value.assets) || value.assets.length > 9) throw new Error('The bridge supports at most nine conditioned image inputs.');
  if (!plainObject(value.source) || value.source.node_id !== value.node_id || value.source.prompt !== value.prompt) throw new Error('The imported prompt provenance does not match the selected node.');
  let bytes = 0;
  const roles = new Set(['reference_image', 'first_frame', 'last_frame']);
  const semanticRoles = new Set(['face', 'character', 'background', 'object', 'palette', 'style', 'wardrobe', 'pose', 'other']);
  const assets: BridgeAsset[] = value.assets.map((item: unknown) => {
    if (!plainObject(item) || typeof item.name !== 'string' || !item.name || item.name.length > 1024 || typeof item.role !== 'string' || !roles.has(item.role)) throw new Error('An imported image has an invalid name or conditioning role.');
    if (typeof item.data_url !== 'string' || item.data_url.length > 12000000) throw new Error('An imported image exceeds the bridge size limit.');
    const match = item.data_url.match(/^data:image\/(?:png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$/);
    if (!match || match[1].length % 4 !== 0) throw new Error('The bridge accepts only inline PNG, JPEG and WebP image data.');
    const imageBytes = match[1].length * 3 / 4 - (match[1].endsWith('==') ? 2 : match[1].endsWith('=') ? 1 : 0);
    bytes += imageBytes;
    if (imageBytes <= 0 || imageBytes > 8 * 1024 * 1024 || bytes > 32 * 1024 * 1024) throw new Error('Imported images exceed the 8 MiB individual or 32 MiB total limit.');
    const asset: BridgeAsset = {
      name: item.name, role: item.role as BridgeAsset['role'], data_url: item.data_url,
      semantic_role: typeof item.semantic_role === 'string' && semanticRoles.has(item.semantic_role) ? item.semantic_role : 'other',
    };
    if (typeof item.input_name === 'string') asset.input_name = item.input_name;
    if (typeof item.source_node_id === 'string') asset.source_node_id = item.source_node_id;
    if (typeof item.reference_index === 'number' && Number.isInteger(item.reference_index)) asset.reference_index = item.reference_index;
    if (typeof item.reference_token === 'string') asset.reference_token = item.reference_token;
    return asset;
  });
  const byRole = (role: BridgeAsset['role']) => assets.filter(asset => asset.role === role).length;
  if (value.mode === 'ref2va' && assets.some(asset => asset.role !== 'reference_image')) throw new Error('Ref2VA imports must retain reference-image roles.');
  if (value.mode === 'fl2va' && (assets.length !== 2 || byRole('first_frame') !== 1 || byRole('last_frame') !== 1)) throw new Error('FL2VA imports require one first frame and one last frame.');
  if (value.mode === 'i2va' && (assets.length !== 1 || byRole('first_frame') !== 1)) throw new Error('I2VA imports require one first frame.');
  if (value.mode === 'l2va' && (assets.length !== 1 || byRole('last_frame') !== 1)) throw new Error('L2VA imports require one last frame.');
  if (value.mode === 't2va' && assets.length !== 0) throw new Error('T2VA imports cannot contain conditioned images.');
  return {
    mode: value.mode as BridgeMode, duration: value.duration, prompt: value.prompt,
    node_id: value.node_id, assets,
    source: { ...value.source, node_id: value.node_id, prompt: value.prompt },
  };
}

export function createBridge(options: BridgeOptions): BridgeConnection {
  const win = options.window ?? window;
  const context = parseBridgeContext(win);
  let disposed = false, receivedImport = false, importReady = false, importedNodeId = '', attempts = 0;
  let retry: ReturnType<typeof setInterval> | undefined;

  const report = (code: string, message: string) => options.onError?.({ code, message });
  const post = (type: string, extra: Record<string, unknown> = {}) => {
    if (!context || disposed) return false;
    context.targetWindow.postMessage({ type, session: context.session, ...extra }, context.origin);
    return true;
  };
  const ready = () => {
    if (disposed || receivedImport) return;
    if (attempts++ >= 30) {
      clearInterval(retry);
      report('connection_timeout', 'Comfy did not send its selected inputs. Reopen Studio from the intended H3 node.');
      return;
    }
    const token = options.resourceToken;
    post('h3studio.ready', typeof token === 'string' && /^[A-Za-z0-9_-]{16,256}$/.test(token) ? { resource_token: token } : {});
  };

  const receive = (event: MessageEvent) => {
    if (disposed || !context || event.origin !== context.origin || event.source !== context.targetWindow || !plainObject(event.data) || event.data.session !== context.session) return;
    if (event.data.type === 'h3studio.import' && !receivedImport) {
      try {
        const payload = validateBridgePayload(event.data.payload);
        receivedImport = true; clearInterval(retry);
        importedNodeId = payload.node_id;
        Promise.resolve(options.onImport(payload))
          .then(() => { if (!disposed) importReady = true; })
          .catch(() => report('import_failed', 'The selected Comfy inputs could not be imported. Reopen Studio to retry.'));
      } catch (error) {
        clearInterval(retry);
        report('invalid_import', error instanceof Error ? error.message : 'The selected Comfy inputs are invalid.');
      }
    } else if (event.data.type === 'h3studio.applied') {
      if (importReady && event.data.node_id === importedNodeId) options.onApplied?.({ node_id: importedNodeId });
    } else if (event.data.type === 'h3studio.transfer_applied') {
      if (typeof event.data.ticket==='string' && /^[A-Za-z0-9_-]{43}$/.test(event.data.ticket)) options.onTransferApplied?.({ticket:event.data.ticket,title:String(event.data.title||'H3 Prompt Studio')});
    } else if (event.data.type === 'h3studio.error') {
      report(typeof event.data.code === 'string' ? event.data.code : 'comfy_error', typeof event.data.message === 'string' ? event.data.message : 'Comfy could not complete this bridge action.');
    }
  };

  function dispose() {
    if (disposed) return;
    disposed = true; clearInterval(retry);
    win.removeEventListener('message', receive);
    win.removeEventListener('pagehide', dispose);
  }

  if (context) {
    win.addEventListener('message', receive);
    win.addEventListener('pagehide', dispose);
    ready();
    retry = setInterval(ready, 1000);
  }
  return {
    context,
    sendTransfer(ticket:string) {
      if(!/^[A-Za-z0-9_-]{43}$/.test(ticket))return false;
      return post('h3studio.transfer',{ticket});
    },
    sendPrompt(prompt: string) {
      if (!context || disposed || !importReady) {
        report('not_connected', 'Open Studio from an H3 prompt node and import its inputs before returning a prompt.');
        return false;
      }
      if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 100000) {
        report('invalid_prompt', 'Compile a nonempty valid prompt before returning it to Comfy.');
        return false;
      }
      return post('h3studio.prompt', { prompt });
    },
    close() { post('h3studio.close'); dispose(); },
    dispose,
  };
}
