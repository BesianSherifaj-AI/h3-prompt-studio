"""Paired prompt experiments using the existing durable VideoRunManager.

Creating/reloading a comparison never renders. Advance submits at most one
variant with its saved request ID; each result remains outside story branches.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from .compiler import compile_project
from .projects import atomic_json, safe_id
from .video_timing import frame_budget


class MotionLabError(ValueError):
    pass


PRESETS = (
    ('still', 'Stay still', 'The subject stays in place. The camera holds steady. No cut.'),
    ('slow', 'Slow movement', 'The subject walks slowly forward. The camera follows steadily. No cut.'),
    ('medium', 'Medium movement', 'The subject walks forward at a normal pace. The camera follows steadily. No cut.'),
    ('fast', 'Fast movement', 'The subject runs forward quickly. The camera follows steadily. No cut.'),
    ('timed', 'Reach target by the ending', 'The subject moves toward the selected visible target, reaches it near the end of the shot and stops. No cut.'),
    ('distance', 'Numeric distance · experimental', 'The subject walks three metres forward during this shot and stops. The camera follows steadily. No cut.'),
    ('acceleration', '0–100 km/h · experimental', 'The moving vehicle accelerates from rest toward 100 kilometres per hour during this shot. The camera follows it. No cut.'),
    ('look-left', 'Camera looks left', 'The subject remains still. The camera pans slowly left during the shot. No cut.'),
    ('cut', 'Cut to destination', 'Use a clear scene cut to show the destination. The subject is already at the destination after the cut; do not show the journey.'),
    ('teleport', 'Teleport effect', 'The subject disappears from the starting position and reappears at the selected destination in a visible teleportation effect. Keep the camera steady.'),
)


def recipes():
    return [{'id': ident, 'name': name, 'instruction': text, 'experimental': ident in ('distance', 'acceleration'),
             'note': 'Numeric motion is a prompt request, not calibrated distance or speed.' if ident in ('distance', 'acceleration') else ''}
            for ident, name, text in PRESETS]


def prepare_experiment(experiment_id, project, settings, recipe_ids, seeds):
    safe_id(experiment_id)
    if (not isinstance(recipe_ids, list) or not recipe_ids or
            any(not isinstance(key, str) for key in recipe_ids) or len(recipe_ids) != len(set(recipe_ids))):
        raise MotionLabError('Choose distinct movement recipes.')
    options = {row['id']: row for row in recipes()}
    if any(not isinstance(key, str) or key not in options for key in recipe_ids):
        raise MotionLabError('Choose an available movement recipe.')
    if (not isinstance(seeds, list) or not seeds or
            any(type(seed) is not int or not 0 <= seed <= 2**53 - 1 for seed in seeds) or len(seeds) != len(set(seeds))):
        raise MotionLabError('Choose distinct whole-number seeds.')
    if len(seeds) * len(recipe_ids) > 12:
        raise MotionLabError('A comparison may contain at most twelve clips.')
    if not isinstance(settings, dict) or not isinstance(project, dict):
        raise MotionLabError('Provide a project and render settings.')
    baseline = copy.deepcopy(project)
    baseline['comfy_render'] = {**baseline.get('comfy_render', {}), **settings}
    frame_budget(baseline.get('duration'), baseline['comfy_render'])
    rows = []
    for seed in seeds:
        for recipe_id in recipe_ids:
            row_project = copy.deepcopy(baseline)
            row_project['comfy_render']['seed'] = seed
            instruction = options[recipe_id]['instruction']
            row_project['custom_instructions'] = (row_project.get('custom_instructions', '') + '\nMotion comparison instruction: ' + instruction).strip()
            result = compile_project(row_project)
            if not result['valid']:
                errors = [issue['message'] for issue in result['issues'] if issue['severity'] == 'error']
                raise MotionLabError('Fix the source project before comparing motion: ' + ' '.join(errors[:3]))
            ident = str(uuid.uuid5(uuid.UUID(experiment_id), recipe_id + ':' + str(seed)))
            rows.append({'request_id': ident, 'recipe_id': recipe_id, 'recipe_name': options[recipe_id]['name'],
                         'seed': seed, 'project': row_project, 'prompt': result['prompt'],
                         'prompt_sha256': hashlib.sha256(result['prompt'].encode()).hexdigest(), 'status': 'ready',
                         'run_id': None, 'ratings': {}, 'notes': ''})
    return rows


class MotionLabManager:
    def __init__(self, data_dir, video_manager):
        self.directory = Path(data_dir) / 'motion_lab'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.video_manager = video_manager
        self.lock = threading.RLock()

    def _path(self, ident):
        return self.directory / (safe_id(ident) + '.json')

    def _read(self, ident):
        try:
            return json.loads(self._path(ident).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise MotionLabError('That motion comparison was not found.') from exc

    def create(self, request_id, project, settings, recipe_ids, seeds):
        request_id = safe_id(request_id)
        payload = {'project': project, 'settings': settings, 'recipe_ids': recipe_ids, 'seeds': seeds}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self.lock:
            if self._path(request_id).exists():
                if self._read(request_id)['digest'] != digest:
                    raise MotionLabError('This comparison identifier already has different inputs.')
                return self.get(request_id)
            rows = prepare_experiment(request_id, project, settings, recipe_ids, seeds)
            record = {'id': request_id, 'digest': digest, 'created_at': time.time(), 'paused': False, 'items': rows}
            atomic_json(self._path(request_id), record)
        return self.get(request_id)

    def get(self, ident):
        """Read receipts and timings only. Never submits, retries or advances."""
        record = self._read(ident)
        video = self.video_manager()
        for item in record['items']:
            try:
                run = video.get(item['request_id'])
            except ValueError:
                run = None
            if run:
                item.update(run_id=run['id'], status=run['status'], run=run)
            item.pop('project', None)
        return record

    def advance(self, ident):
        with self.lock:
            record = self._read(ident)
            if record['paused']:
                raise MotionLabError('This comparison is stopped. Resume it explicitly before advancing.')
            video = self.video_manager()
            for item in record['items']:
                try:
                    run = video.get(item['request_id'])
                except ValueError:
                    run = None
                if run:
                    if run['status'] == 'succeeded':
                        continue
                    # Failed/cancelled/uncertain results require a user decision;
                    # do not silently skip them or create another request ID.
                    return self.get(ident)
                if item['status'] == 'skipped':
                    continue
                item['status'] = 'submitting'
                atomic_json(self._path(ident), record)
                run = video.submit(item['request_id'], item['project'], item['prompt'])
                item.update(status=run['status'], run_id=run['id'])
                atomic_json(self._path(ident), record)
                break
            return self.get(ident)

    def set_paused(self, ident, paused=True):
        if type(paused) is not bool:
            raise MotionLabError('Pause must be true or false.')
        with self.lock:
            record = self._read(ident)
            record['paused'] = paused
            atomic_json(self._path(ident), record)
            # Pause prevents further submissions; caller may separately cancel
            # the existing owned VideoRun using its ordinary Stop action.
            return self.get(ident)

    def rate(self, ident, request_id, ratings, notes=''):
        keys = {'direction', 'endpoint', 'identity', 'speech', 'ownership', 'continuity', 'unwanted_cut'}
        if not isinstance(ratings, dict) or set(ratings) - keys or any(value not in ('pass', 'fail', 'uncertain', 'not_applicable') for value in ratings.values()):
            raise MotionLabError('Use pass, fail, uncertain or not_applicable for each comparison rating.')
        if not isinstance(notes, str) or len(notes) > 2000:
            raise MotionLabError('Keep comparison notes under 2000 characters.')
        with self.lock:
            record = self._read(ident)
            item = next((row for row in record['items'] if row['request_id'] == request_id), None)
            if item is None:
                raise MotionLabError('That result does not belong to this comparison.')
            item.update(ratings=copy.deepcopy(ratings), notes=notes)
            atomic_json(self._path(ident), record)
            return self.get(ident)
