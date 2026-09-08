import test from "node:test";
import assert from "node:assert/strict";
import { createQueueGuard } from "../web/queue-guard.mjs";
const TOKEN = "scoped-release-token-test-123456";
const ok = () => ({ ok: true, json: async () => ({ ready: true }) });
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test("pairing alone does not patch or call the queue API", () => {
  const api = { queuePrompt() {} }, original = api.queuePrompt;
  const guard = createQueueGuard(api, { fetchImpl: () => assert.fail("No implicit request") });
  guard.pair(TOKEN);
  assert.equal(api.queuePrompt, original);
  assert.equal(guard.status().enabled, false);
});

test("explicit guard waits for LM release, then preserves receiver, arguments and return", async () => {
  let release, forwarded = false, request;
  const payload = { graph: "unchanged" }, expected = { prompt_id: "exact-result" }, receiver = {};
  const api = { async queuePrompt(...args) { forwarded = true; assert.equal(this, receiver); assert.deepEqual(args, [7, payload, { front: true }]); return expected; } };
  const guard = createQueueGuard(api, { fetchImpl: async (url, options) => { request = { url, options }; await new Promise(resolve => { release = resolve; }); return ok(); } });
  guard.pair(TOKEN); guard.enable();
  const result = api.queuePrompt.call(receiver, 7, payload, { front: true });
  await tick(); assert.equal(forwarded, false);
  assert.equal(request.url, "http://127.0.0.1:8766/api/gpu/prepare-h3");
  assert.equal(request.options.headers["X-H3-Bridge"], TOKEN);
  assert.equal(request.options.body, "{}");
  release(); assert.equal(await result, expected); assert.equal(forwarded, true);
});

for (const [name, fetchImpl] of [
  ["HTTP failure", async () => ({ ok: false, status: 409 })],
  ["expired pairing", async () => ({ ok: false, status: 403 })],
  ["missing readiness", async () => ({ ok: true, json: async () => ({ released: false }) })],
  ["invalid JSON", async () => ({ ok: true, json: async () => { throw new Error("bad JSON"); } })],
  ["network failure", async () => { throw new Error("offline"); }],
]) test(`${name} blocks queue forwarding`, async () => {
  let count = 0; const api = { queuePrompt() { count++; } };
  const guard = createQueueGuard(api, { fetchImpl }); guard.pair(TOKEN); guard.enable();
  await assert.rejects(api.queuePrompt()); assert.equal(count, 0);
});

test("timeout blocks forwarding even if fetch ignores abort", async () => {
  let count = 0; const api = { queuePrompt() { count++; } };
  const guard = createQueueGuard(api, { fetchImpl: () => new Promise(() => {}), timeoutMs: 10 });
  guard.pair(TOKEN); guard.enable();
  await assert.rejects(api.queuePrompt(), /within 60 seconds/); assert.equal(count, 0);
});

test("original queue errors propagate unchanged after successful preparation", async () => {
  const expected = new Error("original queue validation error");
  const api = { queuePrompt() { throw expected; } };
  const guard = createQueueGuard(api, { fetchImpl: async () => ok() }); guard.pair(TOKEN); guard.enable();
  await assert.rejects(api.queuePrompt(), error => error === expected);
});

test("concurrent calls share preparation, but all queue arguments/results survive", async () => {
  let release, requests = 0; const forwarded = [];
  const api = { queuePrompt(value) { forwarded.push(value); return value + 1; } };
  const guard = createQueueGuard(api, { fetchImpl: async () => { requests++; await new Promise(resolve => { release = resolve; }); return ok(); } });
  guard.pair(TOKEN); guard.enable();
  const one = api.queuePrompt(1), two = api.queuePrompt(2);
  await tick(); assert.equal(requests, 1); assert.deepEqual(forwarded, []);
  release(); assert.deepEqual(await Promise.all([one, two]), [2, 3]); assert.deepEqual(forwarded, [1, 2]);
});

test("disable restores our wrapper, preserves later wrappers and avoids double guards on re-enable", async () => {
  let requests = 0; const api = { queuePrompt: value => value }, original = api.queuePrompt;
  const guard = createQueueGuard(api, { fetchImpl: async () => { requests++; return ok(); } });
  assert.throws(() => guard.enable(), /Open Studio/);
  guard.pair(TOKEN); guard.enable(); guard.disable(); assert.equal(api.queuePrompt, original);
  guard.enable(); const ours = api.queuePrompt;
  const other = function (...args) { return ours.apply(this, args); }; api.queuePrompt = other;
  guard.disable(); assert.equal(api.queuePrompt, other);
  assert.equal(await api.queuePrompt(4), 4); assert.equal(requests, 0);
  guard.enable(); assert.equal(await api.queuePrompt(5), 5); assert.equal(requests, 1);
});
