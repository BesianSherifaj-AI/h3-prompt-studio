/** Run after `npm run build` in frontend. Uses isolated mocked API data; no GPU jobs. */
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const project = { schema_version: 1, id: 'smoke-project', title: 'Pixel street', mode: 't2va', duration: 3,
  aspect_ratio: '16:9', profile: 'director', authoring_mode: 'assisted', story: { text: 'I am on a pixel art city street.', locked: false },
  style: {}, assets: [], subjects: [{ id: 'player', name: 'Alex', description: 'Pixel adventurer', asset_ids: [] }],
  shots: [{ id: 'shot', duration: 3, action: '', setting: '', camera: {}, performance: '', final_state: '', visible_subject_ids: [], offscreen_subject_ids: [], dialogue: [], sound: '', transition: '' }],
  soundscape: '', music: '', custom_instructions: '', comfy_render: { experimental_preview: true, resolution: '0.2', steps: 8 } };
const person = (id, name, state = {}) => ({ id, name, state, control: id === 'player' ? 'player' : 'npc', location_id: 'street',
  description: '', personality: '', goals: [], speaking_style: '', private_knowledge: [], relationships: {}, asset_ids: [], witnessed_events: [] });
const entity = (id, name, fields = {}) => ({ id, name, kind: 'object', asset_ids: [], affordances: [], state: {}, location_id: 'street', owner_id: null, holder_id: null, worn_by_id: null, ...fields });
const story = { id: '11111111-1111-4111-8111-111111111111', title: 'Pixel street', project_id: project.id, project,
  mode: 'game', premise: project.story.text, player_name: 'Alex', player_character_id: 'player', active_branch_id: 'main',
  active_run_id: null, configuration_revision: 1, turns: [], clips: [], jobs: [], guides: [], choices: [],
  settings: { duration: 3, resolution: '0.2', steps: 8, experimental_preview: true, review_before_render: false, assistant_provider: 'lmstudio', style: 'Pixel art', image_model: 'saved-unavailable-model' },
  world: { schema_version: 1, current_location_id: 'street', rules: [], objectives: [], events: [],
    locations: [{ id: 'street', name: 'Lantern Street', description: '', exits: [{ target_id: 'alley' }], asset_ids: [] }, { id: 'alley', name: 'Alley', description: '', exits: ['street'], asset_ids: [] }],
    characters: [person('player', 'Alex'), person('mara', 'Mara'), person('guard', 'Fallen guard', { defeated: true }), { ...person('remote', 'Remote NPC'), location_id: 'alley' }],
    entities: [entity('key', 'Brass key', { holder_id: 'player' }), entity('coat', 'Blue coat', { worn_by_id: 'player' }), entity('coin', 'Silver coin'), entity('door', 'Oak door', { kind: 'door' })] } };
const requests = [], errors = [];
let loseAcknowledgement = false;
let generatorChecks = 0, generatorOffline = true;
function catalog() {
  const targets = [{ id: 'mara', name: 'Mara', kind: 'character' }, { id: 'guard', name: 'Fallen guard', kind: 'character' },
    ...story.world.entities.map(item => ({ id: item.id, name: item.name, kind: item.kind })), { id: 'alley', name: 'Alley', kind: 'location' }];
  const mara = story.world.characters.find(person => person.id === 'mara');
  const actions = [{ kind: 'look', label: 'Look around' }, { kind: 'inventory', label: 'Check inventory' },
    { kind: 'talk', label: 'Talk to Mara', target_id: 'mara', enabled: !mara.state.defeated },
    { kind: 'attack', label: 'Attack Mara', target_id: 'mara', enabled: !mara.state.defeated },
    { kind: 'move', label: 'Go to Alley', target_id: 'alley', enabled: true }];
  for (const item of story.world.entities) {
    actions.push({ kind: 'examine', target_id: item.id, label: `Examine ${item.name}` });
    if (item.holder_id === 'player' && !item.worn_by_id) for (const kind of ['drop', 'give']) actions.push({ kind, target_id: item.id, label: `${kind === 'give' ? 'Give' : 'Drop'} ${item.name}`, enabled: true });
    if (!item.holder_id && !item.worn_by_id && item.kind === 'object') actions.push({ kind: 'take', target_id: item.id, label: `Take ${item.name}`, enabled: true });
  }
  return { targets, actions };
}
const server = createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const relative = pathname === '/' ? 'index.html' : decodeURIComponent(pathname).replace(/^\/+/, '');
    const filename = path.resolve(root, 'dist', relative);
    if (!filename.startsWith(path.join(root, 'dist') + path.sep)) throw new Error('Invalid path');
    const data = await readFile(filename);
    response.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    response.end(data);
  } catch { response.statusCode = 404; response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1360, height: 980 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(id => { localStorage.setItem('h3-game:selected-story', id); localStorage.setItem('h3-workspace-mode', 'game'); }, story.id);
  await page.route('**/api/**', async route => {
    const request = route.request(), pathname = new URL(request.url()).pathname.replace(/^\/api/, '');
    const json = value => route.fulfill({ json: structuredClone(value) });
    if (pathname === '/bootstrap') return json({ token: 'a'.repeat(43), project, projects: [], settings: { model: 'test', persona: 'universal' }, personas: [] });
    if (pathname === '/stories') return json({ stories: [story] });
    if (pathname === `/stories/${story.id}`) {
      if (request.method() === 'PATCH') {
        const body = request.postDataJSON();
        story.settings = { ...story.settings, ...body.settings };
        story.configuration_revision++;
      }
      return json(story);
    }
    if (pathname.endsWith('/actions')) return json(catalog());
    if (pathname.endsWith('/turns') && request.method() === 'POST') {
      const body = request.postDataJSON(); requests.push(body);
      const turn = { id: body.request_id, request_id: body.request_id, message: body.message, status: 'succeeded', created_at: Date.now() / 1000,
        plan: { action: body.message, dialogue: [], characters: [], asset_requests: [], transition: 'continue', setting: 'Street', final_state: '', choices: [] } };
      story.turns.push(turn); story.active_run_id = `smoke-run-${requests.length}`;
      const item = story.world.entities.find(item => item.id === body.intent?.target_id);
      if (body.intent?.kind === 'give' && item) item.holder_id = body.intent.recipient_id;
      if (body.intent?.kind === 'take' && item) item.holder_id = 'player';
      if (body.intent?.kind === 'move') story.world.entities = story.world.entities.filter(item => item.id !== 'door');
      if (body.intent?.kind === 'attack') story.world.characters.find(person => person.id === body.intent.target_id).state.defeated = true;
      if (loseAcknowledgement) { loseAcknowledgement = false; return route.abort('failed'); }
      return json(turn);
    }
    if (pathname === '/connections') return json({ lm: { online: true, models: [] }, comfy: { online: false } });
    if (pathname === '/assets/generators') {
      generatorChecks++;
      if (request.method() !== 'GET') throw new Error('Checking availability must not mutate anything.');
      return json(generatorOffline ? { generators: [], default_model: '', errors: ['ComfyUI node inventory is unavailable.'] }
        : { generators: [{ id: 'h3-frame', name: 'H3 frame', available: true }], default_model: 'h3-frame', errors: [] });
    }
    if (pathname === '/compile') return json({ valid: true, prompt: 'Mock preview', issues: [], references: [], timeline: [] });
    if (pathname === '/projects') return json(request.method() === 'POST' ? request.postDataJSON() : []);
    if (pathname === '/video/runs') return json({ runs: [] });
    return json({});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?game=${story.id}`);
  await expect(page.getByRole('region', { name: 'World and inventory' })).toBeVisible();
  const inventory = page.locator('.game-inventory');
  await expect(inventory).toContainText('Inventory · 2');
  await expect(page.getByLabel('Interact with')).toHaveValue('');
  if (requests.length) throw new Error('Opening a game submitted an unsolicited move.');
  await page.getByRole('button', { name: 'Game settings', exact: true }).click();
  await page.getByRole('button', { name: 'Rendering', exact: true }).click();
  await expect(page.getByLabel('Create extra reference images')).not.toBeChecked();
  await expect(page.getByRole('combobox', { name: 'Image generator', exact: true })).toHaveValue('saved-unavailable-model');
  await expect(page.getByRole('region', { name: 'Extra reference images' })).toContainText('does not confirm missing model files');
  const checkedBeforeRefresh = generatorChecks;
  generatorOffline = false;
  await page.getByRole('button', { name: 'Refresh image generators', exact: true }).click();
  await expect.poll(() => generatorChecks).toBe(checkedBeforeRefresh + 1);
  await expect(page.getByRole('region', { name: 'Extra reference images' })).toContainText('Available: H3 frame');
  await expect(page.getByRole('combobox', { name: 'Image generator', exact: true })).toHaveValue('saved-unavailable-model');
  await expect(page.getByLabel('Create extra reference images')).not.toBeChecked();
  if (requests.length) throw new Error('Refreshing generators queued a game move.');
  await page.getByLabel('Create extra reference images').check();
  await expect(page.getByLabel('Quick item and movement actions')).toBeChecked();
  await page.getByLabel('Quick item and movement actions').uncheck();
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect.poll(() => story.settings.fast_actions).toBe(false);
  await expect.poll(() => story.settings.generate_references).toBe(true);
  expect(story.settings.image_model).toBe('saved-unavailable-model');
  await page.getByRole('button', { name: 'Close game editor', exact: true }).click();
  await page.getByRole('button', { name: 'Game settings', exact: true }).click();
  await page.getByRole('button', { name: 'Rendering', exact: true }).click();
  await expect(page.getByLabel('Quick item and movement actions')).not.toBeChecked();
  await expect(page.getByLabel('Create extra reference images')).toBeChecked();
  await expect(page.getByRole('combobox', { name: 'Image generator', exact: true })).toHaveValue('saved-unavailable-model');
  await page.getByRole('button', { name: 'Close game editor', exact: true }).click();
  await page.getByRole('button', { name: 'Check inventory', exact: true }).click();
  await expect(inventory).toBeFocused();
  await page.locator('#game-next-move').fill('I check my inventory');
  await page.getByRole('button', { name: 'Queue my move', exact: true }).click();
  await expect(inventory).toBeFocused();
  await expect(page.locator('#game-next-move')).toHaveValue('');
  await page.waitForTimeout(3200);
  if (requests.length) throw new Error('Checking inventory queued an unnecessary video.');
  await inventory.getByRole('button', { name: 'Give…', exact: true }).click();
  await expect(page.getByLabel('Give to')).toBeVisible();
  await expect(page.getByLabel('Give to').locator('option')).toHaveText(['Choose a character…', 'Mara']);
  await page.getByLabel('Give to').selectOption('mara');
  await page.getByRole('button', { name: 'Give Brass key', exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].intent).toMatchObject({ kind: 'give', target_id: 'key', recipient_id: 'mara' });
  await expect(inventory).toContainText('Inventory · 1');
  await page.getByText('Items here · 2', { exact: true }).click();
  loseAcknowledgement = true;
  await page.getByRole('button', { name: 'Pick up', exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  await expect(inventory).toContainText('Silver coin');
  await expect(page.getByRole('region', { name: 'Scene queue' })).toContainText('Scene queue · 0');
  expect(requests[1].intent).toMatchObject({ kind: 'take', target_id: 'coin' });
  await page.getByLabel('Interact with').selectOption('door');
  await page.getByRole('button', { name: 'Move forward', exact: true }).click();
  await expect.poll(() => requests.length).toBe(3);
  expect(requests[2].intent).toMatchObject({ kind: 'move', target_id: 'door', extent: 'step' });
  await expect(page.getByLabel('Interact with')).toHaveValue('');
  await page.getByRole('button', { name: 'Attack Mara', exact: true }).click();
  await expect.poll(() => requests.length).toBe(4);
  await expect(page.getByRole('button', { name: 'Attack Mara', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Talk to Mara', exact: true })).toBeDisabled();
  await expect(page.locator('.game-world-people')).toContainText('Mara · Defeated');
  await expect(inventory.getByRole('button', { name: 'Give…', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Give to')).toHaveCount(0);
  expect(new Set(requests.map(body => body.request_id)).size).toBe(4);
  story.turns.push({ ...story.turns.at(-1), id: 'failed-inspection', request_id: 'failed-inspection-request', status: 'inspection_failed', observation: null, error: 'Ending inspection failed. The generated video is kept.' });
  await page.reload();
  await page.getByRole('button', { name: 'Review details', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Use visible result', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Retry ending inspection', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Use intended story', exact: true })).toBeEnabled();
  story.turns.at(-1).observation = { observed_state: 'Mara is on the ground beside the street.' };
  story.turns.at(-1).status = 'awaiting_acceptance';
  await page.reload();
  await page.getByRole('button', { name: 'Review details', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Use visible result', exact: true })).toBeEnabled();
  expect(requests.length).toBe(4);
  expect(errors).toEqual([]);
  await mkdir(path.join(root, 'test-results'), { recursive: true });
  await page.screenshot({ path: path.join(root, 'test-results/frontend-game-smoke.png'), fullPage: true });
  console.log(JSON.stringify({ passed: true, moves: requests.map(body => body.intent), pageErrors: errors,
    screenshot: 'test-results/frontend-game-smoke.png' }, null, 2));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
