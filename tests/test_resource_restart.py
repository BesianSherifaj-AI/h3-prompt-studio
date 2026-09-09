"""Durable LM ownership uses synthetic inventories, temp files and no hardware."""
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from backend import resources


ENDPOINT = 'http://127.0.0.1:1234'
MODEL = 'local-vision-model'


class Inventory:
    origin = ENDPOINT

    def __init__(self):
        self.instances, self.events = [], []
        self.unload_succeeds = True

    def loaded_instances(self):
        self.events.append(('inventory',))
        return copy.deepcopy(self.instances)

    def load_model(self, model, **kwargs):
        assert not self.instances
        ident = f'studio-instance-{len(self.events)}'
        self.events.append(('load', ident, model))
        self.instances.append({'id': ident, 'model': model})
        return {'instance_id': ident}

    def unload_model(self, ident):
        self.events.append(('unload', ident))
        if self.unload_succeeds:
            self.instances = [item for item in self.instances if item.get('id', item.get('instance_id')) != ident]
        return {'instance_id': ident}


@pytest.fixture
def restart_rig(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected real network or GPU operation')

    monkeypatch.setattr(resources.httpx, 'get', forbidden)
    monkeypatch.setattr(resources.httpx, 'post', forbidden)
    monkeypatch.setattr(resources, 'tcp_listener_ports', forbidden)
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 1000})
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(resources.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(resources.time, 'sleep', lambda seconds: setattr(clock, 'now', clock.now + seconds))
    client = Inventory()
    settings = {'lm_url': ENDPOINT + '/v1', 'comfy_urls': [], 'context_length': 8192,
                'model': MODEL, 'ai_memory_mode': 'exclusive'}
    path = tmp_path / 'resource_state.json'

    def reopen():
        manager = resources.ResourceManager(lambda: settings, lambda: client, state_path=path)
        monkeypatch.setattr(manager, 'queues', lambda: [])
        return manager

    result = SimpleNamespace(client=client, settings=settings, path=path, reopen=reopen, clock=clock)
    result.manager = reopen()
    return result


def acquire(rig):
    rig.manager._set_comfy_kind('video')
    ready = rig.manager.run_ai(MODEL)
    return ready['instance_id']


def read_state(rig):
    return json.loads(rig.path.read_text('utf-8'))


def test_acquisition_persists_exact_verified_inventory_and_endpoint_before_inference(restart_rig):
    rig = restart_rig
    rig.manager._set_comfy_kind('image')

    def inspect_marker(ident):
        assert read_state(rig) == {'comfy_kind': 'image', 'exclusive_instance': {
            'endpoint': ENDPOINT, 'instance_id': ident, 'model_key': MODEL}}
        raise ValueError('Synthetic inference failure')

    with pytest.raises(ValueError, match='Synthetic inference'):
        rig.manager.run_ai(MODEL, inspect_marker)
    assert rig.manager.instance_id == read_state(rig)['exclusive_instance']['instance_id']
    assert 'ai_idle_memory_mib' not in read_state(rig) and 'baseline_instance_id' not in read_state(rig)


def test_constructor_reads_candidate_without_inventory_model_or_gpu_calls(restart_rig, monkeypatch):
    rig = restart_rig
    ident = acquire(rig)
    before = rig.path.read_bytes()
    rig.client.events.clear()
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: pytest.fail('Constructor must not inspect GPU'))
    restored = rig.reopen()
    assert restored.exclusive_ownership['instance_id'] == ident
    assert restored.instance_id is restored.model_key is restored.instance_endpoint is None
    assert restored.ai_idle_memory_mib is restored.baseline_instance_id is None
    assert rig.client.events == [] and rig.path.read_bytes() == before


@pytest.mark.parametrize('shape', ['id_model', 'instance_id_model_key'])
def test_restart_recovers_exact_owned_assistant_for_render_and_clears_marker(restart_rig, shape):
    rig = restart_rig
    ident = acquire(rig)
    if shape == 'instance_id_model_key':
        rig.client.instances = [{'instance_id': ident, 'model_key': MODEL}]
    restored = rig.reopen()
    rig.client.events.clear()
    queued = []

    def submit():
        assert restored.lock.locked() and not rig.client.instances
        assert read_state(rig) == {'comfy_kind': 'video', 'exclusive_instance': None}
        queued.append('submitted-once')
        return 'own-job'

    assert restored.prepare_h3_then(submit) == 'own-job'
    assert ('unload', ident) in rig.client.events and not any(event[0] == 'load' for event in rig.client.events)
    assert queued == ['submitted-once']
    assert restored.instance_id is restored.model_key is None
    assert restored.ai_idle_memory_mib is restored.baseline_instance_id is None


@pytest.mark.parametrize('change', ['instance', 'model', 'endpoint', 'duplicate_id'])
def test_stale_or_different_server_inventory_never_unloads_an_unrelated_instance(restart_rig, change):
    rig = restart_rig
    ident = acquire(rig)
    if change == 'instance':
        rig.client.instances = [{'id': 'a-manually-loaded-instance', 'model': MODEL}]
    elif change == 'model':
        rig.client.instances = [{'id': ident, 'model': 'another-model'}]
    elif change == 'endpoint':
        rig.settings['lm_url'] = rig.client.origin = 'http://127.0.0.1:2234'
    else:
        rig.client.instances.append(copy.deepcopy(rig.client.instances[0]))
    restored = rig.reopen()
    rig.client.events.clear()
    with pytest.raises(resources.ResourceError, match='outside this Studio'):
        restored.prepare_h3_then(lambda: pytest.fail('Must not queue'))
    assert all(event[0] == 'inventory' for event in rig.client.events)
    assert read_state(rig) == {'comfy_kind': 'video', 'exclusive_instance': None}


def test_owned_and_foreign_models_only_release_the_owned_one_and_block_submission(restart_rig):
    rig = restart_rig
    ident = acquire(rig)
    foreign = {'id': 'external-model-instance', 'model': 'another-model'}
    rig.client.instances.append(foreign)
    restored = rig.reopen()
    rig.client.events.clear()
    with pytest.raises(resources.ResourceError, match='outside this Studio'):
        restored.prepare_h3_then(lambda: pytest.fail('Must not queue'))
    assert rig.client.instances == [foreign]
    assert [event for event in rig.client.events if event[0] == 'unload'] == [('unload', ident)]
    assert read_state(rig)['exclusive_instance'] is None


def test_failed_unload_retains_ownership_for_retry_without_loading_another_model(restart_rig):
    rig = restart_rig
    ident = acquire(rig)
    rig.client.unload_succeeds = False
    restored = rig.reopen()
    with pytest.raises(resources.ResourceError, match='still has a model loaded'):
        restored.prepare_h3()
    assert read_state(rig)['exclusive_instance']['instance_id'] == ident
    rig.client.unload_succeeds = True
    assert rig.reopen().prepare_h3()['ready']
    assert read_state(rig)['exclusive_instance'] is None


def test_inventory_timeout_keeps_candidate_for_an_explicit_retry(restart_rig, monkeypatch):
    rig = restart_rig
    ident = acquire(rig)
    restored = rig.reopen()
    inventory = rig.client.loaded_instances

    def unavailable():
        raise httpx.ReadTimeout('Inventory response unavailable')

    monkeypatch.setattr(rig.client, 'loaded_instances', unavailable)
    with pytest.raises(httpx.ReadTimeout):
        restored.prepare_h3()
    assert read_state(rig)['exclusive_instance']['instance_id'] == ident
    monkeypatch.setattr(rig.client, 'loaded_instances', inventory)
    assert restored.prepare_h3()['ready']


def test_family_updates_and_forgetting_ownership_preserve_each_others_state(restart_rig):
    rig = restart_rig
    ident = acquire(rig)
    restored = rig.reopen()
    restored._set_comfy_kind('image')
    assert read_state(rig)['exclusive_instance']['instance_id'] == ident
    restored._forget_instance()
    assert read_state(rig) == {'comfy_kind': 'image', 'exclusive_instance': None}
    assert rig.reopen().comfy_kind == 'image'


def test_legacy_family_marker_does_not_authorize_automatic_unloading(restart_rig):
    rig = restart_rig
    rig.path.write_text(json.dumps({'comfy_kind': 'video'}), encoding='utf-8')
    rig.client.instances = [{'id': 'unmarked-selected-model', 'model': MODEL}]
    restored = rig.reopen()
    with pytest.raises(resources.ResourceError, match='outside this Studio'):
        restored.prepare_h3()
    assert not any(event[0] == 'unload' for event in rig.client.events)
    # Preparing AI cannot turn an unrelated instance into owned memory.
    with pytest.raises(resources.ResourceError, match='outside this Studio'):
        restored.run_ai(MODEL)
    assert read_state(rig)['exclusive_instance'] is None
    assert not any(event[0] == 'unload' for event in rig.client.events)


@pytest.mark.parametrize('ownership', [None, {}, {'instance_id': 'incomplete'},
    {'endpoint': 'https://outside.invalid', 'instance_id': 'instance', 'model_key': MODEL},
    {'endpoint': ENDPOINT, 'instance_id': ['invalid'], 'model_key': MODEL},
    {'endpoint': ENDPOINT, 'instance_id': 'instance', 'model_key': MODEL, 'ai_idle_memory_mib': 24000}])
def test_invalid_ownership_cannot_erase_valid_comfy_family_or_authorize_release(restart_rig, ownership):
    rig = restart_rig
    rig.path.write_text(json.dumps({'comfy_kind': 'image', 'exclusive_instance': ownership}), encoding='utf-8')
    restored = rig.reopen()
    assert restored.comfy_kind == 'image' and restored.exclusive_ownership is None
    assert restored.instance_id is None and rig.client.events == []


def test_restart_never_uses_persisted_vram_baseline_to_skip_a_fresh_release(restart_rig, monkeypatch):
    rig = restart_rig
    ident = acquire(rig)
    state = read_state(rig)
    state.update(ai_idle_memory_mib=24000, baseline_instance_id=ident)
    rig.path.write_text(json.dumps(state), encoding='utf-8')
    restored = rig.reopen()
    assert restored.ai_idle_memory_mib is restored.baseline_instance_id is None
    monkeypatch.setattr(restored, 'queues', lambda: [{'url': 'http://127.0.0.1:8010', 'online': True, 'running': 0, 'pending': 0}])
    monkeypatch.setattr(resources.httpx, 'post', lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 12000})
    rig.client.events.clear()
    with pytest.raises(resources.ResourceError, match='memory release could not be verified'):
        restored.run_ai(MODEL)
    assert [event for event in rig.client.events if event[0] == 'unload'] == [('unload', ident)]
    assert not any(event[0] == 'load' for event in rig.client.events)
    assert restored.ai_idle_memory_mib is restored.baseline_instance_id is None
    assert read_state(rig)['exclusive_instance'] is None


def test_busy_comfy_after_restart_blocks_inventory_and_ownership_changes(restart_rig, monkeypatch):
    rig = restart_rig
    acquire(rig)
    restored = rig.reopen()
    before = rig.path.read_bytes()
    rig.client.events.clear()
    monkeypatch.setattr(restored, 'queues', lambda: [{'url': 'http://127.0.0.1:8010', 'online': True, 'running': 1, 'pending': 0}])
    with pytest.raises(resources.ResourceError, match='running or queued'):
        restored.prepare_h3()
    assert rig.client.events == [] and rig.path.read_bytes() == before
