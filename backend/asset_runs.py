"""Durable, local Z-Image-Turbo jobs with at-most-once queue submission.

Only explicit submit/resume operations can start work. Polling and restart
recovery look up the recorded prompt; a lost POST response is never retried.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from .projects import atomic_json
from .resources import ResourceError, local_url


class AssetRunError(ValueError):
    pass


MODELS = ('z_image_turbo_bf16.safetensors', 'z_image_turbo_fp8_e4m3fn.safetensors')
H3_FRAME_MODEL = 'h3-frame-fl2va-5f'
ENCODER, VAE = 'qwen_3_4b.safetensors', 'ae.safetensors'
NODES = ('UNETLoader', 'CLIPLoader', 'VAELoader', 'CLIPTextEncode',
         'ConditioningZeroOut', 'ModelSamplingAuraFlow', 'EmptySD3LatentImage',
         'KSampler', 'VAEDecode', 'SaveImage')
ROLES = frozenset({'face', 'character', 'background', 'object', 'palette', 'style',
                   'wardrobe', 'pose', 'other'})
ACTIVE = frozenset({'preparing', 'queued', 'running', 'uncertain', 'cancelling'})
MAX_BYTES, MAX_SEED = 64 * 1024**2, 2**53 - 1


def _id(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise AssetRunError('Use a valid asset request identifier.') from exc


def _origin(value):
    try:
        result = local_url(value)
        if urlparse(result).path not in ('', '/'):
            raise ValueError()
        return result.rstrip('/')
    except (ValueError, TypeError, AttributeError) as exc:
        raise AssetRunError('Use a configured ComfyUI HTTP origin on this computer, with a port and no path.') from exc


def _spec(value):
    if not isinstance(value, dict):
        raise AssetRunError('Provide an image prompt and asset settings.')
    fields = {'prompt', 'name', 'semantic_role', 'person_id', 'prompt_tag', 'model', 'width', 'height', 'seed'}
    if set(value) - fields:
        raise AssetRunError('The image request contains unsupported settings.')
    result = copy.deepcopy(value)
    for key, limit, default in [('prompt', 6000, ''), ('name', 100, 'Generated reference')]:
        text = result.get(key, default)
        if not isinstance(text, str) or not text.strip() or len(text) > limit or '\x00' in text:
            raise AssetRunError(f'Use a nonempty {key} of at most {limit} characters.')
        result[key] = text.strip()
    result.setdefault('semantic_role', 'other')
    if not isinstance(result['semantic_role'], str) or result['semantic_role'] not in ROLES:
        raise AssetRunError('Choose a supported image reference role.')
    result.setdefault('prompt_tag', 'generated-reference')
    if not isinstance(result['prompt_tag'], str) or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', result['prompt_tag']) or len(result['prompt_tag']) > 64:
        raise AssetRunError('Use a short reference tag such as arin-face or cafe-background.')
    result['person_id'] = _id(result['person_id']) if result.get('person_id') is not None else None
    result.setdefault('model', MODELS[0])
    if result['model'] not in (*MODELS, H3_FRAME_MODEL):
        raise AssetRunError('Select an installed Z-Image-Turbo or experimental H3 frame generator.')
    for key in ('width', 'height'):
        result.setdefault(key, 512)
        if type(result[key]) is not int or not 128 <= result[key] <= 1024 or result[key] % 16:
            raise AssetRunError('Image dimensions must be multiples of 16 between 128 and 1024 pixels.')
        if result['model'] == H3_FRAME_MODEL and result[key] % 32:
            raise AssetRunError('Experimental H3 frame dimensions must be multiples of 32.')
    if type(result.get('seed')) is not int or not 0 <= result['seed'] <= MAX_SEED:
        raise AssetRunError('Provide an explicit whole-number seed between 0 and 9007199254740991.')
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _choices(info, node, field):
    entry = info.get(node)
    inputs = entry.get('input', {}) if isinstance(entry, dict) else {}
    if not isinstance(inputs, dict):
        return []
    required, optional = inputs.get('required', {}), inputs.get('optional', {})
    if not isinstance(required, dict) or not isinstance(optional, dict):
        return []
    descriptor = required.get(field, optional.get(field, []))
    if not isinstance(descriptor, list) or not descriptor:
        return []
    if isinstance(descriptor[0], list):
        return descriptor[0]
    if descriptor[0] == 'COMBO' and len(descriptor) > 1 and isinstance(descriptor[1], dict):
        return descriptor[1].get('options', [])
    return []


def _catalog(info):
    if not isinstance(info, dict):
        raise AssetRunError('ComfyUI did not return a valid node inventory.')
    missing = [node for node in NODES if not isinstance(info.get(node), dict)]
    requirements = [('CLIPLoader', 'clip_name', ENCODER), ('CLIPLoader', 'type', 'lumina2'),
                    ('VAELoader', 'vae_name', VAE), ('KSampler', 'sampler_name', 'res_multistep'),
                    ('KSampler', 'scheduler', 'simple')]
    missing += [f'{node}: {option}' for node, field, option in requirements if option not in _choices(info, node, field)]
    models = [model for model in MODELS if model in _choices(info, 'UNETLoader', 'unet_name')]
    if not models:
        missing.append('Z-Image-Turbo BF16 or FP8 weights')
    return models, missing


def _h3_catalog(info):
    from .comfy_transfer import HERETIC, FL_LORA
    required_nodes = ('UNETLoader', 'CLIPLoader', 'VAELoader', 'LoraLoaderModelOnly', 'MiniMaxH3SigmaShift',
                      'MiniMaxH3ImageToVideo', 'BasicGuider', 'RandomNoise', 'KSamplerSelect', 'BasicScheduler',
                      'SamplerCustomAdvanced', 'VAEDecode', 'ImageFromBatch', 'SaveImage')
    missing = [node for node in required_nodes if not isinstance(info.get(node), dict)]
    required_models = [('UNETLoader', 'unet_name', 'minimax_h3_fl2va_pruned_int8_convrot.safetensors'),
                       ('CLIPLoader', 'clip_name', HERETIC), ('CLIPLoader', 'type', 'minimax'),
                       ('VAELoader', 'vae_name', 'minimax_h3_video_vae_fp16.safetensors'),
                       ('LoraLoaderModelOnly', 'lora_name', FL_LORA), ('KSamplerSelect', 'sampler_name', 'euler'),
                       ('BasicScheduler', 'scheduler', 'simple')]
    missing += [f'{node}: {option}' for node, field, option in required_models if option not in _choices(info, node, field)]
    return ([] if missing else [H3_FRAME_MODEL]), missing


def _h3_frame_graph(request_id, spec):
    from .comfy_transfer import HERETIC, FL_LORA
    def node(kind, **inputs):
        return {'class_type': kind, 'inputs': inputs}
    # Five frames are accepted by native Core but below H3's trained range.
    # This is an experimental extracted frame, not a native text-to-image model.
    return {
        '1': node('UNETLoader', unet_name='minimax_h3_fl2va_pruned_int8_convrot.safetensors', weight_dtype='default'),
        '2': node('CLIPLoader', clip_name=HERETIC, type='minimax', device='default'),
        '3': node('VAELoader', vae_name='minimax_h3_video_vae_fp16.safetensors'),
        '4': node('LoraLoaderModelOnly', model=['1', 0], lora_name=FL_LORA, strength_model=1.0),
        '5': node('MiniMaxH3SigmaShift', model=['4', 0], shift_video=6.0, shift_audio=3.0),
        '6': node('MiniMaxH3ImageToVideo', clip=['2', 0], vae=['3', 0], prompt=spec['prompt'],
                  width=spec['width'], height=spec['height'], length=5),
        '7': node('BasicGuider', model=['5', 0], conditioning=['6', 0]),
        '8': node('RandomNoise', noise_seed=spec['seed']),
        '9': node('KSamplerSelect', sampler_name='euler'),
        '11': node('BasicScheduler', model=['5', 0], scheduler='simple', steps=4, denoise=1.0),
        '12': node('SamplerCustomAdvanced', noise=['8', 0], guider=['7', 0], sampler=['9', 0], sigmas=['11', 0], latent_image=['6', 1]),
        '13': node('VAEDecode', samples=['12', 0], vae=['3', 0]),
        '14': node('ImageFromBatch', image=['13', 0], batch_index=2, length=1),
        '10': node('SaveImage', images=['14', 0], filename_prefix=f'h3_prompt_studio/assets/{request_id}/image'),
    }


def build_graph(request_id, spec):
    """The native official Turbo graph; no arbitrary caller-supplied nodes."""
    if spec['model'] == H3_FRAME_MODEL:
        return _h3_frame_graph(request_id, spec)
    def node(kind, **inputs):
        return {'class_type': kind, 'inputs': inputs}
    return {
        '1': node('UNETLoader', unet_name=spec['model'], weight_dtype='default'),
        '2': node('CLIPLoader', clip_name=ENCODER, type='lumina2', device='default'),
        '3': node('VAELoader', vae_name=VAE),
        '4': node('ModelSamplingAuraFlow', model=['1', 0], shift=3.0),
        '5': node('CLIPTextEncode', clip=['2', 0], text=spec['prompt']),
        '6': node('ConditioningZeroOut', conditioning=['5', 0]),
        '7': node('EmptySD3LatentImage', width=spec['width'], height=spec['height'], batch_size=1),
        '8': node('KSampler', model=['4', 0], positive=['5', 0], negative=['6', 0],
                  latent_image=['7', 0], seed=spec['seed'], steps=8, cfg=1.0,
                  sampler_name='res_multistep', scheduler='simple', denoise=1.0),
        '9': node('VAEDecode', samples=['8', 0], vae=['3', 0]),
        '10': node('SaveImage', images=['9', 0], filename_prefix=f'h3_prompt_studio/assets/{request_id}/image'),
    }


class AssetRunManager:
    def __init__(self, data_dir, resources, get_settings, store_asset, *, client_factory=None,
                 start_workers=True, poll_interval=1.5, monitor_timeout=1800):
        self.directory = (Path(data_dir) / 'asset_runs').resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.resources, self.get_settings, self.store_asset = resources, get_settings, store_asset
        self.client_factory = client_factory or (lambda: httpx.Client(trust_env=False, follow_redirects=False, timeout=15))
        self.start_workers, self.poll_interval, self.monitor_timeout = start_workers, poll_interval, monitor_timeout
        self.lock = threading.RLock()
        self.records, self.job_locks, self.workers = {}, {}, {}
        self.stopping = threading.Event()
        for path in self.directory.glob('*/record.json'):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                ident = _id(record['id'])
                if ident != path.parent.name:
                    continue
                record['spec'] = _spec(record['spec'])
                if record.get('status') in ACTIVE and not record.get('submission_intent'):
                    record.update(status='paused', stage='Resume image preparation', error='The app stopped before submission. Resume to prepare this image.')
                    atomic_json(path, record)
                self.records[ident] = record
                self.job_locks[ident] = threading.RLock()
            except (OSError, ValueError, KeyError, TypeError):
                continue
        # Recovery only polls jobs that already crossed the submission boundary.
        for record in list(self.records.values()):
            if start_workers and record.get('submission_intent') and record['status'] in ACTIVE:
                self._launch(record['id'], monitor_only=True)

    def _origins(self):
        values = self.get_settings().get('comfy_urls', [])
        if not isinstance(values, list) or not values:
            raise AssetRunError('Configure a local ComfyUI connection first.')
        return list(dict.fromkeys(_origin(value) for value in values))

    def options(self):
        servers, errors = [], []
        for origin in self._origins():
            try:
                with self.client_factory() as client:
                    response = client.get(origin + '/object_info')
                    response.raise_for_status()
                    info = response.json()
                    models, missing = _catalog(info)
                    h3_models, h3_missing = _h3_catalog(info)
                available_models = ([] if missing else models) + h3_models
                servers.append({'comfy_url': origin, 'models': available_models, 'missing': missing,
                                'h3_missing': h3_missing, 'ready': bool(available_models)})
            except (httpx.HTTPError, ValueError):
                errors.append(f'ComfyUI node inventory is unavailable at {origin}.')
        available = [model for model in (*MODELS, H3_FRAME_MODEL) if any(s['ready'] and model in s['models'] for s in servers)]
        return {'ready': bool(available), 'models': available, 'default_model': available[0] if available else None,
                'encoder': ENCODER, 'vae': VAE, 'width': 512, 'height': 512, 'max_dimension': 1024,
                'steps': 8, 'cfg': 1, 'sampler': 'res_multistep', 'scheduler': 'simple', 'shift': 3,
                'generators': [{'id': model, 'model': model, 'available': True, 'compatible': True,
                                'name': 'H3 frame · experimental' if model == H3_FRAME_MODEL else model,
                                'label': 'H3 frame · experimental, 5 frames /4 steps' if model == H3_FRAME_MODEL else model,
                                'experimental': model == H3_FRAME_MODEL, 'kind': 'h3_frame' if model == H3_FRAME_MODEL else 'z_image_turbo',
                                'steps': 4 if model == H3_FRAME_MODEL else 8,
                                'note': 'Extracts one frame below the trained video duration. Speed and quality require local testing.' if model == H3_FRAME_MODEL else 'Native eight-step Z-Image-Turbo recipe.'}
                               for model in available], 'servers': servers, 'errors': errors}

    def _record(self, ident):
        ident = _id(ident)
        with self.lock:
            if ident not in self.records:
                raise AssetRunError('That image job was not found.')
            return self.records[ident]

    def _save(self, record, **changes):
        with self.lock:
            record.update(changes, updated_at=time.time())
            atomic_json(self.directory / record['id'] / 'record.json', record)

    def _public(self, record):
        with self.lock:
            result = {key: copy.deepcopy(record.get(key)) for key in
                      ('id', 'request_id', 'status', 'stage', 'error', 'warning', 'created_at', 'updated_at',
                       'started_at', 'finished_at', 'asset', 'asset_id', 'cancel_requested', 'prompt_id')}
            result.update(copy.deepcopy(record['spec']))
            result['elapsed_seconds'] = round(max(0, (record.get('finished_at') or time.time()) -
                                                (record.get('started_at') or record['created_at'])), 3)
            result['can_resume'] = record['status'] == 'paused' and not record.get('submission_intent')
            result['can_retry'] = record['status'] in ('failed', 'cancelled')
            result['can_cancel'] = record['status'] in ACTIVE or record['status'] == 'paused'
            return result

    def get(self, ident):
        return self._public(self._record(ident))

    def list(self):
        with self.lock:
            return [self._public(r) for r in sorted(self.records.values(), key=lambda r: r['created_at'], reverse=True)]

    def submit(self, request_id, spec):
        ident, spec = _id(request_id), _spec(spec)
        digest = _digest(spec)
        origins = self._origins()  # Validate origins before saving or starting work.
        with self.lock:
            if ident in self.records:
                record = self.records[ident]
                if record['digest'] != digest:
                    raise AssetRunError('This image request identifier already belongs to different settings. Use a new identifier.')
                return self._public(record)
            if any(r['status'] in ACTIVE for r in self.records.values()):
                raise AssetRunError('Another image job is active. Wait for it or recover its status first.')
            now = time.time()
            record = {'id': ident, 'request_id': ident, 'spec': spec, 'digest': digest, 'origins': origins,
                      'created_at': now, 'updated_at': now, 'started_at': None, 'finished_at': None,
                      'status': 'preparing', 'stage': 'Checking image models', 'error': None, 'warning': None,
                      'submission_intent': False, 'prompt_id': None, 'prompt_confirmed': False,
                      'client_id': 'h3studio-asset-' + ident, 'cancel_requested': False, 'asset': None, 'asset_id': None}
            folder = self.directory / ident
            folder.mkdir()
            atomic_json(folder / 'spec.json', spec)
            self.records[ident], self.job_locks[ident] = record, threading.RLock()
            self._save(record)
        if self.start_workers:
            self._launch(ident)
        return self._public(record)

    def _launch(self, ident, monitor_only=False):
        with self.lock:
            worker = self.workers.get(ident)
            if worker and worker.is_alive():
                return
            worker = threading.Thread(target=self._monitor if monitor_only else self.process,
                                      args=(ident,), name='h3studio-asset-' + ident, daemon=True)
            self.workers[ident] = worker
            worker.start()

    def _endpoint(self, record):
        origin = _origin(record.get('comfy_url'))
        if origin not in record['origins'] or origin not in self._origins():
            raise AssetRunError('This image job used a ComfyUI connection that is no longer configured. Restore that local connection to recover it.')
        return origin

    @staticmethod
    def _queue(client, origin):
        response = client.get(origin + '/queue')
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ('queue_running', 'queue_pending')):
            raise AssetRunError('ComfyUI queue state could not be verified.')
        return data

    @staticmethod
    def _owned(entry, record):
        # Never inspect the graph/inputs of another queue entry.
        return (isinstance(entry, (list, tuple)) and len(entry) >= 4 and
                (entry[1] == record.get('prompt_id') or
                 isinstance(entry[3], dict) and entry[3].get('client_id') == record['client_id']))

    def process(self, ident):
        record = self._record(ident)
        with self.job_locks[record['id']]:
            if record.get('submission_intent') or record['status'] != 'preparing':
                return self._public(record)
            self._save(record, started_at=record.get('started_at') or time.time())
            try:
                self.resources.assert_idle()
                options = self.options()
                server = next((s for s in options['servers'] if s['ready'] and record['spec']['model'] in s['models']), None)
                if not server:
                    raise AssetRunError('The selected generator’s models, encoder, VAE and native nodes must be installed together in ComfyUI. Check Image generator availability.')
                origin = server['comfy_url']
                if origin not in record['origins']:
                    raise AssetRunError('ComfyUI settings changed during image preparation. Submit a new request.')
                graph = build_graph(record['id'], record['spec'])
                atomic_json(self.directory / record['id'] / 'graph.json', graph)
                self._save(record, comfy_url=origin, stage='Preparing image model')
                if record['cancel_requested']:
                    self._save(record, status='cancelled', stage='Image cancelled', finished_at=time.time())
                    return self._public(record)

                def queue_once():
                    with self.client_factory() as client:
                        self.resources.assert_idle()
                        queue = self._queue(client, origin)
                        if queue['queue_running'] or queue['queue_pending']:
                            raise ResourceError('ComfyUI has running or queued work. The image was not submitted.')
                        with self.lock:
                            if record['cancel_requested']:
                                self._save(record, status='cancelled', stage='Image cancelled', finished_at=time.time())
                                return
                            # This durable boundary precedes POST, including its
                            # caller-specified prompt ID for exact lost-response recovery.
                            self._save(record, submission_intent=True, prompt_id=record['id'],
                                       status='uncertain', stage='Submitting image once')
                        response = client.post(origin + '/prompt', json={
                            'prompt': graph, 'prompt_id': record['id'], 'client_id': record['client_id'],
                            'extra_data': {'studio_asset_request_id': record['id']}})
                        # Request timeout/conflict responses do not prove that
                        # the server never accepted this idempotency identifier.
                        if 400 <= response.status_code < 500 and response.status_code not in (408, 409):
                            self._save(record, status='failed', stage='Image settings rejected',
                                       error=f'ComfyUI rejected the image workflow (HTTP {response.status_code}). Check installed Turbo models and nodes.',
                                       finished_at=time.time())
                            return
                        response.raise_for_status()
                        reply = response.json()
                        prompt_id = _id(reply.get('prompt_id')) if isinstance(reply, dict) else None
                        if not prompt_id:
                            raise AssetRunError('ComfyUI did not confirm the image request identifier.')
                        self._save(record, prompt_id=prompt_id, prompt_confirmed=True,
                                   status='queued', stage='Image queued', error=None)

                self.resources.prepare_comfy_then('video' if record['spec']['model'] == H3_FRAME_MODEL else 'image', queue_once)
            except Exception as exc:
                if record.get('submission_intent') and record['status'] not in ('failed', 'cancelled'):
                    self._save(record, status='uncertain', stage='Recovering image submission',
                               error='The image submission response was not confirmed. Checking the original request; it will not be submitted again automatically.')
                elif record['status'] != 'cancelled':
                    message = str(exc) if isinstance(exc, (AssetRunError, ResourceError)) else 'Image preparation failed. Check local ComfyUI and retry.'
                    self._save(record, status='failed', stage='Image needs attention', error=message, finished_at=time.time())
        if self.start_workers and record['status'] in ACTIVE:
            self._monitor(record['id'])
        return self._public(record)

    def _monitor(self, ident):
        deadline = time.monotonic() + self.monitor_timeout
        while not self.stopping.is_set() and time.monotonic() < deadline:
            result = self.refresh(ident)
            if result['status'] not in ACTIVE:
                return
            self.stopping.wait(self.poll_interval)

    def _history(self, client, origin, record):
        response = client.get(origin + '/history/' + _id(record['prompt_id']))
        response.raise_for_status()
        history = response.json()
        if not isinstance(history, dict):
            raise AssetRunError('ComfyUI image history could not be verified.')
        entry = history.get(record['prompt_id'])
        if entry is not None and not isinstance(entry, dict):
            raise AssetRunError('ComfyUI image history could not be verified.')
        return entry

    def _finish(self, record, client, origin, entry):
        status = entry.get('status', {})
        if not isinstance(status, dict):
            raise AssetRunError('ComfyUI returned an invalid image status.')
        if status.get('status_str') == 'error':
            self._save(record, status='cancelled' if record['cancel_requested'] else 'failed',
                       stage='Image cancelled' if record['cancel_requested'] else 'Image generation failed',
                       error=None if record['cancel_requested'] else 'ComfyUI could not finish this image. Check its execution log and try a new request.',
                       finished_at=time.time())
            return True
        if not status.get('completed') or status.get('status_str') != 'success':
            return False
        if record['cancel_requested']:
            self._save(record, status='cancelled', stage='Image cancelled', error=None, finished_at=time.time())
            return True
        folder = self.directory / record['id']
        asset_path = folder / 'asset.json'
        if asset_path.exists():
            asset = json.loads(asset_path.read_text(encoding='utf-8'))
        else:
            outputs = entry.get('outputs', {})
            result = outputs.get('10', {}) if isinstance(outputs, dict) else {}
            images = result.get('images') if isinstance(result, dict) else None
            if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
                raise AssetRunError('The image job did not return exactly one owned SaveImage result.')
            output = images[0]
            filename = output.get('filename')
            # Native SaveImage uses the host separator (backslashes on Windows).
            # Normalize only for an exact owned-directory comparison; no path
            # resolution, traversal, prefix matching, or arbitrary folders.
            subfolder = output.get('subfolder')
            owned_folder = subfolder.replace('\\', '/') if isinstance(subfolder, str) else None
            if (output.get('type') != 'output' or
                    owned_folder != f'h3_prompt_studio/assets/{record["id"]}' or
                    not isinstance(filename, str) or not re.fullmatch(r'image_[0-9]+_\.png', filename)):
                raise AssetRunError('ComfyUI returned an image outside this job’s owned output folder.')
            data = bytearray()
            with client.stream('GET', origin + '/view', params={k: output[k] for k in ('filename', 'subfolder', 'type')}) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise AssetRunError('The generated image exceeds the 64 MB import limit.')
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != 'PNG' or image.size != (record['spec']['width'], record['spec']['height']):
                        raise AssetRunError('The generated PNG dimensions do not match this request.')
                    image.verify()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise AssetRunError('ComfyUI returned an unreadable PNG image.') from exc
            spec = record['spec']
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', spec['name']).strip(' .') or 'Generated reference'
            asset = self.store_asset(bytes(data), name + '.png', 'image/png')
            if not isinstance(asset, dict) or not asset.get('id'):
                raise AssetRunError('The generated image could not be imported into the asset library.')
            _id(asset['id'])
            asset = {**asset, 'semantic_role': spec['semantic_role'], 'prompt_tag': spec['prompt_tag'],
                     'description': spec['prompt'], 'person_id': spec['person_id'],
                     'generated_by': {'kind': 'h3_frame' if spec['model'] == H3_FRAME_MODEL else 'z_image_turbo',
                                      'run_id': record['id'], 'model': spec['model'], 'seed': spec['seed'],
                                      'steps': 4 if spec['model'] == H3_FRAME_MODEL else 8, 'cfg': 1,
                                      **({'experimental': True, 'generated_frames': 5, 'selected_frame': 2} if spec['model'] == H3_FRAME_MODEL else {})}}
            if spec['person_id'] and spec['semantic_role'] == 'object':
                asset['simple_owner_id'] = spec['person_id']
            atomic_json(asset_path, asset)
            self._save(record, output_sha256=hashlib.sha256(data).hexdigest())
        self._save(record, status='succeeded', stage='Reference image ready', error=None,
                   asset=asset, asset_id=asset['id'], finished_at=time.time())
        return True

    def refresh(self, ident):
        record = self._record(ident)
        with self.job_locks[record['id']]:
            if not record.get('submission_intent') or record['status'] not in ACTIVE:
                return self._public(record)
            try:
                origin = self._endpoint(record)
                with self.client_factory() as client:
                    entry = self._history(client, origin, record)
                    if entry is not None and self._finish(record, client, origin, entry):
                        return self._public(record)
                    queue = self._queue(client, origin)
                    for key, state, stage in [('queue_running', 'running', 'Generating reference image'),
                                               ('queue_pending', 'queued', 'Image queued')]:
                        owned = [item for item in queue[key] if self._owned(item, record)]
                        if len(owned) > 1:
                            raise AssetRunError('More than one queue entry claims this image request. Review ComfyUI before continuing.')
                        if owned:
                            self._save(record, prompt_id=_id(owned[0][1]), prompt_confirmed=True,
                                       status='cancelling' if record['cancel_requested'] else state,
                                       stage='Waiting for own image to finish safely' if record['cancel_requested'] else stage,
                                       error=None)
                            return self._public(record)
                    # The job may have finished between the first history and
                    # queue reads. Check its exact history once more before unknown.
                    entry = self._history(client, origin, record)
                    if entry is not None and self._finish(record, client, origin, entry):
                        return self._public(record)
                    self._save(record, status='uncertain', stage='Original image request not found',
                               error='The original request is absent from queue and history. It will not be generated again automatically; restore the original ComfyUI session to recover it.')
            except Exception as exc:
                message = str(exc) if isinstance(exc, AssetRunError) else 'Could not verify the original image job. Restore the local ComfyUI connection; no new image was submitted.'
                self._save(record, status='uncertain', stage='Image recovery needs attention', error=message)
            return self._public(record)

    def cancel(self, ident):
        record = self._record(ident)
        # This flag can be set while preparation holds the per-job lock. The
        # final check immediately before POST observes it without interrupting.
        with self.lock:
            if record['status'] not in ACTIVE and record['status'] != 'paused':
                return self._public(record)
            self._save(record, cancel_requested=True)
            if not record.get('submission_intent'):
                self._save(record, status='cancelled', stage='Image cancelled', error=None, finished_at=time.time())
                return self._public(record)
        with self.job_locks[record['id']]:
            try:
                origin = self._endpoint(record)
                with self.client_factory() as client:
                    queue = self._queue(client, origin)
                    pending = [entry for entry in queue['queue_pending'] if self._owned(entry, record)]
                    running = any(self._owned(entry, record) for entry in queue['queue_running'])
                    if len(pending) == 1 and not running:
                        prompt_id = _id(pending[0][1])
                        response = client.post(origin + '/queue', json={'delete': [prompt_id]})
                        response.raise_for_status()
                        queue = self._queue(client, origin)
                        if not any(self._owned(entry, record) for key in ('queue_pending', 'queue_running') for entry in queue[key]):
                            self._save(record, status='cancelled', stage='Image cancelled', error=None, finished_at=time.time())
                            return self._public(record)
                    self._save(record, status='cancelling', stage='Waiting for own image to finish safely',
                               warning='An already running image finishes without importing its result. Other ComfyUI jobs are never interrupted.')
            except (httpx.HTTPError, ValueError):
                self._save(record, status='uncertain', stage='Checking image cancellation',
                           error='Cancellation could not be confirmed. The original request is still tracked; no global interrupt was sent.')
        return self.refresh(record['id'])

    def resume(self, ident):
        record = self._record(ident)
        with self.job_locks[record['id']]:
            if record.get('submission_intent'):
                # In particular, NEVER turn an uncertain POST into another POST.
                result = self.refresh(record['id'])
                if self.start_workers and result['status'] in ACTIVE:
                    self._launch(record['id'], monitor_only=True)
                return result
            if record['status'] != 'paused':
                return self._public(record)
            with self.lock:
                if any(r['id'] != record['id'] and r['status'] in ACTIVE for r in self.records.values()):
                    raise AssetRunError('Another image job is active. Wait before resuming this image.')
                self._save(record, status='preparing', stage='Checking image models', error=None)
        if self.start_workers:
            self._launch(record['id'])
        return self._public(record)

    def retry(self, ident, request_id):
        record = self._record(ident)
        if record['status'] not in ('failed', 'cancelled'):
            raise AssetRunError('Recover the original image request before starting a replacement.')
        if _id(request_id) == record['id']:
            raise AssetRunError('A replacement image needs a new request identifier.')
        return self.submit(request_id, record['spec'])

    def close(self):
        self.stopping.set()
