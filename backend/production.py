"""Explicit, durable serial production using Studio's existing asset/video managers.

Creating or reading a batch never starts work. A restart pauses its scheduler;
resuming reconciles saved request IDs and never silently retries a failed take.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

from .compiler import compile_project
from .comfy_transfer import _settings
from .projects import atomic_json, check_project, safe_id


class ProductionError(ValueError):
    pass


ACTIVE = {'preparing', 'submitting', 'queued', 'running', 'uncertain', 'cancelling'}
RENDER_FIELDS = {'seed', 'resolution', 'aspect_ratio', 'quality', 'steps', 'save_mmh3',
                 'experimental_preview', 'loras'}


def render_options(value):
    if not isinstance(value, dict) or set(value) - RENDER_FIELDS:
        raise ProductionError('Use supported video render options only.')
    return copy.deepcopy(value)


class ProductionManager:
    def __init__(self, data_dir, load_project, videos, assets, *, start_workers=True, poll_interval=2):
        self.directory = Path(data_dir).resolve() / 'production'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.load_project, self.videos, self.assets = load_project, videos, assets
        self.start_workers, self.poll_interval = start_workers, poll_interval
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.records, self.workers = {}, {}
        for path in self.directory.glob('*/record.json'):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                ident = safe_id(record['id'])
                if path.parent != self.directory / ident:
                    continue
                if record['status'] == 'running':
                    record.update(status='paused', error='Studio restarted. Resume to reconcile the saved receipts and continue.')
                    atomic_json(path, record)
                self.records[ident] = record
            except (ValueError, KeyError, OSError):
                continue

    def _record(self, ident):
        try:
            return self.records[safe_id(ident)]
        except KeyError as exc:
            raise ProductionError('This production batch was not found.') from exc

    def _save(self, record, **changes):
        record.update(changes, updated_at=time.time())
        atomic_json(self.directory / record['id'] / 'record.json', record)

    def _public(self, record):
        result = copy.deepcopy(record)
        result.pop('digest', None)
        result['completed'] = sum(item['status'] == 'succeeded' and bool(item.get('video_url')) for item in result['items'])
        result['total'] = len(result['items'])
        result['playlist_url'] = '/api/production/' + record['id'] + '/playlist'
        return result

    def get(self, ident):
        with self.lock:
            return self._public(self._record(ident))

    def list(self):
        with self.lock:
            return [self._public(record) for record in sorted(self.records.values(), key=lambda r: r['created_at'], reverse=True)]

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {'request_id', 'name', 'items', 'project_ids', 'render_options', 'stop_on_error', 'prepare_images_first'}:
            raise ProductionError('Provide a batch name and saved project IDs.')
        ident = safe_id(body.get('request_id'))
        name = body.get('name', 'Production batch')
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ProductionError('Use a batch name between 1 and 160 characters.')
        if 'items' in body and 'project_ids' in body:
            raise ProductionError('Provide items or project_ids, not both.')
        entries = body.get('items')
        if entries is None:
            ids = body.get('project_ids')
            if not isinstance(ids, list):
                raise ProductionError('Choose between 1 and 100 saved projects.')
            entries = [{'project_id': value} for value in ids]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise ProductionError('Choose between 1 and 100 saved projects.')
        options = render_options(body.get('render_options', {}))
        if type(body.get('stop_on_error', True)) is not bool:
            raise ProductionError('stop_on_error must be true or false.')
        if type(body.get('prepare_images_first', True)) is not bool:
            raise ProductionError('prepare_images_first must be true or false.')
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self.lock:
            if ident in self.records:
                record = self.records[ident]
                if record['digest'] != digest:
                    raise ProductionError('This batch request ID already belongs to different settings.')
                return self._public(record)
            snapshots, items = [], []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or set(entry) - {'project_id', 'render_options', 'image_spec', 'existing_run_id'}:
                    raise ProductionError('Each item needs a saved project ID and optional render or image settings.')
                project = copy.deepcopy(check_project(self.load_project(safe_id(entry.get('project_id')))))
                project['comfy_render'] = {**project.get('comfy_render', {}), **options,
                                           **render_options(entry.get('render_options', {}))}
                if project['comfy_render'].get('continuation_source'):
                    raise ProductionError('Production items must be independent clips; use Story continuation for linked motion.')
                spec = entry.get('image_spec')
                validation_project = copy.deepcopy(project)
                if spec is not None:
                    from .asset_runs import _spec
                    spec = _spec(spec)
                    if project['mode'] not in ('t2va', 'i2va') or any(a.get('enabled', True) and a.get('role') in ('first_frame', 'last_frame') for a in project['assets']):
                        raise ProductionError('Generated first frames need Text only or First frame projects without an existing enabled keyframe.')
                    validation_project['mode'] = 'i2va'
                    validation_project['assets'].append({'id': str(uuid.uuid4()), 'name': spec['name'], 'media_type': 'image',
                        'role': 'first_frame', 'enabled': True, 'description': spec['prompt'], 'prompt_tag': spec['prompt_tag']})
                timing = _settings(validation_project, project['comfy_render'])
                compiled = compile_project(validation_project)
                if not compiled['valid']:
                    errors = [issue.get('message', '') for issue in compiled['issues'] if issue.get('severity') == 'error']
                    raise ProductionError(f'Project {index + 1} is not ready: ' + ' '.join(errors[:3]))
                snapshots.append(project)
                items.append({'index': index, 'project_id': project['id'], 'title': project['title'],
                              'duration': project['duration'], 'actual_duration': timing['actual_duration'],
                              'status': 'pending', 'stage': 'Ready to start', 'error': None,
                              'run_id': str(uuid.uuid4()), 'asset_run_id': str(uuid.uuid4()) if spec else None,
                              'image_spec': spec, 'asset_done': False, 'video_url': None, 'attempts': []})
                if entry.get('existing_run_id') is not None:
                    if spec:
                        raise ProductionError('An existing video cannot also request a new first frame.')
                    manager = self.videos()
                    receipt = manager.get(safe_id(entry['existing_run_id']))
                    if receipt['status'] != 'succeeded' or not receipt.get('video_url') or manager.snapshot(receipt['id']) != project:
                        raise ProductionError('An adopted take must be successful and match the entire frozen project and render settings.')
                    items[-1].update(run_id=receipt['id'], status='succeeded', stage='Existing matching take',
                                     video_url=receipt['video_url'], adopted=True)
            now = time.time()
            record = {'id': ident, 'name': name.strip(), 'digest': digest, 'status': 'draft', 'error': None,
                      'created_at': now, 'updated_at': now, 'items': items, 'cancel_requested': False,
                      'stop_on_error': body.get('stop_on_error', True),
                      'prepare_images_first': body.get('prepare_images_first', True)}
            folder = self.directory / ident
            folder.mkdir(exist_ok=False)
            for index, project in enumerate(snapshots):
                atomic_json(folder / f'project-{index}.json', project)
            self._save(record)
            self.records[ident] = record
            return self._public(record)

    def start(self, ident):
        with self.lock:
            record = self._record(ident)
            if record['status'] == 'succeeded':
                return self._public(record)
            worker = self.workers.get(record['id'])
            if worker and worker.is_alive():
                return self._public(record)
            if any(r['id'] != record['id'] and r['status'] == 'running' for r in self.records.values()):
                raise ProductionError('Another production batch is running. Pause or finish it first.')
            if record.get('stop_on_error', True) and any(i['status'] in ('failed', 'cancelled') for i in record['items']):
                raise ProductionError('Explicitly retry failed or cancelled items before resuming this batch.')
            self._save(record, status='running', error=None, cancel_requested=False)
            if self.start_workers:
                worker = threading.Thread(target=self.process, args=(record['id'],), daemon=True,
                                          name='h3studio-production-' + record['id'])
                self.workers[record['id']] = worker
                worker.start()
            return self._public(record)

    def _update_item(self, record, item, **changes):
        with self.lock:
            item.update(changes)
            self._save(record)

    def _receipt(self, manager, request_id):
        # get() is a purely local read. Only its explicit missing-record error
        # permits an initial submit; other errors are not evidence of absence.
        from .asset_runs import AssetRunError
        from .video_runs import VideoRunError
        try:
            return manager.get(request_id)
        except (AssetRunError, VideoRunError) as exc:
            if str(exc) not in ('This video run was not found.', 'That image job was not found.'):
                raise
            return None

    def _wait(self, record, item, manager, request_id, submit, phase):
        receipt = self._receipt(manager, request_id)
        if receipt is not None and phase == 'image' and receipt.get('can_resume') and receipt['status'] == 'paused':
            # The enclosing batch start/resume is explicit authorization; the
            # image manager permits this only before any submission intent.
            receipt = manager.resume(request_id)
        if receipt is None:
            if record['cancel_requested'] or record['status'] != 'running':
                return None
            self._update_item(record, item, status='submitting', stage='Submitting ' + phase, error=None)
            # Persisted stable request ID makes a lost in-process return safe
            # to reconcile. The existing manager owns Comfy's submission intent.
            receipt = submit()
        while True:
            self._update_item(record, item, status=receipt['status'], stage=phase.title() + ': ' + receipt.get('stage', receipt['status']),
                              error=receipt.get('error'))
            if record['cancel_requested']:
                if receipt['status'] in ACTIVE:
                    manager.cancel(request_id)
                return None
            if receipt['status'] == 'succeeded':
                return receipt
            if receipt['status'] in ('failed', 'cancelled', 'paused', 'uncertain'):
                # One explicit refresh can resolve a lost response without a
                # POST; still-uncertain work halts the entire serial queue.
                if receipt['status'] == 'uncertain':
                    refreshed = manager.refresh(request_id)
                    if refreshed['status'] != 'uncertain':
                        receipt = refreshed
                        continue
                raise ProductionError(receipt.get('error') or 'This receipt needs attention before production can continue.')
            if self.stop.wait(self.poll_interval):
                return None
            receipt = manager.refresh(request_id)

    def _prepare_image(self, record, item):
        folder = self.directory / record['id']
        project = json.loads((folder / f'project-{item["index"]}.json').read_text(encoding='utf-8'))
        manager = self.assets()
        asset_receipt = self._wait(record, item, manager, item['asset_run_id'],
            lambda: manager.submit(item['asset_run_id'], item['image_spec']), 'image')
        if asset_receipt is None:
            return False
        asset = copy.deepcopy(asset_receipt.get('asset'))
        if not isinstance(asset, dict) or not asset.get('id'):
            raise ProductionError('The image completed without an imported library asset.')
        asset.update(role='first_frame', enabled=True)
        project['mode'] = 'i2va'
        project['assets'].append(asset)
        atomic_json(folder / f'image-project-{item["index"]}.json', project)
        self._update_item(record, item, asset_done=True, asset_id=asset['id'], status='pending',
                          stage='First frame ready; video pending',
                          asset_url='/api/assets/' + asset['id'] + '/file')
        return True

    def _process_item(self, record, item):
        folder = self.directory / record['id']
        if item.get('image_spec') and not item.get('asset_done'):
            if not self._prepare_image(record, item):
                return False
        name = 'image-project' if item.get('asset_done') else 'project'
        project = json.loads((folder / f'{name}-{item["index"]}.json').read_text(encoding='utf-8'))
        compiled = compile_project(check_project(project))
        if not compiled['valid']:
            raise ProductionError('The frozen project could not compile. Review its saved snapshot.')
        atomic_json(folder / f'video-project-{item["index"]}.json', project)
        manager = self.videos()
        receipt = self._wait(record, item, manager, item['run_id'],
            lambda: manager.submit(item['run_id'], project, compiled['prompt']), 'video')
        if receipt is None:
            return False
        self._update_item(record, item, status='succeeded', stage='Video ready for review', error=None,
                          video_url=receipt.get('video_url'), finished_at=time.time())
        return True

    def process(self, ident):
        record = self._record(ident)
        try:
            if record.get('prepare_images_first', True):
                for item in record['items']:
                    if record['status'] != 'running' or record['cancel_requested'] or self.stop.is_set():
                        return
                    if not item.get('image_spec') or item.get('asset_done') or item['status'] in ('failed', 'cancelled'):
                        continue
                    try:
                        if not self._prepare_image(record, item):
                            return
                    except Exception:
                        if item['status'] in ('failed', 'cancelled') and not record.get('stop_on_error', True):
                            continue
                        raise
            for item in record['items']:
                if record['status'] != 'running' or record['cancel_requested'] or self.stop.is_set():
                    return
                if item['status'] == 'succeeded' and item.get('video_url'):
                    continue
                if item['status'] in ('failed', 'cancelled') and not record.get('stop_on_error', True):
                    continue
                try:
                    if not self._process_item(record, item):
                        return
                except Exception:
                    if item['status'] in ('failed', 'cancelled') and not record.get('stop_on_error', True):
                        continue
                    raise
            with self.lock:
                if record['status'] == 'running':
                    failures = sum(i['status'] in ('failed', 'cancelled') for i in record['items'])
                    self._save(record, status='needs_attention' if failures else 'succeeded',
                               error=f'{failures} items failed; explicitly retry them to create new takes.' if failures else None,
                               finished_at=time.time())
        except Exception as exc:
            with self.lock:
                if record['status'] == 'running':
                    self._save(record, status='needs_attention', error=str(exc)[:1000])
        finally:
            with self.lock:
                if self.stop.is_set() and record['status'] == 'running':
                    self._save(record, status='paused', error='Production stopped; resume to reconcile saved receipts.')

    def cancel(self, ident):
        with self.lock:
            record = self._record(ident)
            if record['status'] in ('succeeded', 'cancelled'):
                return self._public(record)
            self._save(record, status='cancelled', cancel_requested=True, error=None)
            current = next((i for i in record['items'] if i['status'] in ACTIVE), None)
        if current:
            try:
                is_image = current.get('image_spec') and not current.get('asset_done')
                manager = self.assets() if is_image else self.videos()
                request_id = current['asset_run_id'] if is_image else current['run_id']
                if self._receipt(manager, request_id):
                    result = manager.cancel(request_id)
                    self._update_item(record, current, status=result['status'], error=result.get('error'), stage=result.get('stage'))
            except Exception as exc:
                with self.lock:
                    self._save(record, error='Stopped future items. Current receipt needs reconciliation: ' + str(exc)[:700])
        return self.get(ident)

    def retry(self, ident, index):
        with self.lock:
            record = self._record(ident)
            if record['status'] == 'running' or (self.workers.get(record['id']) and self.workers[record['id']].is_alive()):
                raise ProductionError('Wait for the current production worker to stop before retrying.')
            if type(index) is not int or not 0 <= index < len(record['items']):
                raise ProductionError('Choose an item in this batch.')
            item = record['items'][index]
            if item['status'] not in ('failed', 'cancelled'):
                raise ProductionError('Only a definite failed or cancelled item can receive a new request ID.')
            item['attempts'].append({k: item.get(k) for k in ('run_id', 'asset_run_id', 'status', 'error')})
            item['run_id'] = str(uuid.uuid4())
            if item.get('image_spec') and not item.get('asset_done'):
                item['asset_run_id'] = str(uuid.uuid4())
            item.update(status='pending', stage='Retry explicitly authorized', error=None, video_url=None)
            self._save(record, status='paused', cancel_requested=False, error=None)
            return self._public(record)

    def playlist(self, ident):
        record = self.get(ident)
        return {'id': record['id'], 'name': record['name'], 'status': record['status'],
                'review_required': True, 'items': [{k: item.get(k) for k in
                    ('index', 'title', 'project_id', 'run_id', 'status', 'duration', 'actual_duration', 'video_url')}
                    for item in record['items']]}
