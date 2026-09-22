"""Settings and queued work retain independent model/context/memory choices."""
import copy
import json

import pytest

from backend.assistant_profiles import migrate_profiles, merge_profile_settings, resolve_profile
from backend.projects import new_project
from test_app import auth, no_hardware, server
from test_stories import rig, story, uid


def profiles():
    return {'studio': {'model': 'large-studio', 'context_length': 16384, 'ai_memory_mode': 'exclusive'},
            'game': {'model': 'cpu-game', 'context_length': 8192, 'ai_memory_mode': 'resident_cpu'}}


def test_legacy_migration_is_offline_and_copies_existing_choices():
    old = {'model': 'chosen-27b', 'context_length': 32768, 'ai_memory_mode': 'exclusive', 'lm_url': 'offline'}
    migrated = migrate_profiles(old)
    assert 'assistant_profiles' not in old
    assert migrated['assistant_profiles']['studio'] == {key: old[key] for key in ('model', 'context_length', 'ai_memory_mode')}
    assert migrated['assistant_profiles']['game'] == migrated['assistant_profiles']['studio']
    migrated['assistant_profiles']['game']['model'] = 'other'
    assert migrated['assistant_profiles']['studio']['model'] == 'chosen-27b'
    assert migrated['lm_url'] == 'offline'


def test_partial_merge_preserves_other_workspace_and_legacy_flat_compatibility():
    original = {'assistant_profiles': profiles()}
    saved = merge_profile_settings(original, {'assistant_profiles': {'game': {'model': 'new-cpu'}}})
    assert saved['assistant_profiles']['studio'] == profiles()['studio']
    assert saved['assistant_profiles']['game'] == {**profiles()['game'], 'model': 'new-cpu'}
    assert saved['model'] == 'large-studio'
    legacy = merge_profile_settings(saved, {'context_length': 24576})
    assert all(profile['context_length'] == 24576 for profile in legacy['assistant_profiles'].values())
    assert original['assistant_profiles'] == profiles()


@pytest.mark.parametrize('patch', [None, {'unknown': {}}, {'game': None}, {'game': {'context_length': True}},
    {'game': {'context_length': 1}}, {'game': {'model': ' '}}, {'game': {'ai_memory_mode': 'anything'}},
    {'game': {'unknown': 1}}])
def test_invalid_profile_patch_does_not_mutate_settings(patch):
    settings = {'assistant_profiles': profiles()}
    with pytest.raises(ValueError):
        merge_profile_settings(settings, {'assistant_profiles': patch})
    assert settings['assistant_profiles'] == profiles()


def test_profile_save_persists_full_response_and_keeps_context_offline(server):
    module, http, _ = server
    response = http.post('/api/settings', headers=auth(module), json={'assistant_profiles': profiles()})
    assert response.status_code == 200, response.text
    assert response.json()['assistant_profiles'] == profiles()
    response = http.post('/api/settings', headers=auth(module), json={'assistant_profiles': {'game': {'model': 'another-cpu'}}})
    saved = response.json()
    assert saved['assistant_profiles']['studio'] == profiles()['studio']
    assert saved['assistant_profiles']['game']['context_length'] == 8192
    assert json.loads((module.DATA / 'settings.json').read_text()) == saved
    assert http.get('/api/bootstrap').json()['settings'] == saved


def test_failed_disk_save_keeps_live_settings(server, monkeypatch):
    module, http, _ = server
    before = copy.deepcopy(module.SETTINGS)
    def fail(*args):
        raise OSError('Disk unavailable')
    monkeypatch.setattr(module, 'atomic_json', fail)
    response = http.post('/api/settings', headers=auth(module), json={'assistant_profiles': profiles()})
    assert response.status_code == 502
    assert module.SETTINGS == before


def test_profile_edits_allowed_during_frozen_work_but_endpoint_change_waits(server):
    module, http, _ = server
    module.RESOURCES.lock.acquire()
    try:
        response = http.post('/api/settings', headers=auth(module), json={'assistant_profiles': profiles()})
        assert response.status_code == 200
        response = http.post('/api/settings', headers=auth(module), json={'lm_url': 'http://127.0.0.1:1235/v1'})
        assert response.status_code == 409
    finally:
        module.RESOURCES.lock.release()


def test_prepare_and_analysis_resolve_requested_workspace(server, monkeypatch):
    module, http, fake = server
    module.SETTINGS.update(assistant_profiles=profiles())
    calls = []
    def run(model, operation=None, *, profile=None):
        calls.append(copy.deepcopy(profile))
        return operation(model) if operation else {'ready': True, 'context_length': profile['context_length']}
    monkeypatch.setattr(module.RESOURCES, 'run_ai', run)
    monkeypatch.setattr(module, 'image_data', lambda asset: 'image')
    monkeypatch.setattr(fake, 'analyse_image', lambda *args: {'observation': 'visible'}, raising=False)
    assert http.post('/api/ai/prepare', headers=auth(module), json={'workspace': 'game'}).status_code == 200
    assert calls[-1] == profiles()['game']
    project = new_project('game')
    assert http.post('/api/ai/analyse', headers=auth(module), json={
        'asset': {'id': uid()}, 'project': project, 'workspace': 'studio'}).status_code == 200
    assert calls[-1] == profiles()['game'], 'Project ownership overrides a stale browser tab.'
    assert http.post('/api/gpu/prepare-ai', headers=auth(module), json={'model': 'legacy-choice'}).status_code == 200
    assert calls[-1] == {**profiles()['studio'], 'model': 'legacy-choice'}


def test_game_turn_freezes_complete_profile_and_reopens_with_it(rig):
    settings = {'assistant_profiles': profiles()}
    rig.manager.get_settings = lambda: copy.deepcopy(settings)
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I ask Nora about the key.'})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    frozen = copy.deepcopy(turn['assistant_profile'])
    assert frozen == profiles()['game']
    settings['assistant_profiles']['game'] = profiles()['studio']
    seen = []
    rig.resources.run_ai = lambda model, operation, *, profile=None: seen.append(copy.deepcopy(profile)) or operation(model)
    rig.manager.plan(rig.manager._execution(record, turn), turn, turn['snapshot']['project'])
    assert seen == [frozen]
    reopened = rig.reopen()
    try:
        restored = reopened._turn(reopened._story(session['id']), public['id'])
        assert restored['assistant_profile'] == frozen
    finally:
        reopened.close()


@pytest.mark.parametrize('action,context', [('retry', 32768), ('resume', 8192)])
def test_retry_changes_context_without_mixing_cached_stages_but_resume_preserves_it(rig, action, context):
    settings = {'assistant_profiles': profiles()}
    rig.manager.get_settings = lambda: copy.deepcopy(settings)
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I ask Nora about the key.'})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    turn['assistant_requests'] = {'first': {'id': uid(), 'stage': 'actor', 'status': 'completed', 'result': {'answer': 'Old'}}}
    rig.manager._change(record, turn, status='failed', error='Invalid response')
    settings['assistant_profiles']['game']['context_length'] = 32768
    rig.manager.action(session['id'], turn['id'], action, {'request_id': uid()})
    assert turn['assistant_profile']['context_length'] == context
    assert bool(turn['assistant_requests']) == (action == 'resume')


def test_legacy_resume_keeps_original_model_and_acquires_context_once(rig):
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I wait.'})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    turn.pop('assistant_profile')
    rig.manager.get_settings = lambda: {'assistant_profiles': profiles()}
    first = rig.manager._assistant_profile(record, turn)
    assert first == {**profiles()['game'], 'model': 'fake-local-vision'}
    rig.manager.get_settings = lambda: {'model': 'unrelated', 'context_length': 65536}
    assert rig.manager._assistant_profile(record, turn) == first
