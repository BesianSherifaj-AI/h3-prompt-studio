"""Take labels never queue work or rewrite immutable render snapshots."""
import copy
import json
import threading
import uuid

import pytest

from backend.video_runs import VideoRunError, VideoRunManager
from test_app import auth, no_hardware, server


def denied(*args, **kwargs):
    raise AssertionError('Take metadata must not access a model or ComfyUI.')


def make_manager(data):
    return VideoRunManager(data, denied, object(), client_factory=denied, start_workers=False)


def add_take(manager, project_id=None):
    project = {'id': project_id or str(uuid.uuid4()), 'duration': 5, 'comfy_render': {'seed': 41}}
    job = manager.submit(str(uuid.uuid4()), project, 'A neutral paper lantern sways gently.')
    manager._save(manager.records[job['id']], status='succeeded', stage='Video ready',
                  video={'filename': 'owned.mp4', 'subfolder': 'owned', 'type': 'output'},
                  continuation_source='owned/clip.mmh3')
    return job['id']


def test_title_and_favorite_persist_without_modifying_snapshot_or_render_identity(tmp_path):
    manager = make_manager(tmp_path)
    ident = add_take(manager)
    snapshot_path = manager.directory / ident / 'project.json'
    snapshot = snapshot_path.read_bytes()
    before = copy.deepcopy(manager.records[ident])
    assert manager.get(ident)['title'] == ''
    assert manager.get(ident)['favorite'] is False
    result = manager.update_metadata(ident, {'title': '  Lantern at dusk  ', 'favorite': True})
    assert result['title'] == 'Lantern at dusk'
    assert result['favorite'] is True
    assert result['video_url'].endswith(ident + '/video')
    assert snapshot_path.read_bytes() == snapshot
    for key, value in before.items():
        if key not in {'title', 'favorite', 'updated_at'}:
            assert manager.records[ident][key] == value
    reloaded = make_manager(tmp_path)
    assert reloaded.get(ident)['title'] == 'Lantern at dusk'
    assert reloaded.list()[0]['favorite'] is True
    assert reloaded.get(ident)['status'] == 'succeeded'


def test_patch_fields_are_independent_and_empty_title_restores_default(tmp_path):
    manager = make_manager(tmp_path)
    ident = add_take(manager)
    manager.update_metadata(ident, {'title': 'Best lantern', 'favorite': True})
    renamed = manager.update_metadata(ident, {'title': ''})
    assert renamed['title'] == '' and renamed['favorite'] is True
    unfavorited = manager.update_metadata(ident, {'favorite': False})
    assert unfavorited['title'] == '' and unfavorited['favorite'] is False


def test_legacy_records_have_stable_defaults(tmp_path):
    manager = make_manager(tmp_path)
    ident = add_take(manager)
    record = manager.records[ident]
    record.pop('title')
    record.pop('favorite')
    manager._save(record)
    reloaded = make_manager(tmp_path)
    assert reloaded.get(ident)['title'] == ''
    assert reloaded.get(ident)['favorite'] is False
    reloaded.update_metadata(ident, {'favorite': True})
    assert make_manager(tmp_path).get(ident)['favorite'] is True


@pytest.mark.parametrize('changes', [
    None, [], {}, {'seed': 100}, {'title': 'Allowed', 'status': 'succeeded'},
    {'title': None}, {'title': 123}, {'title': False}, {'title': 'x' * 81},
    {'title': 'two\nlines'}, {'title': 'nul\x00byte'}, {'title': 'tab\ttext'},
    {'favorite': 'true'}, {'favorite': 1}, {'favorite': 0}, {'favorite': None},
    {'title': 'Must not partially save', 'favorite': 'false'},
])
def test_invalid_patch_is_rejected_atomically(tmp_path, changes):
    manager = make_manager(tmp_path)
    ident = add_take(manager)
    record_path = manager.directory / ident / 'record.json'
    before = record_path.read_bytes()
    with pytest.raises(VideoRunError):
        manager.update_metadata(ident, changes)
    assert record_path.read_bytes() == before
    assert manager.records[ident]['title'] == ''
    assert manager.records[ident]['favorite'] is False


def test_maximum_title_and_unicode_are_preserved(tmp_path):
    manager = make_manager(tmp_path)
    ident = add_take(manager)
    title = 'Dritë ☀ ' + 'a' * 72
    assert len(title) == 80
    assert manager.update_metadata(ident, {'title': title})['title'] == title


def test_only_the_selected_owned_record_can_change(tmp_path):
    manager = make_manager(tmp_path)
    first, second = add_take(manager), add_take(manager)
    other_path = manager.directory / second / 'record.json'
    other_before = other_path.read_bytes()
    manager.update_metadata(first, {'title': 'Selected lantern', 'favorite': True})
    assert other_path.read_bytes() == other_before
    assert manager.get(second)['title'] == '' and manager.get(second)['favorite'] is False
    for missing in (str(uuid.uuid4()), '../escape', '', None):
        with pytest.raises(VideoRunError):
            manager.update_metadata(missing, {'favorite': True})
    assert len(manager.records) == 2


def test_metadata_waits_for_worker_lock_and_preserves_latest_progress(tmp_path):
    manager = make_manager(tmp_path)
    ident = add_take(manager)
    record = manager.records[ident]
    manager._save(record, status='running', stage='Sampling')
    entered, finished = threading.Event(), threading.Event()
    result = {}

    def update():
        entered.set()
        try:
            result['job'] = manager.update_metadata(ident, {'title': 'Keep this take', 'favorite': True})
        except Exception as exc:
            result['error'] = exc
        finally:
            finished.set()

    with manager.lock:
        thread = threading.Thread(target=update)
        thread.start()
        assert entered.wait(2)
        assert not finished.is_set()
        manager._save(record, status='succeeded', stage='Video ready', server_execution_seconds=63.2)
    thread.join(timeout=3)
    assert not thread.is_alive() and 'error' not in result
    assert result['job']['status'] == 'succeeded'
    assert result['job']['server_execution_seconds'] == 63.2
    assert manager.records[ident] is record
    saved = json.loads((manager.directory / ident / 'record.json').read_text(encoding='utf-8'))
    assert saved['title'] == 'Keep this take' and saved['favorite'] is True
    assert saved['status'] == 'succeeded' and saved['server_execution_seconds'] == 63.2


def test_api_metadata_requires_studio_session_and_owns_exact_run(server, monkeypatch):
    module, client, lm = server
    manager = make_manager(module.DATA)
    monkeypatch.setattr(module, 'VIDEO_RUNS', manager)
    first, second = add_take(manager), add_take(manager)
    endpoint = '/api/video/runs/' + first
    patch = {'title': 'Favorite lantern', 'favorite': True}
    assert client.patch(endpoint, json=patch).status_code == 403
    bridge = {'X-H3-Bridge': module.BRIDGE_TOKEN, 'Origin': 'http://127.0.0.1:8010'}
    assert client.patch(endpoint, headers=bridge, json=patch).status_code == 403
    assert manager.get(first)['title'] == ''
    response = client.patch(endpoint, headers=auth(module), json=patch)
    assert response.status_code == 200, response.text
    assert response.json()['title'] == patch['title'] and response.json()['favorite'] is True
    assert manager.get(second)['title'] == '' and manager.get(second)['favorite'] is False
    assert client.get(endpoint).json()['favorite'] is True
    invalid = client.patch(endpoint, headers=auth(module), json={'title': 'x' * 81})
    assert invalid.status_code == 400
    assert client.patch('/api/video/runs/' + str(uuid.uuid4()), headers=auth(module), json=patch).status_code == 400
    assert client.patch('/api/video/runs/not-owned', headers=auth(module), json=patch).status_code == 400
    assert manager.get(first)['title'] == patch['title']
    assert not lm.loads and not lm.unloads
