"""Visible street targets work before a text-only story has a canonical map."""
import copy
import json

import pytest

from backend.ending_observation import observation_request, scene_candidates, validate_observation
from backend.stories import OBSERVE_SCHEMA
from backend.world import validate_world
from test_game_director import coordinator, direction, setup_scene
from test_stories import observed_for_schema, rig, uid


def visible_scene():
    return {'setting': 'A cobblestone street with a shopfront.', 'candidates': [
        {'kind': 'person', 'known_id': None, 'label': 'Person in blue coat', 'description': 'Blue coat and brown trousers.', 'position': 'Left foreground'},
        {'kind': 'person', 'known_id': None, 'label': 'Person in green jacket', 'description': 'Green jacket and black boots.', 'position': 'Right beside shop'},
        {'kind': 'door', 'known_id': None, 'label': 'Shop door', 'description': 'Brown wooden door with glass pane.', 'position': 'Rear right'}]}


def city(rig, *, structured=True):
    project, _ = setup_scene()
    project['subjects'] = project['subjects'][:1]
    project['subjects'][0]['description'] = ''
    project['comfy_render'] = {'seed': 42, 'resolution': '0.2', 'steps': 8}
    run = rig.videos.add(project)
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Alex', 'control': 'player'}]})
    session = rig.manager.create({'request_id': uid(), 'project': project, 'source_run_id': run['id'],
        'mode': 'game', 'player_name': 'Alex', 'world': world, 'player_character_id': 'player',
        'settings': {'duration': 3, 'resolution': '0.2'}})
    story = rig.manager._story(session['id'])
    observation = {'observed_state': 'Two people and a shop door are visible.'}
    if structured:
        observation['visible_scene'] = visible_scene()
    story['observed_by_run'][run['id']] = observation
    return story, run


def binding_body(rig, story, index=0):
    scene = rig.manager.scene_inventory(story)
    return {'request_id': uid(), 'run_id': scene['run_id'], 'branch_id': scene['branch_id'],
            'configuration_revision': scene['configuration_revision'], 'candidate_id': scene['targets'][index]['id']}


def test_catalog_exposes_unregistered_people_and_door_without_creating_world_or_inventory(rig):
    story, _ = city(rig)
    before = copy.deepcopy(rig.manager._state(story))
    result = rig.manager.available_actions(story['id'])
    assert result['targets'] == []
    assert [row['kind'] for row in result['scene']['targets']] == ['person', 'person', 'door']
    assert all(row['known_id'] is None for row in result['scene']['targets'])
    assert [row['kind'] for row in result['scene']['targets'][2]['actions']] == ['examine', 'move']
    assert before == rig.manager._state(story)
    assert not rig.client.calls and not rig.videos.queues and not rig.assets.requests


def test_player_binding_is_explicit_idempotent_and_establishes_only_descriptive_location(rig):
    story, run = city(rig)
    request = binding_body(rig, story)
    rig.manager.bind_scene_player(story['id'], request)
    once = copy.deepcopy(rig.manager._state(story))
    rig.manager.bind_scene_player(story['id'], request)
    assert once == rig.manager._state(story)
    world = once['world']
    assert len(world['characters']) == 1 and world['entities'] == []
    assert world['current_location_id'] and world['locations'][0]['name'] == 'Observed scene'
    assert world['characters'][0]['state']['visual_anchor'] == visible_scene()['candidates'][0]['description']
    assert once['player_visual_anchor']['run_id'] == run['id']
    assert rig.manager.scene_inventory(story)['targets'][0]['known_id'] == 'player'
    assert not rig.videos.queues and not rig.client.calls
    with pytest.raises(ValueError, match='another selection'):
        rig.manager.bind_scene_player(story['id'], {**request, 'candidate_id': 'changed'})


def test_ambiguous_player_motion_requires_binding_but_camera_motion_does_not(rig):
    story, _ = city(rig)
    with pytest.raises(ValueError, match='This is me'):
        rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'Move forward.', 'intent': {'kind': 'move', 'direction': 'forward'}})
    assert not story['turns']
    rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'Move the camera forward.',
        'intent': {'kind': 'move', 'direction': 'forward', 'camera': 'camera'}})
    assert len(story['turns']) == 1


def test_door_selection_is_provisional_and_never_grants_open_unlocked_or_inventory(rig):
    story, _ = city(rig)
    before = copy.deepcopy(rig.manager._state(story))
    target = rig.manager.scene_inventory(story)['targets'][2]
    intent = target['actions'][0]['intent']
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I inspect the shop door.', 'intent': intent})
    turn = rig.manager._turn(story, public['id'])
    assert turn['intent']['kind'] == 'examine'
    door = turn['snapshot']['world']['entities'][0]
    assert door['kind'] == 'door' and door['affordances'] == ['examine']
    assert door['holder_id'] is None and door['owner_id'] is None
    assert not {'open', 'locked'} & door['state'].keys()
    assert turn['resolved_intent']['effects'] == []
    assert rig.manager._state(story) == before


def test_selected_unknown_person_reaches_actor_and_persists_only_after_accepted_turn(rig):
    story, _ = city(rig)
    target = rig.manager.scene_inventory(story)['targets'][1]
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I greet the person in green.',
        'intent': next(row['intent'] for row in target['actions'] if row['kind'] == 'talk')})
    turn = rig.manager._turn(story, public['id'])
    target_id = turn['intent']['target_id']
    assert len(rig.manager._state(story)['world']['characters']) == 1
    calls = []
    def complete(model, system, content, schema, **options):
        if 'character_id' in schema['properties']:
            calls.append('actor')
            assert schema['properties']['character_id']['enum'] == [target_id]
            result = {'character_id': target_id, 'action': 'The person in green waves at Alex.', 'intent': 'Acknowledge the greeting nonverbally.', 'dialogue': []}
        elif 'new_characters' in schema['properties']:
            calls.append('writer')
            result = coordinator()
            result['beats'][0].update(action='The person in green waves at Alex.', setting='The cobblestone street.', final_state='The person in green remains beside the shop.')
        elif 'shots' in schema['properties']:
            result = direction(3)
            result['shots'][0].update(visible_subject_ids=['player', target_id], dialogue_indices=[])
        else:
            result = observed_for_schema(rig.client.observation, schema)
        return {'result': copy.deepcopy(result), 'diagnostics': {}}
    rig.client.complete_json_result = complete
    rig.manager.process(story['id'], turn['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    assert calls == ['actor', 'writer']
    known = rig.manager._state(story)['world']
    assert len(known['characters']) == 2 and known['entities'] == []
    assert next(row for row in known['characters'] if row['id'] == target_id)['name'] == 'Person in green jacket'
    assert rig.manager._state(story)['scene_target_bindings'][target['id']] == target_id
    assert not rig.assets.requests


def test_pending_observed_candidates_are_visible_but_not_actionable_or_bindable(rig):
    story, run = city(rig)
    before = copy.deepcopy(rig.manager._state(story))
    newer = rig.videos.add(rig.videos.snapshot(run['id']))
    story['turns'].append({'id': uid(), 'run_id': newer['id'], 'status': 'awaiting_acceptance',
        'plan': {'characters': [], 'effects': []}, 'snapshot': copy.deepcopy(before),
        'observation': {'visible_scene': visible_scene()}})
    scene = rig.manager.scene_inventory(story)
    assert scene['run_id'] == newer['id'] and scene['status'] == 'pending_review'
    assert all(not action['enabled'] for target in scene['targets'] for action in target['actions'])
    with pytest.raises(ValueError, match='Finish or review'):
        rig.manager.bind_scene_player(story['id'], binding_body(rig, story))
    assert rig.manager._state(story) == before


def test_refresh_legacy_frame_inspects_once_without_render_or_accepted_state_change(rig):
    story, run = city(rig, structured=False)
    assert rig.manager.scene_inventory(story)['status'] == 'unavailable'
    state = copy.deepcopy(rig.manager._state(story))
    original_observation = copy.deepcopy(story['observed_by_run'])
    calls = []
    def complete(model, system, content, schema, **options):
        calls.append(content)
        assert 'visible_scene' in schema['required']
        assert len([part for part in content if part['type'] == 'image_url']) == 1
        return {'result': {'visible_scene': visible_scene()}, 'diagnostics': {}}
    rig.client.complete_json_result = complete
    request = {'request_id': uid(), 'run_id': run['id'], 'branch_id': story['active_branch_id'],
               'configuration_revision': state['configuration_revision']}
    rig.manager.refresh_scene(story['id'], request)
    rig.manager.refresh_scene(story['id'], request)
    rig.manager.process_scene_inspection(story['id'], request['request_id'])
    rig.manager.process_scene_inspection(story['id'], request['request_id'])
    assert len(calls) == 1 and not rig.videos.queues and not rig.assets.requests
    assert state == rig.manager._state(story) and original_observation == story['observed_by_run']
    assert rig.manager.scene_inventory(story)['status'] == 'ready'
    assert len(rig.manager.scene_inventory(story)['targets']) == 3


def test_unknown_player_is_not_recognized_from_expected_cast_alone_and_unknown_ids_rejected():
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Alex'}]})
    observation = {'visible_scene': visible_scene()}
    observation['visible_scene']['candidates'][0]['known_id'] = 'player'
    result = validate_observation(observation, world, {'characters': []}, 'player', include_scene=True)
    assert result['visible_scene']['candidates'][0]['known_id'] is None
    assert observation['visible_scene']['candidates'][0]['known_id'] == 'player'
    bad = copy.deepcopy(observation)
    bad['visible_scene']['candidates'][0]['known_id'] = 'invented-id'
    with pytest.raises(ValueError, match='known IDs'):
        validate_observation(bad, world, {'characters': []}, 'player', include_scene=True)
    bad = copy.deepcopy(observation)
    bad['visible_scene']['candidates'][0]['holder_id'] = 'player'
    with pytest.raises(ValueError):
        validate_observation(bad, world, {'characters': []}, 'player', include_scene=True)


def test_legacy_observation_contract_stays_optional_and_fresh_inventory_is_required():
    world = validate_world({'schema_version': 1, 'characters': []})
    old = {'observed_state': 'A street.'}
    assert validate_observation(old, world, {}, None) == old
    _, schema = observation_request(world, {}, None, OBSERVE_SCHEMA, include_scene=True)
    assert 'visible_scene' in schema['required']
    with pytest.raises(ValueError, match='visible scene inventory'):
        validate_observation(old, world, {}, None, include_scene=True)


def test_bound_directional_movement_uses_no_assistant_and_marks_scene_positions_stale(rig):
    story, source = city(rig)
    rig.manager.bind_scene_player(story['id'], binding_body(rig, story))
    def forbidden(*args, **kwargs):
        raise AssertionError('A plain bound movement must not load or call the assistant.')
    rig.resources.run_ai = forbidden
    rig.client.complete_json_result = forbidden
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I walk forward.',
        'intent': {'kind': 'move', 'direction': 'forward', 'extent': 'step', 'camera': 'player'}})
    rig.manager.process(story['id'], public['id'])
    turn = rig.manager._turn(story, public['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    assert turn['planning_mode'] == 'deterministic_movement'
    assert turn['observation']['inspection_status'] == 'not_run'
    assert 'visible_scene' not in turn['observation']
    scene = rig.manager.scene_inventory(story)
    assert scene['status'] == 'stale' and scene['run_id'] == turn['run_id']
    assert scene['inspected_run_id'] == source['id'] and len(scene['targets']) == 3
    assert all(not action['enabled'] for target in scene['targets'] for action in target['actions'])
    assert not rig.assets.requests and len(rig.videos.queues) == 1


def test_selected_visible_object_does_not_assert_unknown_holder_is_nobody(rig):
    from backend.stories import _render_placements
    story, run = city(rig)
    story['observed_by_run'][run['id']]['visible_scene']['candidates'].append({
        'kind': 'object', 'known_id': None, 'label': 'Brass coin',
        'description': 'Small brass coin.', 'position': 'Near the green figure\'s hand'})
    target = rig.manager.scene_inventory(story)['targets'][-1]
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I inspect the brass coin.',
        'intent': target['actions'][0]['intent']})
    world = rig.manager._turn(story, public['id'])['snapshot']['world']
    rows = _render_placements(world, [], {'player'}, include_ground=True)
    assert len(rows) == 1 and 'holder or wearer has not been identified' in rows[0]['start']
    assert 'held by nobody' not in rows[0]['start'] and 'held by nobody' not in rows[0]['end']
