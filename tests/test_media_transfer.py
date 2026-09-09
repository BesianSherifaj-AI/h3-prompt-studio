"""Real CPU media normalization and mocked native ComfyUI binding contracts."""
import copy
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.audio_tools import AudioToolError, media_info, mix_soundtrack, prepare_reference, transcribe
from backend.comfy_transfer import TransferError, build_transfer
from test_comfy_transfer import FakeComfy, assert_ui_api_parity, make_project, node_schema, schema as base_schema
from test_mmh3_transfer import MMH3Comfy, schema as mmh3_schema


@pytest.fixture
def media(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('CPU FFmpeg integration test needs FFmpeg.')
    audio, video = tmp_path / 'voice.wav', tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', str(audio)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x64:r=30:d=4',
                    '-i', str(audio), '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(video)], check=True)
    return audio, video


def av_schema(mmh3=False):
    result = (mmh3_schema if mmh3 else base_schema).__wrapped__()
    def spec(kind): return [kind, {}]
    def grow(kind, prefix):
        return ['COMFY_AUTOGROW_V3', {'template': {'input': {'required': {prefix.rstrip('_'): spec(kind)}}, 'prefix': prefix, 'min': 0, 'max': 3}}]
    result['LoadAudio'] = node_schema({'audio': [[]]}, ['AUDIO'])
    result['LoadVideo'] = node_schema({'file': [[]]}, ['VIDEO'])
    result['GetVideoComponents'] = node_schema({'video': spec('VIDEO')}, ['IMAGE', 'AUDIO', 'FLOAT', 'COMBO', 'COMBO'])
    optional = result['MiniMaxH3ReferenceToVideo']['input']['optional']
    optional.update(ref_videos=grow('IMAGE', 'ref_video_'), ref_video_audios=grow('AUDIO', 'ref_video_audio_'), ref_audios=grow('AUDIO', 'ref_audio_'))
    if mmh3:
        branch = result['MMH3Create']['input']['required']['task'][1]['options'][1]['inputs']['optional']
        branch.update(reference_videos=grow('VIDEO', 'video_'), reference_audios=grow('AUDIO', 'audio_'))
    return result


def references(media):
    return [{'id': 'video-reference', 'name': 'Scene motion', 'media_type': 'video', 'role': 'reference_video', 'audio_enabled': True,
             'duration': 4, 'clip_start_seconds': 1, 'clip_end_seconds': 3, 'semantic_role': 'background', 'prompt_tag': 'location', 'enabled': True},
            {'id': 'audio-reference', 'name': 'Voice', 'media_type': 'audio', 'role': 'reference_audio', 'duration': 4,
             'clip_start_seconds': 0, 'clip_end_seconds': 2, 'semantic_role': 'other', 'prompt_tag': 'voice', 'enabled': True}]


def test_native_media_bindings_preserve_audio_order_bytes_ranges_and_graph(media, tmp_path):
    schema = av_schema()
    assets = references(media)
    client = FakeComfy(schema)
    result = build_transfer(make_project(assets), 'Motion <Video 1>, its <Audio 1>, standalone <Audio 2>.', {},
                            lambda a: media[1 if a['media_type'] == 'video' else 0], client=client, template_dir=tmp_path / 'none')
    graph = result['prompt']
    cond = next(n['inputs'] for n in graph.values() if n['class_type'] == 'MiniMaxH3ReferenceToVideo')
    refs = result['manifest']['references']
    assert refs[0]['token'] == '<Video 1>' and refs[0]['soundtrack_token'] == '<Audio 1>'
    assert refs[1]['token'] == '<Audio 2>'
    assert cond['ref_videos.ref_video_0'][0] == cond['ref_video_audios.ref_video_audio_0'][0]
    assert cond['ref_video_audios.ref_video_audio_0'][1] == 1
    assert cond['ref_audios.ref_audio_0'] == [refs[1]['node_id'], 0]
    assert refs[0]['fps'] == 24 and refs[0]['duration'] == 2
    assert refs[0]['source_sha256'] == hashlib.sha256(media[1].read_bytes()).hexdigest()
    assert result['manifest']['images'] == [] and result['manifest']['media_bytes_verified']
    assert_ui_api_parity(result, schema)
    assert len(client.uploads) == 2


@pytest.mark.parametrize('continuation', [False, True])
def test_mmh3_records_native_video_and_audio_sources(media, tmp_path, continuation):
    schema = av_schema(True)
    client = MMH3Comfy(schema)
    settings = {'continuation_source': 'mmh3/source.mmh3'} if continuation else {}
    result = build_transfer(make_project(references(media)), 'Exact reference prompt', settings,
                            lambda a: media[1 if a['media_type'] == 'video' else 0], client=client, template_dir=tmp_path / 'none')
    graph = result['prompt']
    packet = next(n['inputs'] for n in graph.values() if n['class_type'] == 'MMH3Create')
    video_link = packet['task.reference_videos.video_0']
    audio_link = packet['task.reference_audios.audio_0']
    assert graph[video_link[0]]['class_type'] == 'LoadVideo'
    assert graph[audio_link[0]]['class_type'] == 'LoadAudio'
    if continuation:
        assert result['manifest']['references'][0]['input_slot'] == 'task.reference_videos.video_0'
    assert_ui_api_parity(result, schema)


def test_unavailable_native_audio_socket_fails_before_any_upload(media, tmp_path):
    schema = av_schema()
    del schema['MiniMaxH3ReferenceToVideo']['input']['optional']['ref_audios']
    client = FakeComfy(schema)
    with pytest.raises(TransferError, match='inputs changed'):
        build_transfer(make_project(references(media)), 'Exact prompt', {}, lambda a: media[1 if a['media_type'] == 'video' else 0], client=client)
    assert not client.uploads


def test_reference_normalization_omits_unselected_sound_and_never_changes_original(media):
    source_hash = hashlib.sha256(media[1].read_bytes()).hexdigest()
    result = prepare_reference(media[1], {'media_type': 'video', 'clip_start_seconds': 1, 'clip_end_seconds': 3, 'audio_enabled': False})
    assert result['source_sha256'] == source_hash
    assert result['audio_enabled'] is False and result['duration'] == 2
    assert hashlib.sha256(media[1].read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize('changes', [{'clip_start_seconds': True}, {'clip_end_seconds': 50}, {'clip_start_seconds': 3, 'clip_end_seconds': 4}, {'sha256': 'wrong'}])
def test_invalid_ranges_and_changed_media_are_rejected(media, changes):
    with pytest.raises(AudioToolError):
        prepare_reference(media[0], {'media_type': 'audio', **changes})


def test_soundtrack_mix_is_playable_nondestructive_and_has_stable_revision(media, tmp_path):
    original_hash = hashlib.sha256(media[1].read_bytes()).hexdigest()
    tracks = [{'path': media[0], 'start_seconds': .5, 'end_seconds': 2.5, 'offset_seconds': .5,
               'gain': .3, 'fade_in': .1, 'fade_out': .2, 'duck': True}]
    result = mix_soundtrack(media[1], tracks, tmp_path / 'mixed.mp4')
    assert media_info(result['path'])['has_audio']
    assert result['duration'] == pytest.approx(4, abs=.1)
    assert hashlib.sha256(media[1].read_bytes()).hexdigest() == original_hash
    second = mix_soundtrack(media[1], tracks, tmp_path / 'second.mp4')
    assert result['revision'] == second['revision']
    with pytest.raises(AudioToolError, match='Original'):
        mix_soundtrack(media[1], tracks, media[1])
    with pytest.raises(AudioToolError, match='previous mixes'):
        mix_soundtrack(media[1], tracks, tmp_path / 'mixed.mp4')


def test_transcription_is_cpu_cached_only_and_preserves_words(media):
    calls = []
    class FakeModel:
        def __init__(self, name, **options): calls.append((name, options))
        def transcribe(self, path, **options):
            from types import SimpleNamespace
            return iter([SimpleNamespace(start=0, end=1, text='Hello there.')]), SimpleNamespace(language='en')
    result = transcribe(media[0], {'model': 'small'}, model_factory=FakeModel)
    assert result['text'] == 'Hello there.'
    assert calls[0][1]['device'] == 'cpu' and calls[0][1]['local_files_only'] is True
