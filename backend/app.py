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
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from .projects import new_project, project_workspace, safe_id, atomic_json, check_project, merge_plan, merge_assist, ALLOWED_SHOT_FIELDS
from .resources import ResourceManager, ResourceError, local_url, gpu_snapshot
from .assistant_profiles import migrate_profiles, merge_profile_settings, resolve_profile, enforce_model_policy, QWEN_MODEL_POLICY, MIN_CONTEXT_TOKENS, MAX_CONTEXT_TOKENS
from .runtime_identity import runtime_fingerprint

ROOT = Path(__file__).resolve().parents[1]
CODE_FINGERPRINT = runtime_fingerprint(ROOT)
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
MOTION_LAB = None
PRODUCTION = None
FILMS = None
VIDEO_FILE_LOCKS = {}
DEFAULT_SETTINGS = {'lm_url': 'http://127.0.0.1:1234/v1', 'model': '', 'context_length': 8192,
                    'comfy_urls': ['http://127.0.0.1:8188', 'http://127.0.0.1:8000', 'http://127.0.0.1:8010'], 'persona': 'universal', 'last_project': '',
                    'last_game_project': '', 'last_video_project': '', 'ai_memory_mode': 'exclusive'}
SETTINGS = {**DEFAULT_SETTINGS}
if (DATA / 'settings.json').exists():
    SETTINGS.update(json.loads((DATA / 'settings.json').read_text(encoding='utf-8')))
# Direct uvicorn starts follow the same family lock as the normal launcher.
# An explicitly empty environment value is reserved for legacy/dev fixtures;
# a missing value always overrides an unlocked legacy settings file.
SETTINGS['assistant_model_policy'] = os.environ.get('H3_STUDIO_MODEL_POLICY', QWEN_MODEL_POLICY)
SETTINGS = migrate_profiles(SETTINGS)
if SETTINGS['assistant_model_policy']:
    # Persist the enforced family and migrated profiles without loading a model.
    atomic_json(DATA / 'settings.json', SETTINGS)

@lru_cache(maxsize=4)
def _assistant_client(base_url, model_policy=''):
    from .lmstudio import LMStudioClient
    return LMStudioClient(base_url=base_url, timeout=180, model_policy=model_policy)


def client():
    # Keep capability knowledge across stages; diagnostics are context-local.
    # A changed endpoint gets a separate client and never inherits its cache.
    return _assistant_client(SETTINGS['lm_url'], SETTINGS.get('assistant_model_policy', ''))

RESOURCES = ResourceManager(lambda: copy.deepcopy(SETTINGS), client, state_path=DATA / 'resource_state.json')
app = FastAPI(title='H3 Prompt Studio', version='1.7.0', docs_url='/api/docs')
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
    with STATE_LOCK:
        if not path.exists():
            raise HTTPException(404, 'Project not found.')
        try:
            project = check_project(json.loads(path.read_text(encoding='utf-8')))
            if safe_id(project['id']) != safe_id(project_id):
                raise ValueError('Project identifier does not match its saved file.')
            return project
        except (OSError, ValueError) as exc:
            raise HTTPException(409, 'This saved project could not be read. Its local file is preserved; restore a portable backup or a history copy.') from exc

def workspace_for_project(project):
    # Generated turns from older releases had a story link but no workspace.
    # Inspect that durable metadata without starting workers or rewriting files.
    story_id = project.get('story_session_id')
    if story_id:
        try:
            story_path = DATA / 'stories' / (safe_id(story_id) + '.json')
            story = json.loads(story_path.read_text(encoding='utf-8'))
            if story.get('mode') == 'game':
                return 'game'
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if project.get('workspace') == 'game':
        return 'game'
    return 'studio' if project.get('film_id') else 'video'


def project_video_summaries():
    summaries, latest = {}, {}
    # Inspect immutable local metadata only; initializing the video manager
    # would resume monitoring workers during a library-only request.
    for path in (DATA / 'video_runs').glob('*/record.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('status') != 'succeeded' or not record.get('video'):
                continue
            project_id = safe_id(record['project_id'])
            run_id = safe_id(record['id'])
            created = float(record.get('created_at', 0))
            summaries[project_id] = summaries.get(project_id, 0) + 1
            if project_id not in latest or created > latest[project_id][0]:
                latest[project_id] = (created, run_id)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    verdicts = {}
    for project_id, (_, run_id) in latest.items():
        try:
            review = json.loads((DATA / 'reviews' / 'run' / run_id / 'review.json').read_text(encoding='utf-8'))
            verdict = review.get('verdict', 'unreviewed')
            if verdict in ('unreviewed', 'approved', 'needs_changes', 'rejected'):
                verdicts[project_id] = verdict
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return summaries, verdicts


def list_projects(workspace='video'):
    project_workspace(workspace)
    values = []
    with STATE_LOCK:
        video_counts, verdicts = project_video_summaries()
        for path in (DATA / 'projects').glob('*.json'):
            try:
                value = check_project(json.loads(path.read_text(encoding='utf-8')))
                if safe_id(value['id']) != safe_id(path.stem) or workspace_for_project(value) != workspace:
                    continue
                simple = value.get('simple', {})
                idea = simple.get('idea', '') if isinstance(simple, dict) else ''
                summary = idea if isinstance(idea, str) and idea else value['story']['text'] or value['shots'][0].get('action', '')
                stamp = path.stat().st_mtime
                values.append({'id': value['id'], 'title': value['title'], 'mode': value['mode'],
                               'workspace': workspace, 'duration': value['duration'], 'updated': stamp,
                               'updated_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                               'reference_count': len(value['assets']), 'shot_count': len(value['shots']),
                               'prompt_summary': summary[:240], 'video_count': video_counts.get(safe_id(value['id']), 0),
                               'last_review_verdict': verdicts.get(safe_id(value['id']))})
            except (OSError, ValueError, TypeError, KeyError):
                continue
    return sorted(values, key=lambda item: item['updated'], reverse=True)

def save_project(project):
    project = copy.deepcopy(check_project(project))
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
        key = {'game': 'last_game_project', 'video': 'last_video_project', 'studio': 'last_project'}[workspace_for_project(project)]
        proposed = {**SETTINGS, key: project['id']}
        atomic_json(DATA / 'settings.json', proposed)
        SETTINGS.update(proposed)
        updated = path.stat().st_mtime
    return {'saved': True, 'id': project['id'], 'updated': updated, 'workspace': workspace_for_project(project)}

@app.get('/api/bootstrap')
def bootstrap():
    from .prompts import PERSONAS
    with STATE_LOCK:
        projects = list_projects('video')
        last = SETTINGS.get('last_video_project') or SETTINGS.get('last_project')
        project = load_project(last) if last and any(p['id'] == last for p in projects) else (load_project(projects[0]['id']) if projects else new_project('video'))
        game_projects = list_projects('game')
        last_game = SETTINGS.get('last_game_project')
        game_project = load_project(last_game) if last_game and any(p['id'] == last_game for p in game_projects) else new_project('game')
        settings = copy.deepcopy(SETTINGS)
    return {'version': app.version, 'workspace_root': str(ROOT), 'code_fingerprint': CODE_FINGERPRINT, 'token': TOKEN, 'resource_token': BRIDGE_TOKEN, 'settings': settings,
            'project': project, 'projects': projects, 'video_project': project, 'video_projects': projects,
            'game_project': game_project, 'personas': PERSONAS}

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
def create_project(workspace: str = 'video', body: dict | None = None):
    body = body or {}
    if set(body) - {'title', 'idea', 'workspace'}:
        raise ValueError('Supply only a project title and an optional idea.')
    project = new_project(body.get('workspace', workspace))
    project['profile'] = 'concise'
    if 'title' in body:
        title = body['title']
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160 or '\x00' in title:
            raise ValueError('Give the project a name between 1 and 160 characters.')
        project['title'] = title.strip()
    if 'idea' in body:
        idea = body['idea']
        if not isinstance(idea, str) or len(idea) > 20000 or '\x00' in idea:
            raise ValueError('Keep the project idea within 20000 characters.')
        project['story']['text'] = idea
    save_project(project)
    return project

@app.get('/api/projects')
def projects_index(workspace: str = 'video'):
    return list_projects(workspace)

@app.get('/api/projects/{project_id}')
def project_get(project_id: str):
    return load_project(project_id)

@app.post('/api/projects/{project_id}/activate')
def project_activate(project_id: str):
    with STATE_LOCK:
        project = load_project(project_id)
        key = {'game': 'last_game_project', 'video': 'last_video_project', 'studio': 'last_project'}[workspace_for_project(project)]
        proposed = {**SETTINGS, key: project['id']}
        atomic_json(DATA / 'settings.json', proposed)
        SETTINGS.update(proposed)
        return project

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
                try:
                    record = load_library_record(path)
                    project = record['project']
                    result[kind].append({k: record[k] for k in ('id', 'name', 'created_at', 'notes', 'rating')} |
                        {'mode': project['mode'], 'duration': project['duration'], 'shot_count': len(project['shots']),
                         'asset_count': len(project['assets']), 'prompt_preview': record.get('prompt', '')[:240]})
                except (OSError, ValueError, TypeError, KeyError):
                    continue
            result[kind].sort(key=lambda value: value['created_at'], reverse=True)
    return result

@app.get('/api/library/{kind}/{record_id}')
def library_get(kind: str, record_id: str):
    path = library_folder(kind) / (safe_id(record_id) + '.json')
    if not path.is_file():
        raise HTTPException(404, 'This saved item no longer exists.')
    with STATE_LOCK:
        try:
            return load_library_record(path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(409, 'This saved library item could not be read. Its local file is preserved.') from exc


def load_library_record(path):
    record = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(record, dict) or safe_id(record.get('id')) != safe_id(path.stem):
        raise ValueError('Invalid saved library identifier.')
    check_project(record.get('project'))
    for key, limit in (('name', 120), ('notes', 5000), ('prompt', 250000), ('created_at', 100)):
        library_text(record, key, limit)
    if not record.get('name', '').strip() or not record.get('created_at'):
        raise ValueError('Invalid saved library name or date.')
    library_rating(record)
    return {**record, 'notes': record.get('notes', ''), 'rating': record.get('rating', 0), 'prompt': record.get('prompt', '')}

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
    if 'assistant_model_policy' in body and body['assistant_model_policy'] != SETTINGS.get('assistant_model_policy', ''):
        raise ValueError('The local Qwen 3.8 27B model lock cannot be changed from the browser.')
    if RESOURCES.lock.locked() and body.get('lm_url', SETTINGS['lm_url']) != SETTINGS['lm_url']:
        raise ResourceError('Wait for AI to finish before changing its connection.')
    allowed = {k: body[k] for k in (*DEFAULT_SETTINGS, 'assistant_profiles') if k in body and k not in ('last_project', 'last_game_project', 'last_video_project')}
    if 'lm_url' in allowed:
        allowed['lm_url'] = local_url(allowed['lm_url'])
    if 'comfy_urls' in allowed:
        if not isinstance(allowed['comfy_urls'], list) or not 1 <= len(allowed['comfy_urls']) <= 4:
            raise ValueError('Choose one to four local ComfyUI instances.')
        allowed['comfy_urls'] = [local_url(u) for u in allowed['comfy_urls']]
    with STATE_LOCK:
        proposed = merge_profile_settings(SETTINGS, allowed)
        proposed.update({key: value for key, value in allowed.items()
                         if key not in ('assistant_profiles', 'model', 'context_length', 'ai_memory_mode')})
        # Save before publishing, so a failed disk write leaves the live choices intact.
        atomic_json(DATA / 'settings.json', proposed)
        SETTINGS.update(proposed)
        return copy.deepcopy(SETTINGS)

@app.get('/api/connections')
def connections():
    result = {'stage': RESOURCES.stage, 'busy': RESOURCES.lock.locked(), 'error': RESOURCES.last_error,
              'gpu': gpu_snapshot(), 'instance_id': RESOURCES.instance_id, 'model': RESOURCES.model_key,
              'ai_memory_mode': SETTINGS['ai_memory_mode'],
              'assistant_profiles': copy.deepcopy(SETTINGS['assistant_profiles']),
              'assistant_model_policy': SETTINGS.get('assistant_model_policy', ''),
              'active_profile': copy.deepcopy(getattr(RESOURCES, 'instance_profile', None)),
              'effective_context_length': None, 'assistant_ready': False}
    try:
        result['lm'] = {'online': True, 'models': client().models(), 'loaded': client().loaded_instances()}
        profile = result['active_profile']
        owned = [item for item in result['lm']['loaded']
                 if item.get('id', item.get('instance_id')) == result['instance_id']
                 and item.get('model_key', item.get('model')) == result['model']]
        if profile and len(owned) == 1 and profile['model'] == result['model']:
            context = RESOURCES._loaded_context(owned[0])
            config = owned[0].get('config', {})
            config = config if isinstance(config, dict) else {}
            kv_gpu = config.get('offload_kv_cache_to_gpu', config.get('offloadKVCacheToGpu'))
            expected_kv_gpu = profile['ai_memory_mode'] == 'exclusive'
            result['assistant_ready'] = (context == profile['context_length'] and type(context) is int
                                         and (kv_gpu is None or kv_gpu is expected_kv_gpu))
            if result['assistant_ready']:
                result['effective_context_length'] = context
    except Exception as exc:
        result['lm'] = {'online': False, 'models': [], 'loaded': [], 'error': str(exc)[:400],
                        'recovery': 'Start LM Studio\u2019s local server, then select Reconnect. Your saved models and contexts are preserved.'}
    if not result['assistant_ready']:
        # Cached ownership/profile is not evidence that the current server still
        # has this exact configured assistant. Never label a foreign instance ready.
        result['active_profile'] = None
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

@app.get('/api/video/runs/{run_id}/live-progress')
def video_run_live_progress(run_id: str):
    return video_manager().live_progress(safe_id(run_id))

@app.get('/api/video/runs/{run_id}/live-progress/events')
def video_run_progress_events(run_id: str):
    manager = video_manager()
    manager.get(safe_id(run_id))
    return StreamingResponse(manager.live_progress_events(run_id), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.get('/api/game/system')
def game_system():
    from .game_director import GAME_ENGINE_SYSTEM
    return {'text': GAME_ENGINE_SYSTEM, 'version': app.version}

@app.get('/api/integrations/upscale')
def upscale_capabilities():
    from .upscale_adapter import capabilities
    return capabilities()

@app.post('/api/integrations/upscale/open')
def upscale_open(body: dict):
    from .upscale_adapter import open_gui
    if set(body) - {'run_id'}:
        raise ValueError('Choose a completed Studio video or open UPSCALE without a file.')
    source = scene_video_path(safe_id(body['run_id'])) if body.get('run_id') else None
    return open_gui(source)

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
    profile = resolve_profile(SETTINGS, workspace_for_project(source))
    started = time.monotonic()
    def generate(model):
        RESOURCES.stage = 'Reading the actual ending and suggesting next scenes'
        ending = video_run_ending_image(job['id'])
        result = suggest_continuations(client(), model, source, duration, image_data(ending['id']), direction,
                                       small_model=profile['ai_memory_mode'] == 'resident_small')
        return {**result, 'ending_asset': ending, 'ending_image_url': '/api/assets/' + ending['id'] + '/file',
                'model': profile['model'], 'source_run_id': job['id']}
    result = RESOURCES.run_ai(profile['model'], generate, profile=profile)
    result['seconds'] = round(time.monotonic() - started, 3)
    return result

@app.post('/api/gpu/prepare-ai')
@app.post('/api/ai/prepare')
def prepare_ai(body: dict):
    profile = resolve_profile(SETTINGS, body.get('workspace', 'studio'))
    if body.get('model'):
        profile = enforce_model_policy(SETTINGS, {**profile, 'model': body['model']})
    return RESOURCES.run_ai(profile['model'], profile=profile)

def asset_manager():
    global ASSET_RUNS
    with STATE_LOCK:
        if ASSET_RUNS is None:
            from .asset_runs import AssetRunManager
            ASSET_RUNS = AssetRunManager(DATA, RESOURCES, lambda: copy.deepcopy(SETTINGS), store_asset)
        return ASSET_RUNS

def production_manager():
    global PRODUCTION
    with STATE_LOCK:
        if PRODUCTION is None:
            from .production import ProductionManager
            PRODUCTION = ProductionManager(DATA, load_project, video_manager, asset_manager)
        return PRODUCTION

def film_manager():
    global FILMS
    with STATE_LOCK:
        if FILMS is None:
            from .films import FilmManager
            def reference_meta(ident):
                meta = asset_meta(ident)
                filename = meta.get('filename', '')
                if not isinstance(filename, str) or Path(filename).name != filename or '/' in filename or '\\' in filename:
                    raise ValueError('Invalid local film reference filename.')
                if not (DATA / 'assets' / safe_id(ident) / filename).is_file():
                    raise ValueError('This film reference file is missing. Add it again before saving or rendering.')
                return meta
            FILMS = FilmManager(DATA, reference_meta)
        return FILMS


@app.get('/api/films')
def film_list():
    return {'films': film_manager().list()}


@app.post('/api/films')
def film_create(body: dict):
    return film_manager().create(body)


@app.get('/api/films/{film_id}')
def film_get(film_id: str):
    return film_manager().get(film_id)


@app.patch('/api/films/{film_id}')
def film_save(film_id: str, body: dict):
    from .films import FilmConflict
    try:
        return film_manager().save(film_id, body)
    except FilmConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post('/api/films/{film_id}/plan')
def film_plan(film_id: str, body: dict):
    from .films import FilmConflict, plan_storyboard
    if set(body) != {'expected_revision'}:
        raise ValueError('Plan the saved film with its expected revision.')
    profile = resolve_profile(SETTINGS, 'studio')
    def generate(film):
        def operation(model):
            RESOURCES.stage = 'Planning the complete film storyboard'
            return plan_storyboard(client(), model, film)
        return RESOURCES.run_ai(profile['model'], operation, profile=profile)
    try:
        return film_manager().plan(film_id, body['expected_revision'], generate)
    except FilmConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post('/api/films/{film_id}/produce')
def film_produce(film_id: str, body: dict):
    from .films import FilmConflict
    if set(body) != {'expected_revision', 'request_id'}:
        raise ValueError('Create the film render queue using its saved revision and a unique request ID.')
    try:
        return film_manager().produce(film_id, body['expected_revision'], body['request_id'], production_manager())
    except FilmConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/api/production')
def production_list():
    return {'batches': production_manager().list()}

@app.post('/api/production')
def production_create(body: dict):
    return production_manager().create(body)

@app.get('/api/production/{batch_id}')
def production_get(batch_id: str):
    return production_manager().get(batch_id)

@app.post('/api/production/{batch_id}/start')
@app.post('/api/production/{batch_id}/resume')
def production_start(batch_id: str):
    return production_manager().start(batch_id)

@app.post('/api/production/{batch_id}/cancel')
def production_cancel(batch_id: str):
    return production_manager().cancel(batch_id)

@app.post('/api/production/{batch_id}/items/{index}/retry')
def production_retry(batch_id: str, index: int):
    return production_manager().retry(batch_id, index)

@app.get('/api/production/{batch_id}/playlist')
def production_playlist(batch_id: str):
    return JSONResponse(production_manager().playlist(batch_id),
                        headers={'Content-Disposition': 'attachment; filename="production-playlist.json"'})

@app.post('/api/production/{batch_id}/export')
def production_export(batch_id: str, body: dict):
    from .production_export import export_production
    if set(body) - {'kind', 'edits', 'normalize_audio'}:
        raise ValueError('Choose a film or individual clips export.')
    manager = production_manager()
    result = export_production(DATA, manager.get(batch_id), scene_video_path,
                               body.get('kind', 'film'), edits=body.get('edits'),
                               normalize_audio=body.get('normalize_audio', False))
    manager.remember_export(batch_id, result)
    return result

@app.get('/api/production/{batch_id}/exports/{export_id}/{filename}')
def production_export_download(batch_id: str, export_id: str, filename: str):
    from .production_export import export_file
    path = export_file(DATA, batch_id, export_id, filename)
    return FileResponse(path, filename=path.name)

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
def stories_list(mode: str | None = None):
    if mode is not None:
        project_workspace(mode)
    return {'stories': [story for story in story_manager().list() if mode is None or story['mode'] == mode]}

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

@app.get('/api/stories/{story_id}/actions')
def story_available_actions(story_id: str, target_id: str | None = None):
    return story_manager().available_actions(story_id, target_id)

@app.post('/api/stories/{story_id}/scene-player')
def story_scene_player(story_id: str, body: dict):
    return story_manager().bind_scene_player(story_id, body)

@app.post('/api/stories/{story_id}/scene-inspection')
def story_scene_inspection(story_id: str, body: dict):
    return story_manager().refresh_scene(story_id, body)

@app.get('/api/video/runs/{run_id}/receipt')
def video_receipt(run_id: str):
    manager = video_manager()
    run = manager.get(safe_id(run_id))
    transfer = manager._load(run_id, 'transfer.json')
    manifest = transfer['manifest']
    graph = transfer['prompt']
    record = manager.records[run_id]
    return {'run_id': run_id, 'request_id': record['request_id'], 'comfy_prompt_id': record.get('prompt_id'),
            'graph_hash': hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
            'manifest': manifest, 'graph': graph, 'status': run['status'], 'video_url': run.get('video_url'),
            'settings': {k: manifest.get(k) for k in ('seed', 'steps', 'resolution', 'width', 'height', 'frames', 'loras')}}

@app.post('/api/stories/{story_id}/preview')
def story_preview(story_id: str, body: dict):
    return story_manager().preview(story_id, body)

@app.get('/api/assistant/requests')
def assistant_pending():
    return story_manager().supervised_requests()

def motion_lab_manager():
    global MOTION_LAB
    from .motion_lab import MotionLabManager
    with STATE_LOCK:
        if MOTION_LAB is None:
            MOTION_LAB = MotionLabManager(DATA, video_manager)
        return MOTION_LAB

@app.get('/api/motion-lab/recipes')
def motion_recipes():
    from .motion_lab import recipes
    return {'recipes': recipes()}

@app.post('/api/motion-lab')
def motion_create(body: dict):
    return motion_lab_manager().create(safe_id(body.get('request_id')), check_project(body.get('project')),
        body.get('settings', {}), body.get('recipe_ids', []), body.get('seeds', []))

@app.get('/api/motion-lab/{comparison_id}')
def motion_get(comparison_id: str):
    return motion_lab_manager().get(safe_id(comparison_id))

@app.post('/api/motion-lab/{comparison_id}/advance')
def motion_advance(comparison_id: str, body: dict):
    return motion_lab_manager().advance(safe_id(comparison_id))

@app.post('/api/motion-lab/{comparison_id}/pause')
def motion_pause(comparison_id: str, body: dict):
    if type(body.get('paused')) is not bool:
        raise ValueError('Paused must be true or false.')
    return motion_lab_manager().set_paused(safe_id(comparison_id), body['paused'])

@app.post('/api/motion-lab/{comparison_id}/rating')
def motion_rating(comparison_id: str, body: dict):
    return motion_lab_manager().rate(safe_id(comparison_id), safe_id(body.get('request_id')), body.get('ratings', {}), body.get('notes', ''))

@app.post('/api/assistant/requests/{request_id}/complete')
def assistant_complete(request_id: str, body: dict):
    return story_manager().complete_supervised(safe_id(request_id), body)

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
    turn['assistant_profile'] = resolve_profile(SETTINGS, workspace_for_project(source))
    turn['assistant_model'] = turn['assistant_profile']['model']
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

def review_store():
    # One process-wide store shares its locks across routes and request threads.
    from .video_review import VideoReviewStore
    with STATE_LOCK:
        if not hasattr(review_store, 'store') or review_store.store.root.parent != DATA:
            review_store.store = VideoReviewStore(DATA)
        return review_store.store


def review_source(kind, ident, *, include_video=False):
    ident = safe_id(ident)
    if kind == 'run':
        manager = video_manager()
        record = manager.get(ident)
        if record.get('status') != 'succeeded':
            raise ValueError('Review a completed take with available video.')
        project = manager.snapshot(ident)
        request = manager._load(ident, 'request.json')
        source = {'kind': kind, 'id': ident, 'name': record.get('title') or project['title'],
                  'source_prompt': request.get('prompt', ''), 'workspace': workspace_for_project(project),
                  'video_url': f'/api/video/runs/{ident}/scene'}
        if include_video:
            source['path'] = scene_video_path(ident)
        return source
    if kind != 'asset':
        raise ValueError('Choose a generated take or an imported video.')
    meta = asset_meta(ident)
    if meta.get('media_type') != 'video':
        raise ValueError('Select an imported video for review.')
    filename = meta.get('filename')
    if not isinstance(filename, str) or Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('Invalid local video filename.')
    source = {'kind': kind, 'id': ident, 'name': meta.get('name', 'Imported video'),
              'source_prompt': '', 'workspace': 'studio', 'video_url': f'/api/assets/{ident}/file',
              **{key: meta.get(key) for key in ('duration', 'width', 'height')}}
    if include_video:
        source['path'] = DATA / 'assets' / ident / filename
    return source


@app.get('/api/reviews/library')
def review_library():
    from .video_review import CRITERIA, VERDICTS
    videos = []
    for metadata in (DATA / 'assets').glob('*/metadata.json'):
        try:
            meta = json.loads(metadata.read_text(encoding='utf-8'))
            if meta.get('media_type') != 'video':
                continue
            source = review_source('asset', metadata.parent.name)
            videos.append({**source, 'review': review_store().get('asset', source['id']),
                           'imported_at': metadata.stat().st_mtime})
        except (OSError, ValueError, TypeError, KeyError, HTTPException):
            continue
    return {'videos': sorted(videos, key=lambda item: item['imported_at'], reverse=True),
            'criteria': list(CRITERIA), 'verdicts': list(VERDICTS)}


@app.post('/api/reviews/import')
async def review_import(file: UploadFile = File(...)):
    from starlette.concurrency import run_in_threadpool
    from .video_review import probe_video
    name = file.filename or 'video.mp4'
    extension = Path(name).suffix.lower()
    if extension not in ('.mp4', '.webm', '.mov'):
        raise ValueError('Import an MP4, WebM or MOV video for local review.')
    ident = str(uuid.uuid4())
    folder = DATA / 'assets' / ident
    folder.mkdir()
    path = folder / ('source' + extension)
    completed = False
    try:
        size, digest = 0, hashlib.sha256()
        with path.open('wb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 128 * 1024 * 1024:
                    raise ValueError('Review videos must be no larger than 128 MB.')
                digest.update(chunk)
                output.write(chunk)
        media = await run_in_threadpool(probe_video, path)
        meta = {'id': ident, 'name': Path(name).stem[:100] or 'Imported video', 'media_type': 'video',
                'filename': path.name, 'mime': 'video/webm' if extension == '.webm' else 'video/mp4',
                'duration': media['duration'], 'width': media['width'], 'height': media['height'],
                'sha256': digest.hexdigest(), 'review_only': True}
        atomic_json(folder / 'metadata.json', meta)
        completed = True
        return {**meta, 'role': 'reference_video', 'semantic_role': 'other', 'enabled': False,
                'description': '', 'observation': '', 'approved_observation': '', 'locked_order': False}
    finally:
        if not completed:
            # This folder was just created under DATA/assets with a fresh UUID.
            # Remove only known files; never recursively remove a supplied path.
            path.unlink(missing_ok=True)
            (folder / 'metadata.json').unlink(missing_ok=True)
            (folder / 'metadata.tmp').unlink(missing_ok=True)
            folder.rmdir()


@app.get('/api/reviews/{kind}/{ident}')
def review_get(kind: str, ident: str):
    source = review_source(kind, ident)
    return {**review_store().get(kind, ident, source['source_prompt']), 'video_url': source['video_url']}


@app.put('/api/reviews/{kind}/{ident}')
def review_save(kind: str, ident: str, body: dict):
    source = review_source(kind, ident)
    return review_store().save(kind, ident, body, source['source_prompt'])


@app.post('/api/reviews/{kind}/{ident}/analyze')
def review_analyze(kind: str, ident: str, body: dict):
    from .video_review import analyze_frames, bounded_text, sample_video
    if set(body) - {'source_prompt', 'intent', 'sample_count'}:
        raise ValueError('Supply only source_prompt, intent and sample_count for video review.')
    source = review_source(kind, ident)
    store = review_store()
    previous = store.get(kind, ident, source['source_prompt'])
    prompt = bounded_text(body.get('source_prompt', previous['source_prompt']), 'source_prompt', 16000)
    intent = bounded_text(body.get('intent', ''), 'intent', 2000)
    count = body.get('sample_count', 6)
    if type(count) is not int or not 4 <= count <= 8:
        raise ValueError('Choose between four and eight review frames.')
    lock = store.analysis_lock(kind, ident)
    if not lock.acquire(blocking=False):
        raise ResourceError('This video already has a review in progress. Wait for it to finish.')
    samples = []
    started = time.monotonic()
    try:
        # Resolve/decode the displayed scene, including its continuation trim.
        path = review_source(kind, ident, include_video=True)['path']
        media, samples = sample_video(path, store.folder(kind, ident), kind, ident, count)
        profile = resolve_profile(SETTINGS, source['workspace'])
        def generate(model):
            RESOURCES.stage = 'Reviewing sampled video frames'
            result = analyze_frames(client(), model, media, samples, prompt, intent, previous['notes'])
            result.update(model_key=profile['model'], source_prompt=prompt, intent=intent,
                          seconds=round(time.monotonic() - started, 3))
            return result
        result = RESOURCES.run_ai(profile['model'], generate, profile=profile)
        return store.record_ai(kind, ident, result, prompt, previous['updated'])
    except Exception:
        for sample in samples:
            (store.folder(kind, ident) / 'frames' / sample['url'].rsplit('/', 1)[-1]).unlink(missing_ok=True)
        raise
    finally:
        lock.release()


@app.get('/api/reviews/{kind}/{ident}/frames/{filename}')
def review_frame(kind: str, ident: str, filename: str):
    review_source(kind, ident)
    try:
        store = review_store()
        with store.lock:
            path = store.frame_path(kind, ident, filename)
            if not path.is_file():
                raise FileNotFoundError()
            # Frames are <=2 MB. Read them while cleanup holds the same lock,
            # so a concurrent reanalysis cannot remove a deferred response file.
            return Response(path.read_bytes(), media_type='image/jpeg')
    except FileNotFoundError:
        raise HTTPException(404, 'Review frame not found.')


@app.get('/api/reviews/{kind}/{ident}/prompt')
def review_prompt(kind: str, ident: str):
    review_source(kind, ident)
    review = review_store().get(kind, ident)
    prompt = (review.get('ai') or {}).get('improved_prompt')
    if not prompt:
        raise HTTPException(404, 'Analyze this video to create a repair prompt first.')
    return Response(prompt, media_type='text/plain; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="H3-review-{safe_id(ident)}.txt"'})

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
    try:
        return _write_asset(data, name, content_type, asset_id, folder)
    except BaseException:
        # This fresh UUID directory belongs only to this rejected upload.
        # Existing library assets are never visited or removed.
        for created_file in folder.iterdir():
            created_file.unlink()
        folder.rmdir()
        raise


def _write_asset(data, name, content_type, asset_id, folder):
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
        from .audio_tools import measured_duration
        duration = measured_duration(folder / filename, probe)
        if not 0 < duration <= 120:
            raise ValueError('Reference clips must be at most two minutes in the library; enable only H3-compatible lengths.')
        meta = {'id': asset_id, 'name': Path(name).stem[:100], 'media_type': 'video' if video else 'audio', 'filename': filename,
                'width': video.get('width') if video else None, 'height': video.get('height') if video else None,
                'duration': duration, 'mime': (('video/webm' if video else 'audio/webm') if ext == '.webm' else
                    ('video/mp4' if video else {'.wav': 'audio/wav', '.flac': 'audio/flac', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4'}.get(ext, 'audio/mpeg')))}
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

@app.get('/api/audio/capabilities')
def audio_capabilities():
    from .audio_tools import transcription_capabilities
    return transcription_capabilities()

@app.post('/api/audio/transcribe')
def audio_transcribe(body: dict):
    from .audio_tools import transcribe
    asset_id = safe_id(body.get('asset_id'))
    meta = asset_meta(asset_id)
    if meta['media_type'] not in ('audio', 'video'):
        raise ValueError('Choose a recording or audio clip to transcribe.')
    return transcribe(DATA / 'assets' / asset_id / meta['filename'], body.get('options', {}))

@app.post('/api/video/runs/{run_id}/soundtrack')
def video_soundtrack(run_id: str, body: dict):
    from .audio_tools import mix_soundtrack
    request_id = safe_id(body.get('request_id'))
    source = scene_video_path(safe_id(run_id))
    supplied = body.get('tracks', [])
    if not isinstance(supplied, list) or len(supplied) > 8:
        raise ValueError('Choose at most eight soundtrack layers.')
    tracks = []
    for track in supplied:
        aid = safe_id(track.get('asset_id'))
        meta = asset_meta(aid)
        if meta['media_type'] not in ('audio', 'video'):
            raise ValueError('Soundtrack layers need audio or video files.')
        tracks.append({**{k: v for k, v in track.items() if k in ('start_seconds', 'end_seconds', 'offset_seconds', 'gain', 'fade_in', 'fade_out', 'duck')},
                       'path': str(DATA / 'assets' / aid / meta['filename'])})
    folder = DATA / 'soundtracks' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    request_path = folder / (request_id + '.json')
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('soundtrack:' + request_id, threading.Lock())
    with lock:
        if request_path.exists() and json.loads(request_path.read_text('utf-8')) != supplied:
            raise ValueError('This soundtrack request already belongs to different layers.')
        atomic_json(request_path, supplied)
        target = folder / (request_id + '.mp4')
        result = mix_soundtrack(source, tracks, target) if not target.exists() else {'original_audio_preserved': True}
    return {**{k: v for k, v in result.items() if k != 'path'}, 'video_url': f'/api/video/runs/{run_id}/soundtrack/{request_id}', 'request_id': request_id}

@app.get('/api/video/runs/{run_id}/soundtrack/{request_id}')
def soundtrack_playback(run_id: str, request_id: str):
    path = DATA / 'soundtracks' / safe_id(run_id) / (safe_id(request_id) + '.mp4')
    if not path.is_file():
        raise HTTPException(404, 'The soundtrack is not ready.')
    return FileResponse(path, media_type='video/mp4')

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
    if body.get('project'):
        workspace = workspace_for_project(check_project(body['project']))
    elif body.get('project_id'):
        workspace = workspace_for_project(load_project(body['project_id']))
    else:
        workspace = body.get('workspace', 'studio')
    profile = resolve_profile(SETTINGS, workspace)
    start = time.monotonic()
    observation = RESOURCES.run_ai(profile['model'], lambda model: client().analyse_image(model, data_url, asset), profile=profile)
    return {'observation': observation, 'seconds': time.monotonic() - start}

@app.post('/api/ai/plan')
def plan(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project'))
    profile = resolve_profile(SETTINGS, workspace_for_project(project))
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
        if profile['ai_memory_mode'] == 'resident_small':
            from .continuation_suggestions import compact_plan
            planning_project.setdefault('simple', {})['directed'] = True
            return compact_plan(lm, model, planning_project, body.get('instructions', ''), body.get('persona', SETTINGS['persona']))
        return lm.propose_plan(model, planning_project, body.get('instructions', ''), body.get('persona', SETTINGS['persona']))
    proposal = RESOURCES.run_ai(profile['model'], generate, profile=profile)
    candidate = merge_plan(planning_project, proposal)
    compiled = compile_project(candidate)
    return {'candidate': candidate, 'proposal': proposal, 'compiled': compiled, 'observations': observations, 'seconds': time.monotonic() - start,
            'notice': 'Review the suggested image observations and scene plan before applying. Accepting approves the shown observations for prompting. Source story, reference identities and exact dialogue were preserved; scene meaning still needs your review.'}

@app.post('/api/ai/assist')
def assist(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project'))
    profile = resolve_profile(SETTINGS, workspace_for_project(project))
    if body.get('field') not in ALLOWED_SHOT_FIELDS - {'visible_subject_ids', 'offscreen_subject_ids', 'transition'}:
        raise ValueError('This field is protected from AI replacement.')
    if not any(s.get('id') == body.get('shot_id') for s in project['shots']):
        raise ValueError('The selected shot no longer exists.')
    start = time.monotonic()
    proposal = RESOURCES.run_ai(profile['model'], lambda model: client().assist(model, project, body['shot_id'], body['field'], body.get('instructions', ''), body.get('persona', SETTINGS['persona'])), profile=profile)
    candidate = merge_assist(project, body['shot_id'], body['field'], proposal['value'])
    return {'candidate': candidate, 'proposal': proposal, 'compiled': compile_project(candidate), 'seconds': time.monotonic() - start}

@app.get('/api/projects/{project_id}/export')
def export_project(project_id: str):
    project = load_project(project_id)
    project['workspace'] = workspace_for_project(project)
    destination = DATA / 'exports' / f'{safe_id(project_id)}-{uuid.uuid4().hex}.h3studio.zip'
    temporary = destination.with_suffix('.building')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('project.json', json.dumps(project, ensure_ascii=False, indent=2))
            seen = set()
            for asset in project['assets']:
                ident = safe_id(asset['id'])
                if ident in seen:
                    continue
                seen.add(ident)
                meta = asset_meta(ident)
                filename = meta.get('filename', '')
                if not isinstance(filename, str) or not filename or Path(filename).name != filename or '/' in filename or '\\' in filename:
                    raise ValueError('Invalid local reference filename. The export was not saved.')
                folder = DATA / 'assets' / ident
                if not (folder / filename).is_file():
                    raise ValueError('A reference file is missing. Restore it before making a portable backup.')
                for name in (filename, 'metadata.json', 'thumbnail.jpg'):
                    if (folder / name).is_file():
                        archive.write(folder / name, f'assets/{ident}/{name}')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return FileResponse(destination, filename=(project['title'][:80] or 'H3 project') + '.h3studio.zip')

@app.post('/api/projects/import')
async def import_project(file: UploadFile = File(...), workspace: str | None = None):
    if workspace is not None:
        project_workspace(workspace)

    def save_import(project):
        project['workspace'] = workspace or workspace_for_project(project)
        project['id'] = str(uuid.uuid4())
        # A portable import is an independent copy, never another editor for
        # the original story session and its current branch.
        project.pop('story_session_id', None)
        save_project(project)
        return project

    data = await file.read(128 * 1024 * 1024 + 1)
    if len(data) > 128 * 1024 * 1024:
        raise ValueError('Portable project is larger than 128 MB.')
    if not (file.filename or '').lower().endswith('.zip'):
        project = check_project(json.loads(data))
        for asset in project['assets']:
            actual = asset_meta(asset['id'])
            if asset['media_type'] != actual['media_type']:
                raise ValueError('A reference media type does not match its stored file.')
        return save_import(project)
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
        return save_import(project)

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
