"""Regressions for discovered door controls and frozen fast-action guidance."""
import copy

import pytest

from backend.game_director import _current_scene_facts, direct_plan, plan_turn
from backend.gameplay import mechanical_plan
from backend.world import actor_context, apply_discoveries, available_actions
from test_game_director_robustness import augmented_predictor, predictor, scene
from test_stories import rig, uid


def discover(entity):
    project, world = scene(())
    discovery = {'entities': [copy.deepcopy(entity)]}
    def edit(plan):
        plan['discoveries'] = discovery
        plan['beats'][0].update(action='Alex looks at the shop entrance.', final_state='Alex remains beside the shop entrance.')
    result = plan_turn(project=project, world=world, player_character_id='player', message='I inspect the shop entrance.',
                       duration=5, predict=augmented_predictor([], edit))
    accepted = apply_discoveries(world, result['discoveries'], player_character_id='player')
    return result, accepted, discovery


@pytest.mark.parametrize(('name', 'kind'), [('Shop Door', 'prop'), ('Iron Gate', 'object'), ('Service entrance', 'door')])
def test_explicit_door_semantics_get_controls_without_inventing_an_unlocked_state(name, kind):
    result, world, original = discover({'id': 'entrance', 'name': name, 'description': 'A solid entrance with a handle.', 'kind': kind})
    entity = world['entities'][0]
    assert entity['affordances'] == ['examine', 'open', 'close']
    assert entity['state'] == {}
    assert 'affordances' not in original['entities'][0]
    assert 'state' not in result['discoveries']['entities'][0]
    actions = {row['kind']: row for row in available_actions(world, 'player', 'entrance')['actions']}
    assert {'examine', 'open', 'close'} <= set(actions)
    assert actions['open']['enabled']


def test_discovered_locked_door_keeps_its_lock_and_mechanics_cannot_bypass_it():
    _, world, _ = discover({'id': 'entrance', 'name': 'Shop Door', 'description': 'A locked shop door.', 'kind': 'prop',
                            'state': {'locked': True, 'open': False}})
    assert world['entities'][0]['state'] == {'locked': True, 'open': False}
    action = next(row for row in available_actions(world, 'player', 'entrance')['actions'] if row['kind'] == 'open')
    assert not action['enabled'] and 'locked' in action['reason']
    with pytest.raises(ValueError, match='locked'):
        mechanical_plan(world, 'player', {'kind': 'open', 'target_id': 'entrance'}, 3)


@pytest.mark.parametrize('affordances', [[], ['examine'], ['examine', 'touch']])
def test_explicit_restricted_interactions_are_not_overwritten(affordances):
    _, world, _ = discover({'id': 'entrance', 'name': 'Shop Door', 'description': 'A decorative door.', 'kind': 'door',
                            'affordances': affordances})
    assert world['entities'][0]['affordances'] == affordances
    assert not any(row['kind'] == 'open' for row in available_actions(world, 'player', 'entrance')['actions'])


@pytest.mark.parametrize('name', ['Door key', 'Doorway', 'Archway', 'Painting of a door', 'Model gate', 'Door handle', 'Key to the shop door'])
def test_related_props_and_representations_do_not_acquire_door_mechanics(name):
    result, world, _ = discover({'id': 'related', 'name': name, 'description': 'A visible item near the entrance.', 'kind': 'prop'})
    assert 'affordances' not in result['discoveries']['entities'][0]
    assert world['entities'][0]['affordances'] == ['examine']


def test_direct_mechanical_staging_preserves_frozen_guides_ending_and_custom_controls(rig):
    project, world = scene()
    project['comfy_render'] = {'seed': 11}
    project['custom_instructions'] = 'Use a low camera; the scene has no music or dialogue.'
    project['shots'][0]['camera']['framing'] = 'wide'
    project['shots'][0]['director_locks'] = ['camera.framing']
    world['entities'] = [{'id': 'key', 'name': 'Brass key', 'kind': 'key', 'holder_id': 'player', 'affordances': ['drop', 'examine']}]
    opening = rig.videos.add(project)
    old_guide = {'id': uid(), 'revision': 1, 'scope': 'persistent', 'text': 'Frame the key clearly and keep Mira in the background.', 'enabled': True}
    disabled = {'id': uid(), 'revision': 1, 'scope': 'persistent', 'text': 'Ignored disabled instruction.', 'enabled': False}
    session = rig.manager.create({'request_id': uid(), 'project': project, 'world': world, 'mode': 'game',
                                  'player_name': 'Alex', 'player_character_id': 'player', 'source_run_id': opening['id'],
                                  'guides': [old_guide, disabled], 'settings': {'duration': 3, 'fast_actions': True}})
    internal_story = rig.manager._story(session['id'])
    observed = {'observed_state': 'Alex holds the brass key near the window.', 'uncertainties': '', 'choices': ['old suggestion']}
    internal_story['observed_by_run'][opening['id']] = observed
    public_turn = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I drop the Brass key.',
                                                   'intent': {'kind': 'drop', 'target_id': 'key'}})
    turn = rig.manager._turn(internal_story, public_turn['id'])
    rig.manager.update(session['id'], {'guides': [{**old_guide, 'text': 'A later guide for another turn.'}]})
    execution = rig.manager._execution(internal_story, turn)
    calls = []
    response = predictor(calls)
    fast = mechanical_plan(execution['world'], 'player', turn['intent'], 3)
    result = direct_plan(fast, turn['snapshot']['project'], duration=3, predict=response,
                         guides=execution['guides'], observed_state=observed,
                         current_facts=_current_scene_facts(execution['world'], actor_context(execution['world'], 'player')))
    assert [call[0] for call in calls] == ['director']
    request = calls[0][2]
    assert request['active_guides'] == [old_guide['text']]
    assert request['user_instructions'] == project['custom_instructions']
    assert request['observed_ending'] == {key: value for key, value in observed.items() if key != 'choices'}
    assert any('ALREADY HELD by Alex' in fact for fact in request['current_scene_facts'])
    assert request['current_controls'][0]['camera']['framing'] == 'wide'
    assert result['shots'][0]['camera']['framing'] == 'wide'
    assert fast['effects'][0] == {'kind': 'holder', 'entity_id': 'key', 'character_id': None}
