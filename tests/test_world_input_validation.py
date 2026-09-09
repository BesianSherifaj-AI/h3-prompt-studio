"""Malformed editor records fail admission before they can break future turns."""
import copy

import pytest

from backend.world import WorldError, actor_context, validate_world
from test_world import sample_world


@pytest.mark.parametrize('changes', [
    {'effects': None}, {'effects': ['not an effect']},
    {'dialogue': None}, {'dialogue': ['not a dialogue line']},
    {'dialogue': [{'speaker_id': 'npc', 'text': None}]},
    {'summary': None}, {'basis': {}},
])
def test_malformed_event_records_rejected_before_actor_context(changes):
    world = sample_world()
    world['events'] = [{'id': 'past', 'witness_ids': ['player'], 'summary': 'A past event.', **changes}]
    before = copy.deepcopy(world)
    with pytest.raises(WorldError, match='Event|Accepted dialogue'):
        validate_world(world)
    assert world == before


@pytest.mark.parametrize('changes', [
    {'private': True, 'known_by': None},
    {'private': True, 'known_by': 'player'},
    {'private': True, 'known_by': ['missing']},
    {'private': 'false'},
])
def test_invalid_objective_visibility_does_not_break_or_leak_actor_context(changes):
    world = sample_world()
    world['objectives'][0].update(changes)
    with pytest.raises(WorldError, match='Objective|objective'):
        validate_world(world)


@pytest.mark.parametrize('group', ['locations', 'entities'])
def test_descriptions_used_in_mechanical_prompts_must_be_text(group):
    world = sample_world()
    world[group][0]['description'] = {'not': 'text'}
    with pytest.raises(WorldError, match='description'):
        validate_world(world)


@pytest.mark.parametrize('changes', [{'label': {'not': 'text'}}, {'locked': 'false'}, {'target_id': []}])
def test_malformed_exits_are_rejected_before_actions_are_displayed(changes):
    world = sample_world()
    world['locations'][0]['exits'][0].update(changes)
    with pytest.raises(WorldError, match='exit|route'):
        validate_world(world)


def test_sparse_legacy_events_and_valid_private_objective_still_work():
    world = sample_world()
    world['events'] = [{'id': 'past', 'witness_ids': ['player']}]
    world['objectives'][0].update(private=True, known_by=['npc'])
    context = actor_context(world, 'player')
    assert context['recent_events'][0]['summary'] == ''
    assert context['recent_events'][0]['effects'] == []
    assert context['objectives'] == []
    assert actor_context(world, 'npc')['objectives'][0]['id'] == 'quest'
