"""CPU-only image job contracts; every ComfyUI request uses MockTransport."""
import copy
import io
import json
import threading
import uuid

import httpx
import pytest
from PIL import Image

from backend.asset_runs import AssetRunError, AssetRunManager, ENCODER, MODELS, NODES, VAE
from backend.resources import ResourceError


def uid():
    return str(uuid.uuid4())


def spec(**changes):
    return {'prompt': 'A brass key on a neutral background, one object.', 'name': 'Brass key',
            'semantic_role': 'object', 'person_id': uid(), 'prompt_tag': 'brass-key',
            'model': MODELS[0], 'width': 512, 'height': 512, 'seed': 12345, **changes}


class Server:
    def __init__(self):
        self.info = {name: {'input': {'required': {}}} for name in NODES}
        for node, field, choices in [('UNETLoader', 'unet_name', list(MODELS) + ['unrelated-private.safetensors']),
                                     ('CLIPLoader', 'clip_name', [ENCODER]), ('CLIPLoader', 'type', ['lumina2']),
                                     ('VAELoader', 'vae_name', [VAE]), ('KSampler', 'sampler_name', ['res_multistep']),
                                     ('KSampler', 'scheduler', ['simple'])]:
            self.info[node]['input']['required'][field] = [choices]
        self.pending, self.running, self.history, self.posts, self.requests = [], [], {}, [], []
        self.lost_response = False
        self.rejected = None
        self.guard_held = False
        self.manager = None
        data = io.BytesIO()
        Image.new('RGB', (512, 512), 'tan').save(data, format='PNG')
        self.image = data.getvalue()

    def handle(self, request):
        path = request.url.path
        self.requests.append((request.method, path))
        assert request.url.host == '127.0.0.1' and request.url.port == 8010
        if path == '/object_info':
            return httpx.Response(200, json=self.info)
        if path == '/queue' and request.method == 'GET':
            return httpx.Response(200, json={'queue_pending': self.pending, 'queue_running': self.running})
        if path == '/queue' and request.method == 'POST':
            payload = json.loads(request.content)
            assert set(payload) == {'delete'}  # Never clear or interrupt other work.
            self.pending[:] = [entry for entry in self.pending if entry[1] not in payload['delete']]
            return httpx.Response(200, json={})
        if path.startswith('/history/'):
            ident = path.rsplit('/', 1)[1]
            return httpx.Response(200, json={ident: self.history[ident]} if ident in self.history else {})
        if path == '/view':
            return httpx.Response(200, content=self.image, headers={'Content-Type': 'image/png'})
        if path == '/prompt':
            assert self.guard_held
            payload = json.loads(request.content)
            self.posts.append(payload)
            record = json.loads((self.manager.directory / payload['prompt_id'] / 'record.json').read_text(encoding='utf-8'))
            assert record['submission_intent'] and record['status'] == 'uncertain'
            assert (self.manager.directory / payload['prompt_id'] / 'graph.json').exists()
            if self.rejected:
                return httpx.Response(self.rejected, json={'error': {'message': 'private input omitted'}})
            self.pending.append([1, payload['prompt_id'], {'private-unrelated-graph': 'do not inspect'},
                                 {'client_id': payload['client_id']}, []])
            if self.lost_response:
                raise httpx.ReadTimeout('Accepted, response lost')
            return httpx.Response(200, json={'prompt_id': payload['prompt_id']})
        raise AssertionError(f'Unexpected network operation: {request.method} {path}')

    def finish(self, ident, **changes):
        self.pending[:] = [entry for entry in self.pending if entry[1] != ident]
        self.running[:] = [entry for entry in self.running if entry[1] != ident]
        image = {'filename': 'image_00001_.png', 'subfolder': f'h3_prompt_studio/assets/{ident}', 'type': 'output', **changes}
        self.history[ident] = {'status': {'completed': True, 'status_str': 'success'}, 'outputs': {'10': {'images': [image]}}}


class Resources:
    def __init__(self, server):
        self.server = server
        self.handoffs = []
        self.before_submit = None

    def assert_idle(self):
        if self.server.pending or self.server.running:
            raise ResourceError('ComfyUI has running or queued work.')
        return [{'url': 'http://127.0.0.1:8010', 'online': True, 'pending': 0, 'running': 0}]

    def prepare_comfy_then(self, kind, operation):
        self.handoffs.append(kind)
        self.assert_idle()
        self.server.guard_held = True
        try:
            if self.before_submit:
                self.before_submit()
            return operation()
        finally:
            self.server.guard_held = False


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # A new unmocked HTTP client must never reach a real local server.
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **k: pytest.fail('Real network forbidden'))
    server = Server()
    resources = Resources(server)
    imports = []
    settings = {'comfy_urls': ['http://127.0.0.1:8010']}
    def store(data, name, content_type):
        imports.append((data, name, content_type))
        return {'id': uid(), 'name': name, 'width': 512, 'height': 512, 'media_type': 'image',
                'role': 'reference_image', 'observation': '', 'approved_observation': '', 'enabled': True}
    def create():
        manager = AssetRunManager(tmp_path, resources, lambda: settings, store,
                                  client_factory=lambda: httpx.Client(transport=httpx.MockTransport(server.handle)),
                                  start_workers=False)
        server.manager = manager
        return manager
    return create(), server, resources, imports, settings, create


def test_options_only_reports_compatible_turbo_choices(setup):
    manager, server, *_ = setup
    options = manager.options()
    assert options['ready'] and options['models'] == list(MODELS)
    assert 'unrelated-private' not in json.dumps(options)
    assert not server.posts
    del server.info['ModelSamplingAuraFlow']
    options = manager.options()
    assert not options['ready'] and options['models'] == []
    assert 'ModelSamplingAuraFlow' in options['servers'][0]['missing']


@pytest.mark.parametrize('changes', [
    {'seed': None}, {'seed': True}, {'seed': -1}, {'seed': 2**53}, {'width': 1040}, {'width': 511},
    {'height': True}, {'prompt': ''}, {'prompt': 'x' * 6001}, {'prompt_tag': 'Bad tag'},
    {'person_id': '../../outside'}, {'semantic_role': 'unknown'}, {'model': 'unrelated-private.safetensors'},
    {'comfy_url': 'http://remote.example:8010'}, {'workflow': {'evil': 'node'}},
])
def test_bad_spec_cannot_create_or_submit_a_job(setup, changes):
    manager, server, *_ = setup
    with pytest.raises(AssetRunError):
        manager.submit(uid(), spec(**changes))
    assert not server.requests and not list(manager.directory.iterdir())


@pytest.mark.parametrize('origin', ['https://127.0.0.1:8010', 'http://example.com:8010',
                                    'http://127.0.0.1:8010/api', 'http://u:p@127.0.0.1:8010',
                                    'http://127.0.0.1:8010?x=1'])
def test_only_configured_loopback_origins_are_allowed(setup, origin):
    manager, server, _, _, settings, _ = setup
    settings['comfy_urls'] = [origin]
    with pytest.raises(AssetRunError):
        manager.submit(uid(), spec())
    assert not server.requests


def test_complete_fixed_seed_native_graph_and_import_binding_once(setup):
    manager, server, resources, imports, *_ = setup
    ident, request = uid(), spec(model=MODELS[1])
    assert manager.submit(ident, request)['status'] == 'preparing'
    request['prompt'] = 'caller mutated object'
    assert manager.process(ident)['status'] == 'queued'
    assert resources.handoffs == ['image'] and len(server.posts) == 1
    graph = server.posts[0]['prompt']
    sampler = graph['8']['inputs']
    assert sampler['seed'] == 12345 and sampler['steps'] == 8 and sampler['cfg'] == 1
    assert sampler['sampler_name'] == 'res_multistep' and sampler['scheduler'] == 'simple'
    assert graph['2']['inputs'] == {'clip_name': ENCODER, 'type': 'lumina2', 'device': 'default'}
    assert graph['4']['inputs']['shift'] == 3
    assert graph['7']['class_type'] == 'EmptySD3LatentImage'
    assert graph['6']['class_type'] == 'ConditioningZeroOut'
    assert graph['1']['inputs']['unet_name'] == MODELS[1]
    assert graph['5']['inputs']['text'] != request['prompt']
    server.finish(ident)
    result = manager.refresh(ident)
    assert result['status'] == 'succeeded' and result['asset_id'] == result['asset']['id']
    assert result['asset']['person_id'] == request['person_id']
    assert result['asset']['simple_owner_id'] == request['person_id']
    assert result['asset']['prompt_tag'] == 'brass-key' and result['asset']['observation'] == ''
    assert result['asset']['generated_by']['seed'] == 12345
    assert manager.refresh(ident)['asset_id'] == result['asset_id']
    assert len(imports) == 1 and imports[0][2] == 'image/png'
    assert not any('interrupt' in path or path == '/free' for _, path in server.requests)


def test_request_idempotency_and_only_one_active_job(setup):
    manager, server, *_ = setup
    ident, request = uid(), spec()
    manager.submit(ident, request)
    manager.process(ident)
    assert manager.submit(ident, copy.deepcopy(request))['status'] == 'queued'
    assert manager.process(ident)['status'] == 'queued'
    with pytest.raises(AssetRunError, match='different settings'):
        manager.submit(ident, {**request, 'seed': 99})
    with pytest.raises(AssetRunError, match='active'):
        manager.submit(uid(), request)
    assert len(server.posts) == 1


def test_lost_post_response_is_recovered_after_restart_without_second_post(setup):
    manager, server, _, imports, _, create = setup
    ident = uid()
    server.lost_response = True
    manager.submit(ident, spec())
    assert manager.process(ident)['status'] == 'uncertain'
    recovered = create()
    assert recovered.resume(ident)['status'] == 'queued'
    assert recovered.process(ident)['status'] == 'queued'
    server.finish(ident)
    assert recovered.refresh(ident)['status'] == 'succeeded'
    assert len(server.posts) == 1 and len(imports) == 1


def test_unknown_original_never_retries_even_after_restart(setup):
    manager, server, _, _, _, create = setup
    ident = uid()
    server.lost_response = True
    manager.submit(ident, spec())
    manager.process(ident)
    server.pending.clear()  # Original Comfy history is lost; acceptance remains uncertain.
    recovered = create()
    assert recovered.resume(ident)['status'] == 'uncertain'
    assert recovered.process(ident)['status'] == 'uncertain'
    with pytest.raises(AssetRunError, match='Recover'):
        recovered.retry(ident, uid())
    assert len(server.posts) == 1


def test_restart_before_submission_pauses_until_explicit_resume(setup):
    manager, server, _, _, _, create = setup
    ident = uid()
    manager.submit(ident, spec())
    recovered = create()
    assert recovered.get(ident)['status'] == 'paused' and not server.posts
    assert recovered.refresh(ident)['status'] == 'paused'
    assert recovered.process(ident)['status'] == 'paused'
    assert recovered.resume(ident)['status'] == 'preparing'
    assert recovered.process(ident)['status'] == 'queued' and len(server.posts) == 1


def test_unrelated_queue_blocks_before_model_handoff(setup):
    manager, server, resources, *_ = setup
    server.pending.append([1, uid(), {'secret': 'must not inspect'}, {'client_id': 'user'}, []])
    ident = uid()
    manager.submit(ident, spec())
    assert manager.process(ident)['status'] == 'failed'
    assert len(server.pending) == 1 and not resources.handoffs and not server.posts


def test_job_arriving_during_handoff_blocks_own_post(setup):
    manager, server, resources, *_ = setup
    resources.before_submit = lambda: server.pending.append([1, uid(), {}, {'client_id': 'user'}, []])
    ident = uid()
    manager.submit(ident, spec())
    assert manager.process(ident)['status'] == 'failed'
    assert not server.posts and len(server.pending) == 1


def test_cancel_during_preparation_prevents_post(setup):
    manager, server, resources, *_ = setup
    ident = uid()
    manager.submit(ident, spec())
    resources.before_submit = lambda: manager.cancel(ident)
    assert manager.process(ident)['status'] == 'cancelled'
    assert not server.posts


def test_pending_cancellation_deletes_only_owned_prompt(setup):
    manager, server, _, imports, *_ = setup
    ident, other = uid(), uid()
    manager.submit(ident, spec())
    manager.process(ident)
    server.pending.append([2, other, {}, {'client_id': 'user'}, []])
    assert manager.cancel(ident)['status'] == 'cancelled'
    assert len(server.pending) == 1 and server.pending[0][1] == other
    assert not imports and ('POST', '/interrupt') not in server.requests


def test_running_cancel_defers_and_does_not_import_or_interrupt(setup):
    manager, server, _, imports, *_ = setup
    ident = uid()
    manager.submit(ident, spec())
    manager.process(ident)
    server.running.append(server.pending.pop())
    assert manager.cancel(ident)['status'] == 'cancelling'
    server.finish(ident)
    assert manager.refresh(ident)['status'] == 'cancelled'
    assert not imports and ('POST', '/interrupt') not in server.requests


@pytest.mark.parametrize('changes', [
    {'filename': '../foreign.png'}, {'filename': 'image_00001_.jpg'}, {'type': 'input'},
    {'subfolder': 'h3_prompt_studio/assets/foreign'}, {'subfolder': 'h3_prompt_studio/assets/../foreign'},
])
def test_foreign_or_traversing_output_is_not_downloaded_or_imported(setup, changes):
    manager, server, _, imports, *_ = setup
    ident = uid()
    manager.submit(ident, spec())
    manager.process(ident)
    server.finish(ident, **changes)
    result = manager.refresh(ident)
    assert result['status'] == 'uncertain' and 'owned output folder' in result['error']
    assert not imports and ('GET', '/view') not in server.requests


def test_native_windows_saveimage_folder_is_imported_without_regeneration(setup):
    manager, server, _, imports, *_ = setup
    ident = uid()
    manager.submit(ident, spec())
    manager.process(ident)
    server.finish(ident, subfolder=f'h3_prompt_studio\\assets\\{ident}')
    assert manager.refresh(ident)['status'] == 'succeeded'
    assert len(imports) == 1 and len(server.posts) == 1


def test_windows_separator_normalization_does_not_accept_traversal(setup):
    manager, server, _, imports, *_ = setup
    ident = uid()
    manager.submit(ident, spec())
    manager.process(ident)
    server.finish(ident, subfolder=f'h3_prompt_studio\\assets\\foreign\\..\\{ident}')
    assert manager.refresh(ident)['status'] == 'uncertain'
    assert not imports and ('GET', '/view') not in server.requests


def test_wrong_dimensions_and_invalid_png_are_not_imported(setup):
    manager, server, _, imports, *_ = setup
    ident = uid()
    manager.submit(ident, spec(width=256))
    manager.process(ident)
    server.finish(ident)
    assert 'dimensions' in manager.refresh(ident)['error']
    server.image = b'not a PNG'
    assert 'unreadable PNG' in manager.refresh(ident)['error']
    assert not imports


def test_import_recovery_does_not_reimport_saved_asset(setup):
    manager, server, _, imports, _, create = setup
    ident = uid()
    manager.submit(ident, spec())
    manager.process(ident)
    server.finish(ident)
    result = manager.refresh(ident)
    # Simulate a stop after asset.json was persisted but before success record.
    record = manager.records[ident]
    manager._save(record, status='queued', asset=None, asset_id=None, finished_at=None)
    recovered = create()
    assert recovered.refresh(ident)['asset_id'] == result['asset_id']
    assert len(imports) == 1 and len(server.posts) == 1


def test_concurrent_refresh_imports_once(setup):
    manager, server, _, imports, *_ = setup
    ident = uid()
    manager.submit(ident, spec())
    manager.process(ident)
    server.finish(ident)
    threads = [threading.Thread(target=manager.refresh, args=(ident,)) for _ in range(3)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(2)
    assert all(not thread.is_alive() for thread in threads)
    assert len(imports) == 1 and manager.get(ident)['status'] == 'succeeded'


def test_explicit_retry_requires_new_identifier_and_retains_original(setup):
    manager, server, *_ = setup
    ident, replacement = uid(), uid()
    server.rejected = 400
    manager.submit(ident, spec())
    assert manager.process(ident)['status'] == 'failed'
    with pytest.raises(AssetRunError, match='new request identifier'):
        manager.retry(ident, ident)
    server.rejected = None
    assert manager.retry(ident, replacement)['status'] == 'preparing'
    assert manager.process(replacement)['status'] == 'queued'
    assert manager.get(ident)['status'] == 'failed' and len(server.posts) == 2


def test_changed_origin_is_not_contacted_during_recovery(setup):
    manager, server, _, _, settings, _ = setup
    ident = uid()
    manager.submit(ident, spec())
    manager.process(ident)
    before = len(server.requests)
    settings['comfy_urls'] = ['http://127.0.0.1:8011']
    assert 'no longer configured' in manager.refresh(ident)['error']
    assert len(server.requests) == before


@pytest.mark.parametrize('http_status', [408, 409, 500])
def test_ambiguous_http_replies_remain_uncertain_without_automatic_retry(setup, http_status):
    manager, server, *_ = setup
    ident = uid()
    server.rejected = http_status
    manager.submit(ident, spec())
    assert manager.process(ident)['status'] == 'uncertain'
    assert manager.resume(ident)['status'] == 'uncertain'
    assert manager.process(ident)['status'] == 'uncertain'
    assert len(server.posts) == 1
