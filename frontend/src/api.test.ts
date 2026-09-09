import { afterEach, expect, test, vi } from 'vitest';
import { api, ApiError, ApiTimeoutError, setToken } from './api';
afterEach(()=>{ vi.unstubAllGlobals(); vi.useRealTimers(); });
test('server restart refreshes token without replacing the draft and retries only the rejected request',async()=>{
  const token='a'.repeat(43), requests:any[]=[];
  const responses=[new Response(JSON.stringify({detail:'Studio session expired. Reload this page.'}),{status:403}),new Response(JSON.stringify({token,project:{id:'another-project'}})),new Response(JSON.stringify({saved:true}))];
  vi.stubGlobal('fetch',vi.fn(async(url,init)=>{requests.push({url,init});return responses.shift()!;}));
  setToken('expired'); const draft={id:'my-unsaved-draft',story:{text:'The lantern glows.'}};
  await expect(api('/projects',draft)).resolves.toEqual({saved:true});
  expect(requests.map(x=>x.url)).toEqual(['/api/projects','/api/bootstrap','/api/projects']);
  expect(requests[2].init.body).toBe(JSON.stringify(draft));
  expect(requests[2].init.headers['X-H3-Token']).toBe(token);
});
test('other permission errors are not retried',async()=>{
  const fetcher=vi.fn(async()=>new Response(JSON.stringify({detail:'This origin is not connected to Studio.'}),{status:403}));
  vi.stubGlobal('fetch',fetcher);
  await expect(api('/projects',{})).rejects.toThrow('This origin');
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test('rejected render preserves status so a corrected request can use a fresh ID',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({detail:'A video is already active.'}),{status:400})));
  try { await api('/video/runs',{}); expect.unreachable(); }
  catch(error){expect(error).toBeInstanceOf(ApiError);expect(error).toMatchObject({status:400,message:'A video is already active.'});}
});

test('a stalled acknowledgement times out without resending the mutation', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn((_url, init) => new Promise<Response>((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  vi.stubGlobal('fetch', fetcher);
  const failure = api('/stories/game/turns', { request_id: 'saved-id' }, undefined, undefined, { timeoutMs: 1000 }).catch(error => error);
  await vi.advanceTimersByTimeAsync(1000);
  expect(await failure).toBeInstanceOf(ApiTimeoutError);
  expect((await failure).message).toContain('Check the saved request');
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});

test('the deadline includes a response body that never finishes', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn(async (_url, init) => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"unfinished":'));
      init.signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')));
    },
  }))));
  const failure = api('/stories/game', undefined, undefined, undefined, { timeoutMs: 1000 }).catch(error => error);
  await vi.advanceTimersByTimeAsync(1000);
  expect(await failure).toBeInstanceOf(ApiTimeoutError);
});

test('an unreadable rejection still preserves its HTTP status for recovery', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Bad request</html>', { status: 422 })));
  await expect(api('/stories/game/turns', {})).rejects.toMatchObject({ status: 422 });
});

test('a caller can cancel a read without retrying or reporting a timeout', async () => {
  const controller = new AbortController();
  const fetcher = vi.fn((_url, init) => new Promise<Response>((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  vi.stubGlobal('fetch', fetcher);
  const failure = api('/stories/game', undefined, undefined, undefined, { signal: controller.signal }).catch(error => error);
  controller.abort();
  expect(await failure).toMatchObject({ name: 'AbortError' });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
