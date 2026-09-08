import { afterEach, expect, test, vi } from 'vitest';
import { api, ApiError, setToken } from './api';
afterEach(()=>vi.unstubAllGlobals());
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
