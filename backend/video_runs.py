"""Persistent, single-admission video jobs. Only explicit submissions can queue.

Project/graph snapshots are private local files. Public records contain result
metadata only. An uncertain POST is never automatically repeated.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

import httpx

from .comfy_transfer import _materialize_ui_defaults, _ui_workflow, _validate_graph
from .mmh3_transfer import repair_studio_controls
from .projects import atomic_json
from .resources import ResourceError, local_url


class VideoRunError(ValueError):
    pass


ACTIVE = frozenset({'preparing', 'queued', 'running', 'uncertain'})
MAX_SEED = 2**53 - 1
MAX_VIDEO_BYTES = 2 * 1024**3


def _id(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise VideoRunError('Use a valid video request identifier.') from exc


def _digest(value):
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(serialized.encode('utf-8')) > 4 * 1024**2:
        raise VideoRunError('This video project is too large. Use library references instead of embedded image data.')
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _seed(value):
    if type(value) is not int or not 0 <= value <= MAX_SEED:
        raise VideoRunError('The seed must be a whole number between 0 and 9007199254740991.')
    return value


def _relative(value, suffix):
    if not isinstance(value, str) or '\\' in value or ':' in value or '\x00' in value:
        raise VideoRunError('ComfyUI returned an invalid output file.')
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')) or
            path.suffix.lower() != suffix):
        raise VideoRunError('ComfyUI returned an invalid output file.')
    return value


def _matches_prefix(relative, prefix):
    return (relative.startswith(prefix) and
            relative[len(prefix):len(prefix) + 1] in ('_', '.'))


def _error_text(value, limit=450):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    value = re.sub(r'[A-Za-z0-9+/_=-]{120,}', '[long value omitted]', value)
    value = ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', value).split())
    return value[:limit] + ('…' if len(value) > limit else '')


def _submission_error(reply, graph, status_code):
    """Extract messages, never received values/configs or whole node inputs."""
    messages = []
    node_errors = reply.get('node_errors') if isinstance(reply, dict) else None
    if isinstance(node_errors, dict):
        for ident, details in list(node_errors.items())[:3]:
            if not isinstance(details, dict):
                continue
            node = _error_text(graph.get(str(ident), {}).get('class_type') or details.get('class_type'), 80)
            errors = details.get('errors', [])
            for error in errors[:2] if isinstance(errors, list) else []:
                if not isinstance(error, dict):
                    continue
                message = _error_text(error.get('message'), 200)
                if message:
                    messages.append((node + ': ' if node else '') + message)
    if not messages and isinstance(reply, dict) and isinstance(reply.get('error'), dict):
        message = _error_text(reply['error'].get('message'), 350)
        if message:
            messages.append(message)
    return _error_text(' '.join(messages), 700) if messages else f'ComfyUI rejected the workflow (HTTP {status_code}). Review the selected models and generation settings.'


def _history_error(history, prompt_id):
    messages = history.get('status', {}).get('messages', [])
    for item in reversed(messages[-30:] if isinstance(messages, list) else []):
        if not (isinstance(item, list) and len(item) == 2 and item[0] == 'execution_error' and isinstance(item[1], dict)):
            continue
        error = item[1]
        if error.get('prompt_id') not in (None, prompt_id):
            continue
        node = _error_text(error.get('node_type'), 80)
        message = _error_text(error.get('exception_message'))
        if message:
            result = (node + ': ' if node else '') + message
            if 'out of memory' in message.lower():
                result += ' Try a lower resolution or a shorter clip.'
            return _error_text(result, 650)
    return 'ComfyUI could not finish this video and returned no error message. Review the generation settings and try a new request.'


class VideoRunManager:
    def __init__(self, data_dir, prepare, resources, *, client_factory=None,
                 poll_interval=1.5, start_workers=True, monitor_timeout=21600):
        self.directory = (Path(data_dir) / 'video_runs').resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prepare, self.resources = prepare, resources
        self.client_factory = client_factory or (lambda: httpx.Client(trust_env=False, follow_redirects=False, timeout=15))
        self.poll_interval, self.monitor_timeout = poll_interval, monitor_timeout
        self.start_workers = start_workers
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.records, self.workers = {}, {}
        for file in self.directory.glob('*/record.json'):
            try:
                record = json.loads(file.read_text(encoding='utf-8'))
                ident = _id(record['id'])
                if file.parent != self.directory / ident:
                    continue
                if record['status'] == 'preparing':
                    record.update(status='uncertain' if record.get('submission_intent') else 'failed',
                                  stage='Check submission' if record.get('submission_intent') else 'Preparation stopped',
                                  error='Studio restarted during this request. It was not automatically submitted again.')
                    atomic_json(file, record)
                self.records[ident] = record
            except (ValueError, KeyError, OSError):
                continue
        if start_workers:
            for ident, record in self.records.items():
                if record['status'] in ('queued', 'running', 'uncertain') and record.get('prompt_id'):
                    self._spawn(ident, monitor_only=True)

    def _folder(self, ident):
        folder = (self.directory / _id(ident)).resolve()
        if not folder.is_relative_to(self.directory):
            raise VideoRunError('The video record is outside Studio’s result folder.')
        return folder

    def _load(self, ident, name):
        try:
            return json.loads((self._folder(ident) / name).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise VideoRunError('This video’s saved project or workflow is unavailable.') from exc

    def _record(self, ident):
        try:
            return self.records[_id(ident)]
        except KeyError as exc:
            raise VideoRunError('This video run was not found.') from exc

    def _save(self, record, **changes):
        with self.lock:
            record.update(changes, updated_at=time.time())
            atomic_json(self._folder(record['id']) / 'record.json',
                        {key: value for key, value in record.items() if key not in ('processing', 'refreshing')})

    def _public(self, record):
        fields = ('id', 'request_id', 'project_id', 'status', 'stage', 'error', 'warning', 'seed', 'duration',
                  'width', 'height', 'resolution', 'steps', 'frames', 'created_at', 'parent_run_id', 'continuation_source',
                  'has_snapshot', 'server_execution_seconds')
        public = {key: copy.deepcopy(record.get(key)) for key in fields}
        public['title'] = record.get('title', '')
        public['favorite'] = record.get('favorite', False)
        finished = record.get('finished_at')
        public['elapsed_seconds'] = max(0, (finished or time.time()) - record['created_at'])
        available = record['status'] == 'succeeded' and bool(record.get('video'))
        public['video_url'] = f'/api/video/runs/{record["id"]}/video' if available else None
        public['download_url'] = public['video_url'] + '?download=1' if available else None
        public['operation'] = record.get('kind', 'generate')
        public['can_reroll'] = available and record.get('kind') != 'combine'
        public['can_continue'] = available and bool(record.get('continuation_source'))
        if available and record.get('kind') == 'combine':
            last_clip = self.records.get(record.get('parent_run_id'))
            if (last_clip and last_clip['status'] == 'succeeded' and last_clip.get('video') and
                    last_clip.get('kind') != 'combine' and last_clip.get('continuation_source') and
                    last_clip.get('project_id') == record.get('project_id') and
                    last_clip.get('comfy_url') == record.get('comfy_url')):
                public['continue_from_run_id'] = last_clip['id']
                public['can_continue'] = True
        public['can_combine'] = available and bool(record.get('continuation_input_source'))
        return public

    def _admit(self, request_id, request_digest, project, *, parent=None, kind='generate', seed=None, prompt=''):
        ident = _id(request_id)
        with self.lock:
            existing = self.records.get(ident)
            if existing:
                if existing.get('request_digest') != request_digest:
                    raise VideoRunError('This request identifier already belongs to a different video action.')
                return existing, False
            if any(item['status'] in ACTIVE for item in self.records.values()):
                raise VideoRunError('A video request is already active. Wait for it to finish or resolve its submission status.')
            record = {'id': ident, 'request_id': ident, 'request_digest': request_digest,
                      'project_id': _id(project['id']), 'status': 'preparing', 'stage': 'Preparing video',
                      'error': None, 'warning': None, 'created_at': time.time(), 'parent_run_id': parent,
                      'title': '', 'favorite': False,
                      'has_snapshot': True, 'kind': kind, 'seed': seed,
                      'duration': project.get('duration'), 'width': None, 'height': None,
                      'continuation_source': None, 'client_id': 'h3studio-video-' + ident,
                      'submission_intent': False, 'prompt_id': None}
            folder = self._folder(ident)
            folder.mkdir(parents=True, exist_ok=False)
            atomic_json(folder / 'project.json', project)
            atomic_json(folder / 'request.json', {'prompt': prompt})
            self.records[ident] = record
            self._save(record)
            return record, True

    def submit(self, request_id, project, prompt, *, parent_run_id=None):
        if not isinstance(project, dict) or not isinstance(prompt, str) or not prompt.strip():
            raise VideoRunError('Write and review the current prompt before generating.')
        snapshot = copy.deepcopy(project)
        render = snapshot.get('comfy_render', {})
        if not isinstance(render, dict):
            raise VideoRunError('The video settings are invalid.')
        seed = _seed(render.get('seed', 9072026))
        parent = _id(parent_run_id) if parent_run_id is not None else None
        digest = _digest({'kind': 'continue' if parent else 'generate', 'project': snapshot, 'prompt': prompt, 'parent': parent})
        with self.lock:
            if parent:
                source = self._record(parent)
                if (source['status'] != 'succeeded' or not source.get('continuation_source') or
                        render.get('continuation_source') != source['continuation_source']):
                    raise VideoRunError('Continue this video must use the exact saved state of the selected finished take.')
            record, fresh = self._admit(request_id, digest, snapshot, parent=parent,
                                        kind='continue' if parent else 'generate', seed=seed, prompt=prompt)
        if fresh and self.start_workers:
            self._spawn(record['id'])
        return self._public(record)

    def reroll(self, request_id, source_run_id, seed=None):
        parent = _id(source_run_id)
        digest = _digest({'kind': 'reroll', 'parent': parent, 'requested_seed': seed})
        with self.lock:
            existing = self.records.get(_id(request_id))
            if existing:
                if existing.get('request_digest') != digest:
                    raise VideoRunError('This request identifier already belongs to a different video action.')
                return self._public(existing)
            source = self._record(parent)
            if source['status'] != 'succeeded' or source.get('kind') == 'combine':
                raise VideoRunError('Finish a video successfully before trying another seed.')
            reserved = [source['seed']] + [r['seed'] for r in self.records.values()
                                           if r.get('parent_run_id') == parent and type(r.get('seed')) is int]
            chosen = _seed(max(reserved) + 1 if seed is None else seed)
            if chosen in reserved:
                raise VideoRunError('Choose a different seed for this variation.')
            project = self._load(parent, 'project.json')
            project.setdefault('comfy_render', {})['seed'] = chosen
            record, fresh = self._admit(request_id, digest, project, parent=parent, kind='reroll', seed=chosen)
        if fresh and self.start_workers:
            self._spawn(record['id'])
        return self._public(record)

    def _chain(self, source_run_id):
        entries, seen = [], set()
        current = self._record(source_run_id)
        while True:
            if current['id'] in seen or len(seen) >= 40:
                raise VideoRunError('This video chain is circular or exceeds 40 takes.')
            seen.add(current['id'])
            if current['status'] != 'succeeded' or not current.get('continuation_source'):
                raise VideoRunError('Every combined take needs a verified successful continuation state.')
            transfer = self._load(current['id'], 'transfer.json')
            entries.append({'run': copy.deepcopy(current), 'transfer': transfer})
            source = transfer['manifest'].get('mmh3', {}).get('source')
            if not source:
                break
            source = source.removeprefix('output::')
            parents = [record for record in self.records.values()
                       if record.get('continuation_source') == source and record.get('comfy_url') == current.get('comfy_url')
                       and record['status'] == 'succeeded']
            if len(parents) != 1:
                raise VideoRunError('The earlier take in this continuation chain is not uniquely recorded in Studio.')
            current = parents[0]
        if len(entries) < 2:
            raise VideoRunError('Continue a video first, then combine the linked takes.')
        return list(reversed(entries))

    def combine(self, request_id, source_run_id):
        source_id = _id(source_run_id)
        digest = _digest({'kind': 'combine', 'source': source_id})
        with self.lock:
            existing = self.records.get(_id(request_id))
            if existing:
                if existing.get('request_digest') != digest:
                    raise VideoRunError('This request identifier already belongs to a different video action.')
                return self._public(existing)
            self._chain(source_id)
            source = self._record(source_id)
            record, fresh = self._admit(request_id, digest, self._load(source_id, 'project.json'),
                                        parent=source_id, kind='combine', seed=None)
        if fresh and self.start_workers:
            self._spawn(record['id'])
        return self._public(record)

    def get(self, run_id):
        with self.lock:
            record = self._record(run_id)
            return self._public(record)

    def update_metadata(self, run_id, changes):
        """Name or bookmark an owned take without touching its render state."""
        if not isinstance(changes, dict) or not changes or set(changes) - {'title', 'favorite'}:
            raise VideoRunError('Update only this take’s title or favorite marker.')
        updates = {}
        if 'title' in changes:
            title = changes['title']
            if not isinstance(title, str) or len(title) > 80 or re.search(r'[\x00-\x1f\x7f]', title):
                raise VideoRunError('Use a single-line take title of at most 80 characters.')
            updates['title'] = title.strip()
        if 'favorite' in changes:
            if type(changes['favorite']) is not bool:
                raise VideoRunError('The favorite marker must be true or false.')
            updates['favorite'] = changes['favorite']
        with self.lock:
            # Merge into the current shared record while holding the same lock
            # as progress updates, so a rename cannot restore an older state.
            record = self._record(run_id)
            self._save(record, **updates)
            return self._public(record)

    def list(self, project_id=None):
        project_id = _id(project_id) if project_id is not None else None
        with self.lock:
            if self.start_workers:
                # A list-only GUI also resumes read-only monitoring after an
                # app restart or a bounded monitor timeout. Never re-prepare.
                for record in self.records.values():
                    if record['status'] in ('queued', 'running', 'uncertain') and record.get('comfy_url'):
                        self._spawn(record['id'], monitor_only=True)
            return [self._public(record) for record in sorted(self.records.values(), key=lambda r: r['created_at'], reverse=True)
                    if project_id is None or record['project_id'] == project_id]

    def snapshot(self, run_id):
        with self.lock:
            self._record(run_id)
            return self._load(run_id, 'project.json')

    def media(self, run_id):
        with self.lock:
            record = self._record(run_id)
            if record['status'] != 'succeeded' or not record.get('video'):
                raise VideoRunError('This video is not available yet.')
            return {'comfy_url': local_url(record['comfy_url']), **copy.deepcopy(record['video']),
                    'mime_type': 'video/mp4', 'download_name': f'H3-{record["id"][:8]}-' +
                    ('combined.mp4' if record.get('kind') == 'combine' else f'seed-{record["seed"]}.mp4'),
                    'max_bytes': MAX_VIDEO_BYTES}

    def resolve_missing(self, run_id):
        """Explicit GUI recovery: check only this job, then release a lost request.

        This never interrupts, deletes, or resubmits a Comfy job. A server that
        cannot be reached cannot supply permission to release the reservation.
        """
        record = self._record(run_id)
        deadline = time.monotonic() + 25
        while True:
            with self.lock:
                if record['status'] == 'succeeded' or (record['status'] == 'failed' and record.get('stage') != 'Video output unavailable'):
                    return self._public(record)
                if record['status'] == 'preparing':
                    raise VideoRunError('This request is still being prepared or submitted. Wait for its response before checking it.')
                if not record.get('refreshing'):
                    record['refreshing'] = True
                    break
            if time.monotonic() >= deadline:
                raise VideoRunError('Studio is still checking this request. Try Check & unlock again shortly.')
            self.stop.wait(0.05)
        client = None
        try:
            if not record.get('comfy_url'):
                raise VideoRunError('The original ComfyUI connection is unknown. This request cannot be verified.')
            base = local_url(record['comfy_url'])
            client = self.client_factory()

            def own_history():
                if not record.get('prompt_id'):
                    return None
                response = client.get(base + '/history/' + _id(record['prompt_id']), timeout=10)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise VideoRunError('ComfyUI returned an unreadable status for this request.')
                return data.get(record['prompt_id'])

            history = own_history()
            response = client.get(base + '/queue', timeout=8)
            response.raise_for_status()
            queue = response.json()
            if not isinstance(queue, dict) or not all(isinstance(queue.get(key), list) for key in ('queue_running', 'queue_pending')):
                raise VideoRunError('ComfyUI’s queue status could not be verified. The previous request remains reserved.')
            active = []
            for key in ('queue_running', 'queue_pending'):
                for entry in queue[key]:
                    if not isinstance(entry, list) or len(entry) < 2:
                        raise VideoRunError('ComfyUI’s queue entries could not be verified. The previous request remains reserved.')
                    own_id = record.get('prompt_id') and entry[1] == record['prompt_id']
                    own_client = len(entry) >= 4 and isinstance(entry[3], dict) and entry[3].get('client_id') == record['client_id']
                    if own_id or own_client:
                        active.append((_id(entry[1]), key == 'queue_running'))
            if len(active) > 1:
                raise VideoRunError('More than one active task matches this request. Nothing was interrupted or released.')
            if active:
                self._save(record, prompt_id=active[0][0], prompt_id_confirmed=True,
                           status='running' if active[0][1] else 'queued',
                           stage='Generating video' if active[0][1] else 'Queued in ComfyUI', error=None)
                return self._public(record)
            # A job may finish between the first history read and queue check.
            history = own_history() or history
            if history:
                status = history.get('status', {})
                if status.get('status_str') == 'error':
                    self._save(record, status='failed', stage='Generation failed', finished_at=time.time(),
                               error=_history_error(history, record['prompt_id']))
                    return self._public(record)
                if status.get('completed') and status.get('status_str') == 'success':
                    self._complete(record, history, client)
                    return self._public(record)
            self._save(record, status='failed', stage='Previous request released; no active Comfy job found',
                       error='No active task or recoverable result matching this request was found. It was not interrupted, deleted, or submitted again. You can generate a new take.',
                       finished_at=time.time(), resolved_at=time.time(), resolution_reason='explicit_check_no_active_own_job')
            return self._public(record)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, VideoRunError):
                raise
            raise VideoRunError('ComfyUI could not verify this request. Keep its server running and try Check & unlock again; the reservation remains in place.') from exc
        finally:
            if client is not None:
                client.close()
            with self.lock:
                record.pop('refreshing', None)

    def _spawn(self, ident, monitor_only=False):
        with self.lock:
            if ident in self.workers and self.workers[ident].is_alive():
                return
            worker = threading.Thread(target=self._monitor if monitor_only else self.process,
                                      args=(ident,), daemon=True, name='h3-video-' + ident[:8])
            self.workers[ident] = worker
            worker.start()

    def _idle(self, client, base):
        self.resources.assert_idle()
        response = client.get(base + '/queue', timeout=8)
        response.raise_for_status()
        queue = response.json()
        if not isinstance(queue.get('queue_running'), list) or not isinstance(queue.get('queue_pending'), list):
            raise VideoRunError('ComfyUI’s queue status could not be verified.')
        if queue['queue_running'] or queue['queue_pending']:
            raise VideoRunError('ComfyUI is already generating another video. Nothing was interrupted or added to its queue.')

    def _reroll_transfer(self, record, client):
        source = self._load(record['parent_run_id'], 'transfer.json')
        graph, manifest = source['prompt'], source['manifest']
        base = local_url(source['comfy_url'])
        response = client.get(base + '/object_info', timeout=20)
        response.raise_for_status()
        schema = response.json()
        if manifest.get('mmh3', {}).get('available') and manifest.get('mmh3', {}).get('reference_packet_node_id'):
            control_id = str(manifest['mmh3']['reference_packet_node_id'])
            if graph.get(control_id, {}).get('class_type') != 'MMH3Create':
                raise VideoRunError('The saved video no longer has its original shared seed control.')
            graph[control_id]['inputs']['seed'] = record['seed']
            repaired = repair_studio_controls(graph, schema, manifest)
            graph, manifest = repaired['prompt'], repaired['manifest']
        else:
            noise = [node for node in graph.values() if node['class_type'] == 'RandomNoise']
            if len(noise) != 1 or type(noise[0]['inputs'].get('noise_seed')) is not int:
                raise VideoRunError('The saved video has an unsupported seed connection. Generate a new Studio take.')
            noise[0]['inputs']['noise_seed'] = record['seed']
            manifest['seed'] = record['seed']
        _materialize_ui_defaults(graph, schema)
        _validate_graph(graph, schema, {item['comfy_image'] for item in manifest.get('images', [])})
        source.update(prompt=graph, manifest=manifest,
                      workflow=_ui_workflow(graph, schema, record['id'], 'Studio video variation'))
        return source

    def _own_output_paths(self, transfer, record):
        transfer = copy.deepcopy(transfer)
        graph, manifest, workflow = transfer['prompt'], transfer['manifest'], transfer['workflow']
        prefix = 'h3_prompt_studio/runs/' + record['id'] + '/video'
        video_nodes = [key for key, node in graph.items() if node['class_type'] == 'SaveVideo']
        state_nodes = [key for key, node in graph.items() if node['class_type'] == 'MMH3Save']
        if len(video_nodes) != 1 or len(state_nodes) > 1:
            raise VideoRunError('Direct generation needs one video output and at most one continuation archive.')
        old_prefix = manifest.get('output_prefix')
        if graph[video_nodes[0]]['inputs'].get('filename_prefix') != old_prefix:
            raise VideoRunError('The prepared video output does not match its manifest.')
        graph[video_nodes[0]]['inputs']['filename_prefix'] = prefix
        if state_nodes:
            graph[state_nodes[0]]['inputs']['filename_prefix'] = 'mmh3/' + prefix
            manifest['mmh3']['output_prefix'] = 'mmh3/' + prefix
        for node in workflow['nodes']:
            ident = str(node['id'])
            if ident not in video_nodes + state_nodes:
                continue
            old = old_prefix if ident in video_nodes else 'mmh3/' + old_prefix
            new = prefix if ident in video_nodes else 'mmh3/' + prefix
            if old not in node.get('widgets_values', []):
                raise VideoRunError('The prepared output widget does not match the API workflow.')
            node['widgets_values'] = [new if value == old else value for value in node['widgets_values']]
        manifest.update(transfer_id=record['id'], output_prefix=prefix, seed=record['seed'], queued=False,
                        video_run_id=record['id'])
        workflow['id'] = record['id']
        workflow.setdefault('extra', {})['h3_prompt_studio'] = copy.deepcopy(manifest)
        transfer.update(id=record['id'], comfy_url=local_url(transfer['comfy_url']))
        return transfer

    def process(self, run_id):
        """Worker entry point; public for deterministic CPU tests with workers off."""
        record = self._record(run_id)
        with self.lock:
            if record['status'] != 'preparing' or record.get('processing'):
                return self._public(record)
            record['processing'] = True
        client = self.client_factory()
        try:
            self.resources.assert_idle()
            if record['kind'] == 'reroll':
                transfer = self._reroll_transfer(record, client)
            elif record['kind'] == 'combine':
                from .video_join import build_join_transfer
                entries = self._chain(record['parent_run_id'])
                response = client.get(entries[-1]['transfer']['comfy_url'] + '/object_info', timeout=20)
                response.raise_for_status()
                transfer = build_join_transfer(entries, response.json())
            else:
                project = self._load(run_id, 'project.json')
                prompt = self._load(run_id, 'request.json')['prompt']
                transfer = self.prepare(copy.deepcopy(project), prompt)
            transfer = self._own_output_paths(transfer, record)
            base, manifest = transfer['comfy_url'], transfer['manifest']
            atomic_json(self._folder(run_id) / 'transfer.json', transfer)
            # Reflect the immutable, actually applied settings in future rerolls.
            project = self._load(run_id, 'project.json')
            render = project.setdefault('comfy_render', {})
            for key in ('seed', 'steps', 'resolution', 'aspect_ratio', 'quality'):
                if record['kind'] != 'combine' and key in manifest and manifest[key] != 'source':
                    render[key] = copy.deepcopy(manifest[key])
            if record['kind'] != 'combine' and 'loras' in manifest:
                render['loras'] = [{'name': item['name'], 'strength': item['strength'], 'enabled': True} for item in manifest['loras']]
            atomic_json(self._folder(run_id) / 'project.json', project)
            self._save(record, comfy_url=base, width=manifest['width'], height=manifest['height'],
                       resolution=manifest.get('resolution'), duration=manifest.get('actual_duration', manifest.get('duration')),
                       steps=manifest.get('steps'), frames=manifest.get('frames'),
                       continuation_input_source=manifest.get('mmh3', {}).get('source'),
                       stage='Preparing GPU', graph_sha256=_digest(transfer['prompt']))

            def queue_once():
                self._idle(client, base)
                self._save(record, submission_intent=True, submitted_at=time.time(), stage='Submitting video',
                           prompt_id=record['id'], prompt_id_confirmed=False)
                # Do not wrap this POST in a retry or infer rejection from a timeout.
                response = client.post(base + '/prompt', json={'prompt': transfer['prompt'], 'prompt_id': record['id'],
                                       'client_id': record['client_id'],
                                       'extra_data': {'extra_pnginfo': {'workflow': transfer['workflow']},
                                                      'h3_prompt_studio_run': record['id']}}, timeout=90)
                if 400 <= response.status_code < 500:
                    try:
                        rejection = response.json()
                    except ValueError:
                        rejection = None
                    self._save(record, status='failed', stage='ComfyUI rejected the workflow',
                               error=_submission_error(rejection, transfer['prompt'], response.status_code),
                               finished_at=time.time())
                    return
                response.raise_for_status()
                reply = response.json()
                prompt_id = _id(reply.get('prompt_id'))
                if reply.get('error') or reply.get('node_errors'):
                    raise VideoRunError('ComfyUI returned an ambiguous submission response.')
                self._save(record, prompt_id=prompt_id, prompt_id_confirmed=True, status='queued', stage='Queued in ComfyUI', error=None)

            self._idle(client, base)
            self.resources.prepare_h3_then(queue_once)
        except Exception as exc:
            uncertain = bool(record.get('submission_intent')) and record['status'] != 'failed'
            message = ('The submission response was lost. Studio will not submit this request again. Check ComfyUI’s queue before starting another video.'
                       if uncertain else str(exc)[:700] if isinstance(exc, (ValueError, ResourceError)) else
                       'Video preparation could not finish. Check the ComfyUI and LM Studio connections; nothing was queued.')
            self._save(record, status='uncertain' if uncertain else 'failed', stage='Check submission' if uncertain else 'Needs attention',
                       error=message, **({} if uncertain else {'finished_at': time.time()}))
        finally:
            client.close()
            with self.lock:
                record.pop('processing', None)
        if self.start_workers and record['status'] in ('queued', 'running', 'uncertain') and record.get('prompt_id'):
            self._monitor(run_id)
        return self._public(record)

    def _recover_prompt_id(self, record, client):
        response = client.get(record['comfy_url'] + '/queue', timeout=8)
        response.raise_for_status()
        queue = response.json()
        matches = []
        for key in ('queue_running', 'queue_pending'):
            for entry in queue.get(key, []):
                # Never inspect another job's prompt dictionary at entry[2].
                if (isinstance(entry, list) and len(entry) >= 4 and isinstance(entry[3], dict) and
                        entry[3].get('client_id') == record['client_id']):
                    matches.append((_id(entry[1]), key == 'queue_running'))
        if len(matches) == 1:
            self._save(record, prompt_id=matches[0][0], prompt_id_confirmed=True, status='running' if matches[0][1] else 'queued',
                       stage='Generating video' if matches[0][1] else 'Queued in ComfyUI', error=None)

    def refresh(self, run_id):
        with self.lock:
            record = self._record(run_id)
            recover_output = record['status'] == 'failed' and record.get('stage') == 'Video output unavailable'
            if record['status'] not in ('queued', 'running', 'uncertain') and not recover_output:
                return self._public(record)
            if record.get('refreshing'):
                return self._public(record)
            record['refreshing'] = True
        client = self.client_factory()
        try:
            if not record.get('prompt_id') or not record.get('prompt_id_confirmed', True):
                if record.get('comfy_url'):
                    self._recover_prompt_id(record, client)
                if not record.get('prompt_id'):
                    return self._public(record)
            base, prompt_id = record['comfy_url'], record['prompt_id']
            response = client.get(base + '/history/' + prompt_id, timeout=10)
            response.raise_for_status()
            history = response.json().get(prompt_id)
            if history:
                status = history.get('status', {})
                if status.get('status_str') == 'error':
                    self._save(record, status='failed', stage='Generation failed', finished_at=time.time(),
                               error=_history_error(history, prompt_id))
                elif status.get('completed') and status.get('status_str') == 'success':
                    self._complete(record, history, client)
            elif not recover_output:
                response = client.get(base + '/queue', timeout=8)
                response.raise_for_status()
                queue = response.json()
                running = any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id for item in queue.get('queue_running', []))
                pending = any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id for item in queue.get('queue_pending', []))
                if running or pending:
                    self._save(record, status='running' if running else 'queued',
                               stage='Generating video' if running else 'Queued in ComfyUI', error=None)
                elif time.time() - record.get('submitted_at', record['created_at']) > 20:
                    self._save(record, status='uncertain', stage='Waiting for ComfyUI result',
                               error='This request is no longer in the queue and its result is unavailable. It was not submitted again.')
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # A read failure cannot establish that a running render failed.
            if record['status'] not in ('succeeded', 'failed'):
                self._save(record, stage='Reconnecting to ComfyUI')
        finally:
            client.close()
            with self.lock:
                record.pop('refreshing', None)
        return self._public(record)

    def _complete(self, record, history, client):
        transfer = self._load(record['id'], 'transfer.json')
        graph, manifest, outputs = transfer['prompt'], transfer['manifest'], history.get('outputs', {})
        video_id = next(key for key, node in graph.items() if node['class_type'] == 'SaveVideo')
        candidates = []
        for entries in outputs.get(video_id, {}).values():
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict) or item.get('type') != 'output':
                    continue
                filename, subfolder = item.get('filename'), item.get('subfolder', '')
                try:
                    if not isinstance(filename, str) or '/' in filename or '\\' in filename:
                        continue
                    if not isinstance(subfolder, str):
                        continue
                    # SaveVideo uses native Windows separators in history.
                    # Normalize only the directory, then apply the same strict
                    # traversal and own-prefix checks as on other platforms.
                    subfolder = subfolder.replace('\\', '/')
                    relative = _relative((subfolder + '/' if subfolder else '') + filename, '.mp4')
                    if _matches_prefix(relative, manifest['output_prefix']):
                        candidates.append({'filename': filename, 'subfolder': subfolder, 'type': 'output'})
                except (VideoRunError, TypeError):
                    continue
        if len(candidates) != 1:
            self._save(record, status='failed', stage='Video output unavailable', finished_at=time.time(),
                       error='ComfyUI finished but did not report one MP4 belonging to this video request.')
            return
        source, warning = None, None
        media = manifest.get('mmh3', {})
        if media.get('save_enabled'):
            saved = outputs.get(str(media.get('save_node_id')), {}).get('mmh3_saved', [])
            try:
                if len(saved) != 1 or not isinstance(saved[0].get('file'), str) or not saved[0]['file'].startswith('output::'):
                    raise VideoRunError('No continuation state was returned.')
                relative = _relative(saved[0]['file'].removeprefix('output::'), '.mmh3')
                if not _matches_prefix(relative, media['output_prefix']):
                    raise VideoRunError('The continuation file did not match this take.')
                response = client.get(record['comfy_url'] + '/mmh3_media/file_info', params={'file': 'output::' + relative}, timeout=15)
                response.raise_for_status()
                card = response.json()
                geometry = card.get('geometry', {})
                if (not card.get('has', {}).get('latent') or geometry.get('fps') != 24 or
                        any(geometry.get(key) != manifest.get(key) for key in ('width', 'height', 'frames'))):
                    raise VideoRunError('The saved continuation state does not match this take.')
                source = relative
            except (ValueError, TypeError, KeyError, httpx.HTTPError):
                warning = 'The video finished, but its continuation state could not be verified. You can still watch or reroll it.'
        timestamps = {}
        for item in history.get('status', {}).get('messages', []):
            if isinstance(item, list) and len(item) == 2 and item[0] in ('execution_start', 'execution_success') and isinstance(item[1], dict):
                timestamps[item[0]] = item[1].get('timestamp')
        seconds = None
        if all(type(timestamps.get(key)) in (int, float) and math.isfinite(timestamps[key]) for key in ('execution_start', 'execution_success')):
            seconds = max(0, (timestamps['execution_success'] - timestamps['execution_start']) / 1000)
        now = time.time()
        end = timestamps.get('execution_success')
        end = end / 1000 if type(end) in (int, float) and math.isfinite(end) else None
        prior = record.get('finished_at')
        if end is not None and record['created_at'] <= end <= now:
            finished_at = end
        elif type(prior) in (int, float) and math.isfinite(prior) and record['created_at'] <= prior <= now:
            finished_at = prior
        else:
            finished_at = now
        self._save(record, status='succeeded', stage='Video ready', error=None, warning=warning,
                   finished_at=finished_at, video=candidates[0], continuation_source=source,
                   server_execution_seconds=seconds)

    def _monitor(self, run_id):
        deadline = time.monotonic() + self.monitor_timeout
        while not self.stop.is_set() and time.monotonic() < deadline:
            state = self.refresh(run_id)
            if state['status'] not in ('queued', 'running', 'uncertain'):
                return
            if self.stop.wait(self.poll_interval):
                return
        if not self.stop.is_set():
            record = self._record(run_id)
            self._save(record, status='uncertain', stage='Check ComfyUI progress',
                       error='This long-running request is still unresolved. It was not interrupted or submitted again.')

    def close(self):
        self.stop.set()
