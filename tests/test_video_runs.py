"""Direct-render lifecycle tests use synthetic projects and a fake Comfy server."""
import copy
import json
import threading
import uuid
from pathlib import PurePosixPath

import httpx
import pytest

from backend.comfy_transfer import build_transfer
from backend.video_runs import VideoRunError, VideoRunManager, _submission_error
from test_comfy_transfer import FakeComfy, library, make_project, assert_ui_api_parity
from test_mmh3_transfer import schema, find, _resolve_control


def uid():
    return str(uuid.uuid4())


class Server:
    def __init__(self, schema):
        self.schema = schema
        self.calls, self.submitted, self.histories = [], [], {}
        self.pending, self.running = [], []
        self.timeout_post = False
        self.reject_post = False
        self.read_fail = False
        self.cards = {}
        self.guard_held = False

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == 'POST' and path == '/prompt':
            assert self.guard_held, 'GPU handoff lock must cover submission'
            if self.reject_post:
                return httpx.Response(400, json={'error': {'type': 'prompt_invalid'}})
            body = json.loads(request.content)
            ident = body.get('prompt_id', uid())
            body['prompt_id'] = ident
            self.submitted.append(body)
            self.pending.append([1, ident, {'PRIVATE_PROMPT_MUST_NOT_BE_READ': True}, {'client_id': body['client_id']}, []])
            if self.timeout_post:
                raise httpx.ReadTimeout('Response lost', request=request)
            return httpx.Response(200, json={'prompt_id': ident, 'number': 1, 'node_errors': {}})
        if request.method == 'GET' and path == '/queue':
            return httpx.Response(200, json={'queue_running': self.running, 'queue_pending': self.pending})
        if path == '/object_info':
            return httpx.Response(200, json=self.schema)
        if path.startswith('/history/'):
            if self.read_fail:
                raise httpx.ConnectError('Offline', request=request)
            ident = path.rsplit('/', 1)[-1]
            assert ident in {item['prompt_id'] for item in self.submitted}
            return httpx.Response(200, json={ident: self.histories[ident]} if ident in self.histories else {})
        if path == '/mmh3_media/file_info':
            return httpx.Response(200, json=self.cards[request.url.params['file']])
        raise AssertionError(f'Unexpected server request: {request.method} {path}')

    def finish(self, *, bad_video=False, bad_state=False, error=False):
        submission = self.submitted[-1]
        ident, graph = submission['prompt_id'], submission['prompt']
        manifest = submission['extra_data']['extra_pnginfo']['workflow']['extra']['h3_prompt_studio']
        self.pending.clear()
        self.running.clear()
        outputs = {}
        for key, node in graph.items():
            if node['class_type'] == 'SaveVideo':
                prefix = PurePosixPath(node['inputs']['filename_prefix'])
                outputs[key] = {'images': [{'filename': ('../escape.mp4' if bad_video else prefix.name + '_00001_.mp4'),
                                            'subfolder': str(prefix.parent), 'type': 'output'}]}
            if node['class_type'] == 'MMH3Save':
                relative = node['inputs']['filename_prefix'] + '_00001_.mmh3'
                outputs[key] = {'mmh3_saved': [{'file': 'output::' + relative}]}
                self.cards['output::' + relative] = {'has': {'latent': not bad_state}, 'geometry': {
                    'width': manifest['width'], 'height': manifest['height'], 'frames': manifest['frames'], 'fps': 24}}
                self.schema['MMH3Load']['input']['required']['file'][0].append('output::' + relative)
        self.histories[ident] = {'outputs': outputs, 'status': {'completed': True, 'status_str': 'error' if error else 'success',
                                   'messages': [['execution_start', {'timestamp': 1000}], ['execution_success', {'timestamp': 31000}]]}}


class Resources:
    def __init__(self, server):
        self.server = server
        self.lock = threading.Lock()
        self.handoffs = 0
        self.busy = False

    def assert_idle(self):
        if self.busy or self.server.pending or self.server.running:
            raise VideoRunError('Another ComfyUI job is active.')

    def prepare_h3_then(self, operation):
        assert self.lock.acquire(blocking=False)
        try:
            self.handoffs += 1
            self.server.guard_held = True
            return operation()
        finally:
            self.server.guard_held = False
            self.lock.release()


@pytest.fixture
def rig(tmp_path, library, schema):
    server = Server(schema)
    resources = Resources(server)
    calls = []
    files, assets = library

    class BuildComfy(FakeComfy):
        def get(self, url, **kwargs):
            if url.endswith('/mmh3_media/file_info'):
                return httpx.Response(200, json=server.cards[kwargs['params']['file']], request=httpx.Request('GET', url))
            return super().get(url, **kwargs)

    def prepare(project, prompt):
        calls.append(copy.deepcopy(project))
        source = project.get('comfy_render', {}).get('continuation_source')
        if source:
            choice = 'output::' + source
            if choice not in schema['MMH3Load']['input']['required']['file'][0]:
                schema['MMH3Load']['input']['required']['file'][0].append(choice)
        return build_transfer(project, prompt, project.get('comfy_render', {}), lambda asset: files[asset['id']],
                              client=BuildComfy(schema), template_dir=tmp_path / 'no-template')

    manager = VideoRunManager(tmp_path / 'data', prepare, resources, client_factory=server.client, start_workers=False)
    project = make_project(assets[:1])
    project['comfy_render'] = {'seed': 0, 'save_mmh3': True, 'resolution': '0.3'}
    return manager, project, server, resources, calls


def complete(rig):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.finish()
    return manager.refresh(job['id'])


def test_generate_is_background_ready_idempotent_and_exact(rig, schema):
    manager, project, server, resources, prepared = rig
    request = uid()
    first = manager.submit(request, project, 'Exact private compiled prompt.')
    assert first['status'] == 'preparing' and not server.submitted
    assert manager.submit(request, project, 'Exact private compiled prompt.')['id'] == first['id']
    manager.process(first['id'])
    manager.process(first['id'])
    assert len(server.submitted) == resources.handoffs == len(prepared) == 1
    transfer = manager._load(first['id'], 'transfer.json')
    assert transfer['prompt'] == server.submitted[0]['prompt']
    assert_ui_api_parity(transfer, schema)
    packet = find(transfer['prompt'], 'MMH3Create')[1]
    assert packet['inputs']['prompt'] == 'Exact private compiled prompt.'
    assert manager.get(first['id'])['status'] == 'queued'
    with pytest.raises(VideoRunError, match='different video'):
        manager.submit(request, project, 'Changed payload.')
    assert all(path != '/history' for _, path in server.calls)


def test_own_result_has_safe_video_state_snapshot_and_no_private_payload(rig):
    manager, project, server, _, _ = rig
    result = complete(rig)
    assert result['status'] == 'succeeded' and result['server_execution_seconds'] == 30
    assert result['continuation_source'].endswith('.mmh3') and result['can_continue']
    assert result['video_url'] == '/api/video/runs/' + result['id'] + '/video'
    assert result['download_url'].endswith('?download=1')
    descriptor = manager.media(result['id'])
    assert descriptor['type'] == 'output' and descriptor['mime_type'] == 'video/mp4'
    assert descriptor['subfolder'].endswith(result['id'])
    assert manager.snapshot(result['id'])['comfy_render']['seed'] == 0
    project['story'] = {'text': 'Changed later'}
    assert manager.snapshot(result['id']).get('story') != project['story']
    public = json.dumps(manager.list(project_id=project['id']))
    assert 'Exact private compiled prompt.' not in public and 'comfy_image' not in public and 'prompt_id' not in public


def test_actual_manifest_steps_and_frames_are_persisted_for_public_take_details(rig):
    manager, project, server, resources, _ = rig
    project['comfy_render']['steps'] = 16
    job = manager.submit(uid(), project, 'A neutral paper lantern glows.')
    assert job['steps'] is None and job['frames'] is None
    manager.process(job['id'])
    manifest = manager._load(job['id'], 'transfer.json')['manifest']
    assert manifest['steps'] == 16
    assert type(manifest['frames']) is int and manifest['frames'] > 0
    actual = manager.get(job['id'])
    assert actual['steps'] == manifest['steps'] and actual['frames'] == manifest['frames']
    server.finish()
    manager.refresh(job['id'])
    restarted = VideoRunManager(manager.directory.parent, manager.prepare, resources,
                                client_factory=server.client, start_workers=False)
    # Details remain available from the record without reading the graph again.
    restarted._load = lambda *args: pytest.fail('Public take details must not re-read a transfer.')
    restored = restarted.get(job['id'])
    assert restored['steps'] == 16 and restored['frames'] == manifest['frames']
    assert restarted.list()[0]['frames'] == manifest['frames']


def test_legacy_take_details_do_not_infer_steps_or_frames(rig):
    manager, _, _, resources, _ = rig
    job = complete(rig)
    record = manager.records[job['id']]
    record.pop('steps')
    record.pop('frames')
    manager._save(record)
    restarted = VideoRunManager(manager.directory.parent, manager.prepare, resources,
                                client_factory=lambda: pytest.fail('No Comfy connection needed.'), start_workers=False)
    restarted._load = lambda *args: pytest.fail('Do not infer legacy details from another file.')
    restored = restarted.get(job['id'])
    assert restored['steps'] is None and restored['frames'] is None


def test_reroll_reuses_original_graph_without_upload_or_prompt_generation(rig, schema):
    manager, project, server, _, prepared = rig
    first = complete(rig)
    original = manager._load(first['id'], 'transfer.json')
    request = uid()
    variation = manager.reroll(request, first['id'])
    assert variation['seed'] == 1
    assert manager.reroll(request, first['id'])['seed'] == 1
    manager.process(variation['id'])
    fixed = manager._load(variation['id'], 'transfer.json')
    assert len(prepared) == 1
    assert fixed['manifest']['images'] == original['manifest']['images']
    assert _resolve_control(fixed['prompt'], find(fixed['prompt'], 'RandomNoise')[1]['inputs']['noise_seed'], schema) == 1
    assert manager.snapshot(variation['id'])['comfy_render']['seed'] == 1
    assert fixed['manifest']['loras'] == original['manifest']['loras']
    assert fixed['manifest']['output_prefix'] != original['manifest']['output_prefix']
    assert_ui_api_parity(fixed, schema)
    server.finish()
    manager.refresh(variation['id'])
    second_variation = manager.reroll(uid(), first['id'])
    assert second_variation['seed'] == 2
    assert manager._load(first['id'], 'transfer.json') == original


def test_timeout_never_reposts_and_recovers_only_own_client_id(rig):
    manager, project, server, _, _ = rig
    server.timeout_post = True
    request = uid()
    job = manager.submit(request, project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    assert manager.get(job['id'])['status'] == 'uncertain'
    manager.submit(request, project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    assert len(server.submitted) == 1
    server.pending.insert(0, [0, uid(), {'DO_NOT_READ': True}, {'client_id': 'someone-else'}, []])
    assert manager.refresh(job['id'])['status'] == 'queued'
    assert manager.records[job['id']]['prompt_id'] == server.submitted[0]['prompt_id']
    assert len(server.submitted) == 1


def test_busy_admission_and_gpu_queue_race_never_interrupt_or_queue(rig):
    manager, project, server, resources, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    with pytest.raises(VideoRunError, match='already active'):
        manager.submit(uid(), project, 'Another prompt.')
    resources.busy = True
    manager.process(job['id'])
    assert manager.get(job['id'])['status'] == 'failed' and not server.submitted
    assert resources.handoffs == 0
    assert not any(path in ('/interrupt', '/free') for _, path in server.calls)


@pytest.mark.parametrize('case', ['bad_video', 'bad_state', 'error'])
def test_invalid_result_never_exposes_unowned_media_or_unverified_state(rig, case):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.finish(**{case: True})
    result = manager.refresh(job['id'])
    if case == 'bad_state':
        assert result['status'] == 'succeeded' and result['video_url'] and result['warning']
        assert result['continuation_source'] is None
    else:
        assert result['status'] == 'failed' and result['video_url'] is None
        with pytest.raises(VideoRunError, match='not available'):
            manager.media(job['id'])


def test_read_failure_preserves_running_job_and_does_not_retry(rig):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.running, server.pending = server.pending, []
    assert manager.refresh(job['id'])['status'] == 'running'
    server.read_fail = True
    assert manager.refresh(job['id'])['status'] == 'running'
    assert len(server.submitted) == 1


def test_restart_preserves_idempotency_and_recovers_without_submission(rig):
    manager, project, server, resources, _ = rig
    request = uid()
    job = manager.submit(request, project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    restarted = VideoRunManager(manager.directory.parent, manager.prepare, resources, client_factory=server.client, start_workers=False)
    assert restarted.submit(request, project, 'Exact private compiled prompt.')['id'] == job['id']
    restarted.process(job['id'])
    assert len(server.submitted) == 1
    server.finish()
    assert restarted.refresh(job['id'])['status'] == 'succeeded'
    record = json.loads((manager.directory / job['id'] / 'record.json').read_text())
    assert 'processing' not in record and 'refreshing' not in record


def test_explicit_continuation_binds_exact_finished_take_and_chain(rig):
    manager, project, server, _, _ = rig
    first = complete(rig)
    follow = manager.snapshot(first['id'])
    follow['comfy_render']['continuation_source'] = 'wrong-file.mmh3'
    with pytest.raises(VideoRunError, match='exact saved state'):
        manager.submit(uid(), follow, 'Future action.', parent_run_id=first['id'])
    follow['comfy_render']['continuation_source'] = first['continuation_source']
    next_job = manager.submit(uid(), follow, 'Future action.', parent_run_id=first['id'])
    assert next_job['operation'] == 'continue'
    manager.process(next_job['id'])
    assert manager.get(next_job['id'])['status'] == 'queued'
    server.finish()
    finished = manager.refresh(next_job['id'])
    assert finished['status'] == 'succeeded'
    assert finished['continuation_source'] != first['continuation_source']
    assert [entry['run']['id'] for entry in manager._chain(next_job['id'])] == [first['id'], next_job['id']]
    # A reroll's parent pointer denotes its variation source, while its actual
    # continuation state still points to the original first take.
    variant = manager.reroll(uid(), next_job['id'])
    manager.process(variant['id'])
    server.finish()
    manager.refresh(variant['id'])
    assert [entry['run']['id'] for entry in manager._chain(variant['id'])] == [first['id'], variant['id']]


def test_lost_response_can_recover_completed_own_reserved_uuid_without_queue_match(rig):
    manager, project, server, _, _ = rig
    server.timeout_post = True
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    assert manager.get(job['id'])['status'] == 'uncertain'
    assert server.submitted[0]['prompt_id'] == job['id']
    server.finish()
    assert manager.refresh(job['id'])['status'] == 'succeeded'
    assert len(server.submitted) == 1


def test_validation_rejection_is_terminal_and_same_request_does_not_retry(rig):
    manager, project, server, _, _ = rig
    server.reject_post = True
    ident = uid()
    manager.submit(ident, project, 'Exact private compiled prompt.')
    manager.process(ident)
    assert manager.get(ident)['status'] == 'failed'
    manager.submit(ident, project, 'Exact private compiled prompt.')
    manager.process(ident)
    assert [path for method, path in server.calls if method == 'POST'] == ['/prompt']


def test_queue_becoming_busy_during_preparation_stops_before_gpu_handoff(rig):
    manager, project, server, resources, _ = rig
    original_prepare = manager.prepare
    def prepare(project, prompt):
        transfer = original_prepare(project, prompt)
        server.pending.append([1, uid(), {'OTHER': True}, {'client_id': 'someone-else'}, []])
        return transfer
    manager.prepare = prepare
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    assert manager.get(job['id'])['status'] == 'failed'
    assert not server.submitted and resources.handoffs == 0


def test_combine_uses_exact_successful_chain_and_normal_idempotent_queue(rig, schema):
    from test_video_join import schema as join_schema
    manager, project, server, resources, prepared = rig
    schema['MMH3H3LatentStitch'] = join_schema.__wrapped__()['MMH3H3LatentStitch']
    first = complete(rig)
    follow = manager.snapshot(first['id'])
    follow['comfy_render']['continuation_source'] = first['continuation_source']
    second = manager.submit(uid(), follow, 'Future action.', parent_run_id=first['id'])
    manager.process(second['id'])
    server.finish()
    assert manager.refresh(second['id'])['status'] == 'succeeded'
    job = manager.combine(uid(), second['id'])
    assert manager.combine(job['request_id'], second['id'])['id'] == job['id']
    manager.process(job['id'])
    assert manager.get(job['id'])['status'] == 'queued'
    transfer = manager._load(job['id'], 'transfer.json')
    assert transfer['manifest']['no_diffusion'] is True and transfer['manifest']['frames'] == 209
    assert len(prepared) == 2 and len(server.submitted) == 3 and resources.handoffs == 3
    assert not any(node['class_type'] in ('RandomNoise', 'SamplerCustomAdvanced', 'MMH3Save') for node in transfer['prompt'].values())
    assert_ui_api_parity(transfer, schema)
    server.finish()
    result = manager.refresh(job['id'])
    assert result['status'] == 'succeeded' and result['video_url']
    assert result['operation'] == 'combine' and result['continuation_source'] is None
    assert result['seed'] is None
    assert manager.snapshot(job['id'])['comfy_render']['steps'] == 8
    assert result['can_continue'] and not result['can_reroll']
    assert result['continue_from_run_id'] == second['id']
    assert 'continue_from_run_id' not in manager.get(second['id'])
    # Combined files have no state of their own. Only a still-verified final
    # individual take may supply the continuation target.
    parent = manager.records[second['id']]
    for key, invalid in [('status', 'failed'), ('continuation_source', None),
                         ('video', None), ('kind', 'combine'), ('project_id', uid()),
                         ('comfy_url', 'http://127.0.0.1:9999')]:
        original = parent.get(key)
        parent[key] = invalid
        unavailable = manager.get(job['id'])
        assert not unavailable['can_continue']
        assert 'continue_from_run_id' not in unavailable
        parent[key] = original
    manager.records[job['id']]['parent_run_id'] = uid()
    assert not manager.get(job['id'])['can_continue']


def test_restart_and_list_only_ui_resume_read_only_monitors(rig, monkeypatch):
    manager, project, server, resources, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    wakes = []
    monkeypatch.setattr(VideoRunManager, '_spawn', lambda self, ident, monitor_only=False: wakes.append((ident, monitor_only)))
    restarted = VideoRunManager(manager.directory.parent, manager.prepare, resources, client_factory=server.client)
    assert wakes == [(job['id'], True)]
    assert restarted.list(project['id'])[0]['status'] == 'queued'
    assert wakes[-1] == (job['id'], True)
    assert len(server.submitted) == 1


def test_own_runtime_error_is_concise_and_never_exposes_raw_inputs_or_other_job(rig):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.finish(error=True)
    server.histories[job['id']]['status']['messages'].extend([
        ['execution_error', {'prompt_id': job['id'], 'node_type': 'SamplerCustomAdvanced',
                             'exception_message': '\u001b[31mCUDA out of memory\nRequested 2 GiB\u001b[0m',
                             'current_inputs': {'prompt': 'DO NOT EXPOSE RAW INPUTS'},
                             'traceback': ['DO NOT EXPOSE STACK TRACE']}],
        ['execution_error', {'prompt_id': uid(), 'node_type': 'OtherJob', 'exception_message': 'OTHER JOB SECRET'}],
    ])
    result = manager.refresh(job['id'])
    assert result['status'] == 'failed'
    assert result['error'] == 'SamplerCustomAdvanced: CUDA out of memory Requested 2 GiB Try a lower resolution or a shorter clip.'
    assert 'DO NOT EXPOSE' not in json.dumps(result) and 'OTHER JOB SECRET' not in json.dumps(result)
    assert len(server.submitted) == 1


def test_submission_error_uses_node_messages_only_with_bounds():
    graph = {'7': {'class_type': 'LoraLoaderModelOnly', 'inputs': {'secret': 'DO NOT EXPOSE GRAPH'}}}
    reply = {'node_errors': {'7': {'errors': [{'message': 'Selected LoRA is not available.',
              'details': 'DO NOT EXPOSE RAW DETAILS', 'extra_info': {'received_value': 'DO NOT EXPOSE RECEIVED VALUE'}}]}},
             'prompt': 'DO NOT EXPOSE PROMPT'}
    assert _submission_error(reply, graph, 400) == 'LoraLoaderModelOnly: Selected LoRA is not available.'
    reply['node_errors']['7']['errors'][0]['message'] = 'Failure ' * 2000
    result = _submission_error(reply, graph, 400)
    assert len(result) <= 701 and 'DO NOT EXPOSE' not in result


def test_explicit_check_releases_missing_run_without_interrupt_delete_or_resubmit(rig):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.pending.clear()  # The server restarted and lost its in-memory queue/history.
    manager._save(manager.records[job['id']], status='uncertain')
    before = len(server.submitted)
    resolved = manager.resolve_missing(job['id'])
    assert resolved['status'] == 'failed' and resolved['stage'] == 'Previous request released; no active Comfy job found'
    assert len(server.submitted) == before
    assert not any(path in ('/interrupt', '/free') or (method == 'POST' and path != '/prompt') for method, path in server.calls)
    assert manager.submit(uid(), project, 'A new explicit request.')['status'] == 'preparing'


def test_explicit_check_recovers_an_actual_result_or_keeps_active_own_job(rig):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    manager._save(manager.records[job['id']], status='uncertain')
    assert manager.resolve_missing(job['id'])['status'] == 'queued'
    server.running, server.pending = server.pending, []
    assert manager.resolve_missing(job['id'])['status'] == 'running'
    server.finish()
    assert manager.resolve_missing(job['id'])['status'] == 'succeeded'
    assert len(server.submitted) == 1


def test_explicit_check_refuses_unverifiable_server_and_preserves_reservation(rig):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    manager._save(manager.records[job['id']], status='uncertain')
    server.read_fail = True
    with pytest.raises(VideoRunError, match='could not verify'):
        manager.resolve_missing(job['id'])
    assert manager.get(job['id'])['status'] == 'uncertain'
    with pytest.raises(VideoRunError, match='already active'):
        manager.submit(uid(), project, 'Do not double queue.')
    assert len(server.submitted) == 1


@pytest.mark.parametrize('recovery', ['refresh', 'resolve_missing'])
def test_windows_savevideo_paths_recover_existing_success_without_rerender(rig, recovery):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.finish()
    history = server.histories[job['id']]
    for output in history['outputs'].values():
        for item in output.get('images', []):
            item['subfolder'] = item['subfolder'].replace('/', '\\')
    manager._save(manager.records[job['id']], status='failed', stage='Video output unavailable',
                  error='The older output parser rejected Windows separators.')
    result = getattr(manager, recovery)(job['id'])
    assert result['status'] == 'succeeded' and result['continuation_source']
    descriptor = manager.media(job['id'])
    assert '\\' not in descriptor['subfolder'] and descriptor['filename'] == 'video_00001_.mp4'
    assert len(server.submitted) == 1


def test_windows_directory_normalization_still_rejects_traversal(rig):
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.finish()
    for output in server.histories[job['id']]['outputs'].values():
        for item in output.get('images', []):
            item['subfolder'] = 'h3_prompt_studio\\runs\\' + job['id'] + '\\..\\another-run'
    assert manager.refresh(job['id'])['status'] == 'failed'
    with pytest.raises(VideoRunError, match='not available'):
        manager.media(job['id'])


@pytest.mark.parametrize('future', [False, True])
def test_recovered_elapsed_uses_actual_completion_time_or_prior_failure_time(rig, monkeypatch, future):
    from backend import video_runs
    manager, project, server, _, _ = rig
    job = manager.submit(uid(), project, 'Exact private compiled prompt.')
    manager.process(job['id'])
    server.finish()
    created = manager.records[job['id']]['created_at']
    monkeypatch.setattr(video_runs.time, 'time', lambda: created + 300)
    manager._save(manager.records[job['id']], status='failed', stage='Video output unavailable', finished_at=created + 85)
    messages = server.histories[job['id']]['status']['messages']
    messages[0][1]['timestamp'] = (created + 1) * 1000
    messages[1][1]['timestamp'] = (created + (900 if future else 80)) * 1000
    recovered = manager.refresh(job['id'])
    assert recovered['status'] == 'succeeded'
    assert recovered['elapsed_seconds'] == pytest.approx(85 if future else 80)
