"""Saved-work recovery, selection and immutable portable export checks."""
import copy
import io
import json
import uuid
import zipfile

import pytest

from backend.projects import atomic_json, new_project
from test_app import auth, no_hardware, server, saved_project


def test_named_video_create_reopen_activation_and_no_empty_bootstrap_write(server):
    module, client, _ = server
    assert client.get('/api/bootstrap').status_code == 200
    assert not list((module.DATA / 'projects').glob('*.json'))
    a = client.post('/api/projects/new', json={'workspace': 'video', 'title': ' Lantern ', 'idea': 'A lantern glows.'}, headers=auth(module)).json()
    b = client.post('/api/projects/new', headers=auth(module)).json()
    assert a['title'] == 'Lantern' and a['story']['text'] == 'A lantern glows.' and a['profile'] == 'concise'
    assert module.SETTINGS['last_video_project'] == b['id']
    assert client.get('/api/projects/' + a['id']).json() == a
    assert module.SETTINGS['last_video_project'] == b['id']  # GET stays read-only.
    assert client.post('/api/projects/' + a['id'] + '/activate', headers=auth(module)).json() == a
    assert client.get('/api/bootstrap').json()['project'] == a
    assert client.post('/api/projects/' + b['id'] + '/activate').status_code == 403


def test_corrupt_project_and_library_record_do_not_hide_saved_work(server):
    module, client, _ = server
    project = saved_project(module, client)
    broken = module.DATA / 'projects' / (str(uuid.uuid4()) + '.json')
    broken.write_text('{partial', encoding='utf-8')
    library = client.post('/api/library/templates', json={'name': 'Saved setup', 'project': project}, headers=auth(module)).json()
    broken_library = module.DATA / 'library' / 'templates' / (str(uuid.uuid4()) + '.json')
    broken_library.write_text('{partial', encoding='utf-8')
    assert [p['id'] for p in client.get('/api/projects').json()] == [project['id']]
    assert client.get('/api/library').json()['templates'][0]['id'] == library['id']
    assert client.get('/api/projects/' + broken.stem).status_code == 409
    assert client.get('/api/library/templates/' + broken_library.stem).status_code == 409
    assert broken.read_text() == '{partial' and broken_library.read_text() == '{partial'


def test_video_migration_preserves_original_files_and_filters_film_owned_clips(server):
    module, client, _ = server
    old = new_project('studio')
    old['story_session_id'] = str(uuid.uuid4())
    atomic_json(module.DATA / 'stories' / (old['story_session_id'] + '.json'), {'mode': 'studio'})
    path = module.DATA / 'projects' / (old['id'] + '.json')
    atomic_json(path, old)
    before = path.read_bytes()
    film_clip = new_project('studio')
    film_clip['film_id'] = str(uuid.uuid4())
    module.save_project(film_clip)
    assert [p['id'] for p in client.get('/api/projects?workspace=video').json()] == [old['id']]
    assert [p['id'] for p in client.get('/api/projects?workspace=studio').json()] == [film_clip['id']]
    assert client.get('/api/bootstrap').json()['project'] == old
    assert path.read_bytes() == before
    assert module.STORIES is None and module.VIDEO_RUNS is None


def test_project_catalog_shows_manual_review_only_and_no_monitor_workers(server):
    module, client, _ = server
    project = saved_project(module, client)
    run_id = str(uuid.uuid4())
    atomic_json(module.DATA / 'video_runs' / run_id / 'record.json',
                {'id': run_id, 'project_id': project['id'], 'status': 'succeeded', 'video': {'filename': 'v.mp4'}, 'created_at': 100})
    atomic_json(module.DATA / 'reviews' / 'run' / run_id / 'review.json', {'verdict': 'unreviewed', 'ai': {'verdict': 'approved'}})
    row = client.get('/api/projects').json()[0]
    assert row['video_count'] == 1 and row['reference_count'] == 1 and row['shot_count'] == 1
    assert row['last_review_verdict'] == 'unreviewed' and row['prompt_summary'] == project['story']['text']
    assert row['updated_at'] and module.VIDEO_RUNS is None


def test_export_creates_immutable_zip_copies_and_cleans_failed_archive(server):
    module, client, _ = server
    project = saved_project(module, client)
    first = module.export_project(project['id'])
    first_bytes = first.path.read_bytes()
    project['title'] = 'New title'
    module.save_project(project)
    second = module.export_project(project['id'])
    assert first.path != second.path and first.path.read_bytes() == first_bytes
    with zipfile.ZipFile(io.BytesIO(second.path.read_bytes())) as archive:
        assert json.loads(archive.read('project.json'))['title'] == 'New title'
    (module.DATA / 'assets' / project['assets'][0]['id'] / 'source.png').unlink()
    before = set((module.DATA / 'exports').iterdir())
    assert client.get('/api/projects/' + project['id'] + '/export').status_code == 400
    assert set((module.DATA / 'exports').iterdir()) == before


def test_activation_disk_failure_does_not_publish_unsaved_selection(server, monkeypatch):
    module, client, _ = server
    a = client.post('/api/projects/new', headers=auth(module)).json()
    b = client.post('/api/projects/new', headers=auth(module)).json()
    before = copy.deepcopy(module.SETTINGS)
    def fail(*args, **kwargs):
        raise OSError('Disk unavailable')
    monkeypatch.setattr(module, 'atomic_json', fail)
    assert client.post('/api/projects/' + a['id'] + '/activate', headers=auth(module)).status_code == 502
    assert module.SETTINGS == before and module.SETTINGS['last_video_project'] == b['id']
