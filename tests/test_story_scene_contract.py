"""Canonical scene intent reaches H3 and final-frame mismatches require review."""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from backend.compiler import compile_project
from backend.ending_observation import observation_request, validate_observation
from backend.projects import new_project, shot
from backend.stories import _stage_scene_contracts
from backend.world import WorldError, validate_world
from test_stories import plan, render, rig, story, uid
from test_story_rewrite_stop import directed


def staging(*, effects=(), two_shots=False, game=True):
    project = new_project()
    project.update(mode='t2va', story={'text': 'Alex points at the door; Mira remains seated.', 'locked': True})
    project['subjects'] = [{'id': cid, 'name': name, 'description': color + ' coat', 'asset_ids': []}
                           for cid, name, color in [('alex', 'Alex', 'blue'), ('mira', 'Mira', 'green'), ('ben', 'Ben', 'red')]]
    project['shots'] = [shot(5)]
    project['shots'][0].update(action=project['story']['text'], setting='City street beside a shop.',
        final_state='The approved action ends.', visible_subject_ids=['alex', 'mira'], offscreen_subject_ids=['ben'],
        scene_contract={'actors': [{'subject_id': 'alex', 'activity': 'act', 'start': 'standing at left', 'action': 'Point at the door.', 'end': 'hand lowered'},
                                   {'subject_id': 'mira', 'activity': 'act', 'start': 'seated on the blue bench', 'action': 'Get up and pace.', 'end': 'standing'}],
                        'objects': [], 'environment': 'City street.', 'background_activity': ''}, scene_contract_source='generated')
    if two_shots:
        project['shots'][0]['duration'] = 2
        project['shots'].append(copy.deepcopy(project['shots'][0]))
        project['shots'][1].update(id=uid(), duration=3)
    world = validate_world({'schema_version': 1, 'current_location_id': 'street',
        'locations': [{'id': 'street', 'name': 'City street'}, {'id': 'shop', 'name': 'Shop'}],
        'characters': [{**person, 'control': 'player' if person['id'] == 'alex' else 'npc', 'location_id': 'street'} for person in project['subjects']],
        'entities': [{'id': 'coin-1', 'name': 'Coin', 'holder_id': 'alex', 'description': 'A small gold coin lying on the pavement.', 'state': {'color': 'gold'}},
                     {'id': 'coin-2', 'name': 'Coin', 'location_id': 'street', 'description': 'A silver coin.', 'state': {'color': 'silver'}},
                     {'id': 'door', 'name': 'Shop door', 'location_id': 'street', 'description': 'A blue wooden door.', 'state': {'open': False, 'locked': True}},
                     {'id': 'secret', 'name': 'Hidden emerald', 'holder_id': 'alex', 'state': {'hidden': True}}]})
    value = {'action': project['story']['text'], 'effects': list(effects)}
    if game:
        value['actor_actions'] = [{'subject_id': 'alex', 'activity': 'act', 'action': 'Point at the door.'}]
    return project, world, value


def stage(project, world, value, *, game=True):
    unchanged = copy.deepcopy((world, value))
    _stage_scene_contracts(project, world, value, 'alex', game_mode=game)
    assert (world, value) == unchanged
    compiled = compile_project(project)
    assert compiled['valid'], compiled['issues']
    return compiled['prompt']


def test_text_only_counts_colors_ground_door_and_passive_bench_reach_prompt():
    project, world, value = staging()
    prompt = stage(project, world, value)
    contract = project['shots'][0]['scene_contract']
    actors = {row['subject_id']: row for row in contract['actors']}
    assert set(actors) == {'alex', 'mira'} and actors['mira']['activity'] == 'hold'
    assert 'seated on the blue bench' in actors['mira']['start'] and actors['mira']['start'] == actors['mira']['end']
    assert 'Get up and pace' not in prompt
    props = {row['entity_id']: row for row in contract['objects']}
    assert set(props) == {'coin-1', 'coin-2', 'door'}
    assert props['coin-1']['count'] == props['coin-2']['count'] == 1
    assert 'gold' in props['coin-1']['description'] and 'silver' in props['coin-2']['description']
    assert 'open: false' in props['door']['start'] and 'locked: true' in props['door']['end']
    assert 'Principal cast in this shot: exactly 2 separate individuals' in prompt
    assert 'Hidden emerald' not in prompt and 'lying on the pavement' not in prompt
    assert 'Coin (distinct prop 1)' in prompt and 'Coin (distinct prop 2)' in prompt


@pytest.mark.parametrize('effects,ending', [
    ([], 'held by Alex'),
    ([{'kind': 'holder', 'entity_id': 'coin-1', 'character_id': 'mira'}], 'held by Mira'),
    ([{'kind': 'holder', 'entity_id': 'coin-1', 'character_id': None}, {'kind': 'entity_location', 'entity_id': 'coin-1', 'location_id': 'street'}], 'held by nobody'),
    ([{'kind': 'character_location', 'character_id': 'alex', 'location_id': 'shop'}], 'held by Alex in Shop'),
])
def test_exact_object_identity_follows_approved_hold_transfer_drop_or_movement(effects, ending):
    project, world, value = staging(effects=effects)
    stage(project, world, value)
    item = next(row for row in project['shots'][0]['scene_contract']['objects'] if row['entity_id'] == 'coin-1')
    assert 'held by Alex' in item['start'] and ending in item['end']
    assert item['count'] == 1


def test_ground_relocation_and_door_opening_only_reach_final_multishot_endpoint():
    effects = [{'kind': 'entity_location', 'entity_id': 'coin-2', 'location_id': 'shop'},
               {'kind': 'entity_state', 'entity_id': 'door', 'key': 'open', 'value': True}]
    project, world, value = staging(effects=effects, two_shots=True)
    world['entities'][1]['description'] = 'A silver coin lying on the street.'
    value['action'] = 'Alex moves the silver coin into the shop after opening the door.'
    stage(project, world, value)
    first, last = [{row['entity_id']: row for row in scene['scene_contract']['objects']} for scene in project['shots']]
    assert 'in City street' in first['coin-2']['start'] and 'in Shop' not in first['coin-2']['end']
    assert 'in Shop' in last['coin-2']['end'] and 'do not reset' in last['coin-2']['start']
    assert 'lying on the street' not in first['coin-2']['description']
    assert 'open: false' in first['door']['start'] and 'open: true' not in first['door']['end']
    assert 'open: true' in last['door']['end']


def test_known_posture_overrides_invented_staging_but_approved_change_reaches_end():
    project, world, value = staging(effects=[{'kind': 'character_state', 'character_id': 'alex', 'key': 'posture', 'value': 'standing'}])
    world['characters'][0]['state']['posture'] = 'seated'
    stage(project, world, value)
    actor = project['shots'][0]['scene_contract']['actors'][0]
    assert 'posture: seated' in actor['start'] and 'standing at left' not in actor['start']
    assert 'posture: standing' in actor['end'] and 'posture: seated' not in actor['end']


@pytest.mark.parametrize('director_rows', [False, True])
def test_camera_only_explicit_holds_override_generated_act_rows_and_fill_empty_staging(director_rows):
    project, world, value = staging()
    value['action'] = project['story']['text'] = project['shots'][0]['action'] = 'The camera slowly pushes in; Alex and Mira stay in place.'
    project['shots'][0]['camera']['movement'] = 'push_in'
    world['characters'][0]['state'].update(posture='standing', position='left of the blue bench')
    world['characters'][1]['state'].update(posture='seated', position='right end of the blue bench')
    value['actor_actions'] = [{'subject_id': cid, 'activity': 'hold', 'action': 'Remain still while the camera moves.'}
                              for cid in ('alex', 'mira')]
    project['shots'][0]['scene_contract']['actors'] = [
        {'subject_id': cid, 'activity': 'act', 'start': '', 'action': '', 'end': ''} for cid in ('alex', 'mira')
    ] if director_rows else []
    prompt = stage(project, world, value)
    rows = {row['subject_id']: row for row in project['shots'][0]['scene_contract']['actors']}
    assert all(row['activity'] == 'hold' and row['start'] == row['end'] and row['action'].strip() for row in rows.values())
    assert 'posture: standing' in rows['alex']['start'] and 'left of the blue bench' in rows['alex']['end']
    assert 'posture: seated' in rows['mira']['start'] and 'right end of the blue bench' in rows['mira']['end']
    assert 'The camera pushes in' in prompt
    assert prompt.count('Maintains this established posture and place throughout the shot') == 2
    assert value['effects'] == []


def test_explicit_hold_keeps_established_director_pose_and_current_beat_speech_scope():
    project, world, value = staging()
    value['actor_actions'] = [{'subject_id': 'mira', 'activity': 'hold', 'action': 'I ask "Will you wait?"'}]
    stage(project, world, value)
    actor = project['shots'][0]['scene_contract']['actors'][1]
    assert actor['activity'] == 'hold' and actor['start'] == actor['end']
    assert 'seated on the blue bench' in actor['start']
    assert 'Get up and pace' not in actor['action'] and 'Will you wait?' not in actor['action']
    assert 'in this beat' in actor['action']


def test_authored_contract_actor_action_wins_over_explicit_writer_hold():
    project, world, value = staging()
    project['shots'][0]['scene_contract_source'] = 'authored'
    value['actor_actions'] = [{'subject_id': 'mira', 'activity': 'hold', 'action': 'Remain seated.'}]
    original = copy.deepcopy(project['shots'][0]['scene_contract']['actors'])
    stage(project, world, value)
    assert project['shots'][0]['scene_contract']['actors'] == original


def test_passive_actor_returning_in_later_shot_gets_known_pose_without_resetting_active_actor():
    project, world, value = staging(two_shots=True)
    world['characters'][1]['state'].update(posture='seated', position='on the blue bench')
    project['shots'][0].update(visible_subject_ids=['alex'], offscreen_subject_ids=['mira', 'ben'])
    project['shots'][0]['scene_contract']['actors'] = [project['shots'][0]['scene_contract']['actors'][0]]
    rows = project['shots'][1]['scene_contract']['actors']
    rows[0].update(start='standing beside the window after the first beat', end='beside the window')
    rows[1].update(activity='hold', start='', action='', end='')
    stage(project, world, value)
    active, passive = project['shots'][1]['scene_contract']['actors']
    assert active['start'] == 'standing beside the window after the first beat'
    assert 'posture: seated' in passive['start'] and 'on the blue bench' in passive['start']
    assert passive['start'] == passive['end']


def test_studio_new_text_props_survive_empty_world_without_duplicate_known_aliases():
    project, world, value = staging(game=False)
    new_prop = {'entity_id': 'parcel', 'name': 'Parcel', 'count': 1, 'description': 'One violet box.', 'start': 'on the table', 'end': 'on the table'}
    project['shots'][0]['scene_contract']['objects'] = [new_prop,
        {**new_prop, 'entity_id': 'coin-alias', 'name': 'Coin', 'description': 'An invented extra gold coin.'}]
    stage(project, world, value, game=False)
    props = project['shots'][0]['scene_contract']['objects']
    assert new_prop in props and not any(row['entity_id'] == 'coin-alias' for row in props)
    empty = validate_world({'schema_version': 1})
    project['shots'][0]['scene_contract']['objects'] = [new_prop]
    stage(project, empty, value, game=False)
    assert project['shots'][0]['scene_contract']['objects'] == [new_prop]


def test_explicit_authored_contract_is_preserved_without_canonical_rewrite():
    project, world, value = staging()
    project['shots'][0]['scene_contract_source'] = 'authored'
    original = copy.deepcopy(project['shots'][0]['scene_contract'])
    stage(project, world, value)
    assert project['shots'][0]['scene_contract'] == original


@pytest.mark.parametrize('bad', ['count', 'alias'])
def test_authored_game_contract_cannot_clone_an_existing_prop(bad):
    project, world, value = staging()
    project['shots'][0]['scene_contract_source'] = 'authored'
    project['shots'][0]['scene_contract']['objects'] = [{'entity_id': 'coin-1' if bad == 'count' else 'new-coin',
        'name': 'Coin', 'count': 2 if bad == 'count' else 1, 'description': 'Gold.', 'start': 'held by Alex', 'end': 'held by Alex'}]
    with pytest.raises(ValueError, match='count|established game object ID'):
        _stage_scene_contracts(project, world, value, 'alex', game_mode=True)


def test_authored_transfer_keeps_detail_and_uses_canonical_final_recipient():
    project, world, value = staging(effects=[{'kind': 'holder', 'entity_id': 'coin-1', 'character_id': 'mira'}])
    project['shots'][0]['scene_contract_source'] = 'authored'
    project['shots'][0]['scene_contract']['objects'] = [{'entity_id': 'coin-1', 'name': 'Coin', 'count': 1,
        'description': 'Gold with engraved bird.', 'start': 'pinched between fingertips', 'end': 'resting in an open palm'}]
    stage(project, world, value)
    item = project['shots'][0]['scene_contract']['objects'][0]
    assert 'Canonical assignment (authoritative): held by Alex' in item['start']
    assert 'Canonical assignment (authoritative): held by Mira' in item['end']
    assert 'resting in an open palm' in item['end'] and 'engraved bird' in item['description']


@pytest.mark.parametrize('condition,expected', [({'dead': True}, 'no breathing'), ({'defeated': True}, 'condition: defeated')])
def test_inactive_character_preserves_recorded_condition_without_new_actions(condition, expected):
    project, world, value = staging()
    world['characters'][1]['state'].update(condition)
    stage(project, world, value)
    actor = project['shots'][0]['scene_contract']['actors'][1]
    assert actor['activity'] == 'hold' and expected in actor['end']
    assert 'Get up' not in actor['action']
    if 'dead' not in condition:
        assert 'no breathing' not in actor['end']


def observation_contract(rig):
    session = story(rig, settings={'duration': 5, 'review_before_render': False})
    supplied = directed(rig)
    supplied['dialogue'] = []
    supplied['direction']['shots'][0]['dialogue_indices'] = []
    supplied['effects'] = []
    return session, supplied


@pytest.mark.parametrize('status', ['mismatch', 'uncertain', 'match', None])
def test_only_clear_final_frame_mismatch_pauses_before_acceptance(rig, status):
    session, supplied = observation_contract(rig)
    actor_id = rig.project['subjects'][1]['id']
    if status:
        rig.client.observation['continuity_checks'] = [{'kind': 'actor', 'id': actor_id, 'status': status,
            'detail': 'The clearly visible coat is red rather than the intended green.' if status == 'mismatch' else 'The coat is partly occluded.'},
            {'kind': 'actor', 'id': rig.project['subjects'][0]['id'], 'status': 'uncertain', 'detail': 'The other actor is partly occluded.'}]
    before = rig.manager._state(rig.manager._story(session['id']))
    before = copy.deepcopy(before)
    turn = render(rig, session, planned=supplied)
    assert turn['status'] == ('awaiting_acceptance' if status == 'mismatch' else 'succeeded'), turn.get('error')
    record = rig.manager._story(session['id'])
    if status == 'mismatch':
        assert turn['stage'] == 'Video ready · continuity needs review'
        assert rig.manager._state(record) == before and record['active_run_id'] is None
        rig.manager.action(session['id'], turn['id'], 'accept-intended', {'request_id': uid()})
        rig.manager.process(session['id'], turn['id'])
        assert rig.manager.get(session['id'])['turns'][-1]['status'] == 'succeeded'
    assert len(rig.videos.queues) == 1
    inspection = next(call for call in rig.client.calls if 'continuity_checks' in call['schema'].get('properties', {}))
    context = json.loads(next(item['text'] for item in inspection['content'] if item.get('type') == 'text')) if isinstance(inspection['content'], list) else json.loads(inspection['content'])
    assert 'intended_final_scene_contract' in context
    assert 'cannot prove continuous stillness' in context['scene_contract_check']


def test_continuity_checks_reject_unknown_identity_and_duplicates_without_world_mutation():
    project, world, value = staging()
    stage(project, world, value)
    contract = project['shots'][0]['scene_contract']
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    context, schema = observation_request(world, {}, 'alex', schema, contract)
    assert 'not proof' in context['continuity_check_rules']
    observed = {'continuity_checks': [{'kind': 'object', 'id': 'coin-1', 'status': 'mismatch', 'detail': 'Two separate gold coins are clearly visible.'}]}
    Draft202012Validator(schema).validate(observed)
    before = copy.deepcopy(world)
    assert validate_observation(observed, world, {}, 'alex', contract) == observed
    observed['continuity_checks'][0]['id'] = 'invented'
    with pytest.raises(WorldError, match='Continuity checks'):
        validate_observation(observed, world, {}, 'alex', contract)
    observed['continuity_checks'][0]['id'] = 'coin-1'
    observed['continuity_checks'] *= 2
    with pytest.raises(WorldError, match='one clear assessment'):
        validate_observation(observed, world, {}, 'alex', contract)
    assert world == before


def test_ending_inspector_only_receives_final_scene_visible_people(rig):
    session, supplied = observation_contract(rig)
    mira, nora = [person['id'] for person in rig.project['subjects']]
    first = supplied['direction']['shots'][0]
    first['duration'] = 2
    final = copy.deepcopy(first)
    final.update(beat_id='beat-2', duration=3, visible_subject_ids=[nora], offscreen_subject_ids=[mira])
    supplied['beats'].append({**supplied['beats'][0], 'id': 'beat-2'})
    supplied['direction']['shots'].append(final)
    turn = render(rig, session, planned=supplied)
    assert turn['status'] == 'succeeded', turn.get('error')
    call = next(call for call in rig.client.calls if 'continuity_checks' in call['schema'].get('properties', {}))
    context = json.loads(next(item['text'] for item in call['content'] if item.get('type') == 'text'))
    assert [row['id'] for row in context['cast']] == [nora]
    assert [row['id'] for row in context['known_visual_candidates']['characters']] == [nora]
    assert [row['subject_id'] for row in context['intended_final_scene_contract']['actors']] == [nora]


def test_new_take_does_not_inherit_previous_outcome_acceptance(rig):
    session, supplied = observation_contract(rig)
    turn = render(rig, session, planned=supplied)
    assert turn['status'] == 'succeeded'
    record = rig.manager._story(session['id'])
    internal = rig.manager._turn(record, turn['id'])
    internal['acceptance_approved'] = True
    accepted_run = record['active_run_id']
    rig.client.observation['continuity_checks'] = [{'kind': 'actor', 'id': rig.project['subjects'][1]['id'],
        'status': 'mismatch', 'detail': 'Two clearly visible versions of Nora are present.'},
        {'kind': 'actor', 'id': rig.project['subjects'][0]['id'], 'status': 'uncertain', 'detail': 'Mira is partly occluded.'}]
    rig.manager.action(session['id'], turn['id'], 'reroll', {'request_id': uid()})
    rig.manager.process(session['id'], turn['id'])
    assert internal['status'] == 'awaiting_acceptance' and record['active_run_id'] == accepted_run
    assert 'acceptance_approved' not in internal


def test_physical_edit_clears_app_actor_map_while_speech_edit_preserves_it(rig):
    session, supplied = observation_contract(rig)
    supplied['actor_actions'] = [{'subject_id': rig.project['subjects'][1]['id'], 'action': supplied['action'], 'activity': 'act'}]
    submitted = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I listen.', 'planned': supplied})
    record = rig.manager._story(session['id'])
    record['narrative_version'] = 2
    turn = rig.manager._turn(record, submitted['id'])
    speech = copy.deepcopy(supplied)
    speech.pop('actor_actions')
    speech['dialogue'] = [{'speaker': 'Nora', 'text': 'Hello.', 'language': 'English'}]
    assert rig.manager._edited_plan(record, turn, speech)['actor_actions'] == supplied['actor_actions']
    changed = copy.deepcopy(supplied)
    changed['action'] = 'Mira walks to the window.'
    assert 'actor_actions' not in rig.manager._edited_plan(record, turn, changed)
