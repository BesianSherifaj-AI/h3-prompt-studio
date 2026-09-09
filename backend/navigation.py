"""Accepted, branch-local image waypoints, not a simulation of 3-D space.

Only structured movement changes the logical coordinates. Saved images never
restore world state: an endpoint is reusable only when its canonical visual
state and presentation still match the proposed outcome. All functions are
local and pure except for their documented turn/accepted-state mutations.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

MAX_VIEWS = 256
_DIRECTIONS = {'left': (-1, 0), 'right': (1, 0), 'forward': (0, 1),
               'backward': (0, -1), 'back': (0, -1)}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _visual_hash(state):
    world = state.get('world', {})
    player_id = state.get('player_character_id')
    characters = []
    for row in world.get('characters', []):
        person = {key: copy.deepcopy(row.get(key)) for key in
                  ('id', 'name', 'description', 'asset_ids', 'location_id', 'state', 'control')}
        # The destination is checked separately; inventory/enemies remain in the hash.
        if row.get('id') == player_id:
            person['location_id'] = None
        characters.append(person)
    def ordered(rows):
        return sorted(rows, key=lambda row: row.get('id', ''))
    return _hash({'player': player_id, 'characters': ordered(characters),
                  'entities': ordered(world.get('entities', [])),
                  'locations': ordered(world.get('locations', [])),
                  'objectives': ordered(world.get('objectives', [])),
                  'rules': world.get('rules', [])})


def _presentation_hash(state):
    project = state.get('project', state.get('base_project', {}))
    settings = state.get('settings', {})
    # Assembly materializes these settings into the project. Compare the
    # effective controls, not whether that deterministic copy happened yet.
    style = copy.deepcopy(project.get('style', {}))
    style['notes'] = settings.get('style') or style.get('notes', '')
    refs = [copy.deepcopy(asset) for asset in project.get('assets', [])
            if asset.get('enabled', True) and not asset.get('video_run_ending')
            and asset.get('role') not in ('inspiration', 'unused')]
    for reference in refs:
        previous_role = reference.pop('movement_reference_role', None)
        if reference.get('role') == 'context' and previous_role == 'reference_image':
            reference['role'] = previous_role
    return _hash({'style': style,
                  'viewpoint': project.get('game_viewpoint', 'auto'),
                  'aspect_ratio': settings.get('aspect_ratio', project.get('aspect_ratio', '16:9')),
                  'references': refs})


def _location(state):
    player = next((row for row in state.get('world', {}).get('characters', [])
                   if row.get('id') == state.get('player_character_id')), {})
    return player.get('location_id', state.get('world', {}).get('current_location_id'))


def _position(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(n) is int and abs(n) <= 1_000_000 for n in value))


def _valid_navigation(value):
    return (isinstance(value, dict) and value.get('version') == 1
            and isinstance(value.get('frame_id'), str) and bool(value['frame_id'])
            and _position(value.get('position')) and isinstance(value.get('views'), list)
            and len(value['views']) <= MAX_VIEWS
            and all(isinstance(row, dict) and isinstance(row.get('run_id'), str)
                    and isinstance(row.get('frame_id'), str) and _position(row.get('position'))
                    and isinstance(row.get('visual_hash'), str)
                    and isinstance(row.get('presentation_hash'), str) for row in value['views']))


def _anchor(state, run_id, frame_id, position):
    return {'run_id': run_id, 'frame_id': frame_id, 'position': list(position),
            'location_id': _location(state), 'visual_hash': _visual_hash(state),
            'presentation_hash': _presentation_hash(state)}


def _lineage(story, turn):
    chain = story.get('branches', {}).get(turn.get('branch_id'), [])
    parent = turn.get('parent_run_id')
    if not isinstance(chain, list) or parent not in chain:
        return []
    return chain[:chain.index(parent) + 1]


def remember_bound_scene(state, run_id, *, accepted_state=None):
    """Label the current accepted image after an explicit ``This is me`` choice.

    This annotates navigation only, not historical state. If historical state is
    available, only the selected player's descriptive identity and a newly named
    observed place may differ. Draft inventory, enemy, reference or style changes
    must not be certified as visible in the old image by this operation.
    """
    annotation = state.get('player_visual_anchor', {})
    player_id = state.get('player_character_id')
    player = next((row for row in state.get('world', {}).get('characters', [])
                   if row.get('id') == player_id), None)
    if (not player or annotation.get('run_id') != run_id
            or annotation.get('description') != player.get('description')
            or player.get('state', {}).get('visual_anchor_run_id') != run_id
            or player['state'].get('visual_anchor') != player['description']):
        return False
    if accepted_state is not None:
        if accepted_state.get('player_character_id') != player_id:
            return False
        expected = copy.deepcopy(accepted_state)
        historical_player = next((row for row in expected.get('world', {}).get('characters', [])
                                  if row.get('id') == player_id), None)
        if historical_player is None:
            return False
        historical_player['description'] = player['description']
        for field in ('visual_anchor', 'visual_anchor_run_id'):
            historical_player.setdefault('state', {})[field] = player['state'][field]
        observed_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'h3-observed-place:' + run_id))
        places = expected['world'].get('locations', [])
        newly_observed = (expected['world'].get('current_location_id') is None
                          and _location(expected) is None and _location(state) == observed_id
                          and not any(row.get('id') == observed_id for row in places))
        if newly_observed:
            place = next((row for row in state['world'].get('locations', [])
                          if row.get('id') == observed_id), {})
            if (set(place) - {'id', 'name', 'description', 'asset_ids', 'exits'}
                    or place.get('name') != 'Observed scene' or place.get('asset_ids')
                    or place.get('exits') or not isinstance(place.get('description'), str)):
                return False
            places.append(copy.deepcopy(place))
            expected['world']['locations'] = places
            expected['world']['current_location_id'] = observed_id
            historical_player['location_id'] = observed_id
        if (_location(expected) != _location(state)
                or _visual_hash(expected) != _visual_hash(state)
                or _presentation_hash(expected) != _presentation_hash(state)):
            return False
    previous = state.get('navigation')
    if _valid_navigation(previous) and previous.get('current_run_id') == run_id:
        navigation = copy.deepcopy(previous)
    else:
        navigation = {'version': 1, 'frame_id': run_id, 'position': [0, 0],
                      'current_run_id': run_id, 'views': []}
    anchor = _anchor(state, run_id, navigation['frame_id'], navigation['position'])
    anchor['basis'] = 'explicit_player_binding'
    navigation['views'] = [row for row in navigation['views'] if row['run_id'] != run_id]
    navigation['views'] = (navigation['views'] + [anchor])[-MAX_VIEWS:]
    navigation['location_id'] = _location(state)
    state['navigation'] = navigation
    return True


def prepare_navigation(story, turn, snapshot):
    """Freeze movement metadata on ``turn``; never mutate the branch/snapshot."""
    lineage = _lineage(story, turn)
    parent = turn.get('parent_run_id')
    previous = snapshot.get('navigation')
    if _valid_navigation(previous) and previous.get('current_run_id') == parent:
        basis = copy.deepcopy(previous)
        basis['views'] = [row for row in basis['views'] if row['run_id'] in lineage]
    else:
        basis = {'version': 1, 'frame_id': parent or turn.get('logical_turn_id', turn['id']),
                 'position': [0, 0], 'location_id': _location(snapshot),
                 'current_run_id': parent, 'views': []}
        if parent in lineage:
            # Use the state actually accepted with that image, not later draft edits.
            historical = story.get('state_by_run', {}).get(parent) or snapshot
            basis['views'].append(_anchor(historical, parent, basis['frame_id'], [0, 0]))
    intent = turn.get('resolved_intent', {}).get('intent') or turn.get('intent', {})
    move = {'version': 1, 'logical_turn_id': turn.get('logical_turn_id', turn['id']),
            'branch_id': turn.get('branch_id'), 'parent_run_id': parent,
            'basis': basis, 'kind': 'stationary', 'from_position': list(basis['position']),
            'to_position': list(basis['position']), 'location_id': _location(snapshot),
            'status': 'not_applicable', 'reason': 'This turn is not an explicit saved-view movement.'}
    if intent.get('kind') == 'move':
        direction, extent = intent.get('direction'), intent.get('extent', 'step')
        if (intent.get('camera', 'player') == 'player'
                and intent.get('presentation', 'continuous') == 'continuous'
                and direction in _DIRECTIONS and extent in ('step', 'nearby')
                and not intent.get('target_id')):
            scale = 3 if extent == 'nearby' else 1
            delta = _DIRECTIONS[direction]
            move.update(kind='relative', direction=direction, extent=extent,
                        to_position=[basis['position'][i] + scale * delta[i] for i in (0, 1)],
                        status='prepared', reason='Logical viewpoint movement prepared; no ending accepted yet.')
        else:
            player = snapshot.get('player_character_id')
            arrivals = [effect.get('location_id') for effect in turn.get('resolved_intent', {}).get('effects', [])
                        if effect.get('kind') == 'character_location' and effect.get('character_id') == player]
            known = {row['id'] for row in snapshot.get('world', {}).get('locations', [])}
            if intent.get('camera', 'player') == 'player' and len(arrivals) == 1 and arrivals[0] in known:
                move.update(kind='location', location_id=arrivals[0], to_position=[0, 0],
                            status='prepared', reason='Travel to an existing named place prepared.')
            else:
                # Camera movement changes the coordinate frame. Unknown movement
                # must not later be mistaken for an inverse of an earlier step.
                move.update(kind='reset', status='not_applicable',
                            reason='This movement has no stable relative viewpoint or known arrival.')
    turn['navigation_move'] = move


def _projected_state(turn):
    from .world import apply_discoveries, apply_effects, world_from_project
    state = copy.deepcopy(turn['snapshot'])
    plan = turn.get('plan', {})
    player = state.get('player_character_id')
    world = apply_discoveries(state['world'], plan.get('discoveries'), player)
    if turn.get('project'):
        state['project'] = copy.deepcopy(turn['project'])
        world = world_from_project(state['project'], world, player)
    state['world'] = apply_effects(world, plan.get('effects', turn.get('resolved_intent', {}).get('effects', [])),
                                   event_id=turn.get('logical_turn_id', turn['id']), actor_id=player)
    return state


def navigation_return(story, turn):
    """Return a safe accepted endpoint run ID, or None with a readable reason.

Call after the final project/effects are available, before adding automatic
conditioning images. The caller still checks that the saved ending file exists
and that its H3 mode can use two frames. No image or world state is restored here.
"""
    move = turn.get('navigation_move')
    if not isinstance(move, dict) or move.get('kind') not in ('relative', 'location'):
        return None
    def skip(reason):
        move.pop('return_run_id', None)
        move.update(status='skipped', reason=reason)
        return None
    if (move.get('branch_id') != turn.get('branch_id')
            or move.get('parent_run_id') != turn.get('parent_run_id')
            or story.get('active_branch_id') != turn.get('branch_id')):
        return skip('The accepted branch changed; this saved viewpoint is stale.')
    lineage = _lineage(story, turn)
    if not lineage or turn.get('parent_run_id') != lineage[-1]:
        return skip('The starting ending is not in this accepted branch.')
    chain = story.get('branches', {}).get(turn.get('branch_id'), [])
    if chain[-1] not in (turn.get('parent_run_id'), turn.get('replace_run_id'), turn.get('reroll_of')):
        return skip('Another ending was accepted after this movement was prepared.')
    basis = move.get('basis')
    if not _valid_navigation(basis):
        return skip('Saved viewpoint metadata is invalid.')
    try:
        projected = _projected_state(turn)
        visual, presentation = _visual_hash(projected), _presentation_hash(projected)
    except (ValueError, KeyError, TypeError):
        return skip('The proposed world state is not ready for a saved-view comparison.')
    project = projected['project']
    if any(a.get('enabled', True) and not a.get('video_run_ending')
           and a.get('role') in ('first_frame', 'last_frame') for a in project.get('assets', [])):
        return skip('Explicit frame references take priority over a saved return ending.')
    if _location(projected) != move.get('location_id'):
        return skip('The planned destination changed; the prepared viewpoint is stale.')
    candidates = [row for row in reversed(basis['views'])
                  if row.get('location_id') == move.get('location_id')
                  and (move['kind'] == 'location' or
                       (row['frame_id'] == basis['frame_id'] and row['position'] == move['to_position']))]
    if not candidates:
        return skip('No accepted ending is saved at this logical destination yet.')
    for row in candidates:
        if row['run_id'] not in lineage:
            continue
        if row['visual_hash'] != visual or row['presentation_hash'] != presentation:
            continue
        move.update(status='ready', return_run_id=row['run_id'],
                    reason='Use the saved ending for this place; current world state is preserved.')
        return row['run_id']
    return skip('Saved endings at this destination belong to another branch or have different world state, style, viewpoint or references.')


def commit_navigation(before, turn, run_id, accepted):
    """Commit one accepted endpoint; rerolls replace its image, not its movement.

The caller must invoke this only in its acceptance transaction and persist the
result in accepted_state, state_by_run and the current branch's navigation.
"""
    move = turn.get('navigation_move')
    if not isinstance(move, dict) or not _valid_navigation(move.get('basis')):
        return  # Legacy accepted turns without a frozen movement remain legacy.
    basis = copy.deepcopy(move['basis'])
    location = _location(accepted)
    frame_id, position = basis['frame_id'], list(basis['position'])
    if move.get('kind') == 'relative' and location == move.get('location_id'):
        position = list(move['to_position'])
    elif move.get('kind') in ('reset', 'location') or location != basis.get('location_id'):
        frame_id, position = turn.get('logical_turn_id', turn['id']), [0, 0]
        # A known return resumes that location's established local coordinate frame.
        target = next((row for row in reversed(basis['views'])
                       if row['run_id'] == move.get('return_run_id') and row.get('location_id') == location), None)
        if target and move.get('kind') == 'location':
            frame_id, position = target['frame_id'], list(target['position'])
    anchor = _anchor(accepted, run_id, frame_id, position)
    anchor['logical_turn_id'] = move.get('logical_turn_id', turn['id'])
    views = [row for row in basis['views'] if row.get('logical_turn_id') != anchor['logical_turn_id']
             and row['run_id'] != run_id]
    views.append(anchor)
    accepted['navigation'] = {'version': 1, 'frame_id': frame_id, 'position': position,
                              'location_id': location, 'current_run_id': run_id,
                              'views': views[-MAX_VIEWS:]}
