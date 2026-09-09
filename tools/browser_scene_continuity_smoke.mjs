/** Built-app smoke with isolated synthetic projects and a mocked API. No model or GPU calls. */
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const contract = {
  actors: [{ subject_id: 'mira', activity: 'act', start: 'Seated left', action: 'Lifts the cup with her right hand', end: 'Holding the cup near her chest' },
    { subject_id: 'nora', activity: 'hold', start: 'Seated right', action: 'Watches quietly; hands stay on knees', end: 'Still seated right' }],
  objects: [{ entity_id: 'cup', name: 'Red cup', description: 'Red ceramic with one white stripe', count: 1, start: 'On the table', end: 'In Mira’s right hand' }],
  environment: 'Warm café; table and doorway fixed in place', background_activity: 'No other foreground movement',
};
let project = {
  schema_version: 1, id: 'scene-smoke-project', title: 'One cup, two people', mode: 't2va', duration: 10,
  aspect_ratio: '16:9', profile: 'director', authoring_mode: 'full', story: { text: 'Mira lifts the cup while Nora stays seated.', locked: true },
  style: {}, assets: [], subjects: [{ id: 'mira', name: 'Mira', description: 'Left actor', asset_ids: [] }, { id: 'nora', name: 'Nora', description: 'Right actor', asset_ids: [] }],
  shots: [{ id: 'shot', duration: 10, action: 'Mira lifts the cup while Nora stays seated.', setting: 'Café', camera: {}, performance: '', final_state: 'Mira holds the cup',
    visible_subject_ids: ['mira', 'nora'], offscreen_subject_ids: [], dialogue: [], sound: '', transition: 'continuous',
    scene_contract: structuredClone(contract), scene_contract_source: 'generated', director_locks: ['camera.framing'] }],
  soundscape: '', music: '', custom_instructions: '', simple: { directed: true },
};
const requests = [], errors = [], forbidden = [];
const server = createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const filename = path.resolve(root, 'dist', pathname === '/' ? 'index.html' : decodeURIComponent(pathname).replace(/^\/+/, ''));
    if (!filename.startsWith(path.join(root, 'dist') + path.sep)) throw new Error('Invalid path');
    response.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    response.end(await readFile(filename));
  } catch { response.statusCode = 404; response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { localStorage.setItem('h3-workspace-mode', 'studio'); localStorage.setItem('h3-studio-view', 'simple'); });
  await page.route('**/api/**', async route => {
    const request = route.request(), pathname = new URL(request.url()).pathname.replace(/^\/api/, '');
    requests.push({ path: pathname, method: request.method() });
    const json = value => route.fulfill({ json: structuredClone(value) });
    if (!['GET', 'HEAD'].includes(request.method()) && !['/projects', '/compile'].includes(pathname)) {
      forbidden.push(`${request.method()} ${pathname}`);
      return route.fulfill({ status: 503, json: { detail: 'Only local project saving and compilation are allowed in this smoke test.' } });
    }
    if (pathname === '/bootstrap') return json({ token: 'a'.repeat(43), project, projects: [], settings: { model: 'test', persona: 'universal' }, personas: [] });
    if (pathname === '/projects') {
      if (request.method() === 'POST') project = request.postDataJSON();
      return json(request.method() === 'POST' ? project : []);
    }
    if (pathname === '/compile') return json({ valid: true, prompt: 'Mock preview', issues: [], references: [], timeline: [] });
    if (pathname === '/connections') return json({ lm: { online: true, models: [] }, comfy: { online: false } });
    if (pathname === '/video/runs') return json({ runs: [] });
    if (pathname === '/stories') return json({ stories: [] });
    return json({});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.getByRole('tab', { name: 'Story & Dialogue', exact: true }).click();
  await page.getByText('Scenes, camera & spoken words', { exact: false }).click();
  const continuity = page.locator('.scene-continuity').first();
  await expect(continuity).not.toHaveAttribute('open');
  await page.waitForTimeout(1400); // Allow normal startup autosave/preview to settle before measuring panel access.
  const beforeOpen = requests.length;
  await continuity.locator('summary').click();
  await expect(page.getByLabel('Scene 1 Nora activity', { exact: true })).toHaveValue('hold');
  await page.waitForTimeout(900);
  expect(requests.slice(beforeOpen).filter(item => !['GET', 'HEAD'].includes(item.method))).toEqual([]);
  expect(project.shots[0].scene_contract).toEqual(contract);
  expect(project.shots[0].scene_contract_source).toBe('generated');
  expect(project.shots[0].director_locks).not.toContain('scene_contract');

  await page.getByLabel('Scene 1 Mira starts', { exact: true }).fill('Standing left with both feet planted');
  await page.getByLabel('Scene 1 Mira activity', { exact: true }).selectOption('hold');
  await page.getByLabel('Scene 1 Mira allowed small movement', { exact: true }).fill('Turns her head; feet remain planted');
  await page.getByLabel('Scene 1 Object 1 count', { exact: true }).fill('2');
  await expect.poll(() => project.shots[0].scene_contract?.objects?.[0]?.count).toBe(2);
  expect(project.shots[0].scene_contract_source).toBeUndefined();
  expect(project.shots[0].director_locks).toEqual(['camera.framing', 'scene_contract']);
  expect(project.shots[0].scene_contract.actors[1]).toEqual(contract.actors[1]);
  expect(project.shots[0].scene_contract.objects[0].entity_id).toBe('cup');

  await page.getByRole('button', { name: 'Scene 1 add object continuity', exact: true }).click();
  await page.getByLabel('Scene 1 Object 2 name', { exact: true }).fill('Blue plate');
  await page.getByLabel('Scene 1 Object 2 appearance', { exact: true }).fill('One blue plate, unchanged size and glaze');
  await expect.poll(() => project.shots[0].scene_contract?.objects?.[1]?.name).toBe('Blue plate');
  const newObjectId = project.shots[0].scene_contract.objects[1].entity_id;
  expect(newObjectId).not.toBe('cup');
  const editedContract = structuredClone(project.shots[0].scene_contract);
  await page.reload();
  await page.getByRole('tab', { name: 'Story & Dialogue', exact: true }).click();
  await page.getByText('Scenes, camera & spoken words', { exact: false }).click();
  await page.locator('.scene-continuity').first().locator('summary').click();
  await expect(page.getByLabel('Scene 1 Object 2 name', { exact: true })).toHaveValue('Blue plate');
  expect(project.shots[0].scene_contract).toEqual(editedContract);
  await page.getByText('More scene options', { exact: false }).click();
  await page.getByLabel('Scene 1 Nora visibility', { exact: true }).selectOption('offscreen');
  await expect(page.getByLabel('Scene 1 Nora activity', { exact: true })).toHaveCount(0);
  await expect.poll(() => project.shots[0].scene_contract?.actors?.length).toBe(1);
  await page.getByRole('button', { name: 'Duplicate scene 1', exact: true }).click();
  await expect.poll(() => project.shots.length).toBe(2);
  expect(project.shots[1].scene_contract).toBeUndefined();
  expect(project.shots[1].scene_contract_source).toBeUndefined();
  expect(project.shots[1].director_locks).not.toContain('scene_contract');
  expect(project.shots[0].scene_contract.objects[1].entity_id).toBe(newObjectId);

  await mkdir(path.join(root, 'test-results'), { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(root, 'test-results/frontend-scene-continuity-smoke.png'), fullPage: true });
  await page.getByRole('button', { name: 'Advanced', exact: true }).click();
  await page.getByRole('button', { name: 'Continuity', exact: true }).click();
  await expect(page.locator('.scene-continuity')).toBeVisible();
  await page.locator('.scene-continuity summary').click();
  await expect(page.getByLabel('Scene 1 Object 2 name', { exact: true })).toHaveValue('Blue plate');
  expect(forbidden).toEqual([]);
  expect(errors).toEqual([]);
  console.log(JSON.stringify({ passed: true, panelOpenMutations: 0, generatedDraftPreserved: true, authoredDirectionsRoundTrip: true,
    copyDoesNotReplay: true, advancedEditor: true, modelOrVideoRequests: forbidden, pageErrors: errors,
    screenshot: 'test-results/frontend-scene-continuity-smoke.png' }, null, 2));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
