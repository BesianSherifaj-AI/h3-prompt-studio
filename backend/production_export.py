"""Export completed production clips without treating render completion as approval."""
from __future__ import annotations
import hashlib
import json
import math
import re
import subprocess
import threading
import zipfile
from pathlib import Path
from .projects import atomic_json, safe_id

_LOCK = threading.Lock()
AUDIO_NORMALIZATION_VERSION = 'measured-two-pass-v2'
AUDIO_HEADROOM_DBTP = -2.5  # Leave room for AAC reconstruction overshoot.

def _run(args):
    result = subprocess.run(args, capture_output=True, timeout=300,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Export failed; original clips are preserved. ' + result.stderr.decode('utf-8', errors='replace')[-500:])

def _duration(item):
    duration = item.get('project', {}).get('duration', item.get('duration'))
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 4 <= duration <= 15:
        raise ValueError('A production clip has an invalid delivery duration.')
    return duration


def _edits(value, items):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError('Provide at most one timing/crop edit per production item.')
    result, seen = [], set()
    for edit in value:
        if (not isinstance(edit, dict) or 'index' not in edit or len(edit) < 2
                or set(edit) - {'index', 'cut_at', 'crop', 'in_point', 'out_point'}
                or ('crop' in edit) != ('cut_at' in edit)):
            raise ValueError('Each edit needs index and trim points, a cut_at/crop pair, or both.')
        index = edit['index']
        if type(index) is not int or not 0 <= index < len(items) or index in seen:
            raise ValueError('Use one unique zero-based item index per edit.')
        duration = _duration(items[index])
        entry = {'index': index}
        end = duration
        if 'in_point' in edit or 'out_point' in edit:
            start, end = edit.get('in_point', 0), edit.get('out_point', duration)
            if (any(type(n) not in (int, float) or not math.isfinite(n) for n in (start, end))
                    or not 0 <= start < end <= duration):
                raise ValueError('Trim points must satisfy 0 <= in_point < out_point <= the authored duration.')
            start, end = (math.floor(n * 24 + 0.5) / 24 for n in (start, end))
            if not 0 <= start < end <= duration:
                raise ValueError('Frame-aligned trim points must retain at least one frame within the authored duration.')
            entry.update(in_point=start, out_point=end)
        if 'crop' not in edit:
            result.append(entry)
            seen.add(index)
            continue
        cut, crop = edit['cut_at'], edit['crop']
        if type(cut) not in (int, float) or not math.isfinite(cut) or not 0 <= cut < _duration(items[index]):
            raise ValueError('A crop cut must be finite and before the end of its clip.')
        frame = math.floor(cut * 24 + 0.5)
        if frame / 24 >= _duration(items[index]):
            raise ValueError('The frame-aligned crop cut must leave at least one reaction frame.')
        if frame / 24 >= end:
            raise ValueError('A crop cut uses source time and must precede the trim out point.')
        if not isinstance(crop, dict) or set(crop) != {'x', 'y', 'width', 'height'}:
            raise ValueError('A crop needs x, y, width and height.')
        if any(type(crop[k]) is not int or crop[k] % 2 or crop[k] < (2 if k in ('width', 'height') else 0) for k in crop):
            raise ValueError('Crop coordinates and dimensions must be nonnegative even integers, with positive dimensions.')
        result.append({**entry, 'cut_at': frame / 24, 'crop': dict(crop)})
        seen.add(index)
    return sorted(result, key=lambda edit: edit['index'])


def _delivery_duration(item, edit=None):
    return edit['out_point'] - edit['in_point'] if edit and 'in_point' in edit else _duration(item)


def _source_dimensions(path):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                            'stream=width,height', '-of', 'json', str(path)], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        stream = json.loads(probe.stdout)['streams'][0]
        width, height = stream['width'], stream['height']
        if probe.returncode or any(type(n) is not int or n <= 0 or n % 2 for n in (width, height)):
            raise ValueError()
        return width, height
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError('The source video dimensions could not be verified for this crop.') from exc


def _crop_filter(edit, dimensions):
    width, height = dimensions
    crop = edit['crop']
    close = f'crop={crop["width"]}:{crop["height"]}:{crop["x"]}:{crop["y"]},scale={width}:{height}:flags=lanczos,setsar=1'
    frame = round(edit['cut_at'] * 24)
    if frame == 0:
        return '[0:v:0]fps=24,' + close + '[edited]'
    return (f'[0:v:0]fps=24,split=2[wide][reaction];'
            f'[wide]trim=end_frame={frame},setpts=PTS-STARTPTS,setsar=1[before];'
            f'[reaction]trim=start_frame={frame},setpts=PTS-STARTPTS,{close}[after];'
            '[before][after]concat=n=2:v=1:a=0[edited]')


def _measure_audio(path, duration, in_point=0):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-threads', '2', '-filter_threads', '2',
        *(['-ss', str(in_point)] if in_point else []), '-i', str(path), '-vn', '-map', '0:a:0', '-af',
        f'atrim=duration={duration},loudnorm=I=-16:TP={AUDIO_HEADROOM_DBTP}:LRA=11:print_format=json',
        '-f', 'null', '-'], capture_output=True, timeout=120,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    text = result.stderr.decode('utf-8', errors='replace')
    try:
        if result.returncode or text.rfind('{') < 0:
            raise ValueError()
        raw = json.JSONDecoder().raw_decode(text[text.rfind('{'):])[0]
        values = {key: float(raw[key]) for key in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')}
        return {key: value if math.isfinite(value) else None for key, value in values.items()}
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('Audio loudness could not be measured; no unchecked normalized export was published.') from exc


def _limited_gain(duration, gain):
    return (f'atrim=duration={duration},volume={gain:.6f}dB,aresample=192000,'
            f'alimiter=limit={10 ** (AUDIO_HEADROOM_DBTP / 20):.9f}:level=false:latency=true,aresample=48000')


def _normalization_plan(source, duration, in_point=0):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
        'stream=index', '-of', 'json', str(source)], capture_output=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if probe.returncode:
        raise ValueError('Audio stream could not be inspected before normalization.')
    if not json.loads(probe.stdout).get('streams'):
        return None
    measured = _measure_audio(source, duration, in_point)
    if measured['input_tp'] is None:
        return {'filter': f'atrim=duration={duration}', 'method': 'silent_source_preserved', 'input': measured}
    if all(value is not None for value in measured.values()):
        chain = (f'atrim=duration={duration},loudnorm=I=-16:TP={AUDIO_HEADROOM_DBTP}:LRA=11:'
                 f'measured_I={measured["input_i"]}:measured_TP={measured["input_tp"]}:'
                 f'measured_LRA={measured["input_lra"]}:measured_thresh={measured["input_thresh"]}:'
                 f'offset={measured["target_offset"]}:linear=true')
        return {'filter': chain, 'method': 'measured_two_pass', 'input': measured}
    # Below EBU's absolute gate, do not feed -inf into loudnorm. First lift
    # measurable content conservatively; digital silence remains untouched.
    gain = min(80, max(-80, -18 - measured['input_tp']))
    return {'filter': _limited_gain(duration, gain), 'method': 'below_gate_peak_recovery',
            'gain_db': gain, 'input': measured}


def _finish_normalization(command, temporary, duration, plan):
    measured = _measure_audio(temporary, duration)
    attempts, method = 0, plan['method']
    source_i = plan['input']['input_i']
    gain = plan.get('gain_db', min(80, max(-80, -16 - source_i)) if source_i is not None else 0)
    # Sparse, very quiet sources can still defeat loudnorm's dynamic second
    # pass. Refine a measured gain with an oversampled limiter, always reading
    # the ORIGINAL input command. Never cascade lossy encodes or add sound.
    while measured['input_i'] is not None and abs(measured['input_i'] + 16) > 1 and attempts < 3:
        gain = min(80, max(-80, gain + (-16 - measured['input_i'])))
        command[command.index('-af') + 1] = _limited_gain(duration, gain)
        _run(command)
        measured = _measure_audio(temporary, duration)
        attempts += 1
        method = 'measured_gain_limited_recovery'
    # Verify the encoded AAC, not merely the filter's predicted true peak.
    for _ in range(2):
        peak = measured['input_tp']
        if peak is None or peak <= -1.5:
            break
        attenuation = -1.5 - peak - 0.35
        command[command.index('-af') + 1] += f',volume={attenuation:.6f}dB'
        _run(command)
        measured = _measure_audio(temporary, duration)
        attempts += 1
    if measured['input_tp'] is not None and measured['input_tp'] > -1.5:
        raise ValueError('AAC true peak still exceeds -1.5 dBTP; no unchecked export was published.')
    return {'version': AUDIO_NORMALIZATION_VERSION, 'method': method, 'input': plan['input'],
            'encoded': measured, 'refinement_encodes': attempts, 'target_lufs': -16, 'maximum_dbtp': -1.5,
            'warning': 'Integrated loudness remains more than 3 LU from target; review sparse audio or silence.'
                       if measured['input_i'] is None or abs(measured['input_i'] + 16) > 3 else None}


def export_production(data: Path, batch: dict, video_path, kind='film', edits=None, normalize_audio=False):
    if kind not in ('film', 'clips'):
        raise ValueError('Choose a film or individual clips export.')
    if type(normalize_audio) is not bool:
        raise ValueError('normalize_audio must be true or false.')
    items = batch.get('items', [])
    if not items or len(items) > 100 or any(x.get('status') != 'succeeded' or not x.get('run_id') for x in items):
        raise ValueError('Every item must finish successfully before exporting this production.')
    edits = _edits(edits, items)
    edit_map = {edit['index']: edit for edit in edits}
    total_duration = sum(_delivery_duration(item, edit_map.get(index)) for index, item in enumerate(items))
    for item in items:
        _duration(item)
    identity = hashlib.sha256(json.dumps({
        'name': batch.get('name', 'Production'),
        'clips': [(x['run_id'], x.get('project', {}).get('duration', x.get('duration')),
                   x.get('project', {}).get('title', x.get('title', 'Clip'))) for x in items],
        'edits': edits,
        'normalize_audio': normalize_audio,
        'audio_normalization_version': AUDIO_NORMALIZATION_VERSION if normalize_audio else None,
    }, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:20]
    folder = data / 'production_exports' / safe_id(batch['id']) / identity
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / ('film.mp4' if kind == 'film' else 'clips.zip')
    with _LOCK:
        if not output.exists():
            # Validate every crop against decoded metadata before any transcode.
            sources = [Path(video_path(safe_id(item['run_id']))) for item in items]
            dimensions = {}
            for edit in edits:
                if 'crop' not in edit:
                    continue
                index, crop = edit['index'], edit['crop']
                dimensions[index] = _source_dimensions(sources[index])
                width, height = dimensions[index]
                if crop['x'] + crop['width'] > width or crop['y'] + crop['height'] > height:
                    raise ValueError(f'Crop for item {index} exceeds its {width}×{height} source frame.')
            files = []
            manifest = {'name': batch.get('name', 'Production'), 'review_status': 'Generated; creative review required',
                        'duration': total_duration,
                        'edits': edits, 'normalize_audio': normalize_audio,
                        'audio_normalization_version': AUDIO_NORMALIZATION_VERSION if normalize_audio else None, 'clips': []}
            for index, item in enumerate(items, 1):
                source = sources[index - 1]
                project = item.get('project', {})
                edit = edit_map.get(index - 1)
                duration = _delivery_duration(item, edit)
                in_point = edit.get('in_point', 0) if edit else 0
                target = folder / f'{index:03}.mp4'
                audio_receipt = folder / f'{index:03}-audio.json'
                if not target.exists() or normalize_audio and not audio_receipt.exists():
                    temp = folder / f'{index:03}-building.mp4'
                    # Crop cut_at remains on the original source timeline. A
                    # cut before the selected in point crops the whole range.
                    relative_crop = {**edit, 'cut_at': max(0, edit['cut_at'] - in_point)} if edit and 'crop' in edit else None
                    video_filter = (['-filter_complex', _crop_filter(relative_crop, dimensions[index - 1]), '-map', '[edited]']
                                    if relative_crop else ['-map', '0:v:0', '-vf', 'setsar=1'])
                    plan = _normalization_plan(source, duration, in_point) if normalize_audio else None
                    audio_filter = ['-af', plan['filter']] if plan else []
                    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                          *(['-ss', str(in_point)] if in_point else []), '-i', str(source),
                          '-t', str(duration), *video_filter, '-map', '0:a:0?', '-map_metadata', '-1',
                          *audio_filter,
                          '-r', '24', '-c:v', 'libx264', '-crf', '17', '-preset', 'fast',
                          '-threads', '4', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
                          '-movflags', '+faststart', str(temp)]
                    _run(command)
                    if normalize_audio:
                        audit = _finish_normalization(command, temp, duration, plan) if plan else {'version': AUDIO_NORMALIZATION_VERSION, 'method': 'no_audio_stream'}
                        atomic_json(audio_receipt, audit)
                    temp.replace(target)
                files.append(target)
                manifest['clips'].append({'index': index, 'title': project.get('title', item.get('title', 'Clip')),
                                          'run_id': item['run_id'], 'duration': duration, 'file': target.name,
                                          **({'in_point': in_point, 'out_point': edit['out_point'], 'source_duration': _duration(item)} if edit and 'in_point' in edit else {}),
                                          **({'audio_normalization': json.loads(audio_receipt.read_text(encoding='utf-8'))} if normalize_audio else {})})
            atomic_json(folder / 'manifest.json', manifest)
            if kind == 'film':
                # Prevent silent distortion or invalid concatenation across differing formats.
                signatures=[]
                for path in files:
                    probe=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(path)],capture_output=True,timeout=30)
                    if probe.returncode: raise ValueError('A clip could not be inspected before joining.')
                    streams=json.loads(probe.stdout)['streams']
                    signatures.append([(s['codec_type'],s.get('codec_name'),s.get('width'),s.get('height'),s.get('sample_rate'),s.get('channels')) for s in streams])
                if any(s != signatures[0] for s in signatures[1:]):
                    raise ValueError('Use individual clips export for a production with mixed video or audio formats.')
                playlist=folder/'assembly.ffconcat'
                playlist.write_text('ffconcat version 1.0\n'+''.join(f"file '{x.name}'\n" for x in files),encoding='utf-8')
                temp=folder/'film-building.mp4'
                _run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-f','concat','-safe','1','-i',str(playlist),'-c','copy','-movflags','+faststart',str(temp)])
                temp.replace(output)
            else:
                temp=folder/'clips-building.zip'
                archive_clips=[]
                for item in manifest['clips']:
                    title=re.sub(r'[^a-zA-Z0-9 _-]','',item['title'])[:90].strip() or 'clip'
                    archive_clips.append({**item, 'file': f'{item["index"]:03}-{title}.mp4'})
                with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr('manifest.json', json.dumps({**manifest, 'clips': archive_clips},
                                                                 ensure_ascii=False, indent=2))
                    for path,item in zip(files,archive_clips):
                        archive.write(path,item['file'])
                temp.replace(output)
    return {'id': batch['id'], 'export_id': identity, 'kind': kind, 'filename': output.name,
            'url': f'/api/production/{batch["id"]}/exports/{identity}/{output.name}',
            'review_status': 'Generated; creative review required', 'clip_count': len(items), 'edits': edits,
            'duration': total_duration,
            'normalize_audio': normalize_audio,
            'audio_normalization_version': AUDIO_NORMALIZATION_VERSION if normalize_audio else None}

def export_file(data: Path, batch_id: str, export_id: str, filename: str):
    if not re.fullmatch(r'[0-9a-f]{20}', export_id) or filename not in ('film.mp4', 'clips.zip', 'manifest.json'):
        raise ValueError('Choose an existing production export.')
    path=data/'production_exports'/safe_id(batch_id)/export_id/filename
    if not path.is_file():raise ValueError('Generate this production export first.')
    return path
