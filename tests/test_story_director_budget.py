"""Output limits reach the real story transport, including semantic repair."""
import copy

from jsonschema import Draft202012Validator

from backend.game_director import DIRECTION_SCHEMA
from backend.scene_contract import scene_contract_schema
from test_stories import rig, story, uid


def test_story_transport_scales_three_shot_cast_budget_and_keeps_actor_cache_on_repair(rig):
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I watch the three people.'})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    execution = rig.manager._execution(record, turn)
    schema = copy.deepcopy(DIRECTION_SCHEMA)
    shots = schema['properties']['shots']
    shots.update(minItems=3, maxItems=3)
    shots['items']['properties']['scene_contract'] = scene_contract_schema(['one', 'two', 'three'], required=True)
    scene = {'beat_id': 'beat-1', 'duration': 1,
        'camera': {'framing': 'wide', 'movement': 'static', 'height': 'eye level', 'speed': 'still', 'focus': 'Three people'},
        'performance': 'The three people remain seated.', 'sound': 'Quiet room tone.',
        'visible_subject_ids': ['one', 'two', 'three'], 'offscreen_subject_ids': [], 'dialogue_indices': [], 'transition': 'continuous',
        'scene_contract': {'actors': [{'subject_id': cid, 'activity': 'hold', 'start': 'seated', 'action': 'Wait quietly.', 'end': 'seated'}
                                     for cid in ('one', 'two', 'three')],
                           'objects': [], 'environment': 'A quiet room.', 'background_activity': ''}}
    answer = {'shots': [{**copy.deepcopy(scene), 'beat_id': 'beat-' + str(index)} for index in range(1, 4)]}
    actor_schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
    calls = []
    def complete(model, system, content, requested_schema, **options):
        result = answer if 'shots' in requested_schema['properties'] else {'answer': 'I will wait.'}
        Draft202012Validator(requested_schema).validate(result)
        calls.append({'model': model, 'system': system, 'content': copy.deepcopy(content), 'options': options})
        return {'result': copy.deepcopy(result), 'diagnostics': {'finish_reason': 'stop'}}
    rig.client.complete_json_result = complete

    actor = rig.manager._predict(execution, turn, 'actor', 'one', 'Act only as yourself.', {}, actor_schema)
    first = rig.manager._predict(execution, turn, 'director', 'director', 'Stage the approved beats.', {}, schema)
    turn['assistant_repair'] = {'stage': 'director', 'actor_id': 'director', 'reason': 'Keep the approved seated posture.',
                                'rejected_response': '{"shots": "Earlier invalid staging"}'}
    corrected = rig.manager._predict(execution, turn, 'director', 'director', 'Stage the approved beats.', {}, schema)
    assert rig.manager._predict(execution, turn, 'actor', 'one', 'Act only as yourself.', {}, actor_schema) == actor

    assert first == corrected == answer
    assert [call['options']['max_tokens'] for call in calls] == [450, 2900, 2900]
    assert calls[1]['options']['request_id'] != calls[2]['options']['request_id']
    assert calls[1]['options']['cancel_event'] is calls[2]['options']['cancel_event']
    assert 'Keep the approved seated posture.' in calls[2]['system']
    assert any('Earlier invalid staging' in part.get('text', '') for part in calls[2]['content'])
    assert 'Earlier invalid staging' not in calls[2]['system']
    assert len(calls) == 3, 'Correcting direction must retain the completed actor response.'
    assert all(request['status'] == 'completed' for request in turn['assistant_requests'].values())
    assert not rig.videos.queues and not rig.assets.requests
