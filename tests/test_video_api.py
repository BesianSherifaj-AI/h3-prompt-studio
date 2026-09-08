"""Neutral API integration tests: temporary data, fake jobs/HTTP/ffmpeg only."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import uuid

import pytest
from PIL import Image

from backend.compiler import compile_project
from backend.projects import new_project
from test_app import auth, no_hardware, server

# Retain the real CPU process runner before per-test hardware guards replace it.
REAL_CPU_RUN = subprocess.run


def neutral_project():
    project = new_project()
    project['mode'] = 't2va'
    project['story']['text'] = 'A paper lantern glows on a quiet wooden table.'
    project['shots'][0]['action'] = 'The lantern sways gently.'
    project['comfy_render'] = {'seed': 101, 'resolution': '0.3', 'save_mmh3': True}
    return project


class FakeManager:
    def __init__(self):
        self.calls = []
        self.ident = str(uuid.uuid4())
        self.project = neutral_project()
        self.job = {'id': self.ident, 'project_id': self.project['id'], 'status': 'succeeded', 'seed': 101}

    def submit(self, request_id, project, prompt, parent_run_id=None):
        self.calls.append(('submit', request_id, copy.deepcopy(project), prompt, parent_run_id))
        return copy.deepcopy(self.job)

    def list(self, project_id):
        self.calls.append(('list', project_id)); return [copy.deepcopy(self.job)]

    def refresh(self, run_id):
        self.calls.append(('refresh', run_id)); return copy.deepcopy(self.job)

    def reroll(self, request_id, run_id):
        self.calls.append(('reroll', request_id, run_id)); return copy.deepcopy(self.job)

    def combine(self, request_id, run_id):
        self.calls.append(('combine', request_id, run_id)); return copy.deepcopy(self.job)

    def snapshot(self, run_id):
        self.calls.append(('snapshot', run_id)); return copy.deepcopy(self.project)

    def media(self, run_id):
        self.calls.append(('media', run_id))
        return {'comfy_url': 'http://127.0.0.1:8010', 'filename': 'owned-result.mp4', 'subfolder': 'h3_prompt_studio/test', 'type': 'output'}


@pytest.fixture
def manager(server, monkeypatch):
    module, _, _ = server
    value = FakeManager()
    monkeypatch.setattr(module, 'VIDEO_RUNS', value)
    return value


def test_create_requires_exact_current_compile_and_forwards_parent_without_project_writes(server, manager):
    module, client, lm = server
    project = neutral_project(); original = copy.deepcopy(project)
    compiled = compile_project(project)
    assert compiled['valid']
    request_id, parent = str(uuid.uuid4()), str(uuid.uuid4())
    response = client.post('/api/video/runs', headers=auth(module), json={
        'request_id': request_id, 'project': project, 'prompt': compiled['prompt'], 'parent_run_id': parent})
    assert response.status_code == 200, response.text
    assert manager.calls == [('submit', request_id, original, compiled['prompt'], parent)]
    assert project == original
    assert not list((module.DATA / 'projects').glob('*.json'))
    assert not lm.loads and not lm.unloads


@pytest.mark.parametrize('prompt', ['stale text', '', None])
def test_stale_or_missing_prompt_never_reaches_job_admission(server, manager, prompt):
    module, client, _ = server
    response = client.post('/api/video/runs', headers=auth(module), json={
        'request_id': str(uuid.uuid4()), 'project': neutral_project(), 'prompt': prompt})
    assert response.status_code == 400
    assert 'current valid prompt' in response.json()['detail']
    assert manager.calls == []


def test_invalid_project_and_missing_session_never_reach_manager(server, manager):
    module, client, _ = server
    assert client.post('/api/video/runs', json={}).status_code == 403
    assert client.post('/api/video/runs', headers=auth(module), json={'project': None}).status_code == 400
    assert client.post('/api/video/runs/'+manager.ident+'/reroll', json={'request_id': str(uuid.uuid4())}).status_code == 403
    assert client.post('/api/video/runs/'+manager.ident+'/ending-image', json={}).status_code == 403
    assert not manager.calls


def test_list_get_snapshot_reroll_and_combine_use_exact_selected_identifiers(server, manager):
    module, client, _ = server
    ident, project_id, request_id = manager.ident, manager.project['id'], str(uuid.uuid4())
    assert client.get('/api/video/runs?project_id='+project_id).json() == {'runs': [manager.job]}
    assert client.get('/api/video/runs/'+ident).json() == manager.job
    assert client.get('/api/video/runs/'+ident+'/project').json() == manager.project
    for action in ('reroll', 'combine'):
        response = client.post('/api/video/runs/'+ident+'/'+action, headers=auth(module), json={'request_id': request_id})
        assert response.status_code == 200
    assert manager.calls == [('list', project_id), ('refresh', ident), ('snapshot', ident),
                             ('reroll', request_id, ident), ('combine', request_id, ident)]
    count = len(manager.calls)
    assert client.get('/api/video/runs?project_id=not-a-project').status_code == 400
    assert client.get('/api/video/runs/not-a-run/project').status_code == 400
    assert len(manager.calls) == count


def fake_stream(monkeypatch, module, chunks, error=None):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): pass
        def iter_bytes(self, size):
            assert size == 1024 * 1024
            yield from chunks
            if error: raise error
    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {'trust_env': False, 'follow_redirects': False, 'timeout': 60}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def stream(self, method, target, params):
            calls.append((method, target, params)); return Response()
    monkeypatch.setattr(module.httpx, 'Client', Client)
    return calls


def test_video_stream_is_cached_and_file_response_supports_download_and_range(server, manager, monkeypatch):
    module, client, _ = server
    calls = fake_stream(monkeypatch, module, [b'neutral-', b'media-bytes'])
    endpoint = '/api/video/runs/'+manager.ident+'/video'
    response = client.get(endpoint)
    assert response.status_code == 200 and response.content == b'neutral-media-bytes'
    assert response.headers['content-type'] == 'video/mp4'
    downloaded = client.get(endpoint+'?download=true')
    assert downloaded.status_code == 200 and 'attachment;' in downloaded.headers['content-disposition']
    assert manager.ident+'.mp4' in downloaded.headers['content-disposition']
    partial = client.get(endpoint, headers={'Range': 'bytes=0-6'})
    assert partial.status_code == 206 and partial.content == b'neutral'
    assert calls == [('GET', 'http://127.0.0.1:8010/view', {'filename': 'owned-result.mp4', 'subfolder': 'h3_prompt_studio/test', 'type': 'output'})]
    assert not list((module.DATA/'video_runs'/manager.ident).glob('*.part'))


@pytest.mark.parametrize('empty', [True, False])
def test_empty_or_failed_download_leaves_no_playable_cache_or_partial_file(server, manager, monkeypatch, empty):
    module, client, _ = server
    fake_stream(monkeypatch, module, [] if empty else [b'incomplete'], None if empty else RuntimeError('Synthetic download interrupted'))
    response = client.get('/api/video/runs/'+manager.ident+'/video')
    assert response.status_code == (400 if empty else 502)
    folder = module.DATA/'video_runs'/manager.ident
    assert not (folder/'playback.mp4').exists()
    assert not list(folder.glob('*.part'))


def test_oversized_download_is_bounded_without_allocating_large_media(server, manager, monkeypatch):
    module, client, _ = server
    class PretendLarge(bytes):
        def __len__(self): return 512 * 1024 * 1024 + 1
    fake_stream(monkeypatch, module, [PretendLarge(b'x')])
    response = client.get('/api/video/runs/'+manager.ident+'/video')
    assert response.status_code == 400 and 'too large' in response.json()['detail']
    assert not (module.DATA/'video_runs'/manager.ident/'playback.mp4').exists()


def test_actual_ending_asset_is_context_only_and_cached_per_selected_run(server, manager, monkeypatch):
    module, client, lm = server
    folder = module.DATA/'video_runs'/manager.ident; folder.mkdir(parents=True)
    playback = folder/'playback.mp4'; playback.write_bytes(b'neutral fixture; not executed')
    monkeypatch.setattr(module, 'cached_run_video', lambda ident: playback if ident == manager.ident else pytest.fail('Wrong run'))
    processes = []
    def decode(command, **kwargs):
        processes.append((command, kwargs))
        assert command[:6] == ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-sseof']
        assert command[6] == '-1' and command[7] == '-i' and command[8] == str(playback)
        assert 'reverse,scale=min(1024\\,iw):-2' in command
        assert kwargs['timeout'] == 45 and kwargs['capture_output'] is True
        Image.new('RGB', (64, 32), '#cbb98e').save(command[-1])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', decode)
    endpoint = '/api/video/runs/'+manager.ident+'/ending-image'
    response = client.post(endpoint, headers=auth(module), json={})
    assert response.status_code == 200, response.text
    asset = response.json()
    assert asset['role'] == 'context' and asset['semantic_role'] == 'pose' and asset['enabled'] is True
    assert asset['video_run_ending'] == manager.ident and asset['media_type'] == 'image'
    assert (asset['width'], asset['height']) == (64, 32)
    assert (module.DATA/'assets'/asset['id']/'source.png').is_file()
    assert (module.DATA/'assets'/asset['id']/'thumbnail.jpg').is_file()
    assert json.loads((folder/'ending-asset.json').read_text()) == asset
    assert client.post(endpoint, headers=auth(module), json={}).json() == asset
    assert len(processes) == 1
    assert not list((module.DATA/'projects').glob('*.json'))
    assert not lm.loads and not lm.unloads


def test_ending_decode_failure_does_not_create_a_false_reference(server, manager, monkeypatch):
    module, client, _ = server
    folder = module.DATA/'video_runs'/manager.ident; folder.mkdir(parents=True)
    playback = folder/'playback.mp4'; playback.write_bytes(b'neutral fixture')
    monkeypatch.setattr(module, 'cached_run_video', lambda ident: playback)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    response = client.post('/api/video/runs/'+manager.ident+'/ending-image', headers=auth(module), json={})
    assert response.status_code == 400 and 'ending image could not be read' in response.json()['detail']
    assert not list((module.DATA/'assets').iterdir())
    assert not (folder/'ending-asset.json').exists()


def test_real_ffmpeg_ending_frame_uses_last_blue_second(server, manager, monkeypatch):
    """Actual software encoding/decoding of a neutral temporary five-second clip."""
    module, client, _ = server
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('The optional CPU FFmpeg integration test requires ffmpeg on PATH.')
    folder = module.DATA/'video_runs'/manager.ident
    folder.mkdir(parents=True)
    playback = folder/'playback.mp4'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    REAL_CPU_RUN([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=24:d=4',
        '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24:d=1',
        '-filter_complex_threads', '1', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p',
        '-an', '-c:v', 'libx264', '-threads', '1', str(playback)],
        check=True, capture_output=True, timeout=20, creationflags=flags)
    first = folder/'first.png'
    REAL_CPU_RUN([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(playback),
        '-an', '-frames:v', '1', str(first)], check=True, capture_output=True, timeout=20, creationflags=flags)
    helper_calls = []
    def cpu_decode(command, **kwargs):
        # Only the existing helper's local, software FFmpeg operation is allowed.
        assert command[0] == 'ffmpeg'
        assert '-hwaccel' not in command and all('nvenc' not in str(item) for item in command)
        assert str(playback) in command and Path(command[-1]).parent == folder
        helper_calls.append(command)
        return REAL_CPU_RUN(command, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', cpu_decode)
    response = client.post('/api/video/runs/'+manager.ident+'/ending-image', headers=auth(module), json={})
    assert response.status_code == 200, response.text
    asset = response.json()
    with Image.open(first) as image:
        first_rgb = image.convert('RGB').getpixel((32, 32))
    with Image.open(module.DATA/'assets'/asset['id']/'source.png') as image:
        last_rgb = image.convert('RGB').getpixel((32, 32))
    assert first_rgb[0] > 240 and first_rgb[2] < 15, first_rgb
    assert last_rgb[2] > 240 and last_rgb[0] < 15, last_rgb
    assert len(helper_calls) == 1
    assert 'reverse,scale=min(1024\\,iw):-2' in helper_calls[0]
    assert asset['role'] == 'context' and asset['video_run_ending'] == manager.ident
    assert manager.calls == [('media', manager.ident)]
