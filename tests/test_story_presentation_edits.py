"""Presentation edits cannot erase an otherwise unchanged game outcome."""
import copy

import pytest

from test_stories import observed_for_schema, rig, story, uid
from test_story_rewrite_stop import directed


def combat_turn(rig):
    session = story(rig, settings={'assistant_provider': 'supervised', 'duration': 3})
    old = directed(rig, action='Mira lands one final strike; Nora falls defeated.')
    old.update(final_state='Nora is defeated with zero health.',
               dialogue=[{'speaker': 'Nora', 'text': 'CLANG!', 'language': 'English', 'delivery': 'loud'}],
               effects=[{'kind': 'character_state', 'character_id': rig.project['subjects'][1]['id'], 'key': key, 'value': value}
                        for key, value in [('health', 0), ('defeated', True), ('dead', True)]])
    old['beats'][0].update(**{key: old[key] for key in ('action', 'setting', 'final_state')})
    old['direction']['shots'][0].update(duration=3, performance=old['action'])
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I deliver the final blow.', 'planned': old})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    rig.manager._change(record, turn, status='awaiting_review')
    return record, turn, old


@pytest.mark.parametrize('field', ['language', 'delivery', 'words', 'remove_dialogue', 'description', 'voice', 'assets', 'transition'])
def test_presentation_edit_preserves_effects_and_rebuilds_only_direction(rig, field):
    record, turn, old = combat_turn(rig)
    edited = copy.deepcopy(old)
    if field in ('language', 'delivery'):
        edited['dialogue'][0][field] = 'Albanian' if field == 'language' else 'quiet'
    elif field == 'words':
        edited['dialogue'][0]['text'] = 'I yield.'
    elif field == 'remove_dialogue':
        edited['dialogue'] = []
    elif field in ('description', 'voice'):
        edited['characters'][1][field] = 'A clear corrected appearance.' if field == 'description' else 'A low, calm voice.'
    elif field == 'assets':
        edited['asset_requests'] = [{'name': 'Courtyard', 'prompt': 'An empty courtyard.',
            'semantic_role': 'background', 'person_name': '', 'prompt_tag': 'courtyard'}]
    else:
        edited['transition'] = 'cut'
    result = rig.manager._edited_plan(record, turn, edited)
    assert result['effects'] == old['effects']
    assert result['beats'] == old['beats']
    assert 'direction' not in result
    assert turn['plan'] == old, 'Preparing an edit must not mutate the saved plan.'


def test_camera_only_edit_keeps_explicit_camera_and_game_effects(rig):
    record, turn, old = combat_turn(rig)
    edited = copy.deepcopy(old)
    edited['direction']['shots'][0]['camera']['movement'] = 'Slow push in'
    result = rig.manager._edited_plan(record, turn, edited)
    assert result['direction'] == edited['direction']
    assert result['effects'] == old['effects']


@pytest.mark.parametrize('field', ['action', 'setting', 'final_state', 'name', 'id'])
def test_changed_physical_event_or_identity_still_discards_stale_effects(rig, field):
    record, turn, old = combat_turn(rig)
    edited = copy.deepcopy(old)
    if field in ('name', 'id'):
        edited['characters'][1][field] = 'Another visitor' if field == 'name' else uid()
    else:
        edited[field] = {'action': 'Mira lowers her sword and waits.', 'setting': 'A different room.',
                         'final_state': 'Nora remains unharmed.'}[field]
    result = rig.manager._edited_plan(record, turn, edited)
    assert result['effects'] == [] and 'direction' not in result
    assert result['beats'][0]['action'] == edited['action']


def test_removing_foley_speech_keeps_final_blow_effects_through_commit(rig):
    record, turn, old = combat_turn(rig)
    edited = copy.deepcopy(old)
    edited['dialogue'] = []
    rig.manager.action(record['id'], turn['id'], 'approve', {'request_id': uid(), 'plan': edited})
    stages = []
    def predict(execution, current, stage, actor, system, content, schema, images=()):
        stages.append(stage)
        if stage == 'director':
            result = copy.deepcopy(old['direction'])
            result['shots'][0]['dialogue_indices'] = []
            return result
        assert stage == 'ending-inspection'
        return observed_for_schema(rig.client.observation, schema)
    rig.manager._predict = predict
    rig.manager.process(record['id'], turn['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    world = rig.manager.get(record['id'])['world']
    enemy = next(c for c in world['characters'] if c['id'] == rig.project['subjects'][1]['id'])
    assert enemy['state'] == {'health': 0, 'defeated': True, 'dead': True}
    assert world['events'][-1]['dialogue'] == []
    assert stages == ['director', 'ending-inspection'] and len(rig.videos.queues) == 1


def test_invalid_new_effect_in_a_presentation_edit_is_rejected_before_gpu(rig):
    record, turn, old = combat_turn(rig)
    edited = copy.deepcopy(old)
    edited['dialogue'] = []
    edited['effects'] = [{'kind': 'holder', 'entity_id': 'missing-prop', 'character_id': record['player_name']}]
    rig.manager.action(record['id'], turn['id'], 'approve', {'request_id': uid(), 'plan': edited})
    rig.manager.process(record['id'], turn['id'])
    assert turn['status'] == 'failed' and 'unknown object' in turn['error']
    assert not rig.assets.requests and not rig.videos.queues
