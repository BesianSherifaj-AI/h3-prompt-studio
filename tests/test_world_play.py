"""Persistent gameplay consequences, discoveries and local targets."""
import copy

import pytest

from backend.world import (WorldError, actor_context, apply_discoveries, apply_effects,
                           available_actions, character_can_act, resolve_intent, validate_world)
from test_world import sample_world


@pytest.mark.parametrize('target', ['npc', 'key', 'box'])
@pytest.mark.parametrize('extent', ['step', 'nearby'])
def test_approach_visible_person_or_object_without_changing_location(target, extent):
    world = sample_world()
    before = copy.deepcopy(world)
    result = resolve_intent(world, 'player', {'kind': 'move', 'target_id': target, 'extent': extent})
    assert result['effects'] == []
    assert 'current location' in result['action']
    assert world == before


@pytest.mark.parametrize('options', [{'extent': 'travel'}, {'presentation': 'teleport'}, {'speed': 'impossible'}])
def test_object_is_not_a_teleport_or_unbounded_travel_destination(options):
    with pytest.raises(WorldError):
        resolve_intent(sample_world(), 'player', {'kind': 'move', 'target_id': 'key', **options})


def test_attack_is_attempt_and_defeat_only_applies_once_after_acceptance():
    world = sample_world()
    proposal = resolve_intent(world, 'player', {'kind': 'kill', 'target_id': 'npc'})
    assert proposal['intent']['kind'] == 'attack'
    assert 'success is not automatic' in proposal['action']
    assert proposal['effects'] == []
    effects = [{'kind': 'character_state', 'character_id': 'npc', 'key': 'defeated', 'value': True}]
    accepted = apply_effects(world, effects, event_id='combat', actor_id='player', witness_ids=['npc', 'player'])
    assert character_can_act(world['characters'][1])
    assert not character_can_act(accepted['characters'][1])
    assert apply_effects(accepted, effects, event_id='combat', actor_id='player') == accepted
    assert actor_context(accepted, 'player')['visible_characters'][0]['state']['defeated']
    for kind in ('talk', 'attack'):
        with pytest.raises(WorldError):
            resolve_intent(accepted, 'player', {'kind': kind, 'target_id': 'npc'})


def test_inventory_matches_carried_items_after_pickup_travel_and_drop():
    world = sample_world()
    take = resolve_intent(world, 'player', {'kind': 'take', 'target_id': 'key'})
    world = apply_effects(world, take['effects'], event_id='pickup', actor_id='player')
    # Carrying an object makes drop/give available even if its authored affordances omit them.
    world['entities'][0]['affordances'] = ['examine']
    assert {'drop', 'give'} <= {a['kind'] for a in available_actions(world, 'player', 'key')['actions'] if a['enabled']}
    inventory = resolve_intent(world, 'player', {'kind': 'inventory'})
    assert 'key' in inventory['action'].lower() and inventory['effects'] == []
    drop = resolve_intent(world, 'player', {'kind': 'drop', 'target_id': 'key'})
    world = apply_effects(world, drop['effects'], event_id='drop', actor_id='player')
    assert 'empty' in resolve_intent(world, 'player', {'kind': 'inventory'})['action']


def test_text_only_world_establishes_place_and_pickable_prop_without_images():
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Alex', 'control': 'player'}]})
    additions = {'locations': [{'id': 'street', 'name': 'Pixel street', 'description': 'A blue door beside a stall.'}],
                 'entities': [{'id': 'coin', 'name': 'Coin', 'description': 'A brass token.', 'kind': 'object'}]}
    discovered = apply_discoveries(world, additions, player_character_id='player')
    assert not world['locations'] and not world['entities']
    assert discovered['current_location_id'] == discovered['characters'][0]['location_id'] == 'street'
    assert discovered['entities'][0]['location_id'] == 'street'
    assert discovered['entities'][0]['asset_ids'] == []
    take = resolve_intent(discovered, 'player', {'kind': 'take', 'target_id': 'coin'})
    accepted = apply_effects(discovered, take['effects'], event_id='first', actor_id='player')
    assert accepted['entities'][0]['holder_id'] == 'player'


def test_discovered_route_connects_existing_place_without_overwriting_other_exits():
    world = sample_world()
    before = copy.deepcopy(world)
    result = apply_discoveries(world, {'locations': [{'id': 'alley', 'name': 'Alley', 'from_location_id': 'room'}]})
    room = next(p for p in result['locations'] if p['id'] == 'room')
    assert room['exits'][:-1] == before['locations'][0]['exits']
    assert room['exits'][-1]['target_id'] == 'alley'
    assert resolve_intent(result, 'player', {'kind': 'move', 'target_id': 'alley'})['effects']
    assert world == before


@pytest.mark.parametrize('discoveries', [
    {'entities': [{'id': 'key', 'name': 'Replacement key'}]},
    {'locations': [{'id': 'new', 'name': 'New', 'from_location_id': 'unknown'}]},
    {'entities': [{'id': 'new', 'name': 'New', 'state': {'_secret': True}}]},
    {'entities': [{'id': 'new', 'name': 'New', 'holder_id': 'player'}]},
    {'characters': []},
])
def test_discoveries_reject_collisions_unknown_routes_and_hidden_mutations(discoveries):
    world = sample_world()
    before = copy.deepcopy(world)
    with pytest.raises(WorldError):
        apply_discoveries(world, discoveries)
    assert world == before
