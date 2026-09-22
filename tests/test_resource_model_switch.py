"""Model-switch contracts with fake LM instances; never touches a GPU/server."""
import pytest

from backend import resources


class Models:
    def __init__(self, *, unload_succeeds=True):
        self.instances = [{'id': 'old-instance', 'model': 'old-model'}]
        self.calls = []
        self.unload_succeeds = unload_succeeds

    def loaded_instances(self):
        self.calls.append(('list',))
        return list(self.instances)

    def unload_model(self, instance):
        self.calls.append(('unload', instance))
        if self.unload_succeeds:
            self.instances = [item for item in self.instances if item['id'] != instance]

    def load_model(self, model, **kwargs):
        self.calls.append(('load', model, kwargs))
        assert not self.instances, 'A second model must not load alongside the previous one'
        self.instances.append({'id': 'new-instance', 'model': model, 'config': {'context_length': kwargs['context_length']}})
        return {'instance_id': 'new-instance', 'load_config': {'context_length': kwargs['context_length']}}


@pytest.fixture
def setup(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected network, sleep or GPU operation')
    monkeypatch.setattr(resources.httpx, 'get', forbidden)
    monkeypatch.setattr(resources.httpx, 'post', forbidden)
    monkeypatch.setattr(resources.time, 'sleep', forbidden)
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 1024, 'free_mib': 14000, 'total_mib': 16000})
    models = Models()
    manager = resources.ResourceManager(lambda: {'comfy_urls': [], 'context_length': 8192}, lambda: models)
    manager.instance_id = 'old-instance'
    manager.model_key = 'old-model'
    manager.baseline_instance_id = 'old-instance'
    manager.ai_idle_memory_mib = 1024
    return manager, models


def test_selected_model_switch_unloads_owned_instance_before_loading_new_one(setup):
    manager, models = setup
    result = manager.run_ai('new-model')
    actions = [call[0] for call in models.calls]
    assert actions.index('unload') < actions.index('load')
    assert ('unload', 'old-instance') in models.calls
    assert ('load', 'new-model', {'context_length': 8192}) in models.calls
    assert result['model'] == 'new-model'
    assert manager.model_key == 'new-model'
    assert manager.instance_id == manager.baseline_instance_id == 'new-instance'


def test_running_comfy_job_blocks_switch_before_any_model_change(setup, monkeypatch):
    manager, models = setup
    monkeypatch.setattr(manager, 'queues', lambda: [{'url': 'http://127.0.0.1:8010', 'online': True, 'running': 1, 'pending': 0}])
    with pytest.raises(resources.ResourceError, match='running or queued'):
        manager.run_ai('new-model')
    assert models.calls == []
    assert manager.instance_id == 'old-instance'


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_running_comfy_job_blocks_render_handoff_before_unloading_ai(setup, monkeypatch, kind):
    manager, models = setup
    monkeypatch.setattr(manager, 'queues', lambda: [{'url': 'http://127.0.0.1:8010', 'online': True, 'running': 1, 'pending': 0}])
    with pytest.raises(resources.ResourceError, match='running or queued'):
        manager.prepare_comfy_then(kind, lambda: pytest.fail('Must not submit'))
    assert models.calls == [] and manager.instance_id == 'old-instance'


def test_failed_release_never_loads_a_second_model(setup):
    manager, models = setup
    models.unload_succeeds = False
    with pytest.raises(resources.ResourceError, match='Another LM Studio model'):
        manager.run_ai('new-model')
    assert not any(call[0] == 'load' for call in models.calls)
    assert models.instances == [{'id': 'old-instance', 'model': 'old-model'}]


def test_external_model_is_not_silently_unloaded_when_selection_changes(setup):
    manager, models = setup
    manager._forget_instance()
    with pytest.raises(resources.ResourceError, match='Another LM Studio model'):
        manager.run_ai('new-model')
    assert not any(call[0] in ('unload', 'load') for call in models.calls)


def test_same_selected_model_reuses_existing_owned_instance(setup):
    manager, models = setup
    assert manager.run_ai('old-model')['instance_id'] == 'old-instance'
    assert not any(call[0] in ('unload', 'load') for call in models.calls)


def test_same_model_new_context_reloads_and_workspace_settings_are_unchanged(setup):
    manager, models = setup
    models.instances[0]['config'] = {'context_length': 8192}
    profile = {'model': 'old-model', 'context_length': 32768, 'ai_memory_mode': 'exclusive'}
    ready = manager.run_ai('old-model', profile=profile)
    assert ('unload', 'old-instance') in models.calls
    assert ('load', 'old-model', {'context_length': 32768}) in models.calls
    assert ready['context_length'] == 32768 and manager.get_settings()['context_length'] == 8192
    models.calls.clear()
    assert manager.run_ai('old-model', profile=profile)['instance_id'] == ready['instance_id']
    assert not any(call[0] in ('load', 'unload') for call in models.calls)


def test_explicit_profile_with_unreported_loaded_context_reloads_owned_model(setup):
    manager, models = setup
    manager.run_ai('old-model', profile={'model': 'old-model', 'context_length': 16384, 'ai_memory_mode': 'exclusive'})
    assert ('unload', 'old-instance') in models.calls


@pytest.mark.parametrize('submitted', [False, True, None])
def test_pending_intent_clears_only_for_proven_pre_submission_failure(setup, submitted):
    from backend.lmstudio import LMStudioError
    manager, models = setup
    def load(model, **kwargs):
        raise LMStudioError('Synthetic load failure', load_submitted=submitted)
    models.load_owned_model = load
    with pytest.raises(LMStudioError):
        manager.run_ai('new-model')
    assert (manager.pending_load is None) is (submitted is False)
    assert manager._active_settings is None and not manager.lock.locked()
    if submitted is not False:
        with pytest.raises(resources.ResourceError, match='uncertain'):
            manager.run_ai('new-model')


def test_profile_model_mismatch_fails_without_touching_model(setup):
    manager, models = setup
    with pytest.raises(resources.ResourceError, match='requested model'):
        manager.run_ai('new-model', profile={'model': 'other', 'context_length': 8192, 'ai_memory_mode': 'exclusive'})
    assert models.calls == [] and not manager.lock.locked()
