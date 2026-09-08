import { afterEach, describe, expect, it, vi } from 'vitest';
import { createBridge, parseBridgeContext, validateBridgePayload } from './bridge';

const SESSION = 'test-session-1234567890';
const COMFY = 'http://127.0.0.1:8010';
const TOKEN = 'release-model-only-token-12345678';
function fakeWindow({ embedded = true, origin = COMFY, session = SESSION, embedFlag = embedded ? '1' : '' } = {}) {
  const listeners = new Map<string, Set<EventListener>>();
  const target = { postMessage: vi.fn() };
  const win = {
    location: { search: `?bridge_session=${encodeURIComponent(session)}&comfy_origin=${encodeURIComponent(origin)}&embed=${embedFlag}` },
    parent: null as unknown, opener: null as unknown,
    addEventListener(type: string, listener: EventListener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type)!.add(listener); },
    removeEventListener(type: string, listener: EventListener) { listeners.get(type)?.delete(listener); },
  };
  win.parent = embedded ? target : win; win.opener = embedded ? null : target;
  return {
    win: win as unknown as Window, target,
    message(data: unknown, origin = COMFY, source: unknown = target) { for (const fn of listeners.get('message') || []) fn({ data, origin, source } as unknown as Event); },
  };
}
function payload() {
  return {
    mode: 'i2va', duration: 124 / 24, prompt: 'Exact existing user prompt', node_id: '10',
    assets: [{ name: 'first.png', role: 'first_frame', semantic_role: 'other', data_url: 'data:image/png;base64,AAAA' }],
    source: { node_id: '10', prompt: 'Exact existing user prompt', requested_length: 124, aligned_length: 124 },
  };
}
afterEach(() => vi.useRealTimers());

describe('local bridge context', () => {
  it('accepts the standard local ComfyUI port with the same session and window checks', () => {
    for (const origin of ['http://127.0.0.1:8188', 'http://localhost:8188']) {
      const f = fakeWindow({ origin });
      expect(parseBridgeContext(f.win)?.origin).toBe(origin);
      expect(parseBridgeContext(fakeWindow({ origin, session: 'short' }).win)).toBeNull();
    }
  });
  it('uses exact trusted parent or opener only with matching embed flag', () => {
    for (const embedded of [true, false]) {
      const f = fakeWindow({ embedded }); const context = parseBridgeContext(f.win);
      expect(context?.targetWindow).toBe(f.target); expect(context?.origin).toBe(COMFY); expect(context?.embedded).toBe(embedded);
    }
    expect(parseBridgeContext(fakeWindow({ embedFlag: '0' }).win)).toBeNull();
    expect(parseBridgeContext(fakeWindow({ embedded: false, embedFlag: '1' }).win)).toBeNull();
  });
  it('rejects arbitrary origins, origin lookalikes and weak sessions', () => {
    for (const origin of ['https://example.com', 'http://127.0.0.1:9999', 'http://127.0.0.1:8010/path', 'http://127.0.0.1:8010.evil.com', 'http://localhost:8010@evil.test']) expect(parseBridgeContext(fakeWindow({ origin }).win)).toBeNull();
    expect(parseBridgeContext(fakeWindow({ session: 'short' }).win)).toBeNull();
  });
});

describe('bridge messages', () => {
  it('sends scoped ready, imports once, and only returns a prompt after an explicit call', async () => {
    vi.useFakeTimers(); const f = fakeWindow(), onImport = vi.fn(), onApplied = vi.fn();
    const bridge = createBridge({ window: f.win, resourceToken: TOKEN, onImport, onApplied });
    expect(f.target.postMessage).toHaveBeenCalledWith({ type: 'h3studio.ready', session: SESSION, resource_token: TOKEN }, COMFY);
    f.message({ type: 'h3studio.import', session: SESSION, payload: payload() });
    f.message({ type: 'h3studio.import', session: SESSION, payload: payload() });
    await Promise.resolve();
    expect(onImport).toHaveBeenCalledTimes(1);
    expect(f.target.postMessage.mock.calls.some(([data]) => data.type === 'h3studio.prompt')).toBe(false);
    expect(bridge.sendPrompt('Compiled exact dialogue')).toBe(true);
    expect(f.target.postMessage).toHaveBeenLastCalledWith({ type: 'h3studio.prompt', session: SESSION, prompt: 'Compiled exact dialogue' }, COMFY);
    f.message({ type: 'h3studio.applied', session: SESSION, node_id: 'unrelated-node' });
    expect(onApplied).not.toHaveBeenCalled();
    f.message({ type: 'h3studio.applied', session: SESSION, node_id: '10' });
    expect(onApplied).toHaveBeenCalledWith({ node_id: '10' });
    const calls = f.target.postMessage.mock.calls.length; vi.advanceTimersByTime(5000); expect(f.target.postMessage).toHaveBeenCalledTimes(calls);
    bridge.dispose();
  });
  it('waits for the app import callback to finish before allowing a return', async () => {
    vi.useFakeTimers(); const f = fakeWindow(); let complete: (() => void) | undefined;
    const bridge = createBridge({ window: f.win, onImport: () => new Promise<void>(resolve => { complete = resolve; }) });
    f.message({ type: 'h3studio.import', session: SESSION, payload: payload() });
    expect(bridge.sendPrompt('Too early')).toBe(false);
    complete!(); await Promise.resolve();
    expect(bridge.sendPrompt('Import has finished')).toBe(true); bridge.dispose();
  });
  it('ignores wrong origin, source and session without touching the project', () => {
    vi.useFakeTimers(); const f = fakeWindow(), onImport = vi.fn();
    const bridge = createBridge({ window: f.win, onImport }); const data = { type: 'h3studio.import', session: SESSION, payload: payload() };
    f.message(data, 'https://example.com'); f.message(data, COMFY, {}); f.message({ ...data, session: 'wrong-session' });
    expect(onImport).not.toHaveBeenCalled(); expect(bridge.sendPrompt('Not connected yet')).toBe(false); bridge.dispose();
  });
  it('rejects malformed media and mismatched source provenance', () => {
    const p = payload(); p.assets[0].data_url = 'https://example.com/image.png'; expect(() => validateBridgePayload(p)).toThrow(/inline PNG/);
    const other = payload(); other.source.prompt = 'A different prompt'; expect(() => validateBridgePayload(other)).toThrow(/provenance/);
    const wrongRole = payload(); wrongRole.assets[0].role = 'reference_image'; expect(() => validateBridgePayload(wrongRole)).toThrow(/first frame/);
  });
  it('keeps nine reference images ordered and does not invent conditioning from library content', () => {
    const p = payload(); p.mode = 'ref2va'; p.assets = Array.from({ length: 9 }, (_, i) => ({ ...p.assets[0], name: `${i}.png`, role: 'reference_image' }));
    expect(validateBridgePayload(p).assets.map(asset => asset.name)).toEqual(Array.from({ length: 9 }, (_, i) => `${i}.png`));
    p.assets.push({ ...p.assets[0] }); expect(() => validateBridgePayload(p)).toThrow(/at most nine/);
  });
  it('stops listeners and handshake retries on disposal', () => {
    vi.useFakeTimers(); const f = fakeWindow(), onImport = vi.fn(); const bridge = createBridge({ window: f.win, onImport });
    bridge.dispose(); const calls = f.target.postMessage.mock.calls.length;
    vi.advanceTimersByTime(60000); f.message({ type: 'h3studio.import', session: SESSION, payload: payload() });
    expect(onImport).not.toHaveBeenCalled(); expect(f.target.postMessage).toHaveBeenCalledTimes(calls); expect(bridge.sendPrompt('No longer connected')).toBe(false);
  });
});
