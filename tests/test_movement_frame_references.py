"""Native movement frames retain the established, saved design references."""
import copy

import pytest

from backend.compiler import compile_project
from backend.world import validate_world
from test_game_director import setup_scene
from test_stories import reference, rig, uid
from test_visible_scene_grounding import binding_body, visible_scene


@pytest.mark.parametrize('change_reference', [False, True])
def test_forward_back_with_bound_design_keeps_identity_and_rejects_changed_design(rig, change_reference):
    project, _ = setup_scene()
    project['subjects'] = project['subjects'][:1]
    design = reference('Player design', 'face', 'player-face')
    project['assets'] = [design]
    project['subjects'][0].update(description='', asset_ids=[design['id']])
    project['comfy_render'] = {'seed': 42, 'resolution': '0.2', 'steps': 8}
    original = rig.videos.add(project)
    world = validate_world({'schema_version': 1, 'characters': [
        {'id': 'player', 'name': 'Alex', 'control': 'player', 'asset_ids': [design['id']]}]})
    public = rig.manager.create({'request_id': uid(), 'project': project, 'source_run_id': original['id'],
        'mode': 'game', 'player_name': 'Alex', 'world': world, 'player_character_id': 'player',
        'settings': {'duration': 3, 'resolution': '0.2'}})
    story = rig.manager._story(public['id'])
    story['observed_by_run'][original['id']] = {'visible_scene': visible_scene()}
    rig.manager.bind_scene_player(story['id'], binding_body(rig, story))
    def forbidden(*args, **kwargs):
        raise AssertionError('Ordinary movement must not call an LLM.')
    rig.resources.run_ai = forbidden
    turns = []
    for direction in ('forward', 'backward'):
        if direction == 'backward' and change_reference:
            draft = copy.deepcopy(rig.manager._state(story)['project'])
            next(asset for asset in draft['assets'] if asset['id'] == design['id'])['description'] = 'A different outfit design.'
            rig.manager.update(story['id'], {'project': draft})
        public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'Move ' + direction,
            'intent': {'kind': 'move', 'direction': direction, 'extent': 'step', 'camera': 'player'}})
        rig.manager.process(story['id'], public['id'])
        turn = rig.manager._turn(story, public['id'])
        assert turn['status'] == 'succeeded', turn.get('error')
        assert compile_project(turn['project'])['valid']
        assert next(subject for subject in turn['project']['subjects'] if subject['id'] == 'player')['asset_ids'] == [design['id']]
        assert next(asset for asset in turn['project']['assets'] if asset['id'] == design['id'])['role'] == 'context'
        turns.append(turn)
    assert turns[0]['project']['mode'] == 'i2va'
    assert turns[1]['project']['mode'] == ('i2va' if change_reference else 'fl2va'), turns[1]['navigation_move']
    if not change_reference:
        assert turns[1]['movement_conditioning']['return_run_id'] == original['id']
    state = rig.manager._state(story)
    player = next(row for row in state['world']['characters'] if row['id'] == 'player')
    assert player['asset_ids'] == player['identity_asset_ids'] == [design['id']]
    assert state['world']['entities'] == []
    assert len(rig.videos.queues) == 2 and not rig.assets.requests
    reopened = rig.reopen()
    assert reopened._state(reopened._story(story['id']))['world'] == state['world']
