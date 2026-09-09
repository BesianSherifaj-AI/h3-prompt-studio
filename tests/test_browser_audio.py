"""CPU regressions for durationless MediaRecorder-style WebM recordings."""
import hashlib
import json
import shutil
import subprocess

import pytest

from backend import audio_tools


def probe(path):
    return json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, check=True).stdout)


def test_real_streaming_webm_without_duration_remains_playable_and_unchanged(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('This CPU integration test needs FFmpeg.')
    source = tmp_path / 'microphone.webm'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=660:duration=2.4',
                    '-c:a', 'libopus', '-live', '1', str(source)], check=True, capture_output=True)
    info = probe(source)
    assert not info['format'].get('duration')
    assert not info['streams'][0].get('duration')
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    result = audio_tools.media_info(source)
    assert result['has_audio'] and not result['has_video']
    assert result['duration'] == pytest.approx(2.4, abs=.05)
    prepared = audio_tools.prepare_reference(source, {'media_type': 'audio', 'clip_start_seconds': 0, 'clip_end_seconds': 2})
    assert prepared['duration'] == 2 and prepared['mime'] == 'audio/wav'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_missing_metadata_uses_playable_packet_span_and_bounded_scan(tmp_path, monkeypatch):
    path = tmp_path / 'recording.webm'
    path.write_bytes(b'recorded-data')
    calls = []
    def run(args, timeout):
        calls.append((args, timeout))
        return json.dumps({'packets': [
            {'stream_index': 0, 'pts_time': 'N/A', 'dts_time': '-0.007', 'duration_time': '.020'},
            {'stream_index': 0, 'pts_time': '1.993', 'duration_time': '.020'},
            {'stream_index': 0, 'pts_time': 'nan', 'duration_time': 'inf'},
            {'stream_index': 2, 'pts_time': '500', 'duration_time': '1'},
        ]}).encode()
    monkeypatch.setattr(audio_tools, '_run', run)
    result = audio_tools.measured_duration(path, {'format': {'duration': 'N/A'}, 'streams': [{'index': 0, 'codec_type': 'audio'}]})
    assert result == pytest.approx(2.02)
    assert calls[0][0][calls[0][0].index('-read_intervals') + 1] == '%+601'
    assert calls[0][1] == 30


def test_valid_stream_duration_avoids_packet_scan(tmp_path, monkeypatch):
    path = tmp_path / 'audio.ogg'
    path.touch()
    monkeypatch.setattr(audio_tools, '_run', lambda *args: pytest.fail('Unnecessary packet scan'))
    assert audio_tools.measured_duration(path, {'format': {'duration': 'nan'}, 'streams': [{'index': 0, 'codec_type': 'audio', 'duration': '4.5'}]}) == 4.5


@pytest.mark.parametrize('packets', [[], [{'stream_index': 0, 'pts_time': 'N/A'}], [{'stream_index': 0, 'pts_time': '0', 'duration_time': 'nan'}]])
def test_invalid_or_unfinished_recordings_are_not_assigned_an_invented_duration(tmp_path, monkeypatch, packets):
    path = tmp_path / 'unfinished.webm'
    path.touch()
    monkeypatch.setattr(audio_tools, '_run', lambda *args: json.dumps({'packets': packets}).encode())
    with pytest.raises(audio_tools.AudioToolError, match='Finish the recording'):
        audio_tools.measured_duration(path, {'streams': [{'index': 0, 'codec_type': 'audio'}]})
