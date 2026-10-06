"""Review persistence, evidence boundaries and authenticated API; no model calls."""
import copy
import importlib
import io
import json
import shutil
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import video_review as vr
from backend.projects import atomic_json, new_project


def ident():
    return str(uuid.uuid4())


def ai_result():
    return {'verdict': 'needs_changes', 'summary': 'Object holder changes between samples.',
            'issues': [{'timestamp': 0, 'severity': 'major', 'category': 'continuity',
                        'description': 'A cup is in a different hand.', 'prompt_fix': 'Keep the cup in her left hand.'}],
            'improved_prompt': 'A woman holds the same cup in her left hand throughout the continuous shot.'}


def test_manual_review_persists_and_ai_keeps_human_verdict(tmp_path):
    store = vr.VideoReviewStore(tmp_path)
    key = ident()
    assert store.get('run', key, 'Original prompt')['verdict'] == 'unreviewed'
    saved = store.save('run', key, {'verdict': 'rejected', 'notes': 'Watch the doorway.',
                                  'checklist': {'motion': 'fail'}}, 'Original prompt')
    result = store.record_ai('run', key, {**ai_result(), 'samples': []}, 'AI submitted prompt', saved['updated'])
    assert result['verdict'] == 'rejected' and result['checklist']['motion'] == 'fail'
    assert result['notes'] == 'Watch the doorway.' and result['source_prompt'] == 'AI submitted prompt'
    assert vr.VideoReviewStore(tmp_path).get('run', key) == result


def test_ai_preserves_concurrent_human_edits(tmp_path):
    store, key = vr.VideoReviewStore(tmp_path), ident()
    snapshot = store.save('asset', key, {'source_prompt': 'Old prompt'})
    store.save('asset', key, {'source_prompt': 'New user edit', 'notes': 'New note', 'verdict': 'approved'})
    result = store.record_ai('asset', key, {**ai_result(), 'samples': []}, 'AI input', snapshot['updated'])
    assert result['source_prompt'] == 'New user edit' and result['notes'] == 'New note'
    assert result['verdict'] == 'approved'


@pytest.mark.parametrize('body', [
    {'verdict': 'yes'}, {'notes': 1}, {'source_prompt': 'x' * 16001},
    {'checklist': {'unknown': 'pass'}}, {'checklist': {'audio': 'yes'}}, {'ai': ai_result()},
])
def test_review_rejects_invalid_manual_fields(tmp_path, body):
    with pytest.raises(ValueError):
        vr.VideoReviewStore(tmp_path).save('asset', ident(), body)


def test_evidence_frames_only_serve_saved_analysis_and_superseded_files_are_removed(tmp_path):
    store, key = vr.VideoReviewStore(tmp_path), ident()
    folder = store.folder('run', key) / 'frames'
    folder.mkdir(parents=True)
    old_name, new_name = uuid.uuid4().hex + '-0.jpg', uuid.uuid4().hex + '-0.jpg'
    for name in (old_name, new_name):
        (folder / name).write_bytes(b'image')
    old_url = f'/api/reviews/run/{key}/frames/{old_name}'
    store.record_ai('run', key, {'samples': [{'timestamp': 0, 'url': old_url}]}, '')
    assert store.frame_path('run', key, old_name) == folder / old_name
    with pytest.raises(FileNotFoundError):
        store.frame_path('run', key, new_name)
    with pytest.raises(ValueError):
        store.frame_path('run', key, '../review.json')
    new_url = f'/api/reviews/run/{key}/frames/{new_name}'
    store.record_ai('run', key, {'samples': [{'timestamp': 0, 'url': new_url}]}, '')
    assert not (folder / old_name).exists() and (folder / new_name).exists()


def test_sampling_is_bounded_cpu_only_and_cleans_failed_extraction(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'original-video')
    calls = []
    metadata = {'duration': 2, 'width': 640, 'height': 480, 'frame_rate': 24, 'has_audio': True}
    monkeypatch.setattr(vr, 'probe_video', lambda path: metadata)
    def run(args, timeout):
        calls.append((args, timeout))
        Image.new('RGB', (32, 24)).save(args[-1])
    monkeypatch.setattr(vr, '_run_media', run)
    media, samples = vr.sample_video(source, tmp_path / 'review', 'asset', ident(), 4)
    assert media == metadata and len(samples) == 4
    assert samples[0]['timestamp'] == 0 and samples[-1]['timestamp'] < 2
    assert all('none' in args and 'file,pipe' in args and timeout == 20 for args, timeout in calls)
    assert source.read_bytes() == b'original-video'
    for count in (True, 3, 9, '6'):
        with pytest.raises(ValueError):
            vr.sample_video(source, tmp_path, 'run', ident(), count)
    failed = tmp_path / 'failed'
    def fail(args, timeout):
        Image.new('RGB', (32, 24)).save(args[-1])
        raise ValueError('decode failed')
    monkeypatch.setattr(vr, '_run_media', fail)
    with pytest.raises(ValueError, match='decode failed'):
        vr.sample_video(source, failed, 'run', ident(), 4)
    assert list((failed / 'frames').iterdir()) == []


def test_probe_rejects_invalid_metadata_and_reports_audio_presence(tmp_path, monkeypatch):
    path = tmp_path / 'video.mp4'
    path.write_bytes(b'video')
    metadata = {'streams': [{'codec_type': 'video', 'width': 1280, 'height': 720,
                             'duration': '4.5', 'avg_frame_rate': '24/1'}, {'codec_type': 'audio'}]}
    monkeypatch.setattr(vr, '_run_media', lambda *args: SimpleNamespace(stdout=json.dumps(metadata).encode()))
    assert vr.probe_video(path) == {'duration': 4.5, 'width': 1280, 'height': 720, 'frame_rate': 24,
                                    'has_audio': True, 'stream_index': 0, 'video_start': 0}
    for value in ('NaN', '0', '3601'):
        metadata['streams'][0]['duration'] = value
        with pytest.raises(ValueError):
            vr.probe_video(path)


def test_analysis_requires_sample_evidence_and_states_limits():
    data = io.BytesIO()
    Image.new('RGB', (32, 32)).save(data, 'JPEG')
    import base64
    samples = [{'timestamp': 0, 'url': '/frame.jpg',
                'data_url': 'data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode()}]
    response = ai_result()
    calls = []
    def complete(model, system, content, schema, **options):
        calls.append((model, system, content, options))
        return copy.deepcopy(response)
    lm = SimpleNamespace(complete_json=complete, last_completion_info={'locally_validated': True})
    result = vr.analyze_frames(lm, 'exact-qwen-instance', {'duration': 2}, samples, 'Original dialogue', 'Keep the cup')
    assert result['model'] == 'exact-qwen-instance' and 'data_url' not in result['samples'][0]
    assert any('Audio' in limit for limit in result['limitations'])
    assert calls[0][0] == 'exact-qwen-instance' and len(calls[0][2]) == 3
    response['issues'][0]['timestamp'] = 1
    with pytest.raises(ValueError, match='unsampled timestamp'):
        vr.analyze_frames(lm, 'model', {'duration': 2}, samples)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv('H3_STUDIO_DATA', str(tmp_path))
    sys.modules.pop('backend.app', None)
    module = importlib.import_module('backend.app')
    key, run_key = ident(), ident()
    folder = module.DATA / 'assets' / key
    folder.mkdir(parents=True)
    (folder / 'source.mp4').write_bytes(b'video')
    atomic_json(folder / 'metadata.json', {'id': key, 'media_type': 'video', 'filename': 'source.mp4',
                'name': 'Imported take', 'duration': 2, 'width': 640, 'height': 360, 'mime': 'video/mp4'})
    project = new_project()
    class Runs:
        def get(self, key):
            return {'id': key, 'status': 'succeeded', 'title': 'Completed take'}
        def snapshot(self, key):
            return project
        def _load(self, key, filename):
            assert filename == 'request.json'
            return {'prompt': 'Saved generation prompt'}
    monkeypatch.setattr(module, 'VIDEO_RUNS', Runs())
    monkeypatch.setattr(module, 'scene_video_path', lambda key: folder / 'source.mp4')
    def denied(*args, **kwargs):
        raise AssertionError('Unexpected model operation')
    monkeypatch.setattr(module.RESOURCES, 'run_ai', denied)
    with TestClient(module.app, base_url='http://127.0.0.1:8766', raise_server_exceptions=False) as client:
        yield module, client, key, run_key


def auth(module):
    return {'X-H3-Token': module.TOKEN}


def test_library_manual_api_auth_and_asset_boundary(server):
    module, client, key, run_key = server
    library = client.get('/api/reviews/library').json()
    assert library['videos'][0]['id'] == key and library['criteria'] == list(vr.CRITERIA)
    base = '/api/reviews/asset/' + key
    assert client.put(base, json={'verdict': 'approved'}).status_code == 403
    assert client.put(base, json={'verdict': 'approved'}, headers=auth(module)).json()['verdict'] == 'approved'
    assert client.get(base).json()['video_url'] == f'/api/assets/{key}/file'
    assert client.get('/api/reviews/run/' + run_key).json()['source_prompt'] == 'Saved generation prompt'
    assert client.get('/api/reviews/asset/' + ident()).status_code == 404
    assert client.get(base + '/prompt').status_code == 404
    assert client.get(base + '/frames/' + uuid.uuid4().hex + '-0.jpg').status_code == 404
    assert client.put(base, json={'path': 'C:/arbitrary/video.mp4'}, headers=auth(module)).status_code == 400


def test_ai_api_resource_lease_saved_evidence_and_failure_preservation(server, monkeypatch):
    module, client, key, run_key = server
    calls = []
    def sample(path, folder, kind, key, count):
        calls.append(('sample', path, kind, count))
        filename = uuid.uuid4().hex + '-0.jpg'
        frames = folder / 'frames'
        frames.mkdir(parents=True, exist_ok=True)
        (frames / filename).write_bytes(b'jpeg')
        return {'duration': 2}, [{'timestamp': 0, 'url': f'/api/reviews/{kind}/{key}/frames/{filename}', 'data_url': 'fake'}]
    def analyze(lm, model, media, samples, prompt, intent, notes):
        calls.append(('analyze', model, prompt, intent))
        return {**ai_result(), 'samples': [{k: sample[k] for k in ('timestamp', 'url')} for sample in samples],
                'limitations': list(vr.LIMITATIONS), 'model': model}
    def lease(model, operation, *, profile):
        calls.append(('lease', model, profile))
        return operation('verified-instance')
    monkeypatch.setattr(vr, 'sample_video', sample)
    monkeypatch.setattr(vr, 'analyze_frames', analyze)
    monkeypatch.setattr(module.RESOURCES, 'run_ai', lease)
    base = '/api/reviews/run/' + run_key
    assert client.post(base + '/analyze', json={}).status_code == 403
    response = client.post(base + '/analyze', json={'sample_count': 4, 'intent': 'Keep the cup'}, headers=auth(module))
    assert response.status_code == 200, response.text
    review = response.json()
    assert review['verdict'] == 'unreviewed' and review['ai']['model'] == 'verified-instance'
    assert calls[0][2] == 'run' and calls[2][2] == 'Saved generation prompt'
    frame_url = review['ai']['samples'][0]['url']
    assert client.get(frame_url).status_code == 200
    assert client.get(base + '/prompt').text == ai_result()['improved_prompt']
    def fail(*args, **kwargs):
        raise ValueError('Model unavailable')
    monkeypatch.setattr(module.RESOURCES, 'run_ai', fail)
    assert client.post(base + '/analyze', json={}, headers=auth(module)).status_code == 400
    assert client.get(base).json()['ai'] == review['ai'] and client.get(frame_url).status_code == 200
    for body in ({'sample_count': 30}, {'path': 'C:/video.mp4'}, {'intent': 'x' * 2001}):
        assert client.post(base + '/analyze', json=body, headers=auth(module)).status_code == 400


def test_duplicate_analysis_does_not_start_second_job(server):
    module, client, key, _ = server
    lock = module.review_store().analysis_lock('asset', key)
    assert lock.acquire(blocking=False)
    try:
        response = client.post(f'/api/reviews/asset/{key}/analyze', json={}, headers=auth(module))
        assert response.status_code == 409
    finally:
        lock.release()


def test_review_import_streamed_long_video_and_cleanup(server, monkeypatch):
    module, client, _, _ = server
    data = b'local-video-bytes'
    def probe(path):
        assert path.read_bytes() == data
        return {'duration': 1800, 'width': 1280, 'height': 720}
    monkeypatch.setattr(vr, 'probe_video', probe)
    assert client.post('/api/reviews/import', files={'file': ('Film.mp4', data)}).status_code == 403
    response = client.post('/api/reviews/import', files={'file': ('Film.mp4', data)}, headers=auth(module))
    assert response.status_code == 200, response.text
    asset = response.json()
    assert asset['duration'] == 1800 and asset['review_only'] is True and asset['enabled'] is False
    assert any(item['id'] == asset['id'] for item in client.get('/api/reviews/library').json()['videos'])
    before = set((module.DATA / 'assets').iterdir())
    def fail(path):
        raise ValueError('Unreadable video')
    monkeypatch.setattr(vr, 'probe_video', fail)
    assert client.post('/api/reviews/import', files={'file': ('Bad.mp4', data)}, headers=auth(module)).status_code == 400
    assert set((module.DATA / 'assets').iterdir()) == before
    assert client.post('/api/reviews/import', files={'file': ('Bad.txt', data)}, headers=auth(module)).status_code == 400
    assert set((module.DATA / 'assets').iterdir()) == before


def test_review_import_rejects_oversize_without_retaining_files(server, monkeypatch):
    module, client, _, _ = server
    before = set((module.DATA / 'assets').iterdir())
    from starlette.datastructures import UploadFile
    class OversizedChunk(bytes):
        def __len__(self):
            return 129 * 1024 * 1024
    async def oversized(self, size=-1):
        return OversizedChunk(b'x')
    monkeypatch.setattr(UploadFile, 'read', oversized)
    response = client.post('/api/reviews/import', files={'file': ('Too-large.mp4', b'x')}, headers=auth(module))
    assert response.status_code == 400 and '128 MB' in response.json()['detail']
    assert set((module.DATA / 'assets').iterdir()) == before


def test_existing_asset_upload_cleans_only_rejected_new_folder(server, monkeypatch):
    module, client, existing, _ = server
    before = set((module.DATA / 'assets').iterdir())
    original = (module.DATA / 'assets' / existing / 'source.mp4').read_bytes()
    for name, content, mime in [('Bad.png', b'not-an-image', 'image/png'),
                                 ('Unknown.txt', b'unknown', 'text/plain')]:
        response = client.post('/api/assets', files={'file': (name, content, mime)}, headers=auth(module))
        assert response.status_code == 400
        assert set((module.DATA / 'assets').iterdir()) == before
    def fail_probe(path):
        raise ValueError('Unreadable video')
    monkeypatch.setattr(module, 'media_probe', fail_probe)
    assert client.post('/api/assets', files={'file': ('Bad.mp4', b'video', 'video/mp4')},
                       headers=auth(module)).status_code == 400
    assert set((module.DATA / 'assets').iterdir()) == before
    assert (module.DATA / 'assets' / existing / 'source.mp4').read_bytes() == original
    image = io.BytesIO()
    Image.new('RGB', (32, 32)).save(image, 'PNG')
    uploaded = client.post('/api/assets', files={'file': ('Good.png', image.getvalue(), 'image/png')}, headers=auth(module))
    assert uploaded.status_code == 200 and uploaded.json()['filename'] == 'source.png'
    assert (module.DATA / 'assets' / uploaded.json()['id'] / 'metadata.json').is_file()


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='CPU FFmpeg runtime unavailable')
def test_webm_samples_video_ending_with_longer_audio_and_packet_fallback(tmp_path, monkeypatch):
    path = tmp_path / 'audio-longer.webm'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'color=c=blue:s=64x64:r=2:d=2', '-f', 'lavfi',
                    '-i', 'sine=frequency=440:duration=5', '-c:v', 'libvpx-vp9',
                    '-deadline', 'realtime', '-cpu-used', '8', '-c:a', 'libopus', str(path)],
                   capture_output=True, check=True, timeout=20)
    original = path.read_bytes()
    media, samples = vr.sample_video(path, tmp_path / 'review', 'asset', ident(), 4)
    assert 1.9 < media['duration'] < 2.2 and media['has_audio'] is True
    assert len(samples) == 4 and max(sample['timestamp'] for sample in samples) < 2
    assert path.read_bytes() == original
    run = vr._run_media
    calls = []
    def without_track_duration(args, timeout):
        result = run(args, timeout)
        calls.append(args)
        if '-show_packets' not in args:
            probe = json.loads(result.stdout)
            for stream in probe['streams']:
                stream.pop('duration', None)
                stream.pop('tags', None)
            return SimpleNamespace(stdout=json.dumps(probe).encode())
        return result
    monkeypatch.setattr(vr, '_run_media', without_track_duration)
    fallback = vr.probe_video(path)
    assert 1.9 < fallback['duration'] < 2.2
    assert any('-show_packets' in args and '-read_intervals' in args for args in calls)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='CPU FFmpeg runtime unavailable')
def test_samples_map_same_default_real_video_stream_as_probe(tmp_path):
    path = tmp_path / 'two-tracks.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'color=c=blue:s=64x64:r=24:d=2', '-f', 'lavfi',
                    '-i', 'color=c=red:s=128x128:r=24:d=3', '-map', '0:v', '-map', '1:v',
                    '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p',
                    '-disposition:v:0', '0', '-disposition:v:1', 'default', str(path)],
                   capture_output=True, check=True, timeout=20)
    folder = tmp_path / 'review'
    media, samples = vr.sample_video(path, folder, 'asset', ident(), 4)
    assert media['stream_index'] == 1 and media['width'] == 128 and media['duration'] == 3
    assert samples[-1]['timestamp'] > 2
    for sample in samples:
        image = Image.open(folder / 'frames' / sample['url'].rsplit('/', 1)[-1]).convert('RGB')
        red, green, blue = image.getpixel((image.width // 2, image.height // 2))
        assert red > 200 and green < 40 and blue < 40


def test_old_frame_cleanup_failure_keeps_new_saved_evidence(tmp_path, monkeypatch):
    store, key = vr.VideoReviewStore(tmp_path), ident()
    folder = store.folder('run', key) / 'frames'
    folder.mkdir(parents=True)
    old_name, new_name = uuid.uuid4().hex + '-0.jpg', uuid.uuid4().hex + '-0.jpg'
    for name in (old_name, new_name):
        (folder / name).write_bytes(b'jpeg')
    old_url, new_url = [f'/api/reviews/run/{key}/frames/{name}' for name in (old_name, new_name)]
    store.record_ai('run', key, {'samples': [{'timestamp': 0, 'url': old_url}]}, '')
    from pathlib import Path
    unlink = Path.unlink
    def windows_reader(path, *args, **kwargs):
        if path.name == old_name:
            raise PermissionError('Simulated Windows open reader')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', windows_reader)
    result = store.record_ai('run', key, {'samples': [{'timestamp': 0, 'url': new_url}]}, '')
    assert result['ai']['samples'][0]['url'] == new_url
    assert store.frame_path('run', key, new_name).read_bytes() == b'jpeg'


def test_frame_route_captures_bytes_before_reanalysis_cleanup(server, monkeypatch):
    module, _, key, _ = server
    store = module.review_store()
    folder = store.folder('asset', key) / 'frames'
    folder.mkdir(parents=True)
    name = uuid.uuid4().hex + '-0.jpg'
    path = folder / name
    path.write_bytes(b'frame-bytes')
    url = f'/api/reviews/asset/{key}/frames/{name}'
    store.record_ai('asset', key, {'samples': [{'timestamp': 0, 'url': url}]}, '')
    response = module.review_frame('asset', key, name)
    store.record_ai('asset', key, {'samples': []}, '')
    assert not path.exists() and response.body == b'frame-bytes'
