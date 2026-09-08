from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import secrets
import subprocess
import threading
import time
import uuid
import zipfile
import httpx
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError

from .projects import new_project, safe_id, atomic_json, check_project, merge_plan, merge_assist, ALLOWED_SHOT_FIELDS
from .resources import ResourceManager, ResourceError, local_url, gpu_snapshot

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('H3_STUDIO_DATA', ROOT / 'data')).resolve()
for folder in ('projects', 'assets', 'history', 'exports', 'library/templates', 'library/versions'):
    (DATA / folder).mkdir(parents=True, exist_ok=True)
Image.MAX_IMAGE_PIXELS = 40_000_000
TOKEN, BRIDGE_TOKEN = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
STATE_LOCK = threading.RLock()
TRANSFERS = {}
TRANSFER_TTL = 15 * 60
VIDEO_RUNS = None
STORIES = None
ASSET_RUNS = None
VIDEO_FILE_LOCKS = {}
DEFAULT_SETTINGS = {'lm_url': 'http://127.0.0.1:1234/v1', 'model': '', 'context_length': 8192,
                    'comfy_urls': ['http://127.0.0.1:8188', 'http://127.0.0.1:8000', 'http://127.0.0.1:8010'], 'persona': 'universal', 'last_project': '',
                    'ai_memory_mode': 'exclusive'}
SETTINGS = {**DEFAULT_SETTINGS}
if (DATA / 'settings.json').exists():
    SETTINGS.update(json.loads((DATA / 'settings.json').read_text(encoding='utf-8')))

def client():
    from .lmstudio import LMStudioClient
    return LMStudioClient(base_url=SETTINGS['lm_url'], timeout=180)

RESOURCES = ResourceManager(lambda: copy.deepcopy(SETTINGS), client, state_path=DATA / 'resource_state.json')
app = FastAPI(title='H3 Prompt Studio', version='1.1.0', docs_url='/api/docs')
BRIDGE_PORTS = ('8188', '8000', '8010')
LOCAL_ORIGINS = [f'http://{host}:{port}' for host in ('127.0.0.1', 'localhost') for port in (8766, 8188, 8010, 8000)]
app.add_middleware(CORSMiddleware, allow_origins=LOCAL_ORIGINS, allow_methods=['GET', 'POST', 'PUT', 'PATCH'], allow_headers=['Content-Type', 'X-H3-Bridge', 'X-H3-Token'])

@app.middleware('http')
async def local_boundary(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in ('127.0.0.1', 'localhost', 'testserver'):
        return JSONResponse({'detail': 'Local connections only.'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin not in LOCAL_ORIGINS:
        return JSONResponse({'detail': 'This origin is not connected to Studio.'}, status_code=403)
    transfer_read = request.method in ('GET', 'OPTIONS') and request.url.path.startswith('/api/comfy/transfers/')
    if origin and origin.rsplit(':', 1)[-1] in BRIDGE_PORTS and request.url.path != '/api/gpu/prepare-h3' and not transfer_read:
        return JSONResponse({'detail': 'ComfyUI bridge access is limited to prepared workflow transfers and GPU hand-off.'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        normal = secrets.compare_digest(request.headers.get('x-h3-token', ''), TOKEN)
        scoped = request.url.path == '/api/gpu/prepare-h3' and secrets.compare_digest(request.headers.get('x-h3-bridge', ''), BRIDGE_TOKEN)
        if not normal and not scoped:
            return JSONResponse({'detail': 'Studio session expired. Reload this page.'}, status_code=403)
    try:
        length = int(request.headers.get('content-length', '0'))
        if length > 140_000_000:
            return JSONResponse({'detail': 'This upload is too large.'}, status_code=413)
    except ValueError:
        return JSONResponse({'detail': 'Invalid content length.'}, status_code=400)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors " + ' '.join(LOCAL_ORIGINS)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.exception_handler(ValueError)
async def bad_value(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)

@app.exception_handler(ResourceError)
async def resource_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=409)

@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    # Never log an image, API credential, request body or user dialogue.
    return JSONResponse({'detail': f'{type(exc).__name__}: {str(exc)[:1200]}'}, status_code=502)

def load_project(project_id):
    path = DATA / 'projects' / (safe_id(project_id) + '.json')
    if not path.exists():
        raise HTTPException(404, 'Project not found.')
    return check_project(json.loads(path.read_text(encoding='utf-8')))

def list_projects():
    values = []
    for path in (DATA / 'projects').glob('*.json'):
        try:
            value = check_project(json.loads(path.read_text(encoding='utf-8')))
            values.append({'id': value['id'], 'title': value['title'], 'mode': value['mode'], 'duration': value['duration'], 'updated': path.stat().st_mtime})
        except (ValueError, KeyError):
            continue
    return sorted(values, key=lambda item: item['updated'], reverse=True)

def save_project(project):
    check_project(project)
    with STATE_LOCK:
        path = DATA / 'projects' / (safe_id(project['id']) + '.json')
        if path.exists():
            previous = path.read_bytes()
            # At most one automatic history snapshot each minute per project.
            history = DATA / 'history' / project['id'] / f'{int(time.time() // 60)}.json'
            if not history.exists():
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_bytes(previous)
        atomic_json(path, project)
        SETTINGS['last_project'] = project['id']
        atomic_json(DATA / 'settings.json', SETTINGS)
    return {'saved': True, 'id': project['id']}

@app.get('/api/bootstrap')
def bootstrap():
    from .prompts import PERSONAS
    projects = list_projects()
    last = SETTINGS.get('last_project')
    project = load_project(last) if last and any(p['id'] == last for p in projects) else new_project()
    return {'version': '1.1.0', 'token': TOKEN, 'resource_token': BRIDGE_TOKEN, 'settings': SETTINGS,
            'project': project, 'projects': projects, 'personas': PERSONAS}

def output_locations():
    # Only these application-owned locations can be opened; never accept a path
    # or shell command from the browser.
    locations = {
        'videos': ('Local video playback copies', DATA / 'video_runs',
                   'Watched videos are cached by run ID. Original videos remain in the connected ComfyUI output/h3_prompt_studio/runs folder.'),
        'examples': ('Published examples', ROOT / 'demo',
                     'Optional public demonstration clips and sample projects included with this release.'),
        'projects': ('Saved projects & references', DATA,
                     'Your edits save automatically here. Open projects in Studio with the Projects button; use Export with images for a portable backup.'),
        'exports': ('Project export copies', DATA / 'exports',
                    'Export with images keeps a ZIP copy here and sends a download to your browser. Prompt text downloads go to your browser’s download location.'),
    }
    # A local environment setting enables Explorer shortcuts without assuming a
    # particular Desktop, portable, or source-checkout ComfyUI installation.
    output = os.environ.get('H3_STUDIO_COMFY_OUTPUT', '').strip()
    if output:
        output = Path(output).expanduser().resolve()
        locations.update({
            'comfy-videos': ('Original ComfyUI videos', output / 'h3_prompt_studio',
                             'Original renders in the configured ComfyUI output folder.'),
            'comfy-states': ('Saved continuation states', output / 'mmh3',
                             'MMH3 working files for continuing generated clips. Keep these with the original videos.'),
        })
    return locations

@app.get('/api/files')
def files_index():
    return {'locations': [{'id': key, 'title': title, 'path': str(path.resolve()),
                           'description': description, 'available': path.is_dir()}
                          for key, (title, path, description) in output_locations().items()]}

@app.post('/api/files/open')
def open_files(body: dict):
    if set(body) != {'id'} or not isinstance(body['id'], str) or body['id'] not in output_locations():
        raise ValueError('Choose one of the listed Studio folders.')
    title, path, _ = output_locations()[body['id']]
    path = path.resolve()
    if not path.is_dir():
        raise HTTPException(404, 'This folder is not available on this computer yet.')
    if not hasattr(os, 'startfile'):
        raise HTTPException(409, 'Automatic folder opening is supported on Windows. Use the displayed path to open this folder.')
    try:
        os.startfile(str(path))
    except OSError as exc:
        raise HTTPException(409, 'Windows could not open this folder. Use the displayed path to open it manually.') from exc
    return {'opened': True, 'id': body['id'], 'title': title}

@app.post('/api/projects/new')
def create_project():
    project = new_project()
    save_project(project)
    return project

@app.get('/api/projects')
def projects_index():
    return list_projects()

@app.get('/api/projects/{project_id}')
def project_get(project_id: str):
    return load_project(project_id)

@app.post('/api/projects')
def project_save(project: dict):
    return save_project(project)

def library_folder(kind):
    if kind not in ('templates', 'versions'):
        raise ValueError('Choose saved setups or saved versions.')
    return DATA / 'library' / kind

def library_text(body, key, limit, default=''):
    value = body.get(key, default)
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{key} must be text with at most {limit} characters.')
    return value

def library_rating(body):
    rating = body.get('rating', 0)
    if type(rating) is not int or not 0 <= rating <= 5:
        raise ValueError('Choose a rating from zero to five stars.')
    return rating

@app.get('/api/library')
def library_index():
    result = {'templates': [], 'versions': []}
    with STATE_LOCK:
        for kind in result:
            for path in library_folder(kind).glob('*.json'):
                record = json.loads(path.read_text(encoding='utf-8'))
                project = record['project']
                result[kind].append({k: record[k] for k in ('id', 'name', 'created_at', 'notes', 'rating')} |
                    {'mode': project['mode'], 'duration': project['duration'], 'shot_count': len(project['shots']),
                     'asset_count': len(project['assets']), 'prompt_preview': record.get('prompt', '')[:240]})
            result[kind].sort(key=lambda value: value['created_at'], reverse=True)
    return result

@app.get('/api/library/{kind}/{record_id}')
def library_get(kind: str, record_id: str):
    path = library_folder(kind) / (safe_id(record_id) + '.json')
    if not path.is_file():
        raise HTTPException(404, 'This saved item no longer exists.')
    return json.loads(path.read_text(encoding='utf-8'))

@app.post('/api/library/{kind}')
def library_save(kind: str, body: dict):
    from .compiler import compile_project
    folder = library_folder(kind)
    project = copy.deepcopy(check_project(body.get('project')))
    name = library_text(body, 'name', 120).strip()
    if not name:
        raise ValueError('Give this saved item a short name.')
    prompt = library_text(body, 'prompt', 250_000)
    if kind == 'versions' and prompt:
        compiled = compile_project(project)
        if not compiled['valid'] or prompt != compiled['prompt']:
            raise ValueError('The prompt changed. Make a fresh prompt before saving this version.')
    record = {'id': str(uuid.uuid4()), 'name': name,
              'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'project': project, 'prompt': prompt if kind == 'versions' else '',
              'notes': library_text(body, 'notes', 5000), 'rating': library_rating(body)}
    with STATE_LOCK:
        atomic_json(folder / (record['id'] + '.json'), record)
    return record

@app.patch('/api/library/versions/{record_id}')
def library_update_version(record_id: str, body: dict):
    if not body or set(body) - {'name', 'notes', 'rating'}:
        raise ValueError('Only the saved name, rating and test notes can be changed.')
    with STATE_LOCK:
        record = library_get('versions', record_id)
        if 'name' in body:
            record['name'] = library_text(body, 'name', 120).strip()
            if not record['name']:
                raise ValueError('Give this saved item a short name.')
        if 'notes' in body:
            record['notes'] = library_text(body, 'notes', 5000)
        if 'rating' in body:
            record['rating'] = library_rating(body)
        atomic_json(library_folder('versions') / (safe_id(record_id) + '.json'), record)
    return record

@app.post('/api/settings')
def save_settings(body: dict):
    if RESOURCES.lock.locked():
        raise ResourceError('Wait for AI to finish before changing its connection.')
    allowed = {k: body[k] for k in DEFAULT_SETTINGS if k in body and k != 'last_project'}
    if 'lm_url' in allowed:
        allowed['lm_url'] = local_url(allowed['lm_url'])
    if 'comfy_urls' in allowed:
        if not isinstance(allowed['comfy_urls'], list) or not 1 <= len(allowed['comfy_urls']) <= 4:
            raise ValueError('Choose one to four local ComfyUI instances.')
        allowed['comfy_urls'] = [local_url(u) for u in allowed['comfy_urls']]
    if allowed.get('context_length', 8192) not in (4096, 8192, 12288, 16384):
        raise ValueError('Choose a supported context length.')
    if 'model' in allowed and (not isinstance(allowed['model'], str) or len(allowed['model']) > 500 or (allowed['model'] and not allowed['model'].strip())):
        raise ValueError('Choose a valid installed LM Studio model.')
    if allowed.get('ai_memory_mode', 'exclusive') not in ('exclusive', 'resident_small'):
        raise ValueError('Choose automatic model switching or the resident 0.8B assistant.')
    proposed = {**SETTINGS, **allowed}
    if proposed.get('ai_memory_mode') == 'resident_small':
        from .lmstudio import LMStudioClient
        LMStudioClient(base_url=proposed['lm_url']).resident_model_info(proposed['model'])
        allowed['context_length'] = 4096
    SETTINGS.update(allowed)
    atomic_json(DATA / 'settings.json', SETTINGS)
    return SETTINGS

@app.get('/api/connections')
def connections():
    result = {'stage': RESOURCES.stage, 'busy': RESOURCES.lock.locked(), 'error': RESOURCES.last_error,
              'gpu': gpu_snapshot(), 'instance_id': RESOURCES.instance_id, 'model': RESOURCES.model_key,
              'ai_memory_mode': SETTINGS['ai_memory_mode']}
    try:
        result['lm'] = {'online': True, 'models': client().models(), 'loaded': client().loaded_instances()}
    except Exception as exc:
        result['lm'] = {'online': False, 'models': [], 'loaded': [], 'error': str(exc)[:400]}
    try:
        result['comfy'] = RESOURCES.queues()
    except Exception as exc:
        result['comfy'] = []
        result['comfy_error'] = str(exc)
    return result

@app.get('/api/comfy/options')
def comfy_options():
    from .comfy_transfer import installed_transfer_options
    return installed_transfer_options(copy.deepcopy(SETTINGS))

@app.post('/api/comfy/prepare')
def comfy_prepare(body: dict):
    project = copy.deepcopy(check_project(body.get('project')))
    transfer = build_studio_transfer(project, body.get('prompt'))
    ticket = secrets.token_urlsafe(32)
    expiry = time.time() + TRANSFER_TTL
    transfer['manifest']['title'] = 'Prompt Studio · ' + project['title']
    record = {**transfer, 'ticket': ticket, 'title': transfer['manifest']['title'],
              'comfy_origin': transfer['comfy_url'], 'resource_token': BRIDGE_TOKEN,
              'expires_at': datetime.fromtimestamp(expiry, timezone.utc).isoformat()}
    folder = DATA / 'exports' / ('comfy-' + transfer['id'])
    folder.mkdir(parents=True, exist_ok=False)
    for name, value in [('workflow.json', transfer['workflow']), ('api-workflow.json', transfer['prompt']), ('manifest.json', transfer['manifest'])]:
        atomic_json(folder / name, value)
    with STATE_LOCK:
        for old in [key for key, value in TRANSFERS.items() if value['expiry'] <= time.time()]:
            del TRANSFERS[old]
        if len(TRANSFERS) >= 40:
            del TRANSFERS[next(iter(TRANSFERS))]
        TRANSFERS[ticket] = {'expiry': expiry, 'record': record}
    return {'ticket': ticket, 'manifest': transfer['manifest'], 'expires_at': record['expires_at'],
            'comfy_url': transfer['comfy_url'], 'open_url': transfer['comfy_url'] + '/?h3studio_transfer=' + ticket,
            'export_folder': str(folder)}

def build_studio_transfer(project, prompt):
    from .compiler import compile_project
    from .comfy_transfer import build_transfer
    project = copy.deepcopy(check_project(project))
    compiled = compile_project(project)
    if not compiled['valid']:
        raise ValueError('Fix the highlighted prompt issues before sending to ComfyUI.')
    if prompt != compiled['prompt']:
        raise ValueError('The prompt changed. Review its current version before sending to ComfyUI.')
    render = project.get('comfy_render', {})
    if not isinstance(render, dict):
        raise ValueError('The ComfyUI settings must be an object.')
    config = {**render, 'comfy_urls': copy.deepcopy(SETTINGS['comfy_urls'])}
    def resolve_asset(asset):
        meta = asset_meta(asset['id'])
        folder = (DATA / 'assets' / safe_id(asset['id'])).resolve()
        path = (folder / meta['filename']).resolve()
        if not path.is_relative_to(folder) or meta.get('media_type') != 'image':
            raise ValueError('This photo is not available in the Studio library.')
        return path
    return build_transfer(project, compiled['prompt'], config, resolve_asset)

@app.get('/api/comfy/transfers/{ticket}')
def comfy_transfer_get(ticket: str, request: Request):
    if len(ticket) != 43 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in ticket):
        raise HTTPException(404, 'This workflow transfer is unavailable. Send it again from Studio.')
    with STATE_LOCK:
        value = TRANSFERS.get(ticket)
        if not value or value['expiry'] <= time.time():
            TRANSFERS.pop(ticket, None)
            raise HTTPException(404, 'This workflow transfer expired. Send it again from Studio.')
        record = value['record']
        origin = request.headers.get('origin')
        if origin and origin.rsplit(':', 1)[-1] in BRIDGE_PORTS and origin != record['comfy_origin']:
            raise HTTPException(403, 'This workflow was prepared for a different ComfyUI instance.')
        return record

@app.get('/api/system-prompt')
def system_prompt(persona: str = 'universal', mode: str = 'ref2va'):
    from .prompts import export_system_prompt
    if mode not in ('ref2va', 'fl2va', 'i2va', 'l2va', 't2va'):
        raise ValueError('Choose a supported H3 mode.')
    from .prompts import PERSONAS
    if persona not in {item['id'] for item in PERSONAS}:
        raise ValueError('Choose a listed system prompt persona.')
    return {'prompt': export_system_prompt(persona, mode)}

def video_manager():
    global VIDEO_RUNS
    with STATE_LOCK:
        if VIDEO_RUNS is None:
            from .video_runs import VideoRunManager
            VIDEO_RUNS = VideoRunManager(DATA, build_studio_transfer, RESOURCES)
        return VIDEO_RUNS

@app.get('/api/video/runs')
def video_runs_list(project_id: str | None = None):
    return {'runs': video_manager().list(safe_id(project_id) if project_id else None)}

@app.post('/api/video/runs')
def video_run_create(body: dict):
    from .compiler import compile_project
    project = copy.deepcopy(check_project(body.get('project')))
    compiled = compile_project(project)
    if not compiled['valid'] or body.get('prompt') != compiled['prompt']:
        raise ValueError('Make a current valid prompt before generating this video.')
    return video_manager().submit(body.get('request_id'), project, compiled['prompt'], parent_run_id=body.get('parent_run_id'))

@app.get('/api/video/runs/{run_id}')
def video_run_get(run_id: str):
    return video_manager().refresh(safe_id(run_id))

@app.patch('/api/video/runs/{run_id}')
def video_run_update(run_id: str, body: dict):
    return video_manager().update_metadata(safe_id(run_id), body)

@app.post('/api/video/runs/{run_id}/resolve')
def video_run_resolve(run_id: str):
    return video_manager().resolve_missing(safe_id(run_id))

@app.post('/api/video/runs/{run_id}/reroll')
def video_run_reroll(run_id: str, body: dict):
    return video_manager().reroll(body.get('request_id'), safe_id(run_id))

@app.post('/api/video/runs/{run_id}/cancel')
def video_run_cancel(run_id: str):
    return video_manager().cancel(safe_id(run_id))

@app.post('/api/video/runs/{run_id}/combine')
def video_run_combine(run_id: str, body: dict):
    return video_manager().combine(body.get('request_id'), safe_id(run_id))

@app.get('/api/video/runs/{run_id}/project')
def video_run_snapshot(run_id: str):
    return video_manager().snapshot(safe_id(run_id))

def cached_run_video(run_id: str):
    run_id = safe_id(run_id)
    manager = video_manager()
    descriptor = manager.media(run_id)
    folder = DATA / 'video_runs' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'playback.mp4'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault(run_id, threading.Lock())
    with lock:
        if path.is_file() and path.stat().st_size:
            return path
        target = local_url(descriptor['comfy_url'], '/view')
        temporary = folder / ('playback-' + uuid.uuid4().hex + '.part')
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
                with client.stream('GET', target, params={key: descriptor[key] for key in ('filename', 'subfolder', 'type')}) as response:
                    response.raise_for_status()
                    size = 0
                    with temporary.open('wb') as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > 512 * 1024 * 1024:
                                raise ValueError('This video is too large for the built-in preview cache.')
                            output.write(chunk)
                    if not size:
                        raise ValueError('ComfyUI returned an empty video file.')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path

@app.get('/api/video/runs/{run_id}/video')
def video_run_playback(run_id: str, download: bool = False):
    path = cached_run_video(run_id)
    return FileResponse(path, media_type='video/mp4', filename=f'H3-{safe_id(run_id)}.mp4' if download else None)

@app.post('/api/video/runs/{run_id}/ending-image')
def video_run_ending_image(run_id: str):
    run_id = safe_id(run_id)
    path = cached_run_video(run_id)
    metadata = path.parent / 'ending-asset.json'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('ending:' + run_id, threading.Lock())
    with lock:
        if metadata.is_file():
            result = json.loads(metadata.read_text(encoding='utf-8'))
            if (DATA / 'assets' / safe_id(result['id']) / 'source.png').is_file():
                return result
        image_path = path.parent / 'ending.png'
        # Decode only the final second, then take its actual last video frame.
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-sseof', '-1', '-i', str(path),
                                 '-an', '-vf', 'reverse,scale=min(1024\\,iw):-2', '-frames:v', '1', str(image_path)],
                                capture_output=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not image_path.is_file():
            raise ValueError('The ending image could not be read. Keep the video available and retry continuation.')
        asset = store_asset(image_path.read_bytes(), 'Previous video - actual ending.png', 'image/png')
        asset.update(role='context', semantic_role='pose', enabled=True, video_run_ending=run_id,
                     description='Actual final frame of the selected previous video. Use it to preserve final positions, clothing, camera framing and object holders. This is continuity context, not a new identity reference.')
        atomic_json(metadata, asset)
        return asset

@app.post('/api/video/runs/{run_id}/suggest')
def video_run_suggest(run_id: str, body: dict):
    from .continuation_suggestions import suggest_continuations
    duration, direction = body.get('duration', 5), body.get('direction', '')
    if type(duration) is not int or not 4 <= duration <= 15:
        raise ValueError('Choose a next clip between 4 and 15 whole seconds.')
    if not isinstance(direction, str) or len(direction) > 1000:
        raise ValueError('Keep your next-scene direction within 1000 characters.')
    job = video_manager().refresh(safe_id(run_id))
    if job.get('operation') == 'combine' and job.get('can_continue') and job.get('continue_from_run_id'):
        job = video_manager().refresh(safe_id(job['continue_from_run_id']))
    if job.get('status') != 'succeeded' or not job.get('continuation_source'):
        raise ValueError('Select a finished take with saved motion before continuing it.')
    source = video_manager().snapshot(job['id'])
    started = time.monotonic()
    def generate(model):
        RESOURCES.stage = 'Reading the actual ending and suggesting next scenes'
        ending = video_run_ending_image(job['id'])
        result = suggest_continuations(client(), model, source, duration, image_data(ending['id']), direction,
                                       small_model=SETTINGS.get('ai_memory_mode') == 'resident_small')
        return {**result, 'ending_asset': ending, 'ending_image_url': '/api/assets/' + ending['id'] + '/file',
                'model': SETTINGS['model'], 'source_run_id': job['id']}
    result = RESOURCES.run_ai(SETTINGS['model'], generate)
    result['seconds'] = round(time.monotonic() - started, 3)
    return result

@app.post('/api/gpu/prepare-ai')
def prepare_ai(body: dict):
    return RESOURCES.run_ai(body.get('model') or SETTINGS['model'])

def asset_manager():
    global ASSET_RUNS
    with STATE_LOCK:
        if ASSET_RUNS is None:
            from .asset_runs import AssetRunManager
            ASSET_RUNS = AssetRunManager(DATA, RESOURCES, lambda: copy.deepcopy(SETTINGS), store_asset)
        return ASSET_RUNS

def story_manager():
    global STORIES
    with STATE_LOCK:
        if STORIES is None:
            from .stories import StoryManager
            def save_story_project(project):
                check_project(project)
                atomic_json(DATA / 'projects' / (project['id'] + '.json'), project)
            STORIES = StoryManager(DATA, video_manager, RESOURCES, client, lambda: copy.deepcopy(SETTINGS),
                                   save_story_project, video_run_ending_image, image_data, asset_manager)
        return STORIES

@app.get('/api/assets/generators')
def asset_generators():
    return asset_manager().options()

@app.post('/api/asset-runs')
def asset_run_create(body: dict):
    return asset_manager().submit(body.get('request_id'), body.get('spec', {}))

@app.get('/api/asset-runs/{run_id}')
def asset_run_status(run_id: str):
    return asset_manager().refresh(safe_id(run_id))

@app.post('/api/asset-runs/{run_id}/cancel')
def asset_run_cancel(run_id: str):
    return asset_manager().cancel(safe_id(run_id))

@app.post('/api/asset-runs/{run_id}/resume')
def asset_run_resume(run_id: str):
    return asset_manager().resume(safe_id(run_id))

@app.post('/api/asset-runs/{run_id}/retry')
def asset_run_retry(run_id: str, body: dict):
    return asset_manager().retry(safe_id(run_id), safe_id(body.get('request_id')))

@app.get('/api/stories')
def stories_list():
    return {'stories': story_manager().list()}

@app.post('/api/stories')
def story_create(body: dict):
    return story_manager().create(body)

@app.get('/api/stories/{story_id}')
def story_get(story_id: str):
    return story_manager().get(story_id)

@app.patch('/api/stories/{story_id}')
def story_update(story_id: str, body: dict):
    return story_manager().update(story_id, body)

@app.post('/api/stories/{story_id}/branch')
def story_branch(story_id: str, body: dict):
    return story_manager().branch(story_id, body.get('run_id'), body.get('request_id'))

@app.post('/api/stories/{story_id}/attach')
def story_attach(story_id: str, body: dict):
    return story_manager().attach_run(story_id, body.get('run_id'), body.get('expected_parent'))

@app.post('/api/stories/{story_id}/alternates')
def story_alternate(story_id: str, body: dict):
    return story_manager().register_alternate(story_id, body.get('run_id'), body.get('original_run_id'))

@app.post('/api/stories/{story_id}/turns')
def story_turn_create(story_id: str, body: dict):
    return story_manager().submit(story_id, body)

@app.post('/api/stories/{story_id}/turns/{turn_id}/{action}')
def story_turn_action(story_id: str, turn_id: str, action: str, body: dict):
    return story_manager().action(story_id, turn_id, action, body)

@app.post('/api/video/runs/{run_id}/plan-continuation')
def plan_video_continuation(run_id: str, body: dict):
    manager = story_manager()
    run = manager._run(safe_id(run_id))
    if run['status'] != 'succeeded' or not run.get('continuation_source'):
        raise ValueError('Choose a completed take with a saved ending.')
    source = video_manager().snapshot(run['id'])
    story = {'mode': 'studio', 'player_name': '', 'premise': source['story']['text'], 'settings': {'style': ''},
             'branches': {'temporary': manager._lineage(run['id'])}, 'observed_by_run': {}}
    duration = body.get('duration', 5)
    from .stories import settings_for, text
    settings_for({'duration': duration})
    turn = {'branch_id': 'temporary', 'duration': duration, 'message': text(body.get('message', ''), 4000), 'parent_run_id': run['id']}
    if not turn['message']:
        raise ValueError('Describe what happens next or ask the assistant to choose.')
    plan = manager.plan(story, turn, source, video_run_ending_image(run['id']))
    if plan['asset_requests']:
        raise ValueError('This idea needs new images. Use Game to create them automatically, or add references in Studio first.')
    return {'plan': plan, 'source_run_id': run['id']}

@app.get('/api/video/runs/{run_id}/ending')
def video_ending_preview(run_id: str):
    asset = video_run_ending_image(safe_id(run_id))
    return FileResponse(DATA / 'assets' / safe_id(asset['id']) / 'source.png', media_type='image/png')

def scene_video_path(run_id):
    run_id = safe_id(run_id)
    source = cached_run_video(run_id)
    record = video_manager().get(run_id)
    overlap = record.get('overlap_frames')
    if overlap is None:
        # Old records retain their original frame budget; inspect the saved transfer
        # only when playback is requested, never during history polling.
        timing = video_manager()._load(run_id, 'transfer.json')['manifest'].get('mmh3', {})
        overlap = timing.get('overlap_frames', 0) if timing.get('source') else 0
    if not overlap:
        return source
    output = source.parent / f'scene-context-{overlap}.mp4'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('scene:' + run_id, threading.Lock())
    with lock:
        if output.is_file() and output.stat().st_size:
            return output
        temporary = output.with_name(output.stem + '-building.mp4')
        seconds = overlap / 24
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
            '-vf', f'trim=start_frame={overlap},setpts=PTS-STARTPTS', '-af', f'atrim=start={seconds},asetpts=PTS-STARTPTS',
            '-map_metadata', '-1', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-threads', '4',
            '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(temporary)],
            capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('The new-footage preview could not be prepared. The original video is still available.')
        temporary.replace(output)
        return output

@app.get('/api/video/runs/{run_id}/scene')
def video_scene_preview(run_id: str):
    return FileResponse(scene_video_path(run_id), media_type='video/mp4')

@app.get('/api/stories/{story_id}/video')
def story_film(story_id: str):
    story = story_manager().get(story_id)
    clips = story['clips']
    if not clips:
        raise ValueError('Generate a scene before saving the film.')
    if len(clips) > 100:
        raise ValueError('Export a branch with at most 100 clips.')
    digest = hashlib.sha256(json.dumps([r['id'] for r in clips]).encode()).hexdigest()[:20]
    folder = DATA / 'story_films' / safe_id(story_id)
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / (digest + '.mp4')
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('film:' + story_id, threading.Lock())
    with lock:
        if not output.is_file():
            width, height = clips[0]['width'], clips[0]['height']
            normalized = []
            for clip in clips:
                path = folder / (clip['id'] + f'-{width}x{height}.mp4')
                if not path.is_file():
                    source = scene_video_path(clip['id'])
                    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1',
                        '-map_metadata', '-1', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-threads', '4',
                        '-c:a', 'aac', '-ar', '32000', '-ac', '2', str(path)], capture_output=True, timeout=180,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if result.returncode:
                        path.unlink(missing_ok=True)
                        raise ValueError('The film could not be assembled. Your individual clips are saved.')
                normalized.append(path)
            listing = folder / (digest + '.txt')
            listing.write_text('\n'.join("file '" + p.name + "'" for p in normalized), 'utf-8')
            temporary = output.with_name(digest + '-building.mp4')
            result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
                '-i', str(listing), '-c', 'copy', '-map_metadata', '-1', '-movflags', '+faststart', str(temporary)],
                capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('The film export did not finish. Individual clips are still available.')
            temporary.replace(output)
    return FileResponse(output, media_type='video/mp4', filename='H3-Story-' + story_id + '.mp4')

@app.post('/api/gpu/prepare-h3')
def prepare_h3(body: dict):
    return RESOURCES.prepare_h3()

def asset_meta(asset_id):
    path = DATA / 'assets' / safe_id(asset_id) / 'metadata.json'
    if not path.exists():
        raise HTTPException(404, 'This reference file is not in the local library. Add it again or import the portable project.')
    return json.loads(path.read_text(encoding='utf-8'))

def media_probe(path):
    run = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if run.returncode:
        raise ValueError('This media file could not be read.')
    return json.loads(run.stdout)

def store_asset(data, name, content_type=''):
    if not data or len(data) > 64 * 1024 * 1024:
        raise ValueError('Each reference must be nonempty and no larger than 64 MB.')
    asset_id = str(uuid.uuid4())
    folder = DATA / 'assets' / asset_id
    folder.mkdir()
    ext = Path(name).suffix.lower()
    if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp') or content_type.startswith('image/'):
        try:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((4096, 4096))
                image.save(folder / 'source.png')
                width, height = image.size
                image.thumbnail((512, 512))
                image.save(folder / 'thumbnail.jpg', quality=88)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('Use a valid PNG, JPEG or WebP image.') from exc
        meta = {'id': asset_id, 'name': Path(name).stem[:100] or 'Reference', 'media_type': 'image', 'filename': 'source.png',
                'width': width, 'height': height, 'duration': None, 'mime': 'image/png'}
    elif ext in ('.mp4', '.webm', '.mov', '.mp3', '.wav', '.m4a', '.flac', '.ogg'):
        filename = 'source' + ext
        (folder / filename).write_bytes(data)
        probe = media_probe(folder / filename)
        video = next((s for s in probe['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
        audio = next((s for s in probe['streams'] if s.get('codec_type') == 'audio'), None)
        if not video and not audio:
            raise ValueError('No playable audio or video stream was found.')
        duration = float(probe.get('format', {}).get('duration', 0))
        if not 0 < duration <= 120:
            raise ValueError('Reference clips must be at most two minutes in the library; enable only H3-compatible lengths.')
        meta = {'id': asset_id, 'name': Path(name).stem[:100], 'media_type': 'video' if video else 'audio', 'filename': filename,
                'width': video.get('width') if video else None, 'height': video.get('height') if video else None,
                'duration': duration, 'mime': 'video/mp4' if video else 'audio/mpeg'}
    else:
        raise ValueError('Use images, common video clips, or audio files for references.')
    meta['sha256'] = hashlib.sha256((folder / meta['filename']).read_bytes()).hexdigest()
    atomic_json(folder / 'metadata.json', meta)
    return {**meta, 'role': 'reference_' + meta['media_type'], 'semantic_role': 'other', 'enabled': True,
            'description': '', 'observation': '', 'approved_observation': '', 'locked_order': False}

@app.post('/api/assets')
async def upload_asset(file: UploadFile = File(...)):
    data = await file.read(64 * 1024 * 1024 + 1)
    return store_asset(data, file.filename or 'image.png', file.content_type or '')

@app.post('/api/assets/from-data')
def data_asset(body: dict):
    data_url = body.get('data_url', '')
    if not isinstance(data_url, str) or len(data_url) > 24_000_000 or not data_url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
        raise ValueError('The ComfyUI bridge must send a bounded PNG/JPEG/WebP image.')
    try:
        data = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    except ValueError:
        raise ValueError('Invalid image encoding.')
    return store_asset(data, body.get('name', 'Comfy reference.png'), data_url[5:].split(';')[0])

@app.get('/api/assets/{asset_id}/{variant}')
def get_asset(asset_id: str, variant: str):
    meta = asset_meta(asset_id)
    filename = 'thumbnail.jpg' if variant == 'thumbnail' and meta['media_type'] == 'image' else meta['filename']
    return FileResponse(DATA / 'assets' / safe_id(asset_id) / filename)

def image_data(asset_id):
    meta = asset_meta(asset_id)
    if meta['media_type'] != 'image':
        raise ValueError('Vision analysis handles images. Describe audio/video references manually; the selected VLM does not hear them.')
    with Image.open(DATA / 'assets' / asset_id / meta['filename']) as source:
        image = source.convert('RGB')
        image.thumbnail((896, 896))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()

@app.post('/api/compile')
def compile_api(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project', body))
    return compile_project(project)

@app.post('/api/ai/analyse')
def analyse(body: dict):
    asset = body.get('asset', {})
    data_url = image_data(safe_id(asset.get('id')))
    start = time.monotonic()
    observation = RESOURCES.run_ai(SETTINGS['model'], lambda model: client().analyse_image(model, data_url, asset))
    return {'observation': observation, 'seconds': time.monotonic() - start}

@app.post('/api/ai/plan')
def plan(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project'))
    alias_codes = {'invalid_reference_tag', 'duplicate_reference_tag', 'unknown_reference_tag', 'disabled_reference_tag', 'inactive_reference_tag'}
    alias_errors = [i['message'] for i in compile_project(project)['issues'] if i['code'] in alias_codes and i['severity'] == 'error']
    if alias_errors:
        raise ValueError('\n'.join(alias_errors))
    start = time.monotonic()
    planning_project = copy.deepcopy(project)
    observations = []
    def generate(model):
        lm = client()
        if body.get('vision', False):
            images = [a for a in planning_project['assets'] if a.get('enabled') and a.get('media_type') == 'image']
            if len(images) > 12:
                raise ValueError('Select at most twelve images for one small-model planning pass; other images can stay in the library.')
            for index, asset in enumerate(images):
                if asset.get('approved_observation') and not body.get('refresh_vision'):
                    continue
                RESOURCES.stage = f'Reading image {index + 1} of {len(images)}'
                observation = lm.analyse_image(model, image_data(safe_id(asset['id'])), asset)
                asset['observation'] = observation['observation']
                asset['approved_observation'] = observation['observation']
                observations.append({'asset_id': asset['id'], 'name': asset['name'], **observation})
        RESOURCES.stage = 'Building the scene plan'
        if SETTINGS.get('ai_memory_mode') == 'resident_small':
            from .continuation_suggestions import compact_plan
            planning_project.setdefault('simple', {})['directed'] = True
            return compact_plan(lm, model, planning_project, body.get('instructions', ''), body.get('persona', SETTINGS['persona']))
        return lm.propose_plan(model, planning_project, body.get('instructions', ''), body.get('persona', SETTINGS['persona']))
    proposal = RESOURCES.run_ai(SETTINGS['model'], generate)
    candidate = merge_plan(planning_project, proposal)
    compiled = compile_project(candidate)
    return {'candidate': candidate, 'proposal': proposal, 'compiled': compiled, 'observations': observations, 'seconds': time.monotonic() - start,
            'notice': 'Review the suggested image observations and scene plan before applying. Accepting approves the shown observations for prompting. Source story, reference identities and exact dialogue were preserved; scene meaning still needs your review.'}

@app.post('/api/ai/assist')
def assist(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project'))
    if body.get('field') not in ALLOWED_SHOT_FIELDS - {'visible_subject_ids', 'offscreen_subject_ids', 'transition'}:
        raise ValueError('This field is protected from AI replacement.')
    if not any(s.get('id') == body.get('shot_id') for s in project['shots']):
        raise ValueError('The selected shot no longer exists.')
    start = time.monotonic()
    proposal = RESOURCES.run_ai(SETTINGS['model'], lambda model: client().assist(model, project, body['shot_id'], body['field'], body.get('instructions', ''), body.get('persona', SETTINGS['persona'])))
    candidate = merge_assist(project, body['shot_id'], body['field'], proposal['value'])
    return {'candidate': candidate, 'proposal': proposal, 'compiled': compile_project(candidate), 'seconds': time.monotonic() - start}

@app.get('/api/projects/{project_id}/export')
def export_project(project_id: str):
    project = load_project(project_id)
    destination = DATA / 'exports' / f'{safe_id(project_id)}.h3studio.zip'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', json.dumps(project, ensure_ascii=False, indent=2))
        for asset in project['assets']:
            meta = asset_meta(asset['id'])
            folder = DATA / 'assets' / safe_id(asset['id'])
            for name in (meta['filename'], 'metadata.json', 'thumbnail.jpg'):
                if (folder / name).exists():
                    archive.write(folder / name, f'assets/{asset["id"]}/{name}')
    return FileResponse(destination, filename=(project['title'][:80] or 'H3 project') + '.h3studio.zip')

@app.post('/api/projects/import')
async def import_project(file: UploadFile = File(...)):
    data = await file.read(128 * 1024 * 1024 + 1)
    if len(data) > 128 * 1024 * 1024:
        raise ValueError('Portable project is larger than 128 MB.')
    if not (file.filename or '').lower().endswith('.zip'):
        project = check_project(json.loads(data))
        for asset in project['assets']:
            actual = asset_meta(asset['id'])
            if asset['media_type'] != actual['media_type']:
                raise ValueError('A reference media type does not match its stored file.')
        project['id'] = str(uuid.uuid4())
        save_project(project)
        return project
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError('Expanded project archive is too large.')
        project = check_project(json.loads(archive.read('project.json')))
        id_map = {}
        for asset in project['assets']:
            old_id = safe_id(asset['id'])
            meta = json.loads(archive.read(f'assets/{old_id}/metadata.json'))
            filename = meta.get('filename', '')
            if Path(filename).name != filename or '/' in filename or '\\' in filename:
                raise ValueError('Invalid portable asset filename.')
            imported = store_asset(archive.read(f'assets/{old_id}/{filename}'), meta['name'] + Path(filename).suffix, meta.get('mime', ''))
            if asset['media_type'] != imported['media_type']:
                raise ValueError('A portable reference media type does not match its actual file.')
            id_map[old_id] = imported['id']
            asset.update({k: imported[k] for k in ('id', 'filename', 'sha256', 'width', 'height', 'duration', 'mime')})
        for subject in project['subjects']:
            subject['asset_ids'] = [id_map.get(a, a) for a in subject.get('asset_ids', [])]
        project['id'] = str(uuid.uuid4())
        save_project(project)
        return project

@app.get('/{path:path}')
def frontend(path: str):
    candidate = (ROOT / 'dist' / path).resolve()
    base = (ROOT / 'dist').resolve()
    if not candidate.is_relative_to(base):
        raise HTTPException(404)
    if candidate.is_file():
        return FileResponse(candidate)
    index = base / 'index.html'
    if index.exists():
        return FileResponse(index)
    return Response('Build the frontend first: npm run build in frontend.', status_code=503)
