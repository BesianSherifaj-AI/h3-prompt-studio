"""Branch-local editable state and frozen execution contracts for story sessions."""
from __future__ import annotations
import copy
import hashlib
import json
import time
import uuid

from .projects import check_project, new_project


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preserve_edits(before, edited, accepted):
    """Apply draft changes over accepted effects without reverting untouched state."""
    if edited == before:
        return copy.deepcopy(accepted)
    if all(isinstance(x, dict) for x in (before, edited, accepted)):
        result = copy.deepcopy(accepted)
        for key in before.keys() - edited.keys():
            result.pop(key, None)
        for key, value in edited.items():
            if key not in before:
                result[key] = copy.deepcopy(value)
            elif value != before[key]:
                result[key] = preserve_edits(before[key], value, accepted.get(key, before[key]))
        return result
    if all(isinstance(x, list) for x in (before, edited, accepted)) and all(
            isinstance(v, dict) and 'id' in v for seq in (before, edited, accepted) for v in seq):
        old = {v['id']: v for v in before}; changed = {v['id']: v for v in edited}
        new = {v['id']: v for v in accepted}
        result = [preserve_edits(old[v['id']], v, new.get(v['id'], old[v['id']]))
                  if v['id'] in old else copy.deepcopy(v) for v in edited]
        result.extend(copy.deepcopy(v) for v in accepted if v['id'] not in old and v['id'] not in changed)
        return result
    return copy.deepcopy(edited)


def guides_for(value):
    if not isinstance(value, list) or len(value) > 40:
        raise ValueError('Keep at most 40 story instructions.')
    result, seen = [], set()
    for guide in value:
        if not isinstance(guide, dict):
            raise ValueError('Each instruction needs text and a scope.')
        item = copy.deepcopy(guide)
        item.setdefault('id', str(uuid.uuid4()))
        item.setdefault('revision', 1)
        item.setdefault('enabled', True)
        if (not isinstance(item['id'], str) or item['id'] in seen or
                type(item['revision']) is not int or item['revision'] < 1 or
                type(item['enabled']) is not bool or item.get('scope') not in ('next', 'persistent') or
                not isinstance(item.get('text'), str) or not item['text'].strip() or len(item['text']) > 3000):
            raise ValueError('Instructions need unique IDs, text, a revision and Next response or From now on.')
        seen.add(item['id']); result.append(item)
    return result


class StoryStateMixin:
    def _ensure_state(self, story):
        from .world import world_from_project
        if story.get('state_version') == 2:
            return
        project = {**new_project(), **copy.deepcopy(story.get('base_project', {}))}
        for person in project['subjects']:
            person.setdefault('id', str(uuid.uuid5(uuid.NAMESPACE_URL, str(story.get('id')) + ':' + person['name'])))
            person.setdefault('description', '')
            person.setdefault('asset_ids', [])
        if story.get('active_run_id'):
            project = copy.deepcopy(self.videos().snapshot(story['active_run_id']))
        player = next((p for p in project['subjects'] if p['name'].casefold() == story.get('player_name', '').casefold()), None)
        if not player and story.get('player_name'):
            player = {'id': str(uuid.uuid4()), 'name': story['player_name'], 'description': '', 'asset_ids': []}
            project['subjects'].append(player)
        player_id = player['id'] if player else None
        state = {'project': project, 'world': world_from_project(project, player_character_id=player_id),
                 'guides': [], 'player_character_id': player_id, 'premise': story.get('premise', ''),
                 'player_name': story.get('player_name', ''), 'settings': copy.deepcopy(story.get('settings', {})),
                 'configuration_revision': 1}
        story.update(state_version=2, branch_states={story['active_branch_id']: state}, state_by_run={})

    def _state(self, story):
        self._ensure_state(story)
        return story['branch_states'][story['active_branch_id']]

    def _execution(self, story, turn):
        state = turn.get('snapshot') or self._state(story)
        return {**story, **copy.deepcopy(state), 'base_project': copy.deepcopy(state['project'])}

    def _snapshot_turn(self, story, turn, body):
        from .world import resolve_intent
        state = self._state(story)
        if 'expected_parent' in body and body['expected_parent'] != story.get('active_run_id'):
            raise ValueError('The story ending changed. Review the latest scene before sending this move.')
        if body.get('configuration_revision', state['configuration_revision']) != state['configuration_revision']:
            raise ValueError('Your story settings changed. Reload the saved editor before sending this move.')
        snapshot = copy.deepcopy(state)
        turn.update(snapshot=snapshot, configuration_revision=state['configuration_revision'], logical_turn_id=turn['id'])
        turn['intent'] = copy.deepcopy(body.get('intent') or {'kind': 'freeform'})
        if turn['intent'].get('kind') != 'freeform':
            turn['resolved_intent'] = resolve_intent(snapshot['world'], snapshot['player_character_id'], turn['intent'])
        turn['receipt'] = {'request_id': turn['request_id'], 'intent': turn['intent'], 'message': turn['message'],
                           'parent_run_id': turn.get('parent_run_id'), 'configuration_revision': state['configuration_revision'],
                           'compiler_version': '1.2.0', 'created_at': time.time()}

    def edit_state(self, story, body):
        from .world import world_from_project, validate_world, project_from_world
        state = copy.deepcopy(self._state(story))
        expected = body.get('expected_configuration_revision', state['configuration_revision'])
        if expected != state['configuration_revision']:
            raise ValueError('These settings changed in another window. Reload before saving your draft.')
        if 'project' in body:
            state['project'] = copy.deepcopy(check_project(body['project']))
        if 'player_character_id' in body:
            state['player_character_id'] = body['player_character_id']
        if 'world' in body:
            state['world'] = validate_world(body['world'])
        else:
            state['world'] = world_from_project(state['project'], state['world'], state.get('player_character_id'))
        if 'world' in body:
            state['project'] = project_from_world(state['project'], state['world'])
        if 'guides' in body:
            incoming = guides_for(body['guides'])
            old = {g['id']: g for g in state['guides']}
            for guide in incoming:
                previous = old.get(guide['id'])
                if previous and any(guide.get(k) != previous.get(k) for k in ('text', 'scope', 'enabled')):
                    guide['revision'] = max(guide['revision'], previous['revision'] + 1)
            state['guides'] = incoming
        for key in ('premise', 'player_name', 'settings'):
            if key in body:
                state[key] = copy.deepcopy(story[key])
        if isinstance(body.get('settings'), dict) and 'style' in body['settings']:
            state['project']['style'].update(notes=state['settings']['style'], visual_style=state['settings']['style'])
        if 'player_character_id' in body:
            player = next((p for p in state['project']['subjects'] if p['id'] == state['player_character_id']), None)
            if not player:
                raise ValueError('Choose an existing character as the player.')
            state['player_name'] = story['player_name'] = player['name']
        state['configuration_revision'] += 1
        story['branch_states'][story['active_branch_id']] = state

    def available_actions(self, story_id, target_id=None):
        from .world import available_actions
        with self.lock:
            state = self._state(self._story(story_id))
            return available_actions(state['world'], state['player_character_id'], target_id)

    def _commit_state(self, story, turn, run_id):
        from .world import apply_effects, apply_discoveries, world_from_project, project_from_world, validate_world
        state = story['branch_states'][turn['branch_id']]
        # A reroll replaces the visual take, never reapplies its consequences.
        if turn.get('accepted_state'):
            accepted = copy.deepcopy(turn['accepted_state'])
        else:
            before = turn.get('snapshot') or state
            discovered = apply_discoveries(before['world'], turn['plan'].get('discoveries'), before.get('player_character_id'))
            world = world_from_project(turn['project'], discovered, before.get('player_character_id'))
            effects = turn['plan'].get('effects', turn.get('resolved_intent', {}).get('effects', []))
            names = {c['name'].casefold() for c in turn['plan']['characters']}
            direction = turn['plan'].get('direction')
            present_ids = {cid for shot in direction.get('shots', []) for cid in shot.get('visible_subject_ids', []) + shot.get('offscreen_subject_ids', [])} if direction else None
            witnesses = [c['id'] for c in world['characters'] if (c['id'] in present_ids if present_ids is not None else c['name'].casefold() in names)]
            summary = turn['plan']['action'] + '\n' + '\n'.join(d['speaker'] + ': ' + d['text'] for d in turn['plan']['dialogue'])
            cast = {c['name'].casefold(): c['id'] for c in world['characters']}
            dialogue = [{**d, 'speaker_id': d.get('speaker_id') or cast.get(d['speaker'].casefold())}
                        for d in turn['plan']['dialogue']]
            world = apply_effects(world, effects, event_id=turn.get('logical_turn_id', turn['id']),
                                  actor_id=before.get('player_character_id'), summary=summary, witness_ids=witnesses,
                                  dialogue=dialogue)
            accepted = {**copy.deepcopy(before), 'world': world, 'project': project_from_world(turn['project'], world)}
            turn['accepted_state'] = copy.deepcopy(accepted)
        # Preserve edits made while rendering. Only merge newly established assets/people.
        edited = state['configuration_revision'] != turn.get('configuration_revision', state['configuration_revision'])
        if not edited:
            state['project'], state['world'] = copy.deepcopy(accepted['project']), copy.deepcopy(accepted['world'])
        else:
            before = turn.get('snapshot') or accepted
            state['project'] = preserve_edits(before['project'], state['project'], accepted['project'])
            state['world'] = validate_world(preserve_edits(before['world'], state['world'], accepted['world']))
        consumed = {(g['id'], g['revision']) for g in (turn.get('snapshot') or {}).get('guides', [])
                    if g['scope'] == 'next' and g['enabled']}
        state['guides'] = [g for g in state['guides'] if (g['id'], g['revision']) not in consumed]
        accepted['guides'] = [g for g in accepted['guides'] if (g['id'], g['revision']) not in consumed]
        story['state_by_run'][run_id] = copy.deepcopy(accepted)

    def _restore_branch_state(self, story, run_id, branch_id):
        from .world import world_from_project
        state = copy.deepcopy(story.get('state_by_run', {}).get(run_id))
        if state is None:
            project = copy.deepcopy(self.videos().snapshot(run_id))
            # Legacy clips have no trustworthy historical private memories.
            player = next((p for p in project['subjects'] if p['name'] == story.get('player_name')), None)
            state = {'project': project, 'world': world_from_project(project, player_character_id=player['id'] if player else None),
                     'guides': [], 'player_character_id': player['id'] if player else None, 'premise': project['story']['text'],
                     'player_name': player['name'] if player else story.get('player_name', ''),
                     'settings': copy.deepcopy(story['settings']), 'configuration_revision': 1}
        story['branch_states'][branch_id] = state
        for key in ('premise', 'player_name', 'settings'):
            story[key] = copy.deepcopy(state[key])

    def supervised_requests(self):
        with self.lock:
            result = []
            for story in self.records.values():
                for turn in story['turns']:
                    if turn.get('cancel_requested') or turn['status'] != 'awaiting_assistant':
                        continue
                    for req in turn.get('assistant_requests', {}).values():
                        if req['status'] == 'pending':
                            result.append({**copy.deepcopy(req), 'story_id': story['id'], 'turn_id': turn['id']})
            return {'requests': result}

    def complete_supervised(self, request_id, body):
        from jsonschema import validate
        with self.lock:
            for story in self.records.values():
                for turn in story['turns']:
                    req = next((r for r in turn.get('assistant_requests', {}).values() if r['id'] == request_id), None)
                    if not req:
                        continue
                    if req['context_hash'] != body.get('context_hash'):
                        raise ValueError('The assistant response belongs to a different context.')
                    validate(body.get('result'), req['schema'])
                    if req['status'] == 'completed':
                        if digest(req['result']) != digest(body['result']):
                            raise ValueError('This request already has a different completed response.')
                        return {'accepted': True, 'request_id': request_id}
                    if turn.get('cancel_requested') or turn['status'] != 'awaiting_assistant':
                        raise ValueError('This assistant request is no longer active.')
                    req.update(status='completed', result=copy.deepcopy(body['result']), completed_at=time.time())
                    self._change(story, turn, status='planning', stage='Continuing the reviewed response')
                    self._spawn(story['id'], turn['id'])
                    return {'accepted': True, 'request_id': request_id}
            raise ValueError('This assistant request was not found.')


class AwaitingAssistant(Exception):
    pass
