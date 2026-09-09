"""Ground final-frame observations without inferring nonvisual game outcomes."""
from __future__ import annotations

import copy

from jsonschema import Draft202012Validator

from .world import WorldError, apply_discoveries, apply_effects, validate_effects, validate_world


VISUAL_RULES = (
    'The image is a single final frame, not proof of the intended action. '
    'visible_effects is optional: use [] when no change is clearly visible or identity is uncertain. '
    'Use only the supplied stable IDs and only people/objects you can identify in this image. '
    'A visible object in a hand may set holder; a visibly worn garment may set worn_by. '
    'Use null only when the identified object is clearly no longer held/worn, never because it is missing or obscured. '
    'Use entity_location or character_location only when the known location is visually identifiable. '
    'The only visual object-state change allowed is open=true/false. '
    'Do not infer ownership, a hidden lock, health, death, defeat, unconsciousness, private knowledge, '
    'completed objectives, spoken words, sounds or lip sync from a still. '
    'Known descriptions and the intended plan identify candidates; they are not evidence. '
    'Report uncertainty in uncertainties instead of guessing, creating identities or applying intended effects.'
)


def _provisional_world(world, plan, player_id):
    """New records are candidates only; the caller still owns actual acceptance."""
    result = apply_discoveries(world, plan.get('discoveries'), player_id)
    by_id = {person['id']: person for person in result['characters']}
    by_name = {person['name'].casefold(): person for person in result['characters']}
    used = {record['id'] for group in ('characters', 'locations', 'entities', 'events', 'objectives')
            for record in result[group]}
    for person in plan.get('characters', []):
        if not isinstance(person, dict):
            continue
        key, name = person.get('id'), person.get('name')
        if not isinstance(key, str) or not isinstance(name, str) or not name.strip():
            # Legacy plans without IDs cannot safely identify a new person.
            continue
        if key in by_id:
            if by_id[key]['name'].casefold() != name.casefold():
                raise WorldError('An observed character ID belongs to another established person.')
            continue
        if key in used or name.casefold() in by_name:
            raise WorldError('An observed character cannot reuse an established world identity.')
        item = {'id': key, 'name': name, 'description': person.get('description', ''),
                'control': 'player' if key == player_id else 'npc',
                'location_id': result['current_location_id'], 'asset_ids': []}
        result['characters'].append(item)
        by_id[key] = item
        by_name[name.casefold()] = item
        used.add(key)
    return validate_world(result)


def _visual_schema(world):
    characters = [person['id'] for person in world['characters']]
    entities = [item['id'] for item in world['entities']]
    locations = [place['id'] for place in world['locations']]
    variants = []

    def enum(values, nullable=False):
        return {'type': ['string', 'null'] if nullable else 'string',
                'enum': values + ([None] if nullable else [])}

    def offer(kind, fields):
        props = {'kind': {'type': 'string', 'const': kind}, **fields}
        variants.append({'type': 'object', 'properties': props, 'required': list(props),
                         'additionalProperties': False})

    if entities:
        for kind in ('holder', 'worn_by'):
            offer(kind, {'entity_id': enum(entities), 'character_id': enum(characters, True)})
        offer('entity_state', {'entity_id': enum(entities), 'key': {'const': 'open', 'type': 'string'},
                               'value': {'type': 'boolean'}})
        if locations:
            offer('entity_location', {'entity_id': enum(entities), 'location_id': enum(locations)})
    if characters and locations:
        offer('character_location', {'character_id': enum(characters), 'location_id': enum(locations)})
    return {'type': 'array', 'maxItems': 16 if variants else 0,
            # Some local grammar builders reject the valid boolean JSON Schema
            # `items: false`. An ordinary object schema with maxItems=0 keeps
            # the same empty-array contract and uses their supported subset.
            'items': {'oneOf': variants} if variants else {
                'type': 'object', 'properties': {}, 'additionalProperties': False},
            'description': 'Only clearly visible changes with known IDs. Omit or use [] for uncertain changes.'}


def ending_output_budget(schema, baseline=1400):
    """Give a requested identity checklist bounded room in the same inspection."""
    count = schema.get('properties', {}).get('continuity_checks', {}).get('minItems', 0)
    count = count if type(count) is int and count > 0 else 0
    return min(4096, max(baseline, 400 + 100 * min(count, 56)))


def _continuity_schema(contract, require_coverage=False):
    contract = contract or {}
    variants = []
    for kind, group, key in (('actor', 'actors', 'subject_id'), ('object', 'objects', 'entity_id')):
        ids = list(dict.fromkeys(row[key] for row in contract.get(group, [])))
        if ids:
            variants.append({'type': 'object', 'additionalProperties': False,
                'required': ['kind', 'id', 'status', 'detail'], 'properties': {
                    'kind': {'const': kind}, 'id': {'type': 'string', 'enum': ids},
                    'status': {'type': 'string', 'enum': ['match', 'mismatch', 'uncertain']},
                    'detail': {'type': 'string', 'minLength': 1, 'maxLength': 240 if require_coverage else 500}}})
    result = {'type': 'array', 'maxItems': min(56, sum(len(contract.get(group, [])) for group in ('actors', 'objects'))),
              'items': {'anyOf': variants} if variants else {'type': 'object'}}
    if require_coverage:
        result['minItems'] = result['maxItems']
    return result


def observation_request(world, plan, player_id, base_schema, scene_contract=None, *, require_coverage=False):
    """Return extra public context and a copy of an existing observation schema.

    Passing the base schema avoids an import cycle with the story manager. The
    extra field stays optional for old models and saved supervised responses.
    """
    provisional = _provisional_world(world, plan, player_id)
    schema = copy.deepcopy(base_schema)
    if not isinstance(schema, dict) or not isinstance(schema.get('properties'), dict):
        raise ValueError('The ending observation needs an object response schema.')
    schema['properties']['visible_effects'] = _visual_schema(provisional)
    schema['properties']['continuity_checks'] = _continuity_schema(scene_contract, require_coverage)
    if require_coverage:
        schema['required'] = list(dict.fromkeys([*schema.get('required', []), 'continuity_checks']))
    public = lambda rows, keys: [{key: copy.deepcopy(row[key]) for key in keys if key in row} for row in rows]
    context = {
        'visual_effect_rules': VISUAL_RULES,
        'continuity_check_rules': 'When a final scene contract is supplied, optionally report continuity_checks for its actor/object IDs only. '
            'Compare final visible posture, placement, distinct instances and appearance/color to the intended endpoint. '
            'Use mismatch only for a clearly visible contradiction, uncertain for occluded or ambiguous evidence. '
            'The expected count and assignment are intent, not proof. A final frame cannot establish continuous stillness, a transfer, speech or sound. '
            'Missing or offscreen identities are not proof of absence. These checks never create objects or change accepted state.',
        'known_visual_candidates': {
            'characters': public(provisional['characters'], ('id', 'name', 'description')),
            'locations': public(provisional['locations'], ('id', 'name', 'description')),
            'entities': public(provisional['entities'], ('id', 'name', 'kind', 'description')),
        },
    }
    if require_coverage:
        context['continuity_check_rules'] = (
            'Report exactly one continuity_checks row for EVERY actor and object identity in the final scene contract. '
            'Use the supplied kind and ID once each; never omit an identity. Keep each detail to one brief evidence clause. '
            'Compare final visible posture, placement, distinct instances and appearance/color to the intended endpoint. '
            'Use mismatch only for a clearly visible contradiction; use uncertain when occluded, unidentifiable or ambiguous. '
            'The expected count and assignment are intent, not proof. A final frame cannot establish continuous stillness, '
            'a transfer, speech or sound. Missing or offscreen identities are not proof of absence. '
            'These checks never create objects or change accepted state.')
    return context, schema


def validate_observation(observation, world, plan, player_id, scene_contract=None, *, require_coverage=False):
    """Validate optional visible effects on a copy; never advance game state."""
    if not isinstance(observation, dict):
        raise WorldError('Ending inspection must return a structured observation.')
    result = copy.deepcopy(observation)
    if require_coverage and 'continuity_checks' not in result:
        raise WorldError('Ending inspection must assess every final scene identity; use uncertain when evidence is unclear.')
    if 'continuity_checks' in result:
        failure = next(Draft202012Validator(_continuity_schema(scene_contract, require_coverage)).iter_errors(result['continuity_checks']), None)
        if failure is not None:
            raise WorldError('Continuity checks must use final scene contract IDs, a supported status and concise visible evidence.')
        seen = set()
        for check in result['continuity_checks']:
            identity = (check['kind'], check['id'])
            if identity in seen or not check['detail'].strip():
                raise WorldError('Continuity checks need one clear assessment per final scene identity.')
            seen.add(identity)
        if require_coverage:
            expected = {('actor', row['subject_id']) for row in (scene_contract or {}).get('actors', [])}
            expected.update(('object', row['entity_id']) for row in (scene_contract or {}).get('objects', []))
            if seen != expected:
                raise WorldError('Ending inspection must assess every final scene identity exactly once; use uncertain when evidence is unclear.')
    if 'visible_effects' not in result:
        # Absence makes no factual claim and preserves existing response contracts.
        return result
    provisional = _provisional_world(world, plan, player_id)
    schema = _visual_schema(provisional)
    failure = next(Draft202012Validator(schema).iter_errors(result['visible_effects']), None)
    if failure is not None:
        raise WorldError('Visible effects must use known IDs and only visible holding, clothing, location or open/closed state.')
    effects = validate_effects(provisional, result['visible_effects'], player_id)
    values = {}
    for effect in effects:
        field = (effect['kind'], effect.get('entity_id') or effect.get('character_id'), effect.get('key'))
        if field in values and values[field] != effect:
            raise WorldError('Ending inspection reported contradictory changes to the same visible fact.')
        values[field] = effect
    # This catches combined assignments which are individually valid, e.g. a
    # coat worn by one person but held by another, before the outcome is offered.
    event_id = 'ending-observation-validation'
    used = {record['id'] for group in ('characters', 'locations', 'entities', 'events', 'objectives')
            for record in provisional[group]}
    while event_id in used:
        event_id += '-x'
    apply_effects(provisional, effects, event_id=event_id, actor_id=player_id,
                  witness_ids=[], summary='Validation only; never accepted.')
    result['visible_effects'] = effects
    return result
