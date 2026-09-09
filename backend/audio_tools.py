"""Local media preparation, optional CPU transcription and reversible sound mixes.

Paths are resolved by API/library code, never accepted as public request paths.
No helper downloads models, queues ComfyUI or changes an original media file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import tempfile
import threading


class AudioToolError(ValueError):
    pass


def _number(value, name, minimum=0, maximum=86400):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise AudioToolError(f'{name} must be a number between {minimum} and {maximum}.')
    return float(value)


def _run(args, timeout=120):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioToolError('The local media tool is unavailable or took too long. Check FFmpeg and retry.') from exc
    if result.returncode:
        raise AudioToolError('The media file could not be decoded or processed. Use a supported audio/video file.')
    return result.stdout


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def measured_duration(path, probe=None):
    """Measure normal files and browser WebM recordings without modifying them.

    MediaRecorder's streaming WebM often has no container/stream duration.
    Packet timestamps provide its actual playable span. The fallback reads at
    most 601 seconds: that is enough to reject files over either the library's
    120-second or transcription's 600-second limit, without scanning indefinitely.
    Beyond that limit the returned value is a lower bound, not a full duration.
    """
    path = Path(path).resolve(strict=True)
    if probe is None:
        probe = json.loads(_run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], 30))
    streams = [s for s in probe.get('streams', []) if s.get('codec_type') in ('audio', 'video')
               and not s.get('disposition', {}).get('attached_pic')]
    if not streams:
        raise AudioToolError('No playable audio or video stream was found.')
    duration = _finite(probe.get('format', {}).get('duration'))
    if duration is not None and duration > 0:
        return duration
    durations = [_finite(s.get('duration')) for s in streams]
    durations = [value for value in durations if value is not None and value > 0]
    if durations:
        return max(durations)
    try:
        packets = json.loads(_run(['ffprobe', '-v', 'error', '-read_intervals', '%+601', '-show_packets',
                                  '-show_entries', 'packet=stream_index,pts_time,dts_time,duration_time',
                                  '-of', 'json', str(path)], 30)).get('packets', [])
        indexes = {s.get('index') for s in streams}
        start, end = None, None
        for packet in packets:
            if packet.get('stream_index') not in indexes:
                continue
            timestamp = _finite(packet.get('pts_time'))
            if timestamp is None:
                timestamp = _finite(packet.get('dts_time'))
            if timestamp is None:
                continue
            span = _finite(packet.get('duration_time'))
            packet_end = timestamp + max(0, span or 0)
            start = timestamp if start is None else min(start, timestamp)
            end = packet_end if end is None else max(end, packet_end)
        duration = end - start if start is not None and end is not None else 0
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('duration')
        return duration
    except (ValueError, TypeError, KeyError) as exc:
        raise AudioToolError('The media duration could not be measured. Finish the recording and try uploading it again.') from exc


def media_info(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise AudioToolError('The media file is missing.')
    try:
        info = json.loads(_run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], 30))
        streams = info.get('streams', [])
        duration = measured_duration(path, info)
    except (ValueError, KeyError, TypeError) as exc:
        raise AudioToolError('The media duration could not be measured.') from exc
    video = next((s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    return {'duration': duration, 'has_audio': any(s.get('codec_type') == 'audio' for s in streams),
            'has_video': video is not None, 'width': video.get('width') if video else None,
            'height': video.get('height') if video else None}


def prepare_reference(path, asset):
    """Return normalized bytes and provenance for a 2–15s H3 reference.

    clip_start_seconds/clip_end_seconds select the original file interval.
    Videos become 24fps MP4; unselected soundtracks are physically omitted.
    """
    path = Path(path).resolve(strict=True)
    if not 0 < path.stat().st_size <= 256 * 1024 * 1024:
        raise AudioToolError('Reference audio/video must be between 1 byte and 256 MB.')
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if asset.get('sha256') and asset['sha256'] != original_hash:
        raise AudioToolError('The reference changed in the library. Replace it before sending.')
    kind = asset.get('media_type')
    if kind not in ('audio', 'video'):
        raise AudioToolError('Choose an audio or video reference.')
    info = media_info(path)
    if not info['has_' + kind]:
        raise AudioToolError(f'The selected file has no {kind} stream.')
    start = _number(asset.get('clip_start_seconds', 0), 'Clip start')
    end = _number(asset.get('clip_end_seconds') if asset.get('clip_end_seconds') is not None else info['duration'], 'Clip end')
    if end > info['duration'] + .05 or not 2 <= end - start <= 15:
        raise AudioToolError('Choose a 2–15 second reference range within the source file.')
    if kind == 'video' and asset.get('audio_enabled') is True and not info['has_audio']:
        raise AudioToolError('This video has no soundtrack. Turn off its audio reference.')
    ext, mime = ('.wav', 'audio/wav') if kind == 'audio' else ('.mp4', 'video/mp4')
    with tempfile.TemporaryDirectory(prefix='h3-reference-') as folder:
        output = Path(folder) / ('reference' + ext)
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(path), '-ss', str(start), '-t', str(end - start)]
        if kind == 'audio':
            args += ['-map', '0:a:0', '-vn', '-ac', '2', '-ar', '32000', '-c:a', 'pcm_s16le']
        else:
            args += ['-map', '0:v:0', '-vf', "fps=24,scale=trunc(iw/2)*2:trunc(ih/2)*2", '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p']
            args += ['-map', '0:a:0', '-c:a', 'aac', '-ar', '32000', '-ac', '2'] if asset.get('audio_enabled') is True else ['-an']
            args += ['-movflags', '+faststart']
        _run(args + ['-y', str(output)])
        result_info = media_info(output)
        data = output.read_bytes()
    if len(data) > 256 * 1024 * 1024:
        raise AudioToolError('Prepared reference is larger than 256 MB. Use a smaller source.')
    return {'asset': asset, 'data': data, 'mime': mime, 'extension': ext, 'sha256': hashlib.sha256(data).hexdigest(),
            'source_sha256': original_hash, 'source_duration': info['duration'], 'duration': end - start,
            'clip_start_seconds': start, 'clip_end_seconds': end, 'fps': 24 if kind == 'video' else None,
            'dimensions': [result_info['width'], result_info['height']] if kind == 'video' else None,
            'audio_enabled': asset.get('audio_enabled') is True if kind == 'video' else True}


_transcription_lock = threading.Lock()


def transcription_capabilities():
    available = importlib.util.find_spec('faster_whisper') is not None
    return {'available': available, 'engine': 'faster-whisper', 'device': 'cpu', 'downloads_automatically': False,
            'default_model': 'small', 'note': 'Uses an already cached model on CPU; missing models are reported without downloading.'}


def transcribe(path, options=None, *, model_factory=None):
    """options: model (cached name/path), language (optional), cpu_threads (1–8)."""
    options = options or {}
    info = media_info(path)
    if not info['has_audio'] or info['duration'] > 600:
        raise AudioToolError('Choose an audio recording up to ten minutes long.')
    model_name = options.get('model', 'small')
    if not isinstance(model_name, str) or not model_name.strip() or len(model_name) > 512:
        raise AudioToolError('Choose a cached transcription model.')
    language = options.get('language') or None
    if language is not None and (not isinstance(language, str) or not language.isalpha() or not 2 <= len(language) <= 3):
        raise AudioToolError('Use a two or three letter language code, or automatic detection.')
    threads = options.get('cpu_threads', 4)
    if type(threads) is not int or not 1 <= threads <= 8:
        raise AudioToolError('CPU threads must be between 1 and 8.')
    try:
        if model_factory is None:
            from faster_whisper import WhisperModel
            model_factory = WhisperModel
        with _transcription_lock:
            model = model_factory(model_name, device='cpu', compute_type='int8', cpu_threads=threads, local_files_only=True)
            segments, result = model.transcribe(str(Path(path).resolve(strict=True)), language=language, beam_size=3,
                                                vad_filter=True, condition_on_previous_text=False)
            rows = [{'start': float(s.start), 'end': float(s.end), 'text': s.text.strip()} for s in segments]
        return {'text': ' '.join(s['text'] for s in rows).strip(), 'segments': rows,
                'language': result.language, 'engine': 'faster-whisper', 'model': model_name, 'device': 'cpu'}
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise AudioToolError('Local transcription is unavailable. Install faster-whisper and cache the chosen model, or type the text.') from exc


def mix_soundtrack(video_path, tracks, output_path):
    """tracks: {path,start_seconds,end_seconds,offset_seconds,gain,fade_in,fade_out,duck}.

    Trusted resolved paths only. Original audio is always retained; duck reduces
    the added track using the original audio envelope, not a claimed speech detector.
    """
    source = Path(video_path).resolve(strict=True)
    output = Path(output_path).resolve()
    if source == output or output.exists():
        raise AudioToolError('Choose a new output file. Original videos and previous mixes are preserved.')
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 8:
        raise AudioToolError('Choose between one and eight soundtrack tracks.')
    info = media_info(source)
    if not info['has_video']:
        raise AudioToolError('The source must contain video.')
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(source)]
    filters, mix_inputs, normalized = [], ['[base]'], []
    if info['has_audio']:
        filters.append('[0:a]aresample=32000,aformat=channel_layouts=stereo,apad[original]')
    else:
        filters.append(f'anullsrc=r=32000:cl=stereo,atrim=duration={info["duration"]}[original]')
    duck_count = sum(t.get('duck') is True for t in tracks if isinstance(t, dict))
    if duck_count:
        filters.append('[original]asplit=' + str(duck_count + 1) + '[base]' + ''.join(f'[side{i}]' for i in range(duck_count)))
    else:
        filters.append('[original]anull[base]')
    duck_index = 0
    for index, track in enumerate(tracks, 1):
        if not isinstance(track, dict):
            raise AudioToolError('Each soundtrack needs a file and timing settings.')
        path = Path(track['path']).resolve(strict=True)
        ti = media_info(path)
        if not ti['has_audio']:
            raise AudioToolError('A selected soundtrack has no audio.')
        start = _number(track.get('start_seconds', 0), 'Track start')
        end = _number(track.get('end_seconds') if track.get('end_seconds') is not None else ti['duration'], 'Track end')
        offset = _number(track.get('offset_seconds', 0), 'Track placement', maximum=info['duration'])
        gain = _number(track.get('gain', 1), 'Volume', maximum=4)
        fade_in = _number(track.get('fade_in', 0), 'Fade in')
        fade_out = _number(track.get('fade_out', 0), 'Fade out')
        if end <= start or end > ti['duration'] + .05 or max(fade_in, fade_out) > end - start:
            raise AudioToolError('Track range and fades must fit within the source recording.')
        args += ['-i', str(path)]
        chain = f'[{index}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aresample=32000,aformat=channel_layouts=stereo,volume={gain}'
        if fade_in:
            chain += f',afade=t=in:st=0:d={fade_in}'
        if fade_out:
            chain += f',afade=t=out:st={end - start - fade_out}:d={fade_out}'
        chain += f',adelay={round(offset * 1000)}:all=1,apad[track{index}]'
        filters.append(chain)
        if track.get('duck') is True:
            filters.append(f'[track{index}][side{duck_index}]sidechaincompress=threshold=0.035:ratio=6:attack=20:release=250[duck{index}]')
            duck_index += 1
            mix_inputs.append(f'[duck{index}]')
        else:
            mix_inputs.append(f'[track{index}]')
        normalized.append({'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'start': start, 'end': end,
                           'offset': offset, 'gain': gain, 'fade_in': fade_in, 'fade_out': fade_out, 'duck': track.get('duck') is True})
    filters.append(''.join(mix_inputs) + f'amix=inputs={len(mix_inputs)}:duration=first:normalize=0,alimiter=limit=0.95,atrim=duration={info["duration"]}[mix]')
    revision = hashlib.sha256(json.dumps({'video': hashlib.sha256(source.read_bytes()).hexdigest(), 'tracks': normalized}, sort_keys=True).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='h3-mix-', dir=output.parent) as folder:
        temporary = Path(folder) / 'mix.mp4'
        _run(args + ['-filter_complex', ';'.join(filters), '-map', '0:v:0', '-map', '[mix]', '-c:v', 'copy',
                     '-c:a', 'aac', '-ar', '32000', '-ac', '2', '-t', str(info['duration']), '-movflags', '+faststart', str(temporary)], 300)
        _run(['ffmpeg', '-v', 'error', '-i', str(temporary), '-f', 'null', '-'], 120)
        # Exclusive creation also protects against two workers selecting one name.
        with output.open('xb') as target:
            target.write(temporary.read_bytes())
    return {'path': str(output), 'duration': info['duration'], 'revision': revision, 'tracks': normalized,
            'original_audio_preserved': True}
