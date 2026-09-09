"""Authored game conditions must not be bypassed by fast deterministic effects."""
import copy

import pytest

from backend.game_director import plan_turn
from backend.gameplay import mechanical_plan
from test_game_director_robustness import augmented_predictor, scene
from test_stories import rig, uid


def key_scene():
    project, world = scene()
    project['custom_instructions'] = ''
    world['entities'] = [{'id': 'key', 'name': 'Brass key', 'kind': 'key', 'affordances': ['take', 'examine']}]
    return project, world


@pytest.mark.parametrize('kind', ['take', 'look', 'examine', 'move'])
def test_world_rules_require_narrative_resolution_even_for_observation(kind):
    _, world = key_scene()
    world['rules'] = ['The cursed key cannot be lifted until a ritual is completed; looking at it may activate the curse.']
    assert mechanical_plan(world, 'player', {'kind': kind, 'target_id': 'key'}, 3) is None


@pytest.mark.parametrize('instructions', [
    {'guides': [{'enabled': True, 'text': 'The player must not touch the key.'}]},
    {'guides': [{'enabled': True, 'text': 'Use a low camera.'}]},
    {'user_instructions': 'Do not cross the bridge until it is repaired.'},
])
def test_arbitrary_authored_instructions_are_not_classified_as_safe_mechanics(instructions):
    _, world = key_scene()
    assert mechanical_plan(world, 'player', {'kind': 'take', 'target_id': 'key'}, 3, **instructions) is None


@pytest.mark.parametrize('condition', [{'health': 1}, {'petrified': True}, {'status': 'immobilized'}, {'status': None}])
def test_unrecognized_character_conditions_need_narrative_resolution(condition):
    _, world = key_scene()
    world['characters'][0]['state'] = condition
    assert mechanical_plan(world, 'player', {'kind': 'take', 'target_id': 'key'}, 3) is None


def test_unknown_item_condition_is_not_ignored_but_plain_disabled_guides_keep_fast_path():
    _, world = key_scene()
    ordinary = mechanical_plan(world, 'player', {'kind': 'take', 'target_id': 'key'}, 3,
                               guides=[{'enabled': False, 'text': 'Never take the key.'}])
    assert ordinary['effects'] == [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}]
    world['entities'][0]['state'] = {'cursed': True}
    assert mechanical_plan(world, 'player', {'kind': 'take', 'target_id': 'key'}, 3) is None


def test_inventory_remains_a_read_only_listing_under_game_rules():
    _, world = key_scene()
    world['rules'] = ['A cursed statue reacts to physical interaction.']
    plan = mechanical_plan(world, 'player', {'kind': 'inventory'}, 3,
                           guides=[{'enabled': True, 'text': 'Touch nothing.'}])
    assert plan is not None and plan['effects'] == []


def refuse_pickup(plan):
    plan['effects'] = []
    plan['beats'][0].update(action='Alex tries to lift the cursed key, which stays fixed to the table.',
                            final_state='The key remains on the table and Alex holds nothing new.')


def test_rule_constrained_button_can_fail_without_forcing_the_success_effect():
    project, world = key_scene()
    world['rules'] = ['The cursed key cannot be lifted until a ritual has been completed. No ritual has occurred.']
    original = copy.deepcopy(world)
    calls = []
    result = plan_turn(project=project, world=world, player_character_id='player', message='I take the Brass key.',
                       intent={'kind': 'take', 'target_id': 'key'}, duration=5,
                       predict=augmented_predictor(calls, refuse_pickup))
    assert result['effects'] == [] and world == original
    resolved = next(call[2]['resolved_intent'] for call in calls if call[0] == 'roleplay')
    assert resolved['effects_are_conditional'] is True
    assert resolved['effects'] == [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}]


def test_unconstrained_supported_button_still_requires_its_selected_effect():
    project, world = key_scene()
    with pytest.raises(ValueError, match='omitted the selected interaction'):
        plan_turn(project=project, world=world, player_character_id='player', message='I take the Brass key.',
                  intent={'kind': 'take', 'target_id': 'key'}, duration=5,
                  predict=augmented_predictor([], refuse_pickup))


def test_story_orchestrator_routes_a_guided_typed_action_through_creative_planning(rig, monkeypatch):
    project, world = key_scene()
    guide = {'id': uid(), 'scope': 'persistent', 'text': 'The cursed key cannot be taken before the ritual.', 'enabled': True}
    session = rig.manager.create({'request_id': uid(), 'project': project, 'world': world, 'mode': 'game',
                                  'player_name': 'Alex', 'player_character_id': 'player', 'guides': [guide]})
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I take the Brass key.',
                                               'intent': {'kind': 'take', 'target_id': 'key'}})
    story = rig.manager._story(session['id'])
    turn = rig.manager._turn(story, public['id'])
    execution = rig.manager._execution(story, turn)
    calls = []
    response = augmented_predictor(calls, refuse_pickup)
    monkeypatch.setattr(rig.manager, '_predict', lambda execution, turn, stage, actor_id, system, content, schema, images, **kwargs:
                        response(stage, actor_id, system, content, schema))
    result = rig.manager.plan(execution, turn, turn['snapshot']['project'])
    assert [call[0] for call in calls] == ['actor', 'roleplay', 'director']
    assert result['effects'] == []
    assert all(stage['stage'] != 'mechanics' for stage in result['assistant_stages'])
