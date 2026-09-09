"""Local projects, immutable AI boundaries, and small readable defaults."""
from __future__ import annotations
import copy
import json
import math
import re
import uuid
from decimal import Decimal
from pathlib import Path

DIRECTOR_LOCK_FIELDS = frozenset({
    'camera.framing', 'camera.movement', 'camera.height', 'camera.focus', 'camera.speed',
    'transition', 'setting', 'visible_subject_ids', 'offscreen_subject_ids', 'final_state', 'scene_contract',
})


def directed_structure(project):
    """Explicit scene controls belong to the user, including their timing."""
    simple = project.get('simple')
    return (isinstance(simple, dict) and simple.get('directed') is True) or any(
        s.get('director_locks') or ('scene_contract' in s and s.get('scene_contract_source') != 'generated')
        for s in project.get('shots', []) if isinstance(s, dict)
    )

def uid():
    return str(uuid.uuid4())

def shot(duration=5):
    return {'id': uid(), 'duration': duration, 'action': '', 'setting': '',
            'camera': {'framing': 'medium', 'movement': 'static', 'height': 'eye level', 'speed': 'slow', 'focus': ''},
            'performance': '', 'final_state': '', 'visible_subject_ids': [], 'offscreen_subject_ids': [],
            'dialogue': [], 'sound': '', 'transition': 'continuous'}

def new_project():
    return {'schema_version': 1, 'id': uid(), 'title': 'Untitled film', 'mode': 'ref2va',
            'duration': 5, 'aspect_ratio': '16:9', 'profile': 'director', 'authoring_mode': 'assisted',
            'story': {'text': '', 'locked': True},
            'style': {'genre': 'cinematic', 'vibe': '', 'lighting': '', 'color': '', 'notes': ''},
            'assets': [], 'subjects': [], 'shots': [shot()], 'soundscape': '', 'music': '',
            'custom_instructions': ''}

def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9-]{36}', value):
        raise ValueError('Invalid local identifier.')
    return str(uuid.UUID(value))

def check_project(project):
    """Validate an editable, client-renderable shape, not H3 semantic validity.

    Empty text, unknown choice strings, unresolved bindings and finite draft
    durations remain compiler concerns. Validation never repairs or rewrites
    source text, dialogue, IDs or optional extension data.
    """
    if not isinstance(project, dict) or type(project.get('schema_version')) is not int or project['schema_version'] != 1:
        raise ValueError('This is not a version 1 H3 Prompt Studio project.')
    safe_id(project.get('id'))
    try:
        encoded = json.dumps(project, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError('Project data must be ordinary JSON with finite numbers.') from exc
    if len(encoded) > 2_000_000:
        raise ValueError('Project is too large; images belong in the reference library.')

    def object_value(value, path):
        if not isinstance(value, dict):
            raise ValueError(f'{path} must be an object.')
        return value

    def text_fields(value, fields, path, required=()):
        for field in fields:
            if field in value or field in required:
                if not isinstance(value.get(field), str):
                    raise ValueError(f'{path}.{field} must be text.')

    def flags(value, fields, path):
        for field in fields:
            if field in value and type(value[field]) is not bool:
                raise ValueError(f'{path}.{field} must be true or false.')

    def number(value, path, nullable=False):
        if nullable and value is None:
            return
        try:
            finite = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f'{path} must be a finite number' + (' or null.' if nullable else '.'))

    def string_list(value, path):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f'{path} must be a list of text identifiers.')

    def text_object(value, path):
        object_value(value, path)
        if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
            raise ValueError(f'{path} must contain text fields only.')

    text_fields(project, ('title', 'mode', 'aspect_ratio', 'profile', 'authoring_mode',
                          'soundscape', 'music', 'custom_instructions'), 'project', required=('title', 'mode'))
    number(project.get('duration'), 'project.duration')
    story = object_value(project.get('story'), 'story')
    text_fields(story, ('text',), 'story', required=('text',))
    flags(story, ('locked',), 'story')
    text_object(project.get('style'), 'style')

    for key, limit in [('assets', 200), ('subjects', 32), ('shots', 24)]:
        values = project.get(key)
        if not isinstance(values, list) or len(values) > limit:
            raise ValueError(f'{key} must be a list with at most {limit} entries.')
        if key == 'shots' and not values:
            raise ValueError('shots must contain at least one editable shot.')
        for index, value in enumerate(values):
            path = f'{key}[{index}]'
            object_value(value, path)
            text_fields(value, ('id',), path, required=('id',))
            if key == 'assets':
                text_fields(value, ('name', 'media_type', 'role', 'semantic_role', 'description',
                                    'observation', 'approved_observation', 'filename', 'mime', 'sha256', 'prompt_tag'),
                            path, required=('name', 'media_type'))
                flags(value, ('enabled', 'locked_order', 'audio_enabled'), path)
                for field in ('duration', 'width', 'height'):
                    if field in value:
                        number(value[field], f'{path}.{field}', nullable=True)
            elif key == 'subjects':
                text_fields(value, ('name', 'description'), path, required=('name',))
                string_list(value.get('asset_ids'), path + '.asset_ids')
            else:
                number(value.get('duration'), path + '.duration')
                text_fields(value, ('action', 'setting', 'performance', 'final_state', 'sound', 'transition'), path)
                text_object(value.get('camera'), path + '.camera')
                for field in ('visible_subject_ids', 'offscreen_subject_ids'):
                    string_list(value.get(field), f'{path}.{field}')
                if 'director_locks' in value:
                    string_list(value['director_locks'], path + '.director_locks')
                    if any(field not in DIRECTOR_LOCK_FIELDS for field in value['director_locks']):
                        raise ValueError(f'{path}.director_locks contains an unsupported scene control.')
                dialogue = value.get('dialogue')
                if 'scene_contract' in value:
                    from .scene_contract import validate_scene_contract
                    problems = validate_scene_contract(value['scene_contract'])
                    if problems:
                        raise ValueError(f'{path}.scene_contract: ' + problems[0]['message'])
                if 'scene_contract_source' in value and value['scene_contract_source'] not in ('generated', 'authored'):
                    raise ValueError(f'{path}.scene_contract_source must be generated or authored.')
                if not isinstance(dialogue, list):
                    raise ValueError(f'{path}.dialogue must be a list of dialogue objects.')
                for line_index, line in enumerate(dialogue):
                    line_path = f'{path}.dialogue[{line_index}]'
                    object_value(line, line_path)
                    text_fields(line, ('id', 'speaker_id', 'language', 'text', 'delivery'), line_path,
                                required=('id', 'speaker_id', 'text'))
                    flags(line, ('locked', 'voiceover'), line_path)
    return project

def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)

ALLOWED_SHOT_FIELDS = {'action', 'setting', 'camera', 'performance', 'final_state', 'sound', 'transition', 'visible_subject_ids', 'offscreen_subject_ids', 'scene_contract'}

def merge_plan(project, proposal):
    """A model can suggest a plan but cannot replace source facts or exact dialogue."""
    result = copy.deepcopy(check_project(project))
    proposed = proposal.get('shots', [])
    if not isinstance(proposed, list) or not 1 <= len(proposed) <= 8:
        raise ValueError('AI must propose between one and eight shots.')
    subjects = {s['id'] for s in result['subjects']}
    preserve_structure = directed_structure(result)
    if preserve_structure and len(proposed) != len(result['shots']):
        raise ValueError('The assistant changed your number of scenes. Your draft is unchanged; try Make my prompt again to keep your scene controls.')
    durations = []
    for item in proposed:
        if not isinstance(item, dict):
            raise ValueError('AI shots must be structured objects.')
        duration = item.get('duration', 1)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('AI returned an invalid shot duration.')
        durations.append(Decimal(str(duration)))
    duration = result['duration']
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or int(duration) != duration or not 4 <= duration <= 15:
        raise ValueError('Choose an integer project duration from 4 through 15 seconds before planning.')
    # Give every shot at least one millisecond, then apportion the remaining
    # milliseconds by weight. Rounding only the last shot can make it negative.
    total_ms = int(duration * 1000)
    shares = [d / sum(durations) * (total_ms - len(durations)) for d in durations]
    shot_ms = [1 + int(value) for value in shares]
    remainder = total_ms - sum(shot_ms)
    order = sorted(range(len(shares)), key=lambda i: (shares[i] - int(shares[i]), -i), reverse=True)
    for index in order[:remainder]:
        shot_ms[index] += 1
    if preserve_structure:
        source_durations = [Decimal(str(s['duration'])) for s in result['shots']]
        if any(d <= 0 for d in source_durations) or abs(sum(source_durations) - Decimal(str(duration))) > Decimal('0.0005'):
            raise ValueError('Set your scene lengths to add up to the video duration before making the prompt.')
    new_shots = []
    for index, item in enumerate(proposed):
        source = result['shots'][index] if preserve_structure else None
        target = copy.deepcopy(source) if source is not None else shot(shot_ms[index] / 1000)
        target['dialogue'] = []
        for field in ALLOWED_SHOT_FIELDS:
            if field in item:
                target[field] = copy.deepcopy(item[field])
                if field == 'scene_contract':
                    target['scene_contract_source'] = 'generated'
        locks = set(source.get('director_locks', [])) if source is not None else set()
        if source is not None and 'scene_contract' in source and source.get('scene_contract_source') != 'generated':
            locks.add('scene_contract')
        for field in locks:
            if field.startswith('camera.'):
                if not isinstance(target.get('camera'), dict):
                    raise ValueError('AI returned an invalid camera object.')
                key = field.split('.', 1)[1]
                target['camera'][key] = copy.deepcopy(source['camera'].get(key, ''))
            else:
                default = {} if field == 'scene_contract' else ([] if field.endswith('_ids') else '')
                target[field] = copy.deepcopy(source.get(field, default))
                if field == 'scene_contract':
                    target['scene_contract_source'] = source.get('scene_contract_source', 'authored')
        for field in ('visible_subject_ids', 'offscreen_subject_ids'):
            ids = target[field]
            if not isinstance(ids, list) or not all(isinstance(v, str) and v in subjects for v in ids):
                raise ValueError('AI referenced a subject that is not in your project.')
        visible, offscreen = 'visible_subject_ids', 'offscreen_subject_ids'
        if 'scene_contract' in locks:
            contract_ids = [row['subject_id'] for row in target.get('scene_contract', {}).get('actors', [])]
            if any(sid not in subjects for sid in contract_ids):
                raise ValueError('Your authored scene continuity refers to an unknown character.')
            if (visible in locks and not set(contract_ids) <= set(source[visible])) or (offscreen in locks and set(contract_ids) & set(source[offscreen])):
                raise ValueError('Your authored physical scene continuity conflicts with the selected visible/off-screen characters. Resolve those scene controls before planning.')
            # Physical actor controls imply that actor is in the shot. A model
            # cannot silently remove the actor while the authored pose survives.
            target[offscreen] = [sid for sid in target[offscreen] if sid not in contract_ids]
            target[visible] += [sid for sid in contract_ids if sid not in target[visible]]
        if locks & {visible, offscreen}:
            if set(source[visible]) & set(source[offscreen]):
                raise ValueError('A character cannot be both in the scene and off-screen. Choose one place for each character in this scene.')
            # A selected roster wins over an assistant's contradictory roster.
            # Never discard a user-selected character to accommodate the model.
            if visible in locks and offscreen not in locks:
                target[offscreen] = [sid for sid in target[offscreen] if sid not in target[visible]]
            elif offscreen in locks and visible not in locks:
                target[visible] = [sid for sid in target[visible] if sid not in target[offscreen]]
            if set(target[visible]) & set(target[offscreen]):
                raise ValueError('A character cannot be both in the scene and off-screen. Check the selected characters for this scene.')
        if not isinstance(target['camera'], dict) or not all(isinstance(v, str) and len(v) <= 3000 for v in target['camera'].values()):
            raise ValueError('AI returned an invalid camera object.')
        for field in ('action', 'setting', 'performance', 'final_state', 'sound', 'transition'):
            if not isinstance(target[field], str) or len(target[field]) > 8000:
                raise ValueError(f'AI returned invalid {field} text.')
        new_shots.append(target)
    # Preserve every dialogue object, byte-for-byte, near its original timeline start.
    old_start = 0
    preserved_visibility = {}
    for old_index, old in enumerate(result['shots']):
        start = 0
        destination = new_shots[old_index] if preserve_structure else new_shots[-1]
        if not preserve_structure:
            for item in new_shots:
                if start + item['duration'] > old_start:
                    destination = item
                    break
                start += item['duration']
        destination['dialogue'].extend(copy.deepcopy(old.get('dialogue', [])))
        for d in old.get('dialogue', []):
            sid = d.get('speaker_id')
            if sid not in subjects:
                continue
            old_visible = sid in old.get('visible_subject_ids', [])
            old_offscreen = sid in old.get('offscreen_subject_ids', [])
            if old_visible and old_offscreen:
                raise ValueError('Resolve the source speaker\'s visible/off-screen conflict before planning.')
            field = 'visible_subject_ids' if old_visible else 'offscreen_subject_ids' if old_offscreen else None
            if field:
                key = (destination['id'], sid)
                if key in preserved_visibility and preserved_visibility[key] != field:
                    raise ValueError('AI merged different visible/off-screen states for retained dialogue into one shot. Keep those speaking shots separate.')
                preserved_visibility[key] = field
                other = 'offscreen_subject_ids' if field == 'visible_subject_ids' else 'visible_subject_ids'
                destination[other] = [value for value in destination[other] if value != sid]
                if sid not in destination[field]:
                    destination[field].append(sid)
        old_start += old['duration']
    result['shots'] = new_shots
    for field in ('soundscape', 'music'):
        if isinstance(proposal.get(field), str):
            result[field] = proposal[field][:8000]
    if isinstance(proposal.get('style'), dict):
        for key in result['style']:
            if isinstance(proposal['style'].get(key), str):
                result['style'][key] = proposal['style'][key][:3000]
    return check_project(result)

def merge_assist(project, shot_id, field, value):
    result = copy.deepcopy(check_project(project))
    if field not in ALLOWED_SHOT_FIELDS - {'visible_subject_ids', 'offscreen_subject_ids', 'transition', 'scene_contract'}:
        raise ValueError('This field is protected from AI replacement.')
    target = next((s for s in result['shots'] if s['id'] == shot_id), None)
    if not target:
        raise ValueError('The selected shot no longer exists.')
    if field == 'camera':
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise ValueError('Camera suggestion must be a structured object.')
        target[field].update({k: v[:2000] for k,v in value.items() if k in target[field]})
    elif not isinstance(value, str) or len(value) > 8000:
        raise ValueError('AI suggestion must be concise text.')
    else:
        target[field] = value
    return result
