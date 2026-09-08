"""Story HTTP boundaries and media routes use temp files and fake services only."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.resources import ResourceError
from backend.stories import PLAN_SYSTEM, StoryManager
from test_app import auth, no_hardware, server
from test_stories import Assets, Client, Videos, plan, project, reference, uid


class RouteVideos(Videos):
    def __init__(self):
        super().__init__()
        self.loads, self.transfers = [], {}

    def get(self, rid):
        if rid not in self.records:
            raise ValueError('This take was not found.')
        return super().get(rid)

    def list(self, project_id=None):
        return [self.get(rid) for rid in self.records if not project_id or self.records[rid]['project_id'] == project_id]

    def _load(self, rid, filename):
        self.loads.append((rid, filename))
        assert filename == 'transfer.json'
        return copy.deepcopy(self.transfers[rid])


@pytest.fixture
def api_rig(server, monkeypatch):
    module, http, _ = server
    videos, assets, llm = RouteVideos(), Assets(), Client()
    calls, ending_calls, cached_calls, saved = [], [], [], {}

    def run_ai(model, operation):
        calls.append(model)
        return operation(model)

    resources = SimpleNamespace(run_ai=run_ai)
    monkeypatch.setattr(module, 'RESOURCES', resources)
    monkeypatch.setitem(module.SETTINGS, 'model', 'fake-route-vision')
    monkeypatch.setattr(module, 'video_manager', lambda: videos)
    monkeypatch.setattr(module, 'asset_manager', lambda: assets)

    def cached(run_id):
        cached_calls.append(run_id)
        videos.get(run_id)
        folder = module.DATA / 'video_runs' / run_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'playback.mp4'
        if not path.exists():
            path.write_bytes(b'ORIGINAL-SYNTHETIC-VIDEO')
        return path

    def ending(run_id):
        ending_calls.append(run_id)
        videos.get(run_id)
        item = reference('Verified final frame', 'pose', 'ending-frame')
        item.update(role='context', video_run_ending=run_id)
        folder = module.DATA / 'assets' / item['id']
        folder.mkdir(parents=True)
        (folder / 'source.png').write_bytes(b'SYNTHETIC-FINAL-FRAME')
        return item

    monkeypatch.setattr(module, 'cached_run_video', cached)
    monkeypatch.setattr(module, 'video_run_ending_image', ending)
    image_reads = []

    def image_data(asset_id):
        image_reads.append(asset_id)
        return 'data:image/png;base64,AA=='

    manager = StoryManager(module.DATA, lambda: videos, resources, lambda: llm,
                           lambda: copy.deepcopy(module.SETTINGS), lambda p: saved.update({p['id']: copy.deepcopy(p)}),
                           ending, image_data, lambda: assets, start_workers=False, poll_interval=0)
    monkeypatch.setattr(module, 'story_manager', lambda: manager)
    monkeypatch.setattr(module, 'image_data', image_data)
    result = SimpleNamespace(module=module, http=http, manager=manager, videos=videos, assets=assets, llm=llm,
                             resources=resources, calls=calls, ending_calls=ending_calls, cached_calls=cached_calls,
                             image_reads=image_reads, saved=saved, project=project())
    yield result
    manager.close()


def create_session(rig, *, source=None, **changes):
    body = {'request_id': uid(), 'project': rig.project, 'mode': 'game', 'player_name': 'Mira',
            **({'source_run_id': source} if source else {}), **changes}
    response = rig.http.post('/api/stories', headers=auth(rig.module), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def add_run(rig, *, source=None, overlap=None, operation='generate'):
    run = rig.videos.add(rig.project, source=source, operation=operation)
    rig.videos.records[run['id']].update(width=736, height=416, frames=124, duration=124 / 24)
    if overlap is not None:
        rig.videos.records[run['id']]['overlap_frames'] = overlap
    rig.videos.transfers[run['id']] = {'manifest': {'mmh3': {'source': source, 'overlap_frames': 39}}}
    return rig.videos.get(run['id'])


def fake_ffmpeg(monkeypatch, module, *, returncode=0):
    calls = []

    def execute(command, **kwargs):
        assert command[0] == 'ffmpeg'
        entry = {'command': list(command), 'options': kwargs}
        if 'concat' in command:
            entry['listing'] = Path(command[command.index('-i') + 1]).read_text('utf-8')
        calls.append(entry)
        if not returncode:
            Path(command[-1]).write_bytes(b'SYNTHETIC-ENCODED-VIDEO')
        return SimpleNamespace(returncode=returncode, stdout=b'', stderr=b'')

    monkeypatch.setattr(module.subprocess, 'run', execute)
    return calls


@pytest.mark.parametrize('path', ['/api/stories', '/api/stories/{id}/branch', '/api/stories/{id}/attach',
                                  '/api/stories/{id}/alternates',
                                  '/api/stories/{id}/turns', '/api/stories/{id}/turns/{id}/retry',
                                  '/api/video/runs/{id}/plan-continuation', '/api/asset-runs'])
def test_story_mutations_require_the_studio_session_token(api_rig, path):
    rig = api_rig
    response = rig.http.post(path.format(id=uid()), json={})
    assert response.status_code == 403 and 'session' in response.json()['detail']
    assert not rig.manager.records and not rig.calls and not rig.videos.queues and not rig.assets.requests


@pytest.mark.parametrize('path', ['/api/stories', '/api/stories/{id}', '/api/video/runs/{id}/scene',
                                  '/api/stories/{id}/video', '/api/video/runs/{id}/ending'])
def test_foreign_and_comfy_origins_cannot_read_story_or_media_routes(api_rig, path):
    rig = api_rig
    for origin in ('https://outside.invalid', 'http://127.0.0.1:8010'):
        response = rig.http.get(path.format(id=uid()), headers={'Origin': origin, 'X-H3-Token': rig.module.TOKEN})
        assert response.status_code == 403
    assert not rig.cached_calls and not rig.ending_calls and not rig.calls


def test_story_ids_and_turn_membership_errors_are_client_errors(api_rig):
    rig = api_rig
    first, second = create_session(rig), create_session(rig)
    turn = rig.http.post(f'/api/stories/{first["id"]}/turns', headers=auth(rig.module),
                         json={'request_id': uid(), 'message': 'I wait.'}).json()
    wrong_story = rig.http.post(f'/api/stories/{second["id"]}/turns/{turn["id"]}/cancel', headers=auth(rig.module), json={})
    assert wrong_story.status_code == 400 and 'not found' in wrong_story.json()['detail']
    assert rig.http.get('/api/stories/not-a-local-id').status_code == 400
    assert rig.http.get('/api/stories/' + uid()).status_code == 400
    assert rig.http.post('/api/stories', headers=auth(rig.module), json=[]).status_code == 422
    assert rig.manager.get(first['id'])['turns'][0]['status'] == 'planning'
    assert not rig.videos.queues


def test_create_and_turn_routes_preserve_request_receipts_and_hide_private_working_state(api_rig):
    rig = api_rig
    session = create_session(rig)
    body = {'request_id': uid(), 'message': 'I ask about the drawer.'}
    path = f'/api/stories/{session["id"]}/turns'
    first = rig.http.post(path, headers=auth(rig.module), json=body)
    repeated = rig.http.post(path, headers=auth(rig.module), json=body)
    assert first.status_code == repeated.status_code == 200 and first.json()['id'] == repeated.json()['id']
    rig.manager.process(session['id'], first.json()['id'])
    response = rig.http.get('/api/stories/' + session['id'])
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    public = response.json()
    assert 'base_project' not in public and 'action_requests' not in public and 'observed_by_run' not in public
    assert all(key not in public['turns'][0] for key in ('project', 'asset_specs', 'render_request_id', 'request_digest'))
    assert public['turns'][0]['video']['id'] == public['active_run_id']
    listed = rig.http.get('/api/stories').json()['stories']
    summary = next(item for item in listed if item['id'] == session['id'])
    assert summary['create_request_id'] == session['create_request_id']
    assert 'create_digest' not in summary and 'base_project' not in summary
    assert len(rig.videos.queues) == 1


def test_studio_continuation_uses_the_selected_snapshot_actual_ending_and_completed_lineage(api_rig):
    rig = api_rig
    opening = add_run(rig)
    endpoint = add_run(rig, source=opening['continuation_source'])
    alternate = add_run(rig, source=opening['continuation_source'], operation='reroll')
    alternate_project = rig.videos.projects[alternate['id']]
    alternate_project['story']['text'] = 'Nora holds the brass key by the window.'
    rig.llm.plans.append(plan(action='Nora places the brass key on the sill.'))
    response = rig.http.post(f'/api/video/runs/{alternate["id"]}/plan-continuation', headers=auth(rig.module),
                             json={'message': 'Decide what happens next.', 'duration': 5})
    assert response.status_code == 200, response.text
    assert response.json()['source_run_id'] == alternate['id']
    assert response.json()['plan']['action'] == 'Nora places the brass key on the sill.'
    assert rig.ending_calls == [alternate['id']]
    call = next(call for call in rig.llm.calls if call['system'] == PLAN_SYSTEM)
    context = json.loads(call['content'][0]['text'])
    assert context['mode'] == 'studio' and context['message'] == 'Decide what happens next.'
    assert context['completed_events_do_not_repeat'][-1]['action'] == alternate_project['story']['text']
    assert len(context['completed_events_do_not_repeat']) == 2
    assert [part['type'] for part in call['content']] == ['text', 'image_url', 'image_url', 'image_url']
    assert not rig.manager.records and not rig.videos.queues and not rig.assets.requests
    assert endpoint['id'] not in response.text


def test_studio_planner_follows_a_combined_film_to_its_verified_last_take(api_rig):
    rig = api_rig
    final = add_run(rig)
    joined = add_run(rig, operation='combine')
    rig.videos.records[joined['id']].update(continue_from_run_id=final['id'], continuation_source=None, can_continue=True)
    response = rig.http.post(f'/api/video/runs/{joined["id"]}/plan-continuation', headers=auth(rig.module),
                             json={'message': 'Let Nora answer.', 'duration': 5})
    assert response.status_code == 200 and response.json()['source_run_id'] == final['id']
    assert rig.ending_calls == [final['id']]


@pytest.mark.parametrize('body', [{'message': ''}, {'message': 'Next', 'duration': 15}, {'message': ['not text']},
                                  {'message': 'Next', 'duration': True}])
def test_invalid_studio_planner_requests_fail_before_image_or_inference_work(api_rig, body):
    rig = api_rig
    run = add_run(rig)
    response = rig.http.post(f'/api/video/runs/{run["id"]}/plan-continuation', headers=auth(rig.module), json=body)
    assert response.status_code == 400
    assert not rig.ending_calls and not rig.calls


@pytest.mark.parametrize('changes', [{'status': 'running'}, {'status': 'failed'}, {'continuation_source': None}])
def test_studio_planner_rejects_unfinished_or_uncontinuable_sources(api_rig, changes):
    rig = api_rig
    run = add_run(rig)
    rig.videos.records[run['id']].update(changes)
    response = rig.http.post(f'/api/video/runs/{run["id"]}/plan-continuation', headers=auth(rig.module),
                             json={'message': 'Continue.'})
    assert response.status_code == 400 and 'completed take' in response.json()['detail']
    assert not rig.ending_calls and not rig.calls and not rig.videos.queues


def test_ending_preview_returns_only_the_selected_final_frame_without_inference(api_rig):
    rig = api_rig
    run = add_run(rig)
    response = rig.http.get(f'/api/video/runs/{run["id"]}/ending')
    assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
    assert response.content == b'SYNTHETIC-FINAL-FRAME'
    assert rig.ending_calls == [run['id']] and not rig.calls and not rig.videos.queues


def test_planner_resource_conflicts_and_missing_assets_return_actionable_errors(api_rig, monkeypatch):
    rig = api_rig
    run = add_run(rig)
    path = f'/api/video/runs/{run["id"]}/plan-continuation'
    rig.llm.plans.append(plan(asset_requests=[{'name': 'Garden', 'prompt': 'A garden.', 'semantic_role': 'background',
                                               'person_name': '', 'prompt_tag': 'garden'}]))
    response = rig.http.post(path, headers=auth(rig.module), json={'message': 'Go to a garden.'})
    assert response.status_code == 400 and 'new images' in response.json()['detail']
    assert not rig.assets.requests and not rig.videos.queues
    def conflict(*args):
        raise ResourceError('Another render is active.')
    monkeypatch.setattr(rig.resources, 'run_ai', conflict)
    response = rig.http.post(path, headers=auth(rig.module), json={'message': 'Continue.'})
    assert response.status_code == 409 and 'active' in response.json()['detail']


def test_history_reads_never_load_transfer_files_or_prepare_playback(api_rig):
    rig = api_rig
    run = add_run(rig)
    session = create_session(rig, source=run['id'])
    for path in ('/api/stories', '/api/stories/' + session['id'], '/api/video/runs', '/api/video/runs/' + run['id']):
        assert rig.http.get(path).status_code == 200
    assert not rig.videos.loads and not rig.cached_calls and not rig.ending_calls and not rig.calls


def test_legacy_scene_trim_uses_saved_transfer_context_only_on_playback(api_rig, monkeypatch):
    rig = api_rig
    opening = add_run(rig)
    run = add_run(rig, source=opening['continuation_source'])
    calls = fake_ffmpeg(monkeypatch, rig.module)
    assert rig.http.get('/api/video/runs/' + run['id']).status_code == 200
    assert not calls and not rig.videos.loads
    path = f'/api/video/runs/{run["id"]}/scene'
    response = rig.http.get(path)
    assert response.status_code == 200 and response.headers['content-type'] == 'video/mp4'
    assert rig.videos.loads == [(run['id'], 'transfer.json')]
    command = calls[0]['command']
    assert command[command.index('-vf') + 1] == 'trim=start_frame=39,setpts=PTS-STARTPTS'
    assert command[command.index('-af') + 1] == 'atrim=start=1.625,asetpts=PTS-STARTPTS'
    assert command[command.index('-map_metadata') + 1] == '-1'
    assert rig.http.get(path).status_code == 200 and len(calls) == 1
    assert (rig.module.DATA / 'video_runs' / run['id'] / 'playback.mp4').read_bytes() == b'ORIGINAL-SYNTHETIC-VIDEO'
    assert not rig.videos.queues


@pytest.mark.parametrize('overlap', [0, 39, 90])
def test_new_records_use_their_recorded_context_without_loading_legacy_transfer(api_rig, monkeypatch, overlap):
    rig = api_rig
    run = add_run(rig, overlap=overlap)
    calls = fake_ffmpeg(monkeypatch, rig.module)
    response = rig.http.get(f'/api/video/runs/{run["id"]}/scene')
    assert response.status_code == 200 and not rig.videos.loads
    assert len(calls) == bool(overlap)
    if overlap:
        assert f'trim=start_frame={overlap},setpts=PTS-STARTPTS' in calls[0]['command']
    else:
        assert response.content == b'ORIGINAL-SYNTHETIC-VIDEO'


def test_legacy_opening_does_not_trim_a_leftover_overlap_setting(api_rig, monkeypatch):
    rig = api_rig
    run = add_run(rig)
    calls = fake_ffmpeg(monkeypatch, rig.module)
    response = rig.http.get(f'/api/video/runs/{run["id"]}/scene')
    assert response.status_code == 200 and response.content == b'ORIGINAL-SYNTHETIC-VIDEO'
    assert not calls


def test_failed_scene_preparation_returns_client_error_and_keeps_original_playable(api_rig, monkeypatch):
    rig = api_rig
    run = add_run(rig, overlap=39)
    fake_ffmpeg(monkeypatch, rig.module, returncode=1)
    response = rig.http.get(f'/api/video/runs/{run["id"]}/scene')
    assert response.status_code == 400 and 'original video' in response.json()['detail']
    original = rig.http.get(f'/api/video/runs/{run["id"]}/video')
    assert original.status_code == 200 and original.content == b'ORIGINAL-SYNTHETIC-VIDEO'


def test_story_export_uses_only_active_branch_clips_in_order_and_reuses_branch_cache(api_rig, monkeypatch):
    rig = api_rig
    opening = add_run(rig, overlap=0)
    unused = add_run(rig, source=opening['continuation_source'], overlap=39)
    alternate = add_run(rig, source=opening['continuation_source'], overlap=39, operation='reroll')
    session = create_session(rig, source=alternate['id'])
    calls = fake_ffmpeg(monkeypatch, rig.module)
    path = f'/api/stories/{session["id"]}/video'
    response = rig.http.get(path)
    assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
    listing = next(call['listing'] for call in calls if 'listing' in call)
    assert listing.splitlines() == [f"file '{opening['id']}-736x416.mp4'", f"file '{alternate['id']}-736x416.mp4'"]
    assert unused['id'] not in str(calls)
    assert any('scene-context-39.mp4' in str(call['command']) for call in calls)
    total = len(calls)
    assert rig.http.get(path).status_code == 200 and len(calls) == total
    branch = rig.http.post(f'/api/stories/{session["id"]}/branch', headers=auth(rig.module), json={'run_id': opening['id'], 'request_id': uid()})
    assert branch.status_code == 200
    assert rig.http.get(path).status_code == 200 and len(calls) == total + 1
    assert calls[-1]['listing'] == f"file '{opening['id']}-736x416.mp4'"
    assert not rig.calls and not rig.videos.queues


def test_empty_story_export_is_a_client_error_without_media_work(api_rig):
    rig = api_rig
    session = create_session(rig)
    response = rig.http.get(f'/api/stories/{session["id"]}/video')
    assert response.status_code == 400 and 'Generate a scene' in response.json()['detail']
    assert not rig.cached_calls and not rig.calls


def test_studio_alternate_route_preserves_pending_take_then_branches_to_its_finished_replacement(api_rig):
    rig = api_rig
    opening = rig.videos.add(rig.project)
    original = rig.videos.add(rig.project, source=opening['continuation_source'])
    alternate = rig.videos.add(rig.project, source=opening['continuation_source'],
                               operation='reroll', parent=original['id'], status='running')
    session = create_session(rig, mode='studio', source=original['id'])
    path = f'/api/stories/{session["id"]}/alternates'
    body = {'run_id': alternate['id'], 'original_run_id': original['id']}
    for _ in range(2):
        response = rig.http.post(path, headers=auth(rig.module), json=body)
        assert response.status_code == 200, response.text
        public = response.json()
        assert public['active_run_id'] == original['id']
        assert [clip['id'] for clip in public['clips']] == [opening['id'], original['id']]
        assert public['studio_alternates'] == {alternate['id']: {'original_run_id': original['id']}}
        assert [job['id'] for job in public['jobs']].count(alternate['id']) == 1
        assert next(job for job in public['jobs'] if job['id'] == alternate['id'])['status'] == 'running'
    branch_path = f'/api/stories/{session["id"]}/branch'
    receipt = {'run_id': alternate['id'], 'request_id': uid()}
    pending = rig.http.post(branch_path, headers=auth(rig.module), json=receipt)
    assert pending.status_code == 400 and 'finish' in pending.json()['detail']
    assert rig.manager.get(session['id'])['active_run_id'] == original['id']
    rig.videos.records[alternate['id']]['status'] = 'succeeded'
    response = rig.http.post(branch_path, headers=auth(rig.module), json=receipt)
    assert response.status_code == 200, response.text
    branched = response.json()
    assert [clip['id'] for clip in branched['clips']] == [opening['id'], alternate['id']]
    assert branched['branches'][session['active_branch_id']] == [opening['id'], original['id']]
    assert {job['id'] for job in branched['jobs']} == {opening['id'], original['id'], alternate['id']}
    assert len(branched['jobs']) == 3
    repeated = rig.http.post(branch_path, headers=auth(rig.module), json=receipt)
    assert repeated.status_code == 200 and repeated.json()['active_branch_id'] == branched['active_branch_id']
    assert len(repeated.json()['branches']) == 2
    assert not rig.calls and not rig.ending_calls and not rig.cached_calls and not rig.videos.queues


def test_studio_alternate_route_supports_a_reroll_of_an_already_selected_alternate(api_rig):
    rig = api_rig
    opening = rig.videos.add(rig.project)
    original = rig.videos.add(rig.project, source=opening['continuation_source'])
    session = create_session(rig, mode='studio', source=original['id'])
    parent = original
    for _ in range(3):
        alternate = rig.videos.add(rig.project, source=opening['continuation_source'],
                                   operation='reroll', parent=parent['id'])
        registered = rig.http.post(f'/api/stories/{session["id"]}/alternates', headers=auth(rig.module),
                                   json={'run_id': alternate['id'], 'original_run_id': parent['id']})
        assert registered.status_code == 200, registered.text
        assert registered.json()['active_run_id'] == parent['id']
        branch = rig.http.post(f'/api/stories/{session["id"]}/branch', headers=auth(rig.module),
                               json={'run_id': alternate['id'], 'request_id': uid()})
        assert branch.status_code == 200, branch.text
        assert [clip['id'] for clip in branch.json()['clips']] == [opening['id'], alternate['id']]
        parent = alternate
    assert not rig.calls and not rig.videos.queues


@pytest.mark.parametrize('invalid', ['operation', 'parent', 'unrelated_story', 'game', 'missing_run',
                                    'missing_original', 'missing_story', 'bad_id'])
def test_studio_alternate_route_returns_client_errors_without_registering_invalid_takes(api_rig, invalid):
    rig = api_rig
    opening = rig.videos.add(rig.project)
    unrelated = rig.videos.add(rig.project)
    session = create_session(rig, mode='game' if invalid == 'game' else 'studio', source=opening['id'])
    source_id = unrelated['id'] if invalid == 'unrelated_story' else opening['id']
    alternate = rig.videos.add(rig.project, operation='generate' if invalid == 'operation' else 'reroll',
                               parent=unrelated['id'] if invalid == 'parent' else source_id)
    body = {'run_id': alternate['id'], 'original_run_id': source_id}
    if invalid == 'missing_run':
        body['run_id'] = uid()
    elif invalid == 'missing_original':
        body['original_run_id'] = uid()
    elif invalid == 'bad_id':
        body['run_id'] = '../another-story'
    story_id = uid() if invalid == 'missing_story' else session['id']
    before = copy.deepcopy(rig.manager.records[session['id']])
    response = rig.http.post(f'/api/stories/{story_id}/alternates', headers=auth(rig.module), json=body)
    assert response.status_code == 400, response.text
    assert rig.manager.records[session['id']] == before
    assert not rig.calls and not rig.videos.queues and not rig.ending_calls
