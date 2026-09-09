"""Explicit image labels may update a waypoint without certifying draft effects."""
import copy

import pytest

from backend.navigation import navigation_return, remember_bound_scene
from test_navigation import accept, prepare, session
from test_stories import rig
from test_visible_scene_grounding import binding_body, city


def test_existing_accepted_origin_can_be_labelled_then_used_for_forward_back(rig):
    story, original = city(rig)
    historical = copy.deepcopy(rig.manager._state(story))
    story['state_by_run'][original['id']] = copy.deepcopy(historical)
    rig.manager.bind_scene_player(story['id'], binding_body(rig, story))
    state = rig.manager._state(story)
    assert remember_bound_scene(state, original['id'], accepted_state=historical)
    assert story['state_by_run'][original['id']] == historical
    assert state['world']['entities'] == historical['world']['entities'] == []
    assert state['navigation']['views'][-1]['basis'] == 'explicit_player_binding'
    forward = prepare(story)
    accept(story, forward, 'forward-image')
    back = prepare(story, 'backward')
    assert navigation_return(story, back) == original['id']


@pytest.mark.parametrize('changed', ['inventory', 'enemy', 'style'])
def test_binding_cannot_certify_unrendered_gameplay_or_style_changes(changed):
    story = session()
    historical = copy.deepcopy(story['state_by_run']['origin'])
    state = story['branch_states']['main']
    player = state['world']['characters'][0]
    player['description'] = 'Orange coat, standing at the left.'
    player['state'].update(visual_anchor=player['description'], visual_anchor_run_id='origin')
    state['player_visual_anchor'] = {'run_id': 'origin', 'description': player['description']}
    if changed == 'inventory':
        state['world']['entities'][0]['holder_id'] = 'player'
    elif changed == 'enemy':
        state['world']['characters'][1]['state']['health'] = 0
    else:
        state['settings']['style'] = 'Photorealistic'
    before = copy.deepcopy(state)
    assert not remember_bound_scene(state, 'origin', accepted_state=historical)
    assert state == before
    assert story['state_by_run']['origin'] == historical


def test_binding_updates_current_endpoint_only_and_is_idempotent():
    story = session()
    accept(story, prepare(story), 'forward-image')
    state = story['branch_states']['main']
    historical = copy.deepcopy(state)
    earlier = copy.deepcopy(state['navigation']['views'][0])
    player = state['world']['characters'][0]
    player['description'] = 'Orange coat, standing at the left.'
    player['state'].update(visual_anchor=player['description'], visual_anchor_run_id='forward-image')
    state['player_visual_anchor'] = {'run_id': 'forward-image', 'description': player['description']}
    assert remember_bound_scene(state, 'forward-image', accepted_state=historical)
    assert state['navigation']['position'] == [0, 1]
    assert state['navigation']['views'][0] == earlier
    once = copy.deepcopy(state)
    assert remember_bound_scene(state, 'forward-image', accepted_state=historical)
    assert state == once
