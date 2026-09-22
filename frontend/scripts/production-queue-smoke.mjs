// Isolated production UI regression: all API requests are mocked; no media is generated.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';

const dist = resolve(dirname(fileURLToPath(import.meta.url)), '../../dist');
const server = createServer(async (req, res) => {
  try {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = pathname.startsWith('/assets/') ? resolve(dist, '.' + pathname) : resolve(dist, 'index.html');
    if (!file.startsWith(dist + sep)) { res.writeHead(403).end(); return; }
    res.setHeader('Content-Type', ({ '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' })[extname(file)] || 'application/octet-stream');
    res.end(await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(6000);
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const settings = { assistant_profiles: { studio: { model: 'large', context_length: 8192, ai_memory_mode: 'exclusive' }, game: { model: 'small', context_length: 8192, ai_memory_mode: 'resident_cpu' } } };
  const project = { schema_version: 1, id: 'fixture-project', workspace: 'studio', title: 'Opening scene', mode: 't2va', duration: 5, aspect_ratio: '16:9', profile: 'director', authoring_mode: 'ai', story: { text: 'A lantern lights up.', locked: false }, style: {}, assets: [], subjects: [], shots: [{ id: 'shot', duration: 5, action: 'A lantern glows', setting: 'Garden', camera: {}, visible_subject_ids: [], offscreen_subject_ids: [], dialogue: [] }], soundscape: '', music: '', custom_instructions: '' };
  let batches = [], failCreate = true;
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace('/api', '');
    const body = request.method() === 'GET' ? null : request.postDataJSON();
    let result = {};
    if (path === '/bootstrap') result = { token: 'a'.repeat(43), project, projects: [project], settings, personas: [] };
    else if (path === '/connections') result = { lm: { online: true, models: [] }, comfy: [], busy: false, stage: 'idle' };
    else if (path === '/compile') result = { valid: true, prompt: 'A lantern glows.', issues: [], references: [], timeline: [] };
    else if (path === '/projects') result = body || [project];
    else if (path === '/video/runs') result = { runs: [] };
    else if (path === '/production' && body) {
      writes.push(body);
      if (failCreate) { await route.fulfill({ status: 503, json: { detail: 'Production storage temporarily unavailable.' } }); return; }
      const batch = { id: body.request_id, name: body.name, status: 'draft', completed: 0, total: 1, items: [{ index: 0, project_id: project.id, title: project.title, status: 'queued' }] };
      batches = [batch]; result = batch;
    } else if (path === '/production') result = { batches };
    else if (path.startsWith('/production/')) {
      const action = path.split('/')[3];
      if (body) {
        writes.push({ action });
        if (action === 'start' || action === 'resume') batches[0].status = 'running';
        if (action === 'cancel') batches[0].status = 'cancelled';
      }
      result = batches[0];
    }
    await route.fulfill({ json: result });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/studio`);
  await page.locator('.production-queue > summary').click();
  await page.locator('.production-create > summary').click();
  await page.getByLabel('Batch name', { exact: true }).fill('Film test');
  await page.getByRole('button', { name: 'Queue current project', exact: true }).click();
  await expect(page.locator('.production-error')).toContainText('Production storage temporarily unavailable.');
  failCreate = false;
  await page.getByRole('button', { name: 'Queue current project', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start batch', exact: true })).toBeEnabled();
  assert.equal(writes[0].request_id, writes[1].request_id, 'Retry a failed create with the original idempotency key');
  await page.getByRole('button', { name: 'Start batch', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Stop queue', exact: true })).toBeEnabled();
  await page.reload();
  await page.locator('.production-queue > summary').click();
  await expect(page.getByRole('button', { name: 'Stop queue', exact: true })).toBeEnabled();
  assert.equal(writes.filter(item => item.action === 'start').length, 1, 'Refreshing never starts another worker');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, 'Production controls fit 390px');
  await page.getByRole('button', { name: 'Stop queue', exact: true }).click();
  await expect(page.locator('.production-batch-heading')).toContainText('Stopped');
  assert.deepEqual(errors, []);
  console.log('PASS: failed create recovery, stable request ID, start, restart visibility, explicit stop, 390px layout, no runtime errors.');
} finally { await browser.close(); await new Promise(done => server.close(done)); }
