/** Run after `npm run build` in frontend. Uses isolated mocked API data; no GPU jobs. */
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const playerPicker = process.argv.includes('--player-picker');
const playerPickerRecovery = process.argv.includes('--player-picker-recovery');
const sceneRecovery = process.argv.includes('--scene-recovery') || playerPicker || playerPickerRecovery;
const project = { schema_version: 1, id: 'smoke-project', title: 'Pixel street', mode: 't2va', duration: 3,
  aspect_ratio: '16:9', profile: 'director', authoring_mode: 'assisted', story: { text: 'I am on a pixel art city street.', locked: false },
  style: {}, assets: [], subjects: [{ id: 'player', name: 'Alex', description: 'Pixel adventurer', asset_ids: [] }],
  shots: [{ id: 'shot', duration: 3, action: '', setting: '', camera: {}, performance: '', final_state: '', visible_subject_ids: [], offscreen_subject_ids: [], dialogue: [], sound: '', transition: '' }],
  soundscape: '', music: '', custom_instructions: '', comfy_render: { experimental_preview: true, resolution: '0.2', steps: 8 } };
const person = (id, name, state = {}) => ({ id, name, state, control: id === 'player' ? 'player' : 'npc', location_id: 'street',
  description: id === 'player' ? 'Pixel adventurer' : '', personality: '', goals: [], speaking_style: '', private_knowledge: [], relationships: {}, asset_ids: [], witnessed_events: [] });
const entity = (id, name, fields = {}) => ({ id, name, kind: 'object', asset_ids: [], affordances: [], state: {}, location_id: 'street', owner_id: null, holder_id: null, worn_by_id: null, ...fields });
const story = { id: '11111111-1111-4111-8111-111111111111', title: 'Pixel street', project_id: project.id, project,
  mode: 'game', premise: project.story.text, player_name: 'Alex', player_character_id: 'player', active_branch_id: 'main',
  active_run_id: null, configuration_revision: 1, turns: [], clips: [], jobs: [], guides: [], choices: [],
  settings: { duration: 3, resolution: '0.2', steps: 8, experimental_preview: true, review_before_render: false, assistant_provider: 'lmstudio', style: 'Pixel art', image_model: 'saved-unavailable-model' },
  world: { schema_version: 1, current_location_id: 'street', rules: [], objectives: [], events: [],
    locations: [{ id: 'street', name: 'Lantern Street', description: '', exits: [{ target_id: 'alley' }], asset_ids: [] }, { id: 'alley', name: 'Alley', description: '', exits: ['street'], asset_ids: [] }],
    characters: [person('player', 'Alex'), person('mara', 'Mara'), person('guard', 'Fallen guard', { defeated: true }), { ...person('remote', 'Remote NPC'), location_id: 'alley' }],
    entities: [entity('key', 'Brass key', { holder_id: 'player' }), entity('coat', 'Blue coat', { worn_by_id: 'player' }), entity('coin', 'Silver coin'), entity('door', 'Oak door', { kind: 'door' })] } };
const requests = [], errors = [], mutationPaths = [];
let loseAcknowledgement = false;
let generatorChecks = 0, generatorOffline = true;
const sceneRequests = [];
let sceneInspected = false, sceneInspectionStatus = '', sceneInspectionReads = 0;
let failSceneScan = false;
const sceneVideo = id => ({ id, project_id: project.id, status: 'succeeded', video_url: `/api/mock-video/${id}`, scene_video_url: `/api/mock-video/${id}/scene`, ending_image_url: `/api/mock-ending/${id}`, duration: 3, width: 608, height: 320 });
if (sceneRecovery) {
  story.active_run_id = 'scene-old'; story.clips = [sceneVideo('scene-old')]; story.jobs = [...story.clips];
  story.turns = [{ id: 'opening', request_id: 'opening', status: 'succeeded', run_id: 'scene-old', message: 'Start on the street.', created_at: 1, video: story.clips[0], observation: { observed_state: 'People and shops line a pixel street.' } }];
  story.world = { ...story.world, current_location_id: null, locations: [], characters: [{ ...person('player', 'Alex'), description: '', location_id: null }], entities: [] };
}
const failedMove = { id: 'legacy-failed', request_id: 'legacy-request', status: 'failed', branch_id: 'main', parent_run_id: 'scene-old', created_at: 2,
  message: 'I attempt to move forward a small distance, at slow speed. Show continuous movement.',
  intent: { kind: 'move', direction: 'forward', extent: 'step', speed: 'slow', camera: 'player', presentation: 'continuous' },
  error: 'Identify your character first before moving.' };
const laterMove = { id: 'later-queued', message: 'Then I look left.', intent: { kind: 'look' }, readyAt: 0, submitted: false };
if (playerPickerRecovery) story.turns.push(failedMove);
function sceneCatalog() {
  if (sceneInspectionStatus === 'running' && ++sceneInspectionReads > 1) { sceneInspectionStatus = failSceneScan ? 'failed' : 'succeeded'; sceneInspected = !failSceneScan; }
  const pending = story.turns.at(-1)?.status === 'awaiting_acceptance';
  const stale = story.turns.at(-1)?.observation?.inspection_status === 'not_run';
  const run = pending ? story.turns.at(-1).run_id : story.active_run_id;
  const candidate = { id: 'person-left', kind: 'person', known_id: story.world.characters[0].description ? 'player' : null,
    label: 'Person in purple', description: 'Purple shirt and glasses', position: 'Left side of the street',
    identity_status: story.world.characters[0].description ? 'known' : 'unidentified',
    actions: [{ kind: 'examine', label: 'Examine', enabled: !pending && !stale, intent: { kind: 'scene_target', scene_run_id: run, candidate_id: 'person-left', action: 'examine' } }] };
  const otherPerson = { ...candidate, id: 'person-right', known_id: null, identity_status: 'unidentified', label: 'Person in orange', description: 'Orange hat and jacket', position: 'Right storefront',
    actions: [{ kind: 'talk', label: 'Talk to person in orange', enabled: !pending && !stale, intent: { kind: 'scene_target', scene_run_id: run, candidate_id: 'person-right', action: 'talk' } }] };
  return { targets: [], actions: [], scene: { run_id: run, branch_id: story.active_branch_id, configuration_revision: story.configuration_revision,
    status: pending ? 'pending_review' : stale ? 'stale' : sceneInspected ? 'ready' : 'unavailable', setting: 'Pixel street', targets: sceneInspected ? [candidate, otherPerson] : [],
    ...(stale ? { inspected_run_id: 'scene-old', inspection_status: 'not_run' } : {}),
    ...(sceneInspectionStatus ? { inspection: { status: sceneInspectionStatus, request_id: 'inspection' } } : {}) } };
}
function catalog() {
  if (sceneRecovery) return sceneCatalog();
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
  if (playerPickerRecovery) await page.addInitScript(({ id, first, later }) => localStorage.setItem(`h3-game:move-queue:${id}`, JSON.stringify({ branch: 'main', paused: true, error: 'Old move failed.', items: [{ id: first.request_id, message: first.message, intent: first.intent, readyAt: 0, submitted: true }, later] })), { id: story.id, first: failedMove, later: laterMove });
  await page.route('**/api/**', async route => {
    const request = route.request(), pathname = new URL(request.url()).pathname.replace(/^\/api/, '');
    if (!['GET', 'HEAD'].includes(request.method())) mutationPaths.push(pathname);
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
    if (sceneRecovery && pathname.endsWith('/scene-inspection') && request.method() === 'POST') {
      sceneRequests.push({ path: pathname, body: request.postDataJSON() }); sceneInspectionStatus = 'running'; sceneInspectionReads = 0;
      return json({ status: 'pending', request_id: request.postDataJSON().request_id });
    }
    if (sceneRecovery && pathname.endsWith('/scene-player') && request.method() === 'POST') {
      sceneRequests.push({ path: pathname, body: request.postDataJSON() });
      story.world.characters[0].description = request.postDataJSON().appearance || 'Purple shirt and glasses';
      story.world.characters[0].state.visual_anchor = story.world.characters[0].description;
      story.configuration_revision++;
      return json(story);
    }
    if (sceneRecovery && pathname.endsWith('/accept-visible') && request.method() === 'POST') {
      sceneRequests.push({ path: pathname, body: request.postDataJSON() });
      story.turns.at(-1).status = 'succeeded'; story.active_run_id = story.turns.at(-1).run_id;
      story.clips.push(story.turns.at(-1).video); return json(story);
    }
    if (pathname.endsWith('/turns') && request.method() === 'POST') {
      const body = request.postDataJSON(); requests.push(body);
      const turn = { id: body.request_id, request_id: body.request_id, message: body.message, status: 'succeeded', created_at: Date.now() / 1000,
        plan: { action: body.message, dialogue: [], characters: [], asset_requests: [], transition: 'continue', setting: 'Street', final_state: '', choices: [] } };
      if (sceneRecovery) { story.turns.push({ ...turn, status: 'planning', intent: body.intent, ...(requests.length === 2 ? { planning_mode: 'deterministic_movement' } : {}) }); return json(story.turns.at(-1)); }
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
    if (pathname.startsWith('/mock-ending/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="608" height="320"><rect width="608" height="320" fill="#263c35"/><text x="28" y="55" fill="#fff" font-size="22">Synthetic saved ending fixture</text><rect x="100" y="130" width="75" height="130" fill="#9971d5"/><circle cx="138" cy="108" r="25" fill="#edc394"/></svg>' });
    return json({});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/?game=${story.id}`);
  if (playerPickerRecovery) {
    const dialog = page.getByRole('dialog', { name: 'Who are you playing?' });
    await page.getByRole('button', { name: 'Choose character & continue', exact: true }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: /Person in purple/ })).toBeVisible();
    expect(requests).toHaveLength(0);
    // The actual modal must sit above sticky tabs and suggestion cards.
    expect(await dialog.evaluate(element => { const box = element.getBoundingClientRect(); return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + 160)); })).toBe(true);
    await mkdir(path.join(root, 'test-results'), { recursive: true });
    await page.screenshot({ path: path.join(root, 'test-results/frontend-player-picker-recovery.png'), fullPage: false });
    await dialog.getByRole('button', { name: /Person in purple/ }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0]).toMatchObject({ message: failedMove.message, intent: failedMove.intent, configuration_revision: 2, expected_parent: 'scene-old' });
    expect(requests[0].request_id).not.toBe(failedMove.request_id);
    const queue = await page.evaluate(id => JSON.parse(localStorage.getItem(`h3-game:move-queue:${id}`)), story.id);
    expect(queue.items.map(item => item.id)).toEqual([requests[0].request_id, laterMove.id]);
    expect(queue.items[1]).toEqual(laterMove);
    expect(queue.items[0].recoverySourceId).toBe(failedMove.request_id);
    expect(mutationPaths.some(value => value.includes('/retry'))).toBe(false);
    await expect(page.getByRole('button', { name: 'Change character', exact: true })).toBeVisible();
    expect(sceneRequests.filter(item => item.path.endsWith('/scene-player'))).toHaveLength(1);
    expect(errors).toEqual([]);
    console.log(JSON.stringify({ passed: true, scenario: 'player-picker-legacy-recovery', freshRequest: true, freshRevision: 2, originalMovePreserved: true, laterQueueEntryPreserved: true, oldRetryEndpointCalled: false, modalAboveStickyControls: true, pageErrors: errors, screenshot: 'test-results/frontend-player-picker-recovery.png' }, null, 2));
  } else if (playerPicker) {
    const dialog = page.getByRole('dialog', { name: 'Who are you playing?' });
    await page.getByRole('button', { name: 'Choose character', exact: true }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('img')).toHaveAttribute('src', '/api/mock-ending/scene-old');
    await expect.poll(() => sceneRequests.filter(item => item.path.endsWith('/scene-inspection')).length).toBe(1);
    await dialog.getByRole('button', { name: 'Cancel character selection', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.waitForTimeout(3200); expect(requests).toHaveLength(0);
    await page.getByText('Movement options', { exact: false }).click();
    await page.getByRole('combobox', { name: 'Distance', exact: true }).selectOption('nearby');
    await page.getByRole('combobox', { name: 'Motion speed', exact: true }).selectOption('slow');
    await page.getByRole('button', { name: 'Move forward', exact: true }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('Choose once. Your move continues after you pick.');
    await expect(dialog.getByRole('button', { name: /Person in purple/ })).toBeVisible();
    expect(requests).toHaveLength(0);
    await dialog.getByRole('button', { name: /Person in purple/ }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].intent).toMatchObject({ direction: 'forward', extent: 'nearby', speed: 'slow', camera: 'player', presentation: 'continuous' });
    expect(requests[0].configuration_revision).toBe(2);
    expect(sceneRequests.filter(item => item.path.endsWith('/scene-player'))).toHaveLength(1);
    expect(sceneRequests.filter(item => item.path.endsWith('/scene-inspection'))).toHaveLength(1);
    // A separate unbound saved game exercises the always-available manual path.
    story.turns = [story.turns[0]]; story.world.characters[0].description = ''; story.world.characters[0].state = {};
    story.configuration_revision++; sceneInspected = false; sceneInspectionStatus = ''; failSceneScan = true;
    await page.evaluate(id => { for (const key of Object.keys(localStorage)) if (key.startsWith('h3-game:move-queue:') || key.startsWith('h3-game:configuration:')) localStorage.removeItem(key); }, story.id);
    await page.reload();
    await page.getByRole('button', { name: 'Move left', exact: true }).click();
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Describe my character instead', exact: true }).click();
    await dialog.getByRole('textbox', { name: 'What does your character look like?', exact: true }).fill('The person on the right in the orange hat.');
    await mkdir(path.join(root, 'test-results'), { recursive: true });
    await page.screenshot({ path: path.join(root, 'test-results/frontend-player-picker-smoke.png'), fullPage: false });
    await dialog.getByRole('button', { name: 'Use this character and continue', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(() => requests.length).toBe(2);
    const manualBody = sceneRequests.filter(item => item.path.endsWith('/scene-player')).at(-1).body;
    expect(manualBody.appearance).toBe('The person on the right in the orange hat.');
    expect(manualBody).not.toHaveProperty('candidate_id');
    expect(requests[1].intent.direction).toBe('left');
    expect(requests[1].configuration_revision).toBe(4);
    expect(new Set(requests.map(body => body.request_id)).size).toBe(2);
    expect(errors).toEqual([]);
    console.log(JSON.stringify({ passed: true, scenario: 'player-picker', cancelledMoveSent: false, singleChoiceContinuesOnce: true, originalOptionsPreserved: true, manualAppearanceWithoutCandidate: true, sceneRequests: sceneRequests.map(item => item.path.split('/').at(-1)), movementRequests: requests.map(body => body.intent.direction), pageErrors: errors, screenshot: 'test-results/frontend-player-picker-smoke.png' }, null, 2));
  } else if (sceneRecovery) {
    const scenePanel = page.getByRole('region', { name: 'Visible scene' });
    const progress = page.getByRole('region', { name: 'Current move progress' });
    await expect(scenePanel).toContainText('no saved selectable people or objects yet');
    await expect(scenePanel).toContainText('Your appearance is not identified yet');
    await expect(page.getByRole('region', { name: 'World and inventory' })).toContainText('No other characters are established here yet');
    expect(requests.length).toBe(0); expect(sceneRequests.length).toBe(0);
    await scenePanel.getByRole('button', { name: 'Inspect this ending', exact: true }).click();
    await expect.poll(() => sceneRequests.length).toBe(1);
    expect(sceneRequests[0].body).toMatchObject({ run_id: 'scene-old', branch_id: 'main', configuration_revision: 1 });
    await expect(scenePanel.getByRole('button', { name: /Person in purple/ })).toBeVisible();
    expect(requests.length).toBe(0);
    await page.getByRole('button', { name: 'Move forward', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Who are you playing?' })).toBeVisible();
    await page.getByRole('button', { name: 'Cancel this move', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Scene queue' })).toContainText('Scene queue · 0');
    expect(requests.length).toBe(0);
    await scenePanel.getByRole('button', { name: /Person in purple/ }).click();
    await scenePanel.getByRole('button', { name: 'This is me', exact: true }).click();
    await expect.poll(() => sceneRequests.length).toBe(2);
    expect(sceneRequests[1].body).toMatchObject({ run_id: 'scene-old', candidate_id: 'person-left', branch_id: 'main', configuration_revision: 1 });
    await expect(scenePanel).toContainText('Purple shirt and glasses');
    await expect(scenePanel.getByRole('button', { name: /Person in purple · You/ })).toBeVisible();
    expect(story.world.entities).toHaveLength(0); expect(requests.length).toBe(0);
    await page.getByRole('button', { name: 'Move forward', exact: true }).click();
    await expect(progress).toContainText('Move queued');
    await expect(progress).toContainText('move forward');
    await page.getByRole('button', { name: 'Move left', exact: true }).click();
    await expect.poll(() => requests.length).toBe(1);
    await expect(progress).toContainText('Planning the next moment');
    story.turns.at(-1).status = 'rendering'; story.turns.at(-1).stage = 'Rendering movement';
    await expect(progress).toContainText('Creating your video');
    const result = sceneVideo('scene-new'); story.jobs.push(result);
    Object.assign(story.turns.at(-1), { status: 'awaiting_acceptance', run_id: result.id, video: result,
      observation: { observed_state: 'The player and two other people stand beside the shops.', continuity_checks: [{ status: 'mismatch', kind: 'actor', id: 'player', detail: 'Two other people are visible beside the player.' }] } });
    await expect(progress).toContainText('New video ready · your review is needed');
    await expect(progress).toContainText('Two other people are visible beside the player.');
    await expect(progress).toContainText('waiting for your review');
    await expect(page.getByLabel('Game video')).toHaveAttribute('src', result.scene_video_url);
    await expect(page.getByLabel('Game video')).toHaveAttribute('poster', result.ending_image_url);
    await expect(page.getByRole('button', { name: 'Replay this scene', exact: true })).toBeVisible();
    await expect(scenePanel).toContainText('New ending · waiting for review');
    await scenePanel.getByRole('button', { name: /Person in purple/ }).click();
    await expect(scenePanel.getByRole('button', { name: 'This is me', exact: true })).toBeDisabled();
    await expect(scenePanel.getByRole('button', { name: 'Examine', exact: true })).toBeDisabled();
    await page.waitForTimeout(3200); expect(requests.length).toBe(1);
    await mkdir(path.join(root, 'test-results'), { recursive: true });
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.screenshot({ path: path.join(root, 'test-results/frontend-scene-recovery-smoke.png'), fullPage: false });
    await progress.getByRole('button', { name: 'Use visible result', exact: true }).click();
    await expect.poll(() => requests.length).toBe(2);
    expect(requests[1].intent.direction).toBe('left');
    expect(new Set(requests.map(body => body.request_id)).size).toBe(2);
    await expect(progress).toContainText('Preparing movement');
    await expect(progress).toContainText('No language model is called for this turn');
    const localResult = sceneVideo('scene-local'); story.jobs.push(localResult); story.clips.push(localResult); story.active_run_id = localResult.id;
    Object.assign(story.turns.at(-1), { status: 'succeeded', run_id: localResult.id, video: localResult, observation: { inspection_status: 'not_run', cached_scene_run_id: 'scene-old' } });
    await expect(progress).toContainText('Ready for your next move');
    await expect(scenePanel).toContainText('positions in this new ending have not been checked');
    await expect(scenePanel).toContainText('Previously: Left side of the street');
    await scenePanel.getByRole('button', { name: /Person in purple/ }).click();
    await expect(scenePanel.getByRole('button', { name: 'This is me', exact: true })).toHaveCount(0);
    await expect(scenePanel.getByRole('button', { name: 'Examine', exact: true })).toBeDisabled();
    await expect(scenePanel.getByRole('button', { name: 'Inspect this ending', exact: true })).toBeEnabled();
    expect(sceneRequests).toHaveLength(3);
    expect(errors).toEqual([]);
    console.log(JSON.stringify({ passed: true, scenario: 'scene-recovery', explicitSceneRequests: sceneRequests.map(item => item.path.split('/').at(-1)), movementRequests: requests.map(body => body.intent.direction), pageErrors: errors, screenshot: 'test-results/frontend-scene-recovery-smoke.png' }, null, 2));
  } else {
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
  }
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
