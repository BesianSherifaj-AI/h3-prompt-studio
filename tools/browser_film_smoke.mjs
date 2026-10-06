/** Built-app film workflow with an isolated mocked API. Never loads models or starts a GPU batch. */
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const results = path.join(root, 'test-results');
const films = new Map(), batches = new Map(), frozen = new Map(), requests = [], forbidden = [], errors = [];
const editable = ['title', 'idea', 'target_minutes', 'style', 'continuity_notes', 'aspect_ratio', 'quality', 'references', 'shots'];
const clone = value => structuredClone(value);
const fingerprint = film => createHash('sha256').update(JSON.stringify(Object.fromEntries(editable.map(key => [key, film[key]])))).digest('hex');
const blankShot = index => ({ id: randomUUID(), title: `Clip ${index + 1}`, action: '', setting: '', final_state: '', sound: '', camera: { framing: 'medium', movement: 'static' }, dialogue: [] });
const video = { schema_version: 1, id: randomUUID(), workspace: 'video', title: 'Separate video', mode: 't2va', duration: 5,
  aspect_ratio: '16:9', profile: 'director', authoring_mode: 'assisted', story: { text: '', locked: true }, style: {},
  assets: [], subjects: [], shots: [{ ...blankShot(0), duration: 5, performance: '', visible_subject_ids: [], offscreen_subject_ids: [], transition: 'continuous' }], soundscape: '', music: '', custom_instructions: '' };
const game = { ...clone(video), id: randomUUID(), workspace: 'game', title: 'Separate game' };
let loseNextQueueResponse = true;
const server = createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const route = pathname.replace(/\/$/, '') || '/';
    const filename = path.resolve(root, 'dist', ['/', '/video', '/studio', '/game'].includes(route) ? 'index.html' : decodeURIComponent(pathname).replace(/^\/+/, ''));
    if (!filename.startsWith(path.join(root, 'dist') + path.sep)) throw new Error('Invalid asset path');
    response.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    response.end(await readFile(filename));
  } catch { response.statusCode = 404; response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const studio = page.locator('#studio-workspace');
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('h3-workspace-mode', 'studio'));
  await page.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname.replace(/^\/api/, ''), method = request.method();
    requests.push({ path: endpoint, method });
    const json = value => route.fulfill({ json: clone(value) });
    const filmMatch = endpoint.match(/^\/films\/([^/]+)(\/produce)?$/);
    const permittedWrite = endpoint === '/projects' || endpoint === '/compile' || endpoint === '/films' || (filmMatch && ['PATCH', 'POST'].includes(method));
    if (!['GET', 'HEAD'].includes(method) && !permittedWrite) {
      forbidden.push(`${method} ${endpoint}`);
      return route.fulfill({ status: 503, json: { detail: 'Film smoke forbids AI, image generation, Game actions and GPU batch starts.' } });
    }
    if (endpoint === '/bootstrap') return json({ token: 'a'.repeat(43), project: video, video_project: video, game_project: game, projects: [video], settings: { model: 'test', persona: 'universal', last_video_project: video.id }, personas: [] });
    if (endpoint === '/connections') return json({ lm: { online: true, models: [] }, comfy: [] });
    if (endpoint === '/projects') {
      if (method === 'POST') { const draft=request.postDataJSON(); expect(draft.id).toBe(video.id);expect(draft.workspace).toBe('video');expect(draft.story.text).toBe(video.story.text);return json(draft); }
      return json([video]);
    }
    if (endpoint === '/compile') return json({ valid: false, prompt: '', issues: [], references: [], timeline: [] });
    if (endpoint === '/video/runs') return json({ runs: [] });
    if (endpoint === '/stories') return json({ stories: [] });
    if (endpoint === '/films' && method === 'GET') return json({ films: [...films.values()] });
    if (endpoint === '/films' && method === 'POST') {
      const body = request.postDataJSON(), id = randomUUID();
      const film = { id, title: body.title, idea: body.idea || '', target_minutes: body.target_minutes, clip_seconds: 15, revision: 1,
        status: 'draft', style: body.style || '', continuity_notes: body.continuity_notes || '', aspect_ratio: body.aspect_ratio || '16:9', quality: body.quality || 'draft',
        references: body.references || [], shots: body.shots ? body.shots.map(shot => ({ ...shot, id: randomUUID() })) : Array.from({ length: body.target_minutes * 4 }, (_, i) => blankShot(i)),
        latest_batch_id: null, batch_history: [], batch_revisions: {}, batch_fingerprints: {}, created_at: Date.now() / 1000, updated_at: Date.now() / 1000 };
      film.render_fingerprint = fingerprint(film); films.set(id, film); return json(film);
    }
    if (filmMatch) {
      const film = films.get(filmMatch[1]);
      if (!film) return route.fulfill({ status: 404, json: { detail: 'Missing mock film' } });
      if (method === 'GET') return json(film);
      const body = request.postDataJSON();
      if (filmMatch[2] && film.batch_history.includes(body.request_id)) {
        expect(body.expected_revision).toBe(film.batch_revisions[body.request_id]);
        return json({ film, batch: batches.get(body.request_id) });
      }
      expect(body.expected_revision).toBe(film.revision);
      if (filmMatch[2]) {
        expect(film.shots.every(shot => shot.action.trim())).toBe(true);
        const id = body.request_id, batch = { id, name: film.title, status: 'draft', completed: 0, total: film.shots.length,
          items: film.shots.map((shot, index) => ({ index, title: shot.title, project_id: randomUUID(), status: 'pending', duration: 15 })) };
        frozen.set(id, clone(film.shots)); batches.set(id, batch); film.batch_history.push(id);
        film.batch_revisions[id] = film.revision; film.batch_fingerprints[id] = film.render_fingerprint;
        film.latest_batch_id = id; film.revision++; film.status = 'queue_created';
        if (loseNextQueueResponse) { loseNextQueueResponse = false; return route.fulfill({ status: 503, json: { detail: 'The queue was saved; retry to recover the same request.' } }); }
        return json({ film, batch });
      }
      for (const field of editable) if (Object.hasOwn(body, field)) film[field] = clone(body[field]);
      film.revision++; film.updated_at = Date.now() / 1000; film.render_fingerprint = fingerprint(film); return json(film);
    }
    const batchId = endpoint.match(/^\/production\/([^/]+)$/)?.[1];
    if (batchId && batches.has(batchId)) return json(batches.get(batchId));
    return json({});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/studio`);
  await expect(page.locator('#studio-workspace')).toBeVisible();
  await expect(page.locator('#video-workspace')).not.toBeVisible();
  await expect(page.locator('#game-workspace')).not.toBeVisible();
  await studio.getByRole('button', { name: 'New film', exact: true }).first().click();
  await studio.getByLabel('Film name', { exact: true }).fill('Lantern film');
  await expect(studio.getByLabel('Film length').locator('option')).toHaveCount(10);
  await studio.getByLabel('Story idea').fill('A red paper lantern moves in a garden, then settles.');
  await studio.getByRole('button', { name: 'Create film', exact: true }).click();
  await expect(studio.getByLabel('Film idea')).toHaveValue('A red paper lantern moves in a garden, then settles.');
  const originalId = [...films.keys()][0];
  await expect(studio.getByRole('button', { name: 'Create render queue', exact: true })).toBeDisabled();
  for (let index = 0; index < 4; index++) {
    await studio.getByRole('button', { name: `Edit clip ${index + 1}: Clip ${index + 1}`, exact: true }).click();
    await studio.getByLabel('Clip action', { exact: true }).fill(`Beat ${index + 1}: the same red lantern sways, then settles.`);
  }
  await studio.getByText('Camera, sound & spoken lines', { exact: true }).click();
  await studio.getByRole('button', { name: 'Add spoken line', exact: true }).click();
  await studio.getByLabel('Clip speaker 1', { exact: true }).fill('Mira');
  await studio.getByLabel('Clip spoken line 1', { exact: true }).fill('Mirëmbrëma.');
  await studio.getByLabel('Clip dialogue language 1', { exact: true }).fill('Albanian');
  await studio.locator('.film-project-bar').getByRole('button', { name: 'Save now', exact: true }).click();
  await expect.poll(() => films.get(originalId).shots[3].dialogue[0]?.language).toBe('Albanian');
  await studio.locator('.film-project-bar').getByRole('button', { name: 'My films', exact: true }).click();
  await studio.locator('.film-library > button').filter({ has: page.locator('strong', { hasText: /^Lantern film$/ }) }).click();
  await expect(studio.getByLabel('Clip action', { exact: true })).toHaveValue('Beat 1: the same red lantern sways, then settles.');
  await studio.getByRole('button', { name: 'Create render queue', exact: true }).click();
  await expect(studio.getByRole('alert')).toContainText('retry to recover the same request');
  await studio.getByRole('button', { name: 'Create render queue', exact: true }).click();
  await expect(studio.getByRole('button', { name: 'Start batch', exact: true })).toBeVisible();
  expect(batches.size).toBe(1);
  const firstQueue = [...batches.keys()][0], frozenOpening = frozen.get(firstQueue)[0].action;
  await studio.getByLabel('Clip action', { exact: true }).fill('The lantern brightens before the breeze begins.');
  await expect(studio.locator('.film-queue-warning')).toBeVisible();
  await studio.getByRole('button', { name: 'Create render queue', exact: true }).click();
  await expect.poll(() => batches.size).toBe(2);
  await studio.getByLabel('Saved film render queue').selectOption(firstQueue);
  await expect(studio.locator('.film-queue-warning')).toBeVisible();
  expect(frozen.get(firstQueue)[0].action).toBe(frozenOpening);
  expect([...batches.values()].every(batch => batch.status === 'draft')).toBe(true);
  await studio.locator('.film-backup-tools > summary').click();
  const downloadPromise = page.waitForEvent('download');
  await studio.getByRole('button', { name: 'Download storyboard', exact: true }).click();
  const download = await downloadPromise;
  await mkdir(results, { recursive: true });
  const backupPath = path.join(results, 'film-storyboard-smoke.h3film.json');
  await download.saveAs(backupPath);
  const backup = JSON.parse(await readFile(backupPath, 'utf8'));
  expect(backup.format).toBe('h3-film-storyboard'); expect(backup.film.shots).toHaveLength(4);
  expect(backup.film).not.toHaveProperty('latest_batch_id'); expect(backup.film).not.toHaveProperty('id');
  const originalShotIds = films.get(originalId).shots.map(shot => shot.id);
  await studio.getByRole('button', { name: 'Duplicate film', exact: true }).click();
  await expect(studio.getByLabel('Film name', { exact: true })).toHaveValue('Lantern film · copy');
  expect(films.size).toBe(2);
  const copied = [...films.values()].find(film => film.id !== originalId);
  expect(copied.batch_history).toEqual([]);
  expect(copied.shots.every(shot => !originalShotIds.includes(shot.id))).toBe(true);
  for (const mode of ['video', 'game', 'studio']) {
    await page.locator(`.workspace-switch a[href="/${mode}"]`).click();
    for (const pane of ['video', 'game', 'studio']) {
      if (pane === mode) await expect(page.locator(`#${pane}-workspace`)).toBeVisible();
      else await expect(page.locator(`#${pane}-workspace`)).not.toBeVisible();
    }
  }
  await expect(studio.getByLabel('Film name', { exact: true })).toHaveValue('Lantern film · copy');
  expect(films.size).toBe(2);
  await studio.locator('.film-project-bar').getByRole('button', { name: 'My films', exact: true }).click();
  await studio.getByLabel('Import film storyboard file').setInputFiles(backupPath);
  await expect(studio.getByLabel('Film name', { exact: true })).toHaveValue('Lantern film');
  expect(films.size).toBe(3);
  const imported=[...films.values()].at(-1);
  expect(imported.shots.map(shot=>shot.action)).toEqual(backup.film.shots.map(shot=>shot.action));
  expect(imported.shots[3].dialogue[0].language).toBe('Albanian');expect(imported.batch_history).toEqual([]);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.screenshot({ path: path.join(results, 'frontend-film-smoke-mobile.png'), fullPage: true });
  expect(forbidden).toEqual([]); expect(errors).toEqual([]);
  console.log(JSON.stringify({ passed: true, films: films.size, queues: batches.size, lostQueueResponseRecovered: true, frozenStoryboardsPreserved: true,
    dialogueLanguage: true, backupImported: true, workspacePaneIsolation: true, batchStarts: 0, modelRequests: 0, requests: requests.length }, null, 2));
} finally {
  await browser.close(); await new Promise(resolve => server.close(resolve));
}
