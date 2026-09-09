"""Grounded common interactions: the game owns mechanics, the director stages them."""
from __future__ import annotations

import copy
import re

from .world import actor_context, apply_effects, resolve_intent


MECHANICAL = {'look', 'examine', 'inventory', 'take', 'drop', 'give', 'open', 'close', 'move'}


def read_only_inspection(world, player_id, message):
    """Recognize a whole named inspection, including a simple placement phrase.

    This intentionally leaves compound actions, pronouns and uncertain language
    to the creative planner. The suffix names placement; it cannot add an action.
    """
    text = message.strip().rstrip('.!').strip()
    match = re.fullmatch(r'(?:please\s+)?(?:I\s+)?(?:want to\s+)?(?:examine|inspect|look at)\s+(?:the\s+)?(.+)', text, re.I)
    if not match:
        return None
    targets = []
    for entity in actor_context(world, player_id)['visible_objects']:
        tail = re.fullmatch(re.escape(entity['name'].strip()) + r'(.*)', match[1], re.I)
        if not tail:
            continue
        suffix = tail[1]
        if suffix and not re.fullmatch(
                r'\s+(?:currently\s+)?(?:on|in|at|inside|beside|under|above|near)\s+'
                r'(?:the|a|an|my|your|her|his|its)\s+[\w-]+(?:\s+[\w-]+){0,3}', suffix, re.I):
            continue
        if re.search(r'\b(?:and|then|while|before|after|but|to|touch|take|pick|grab|drop|give|move|open|close)\b', suffix, re.I):
            continue
        targets.append(entity)
    return copy.deepcopy(targets[0]) if len(targets) == 1 else None


def mechanics_need_narrative(world, player_id, intent, *, guides=(), user_instructions=''):
    """Unknown authored conditions must be resolved before committing mechanics.

    No keyword classifier can establish that arbitrary prose is merely styling.
    Inventory is a read-only listing; other actions retain creative resolution
    whenever rules, active guides or custom instructions might qualify them.
    """
    if not intent or intent.get('kind') not in MECHANICAL or intent['kind'] == 'inventory':
        return False
    if world.get('rules') or str(user_instructions or '').strip():
        return True
    if any(isinstance(guide, dict) and guide.get('enabled', True) and str(guide.get('text', '')).strip() for guide in guides):
        return True
    actor = next((character for character in world['characters'] if character['id'] == player_id), {})
    # The core mechanic understands terminal actor status and ordinary doors;
    # it does not understand additional conditions such as curse/weight/health.
    known_actor = {'dead', 'defeated', 'unconscious', 'alive', 'status', 'visual_anchor', 'visual_anchor_run_id'}
    if set(actor.get('state', {})) - known_actor:
        return True
    if str(actor.get('state', {}).get('status', '')).casefold() not in ('', 'active', 'alive', 'healthy', 'normal', 'dead', 'defeated', 'unconscious'):
        return True
    target = next((entity for entity in world['entities'] if entity['id'] == intent.get('target_id')), {})
    return bool(set(target.get('state', {})) - {'locked', 'open', 'hidden', 'basis'})


def infer_simple_intent(world, player_id, message):
    """Match only unambiguous whole commands against visible named targets.

    Creative, compound, quoted and unknown requests remain with the roleplay
    model. No pronoun guessing or matching a name from another location.
    """
    text = message.strip().rstrip('.!').strip()
    if any(mark in text for mark in ('"', '“', '”', '?', ';', '\n')):
        return None
    text = re.sub(r'^(?:please\s+)?(?:I\s+)?(?:want to\s+)?', '', text, flags=re.I)
    inspection = read_only_inspection(world, player_id, message)
    if inspection:
        return {'kind': 'examine', 'target_id': inspection['id']}
    if text.casefold() in ('look around', 'look', 'wait', 'wait and observe'):
        return {'kind': 'look' if text.casefold().startswith('look') else 'wait'}
    if text.casefold() in ('inventory', 'open inventory', 'check inventory', 'check my inventory', 'show inventory'):
        return {'kind': 'inventory'}
    context = actor_context(world, player_id)
    candidates = context['visible_objects'] + context['visible_characters']
    if context.get('location'):
        available_places = {edge['target_id'] for edge in context['location'].get('exits', [])}
        candidates += [place for place in world['locations'] if place['id'] in available_places]
    verbs = {'pick up': 'take', 'pickup': 'take', 'take': 'take', 'grab': 'take', 'get': 'take', 'drop': 'drop',
             'examine': 'examine', 'inspect': 'examine', 'look at': 'examine', 'open': 'open', 'close': 'close',
             'go to': 'move', 'walk to': 'move', 'approach': 'move'}
    for verb, kind in verbs.items():
        match = re.fullmatch(re.escape(verb) + r'\s+(?:the\s+)?(.+)', text, re.I)
        if not match:
            continue
        targets = [row for row in candidates if row['name'].strip().casefold() == match[1].strip().casefold()]
        if len(targets) != 1:
            return None
        result = {'kind': kind, 'target_id': targets[0]['id']}
        if kind == 'move':
            result['extent'] = 'travel' if any(p['id'] == targets[0]['id'] for p in world['locations']) else 'nearby'
        return result
    return None


def mechanical_plan(world, player_id, intent, duration, *, guides=(), user_instructions='', current_setting=''):
    """Return exact mechanical consequences without inventing an NPC response."""
    if not intent or intent.get('kind') not in MECHANICAL:
        return None
    if mechanics_need_narrative(world, player_id, intent, guides=guides, user_instructions=user_instructions):
        return None
    context = actor_context(world, player_id)
    directional = intent.get('kind') == 'move' and not intent.get('target_id')
    if not context.get('location') and not directional:
        return None  # The creative planner first establishes a text-only world.
    resolved = resolve_intent(world, player_id, intent)
    kind = resolved['intent']['kind']
    actor = context['actor']
    targets = {row['id']: row for row in world['entities'] + world['characters'] + world['locations']}
    target = targets.get(intent.get('target_id'), {})
    name, object_name = actor['name'], target.get('name', '')
    action = resolved['action']
    if kind == 'take':
        action = f'{name} reaches for {object_name}, grasps it and lifts it into their hand.'
    elif kind == 'drop':
        action = f'{name} lowers {object_name} onto the nearby ground and releases it. Nobody picks it up during this turn.'
    elif kind == 'give':
        recipient = targets[intent['recipient_id']]['name']
        action = f'{name} hands {object_name} to {recipient}, who receives and holds it.'
    elif kind in ('open', 'close'):
        action = f'{name} {"opens" if kind == "open" else "closes"} {object_name} with a deliberate hand movement.'
    elif kind == 'examine':
        action = f'{name} looks closely at {object_name}, keeping their hands still. No person or object changes place or holder.'
    elif kind == 'look':
        action = f'{name} slowly looks around the current scene. People and objects remain in their current places.'
    elif kind == 'wait':
        action = f'{name} waits and watches quietly, breathing naturally. The current scene and possessions remain unchanged.'
    preview_id = 'mechanical-preview'
    used = {record['id'] for group in ('characters', 'entities', 'locations', 'events', 'objectives')
            for record in world[group]}
    while preview_id in used:
        preview_id += '-x'
    proposed = apply_effects(world, resolved['effects'], event_id=preview_id, actor_id=player_id)
    changed_actor = next(c for c in proposed['characters'] if c['id'] == player_id)
    place = next((p for p in proposed['locations'] if p['id'] == changed_actor['location_id']),
                 {'name': 'Current scene', 'description': str(current_setting or '')[:2400]})
    cast = [c for c in proposed['characters'] if c['location_id'] == changed_actor['location_id'] or c['id'] == player_id]
    names = {c['id']: c['name'] for c in proposed['characters']}
    ending = f'{name} is in {place["name"]}.'
    if directional:
        axis = {'forward': 'farther into the current view, away from the viewer',
                'backward': 'closer to the viewer', 'left': 'farther to screen-left', 'right': 'farther to screen-right'}
        if intent.get('camera', 'player') == 'camera':
            ending += ' The viewpoint has shifted; every person retains their world position and appearance.'
        else:
            ending += f' {name} finishes the step {axis[intent.get("direction", "forward")]}, visibly displaced relative to the nearby fixed ground and architecture, with the same appearance.'
    changed_item = next((e for e in proposed['entities'] if e['id'] == target.get('id')), None)
    if changed_item:
        holder = changed_item['holder_id'] or changed_item['worn_by_id']
        ending += f' {changed_item["name"]} is ' + (f'held by {names[holder]}.' if holder else 'unheld in its current location.')
        if 'open' in changed_item['state']:
            ending += ' It is ' + ('open.' if changed_item['state']['open'] else 'closed.')
    if not resolved['effects']:
        ending += ' All established possessions and character states remain unchanged.'
    setting = place['name'] + (': ' + place['description'] if place.get('description') else '')
    return {'action': action, 'setting': setting, 'final_state': ending,
            'transition': 'cut' if changed_actor['location_id'] != actor['location_id'] else 'continue',
            'beats': [{'id': 'beat-1', 'action': action, 'setting': setting, 'final_state': ending}],
            'dialogue': [], 'characters': [{'id': c['id'], 'name': c['name'], 'description': c['description'],
                                           'voice': c.get('speaking_style', '')} for c in cast],
            'effects': copy.deepcopy(resolved['effects']), 'asset_requests': [],
            'choices': [{'title': 'Look around', 'message': 'I look around.'},
                        {'title': 'Check inventory', 'message': 'I check my inventory.'},
                        {'title': 'Wait', 'message': 'I wait and observe.'}]}
