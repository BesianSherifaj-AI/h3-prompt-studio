"""Revision, continuation and provider boundaries independent of model quality."""
import copy
import pytest
from backend.story_state import AwaitingAssistant
from backend.story_state import preserve_edits
from test_stories import rig, story, render, plan, uid


def test_edit_during_turn_freezes_attempt_and_preserves_new_instruction(rig):
    session = story(rig)
    guide = {'id': uid(), 'revision': 1, 'text': 'Nora is cautious.', 'scope': 'next', 'enabled': True}
    session = rig.manager.update(session['id'], {'guides': [guide]})
    turn = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look at Nora.', 'planned': plan()})
    old = copy.deepcopy(rig.manager._turn(rig.manager._story(session['id']), turn['id'])['snapshot'])
    updated = rig.manager.update(session['id'], {'guides': [{**guide, 'text': 'Nora is curious.'}], 'settings': {'aspect_ratio': '9:16'}})
    internal = rig.manager._turn(rig.manager._story(session['id']), turn['id'])
    assert internal['snapshot'] == old
    assert updated['guides'][0]['revision'] == 2
    assert updated['settings']['aspect_ratio'] == '9:16'


def test_stale_configuration_rejected_without_adding_turn(rig):
    session = story(rig)
    rig.manager.update(session['id'], {'premise': 'A new premise'})
    with pytest.raises(ValueError, match='settings changed'):
        rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look around.', 'configuration_revision': session['configuration_revision']})
    assert rig.manager.get(session['id'])['turns'] == []


def test_supervised_result_idempotent_and_context_checked(rig):
    session = story(rig, settings={'assistant_provider': 'supervised'})
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look around.'})
    original = rig.manager._story(session['id']); turn = rig.manager._turn(original, public['id'])
    execution = rig.manager._execution(original, turn)
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer'], 'additionalProperties': False}
    with pytest.raises(AwaitingAssistant):
        rig.manager._predict(execution, turn, 'probe', None, 'Return JSON.', {'current': 'room'}, schema)
    turn['status'] = 'awaiting_assistant'
    request = rig.manager.supervised_requests()['requests'][0]
    with pytest.raises(ValueError, match='different context'):
        rig.manager.complete_supervised(request['id'], {'context_hash': 'wrong', 'result': {'answer': 'Ready'}})
    body = {'context_hash': request['context_hash'], 'result': {'answer': 'Ready'}}
    assert rig.manager.complete_supervised(request['id'], body)['accepted']
    assert rig.manager.complete_supervised(request['id'], body)['accepted']
    assert rig.manager._predict(execution, turn, 'probe', None, 'Return JSON.', {'current': 'room'}, schema) == body['result']
    assert not rig.resources.calls


def test_inspection_failure_keeps_video_and_acceptance_never_requeues(rig):
    rig.client.observation = RuntimeError('Vision temporarily unavailable')
    session = story(rig)
    turn = render(rig, session)
    assert turn['status'] == 'inspection_failed'
    assert turn['video']['status'] == 'succeeded'
    assert len(rig.videos.queues) == 1
    rig.manager.action(session['id'], turn['id'], 'accept-intended', {'request_id': uid()})
    rig.manager.process(session['id'], turn['id'])
    after = rig.manager.get(session['id'])
    assert after['active_run_id'] == turn['run_id']
    assert len(rig.videos.queues) == 1
    assert len(after['world']['events']) == 1


def test_branch_restores_historical_guidance_and_excludes_future(rig):
    session = story(rig, premise='The first room')
    first = render(rig, session)
    rig.manager.update(session['id'], {'premise': 'The later secret', 'guides': [{'id': uid(), 'revision': 1,
        'text': 'Nora has learned the secret.', 'scope': 'persistent', 'enabled': True}]})
    branched = rig.manager.branch(session['id'], first['run_id'])
    assert branched['premise'] == 'The first room'
    assert not branched['guides']


def test_accepted_effects_keep_next_turn_edits_without_resurrecting_removed_asset():
    before = {'characters': [{'id': 'npc', 'personality': 'quiet', 'location': 'room'}],
              'assets': [{'id': 'removed'}, {'id': 'kept'}], 'events': []}
    edited = copy.deepcopy(before)
    edited['characters'][0]['personality'] = 'curious'
    edited['assets'].pop(0)
    accepted = copy.deepcopy(before)
    accepted['characters'][0]['location'] = 'hall'
    accepted['assets'].append({'id': 'new'})
    accepted['events'].append({'id': 'turn-1'})
    result = preserve_edits(before, edited, accepted)
    assert result['characters'] == [{'id': 'npc', 'personality': 'curious', 'location': 'hall'}]
    assert [a['id'] for a in result['assets']] == ['kept', 'new']
    assert result['events'] == [{'id': 'turn-1'}]
