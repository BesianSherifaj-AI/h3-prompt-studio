"""Workspace isolation uses temporary data; no model or render jobs are started."""
import copy
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.projects import atomic_json, check_project, new_project
from test_app import auth, no_hardware, server


def test_studio_and_game_projects_have_independent_indexes_and_resume_pointers(server):
    module, client, _ = server
    studio = client.post('/api/projects/new', headers=auth(module)).json()
    studio['story']['text'] = 'Private Studio draft'
    client.post('/api/projects', headers=auth(module), json=studio).raise_for_status()
    game = client.post('/api/projects/new?workspace=game', headers=auth(module)).json()
    assert game['workspace'] == 'game' and game['id'] != studio['id']
    assert game['story']['text'] == '' and game['assets'] == []
    assert module.SETTINGS['last_project'] == studio['id']
    assert module.SETTINGS['last_game_project'] == game['id']
    assert [p['id'] for p in client.get('/api/projects').json()] == [studio['id']]
    assert [p['id'] for p in client.get('/api/projects?workspace=game').json()] == [game['id']]
    bootstrap = client.get('/api/bootstrap').json()
    assert bootstrap['project'] == studio
    assert bootstrap['game_project'] == game
    assert [p['id'] for p in bootstrap['projects']] == [studio['id']]


def test_bootstrap_recovers_from_legacy_game_last_project_without_changing_files(server):
    module, client, _ = server
    studio = client.post('/api/projects/new', headers=auth(module)).json()
    studio.pop('workspace')  # Projects from earlier releases remain compatible.
    atomic_json(module.DATA / 'projects' / (studio['id'] + '.json'), studio)
    legacy_game = copy.deepcopy(studio)
    legacy_game.update(id=str(uuid.uuid4()), story_session_id=str(uuid.uuid4()), title='Game turn')
    story_path = module.DATA / 'stories' / (legacy_game['story_session_id'] + '.json')
    atomic_json(story_path, {'id': legacy_game['story_session_id'], 'mode': 'game'})
    game_path = module.DATA / 'projects' / (legacy_game['id'] + '.json')
    atomic_json(game_path, legacy_game)
    before = game_path.read_bytes(), story_path.read_bytes()
    module.SETTINGS['last_project'] = legacy_game['id']
    bootstrap = client.get('/api/bootstrap').json()
    assert bootstrap['project'] == studio
    assert [p['id'] for p in bootstrap['projects']] == [studio['id']]
    assert bootstrap['game_project']['workspace'] == 'game'
    assert bootstrap['game_project']['assets'] == []
    assert bootstrap['game_project']['story']['text'] == ''
    assert [p['id'] for p in client.get('/api/projects?workspace=game').json()] == [legacy_game['id']]
    assert before == (game_path.read_bytes(), story_path.read_bytes())
    assert module.STORIES is None and module.VIDEO_RUNS is None


def test_legacy_game_save_does_not_replace_studio_resume_target(server):
    module, client, _ = server
    studio = client.post('/api/projects/new', headers=auth(module)).json()
    game = new_project()
    game.pop('workspace')
    game['story_session_id'] = str(uuid.uuid4())
    atomic_json(module.DATA / 'stories' / (game['story_session_id'] + '.json'), {'mode': 'game'})
    response = client.post('/api/projects', headers=auth(module), json=game)
    assert response.status_code == 200
    assert module.SETTINGS['last_project'] == studio['id']
    assert module.SETTINGS['last_game_project'] == game['id']


@pytest.mark.parametrize('workspace', ['', 'all', 'ref2va', None, [], {}])
def test_invalid_workspace_is_rejected_before_saving(workspace, server):
    module, client, _ = server
    project = new_project()
    project['workspace'] = workspace
    with pytest.raises(ValueError, match='workspace'):
        check_project(project)
    assert client.post('/api/projects', headers=auth(module), json=project).status_code == 400
    assert not (module.DATA / 'projects' / (project['id'] + '.json')).exists()


def test_unknown_workspace_queries_are_client_errors(server):
    module, client, _ = server
    assert client.get('/api/projects?workspace=unknown').status_code == 400
    assert client.post('/api/projects/new?workspace=unknown', headers=auth(module)).status_code == 400
    assert client.get('/api/stories?mode=unknown').status_code == 400
    assert module.STORIES is None


def test_portable_exports_keep_legacy_workspace_and_imports_detach_story_identity(server):
    module, client, _ = server
    original = new_project()
    original.pop('workspace')
    original['story_session_id'] = str(uuid.uuid4())
    atomic_json(module.DATA / 'stories' / (original['story_session_id'] + '.json'), {'mode': 'game'})
    module.save_project(original)
    source_path = module.DATA / 'projects' / (original['id'] + '.json')
    before = source_path.read_bytes()
    exported = client.get('/api/projects/' + original['id'] + '/export')
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert json.loads(archive.read('project.json'))['workspace'] == 'game'
    for workspace in (None, 'studio'):
        path = '/api/projects/import' + ('?workspace=' + workspace if workspace else '')
        response = client.post(path, headers=auth(module),
                               files={'file': ('project.zip', exported.content, 'application/zip')})
        assert response.status_code == 200
        imported = response.json()
        assert imported['workspace'] == (workspace or 'game')
        assert imported['id'] != original['id'] and 'story_session_id' not in imported
        assert imported['story'] == original['story']
    assert source_path.read_bytes() == before


def test_json_import_workspace_conversion_detaches_story_link_and_validates_query(server):
    module, client, _ = server
    game = new_project('game')
    game['story_session_id'] = str(uuid.uuid4())
    files = {'file': ('project.json', json.dumps(game), 'application/json')}
    response = client.post('/api/projects/import?workspace=studio', headers=auth(module), files=files)
    assert response.status_code == 200
    assert response.json()['workspace'] == 'studio' and 'story_session_id' not in response.json()
    assert client.post('/api/projects/import?workspace=invalid', headers=auth(module), files=files).status_code == 400


def test_concurrent_workspace_and_settings_saves_keep_both_resume_pointers(server):
    module, _, _ = server
    projects = [new_project(workspace) for workspace in ('studio', 'game') for _ in range(5)]
    def save(project):
        module.save_project(project)
        module.save_settings({'context_length': 8192})
        return module.bootstrap()
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(save, projects))
    assert all(snapshot['project']['workspace'] == 'studio' and snapshot['game_project']['workspace'] == 'game'
               for snapshot in snapshots)
    settings = json.loads((module.DATA / 'settings.json').read_text(encoding='utf-8'))
    assert settings == module.SETTINGS
    assert settings['last_project'] in {p['id'] for p in projects if p['workspace'] == 'studio'}
    assert settings['last_game_project'] in {p['id'] for p in projects if p['workspace'] == 'game'}
    previous = copy.deepcopy(snapshots[-1]['settings'])
    module.save_project(new_project())
    assert snapshots[-1]['settings'] == previous


@pytest.mark.parametrize('route', ['/studio', '/game', '/studio/', '/game/'])
def test_workspace_deep_links_serve_the_built_app(route, server, tmp_path, monkeypatch):
    module, client, _ = server
    root = tmp_path / 'app'
    (root / 'dist').mkdir(parents=True)
    (root / 'dist' / 'index.html').write_text('<main>Studio and Game</main>', encoding='utf-8')
    monkeypatch.setattr(module, 'ROOT', root)
    response = client.get(route)
    assert response.status_code == 200
    assert response.text == '<main>Studio and Game</main>'
    assert response.headers['content-type'].startswith('text/html')
