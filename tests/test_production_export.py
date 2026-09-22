"""Export safety and real CPU FFmpeg delivery; no GPU or model inference."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile

import pytest

from backend import production_export as exports


def batch(*durations):
    return {'id': str(uuid.uuid4()), 'name': 'A small production', 'items': [
        {'run_id': str(uuid.uuid4()), 'status': 'succeeded', 'duration': d,
         'title': f'Clip {index}'} for index, d in enumerate(durations, 1)]}


def reject_lookup(_):
    pytest.fail('Invalid export input must not resolve or transcode media.')


@pytest.mark.parametrize('change', [
    lambda b: b.update(items=[]),
    lambda b: b['items'][0].update(status='running'),
    lambda b: b['items'][0].pop('run_id'),
    lambda b: b.update(items=b['items'] * 101),
    lambda b: b.update(id='../outside'),
    lambda b: b['items'][0].update(run_id='../outside'),
])
def test_invalid_batches_never_resolve_media(tmp_path, change):
    value = batch(4)
    change(value)
    with pytest.raises(ValueError):
        exports.export_production(tmp_path, value, reject_lookup)


def test_unknown_export_kind_never_resolves_media(tmp_path):
    with pytest.raises(ValueError, match='film or individual'):
        exports.export_production(tmp_path, batch(4), reject_lookup, '../arbitrary')


@pytest.mark.parametrize('duration', [None, True, 3.99, 15.01, '4', float('nan'), float('inf')])
def test_invalid_duration_never_transcodes(tmp_path, monkeypatch, duration):
    monkeypatch.setattr(exports, '_run', lambda _: pytest.fail('No invalid-duration transcode'))
    with pytest.raises(ValueError, match='invalid delivery duration'):
        exports.export_production(tmp_path, batch(duration), lambda _: tmp_path / 'source.mp4')


@pytest.mark.parametrize('batch_id,export_id,filename', [
    ('../outside', 'a' * 20, 'film.mp4'),
    (str(uuid.uuid4()), '../outside', 'film.mp4'),
    (str(uuid.uuid4()), 'a' * 19, 'film.mp4'),
    (str(uuid.uuid4()), 'A' * 20, 'film.mp4'),
    (str(uuid.uuid4()), 'a' * 20, '../film.mp4'),
    (str(uuid.uuid4()), 'a' * 20, 'assembly.ffconcat'),
])
def test_download_path_rejects_traversal_and_nonpublic_files(tmp_path, batch_id, export_id, filename):
    with pytest.raises(ValueError):
        exports.export_file(tmp_path, batch_id, export_id, filename)


def test_download_requires_existing_export(tmp_path):
    with pytest.raises(ValueError, match='Generate this production export first'):
        exports.export_file(tmp_path, str(uuid.uuid4()), 'a' * 20, 'film.mp4')


@pytest.fixture(scope='module')
def synthetic_media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real CPU FFmpeg export validation requires ffmpeg and ffprobe.')
    root = tmp_path_factory.mktemp('production-export-media')
    results = []
    for index, (color, frequency) in enumerate([('red', 440), ('blue', 660)]):
        target = root / f'input-{index}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=96x160:r=24:d=4.25', '-f', 'lavfi', '-i',
                        f'sine=frequency={frequency}:sample_rate=48000:duration=4.25',
                        '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-shortest', str(target)], check=True, capture_output=True)
        results.append(target)
    return results


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], check=True, capture_output=True)
    return json.loads(result.stdout)


def test_real_two_clip_film_has_exact_eight_seconds_and_preserves_sources(tmp_path, synthetic_media):
    value = batch(4, 4)
    sources = dict(zip((x['run_id'] for x in value['items']), synthetic_media))
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in synthetic_media]
    result = exports.export_production(tmp_path, value, sources.__getitem__)
    output = exports.export_file(tmp_path, value['id'], result['export_id'], result['filename'])
    info = probe(output)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert float(video['duration']) == pytest.approx(8, abs=1 / 240)
    assert int(video['nb_frames']) == 192
    assert float(info['format']['duration']) == pytest.approx(8, abs=1 / 24)
    assert (video['width'], video['height']) == (96, 160)
    assert any(s['codec_type'] == 'audio' for s in info['streams'])
    assert result['clip_count'] == 2 and result['review_status'] == 'Generated; creative review required'
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in synthetic_media]
    manifest = json.loads(output.with_name('manifest.json').read_text())
    assert [x['duration'] for x in manifest['clips']] == [4, 4]
    assert [x['title'] for x in manifest['clips']] == ['Clip 1', 'Clip 2']


def test_zip_titles_are_portable_unique_and_manifest_preserves_originals(tmp_path, synthetic_media):
    value = batch(4, 4)
    titles = ['../CON:<bad>| / movie?* 🎬', '日本語']
    for item, title in zip(value['items'], titles):
        item['title'] = title
    sources = dict(zip((x['run_id'] for x in value['items']), synthetic_media))
    result = exports.export_production(tmp_path, value, sources.__getitem__, 'clips')
    output = exports.export_file(tmp_path, value['id'], result['export_id'], 'clips.zip')
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ['manifest.json', '001-CONbad  movie.mp4', '002-clip.mp4']
        assert all('/' not in name and '\\' not in name for name in archive.namelist())
        manifest = json.loads(archive.read('manifest.json'))
        assert [x['title'] for x in manifest['clips']] == titles
        assert all(x['file'] in archive.namelist() for x in manifest['clips'])
        assert all(archive.getinfo(name).file_size > 0 for name in archive.namelist())


def test_cache_tracks_top_level_duration_titles_and_name(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, '_run', lambda args: Path(args[-1]).write_bytes(b'test media'))
    value = batch(4)
    original = exports.export_production(tmp_path, value, lambda _: tmp_path / 'source.mp4', 'clips')
    identical = exports.export_production(tmp_path, copy.deepcopy(value), reject_lookup, 'clips')
    assert identical['export_id'] == original['export_id']
    ids = {original['export_id']}
    for change in [lambda b: b['items'][0].update(duration=5),
                   lambda b: b['items'][0].update(title='Changed title'),
                   lambda b: b.update(name='Changed name')]:
        changed = copy.deepcopy(value)
        change(changed)
        result = exports.export_production(tmp_path, changed, lambda _: tmp_path / 'source.mp4', 'clips')
        assert result['export_id'] not in ids
        ids.add(result['export_id'])
        manifest_path = exports.export_file(tmp_path, value['id'], result['export_id'], 'manifest.json')
        manifest = json.loads(manifest_path.read_text())
        assert manifest['name'] == changed['name']
        assert manifest['clips'][0]['duration'] == changed['items'][0]['duration']
        assert manifest['clips'][0]['title'] == changed['items'][0]['title']


def test_inline_project_metadata_keeps_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, '_run', lambda args: Path(args[-1]).write_bytes(b'test media'))
    value = batch(4)
    value['items'][0]['project'] = {'duration': 5, 'title': 'Project title'}
    result = exports.export_production(tmp_path, value, lambda _: tmp_path / 'source.mp4', 'clips')
    manifest = json.loads(exports.export_file(tmp_path, value['id'], result['export_id'], 'manifest.json').read_text())
    assert manifest['clips'][0]['duration'] == 5
    assert manifest['clips'][0]['title'] == 'Project title'


def test_failed_transcode_does_not_publish_export_or_modify_source(tmp_path, monkeypatch):
    source = tmp_path / 'original.mp4'
    source.write_bytes(b'original remains intact')
    def fail(args):
        Path(args[-1]).write_bytes(b'incomplete')
        raise ValueError('Export failed')
    monkeypatch.setattr(exports, '_run', fail)
    with pytest.raises(ValueError, match='Export failed'):
        exports.export_production(tmp_path, batch(4), lambda _: source, 'clips')
    assert source.read_bytes() == b'original remains intact'
    assert not list(tmp_path.rglob('clips.zip'))
    assert not list(tmp_path.rglob('001.mp4'))


def test_mixed_resolutions_require_individual_export(tmp_path, synthetic_media):
    differently_sized = tmp_path / 'wide.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(synthetic_media[1]),
                    '-vf', 'scale=160:96', '-c:v', 'libx264', '-threads', '2', '-c:a', 'copy',
                    str(differently_sized)], check=True, capture_output=True)
    value = batch(4, 4)
    sources = dict(zip((x['run_id'] for x in value['items']), [synthetic_media[0], differently_sized]))
    with pytest.raises(ValueError, match='mixed video or audio formats'):
        exports.export_production(tmp_path, value, sources.__getitem__)
    assert not list(tmp_path.rglob('film.mp4'))
    clips = exports.export_production(tmp_path, value, sources.__getitem__, 'clips')
    assert exports.export_file(tmp_path, value['id'], clips['export_id'], 'clips.zip').is_file()


def edit(index=0, cut=0, **crop):
    return {'index': index, 'cut_at': cut, 'crop': {'x': 0, 'y': 0, 'width': 32, 'height': 80, **crop}}


@pytest.mark.parametrize('edits', [
    {}, [edit()] * 101, [edit(), edit()], [edit(index=True)], [edit(index=1)],
    [edit(cut=float('nan'))], [edit(cut=float('inf'))], [edit(cut=-1)], [edit(cut=4)],
    [edit(cut=3.999)], [edit(width=0)], [edit(width=31)], [edit(x=-2)], [edit(x=True)],
    [{'index': 0, 'crop': {}}], [{**edit(), 'arbitrary': 'filter'}], [edit(width=32.0)],
])
def test_invalid_edits_reject_before_media_lookup(tmp_path, edits):
    with pytest.raises(ValueError):
        exports.export_production(tmp_path, batch(4), reject_lookup, edits=edits)


def test_all_crop_bounds_checked_before_any_transcode(tmp_path, synthetic_media, monkeypatch):
    value = batch(4, 4)
    sources = dict(zip((x['run_id'] for x in value['items']), synthetic_media))
    monkeypatch.setattr(exports, '_run', lambda _: pytest.fail('No transcode before all bounds pass'))
    with pytest.raises(ValueError, match='exceeds its 96×160'):
        exports.export_production(tmp_path, value, sources.__getitem__, 'clips',
                                  [edit(), edit(index=1, x=90)])


def test_edits_change_cache_and_manifest_and_are_frame_aligned(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, '_run', lambda args: Path(args[-1]).write_bytes(b'media'))
    monkeypatch.setattr(exports, '_source_dimensions', lambda _: (96, 160))
    value = batch(4)
    lookup = lambda _: tmp_path / 'source.mp4'
    untouched = exports.export_production(tmp_path, value, lookup, 'clips')
    cropped = exports.export_production(tmp_path, value, lookup, 'clips', [edit(cut=1.51)])
    cached = exports.export_production(tmp_path, value, reject_lookup, 'clips', [edit(cut=1.5)])
    assert untouched['export_id'] != cropped['export_id'] == cached['export_id']
    assert cropped['edits'][0]['cut_at'] == 1.5
    manifest = json.loads(exports.export_file(tmp_path, value['id'], cropped['export_id'], 'manifest.json').read_text())
    assert manifest['edits'] == cropped['edits']
    with zipfile.ZipFile(exports.export_file(tmp_path, value['id'], cropped['export_id'], 'clips.zip')) as archive:
        assert json.loads(archive.read('manifest.json'))['edits'] == cropped['edits']


def test_real_reaction_cut_keeps_timing_audio_source_and_output_dimensions(tmp_path, synthetic_media):
    source = tmp_path / 'spatial-source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(synthetic_media[1]),
                    '-vf', 'drawbox=x=0:y=0:w=32:h=160:color=red:t=fill', '-c:v', 'libx264',
                    '-threads', '2', '-c:a', 'copy', str(source)], check=True, capture_output=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    value = batch(4)
    original = exports.export_production(tmp_path, value, lambda _: source)
    result = exports.export_production(tmp_path, value, lambda _: source, edits=[edit(cut=1.5)])
    output = exports.export_file(tmp_path, value['id'], result['export_id'], result['filename'])
    original_output = exports.export_file(tmp_path, value['id'], original['export_id'], original['filename'])
    info = probe(output)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height'], int(video['nb_frames'])) == (96, 160, 96)
    assert float(video['duration']) == pytest.approx(4, abs=1 / 240)
    def pixel(t):
        return subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', str(output),
            '-frames:v', '1', '-vf', 'crop=2:2:80:80,scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
            check=True, capture_output=True).stdout
    before, after = pixel(1.4), pixel(1.6)
    assert before[2] > 200 and before[0] < 30  # Original right side remains blue.
    assert after[0] > 200 and after[2] < 30  # Reaction crop fills original output size.
    def audio(path):
        return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
            '-f', 's16le', '-acodec', 'pcm_s16le', '-'], check=True, capture_output=True).stdout
    assert audio(output) == audio(original_output)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    full_crop = exports.export_production(tmp_path, value, lambda _: source, edits=[edit(cut=0)])
    full_output = exports.export_file(tmp_path, value['id'], full_crop['export_id'], full_crop['filename'])
    full_video = next(s for s in probe(full_output)['streams'] if s['codec_type'] == 'video')
    assert (full_video['width'], full_video['height'], int(full_video['nb_frames'])) == (96, 160, 96)


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}, float('nan'), float('inf')])
def test_audio_normalization_requires_boolean_before_resolving_media(tmp_path, value):
    with pytest.raises(ValueError, match='normalize_audio must be true or false'):
        exports.export_production(tmp_path, batch(4), reject_lookup, normalize_audio=value)


def test_audio_normalization_changes_cache_and_manifest_only_when_enabled(tmp_path, monkeypatch):
    commands = []
    def encode(args):
        commands.append(args)
        Path(args[-1]).write_bytes(b'media')
    monkeypatch.setattr(exports, '_run', encode)
    measured_filter = 'loudnorm=I=-16:TP=-2.5:LRA=11:measured_I=-40:measured_TP=-25:measured_LRA=2:measured_thresh=-50:offset=0:linear=true'
    monkeypatch.setattr(exports, '_normalization_plan', lambda *args: {'filter': measured_filter})
    monkeypatch.setattr(exports, '_finish_normalization', lambda *args: {'version': exports.AUDIO_NORMALIZATION_VERSION})
    value = batch(4)
    default = exports.export_production(tmp_path, value, lambda _: tmp_path / 'source.mp4', 'clips')
    assert '-af' not in commands[0]
    explicit_false = exports.export_production(tmp_path, value, reject_lookup, 'clips', normalize_audio=False)
    assert explicit_false['export_id'] == default['export_id']
    normalized = exports.export_production(tmp_path, value, lambda _: tmp_path / 'source.mp4', 'clips', normalize_audio=True)
    assert normalized['export_id'] != default['export_id'] and normalized['normalize_audio'] is True
    assert commands[1][commands[1].index('-af') + 1] == measured_filter
    assert exports.export_production(tmp_path, value, reject_lookup, 'clips', normalize_audio=True)['export_id'] == normalized['export_id']
    manifest = json.loads(exports.export_file(tmp_path, value['id'], normalized['export_id'], 'manifest.json').read_text())
    assert manifest['normalize_audio'] is True
    assert manifest['audio_normalization_version'] == normalized['audio_normalization_version'] == exports.AUDIO_NORMALIZATION_VERSION
    with zipfile.ZipFile(exports.export_file(tmp_path, value['id'], normalized['export_id'], 'clips.zip')) as archive:
        assert json.loads(archive.read('manifest.json'))['normalize_audio'] is True
    monkeypatch.setattr(exports, 'AUDIO_NORMALIZATION_VERSION', 'future-measured-version')
    revised = exports.export_production(tmp_path, value, lambda _: tmp_path / 'source.mp4', 'clips', normalize_audio=True)
    assert revised['export_id'] != normalized['export_id']


def loudness(path):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path), '-map', '0:a:0',
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
        capture_output=True, check=True)
    text = result.stderr.decode('utf-8', errors='replace')
    return json.JSONDecoder().raw_decode(text[text.rfind('{'):])[0]


def test_real_quiet_audio_normalized_near_target_without_changing_source(tmp_path, synthetic_media):
    source = tmp_path / 'quiet-original.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(synthetic_media[0]),
                    '-af', 'volume=0.03', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', str(source)],
                    check=True, capture_output=True)
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    before = loudness(source)
    assert float(before['input_i']) < -40
    value = batch(4)
    result = exports.export_production(tmp_path, value, lambda _: source, normalize_audio=True)
    output = exports.export_file(tmp_path, value['id'], result['export_id'], result['filename'])
    after = loudness(output)
    assert float(after['input_i']) == pytest.approx(-16, abs=1)
    assert float(after['input_tp']) <= -1.1  # Allow at most 0.4 dB AAC encoding overshoot.
    assert float(after['input_i']) - float(before['input_i']) > 20
    info = probe(output)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    assert int(video['nb_frames']) == 96 and audio['sample_rate'] == '48000'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash


def test_normalization_allows_a_video_with_no_audio(tmp_path, synthetic_media):
    source = tmp_path / 'silent.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(synthetic_media[0]),
                    '-an', '-c:v', 'copy', str(source)], capture_output=True, check=True)
    value = batch(4)
    result = exports.export_production(tmp_path, value, lambda _: source, normalize_audio=True)
    output = exports.export_file(tmp_path, value['id'], result['export_id'], result['filename'])
    assert not any(s['codec_type'] == 'audio' for s in probe(output)['streams'])


def test_bounded_recovery_reads_original_and_enforces_encoded_peak(monkeypatch, tmp_path):
    calls = []
    command = ['ffmpeg', '-i', 'original.mp4', '-af', 'initial-filter', str(tmp_path / 'output.mp4')]
    measurements = iter([{'input_i': -25, 'input_tp': -.6}, {'input_i': -17, 'input_tp': -.7},
                         {'input_i': -17.8, 'input_tp': -1.7}])
    monkeypatch.setattr(exports, '_measure_audio', lambda *args: next(measurements))
    monkeypatch.setattr(exports, '_run', lambda args: calls.append(list(args)))
    plan = {'method': 'measured_two_pass', 'input': {'input_i': -54, 'input_tp': -35}}
    result = exports._finish_normalization(command, tmp_path / 'output.mp4', 4, plan)
    assert len(calls) == 2 and all(c[c.index('-i') + 1] == 'original.mp4' for c in calls)
    assert 'alimiter=' in calls[0][calls[0].index('-af') + 1]
    assert result['encoded']['input_tp'] <= -1.5


def test_peak_verification_failure_is_not_published(monkeypatch, tmp_path):
    monkeypatch.setattr(exports, '_measure_audio', lambda *args: {'input_i': -16, 'input_tp': 0})
    monkeypatch.setattr(exports, '_run', lambda args: None)
    with pytest.raises(ValueError, match='AAC true peak'):
        exports._finish_normalization(['ffmpeg', '-af', 'filter'], tmp_path / 'output.mp4', 4,
                                      {'method': 'measured_two_pass', 'input': {'input_i': -20, 'input_tp': -2}})


def test_silence_is_preserved_without_inventing_gain(monkeypatch, tmp_path):
    monkeypatch.setattr(exports, '_measure_audio', lambda *args: {'input_i': None, 'input_tp': None})
    monkeypatch.setattr(exports, '_run', lambda args: pytest.fail('Do not boost digital silence'))
    result = exports._finish_normalization([], tmp_path / 'output.mp4', 4,
        {'method': 'silent_source_preserved', 'input': {'input_i': None, 'input_tp': None}})
    assert result['method'] == 'silent_source_preserved' and result['warning']
