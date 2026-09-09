"""Explicit player appearance remains usable when the frame scan cannot help."""
import copy

import pytest

from backend.world import project_from_world, validate_world
from test_stories import rig, uid
from test_visible_scene_grounding import binding_body, city


def appearance_body(rig, story, appearance='the person in the orange jacket'):
    return {'request_id': uid(), 'run_id': story['active_run_id'],
            'branch_id': story['active_branch_id'],
            'configuration_revision': rig.manager._state(story)['configuration_revision'],
            'appearance': appearance}


@pytest.mark.parametrize('scan', ['absent', 'failed', 'no_people', 'stale'])
def test_explicit_appearance_needs_no_successful_scan_and_remembers_current_view(rig, scan):
    story, run = city(rig, structured=False)
    if scan == 'failed':
        story['scene_inspections'] = {uid(): {'run_id': run['id'], 'status': 'failed', 'error': 'Vision unavailable.'}}
    elif scan == 'no_people':
        story['observed_by_run'][run['id']]['visible_scene'] = {'setting': 'A street.', 'candidates': []}
    elif scan == 'stale':
        story['observed_by_run'][run['id']].update(inspection_status='not_run', cached_scene_run_id=uid())
    request = appearance_body(rig, story, '  the person in the orange jacket  ')
    historical = copy.deepcopy(story['state_by_run'])
    observations = copy.deepcopy(story['observed_by_run'])
    response = rig.manager.bind_scene_player(story['id'], request)
    state = rig.manager._state(story)
    assert state['world']['characters'][0]['state']['visual_anchor'] == 'the person in the orange jacket'
    assert state['world']['characters'][0]['description'] == 'the person in the orange jacket'
    assert state['player_visual_anchor'] == {'run_id': run['id'], 'candidate_id': None,
        'description': 'the person in the orange jacket', 'position': '', 'source': 'user_description'}
    assert response['player_visual_anchor'] == state['player_visual_anchor']
    assert state['navigation']['current_run_id'] == run['id']
    assert state['navigation']['views'][-1]['basis'] == 'explicit_player_binding'
    assert state['world']['locations'] == [] and state['world']['entities'] == []
    assert story['state_by_run'] == historical and story['observed_by_run'] == observations
    assert not rig.client.calls and not rig.videos.queues and not rig.assets.requests


def test_description_updates_only_player_preserving_npc_inventory_history_and_failed_turn(rig):
    story, run = city(rig)
    state = rig.manager._state(story)
    world = copy.deepcopy(state['world'])
    world['characters'].append({'id': 'npc', 'name': 'Nora', 'description': 'An orange jacket.', 'control': 'npc'})
    world['entities'].append({'id': 'coin', 'name': 'Coin', 'kind': 'prop', 'holder_id': 'player'})
    state['world'] = validate_world(world)
    state['project'] = project_from_world(state['project'], state['world'])
    state['scene_target_bindings'] = {'old-player-candidate': 'player', 'npc-candidate': 'npc'}
    story['state_by_run'][run['id']] = copy.deepcopy(state)
    submitted = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I wait.'})
    rig.manager._turn(story, submitted['id'])['status'] = 'failed'
    before = copy.deepcopy(story)
    request = appearance_body(rig, story, 'Nora in the orange jacket')
    rig.manager.bind_scene_player(story['id'], request)
    state = rig.manager._state(story)
    old_state = before['branch_states'][story['active_branch_id']]
    assert state['world']['characters'][0]['id'] == 'player'
    assert state['world']['characters'][1:] == old_state['world']['characters'][1:]
    assert state['world']['entities'] == old_state['world']['entities']
    assert state['world']['locations'] == old_state['world']['locations']
    assert state['world']['events'] == old_state['world']['events']
    assert state['scene_target_bindings'] == {'npc-candidate': 'npc'}
    assert story['turns'] == before['turns'] and story['state_by_run'] == before['state_by_run']
    assert story['active_run_id'] == run['id'] and story['branches'] == before['branches']
    once = copy.deepcopy(story)
    rig.manager.bind_scene_player(story['id'], request)
    assert story == once
    with pytest.raises(ValueError, match='another selection'):
        rig.manager.bind_scene_player(story['id'], {**request, 'appearance': 'the person in blue'})
    assert story == once


def test_explicit_description_unlocks_plain_movement_without_scan_or_assistant(rig):
    story, _ = city(rig, structured=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('Description binding and a plain step must not load or call the assistant.')
    rig.resources.run_ai = forbidden
    rig.client.complete_json_result = forbidden
    rig.manager.bind_scene_player(story['id'], appearance_body(rig, story))
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I walk forward.',
        'intent': {'kind': 'move', 'direction': 'forward', 'extent': 'step', 'camera': 'player'}})
    rig.manager.process(story['id'], public['id'])
    turn = rig.manager._turn(story, public['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    assert turn['planning_mode'] == 'deterministic_movement'
    assert turn['observation']['inspection_status'] == 'not_run'
    assert len(rig.videos.queues) == 1 and not rig.assets.requests
    assert len(rig.manager._state(story)['world']['characters']) == 1


@pytest.mark.parametrize('change', [
    {'appearance': ''}, {'appearance': '   '}, {'appearance': 'x' * 501},
    {'appearance': None}, {'appearance': 42}, {'appearance': []},
    {'candidate_id': 'some-person'}, {'run_id': 'older-run'}, {'branch_id': 'other-branch'},
    {'configuration_revision': 0}, {'configuration_revision': True}, {'configuration_revision': '1'},
])
def test_invalid_or_stale_description_never_changes_state(rig, change):
    story, _ = city(rig)
    request = {**appearance_body(rig, story), **change}
    before = copy.deepcopy(story)
    with pytest.raises(ValueError):
        rig.manager.bind_scene_player(story['id'], request)
    assert story == before


@pytest.mark.parametrize('missing', ['run_id', 'branch_id', 'configuration_revision'])
def test_description_requires_all_current_frame_guards(rig, missing):
    story, _ = city(rig)
    request = appearance_body(rig, story)
    del request[missing]
    before = copy.deepcopy(story)
    with pytest.raises(ValueError, match='current scene and revision'):
        rig.manager.bind_scene_player(story['id'], request)
    assert story == before


@pytest.mark.parametrize('status', ['awaiting_acceptance', 'inspection_failed', 'uncertain', 'planning'])
def test_description_cannot_change_pending_turn_identity(rig, status):
    story, _ = city(rig)
    story['turns'].append({'id': uid(), 'status': status})
    before = copy.deepcopy(story)
    with pytest.raises(ValueError, match='Finish or review'):
        rig.manager.bind_scene_player(story['id'], appearance_body(rig, story))
    assert story == before


def test_description_does_not_bypass_known_npc_candidate_rejection(rig):
    story, run = city(rig)
    state = rig.manager._state(story)
    state['world']['characters'].append({'id': 'npc', 'name': 'Nora', 'description': 'Blue coat and brown trousers.', 'control': 'npc'})
    state['world'] = validate_world(state['world'])
    story['observed_by_run'][run['id']]['visible_scene']['candidates'][0]['known_id'] = 'npc'
    request = binding_body(rig, story)
    before = copy.deepcopy(story)
    with pytest.raises(ValueError, match='another established character'):
        rig.manager.bind_scene_player(story['id'], request)
    assert story == before


def test_failed_project_validation_does_not_partially_write_player_description(rig, monkeypatch):
    story, _ = city(rig)
    def reject(*args, **kwargs):
        raise ValueError('Project rejected.')
    monkeypatch.setattr('backend.world.project_from_world', reject)
    before = copy.deepcopy(story)
    with pytest.raises(ValueError, match='Project rejected'):
        rig.manager.bind_scene_player(story['id'], appearance_body(rig, story))
    assert story == before


def test_switching_visible_player_keeps_only_new_you_mapping_and_preserves_npcs(rig):
    story, run = city(rig)
    state = rig.manager._state(story)
    state['world']['characters'].append({'id': 'npc', 'name': 'Nora', 'description': 'Gray jacket.', 'control': 'npc'})
    state['world'] = validate_world(state['world'])
    story['observed_by_run'][run['id']]['visible_scene']['candidates'].append({
        'kind': 'person', 'known_id': None, 'label': 'Person in gray',
        'description': 'Gray jacket.', 'position': 'By the street lamp'})
    npc_candidate = rig.manager.scene_inventory(story)['targets'][-1]['id']
    state['scene_target_bindings'] = {npc_candidate: 'npc'}
    first = binding_body(rig, story, 0)
    rig.manager.bind_scene_player(story['id'], first)
    second = binding_body(rig, story, 1)
    rig.manager.bind_scene_player(story['id'], second)
    once = copy.deepcopy(rig.manager._state(story))
    assert once['scene_target_bindings'] == {npc_candidate: 'npc', second['candidate_id']: 'player'}
    targets = rig.manager.scene_inventory(story)['targets']
    assert [target['known_id'] for target in targets] == [None, 'player', None, 'npc']
    assert once['player_visual_anchor']['candidate_id'] == second['candidate_id']
    assert once['world']['characters'][0]['description'] == targets[1]['description']
    rig.manager.bind_scene_player(story['id'], first)
    rig.manager.bind_scene_player(story['id'], second)
    assert rig.manager._state(story) == once
    assert story['active_run_id'] == run['id'] and not rig.client.calls and not rig.videos.queues
