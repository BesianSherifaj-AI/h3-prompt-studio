import copy

import pytest

from backend.gameplay import infer_simple_intent, mechanical_plan
from backend.world import apply_effects, validate_world
from backend.stories import validate_plan
from test_world import sample_world


@pytest.mark.parametrize('verb', ['take', 'pick up', 'grab', 'get'])
def test_exact_named_pickup_uses_mechanics(verb):
    world = sample_world()
    name = world['entities'][0]['name']
    intent = infer_simple_intent(world, 'player', f'I {verb} the {name}.')
    assert intent == {'kind': 'take', 'target_id': 'key'}
    plan = mechanical_plan(world, 'player', intent, 3)
    assert plan['effects'] == [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}]
    assert not plan['dialogue'] and not plan['asset_requests']
    assert world['entities'][0].get('holder_id') is None


@pytest.mark.parametrize('message', ['I take the key and run away', 'I take it', 'I say "take the key"', 'Should I take the key?', 'I take a missing sword'])
def test_complex_or_ambiguous_text_keeps_creative_planning(message):
    assert infer_simple_intent(sample_world(), 'player', message) is None


def test_examine_and_look_cannot_pick_up_objects_or_move_characters():
    world = sample_world()
    for intent in ({'kind': 'examine', 'target_id': 'key'}, {'kind': 'look'}):
        plan = mechanical_plan(world, 'player', intent, 3)
        assert plan['effects'] == []
        assert 'remain unchanged' in plan['final_state']


def test_drop_give_and_travel_keep_expected_state_and_scene_cast():
    world = apply_effects(sample_world(), [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}], event_id='pickup', actor_id='player')
    drop = mechanical_plan(world, 'player', {'kind': 'drop', 'target_id': 'key'}, 3)
    assert drop['effects'][0]['character_id'] is None
    assert 'Nobody picks it up' in drop['action']
    give = mechanical_plan(world, 'player', {'kind': 'give', 'target_id': 'key', 'recipient_id': 'npc'}, 3)
    assert give['effects'][0]['character_id'] == 'npc'
    move = mechanical_plan(world, 'player', {'kind': 'move', 'target_id': 'hall'}, 3)
    assert move['transition'] == 'cut'
    assert [c['id'] for c in move['characters']] == ['player']


def test_text_only_unestablished_world_combat_and_talk_keep_creative_planning():
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Alex', 'control': 'player'}]})
    assert mechanical_plan(world, 'player', {'kind': 'look'}, 3) is None
    for kind in ('attack', 'talk', 'freeform', 'wait'):
        assert mechanical_plan(sample_world(), 'player', {'kind': kind, 'target_id': 'npc'}, 3) is None


@pytest.mark.parametrize('value', [None, [], 'plain prose', 7, {'value': float('nan')}])
def test_user_supplied_invalid_scene_is_actionable_not_server_error(value):
    with pytest.raises(ValueError):
        validate_plan(value)


def test_ambiguous_identical_item_names_are_not_guessed():
    world = sample_world()
    world['entities'].append({**copy.deepcopy(world['entities'][0]), 'id': 'other-key'})
    assert infer_simple_intent(world, 'player', 'I take the key.') is None


def test_preview_id_cannot_collide_with_an_accepted_event_and_skip_pickup_simulation():
    world = apply_effects(sample_world(), [], event_id='mechanical-preview', actor_id='player')
    world = apply_effects(world, [], event_id='mechanical-preview-x', actor_id='player')
    original = copy.deepcopy(world)
    plan = mechanical_plan(world, 'player', {'kind': 'take', 'target_id': 'key'}, 3)
    assert plan['effects'] == [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}]
    assert 'held by ' + world['characters'][0]['name'] in plan['final_state']
    assert 'unheld' not in plan['final_state']
    assert world == original


def test_optional_studio_choices_do_not_discard_good_authored_scene():
    from backend.game_director import _studio_choices
    suggestions = _studio_choices([{'title': 'Next', 'message': 'Follow the courier.'}, {'title': '', 'message': ''}])
    assert suggestions[0] == {'title': 'Next', 'message': 'Follow the courier.'}
    assert len(suggestions) == 3 and all(c['message'] and c['title'] for c in suggestions)


def test_model_director_cannot_add_pickup_to_approved_inspection():
    from backend.game_director import direct_plan
    from test_game_director_robustness import scene, predictor
    project, world = scene()
    plan = mechanical_plan(world, 'player', {'kind': 'examine', 'target_id': 'npc-0'}, 3)
    seen = []
    base = predictor(seen)
    def malicious_direction(*args):
        result = base(*args)
        result['shots'][0]['performance'] = 'Alex steals the coin and pockets it.'
        return result
    directed = direct_plan(plan, project, duration=3, predict=malicious_direction)
    performance = directed['game_direction']['shots'][0]['performance']
    assert 'steals' not in performance and 'pockets' not in performance
    assert plan['action'] in performance
