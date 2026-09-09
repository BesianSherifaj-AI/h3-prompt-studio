"""The reported Forward / Back path must queue real saved-frame conditioning."""
import copy

from test_stories import rig, uid
from test_visible_scene_grounding import binding_body, city


def test_forward_then_back_uses_saved_start_and_return_frames_without_ai(rig):
    story, original = city(rig)
    rig.manager.bind_scene_player(story['id'], binding_body(rig, story))
    before_world = copy.deepcopy(rig.manager._state(story)['world'])
    def forbidden(*args, **kwargs):
        raise AssertionError('Directional movement must not load or call an LLM.')
    rig.resources.run_ai = forbidden
    rig.client.complete_json_result = forbidden
    turns = []
    for direction in ('forward', 'backward'):
        result = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'Move ' + direction,
            'intent': {'kind': 'move', 'direction': direction, 'extent': 'step', 'camera': 'player'}})
        rig.manager.process(story['id'], result['id'])
        turn = rig.manager._turn(story, result['id'])
        assert turn['status'] == 'succeeded', turn.get('error')
        assert turn['planning_mode'] == 'deterministic_movement'
        assert 'continuation_source' not in turn['project']['comfy_render']
        turns.append(turn)
    outward, back = turns
    assert outward['project']['mode'] == 'i2va'
    assert outward['movement_conditioning']['start_run_id'] == original['id']
    assert back['project']['mode'] == 'fl2va', back.get('navigation_move')
    assert back['movement_conditioning']['start_run_id'] == outward['run_id']
    assert back['movement_conditioning']['return_run_id'] == original['id']
    assert [asset['role'] for asset in back['project']['assets'] if asset.get('enabled', True) and asset['role'] != 'context'] == ['first_frame', 'last_frame']
    state = rig.manager._state(story)
    assert state['navigation']['position'] == [0, 0]
    assert state['world']['entities'] == before_world['entities']
    assert len(state['world']['events']) == len(before_world['events']) + 2
    assert len(rig.videos.queues) == 2 and not rig.assets.requests
    # A saved return restores visual conditioning, not the old world's events.
    reopened = rig.reopen()
    assert reopened._state(reopened._story(story['id']))['navigation'] == state['navigation']
