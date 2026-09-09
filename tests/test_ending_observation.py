import copy
import json

import pytest
from jsonschema import Draft202012Validator

from backend.ending_observation import observation_request, validate_observation
from backend.world import WorldError, validate_world


def scene():
    return validate_world({'schema_version': 1, 'current_location_id': 'street',
        'locations': [{'id': 'street', 'name': 'Street'}, {'id': 'alley', 'name': 'Alley'}],
        'characters': [{'id': 'player', 'name': 'Alex', 'control': 'player'},
                       {'id': 'npc', 'name': 'Mara', 'private_knowledge': ['Secret code 731.'],
                        'goals': ['Secret objective.'], 'state': {'health': 3}}],
        'entities': [{'id': 'key', 'name': 'Key'}, {'id': 'coat', 'name': 'Coat', 'worn_by_id': 'player'},
                     {'id': 'door', 'name': 'Door'}]})


def base_schema():
    return {'type': 'object', 'properties': {'observed_state': {'type': 'string'},
        'uncertainties': {'type': 'string'}, 'choices': {'type': 'array'}},
        'required': ['observed_state', 'uncertainties', 'choices'], 'additionalProperties': False}


def observed(effects=None):
    result = {'observed_state': 'Alex holds the key near the open door.', 'uncertainties': '', 'choices': []}
    if effects is not None:
        result['visible_effects'] = effects
    return result


def test_optional_schema_remains_valid_and_does_not_change_legacy_observations():
    world, schema = scene(), base_schema()
    original = copy.deepcopy((world, schema))
    _, extended = observation_request(world, {}, 'player', schema)
    Draft202012Validator.check_schema(extended)
    Draft202012Validator(extended).validate(observed())
    Draft202012Validator(extended).validate(observed([]))
    assert 'visible_effects' not in extended['required']
    assert validate_observation(observed(), world, {}, 'player') == observed()
    assert (world, schema) == original


def test_observation_context_never_includes_secrets_or_treats_intent_as_evidence():
    context, _ = observation_request(scene(), {}, 'player', base_schema())
    encoded = json.dumps(context)
    assert 'Secret code' not in encoded and 'Secret objective' not in encoded
    assert 'health": 3' not in encoded and 'private_knowledge' not in encoded
    assert context['known_visual_candidates']['characters'][1]['name'] == 'Mara'
    assert 'not evidence' in context['visual_effect_rules']
    assert 'death' in context['visual_effect_rules'] and 'lip sync' in context['visual_effect_rules']


def test_visible_pickup_open_door_and_movement_have_known_ids_without_mutating_world():
    world = scene(); before = copy.deepcopy(world)
    effects = [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'},
               {'kind': 'entity_state', 'entity_id': 'door', 'key': 'open', 'value': True},
               {'kind': 'character_location', 'character_id': 'player', 'location_id': 'alley'}]
    observation = observed(effects)
    assert validate_observation(observation, world, {}, 'player') == observation
    assert world == before and observation == observed(effects)


@pytest.mark.parametrize('effect', [
    {'kind': 'holder', 'entity_id': 'unknown', 'character_id': 'player'},
    {'kind': 'holder', 'entity_id': 'key', 'character_id': 'unknown'},
    {'kind': 'entity_location', 'entity_id': 'key', 'location_id': None},
    {'kind': 'character_location', 'character_id': 'npc', 'location_id': 'invented'},
    {'kind': 'entity_state', 'entity_id': 'door', 'key': 'locked', 'value': True},
    {'kind': 'entity_state', 'entity_id': 'door', 'key': 'open', 'value': 'yes'},
    {'kind': 'character_state', 'character_id': 'npc', 'key': 'dead', 'value': True},
    {'kind': 'character_state', 'character_id': 'npc', 'key': 'health', 'value': 0},
    {'kind': 'owner', 'entity_id': 'key', 'character_id': 'player'},
    {'kind': 'knowledge', 'character_id': 'player', 'fact': 'Mara said the password.'},
    {'kind': 'holder', 'entity_id': 'key', 'character_id': 'player', 'audio_evidence': 'Mara said so'},
])
def test_unknown_ids_nonvisual_claims_and_extra_fields_are_rejected(effect):
    with pytest.raises(WorldError, match='Visible effects'):
        validate_observation(observed([effect]), scene(), {}, 'player')


def test_discovered_location_and_object_and_new_known_cast_are_provisional_candidates():
    world = scene(); before = copy.deepcopy(world)
    plan = {'discoveries': {'locations': [{'id': 'shop', 'name': 'Shop', 'from_location_id': 'street'}],
                           'entities': [{'id': 'coin', 'name': 'Coin', 'location_id': 'shop'}]},
            'characters': [{'id': 'new-person', 'name': 'Trader', 'description': 'Blue cap'}]}
    effects = [{'kind': 'holder', 'entity_id': 'coin', 'character_id': 'new-person'},
               {'kind': 'character_location', 'character_id': 'new-person', 'location_id': 'shop'}]
    context, schema = observation_request(world, plan, 'player', base_schema())
    Draft202012Validator(schema).validate(observed(effects))
    assert validate_observation(observed(effects), world, plan, 'player')['visible_effects'] == effects
    assert any(item['id'] == 'coin' for item in context['known_visual_candidates']['entities'])
    assert world == before


def test_contradictory_and_impossible_clothing_assignments_are_rejected():
    with pytest.raises(WorldError, match='contradictory'):
        validate_observation(observed([{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'},
                                      {'kind': 'holder', 'entity_id': 'key', 'character_id': 'npc'}]), scene(), {}, 'player')
    with pytest.raises(WorldError, match='worn|outfit|Outfit'):
        validate_observation(observed([{'kind': 'holder', 'entity_id': 'coat', 'character_id': 'npc'}]), scene(), {}, 'player')


@pytest.mark.parametrize('characters', [[], [{'id': 'actor', 'name': 'Actor'}]])
def test_empty_or_studio_world_uses_portable_object_items_for_empty_effect_array(characters):
    world = validate_world({'schema_version': 1, 'characters': characters})
    _, schema = observation_request(world, {}, None, base_schema())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(observed([]))
    visual = schema['properties']['visible_effects']
    assert visual['maxItems'] == 0
    assert isinstance(visual['items'], dict) and visual['items']['type'] == 'object'
    assert not Draft202012Validator(schema).is_valid(observed([{}]))
    assert validate_observation(observed([]), world, {}, None) == observed([])


def test_plan_character_cannot_reassign_an_existing_identity():
    with pytest.raises(WorldError, match='another established'):
        observation_request(scene(), {'characters': [{'id': 'npc', 'name': 'Imposter'}]}, 'player', base_schema())
