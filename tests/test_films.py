"""Film authoring and durable queues use mocked AI and never start hardware."""
import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from backend import films
from backend.compiler import compile_project
from backend.production import ProductionManager
from backend.projects import atomic_json
from test_app import auth, no_hardware, server, upload


def complete_scenes(film):
    result = copy.deepcopy(film['shots'])
    for index, scene in enumerate(result):
        scene.update(action=f'A paper boat drifts from marker {index} to marker {index + 1}.',
                     setting='A quiet pond in afternoon light.', final_state=f'The boat rests beside marker {index + 1}.')
    return result


def test_film_create_save_reopen_and_revision_conflict_without_starting_jobs(server):
    module, client, _ = server
    assert client.post('/api/films', json={'title': 'Film'}).status_code == 403
    response = client.post('/api/films', json={'title': ' Pond film ', 'idea': 'A paper boat reaches the shore.',
                                             'target_minutes': 1}, headers=auth(module))
    assert response.status_code == 200, response.text
    film = response.json()
    assert film['title'] == 'Pond film' and film['clip_seconds'] == 15 and len(film['shots']) == 4
    assert film['revision'] == 1 and film['latest_batch_id'] is None and film['batch_history'] == []
    assert module.VIDEO_RUNS is None and module.ASSET_RUNS is None and module.PRODUCTION is None
    base = '/api/films/' + film['id']
    saved = client.patch(base, json={'expected_revision': 1, 'shots': complete_scenes(film)}, headers=auth(module)).json()
    assert saved['revision'] == 2 and saved['status'] == 'storyboard_ready'
    assert client.get(base).json() == saved
    assert client.get('/api/films').json()['films'] == [saved]
    stale = client.patch(base, json={'expected_revision': 1, 'title': 'Stale overwrite'}, headers=auth(module))
    assert stale.status_code == 409 and client.get(base).json()['title'] == 'Pond film'


@pytest.mark.parametrize('body', [{'target_minutes': 0}, {'target_minutes': 11}, {'target_minutes': 1.5},
                                  {'title': ''}, {'idea': 'x' * 20001}, {'quality': 'ultra'},
                                  {'shots': ['bad']}, {'shots': {'bad': True}},
                                  {'latest_batch_id': str(uuid.uuid4())}])
def test_invalid_film_create_has_no_writes(server, body):
    module, client, _ = server
    assert client.post('/api/films', json=body, headers=auth(module)).status_code == 400
    assert not list((module.DATA / 'films').glob('*.json'))


def test_film_produce_freezes_four_compiled_clips_and_is_idempotent_without_render(server, monkeypatch):
    module, client, _ = server
    def denied(*args, **kwargs):
        raise AssertionError('Film authoring must not start model, image or video work')
    production = ProductionManager(module.DATA, module.load_project, denied, denied, start_workers=False)
    monkeypatch.setattr(module, 'PRODUCTION', production)
    film = module.film_manager().create({'title': 'Boat', 'target_minutes': 1})
    film = module.film_manager().save(film['id'], {'expected_revision': 1, 'shots': complete_scenes(film)})
    revision, request_id = film['revision'], str(uuid.uuid4())
    response = client.post(f"/api/films/{film['id']}/produce", json={'expected_revision': revision, 'request_id': request_id}, headers=auth(module))
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['batch']['status'] == 'draft' and value['batch']['total'] == 4
    assert value['film']['batch_history'] == [request_id] and value['film']['batch_revisions'][request_id] == revision
    assert value['film']['batch_fingerprints'][request_id] == value['film']['render_fingerprint']
    assert not production.workers and module.VIDEO_RUNS is None and module.ASSET_RUNS is None
    for item in value['batch']['items']:
        project = module.load_project(item['project_id'])
        assert project['film_id'] == film['id'] and project['duration'] == 15
        assert project['comfy_render']['steps'] == 4 and compile_project(project)['valid']
    replay = client.post(f"/api/films/{film['id']}/produce", json={'expected_revision': revision, 'request_id': request_id}, headers=auth(module))
    assert replay.status_code == 200 and replay.json() == value
    assert len(client.get('/api/projects?workspace=studio').json()) == 4
    assert client.get('/api/projects?workspace=video').json() == []
    restored = ProductionManager(module.DATA, module.load_project, denied, denied, start_workers=False)
    assert restored.get(request_id)['status'] == 'draft' and len(restored.get(request_id)['items']) == 4


def test_film_references_dialogue_and_duplicate_history_are_independent(server, monkeypatch):
    module, client, _ = server
    asset = upload(module, client)
    film = module.film_manager().create({'title': 'Speaker film', 'target_minutes': 1})
    scenes = complete_scenes(film)
    scenes[0]['dialogue'] = [{'speaker': 'Mira', 'text': '  Përshëndetje! Exact words.  ', 'language': 'Albanian'}]
    film = module.film_manager().save(film['id'], {'expected_revision': 1, 'shots': scenes, 'references': [asset], 'quality': 'quality'})
    project = films.clip_project(film, film['shots'][0], 0, str(uuid.uuid4()))
    assert project['mode'] == 'ref2va' and project['assets'][0]['id'] == asset['id']
    assert project['comfy_render']['steps'] == 8 and compile_project(project)['valid']
    assert project['shots'][0]['dialogue'][0]['text'] == scenes[0]['dialogue'][0]['text']
    assert project['subjects'][0]['name'] == 'Mira'
    clone = module.film_manager().create({key: film[key] for key in films.EDITABLE})
    assert clone['id'] != film['id'] and clone['batch_history'] == [] and clone['latest_batch_id'] is None
    assert all(a['id'] != b['id'] for a, b in zip(film['shots'], clone['shots']))
    assert clone['shots'][0]['dialogue'] == film['shots'][0]['dialogue']
    assert clone['references'][0]['id'] == asset['id']


def test_film_plan_cannot_replace_edits_made_during_model_call(server, monkeypatch):
    module, client, _ = server
    film = module.film_manager().create({'title': 'Film', 'idea': 'A boat drifts.'})
    def lease(model, operation, *, profile):
        module.film_manager().save(film['id'], {'expected_revision': 1, 'title': 'User edit during planning'})
        return complete_scenes(film)
    monkeypatch.setattr(module.RESOURCES, 'run_ai', lease)
    response = client.post(f"/api/films/{film['id']}/plan", json={'expected_revision': 1}, headers=auth(module))
    assert response.status_code == 409
    assert module.film_manager().get(film['id'])['title'] == 'User edit during planning'
    assert all(not shot['action'] for shot in module.film_manager().get(film['id'])['shots'])
    assert module.VIDEO_RUNS is None and module.PRODUCTION is None


def test_ten_minute_plan_uses_whole_story_outline_bounded_detail_calls_and_no_invented_speech(tmp_path):
    manager = films.FilmManager(tmp_path, lambda key: {})
    film = manager.create({'title': 'Long boat story', 'target_minutes': 10, 'idea': 'A boat reaches the shore.'})
    calls = []
    def complete(model, system, content, schema, **kwargs):
        calls.append((content, kwargs))
        if 'beats' in schema['properties']:
            return {'beats': [{'title': f'Beat {i}', 'action': f'Boat moves to marker {i}.'} for i in range(40)]}
        count = schema['properties']['shots']['minItems']
        return {'shots': [{'title': 'Boat drifts', 'action': 'A boat drifts steadily.', 'setting': 'A pond.',
                           'final_state': 'Boat rests by a marker.', 'sound': 'Soft water.',
                           'camera': {'framing': 'wide', 'movement': 'static'},
                           'dialogue': [{'speaker': 'Invented narrator', 'text': 'Made up words.', 'language': 'English'}]}
                          for _ in range(count)]}
    result = films.plan_storyboard(SimpleNamespace(complete_json=complete), 'qwen-instance', film)
    assert len(result) == 40 and len(calls) == 6
    assert all(call[1]['max_tokens'] <= 4096 for call in calls)
    assert all(not shot['dialogue'] for shot in result)
    assert all(a['id'] == b['id'] for a, b in zip(result, film['shots']))


def test_queue_creation_failure_does_not_poison_stable_request_id(tmp_path, monkeypatch):
    from backend import production as production_module
    manager = films.FilmManager(tmp_path, lambda key: {})
    film = manager.create({'title': 'Boat'})
    film = manager.save(film['id'], {'expected_revision': 1, 'shots': complete_scenes(film)})
    def load(key):
        return json.loads((tmp_path / 'projects' / (key + '.json')).read_text())
    production = ProductionManager(tmp_path, load, lambda: None, lambda: None, start_workers=False)
    request_id = str(uuid.uuid4())
    original = production_module.atomic_json
    calls = 0
    def fail_snapshot(path, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('Simulated interrupted snapshot write')
        return original(path, value)
    monkeypatch.setattr(production_module, 'atomic_json', fail_snapshot)
    with pytest.raises(OSError):
        manager.produce(film['id'], film['revision'], request_id, production)
    assert not (tmp_path / 'production' / request_id).exists()
    assert not list((tmp_path / 'production').glob('*.building'))
    monkeypatch.setattr(production_module, 'atomic_json', original)
    retry = manager.produce(film['id'], film['revision'], request_id, production)
    assert retry['batch']['status'] == 'draft' and retry['batch']['total'] == 4


def test_saved_render_fingerprint_changes_only_with_editable_directions(server, monkeypatch):
    module, _, _ = server
    manager = module.film_manager()
    film = manager.create({'title': 'Boat'})
    unchanged = manager.save(film['id'], {'expected_revision': 1, 'title': 'Boat'})
    assert unchanged['revision'] == 2 and unchanged['render_fingerprint'] == film['render_fingerprint']
    changed = manager.save(film['id'], {'expected_revision': 2, 'style': 'Evening light'})
    assert changed['render_fingerprint'] != film['render_fingerprint']


@pytest.mark.parametrize('corrupt', ['bad-date', float('nan'), True, None])
def test_corrupt_film_dates_cannot_break_entire_library(tmp_path, corrupt):
    manager = films.FilmManager(tmp_path, lambda key: {})
    healthy = manager.create({'title': 'Readable film'})
    damaged = manager.create({'title': 'Damaged film'})
    damaged['updated_at'] = corrupt
    path = manager._path(damaged['id'])
    path.write_text(json.dumps(damaged), encoding='utf-8')
    before = path.read_bytes()
    assert [film['id'] for film in manager.list()] == [healthy['id']]
    with pytest.raises(ValueError, match='local file is preserved'):
        manager.get(damaged['id'])
    assert path.read_bytes() == before


def test_nonobject_film_record_is_unreadable_without_raw_attribute_error(tmp_path):
    manager = films.FilmManager(tmp_path, lambda key: {})
    film = manager.create({'title': 'Damaged film'})
    manager._path(film['id']).write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='local file is preserved'):
        manager.get(film['id'])
