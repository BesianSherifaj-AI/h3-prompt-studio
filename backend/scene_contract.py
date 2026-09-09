"""Portable, bounded scene staging; prose compilation requires no inference.

IDs identify instances, not object categories. A coin described by two references
is still one coin; two separately registered coins remain two distinct props.
The contract describes generation intent, never evidence of rendered success.
"""
from __future__ import annotations

import copy
import json
from collections import Counter

from jsonschema import Draft202012Validator


MAX_ACTORS = 32
MAX_OBJECTS = 24
MAX_CONTRACT_CHARS = 18000


def director_output_budget(schema, baseline=1400):
    """Allow each requested shot to finish its cast/staging JSON.

    A live three-shot, three-actor request exhausted the old flat 1,400-token
    allowance. This local estimate scales with the actual bounded output shape,
    without another inference or tokenization request. It is a ceiling, not a
    request to write more; the transport's 4,096-token safety limit still applies.
    """
    shots = schema.get('properties', {}).get('shots', {})
    count = shots.get('minItems', 1)
    count = count if type(count) is int and count > 0 else 1
    actors = shots.get('items', {}).get('properties', {}).get('scene_contract', {}).get('properties', {}).get('actors', {})
    cast = actors.get('maxItems', 0)
    cast = cast if type(cast) is int and cast >= 0 else 0
    return min(4096, max(baseline, 500 + min(count, 32) * (350 + 150 * min(cast, MAX_ACTORS))))


def scene_contract_schema(subject_ids=None, required=False):
    def obj(properties, fields=None):
        return {'type': 'object', 'properties': properties,
                'required': list(properties) if fields is None else fields,
                'additionalProperties': False}

    text = {'type': 'string', 'maxLength': 500}
    identifier = {'type': 'string', 'minLength': 1, 'maxLength': 160}
    actor_id = copy.deepcopy(identifier)
    if subject_ids:
        actor_id['enum'] = list(dict.fromkeys(subject_ids))
    actor = obj({'subject_id': actor_id,
                 'activity': {'type': 'string', 'enum': ['act', 'hold']},
                 'start': text, 'action': text, 'end': text})
    prop = obj({'entity_id': identifier, 'name': {'type': 'string', 'minLength': 1, 'maxLength': 120},
                'description': text, 'count': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                'start': text, 'end': text})
    properties = {'actors': {'type': 'array', 'maxItems': min(len(subject_ids), MAX_ACTORS)
                            if subject_ids is not None else MAX_ACTORS, 'items': actor},
                  'objects': {'type': 'array', 'maxItems': MAX_OBJECTS, 'items': prop},
                  'environment': {'type': 'string', 'maxLength': 1000},
                  'background_activity': text}
    return obj(properties, None if required else [])


def validate_scene_contract(contract, subject_ids=None, visible_ids=None, offscreen_ids=()):
    """Return compiler-style issues, with paths relative to scene_contract."""
    issues = []
    def issue(code, path, message):
        issues.append({'severity': 'error', 'code': code, 'path': path, 'message': message})

    try:
        encoded = json.dumps(contract, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        issue('invalid_scene_contract', '', 'Scene continuity must contain ordinary finite JSON.')
        return issues
    if len(encoded) > MAX_CONTRACT_CHARS:
        issue('scene_contract_budget', '', 'Scene continuity is too long. Keep staging focused on this shot.')
        return issues
    failure = next(Draft202012Validator(scene_contract_schema()).iter_errors(contract), None)
    if failure is not None:
        path = '.'.join(str(part) for part in failure.absolute_path)
        issue('invalid_scene_contract', path, 'Invalid scene continuity: ' + failure.message[:240])
        return issues
    known = set(subject_ids) if subject_ids is not None else None
    visible = set(visible_ids) if visible_ids is not None else None
    offscreen = set(offscreen_ids)
    seen = set()
    for index, row in enumerate(contract.get('actors', [])):
        sid = row['subject_id']
        if known is not None and sid not in known:
            issue('unknown_contract_actor', f'actors[{index}].subject_id', 'Scene continuity refers to an unknown character.')
        elif (visible is not None and sid not in visible) or sid in offscreen:
            issue('offscreen_contract_actor', f'actors[{index}].subject_id', 'Physical staging belongs to a visible character. Keep off-screen voices in the off-screen roster.')
        if sid in seen:
            issue('duplicate_contract_actor', f'actors[{index}].subject_id', 'Describe each visible character once.')
        seen.add(sid)
    seen = set()
    for index, row in enumerate(contract.get('objects', [])):
        if row['entity_id'] in seen:
            issue('duplicate_contract_object', f'objects[{index}].entity_id', 'Describe each object identity once; use count for an authored group.')
        seen.add(row['entity_id'])
    return issues


def contract_texts(contract):
    """Yield authored prose for the compiler's existing tag/syntax validation."""
    for key in ('environment', 'background_activity'):
        if key in contract:
            yield key, contract[key]
    for group, fields in (('actors', ('start', 'action', 'end')),
                          ('objects', ('name', 'description', 'start', 'end'))):
        for index, row in enumerate(contract.get(group, [])):
            for field in fields:
                yield f'{group}[{index}].{field}', row[field]


def map_contract_texts(contract, transform):
    result = copy.deepcopy(contract)
    for path, value in contract_texts(result):
        if '[' not in path:
            result[path] = transform(value, path)
        else:
            group, rest = path.split('[', 1)
            index, field = rest.split('].', 1)
            result[group][int(index)][field] = transform(value, path)
    return result


def render_scene_contract(project, shot, name_fn):
    """Render positive, local instructions without guessing poses or colors.

    A legacy shot still gets identity/count and restrained fallback direction.
    Explicit staging adds concrete starts, actions and ends; absent fields do
    not authorize invention or overwrite a first/last-frame composition.
    """
    contract = shot.get('scene_contract') or {}
    visible = shot.get('visible_subject_ids', [])
    pov_id = project.get('game_player_id') if project.get('game_viewpoint') == 'pov' else None
    bodies = [sid for sid in visible if sid != pov_id]
    paragraphs = []
    if bodies:
        paragraphs.append(f"Principal cast in this shot: exactly {len(bodies)} separate "
                          + ('individual' if len(bodies) == 1 else 'individuals') + ', '
                          + '; '.join(name_fn(sid) for sid in bodies)
                          + '. Each named identity occurs once, with its own established appearance, colors and clothing. '
                          'Multiple references to one identity depict the same individual.')
    elif visible and pov_id:
        paragraphs.append('The player supplies the viewpoint and their own hands only; no separate player body appears in the scene.')
    if contract.get('environment', '').strip():
        paragraphs.append('Scene layout and appearance: ' + contract['environment'].strip().rstrip('.') + '.')
    if contract.get('background_activity', '').strip():
        paragraphs.append('Background activity: ' + contract['background_activity'].strip().rstrip('.') + '.')
    elif visible:
        paragraphs.append('Background figures already established in the frame keep their positions with quiet idle motion. '
                          'Surrounding space remains free of additional foreground characters.')
    actor_rows = {row['subject_id']: row for row in contract.get('actors', [])}
    for sid in visible:
        if sid == pov_id:
            paragraphs.append('The viewpoint character uses only their own hands for the assigned action; retain the first-person framing without introducing an external view of their body.')
            continue
        label = name_fn(sid)
        row = actor_rows.get(sid)
        if not row:
            paragraphs.append(label + ' performs only the action assigned to that character in this shot; '
                              'between those actions, maintain the established posture and position, with only incidental motion appropriate to the recorded physical condition. '
                              'Keep gestures and handled props attached to the correct character.')
            continue
        parts = [label + ':']
        if row['start'].strip():
            parts.append('Starts ' + row['start'].strip().rstrip('.') + '.')
        if row['activity'] == 'hold':
            parts.append('Maintains this established posture and place throughout the shot. Limit motion to the assigned performance '
                         'and approved speech, preserving the recorded physical condition; other characters perform their own actions.')
        if row['action'].strip():
            parts.append('Assigned performance: ' + row['action'].strip().rstrip('.') + '.')
        if row['end'].strip():
            parts.append('Ends ' + row['end'].strip().rstrip('.') + '.')
        paragraphs.append(' '.join(parts))
    props = contract.get('objects', [])
    duplicates = Counter(row['name'].casefold() for row in props)
    for index, row in enumerate(props):
        label = row['name'] + (f' (distinct prop {index + 1})' if duplicates[row['name'].casefold()] > 1 else '')
        parts = [f"{label}: exactly {row['count']} physical " + ('instance' if row['count'] == 1 else 'instances') + '.']
        if row['description'].strip():
            parts.append('Appearance: ' + row['description'].strip().rstrip('.') + '.')
        if row['start'].strip():
            parts.append('At the start: ' + row['start'].strip().rstrip('.') + '.')
        if row['end'].strip():
            parts.append('At the end: ' + row['end'].strip().rstrip('.') + '.')
        paragraphs.append(' '.join(parts))
    if props:
        paragraphs.append('Track these same physical objects through only the assigned action; preserve each count, color, shape and identity '
                          'unless that action explicitly changes it. An approved handoff moves the existing prop and leaves the former hand '
                          'empty of that prop. Hidden or occluded items keep their established placement without being brought into view.')
    return paragraphs
