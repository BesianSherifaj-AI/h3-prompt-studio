"""Resident CPU mode without real model loads, inference, or Comfy operations."""
import copy
from types import SimpleNamespace

import pytest

from backend import resources
from backend.lmstudio import (LMStudioClient, LMStudioError, RESIDENT_PREFIX,
                             resident_cpu_profile, validate_resident_cpu_config)


KEY = 'qwen3.5-0.8b@q8_0'
PATH = 'local/Qwen3.5-0.8B-GGUF/Qwen3.5-0.8B-Q8_0.gguf'


class Struct:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return copy.deepcopy(self.data)


class FakeSdk:
    def __init__(self):
        self.native = {'key': KEY, 'type': 'llm', 'size_bytes': 1_214_225_504,
                       'capabilities': {'vision': True}, 'loaded_instances': []}
        self.downloaded = [SimpleNamespace(model_key=KEY, path=PATH,
                          info=Struct({'vision': True, 'sizeBytes': self.native['size_bytes']}))]
        self.handles, self.loads, self.reads = [], [], []
        self.config = resident_cpu_profile()
        self.info_override = {}
        self.load_failure = None
        self.llm = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def list_downloaded(self):
        self.reads.append('list_downloaded')
        return self.downloaded

    def list_loaded(self):
        self.reads.append('list_loaded')
        return self.handles

    def add_handle(self, ident=RESIDENT_PREFIX + 'cpu-test'):
        info = {'modelKey': KEY, 'path': PATH, 'identifier': ident, **self.info_override}
        handle = SimpleNamespace(identifier=ident, get_info=lambda: Struct(info),
                                 get_load_config=lambda: Struct(self.config))
        self.handles.append(handle)
        self.native['loaded_instances'].append({'id': ident, 'config': {'context_length': 4096}})
        return handle

    def load_new_instance(self, path, ident, **kwargs):
        self.loads.append((path, ident, copy.deepcopy(kwargs)))
        if self.load_failure:
            raise self.load_failure
        return self.add_handle(ident)

    def model(self, *args, **kwargs):
        raise AssertionError('Adoption must never use a JIT API')


@pytest.fixture
def sdk_rig():
    sdk = FakeSdk()
    client = LMStudioClient(sdk_factory=lambda: sdk)
    def request(method, path, payload=None):
        assert method == 'GET' and path == '/api/v1/models'
        return {'models': [copy.deepcopy(sdk.native)]}
    client._request = request
    return sdk, client


def test_profile_uses_supported_sdk_types_and_fresh_values():
    from lmstudio import LlmLoadModelConfig
    profile = resident_cpu_profile()
    assert LlmLoadModelConfig._from_api_dict(profile).to_dict() == profile
    profile['gpu']['disabledGpus'].clear()
    assert resident_cpu_profile()['gpu']['disabledGpus'] == [0]


@pytest.mark.parametrize('change', [
    {'key': 'qwen-27b'}, {'type': 'embedding'}, {'capabilities': {'vision': False}},
    {'capabilities': {}}, {'size_bytes': 1_500_000_001}, {'size_bytes': 0},
    {'size_bytes': True}, {'size_bytes': '800000000'},
])
def test_resident_inventory_gate_rejects_ineligible_models_without_sdk(sdk_rig, change):
    sdk, client = sdk_rig
    sdk.native.update(change)
    with pytest.raises(LMStudioError):
        client.load_resident_model(sdk.native['key'])
    assert not sdk.loads and not sdk.reads


def test_load_uses_exact_sdk_file_cpu_profile_no_ttl_and_confirmed_native_instance(sdk_rig):
    sdk, client = sdk_rig
    result = client.load_resident_model(KEY)
    assert result['instance_id'].startswith(RESIDENT_PREFIX)
    assert result['profile'] == 'resident_small_cpu' and result['context_length'] == 4096
    assert sdk.loads == [(PATH, result['instance_id'], {'config': resident_cpu_profile(), 'ttl': None})]
    assert result['load_config'] == resident_cpu_profile()


def test_adoption_is_read_only_and_checks_exact_file_and_configuration(sdk_rig):
    sdk, client = sdk_rig
    handle = sdk.add_handle()
    result = client.verify_resident_model(KEY, handle.identifier)
    assert result['instance_id'] == handle.identifier and not sdk.loads
    assert sdk.reads == ['list_downloaded', 'list_loaded']


@pytest.mark.parametrize('section,key,bad', [
    ('gpu', 'ratio', 0.5), ('gpu', 'ratio', False), ('gpu', 'disabledGpus', []),
    (None, 'contextLength', 8192), (None, 'contextLength', None),
    (None, 'offloadKVCacheToGpu', True), (None, 'evalBatchSize', 512),
    (None, 'flashAttention', False), (None, 'gpuStrictVramCap', False),
])
def test_adoption_rejects_changed_or_unreported_resident_configuration(sdk_rig, section, key, bad):
    sdk, client = sdk_rig
    (sdk.config[section] if section else sdk.config)[key] = bad
    handle = sdk.add_handle()
    with pytest.raises(LMStudioError, match='CPU placement'):
        client.verify_resident_model(KEY, handle.identifier)
    assert not sdk.loads


def test_normalized_off_ratio_is_cpu_but_missing_disabled_gpu_is_not():
    config = resident_cpu_profile()
    config['gpu']['ratio'] = 'off'
    assert validate_resident_cpu_config(config) == config
    config['gpu'].pop('disabledGpus')
    with pytest.raises(LMStudioError):
        validate_resident_cpu_config(config)


@pytest.mark.parametrize('change', [{'path': 'other/model.gguf'}, {'modelKey': 'other-0.8b'},
                                    {'identifier': RESIDENT_PREFIX + 'other'}])
def test_adoption_rejects_aliases_or_wrong_gguf_file(sdk_rig, change):
    sdk, client = sdk_rig
    sdk.info_override = change
    handle = sdk.add_handle()
    with pytest.raises(LMStudioError, match='exact selected file'):
        client.verify_resident_model(KEY, handle.identifier)


def test_foreign_instance_and_duplicate_inventory_are_never_adopted(sdk_rig):
    sdk, client = sdk_rig
    with pytest.raises(LMStudioError, match='h3-studio-resident'):
        client.verify_resident_model(KEY, 'manual-user-instance')
    assert not sdk.reads
    sdk.downloaded.append(sdk.downloaded[0])
    with pytest.raises(LMStudioError, match='one exact local file'):
        client.load_resident_model(KEY)
    assert not sdk.loads


def test_load_refuses_already_loaded_model_and_does_not_retry_uncertain_load(sdk_rig):
    sdk, client = sdk_rig
    sdk.add_handle('manual-user-instance')
    with pytest.raises(LMStudioError, match='Unload the other'):
        client.load_resident_model(KEY)
    assert not sdk.loads
    sdk.handles.clear()
    sdk.native['loaded_instances'].clear()
    sdk.load_failure = TimeoutError('Unknown response')
    with pytest.raises(LMStudioError, match='could not finish'):
        client.load_resident_model(KEY)
    assert len(sdk.loads) == 1


@pytest.fixture
def resident_manager(sdk_rig, monkeypatch):
    sdk, client = sdk_rig
    settings = {'model': KEY, 'ai_memory_mode': 'resident_small', 'context_length': 16384,
                'comfy_urls': ['http://127.0.0.1:8010']}
    manager = resources.ResourceManager(lambda: settings, lambda: client)
    monkeypatch.setattr(manager, 'assert_idle', lambda: [{'online': True, 'url': settings['comfy_urls'][0]}])
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 23800, 'free_mib': 500})
    def denied(*args, **kwargs):
        raise AssertionError('Resident mode must not release H3 or unload models')
    monkeypatch.setattr(resources.httpx, 'post', denied)
    monkeypatch.setattr(client, 'unload_model', denied)
    return manager, sdk, settings


def test_resident_ai_does_not_unload_h3_or_apply_exclusive_global_vram_threshold(resident_manager):
    manager, sdk, _ = resident_manager
    result = manager.run_ai(KEY)
    assert result['memory_mode'] == 'resident_small' and len(sdk.loads) == 1
    assert result['context_length'] == 4096
    assert manager.ai_idle_memory_mib is None and manager.baseline_instance_id is None
    assert manager.run_ai(KEY)['instance_id'] == result['instance_id']
    assert len(sdk.loads) == 1


def test_h3_preserves_resident_model_and_lock_excludes_simultaneous_ai(resident_manager):
    manager, sdk, _ = resident_manager
    resident = manager.run_ai(KEY)
    def queue_once():
        assert manager.lock.locked()
        with pytest.raises(resources.ResourceError, match='in progress'):
            manager.run_ai(KEY)
        return 'own-prompt-id'
    assert manager.prepare_h3_then(queue_once) == 'own-prompt-id'
    assert manager.instance_id == resident['instance_id'] and len(sdk.loads) == 1
    assert len(sdk.handles) == 1 and not manager.lock.locked()


def test_resident_inference_holds_lock_until_finished(resident_manager):
    manager, _, _ = resident_manager
    def infer(instance):
        assert instance.startswith(RESIDENT_PREFIX) and manager.lock.locked()
        with pytest.raises(resources.ResourceError, match='AI is still working'):
            manager.prepare_h3()
        return {'proposal': 'neutral'}
    assert manager.run_ai(KEY, infer) == {'proposal': 'neutral'}
    assert not manager.lock.locked()


def test_resident_restart_adopts_only_verified_instance_and_h3_does_not_load_ai(resident_manager):
    manager, sdk, _ = resident_manager
    assert manager.prepare_h3()['ready'] and not sdk.loads
    handle = sdk.add_handle()
    assert manager.prepare_h3()['memory_mode'] == 'resident_small'
    assert manager.instance_id == handle.identifier and not sdk.loads


def test_busy_comfy_blocks_resident_load_and_h3_submission(resident_manager, monkeypatch):
    manager, sdk, _ = resident_manager
    def busy():
        raise resources.ResourceError('ComfyUI has running or queued work.')
    monkeypatch.setattr(manager, 'assert_idle', busy)
    for operation in [lambda: manager.run_ai(KEY), manager.prepare_h3]:
        with pytest.raises(resources.ResourceError, match='queued work'):
            operation()
    assert not sdk.loads and not manager.lock.locked()


def test_foreign_large_or_changed_selected_model_blocks_resident_mode(resident_manager, monkeypatch):
    manager, sdk, settings = resident_manager
    monkeypatch.setattr(manager.get_client(), 'loaded_instances', lambda: [{'id': 'foreign', 'model': 'qwen-27b'}])
    for operation in [lambda: manager.run_ai(KEY), manager.prepare_h3]:
        with pytest.raises(resources.ResourceError, match='other LM Studio'):
            operation()
    assert not sdk.loads
    with pytest.raises(resources.ResourceError, match='exact small vision model'):
        manager.run_ai('different-0.8b')
    settings['model'] = 'different-0.8b'
    with pytest.raises(LMStudioError, match='exact local model'):
        manager.run_ai(settings['model'])


def test_changed_runtime_config_refuses_h3_and_never_submits(resident_manager):
    manager, sdk, _ = resident_manager
    manager.run_ai(KEY)
    sdk.config['gpu']['ratio'] = 1
    calls = []
    with pytest.raises(LMStudioError, match='CPU placement'):
        manager.prepare_h3_then(lambda: calls.append('queue'))
    assert not calls and len(sdk.loads) == 1 and not manager.lock.locked()


def test_restart_exclusive_h3_releases_verified_cpu_without_demanding_vram_drop(resident_manager, monkeypatch):
    manager, sdk, settings = resident_manager
    handle = sdk.add_handle()
    settings.update(ai_memory_mode='exclusive', model='qwen3.5-9b')
    calls = []
    def unload(ident):
        calls.append(ident)
        assert ident == handle.identifier
        sdk.handles.clear()
        sdk.native['loaded_instances'].clear()
    monkeypatch.setattr(manager.get_client(), 'unload_model', unload)
    def no_wait(*args):
        raise AssertionError('Verified CPU unload must not wait for a global VRAM drop')
    monkeypatch.setattr(resources.time, 'sleep', no_wait)
    assert manager.instance_id is None  # Fresh coordinator after restart.
    assert manager.prepare_h3()['ready']
    assert calls == [handle.identifier] and manager.instance_id is None
    assert not sdk.loads


def test_restart_switch_to_larger_model_verifies_then_releases_own_small_model(resident_manager, monkeypatch):
    manager, sdk, settings = resident_manager
    handle = sdk.add_handle()
    settings.update(ai_memory_mode='exclusive', model='qwen3.5-9b')
    calls = []
    def unload(ident):
        assert sdk.reads == ['list_downloaded', 'list_loaded']
        calls.append(('unload', ident))
        sdk.handles.clear()
        sdk.native['loaded_instances'].clear()
    def load(model, **kwargs):
        assert model == settings['model'] and not sdk.native['loaded_instances']
        calls.append(('load', model))
        sdk.native.update(key=model, loaded_instances=[{'id': 'new-large-instance'}])
        return {'instance_id': 'new-large-instance'}
    def release_h3(url, **kwargs):
        assert kwargs['json'] == {'unload_models': True, 'free_memory': True}
        calls.append(('free', url))
        return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(manager.get_client(), 'unload_model', unload)
    monkeypatch.setattr(manager.get_client(), 'load_model', load)
    monkeypatch.setattr(resources.httpx, 'post', release_h3)
    monkeypatch.setattr(resources.time, 'sleep', lambda *args: None)
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 1024, 'free_mib': 23000})
    result = manager.run_ai(settings['model'])
    assert calls == [('unload', handle.identifier), ('free', 'http://127.0.0.1:8010/free'),
                     ('load', settings['model'])]
    assert result['instance_id'] == 'new-large-instance'


@pytest.mark.parametrize('defect', ['config', 'wrong_path', 'foreign_prefix', 'multiple'])
def test_restart_never_claims_unverified_or_foreign_cpu_model(resident_manager, defect):
    manager, sdk, settings = resident_manager
    settings.update(ai_memory_mode='exclusive', model='qwen3.5-9b')
    if defect == 'config':
        sdk.config['gpu']['ratio'] = 1
    if defect == 'wrong_path':
        sdk.info_override['path'] = 'foreign/path.gguf'
    sdk.add_handle('manual-instance' if defect == 'foreign_prefix' else RESIDENT_PREFIX + 'previous')
    if defect == 'multiple':
        sdk.add_handle('manual-second-instance')
    for operation in (manager.prepare_h3, lambda: manager.run_ai(settings['model'])):
        with pytest.raises((LMStudioError, resources.ResourceError)):
            operation()
    assert manager.instance_id is None and not sdk.loads


def test_failed_cpu_unload_still_blocks_h3_despite_placement_verification(resident_manager, monkeypatch):
    manager, sdk, settings = resident_manager
    sdk.add_handle()
    settings.update(ai_memory_mode='exclusive')
    monkeypatch.setattr(manager.get_client(), 'unload_model', lambda ident: {'instance_id': ident})
    with pytest.raises(resources.ResourceError, match='still has a model loaded'):
        manager.prepare_h3()
    assert len(sdk.handles) == 1 and not sdk.loads
