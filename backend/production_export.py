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
        raise ValueError('Provide at most one crop edit per production item.')
    result, seen = [], set()
    for edit in value:
        if not isinstance(edit, dict) or set(edit) != {'index', 'cut_at', 'crop'}:
            raise ValueError('Each edit needs index, cut_at and crop.')
        index, cut, crop = edit['index'], edit['cut_at'], edit['crop']
        if type(index) is not int or not 0 <= index < len(items) or index in seen:
            raise ValueError('Use one unique zero-based item index per edit.')
        if type(cut) not in (int, float) or not math.isfinite(cut) or not 0 <= cut < _duration(items[index]):
            raise ValueError('A crop cut must be finite and before the end of its clip.')
        frame = math.floor(cut * 24 + 0.5)
        if frame / 24 >= _duration(items[index]):
            raise ValueError('The frame-aligned crop cut must leave at least one reaction frame.')
        if not isinstance(crop, dict) or set(crop) != {'x', 'y', 'width', 'height'}:
            raise ValueError('A crop needs x, y, width and height.')
        if any(type(crop[k]) is not int or crop[k] % 2 or crop[k] < (2 if k in ('width', 'height') else 0) for k in crop):
            raise ValueError('Crop coordinates and dimensions must be nonnegative even integers, with positive dimensions.')
        result.append({'index': index, 'cut_at': frame / 24, 'crop': dict(crop)})
        seen.add(index)
    return sorted(result, key=lambda edit: edit['index'])


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


def export_production(data: Path, batch: dict, video_path, kind='film', edits=None):
    if kind not in ('film', 'clips'):
        raise ValueError('Choose a film or individual clips export.')
    items = batch.get('items', [])
    if not items or len(items) > 100 or any(x.get('status') != 'succeeded' or not x.get('run_id') for x in items):
        raise ValueError('Every item must finish successfully before exporting this production.')
    edits = _edits(edits, items)
    for item in items:
        _duration(item)
    identity = hashlib.sha256(json.dumps({
        'name': batch.get('name', 'Production'),
        'clips': [(x['run_id'], x.get('project', {}).get('duration', x.get('duration')),
                   x.get('project', {}).get('title', x.get('title', 'Clip'))) for x in items],
        'edits': edits,
    }, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:20]
    folder = data / 'production_exports' / safe_id(batch['id']) / identity
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / ('film.mp4' if kind == 'film' else 'clips.zip')
    with _LOCK:
        if not output.exists():
            # Validate every crop against decoded metadata before any transcode.
            sources = [Path(video_path(safe_id(item['run_id']))) for item in items]
            edit_map, dimensions = {edit['index']: edit for edit in edits}, {}
            for edit in edits:
                index, crop = edit['index'], edit['crop']
                dimensions[index] = _source_dimensions(sources[index])
                width, height = dimensions[index]
                if crop['x'] + crop['width'] > width or crop['y'] + crop['height'] > height:
                    raise ValueError(f'Crop for item {index} exceeds its {width}×{height} source frame.')
            files = []
            manifest = {'name': batch.get('name', 'Production'), 'review_status': 'Generated; creative review required', 'edits': edits, 'clips': []}
            for index, item in enumerate(items, 1):
                source = sources[index - 1]
                project = item.get('project', {})
                duration = _duration(item)
                target = folder / f'{index:03}.mp4'
                if not target.exists():
                    temp = folder / f'{index:03}-building.mp4'
                    edit = edit_map.get(index - 1)
                    video_filter = (['-filter_complex', _crop_filter(edit, dimensions[index - 1]), '-map', '[edited]']
                                    if edit else ['-map', '0:v:0', '-vf', 'setsar=1'])
                    _run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(source),
                          '-t', str(duration), *video_filter, '-map', '0:a:0?', '-map_metadata', '-1',
                          '-r', '24', '-c:v', 'libx264', '-crf', '17', '-preset', 'fast',
                          '-threads', '4', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
                          '-movflags', '+faststart', str(temp)])
                    temp.replace(target)
                files.append(target)
                manifest['clips'].append({'index': index, 'title': project.get('title', item.get('title', 'Clip')),
                                          'run_id': item['run_id'], 'duration': duration, 'file': target.name})
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
            'review_status': 'Generated; creative review required', 'clip_count': len(items), 'edits': edits}

def export_file(data: Path, batch_id: str, export_id: str, filename: str):
    if not re.fullmatch(r'[0-9a-f]{20}', export_id) or filename not in ('film.mp4', 'clips.zip', 'manifest.json'):
        raise ValueError('Choose an existing production export.')
    path=data/'production_exports'/safe_id(batch_id)/export_id/filename
    if not path.is_file():raise ValueError('Generate this production export first.')
    return path
