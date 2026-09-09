"""Pure, branch-local world state. No inference, files, or speculative commits.

Public functions return copies. The caller persists a returned world only when the
owning story turn is accepted; event IDs make that acceptance idempotent.
"""
from __future__ import annotations

import copy
import json
import uuid


class WorldError(ValueError):
    pass


def _id(value, label='identifier'):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise WorldError(f'Use a nonempty {label} of at most 160 characters.')
    return value


def _strings(value, label):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise WorldError(f'{label} must be a list of text values.')
    return value


def _optional_id(value):
    if value is None or value == '':
        return None
    return _id(value)


def _stable(kind, value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'h3-studio:{kind}:{value}'))


def validate_world(world):
    """Validate references and normalize optional fields without changing IDs."""
    if not isinstance(world, dict) or type(world.get('schema_version')) is not int or world['schema_version'] != 1:
        raise WorldError('Use a version 1 story world.')
    try:
        encoded = json.dumps(world, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise WorldError('World state must contain ordinary finite JSON.') from exc
    if len(encoded) > 2_000_000:
        raise WorldError('The world is too large; keep image files in the asset library.')
    result = copy.deepcopy(world)
    groups = ('locations', 'entities', 'characters', 'objectives', 'events')
    all_ids = set()
    for group in groups:
        result.setdefault(group, [])
        if not isinstance(result[group], list) or len(result[group]) > (10000 if group == 'events' else 2000):
            raise WorldError(f'{group} must be a bounded list.')
        for item in result[group]:
            if not isinstance(item, dict):
                raise WorldError(f'Every {group} entry must be an object.')
            key = _id(item.get('id'))
            if key in all_ids:
                raise WorldError('Every world record needs a unique stable identifier.')
            all_ids.add(key)
            if group == 'objectives':
                item.setdefault('name', item.get('title', ''))
                item.setdefault('title', item['name'])
                if item.setdefault('status', 'active') not in ('active', 'completed', 'failed'):
                    raise WorldError('An objective status must be active, completed or failed.')
            if group != 'events' and not isinstance(item.get('name'), str):
                raise WorldError(f'Every {group} entry needs a name.')
    locations = {x['id'] for x in result['locations']}
    characters = {x['id'] for x in result['characters']}
    event_ids = {x['id'] for x in result['events']}
    current = _optional_id(result.setdefault('current_location_id', None))
    result['current_location_id'] = current
    if current is not None and current not in locations:
        raise WorldError('The current location is missing from this world.')
    rules = result.setdefault('rules', [])
    if isinstance(rules, str):
        result['rules'] = [rules] if rules.strip() else []
    else:
        _strings(rules, 'World rules')
    for location in result['locations']:
        location.setdefault('description', '')
        if not isinstance(location['description'], str):
            raise WorldError('Location description must be text.')
        _strings(location.setdefault('asset_ids', []), 'Location references')
        exits = location.setdefault('exits', [])
        if not isinstance(exits, list):
            raise WorldError('Location exits must be a list.')
        for i, item in enumerate(exits):
            if isinstance(item, str):
                item = exits[i] = {'target_id': item, 'label': ''}
            if not isinstance(item, dict) or not isinstance(item.get('target_id'), str) or item['target_id'] not in locations:
                raise WorldError('A location exit points to a missing place.')
            if not isinstance(item.get('label', ''), str) or type(item.get('locked', False)) is not bool:
                raise WorldError('A location exit needs a text label and an on/off locked flag.')
    for character in result['characters']:
        character.setdefault('control', 'npc')
        if character['control'] not in ('player', 'npc'):
            raise WorldError('Character control must be player or npc.')
        if not isinstance(character.setdefault('state', {}), dict):
            raise WorldError('Character state must be an object.')
        for field in ('description', 'personality', 'speaking_style'):
            character.setdefault(field, '')
            if not isinstance(character[field], str):
                raise WorldError(f'Character {field} must be text.')
        if isinstance(character.get('goals'), str):
            character['goals'] = [character['goals']] if character['goals'].strip() else []
        for field in ('goals', 'asset_ids', 'witnessed_events'):
            _strings(character.setdefault(field, []), f'Character {field}')
        if any(e not in event_ids for e in character['witnessed_events']):
            raise WorldError('A character remembers an event missing from this branch.')
        knowledge = character.setdefault('private_knowledge', [])
        if isinstance(knowledge, str):
            knowledge = character['private_knowledge'] = [knowledge] if knowledge.strip() else []
        if not isinstance(knowledge, list) or not all(isinstance(x, (str, dict)) for x in knowledge):
            raise WorldError('Private knowledge must be a list of facts.')
        for fact in knowledge:
            if isinstance(fact, dict):
                if not isinstance(fact.get('fact'), str):
                    raise WorldError('A private knowledge record needs fact text.')
                if fact.get('source_event_id') is not None and fact['source_event_id'] not in event_ids:
                    raise WorldError('A private fact references an event outside this branch.')
        if isinstance(character.get('relationships'), str):
            character['relationships'] = {'_notes': character['relationships']}
        if not isinstance(character.setdefault('relationships', {}), dict):
            raise WorldError('Character relationships must be an object keyed by character ID.')
        if any(k not in characters and k != '_notes' for k in character['relationships']):
            raise WorldError('A relationship references a missing character.')
        character['location_id'] = _optional_id(character.get('location_id', current))
        if character['location_id'] not in locations | {None}:
            raise WorldError('A character is assigned to a missing location.')
    for entity in result['entities']:
        entity.setdefault('kind', 'object')
        if not isinstance(entity['kind'], str):
            raise WorldError('Entity kind must be text.')
        if not isinstance(entity.get('description', ''), str):
            raise WorldError('Object description must be text.')
        entity['location_id'] = _optional_id(entity.get('location_id', current))
        if entity['location_id'] not in locations | {None}:
            raise WorldError('An object is assigned to a missing location.')
        for key in ('holder_id', 'worn_by_id', 'owner_id'):
            entity[key] = _optional_id(entity.get(key))
            if entity[key] not in characters | {None}:
                raise WorldError(f'An object has an unknown {key}.')
        if entity['holder_id'] and entity['worn_by_id'] and entity['holder_id'] != entity['worn_by_id']:
            raise WorldError('An outfit cannot be held by a different person while worn.')
        _strings(entity.setdefault('asset_ids', []), 'Object references')
        _strings(entity.setdefault('affordances', []), 'Object interactions')
        if not isinstance(entity.setdefault('state', {}), dict):
            raise WorldError('Object state must be an object.')
    for objective in result['objectives']:
        if type(objective.get('private', False)) is not bool:
            raise WorldError('Objective privacy must be on or off.')
        known_by = _strings(objective.get('known_by', []), 'Objective known_by')
        if any(cid not in characters for cid in known_by):
            raise WorldError('An objective is known by a missing character.')
    for event in result['events']:
        _strings(event.setdefault('witness_ids', []), 'Event witnesses')
        if any(x not in characters for x in event['witness_ids']):
            raise WorldError('An event has an unknown witness.')
        event.setdefault('summary', '')
        event.setdefault('basis', 'accepted_intent')
        if not isinstance(event['summary'], str) or not isinstance(event['basis'], str):
            raise WorldError('Event summary and basis must be text.')
        # Historical IDs can describe earlier state. Validate the record shapes
        # consumed by actor context without replaying past effects on today's world.
        effects = event.get('effects', [])
        if not isinstance(effects, list) or not all(isinstance(effect, dict) and isinstance(effect.get('kind'), str) for effect in effects):
            raise WorldError('Event effects must be a list of structured effects.')
        dialogue = event.get('dialogue', [])
        if not isinstance(dialogue, list) or not all(isinstance(line, dict) and isinstance(line.get('text'), str) for line in dialogue):
            raise WorldError('Event dialogue must be a list of lines with exact text.')
    return result


def world_from_project(project, previous=None, player_character_id=None):
    """Import editable references while retaining accepted state and knowledge.

    Imported IDs derive from existing IDs. A description never guesses a room
    layout or invents links between independently uploaded location images.
    """
    result = validate_world(previous) if previous is not None else {
        'schema_version': 1, 'current_location_id': None, 'locations': [],
        'entities': [], 'characters': [], 'objectives': [], 'rules': [], 'events': [],
    }
    cast = {x['id']: x for x in result['characters']}
    identity_assets = {a['id'] for a in project.get('assets', []) if a.get('semantic_role') in ('face', 'character')}
    for subject in project.get('subjects', []):
        key = _id(subject['id'])
        if key not in cast:
            character = {'id': key, 'name': subject['name'], 'description': subject.get('description', ''),
                         'asset_ids': list(subject.get('asset_ids', [])), 'location_id': result['current_location_id'],
                         'control': 'player' if key == player_character_id else 'npc'}
            for field in ('personality', 'goals', 'speaking_style', 'private_knowledge', 'relationships'):
                if field in subject:
                    character[field] = copy.deepcopy(subject[field])
            result['characters'].append(character)
            cast[key] = character
        else:
            cast[key].update(name=subject['name'], description=subject.get('description', ''),
                             asset_ids=list(subject.get('asset_ids', [])))
        if player_character_id is not None:
            cast[key]['control'] = 'player' if key == player_character_id else 'npc'
        cast[key]['identity_asset_ids'] = [aid for aid in cast[key]['asset_ids'] if aid in identity_assets]
    if player_character_id is not None and player_character_id not in cast:
        raise WorldError('Choose a player from the game characters.')
    locations_by_asset = {a: x for x in result['locations'] for a in x.get('asset_ids', [])}
    entities_by_asset = {a: x for x in result['entities'] for a in x.get('asset_ids', [])}
    for asset in project.get('assets', []):
        semantic = asset.get('semantic_role')
        if asset.get('media_type') != 'image' or asset.get('video_run_ending'):
            continue
        aid = asset['id']
        description = asset.get('approved_observation') or asset.get('description') or asset.get('observation', '')
        if semantic == 'background' and aid not in locations_by_asset:
            place = {'id': _stable('location', aid), 'name': asset.get('name', 'Location'),
                     'description': description, 'asset_ids': [aid], 'exits': []}
            result['locations'].append(place)
            locations_by_asset[aid] = place
            if result['current_location_id'] is None and asset.get('enabled', True) and asset.get('role') != 'context':
                result['current_location_id'] = place['id']
                for character in result['characters']:
                    if character.get('location_id') is None:
                        character['location_id'] = place['id']
        if semantic in ('object', 'wardrobe') and aid not in entities_by_asset:
            assigned = asset.get('simple_owner_id')
            if assigned not in cast:
                assigned = next((c['id'] for c in cast.values() if aid in c.get('asset_ids', [])), None)
            entity = {'id': _stable('entity', aid), 'name': asset.get('name', 'Object'), 'kind': semantic,
                      'description': description, 'asset_ids': [aid], 'location_id': result['current_location_id'],
                      'owner_id': assigned, 'holder_id': None, 'worn_by_id': assigned if semantic == 'wardrobe' else None,
                      'affordances': ['examine', 'touch'] + (['take', 'give', 'drop'] if semantic == 'object' else []),
                      'state': {'basis': 'user_reference_assignment'}}
            result['entities'].append(entity)
            entities_by_asset[aid] = entity
    return validate_world(result)


def project_from_world(project, world):
    """Copy names/appearance/assignments into the draft, preserving all controls."""
    world = validate_world(world)
    result = copy.deepcopy(project)
    subjects = {x['id']: x for x in result.get('subjects', [])}
    for character in world['characters']:
        target = subjects.get(character['id'])
        if target is None:
            target = {'id': character['id']}
            result.setdefault('subjects', []).append(target)
        target.update(name=character['name'], description=character['description'], asset_ids=list(character['asset_ids']))
        for field in ('personality', 'goals', 'speaking_style'):
            target[field] = copy.deepcopy(character[field])
    assignments = {aid: entity for entity in world['entities'] for aid in entity['asset_ids']}
    for asset in result.get('assets', []):
        if asset['id'] in assignments:
            entity = assignments[asset['id']]
            if asset.get('semantic_role') == 'object':
                asset['simple_owner_id'] = entity['owner_id'] or ''
                asset['current_holder_id'] = entity['holder_id']
            else:
                asset.pop('simple_owner_id', None)
                asset.pop('current_holder_id', None)
    return result


def _records(world):
    return ({x['id']: x for x in world['characters']}, {x['id']: x for x in world['entities']},
            {x['id']: x for x in world['locations']})


def character_can_act(character):
    state = character.get('state', {})
    return not (state.get('dead') is True or state.get('defeated') is True
                or state.get('unconscious') is True or state.get('alive') is False
                or str(state.get('status', '')).casefold() in ('dead', 'defeated', 'unconscious'))


ASSIGNMENT_FIELDS = {'holder', 'holder_id', 'held_by', 'worn_by', 'worn_by_id', 'owner', 'owner_id', 'location', 'location_id'}


def apply_discoveries(world, discoveries, player_character_id=None):
    """Validate newly established places/props on a copy, before any render.

    Stable IDs are part of the accepted plan. Existing records can only change
    through effects/editor updates; discovery never overwrites earlier facts.
    """
    result = validate_world(world)
    if discoveries is None:
        return result
    if not isinstance(discoveries, dict) or set(discoveries) - {'locations', 'entities'}:
        raise WorldError('Discoveries must contain new locations and entities only.')
    used = {row['id'] for group in ('locations', 'entities', 'characters', 'events', 'objectives') for row in result[group]}
    for group in ('locations', 'entities'):
        rows = discoveries.get(group, [])
        if not isinstance(rows, list) or len(rows) > 4:
            raise WorldError('Discover at most four new places and four new objects per turn.')
        for row in rows:
            if not isinstance(row, dict):
                raise WorldError('Each discovery must be a named record.')
            key = _id(row.get('id'))
            if key in used:
                raise WorldError('A discovery cannot replace an existing world record or reuse its ID.')
            used.add(key)
            if not isinstance(row.get('name'), str) or not row['name'].strip() or len(row['name']) > 120:
                raise WorldError('Each discovery needs a short, nonempty name.')
            if not isinstance(row.get('description', ''), str) or len(row.get('description', '')) > 2400:
                raise WorldError('Keep discovery descriptions concise.')
            allowed = {'id', 'name', 'description', 'exits', 'from_location_id'} if group == 'locations' else {'id', 'name', 'description', 'kind', 'location_id', 'affordances', 'state'}
            if set(row) - allowed:
                raise WorldError('A discovery contains unsupported fields; use effects for assignments.')
            item = copy.deepcopy(row)
            item['asset_ids'] = []
            if group == 'entities':
                item.setdefault('kind', 'object')
                item.setdefault('location_id', result['current_location_id'])
                state = item.setdefault('state', {})
                if isinstance(state, dict) and set(state) & ASSIGNMENT_FIELDS:
                    raise WorldError('Use holder, worn_by, owner or entity_location effects for assignments, not object state aliases such as held_by.')
                if not isinstance(state, dict) or len(state) > 24 or any(not isinstance(k, str) or not k or k.startswith('_') or not isinstance(v, (str, int, float, bool, type(None))) for k, v in state.items()):
                    raise WorldError('Object discoveries need a small set of ordinary scalar state fields.')
                item.setdefault('affordances', ['examine', 'take', 'drop', 'give'] if item['kind'] in ('object', 'item', 'weapon', 'key', 'tool') else ['examine'])
            result[group].append(item)
        if group == 'locations' and result['current_location_id'] is None and rows:
            result['current_location_id'] = rows[0]['id']
            for character in result['characters']:
                if character['location_id'] is None:
                    character['location_id'] = result['current_location_id']
    places = {item['id']: item for item in result['locations']}
    for row in discoveries.get('locations', []):
        destination = places[row['id']]
        source_id = destination.pop('from_location_id', None)
        if source_id is not None:
            if source_id not in places or source_id == row['id']:
                raise WorldError('A discovered route must connect two known, different places.')
            source = places[source_id]
            source.setdefault('exits', []).append({'target_id': destination['id'], 'label': 'Go to ' + destination['name']})
            if not any(edge.get('target_id') == source_id if isinstance(edge, dict) else edge == source_id for edge in destination.setdefault('exits', [])):
                destination['exits'].append({'target_id': source_id, 'label': 'Return to ' + source['name']})
    return validate_world(result)


def available_actions(world, actor_id, target_id=None):
    world = validate_world(world)
    cast, entities, places = _records(world)
    if actor_id not in cast:
        raise WorldError('Choose a player character before interacting.')
    actor = cast[actor_id]
    place = actor['location_id']
    targets, actions = [], []
    def offer(kind, label, target=None, enabled=True, reason=''):
        if not character_can_act(actor) and kind not in ('look', 'examine', 'inventory', 'wait'):
            enabled, reason = False, 'Your character cannot act in the current state.'
        action = {'kind': kind, 'label': label, 'enabled': bool(enabled), 'reason': reason}
        if target is not None:
            action['target_id'] = target
        actions.append(action)
    if target_id is None:
        offer('look', 'Look around')
        offer('wait', 'Wait and observe')
        offer('inventory', 'Check inventory')
    for character in world['characters']:
        if character['id'] == actor_id or character['location_id'] != place:
            continue
        targets.append({'id': character['id'], 'name': character['name'], 'kind': 'character'})
        if target_id in (None, character['id']):
            offer('examine', f'Examine {character["name"]}', character['id'])
            active = character_can_act(character)
            offer('talk', f'Talk to {character["name"]}', character['id'], active, '' if active else 'This character cannot respond in the current state.')
            offer('attack', f'Attack {character["name"]}', character['id'], active, '' if active else 'This character is already unable to fight.')
    for entity in world['entities']:
        holder = entity['holder_id'] or entity['worn_by_id']
        present = (cast[holder]['location_id'] == place) if holder else entity['location_id'] == place
        if not present or entity['state'].get('hidden') is True:
            continue
        targets.append({'id': entity['id'], 'name': entity['name'], 'kind': entity['kind']})
        if target_id not in (None, entity['id']):
            continue
        held_actions = ['drop', 'give'] if entity['holder_id'] == actor_id and not entity['worn_by_id'] else []
        for kind in dict.fromkeys(['examine'] + entity['affordances'] + held_actions):
            enabled, reason = True, ''
            if kind == 'take' and holder:
                enabled, reason = False, 'Someone is holding or wearing it; ask them first.'
            elif kind in ('give', 'drop') and entity['holder_id'] != actor_id:
                enabled, reason = False, 'You must be holding it first.'
            elif kind in ('give', 'drop') and entity['worn_by_id']:
                enabled, reason = False, 'Remove the worn item before giving or dropping it.'
            elif kind == 'open' and (entity['state'].get('locked') or entity['state'].get('open')):
                enabled, reason = False, 'It is locked.' if entity['state'].get('locked') else 'It is already open.'
            elif kind == 'close' and not entity['state'].get('open'):
                enabled, reason = False, 'It is already closed.'
            offer(kind, f'{kind.capitalize()} {entity["name"]}', entity['id'], enabled, reason)
    for edge in places.get(place, {}).get('exits', []):
        destination = places[edge['target_id']]
        targets.append({'id': destination['id'], 'name': destination['name'], 'kind': 'location'})
        if target_id in (None, destination['id']):
            offer('move', edge.get('label') or f'Go to {destination["name"]}', destination['id'],
                  not edge.get('locked', False), 'This route is locked.' if edge.get('locked') else '')
    if target_id and target_id not in {x['id'] for x in targets}:
        raise WorldError('That target is not available in the current scene.')
    return {'targets': targets, 'actions': actions}


def resolve_intent(world, actor_id, intent):
    """Resolve a supported button into grounded proposed effects, never apply it."""
    world = validate_world(world)
    if not isinstance(intent, dict):
        raise WorldError('An interaction must be a structured intent.')
    clean = copy.deepcopy(intent)
    aliases = {'grab': 'take', 'get': 'take', 'ask': 'talk', 'return': 'move', 'inspect': 'examine', 'kill': 'attack'}
    kind = aliases.get(clean.get('kind'), clean.get('kind'))
    target = clean.get('target_id') or None
    cast, entities, places = _records(world)
    if actor_id not in cast:
        raise WorldError('Choose a player character before interacting.')
    if not character_can_act(cast[actor_id]) and kind not in ('look', 'examine', 'inventory', 'wait'):
        raise WorldError('Your character cannot act in the current state.')
    if kind == 'move' and target in {**cast, **entities}:
        # A visible door or NPC is a local destination, not a location ID.
        available_actions(world, actor_id, target)
        if clean.get('extent', 'nearby') not in ('step', 'nearby') or clean.get('presentation', 'continuous') == 'teleport':
            raise WorldError('Use a local step or approach for a person or object; travel needs a connected location.')
        speed, camera = clean.get('speed', 'normal'), clean.get('camera', 'player')
        if speed not in ('slow', 'normal', 'fast') or camera not in ('player', 'camera') or clean.get('presentation', 'continuous') not in ('continuous', 'cut'):
            raise WorldError('Choose a supported local movement pace, subject and presentation.')
        who = 'The camera' if camera == 'camera' else cast[actor_id]['name']
        pace = {'slow': 'slowly', 'normal': 'at a natural pace', 'fast': 'quickly'}[speed]
        destination = (cast.get(target) or entities[target])['name']
        action = f'{who} approaches {destination} {pace}, remaining in the current location.'
        if clean.get('extent') == 'step':
            action = f'{who} takes one short step toward {destination} {pace}, remaining in the current location.'
        if camera == 'camera':
            action += ' All characters remain in place.'
        return {'intent': {**clean, 'kind': kind}, 'action': action, 'effects': []}
    if kind == 'move' and target is None:
        cast, _, _ = _records(world)
        if actor_id not in cast:
            raise WorldError('Choose a player character before moving.')
        extent, direction = clean.get('extent', 'step'), clean.get('direction', 'forward')
        speed, camera = clean.get('speed', 'normal'), clean.get('camera', 'player')
        if extent not in ('step', 'nearby'):
            raise WorldError('Choose a connected destination for travel. A local step does not need one.')
        if direction not in ('forward', 'backward', 'left', 'right') or speed not in ('slow', 'normal', 'fast') or camera not in ('player', 'camera'):
            raise WorldError('Choose a supported direction, motion pace and moving subject.')
        if clean.get('presentation', 'continuous') not in ('continuous', 'cut'):
            raise WorldError('Teleportation needs a known destination.')
        who = 'The camera' if camera == 'camera' else cast[actor_id]['name']
        amount = 'one short step' if extent == 'step' else 'a short distance'
        pace = {'slow': 'slowly', 'normal': 'at a natural pace', 'fast': 'quickly'}[speed]
        action = f'{who} moves {direction} {amount} {pace}, within the current location.'
        if camera == 'camera':
            action += ' The characters stay in their current positions.'
        clean['kind'] = kind
        return {'intent': clean, 'action': action, 'effects': []}
    choices = available_actions(world, actor_id, target)
    candidate = next((x for x in choices['actions'] if x['kind'] == kind and x.get('target_id') == target), None)
    if candidate is None:
        raise WorldError('This action is not available for that target. Edit its interactions or choose another action.')
    if not candidate['enabled']:
        raise WorldError(candidate['reason'])
    cast, entities, places = _records(world)
    effects = []
    if kind == 'attack':
        candidate = {**candidate, 'label': f'{cast[actor_id]["name"]} attempts to attack {cast[target]["name"]}. Resolve resistance and consequences from the established state and world rules; success is not automatic.'}
    elif kind == 'inventory':
        names = [item['name'] for item in world['entities'] if actor_id in (item['holder_id'], item['worn_by_id'])]
        candidate = {**candidate, 'label': f'{cast[actor_id]["name"]} checks their current inventory: ' + (', '.join(names) if names else 'empty') + '. No item changes hands.'}
    elif kind == 'take':
        effects = [{'kind': 'holder', 'entity_id': target, 'character_id': actor_id}]
    elif kind == 'drop':
        effects = [{'kind': 'holder', 'entity_id': target, 'character_id': None},
                   {'kind': 'entity_location', 'entity_id': target, 'location_id': cast[actor_id]['location_id']}]
    elif kind == 'give':
        recipient = clean.get('recipient_id')
        if recipient == actor_id or recipient not in cast or cast[recipient]['location_id'] != cast[actor_id]['location_id'] or not character_can_act(cast[recipient]):
            raise WorldError('Choose another character in this scene to receive the object.')
        effects = [{'kind': 'holder', 'entity_id': target, 'character_id': recipient}]
    elif kind in ('open', 'close'):
        effects = [{'kind': 'entity_state', 'entity_id': target, 'key': 'open', 'value': kind == 'open'}]
    elif kind == 'move':
        extent, speed = clean.get('extent', 'travel'), clean.get('speed', 'normal')
        camera, presentation = clean.get('camera', 'player'), clean.get('presentation', 'continuous')
        if extent not in ('step', 'nearby', 'travel') or speed not in ('slow', 'normal', 'fast'):
            raise WorldError('Choose step, nearby or travel and a slow, normal or fast motion pace.')
        if camera not in ('player', 'camera') or presentation not in ('continuous', 'cut', 'teleport'):
            raise WorldError('Choose whether the player or camera moves and how the movement is shown.')
        who, destination = cast[actor_id]['name'], places[target]['name']
        pace = {'slow': 'slowly', 'normal': 'at a natural pace', 'fast': 'quickly'}[speed]
        if camera == 'camera':
            action = f'The camera moves {pace} toward {destination}; {who} remains in the current location.'
        elif presentation == 'teleport':
            action = f'{who} visibly teleports to {destination}.'
            effects = [{'kind': 'character_location', 'character_id': actor_id, 'location_id': target}]
        elif extent == 'step':
            action = f'{who} takes one step {pace} toward {destination}, remaining in the current location.'
        elif extent == 'nearby':
            action = f'{who} approaches the route to {destination} {pace}, without arriving there yet.'
        else:
            action = f'{who} moves to {destination} {pace}.'
            effects = [{'kind': 'character_location', 'character_id': actor_id, 'location_id': target}]
        if presentation == 'cut' and camera == 'player':
            action += ' Show the action using a clear scene cut.'
        candidate = {**candidate, 'label': action}
    clean['kind'] = kind
    return {'intent': clean, 'action': candidate['label'], 'effects': validate_effects(world, effects, actor_id)}


def validate_effects(world, effects, actor_id=None):
    world = validate_world(world)
    cast, entities, places = _records(world)
    if actor_id is not None and actor_id not in cast:
        raise WorldError('The acting character is missing.')
    if not isinstance(effects, list) or len(effects) > 32:
        raise WorldError('A turn can propose at most 32 structured effects.')
    objectives = {x['id'] for x in world['objectives']}
    clean = copy.deepcopy(effects)
    for effect in clean:
        if not isinstance(effect, dict):
            raise WorldError('Every effect must be an object.')
        kind = effect.get('kind')
        if kind in ('holder', 'worn_by', 'owner', 'entity_location', 'entity_state'):
            if effect.get('entity_id') not in entities:
                raise WorldError('An effect targets an unknown object.')
            if kind in ('holder', 'worn_by', 'owner') and effect.get('character_id') not in set(cast) | {None}:
                raise WorldError('An assignment targets an unknown character.')
            if kind == 'entity_location' and effect.get('location_id') not in set(places) | {None}:
                raise WorldError('An effect targets an unknown location.')
            if kind == 'entity_state':
                _id(effect.get('key'), 'state field')
                if effect['key'] in ASSIGNMENT_FIELDS:
                    raise WorldError('Use an assignment effect instead of storing holders or locations in object state.')
                if effect['key'].startswith('_') or 'value' not in effect:
                    raise WorldError('Choose an ordinary object state field and value.')
        elif kind == 'character_state':
            if effect.get('character_id') not in cast:
                raise WorldError('A state change targets an unknown character.')
            _id(effect.get('key'), 'state field')
            if effect['key'].startswith('_') or 'value' not in effect or not isinstance(effect['value'], (str, int, float, bool, type(None))):
                raise WorldError('Choose an ordinary character state field and scalar value.')
        elif kind == 'character_location':
            if effect.get('character_id') not in cast or effect.get('location_id') not in places:
                raise WorldError('Movement must connect an existing character and location.')
        elif kind == 'knowledge':
            if effect.get('character_id') not in cast or not isinstance(effect.get('fact'), str) or not effect['fact'].strip():
                raise WorldError('Knowledge needs an existing character and a concrete fact.')
        elif kind == 'objective':
            if effect.get('objective_id') not in objectives or effect.get('status') not in ('active', 'completed', 'failed'):
                raise WorldError('Objective updates need an existing objective and valid status.')
        else:
            raise WorldError('The assistant proposed an unsupported world effect.')
    try:
        json.dumps(clean, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorldError('World effects must be finite JSON.') from exc
    # Check the combined proposed state, allowing a remove-then-give sequence
    # while rejecting conflicting holders before an expensive render starts.
    proposed = copy.deepcopy(world)
    proposed_cast, proposed_entities, _ = _records(proposed)
    for effect in clean:
        kind = effect['kind']
        if kind in ('holder', 'worn_by', 'owner'):
            proposed_entities[effect['entity_id']][{'holder': 'holder_id', 'worn_by': 'worn_by_id', 'owner': 'owner_id'}[kind]] = effect.get('character_id')
        elif kind == 'entity_location':
            proposed_entities[effect['entity_id']]['location_id'] = effect.get('location_id')
        elif kind == 'character_location':
            proposed_cast[effect['character_id']]['location_id'] = effect['location_id']
    validate_world(proposed)
    return clean


def apply_effects(world, effects, *, event_id, actor_id=None, summary='', witness_ids=None, dialogue=()):
    result = validate_world(world)
    event_id = _id(event_id, 'accepted event identifier')
    previous = next((x for x in result['events'] if x['id'] == event_id), None)
    if previous is not None:
        if previous.get('effects') != effects or previous.get('actor_id') != actor_id:
            raise WorldError('This accepted event identifier already has different effects.')
        return result
    clean = validate_effects(result, effects, actor_id)
    cast, entities, places = _records(result)
    witnesses = list(dict.fromkeys(witness_ids if witness_ids is not None else ([actor_id] if actor_id else [])))
    if any(x not in cast for x in witnesses):
        raise WorldError('An event witness is not part of this branch.')
    for effect in clean:
        kind = effect['kind']
        if kind in ('holder', 'worn_by', 'owner'):
            entities[effect['entity_id']][{'holder': 'holder_id', 'worn_by': 'worn_by_id', 'owner': 'owner_id'}[kind]] = effect.get('character_id')
        elif kind == 'entity_location':
            entities[effect['entity_id']]['location_id'] = effect.get('location_id')
        elif kind == 'entity_state':
            entities[effect['entity_id']]['state'][effect['key']] = copy.deepcopy(effect['value'])
        elif kind == 'character_state':
            cast[effect['character_id']]['state'][effect['key']] = copy.deepcopy(effect['value'])
        elif kind == 'character_location':
            character = cast[effect['character_id']]
            character['location_id'] = effect['location_id']
            if character['control'] == 'player':
                result['current_location_id'] = effect['location_id']
        elif kind == 'knowledge':
            cast[effect['character_id']]['private_knowledge'].append({'fact': effect['fact'], 'source_event_id': event_id})
        elif kind == 'objective':
            next(x for x in result['objectives'] if x['id'] == effect['objective_id'])['status'] = effect['status']
    lines = copy.deepcopy(list(dialogue))
    for line in lines:
        if not isinstance(line, dict) or line.get('speaker_id') not in cast or not isinstance(line.get('text'), str):
            raise WorldError('Accepted dialogue needs an existing speaker ID and exact text.')
    result['events'].append({'id': event_id, 'actor_id': actor_id, 'summary': summary, 'dialogue': lines,
                             'effects': clean, 'witness_ids': witnesses, 'basis': 'accepted_intent'})
    for cid in witnesses:
        cast[cid]['witnessed_events'].append(event_id)
    return validate_world(result)


def actor_context(world, actor_id, *, recent_limit=8):
    """Project only what this actor knows; redact other characters' hidden state."""
    world = validate_world(world)
    cast, entities, places = _records(world)
    if actor_id not in cast:
        raise WorldError('The requested actor does not exist.')
    actor = cast[actor_id]
    witnessed = set(actor['witnessed_events'])
    events = []
    for event in world['events']:
        if event['id'] in witnessed or actor_id in event['witness_ids']:
            public = {k: copy.deepcopy(event[k]) for k in ('id', 'summary', 'basis')}
            public['dialogue'] = copy.deepcopy(event.get('dialogue', []))
            # The narrative summary is public to witnesses, but another actor's
            # knowledge effect may be private even during that same event.
            public['effects'] = [copy.deepcopy(e) for e in event.get('effects', [])
                                 if e.get('kind') != 'knowledge' or e.get('character_id') == actor_id]
            events.append(public)
    visible = [c for c in world['characters'] if c['location_id'] == actor['location_id'] and c['id'] != actor_id]
    others = [{k: copy.deepcopy(c[k]) for k in ('id', 'name', 'description', 'asset_ids', 'location_id', 'state')}
              for c in visible]
    objects = []
    for entity in world['entities']:
        holder = entity['holder_id'] or entity['worn_by_id']
        location = cast[holder]['location_id'] if holder else entity['location_id']
        if location == actor['location_id'] and not entity['state'].get('hidden'):
            objects.append({k: copy.deepcopy(v) for k, v in entity.items() if k not in ('private_knowledge', 'secrets')})
    return {'actor': copy.deepcopy(actor), 'location': copy.deepcopy(places.get(actor['location_id'])),
            'visible_characters': others, 'visible_objects': objects, 'rules': copy.deepcopy(world['rules']),
            'recent_events': events[-max(0, recent_limit):] if recent_limit else [],
            'objectives': [copy.deepcopy(o) for o in world['objectives']
                           if not o.get('private', False) or actor_id in o.get('known_by', [])]}


def trim_context(context, count_tokens, token_budget, *, required_keys=()):
    """Trim only named historical/retrieval lists; never silently discard rules.

    count_tokens receives the serialized candidate. The caller must subtract
    system/schema/output/image allowance from the loaded context beforehand.
    """
    if type(token_budget) is not int or token_budget <= 0:
        raise WorldError('The assistant needs a positive available context budget.')
    result = copy.deepcopy(context)
    removed = {}
    def cost():
        return count_tokens(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    initial = cost()
    for field in ('retrieved_events', 'recent_events', 'earlier_dialogue', 'reference_descriptions'):
        if field in required_keys or not isinstance(result.get(field), list):
            continue
        while result[field] and cost() > token_budget:
            result[field].pop(0)
            removed[field] = removed.get(field, 0) + 1
    remaining = cost()
    if remaining > token_budget:
        raise WorldError('The required character, instructions and current action do not fit this model context. Increase context or choose another assistant; the draft is preserved.')
    return result, {'initial_tokens': initial, 'input_tokens': remaining, 'budget_tokens': token_budget, 'removed': removed}
