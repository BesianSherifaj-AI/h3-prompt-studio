"""The local family lock is enforced before any GPU or inference submission."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

# The imported legacy no_hardware fixture replaces subprocess.run. Capture the
# real runner only for this explicit, isolated Python startup regression.
STARTUP_PROCESS = subprocess.run

import pytest

from backend.assistant_profiles import (
    DEFAULT_QWEN_MODEL, QWEN_MODEL_POLICY, is_qwen_27b_model,
    merge_profile_settings, migrate_profiles, resolve_profile,
)
from backend.lmstudio import LMStudioClient, LMStudioError
from backend.resources import ResourceError, ResourceManager
from test_app import auth, no_hardware, server


def locked_settings():
    return {'assistant_model_policy': QWEN_MODEL_POLICY, 'model': DEFAULT_QWEN_MODEL,
            'context_length': 16384, 'ai_memory_mode': 'exclusive',
            'assistant_profiles': {'game': {'model': 'qwen3.5-4b', 'context_length': 8192, 'ai_memory_mode': 'resident_cpu'}}}


@pytest.mark.parametrize('key', [DEFAULT_QWEN_MODEL, 'huihui-qwen3.8-27b-abliterated@q4_k_s',
    'publisher/Qwen3.8-27B@Q8_0', 'qwen3.8-27b-xs-16gb-vram'])
def test_exact_installed_family_variants(key):
    assert is_qwen_27b_model(key)


@pytest.mark.parametrize('key', ['qwen3.5-27b', 'qwen3.8-4b', 'qwen3.8-127b', 'notqwen3.8-27b', None])
def test_other_families_and_sizes_are_rejected(key):
    assert not is_qwen_27b_model(key)


def test_offline_local_migration_preserves_context_and_valid_exact_keys():
    original = locked_settings()
    saved = migrate_profiles(original)
    assert saved['assistant_profiles']['game'] == {'model': DEFAULT_QWEN_MODEL, 'context_length': 8192, 'ai_memory_mode': 'exclusive'}
    assert saved['assistant_profiles']['studio']['context_length'] == 16384
    assert original['assistant_profiles']['game']['model'] == 'qwen3.5-4b'
    variant = 'huihui-qwen3.8-27b-abliterated@q4_k_s'
    saved['assistant_profiles']['game']['model'] = variant
    assert resolve_profile(saved, 'game')['model'] == variant


@pytest.mark.parametrize('patch', [{'model': 'qwen3.5-4b'}, {'ai_memory_mode': 'resident_cpu'}, {'ai_memory_mode': 'resident_small'}])
def test_settings_edits_cannot_bypass_lock(patch):
    settings = locked_settings()
    before = copy.deepcopy(settings)
    with pytest.raises(ValueError, match='Qwen 3.8 27B'):
        merge_profile_settings(settings, {'assistant_profiles': {'game': patch}})
    assert settings == before


def test_valid_variant_and_context_remain_configurable():
    variant = 'qwen3.8-27b@q8_0'
    saved = merge_profile_settings(locked_settings(), {'assistant_profiles': {'game': {'model': variant, 'context_length': 32768}}})
    assert resolve_profile(saved, 'game') == {'model': variant, 'context_length': 32768, 'ai_memory_mode': 'exclusive'}


def test_old_frozen_queued_profile_is_blocked_before_gpu_handoff(monkeypatch):
    calls = []
    manager = ResourceManager(locked_settings, lambda: pytest.fail('No LM client may be contacted'))
    monkeypatch.setattr(manager, '_prepare_ai', lambda model: calls.append(model))
    with pytest.raises(ResourceError, match='locked to Qwen'):
        manager.run_ai('qwen3.5-4b', profile={'model': 'qwen3.5-4b', 'context_length': 8192, 'ai_memory_mode': 'resident_cpu'})
    assert not calls and not manager.lock.locked()
    assert manager._active_settings is None


def test_batch_frozen_profile_is_also_blocked(monkeypatch):
    manager = ResourceManager(locked_settings, lambda: pytest.fail('No LM client may be contacted'))
    monkeypatch.setattr(manager, '_prepare_ai', lambda model: pytest.fail('No GPU load may be submitted'))
    with pytest.raises(ResourceError, match='locked to Qwen'):
        manager.run_ai_batch('qwen3.5-4b', [lambda *_: None], profile={'model': 'qwen3.5-4b'})
    assert not manager.lock.locked()


def test_client_rejects_smaller_instance_alias_before_completion():
    client = LMStudioClient(model_policy=QWEN_MODEL_POLICY)
    calls = []
    def request(method, path, payload=None):
        calls.append((method, path))
        return {'models': [{'key': 'qwen3.5-4b', 'type': 'llm', 'capabilities': {'vision': True}, 'loaded_instances': [{'id': 'alias'}]}]}
    client._request = request
    with pytest.raises(LMStudioError) as error:
        client.complete_json('alias', 'Write JSON.', 'A scene.', {'type': 'object'}, max_tokens=32)
    assert error.value.code == 'model_policy_violation'
    assert calls == [('GET', '/api/v1/models')]


def test_client_accepts_allowed_instance_alias():
    client = LMStudioClient(model_policy=QWEN_MODEL_POLICY)
    client.native_models = lambda: [{'key': DEFAULT_QWEN_MODEL, 'type': 'llm', 'capabilities': {'vision': True}, 'loaded_instances': [{'id': 'alias'}]}]
    assert client._loaded_model('alias', require_vision=True)[0] == 'alias'


@pytest.mark.parametrize('method', ['load_model', 'load_owned_model', 'load_resident_model'])
def test_model_load_rejected_before_inventory_or_sdk(method):
    client = LMStudioClient(model_policy=QWEN_MODEL_POLICY)
    client.native_models = lambda: pytest.fail('No model preflight/network is needed for a blocked family')
    with pytest.raises(LMStudioError) as error:
        getattr(client, method)('qwen3.5-4b')
    assert error.value.code == 'model_policy_violation'
    assert error.value.load_submitted is False


def test_api_lock_cannot_be_disabled_or_overridden_by_legacy_prepare(server, monkeypatch):
    module, http, _ = server
    module.SETTINGS.update(migrate_profiles(locked_settings()))
    monkeypatch.setattr(module.RESOURCES, 'run_ai', lambda *_args, **_kwargs: pytest.fail('Invalid model must fail at the endpoint'))
    for path, body in [('/api/settings', {'assistant_model_policy': ''}),
                       ('/api/settings', {'model': 'qwen3.5-4b'}),
                       ('/api/gpu/prepare-ai', {'model': 'qwen3.5-4b'})]:
        response = http.post(path, headers=auth(module), json=body)
        assert response.status_code == 400, response.text
    response = http.post('/api/settings', headers=auth(module), json={'assistant_profiles': {'game': {'context_length': 12288}}})
    assert response.status_code == 200, response.text
    assert response.json()['assistant_model_policy'] == QWEN_MODEL_POLICY
    assert response.json()['assistant_profiles']['game']['model'] == DEFAULT_QWEN_MODEL


@pytest.mark.parametrize('legacy', [False, True])
def test_direct_app_start_enforces_default_lock_without_launcher_or_gpu(tmp_path, legacy):
    data = tmp_path / 'direct-start'
    data.mkdir()
    if legacy:
        (data / 'settings.json').write_text(json.dumps({'assistant_model_policy': '', 'model': 'qwen3.5-4b',
            'context_length': 12288, 'ai_memory_mode': 'resident_cpu'}), encoding='utf-8')
    environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    environment['H3_STUDIO_DATA'] = str(data)
    script = '''
import json
import httpx
from backend import resources
from backend.lmstudio import LMStudioClient
def denied(*args, **kwargs):
    raise AssertionError('Startup and rejected settings must not access models or GPU')
resources.subprocess.run = denied
LMStudioClient._request = denied
httpx.get = denied
httpx.post = denied
from backend import app as module
from fastapi.testclient import TestClient
with TestClient(module.app, base_url='http://127.0.0.1:8766') as client:
    headers = {'X-H3-Token': module.TOKEN}
    boot = client.get('/api/bootstrap').json()
    rejected = [client.post(path, headers=headers, json=body).status_code for path, body in (
        ('/api/settings', {'assistant_model_policy': ''}),
        ('/api/settings', {'model': 'qwen3.5-4b'}),
        ('/api/gpu/prepare-ai', {'model': 'qwen3.5-4b'}))]
print(json.dumps({'policy': boot['settings']['assistant_model_policy'],
    'profiles': boot['settings']['assistant_profiles'], 'rejected': rejected,
    'client_policy': module.client().model_policy}))
'''
    result = STARTUP_PROCESS([sys.executable, '-X', 'utf8', '-c', script], cwd=Path(__file__).resolve().parents[1],
                            env=environment, capture_output=True, text=True, timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['policy'] == report['client_policy'] == QWEN_MODEL_POLICY
    assert report['rejected'] == [400, 400, 400]
    for profile in report['profiles'].values():
        assert is_qwen_27b_model(profile['model']) and profile['ai_memory_mode'] == 'exclusive'
        assert profile['context_length'] == (12288 if legacy else 8192)
    persisted = json.loads((data / 'settings.json').read_text(encoding='utf-8'))
    assert persisted['assistant_model_policy'] == QWEN_MODEL_POLICY
