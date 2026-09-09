"""Failures must preserve the last accepted world and repair only the bad role."""
import copy

import pytest

from test_stories import rig, story, render, plan, uid


def test_invalid_effect_cannot_advance_branch_or_partially_commit_world(rig):
    session = story(rig)
    first = render(rig, session)
    before = rig.manager.get(session['id'])
    invalid = plan(effects=[{'kind': 'holder', 'entity_id': 'missing-object', 'character_id': before['player_character_id']}])
    turn = render(rig, session, planned=invalid)
    after = rig.manager.get(session['id'])
    assert turn['status'] == 'failed'
    assert not turn.get('run_id'), 'Invalid effects are rejected before rendering.'
    assert after['active_run_id'] == first['run_id']
    assert after['branches'] == before['branches']
    assert after['world'] == before['world']
    assert after['observed_state'] == before['observed_state']
    assert len(rig.videos.queues) == 1


def test_failed_world_merge_does_not_publish_partially_mutated_state(rig, monkeypatch):
    session = story(rig)
    before = rig.manager.get(session['id'])
    def broken_commit(record, turn, run_id):
        record['branch_states'][turn['branch_id']]['world']['rules'].append('partial mutation')
        turn['accepted_state'] = {'broken': True}
        raise ValueError('Cannot merge this edited world.')
    monkeypatch.setattr(rig.manager, '_commit_state', broken_commit)
    turn = render(rig, session)
    after = rig.manager.get(session['id'])
    assert turn['status'] == 'failed'
    assert after['active_run_id'] is None
    assert after['branches'] == before['branches']
    assert after['world'] == before['world']
    internal = rig.manager._turn(rig.manager._story(session['id']), turn['id'])
    assert 'accepted_state' not in internal


def test_semantic_repair_preserves_good_actor_cache_and_supplies_rejected_answer(rig):
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I ask Nora about the key.'})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    execution = rig.manager._execution(record, turn)
    calls = []
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
    def answer(model, system, content, schema, **kwargs):
        calls.append((system, copy.deepcopy(content)))
        return {'result': {'answer': 'Nora nods.'}, 'diagnostics': {}}
    rig.client.complete_json_result = answer
    rig.manager._predict(execution, turn, 'actor', 'first-npc', 'Act.', {}, schema)
    turn['assistant_repair'] = {'stage': 'actor', 'actor_id': 'second-npc', 'reason': 'Wrong actor.',
                                'rejected_response': '{"answer":"The player decides to leave."}'}
    rig.manager._predict(execution, turn, 'actor', 'first-npc', 'Act.', {}, schema)
    assert len(calls) == 1, 'Repairing another NPC must not invalidate successful actors.'
    rig.manager._predict(execution, turn, 'actor', 'second-npc', 'Act.', {}, schema)
    assert 'Wrong actor.' in calls[-1][0]
    assert any('The player decides to leave.' in part.get('text', '') for part in calls[-1][1])
    assert 'The player decides to leave.' not in calls[-1][0], 'Rejected output is data, not system instructions.'
    assert 'rejected_response' not in rig.manager.get(session['id'])['turns'][-1]['assistant_repair']
    assert turn['assistant_repair']['rejected_response']


def test_actor_coordinator_and_director_share_one_resource_lease(rig, monkeypatch):
    session = story(rig, player_character_id=rig.project['subjects'][0]['id'])
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look around.'})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
    rig.client.complete_json_result = lambda *a, **kw: {'result': {'answer': 'Ready.'}, 'diagnostics': {}}
    def staged_plan(**kwargs):
        assert kwargs['premise'] == record['premise']
        for stage in ('actor', 'roleplay', 'director'):
            kwargs['predict'](stage, stage, 'Write.', {}, schema)
        return plan()
    monkeypatch.setattr('backend.game_director.plan_turn', staged_plan)
    rig.manager.plan(rig.manager._execution(record, turn), turn, turn['snapshot']['project'])
    assert len(rig.resources.calls) == 1
    assert len(turn['assistant_requests']) == 3


@pytest.mark.parametrize('action,expected', [('retry', 'new-model'), ('resume', 'old-model')])
def test_explicit_retry_can_switch_bad_model_while_resume_keeps_frozen_model(rig, action, expected):
    selected = {'model': 'old-model'}
    rig.manager.get_settings = lambda: selected.copy()
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I ask Nora about the key.'})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    turn['assistant_requests'] = {'first': {'id': uid(), 'stage': 'actor', 'model': 'old-model', 'status': 'completed', 'result': {'answer': 'Old.'}}}
    rig.manager._change(record, turn, status='failed', error='Old model could not write a valid response.')
    selected['model'] = 'new-model'
    snapshot = copy.deepcopy(turn['snapshot'])
    rig.manager.action(session['id'], turn['id'], action, {'request_id': uid()})
    assert turn['assistant_model'] == expected
    assert turn['snapshot'] == snapshot
    assert bool(turn['assistant_requests']) == (action == 'resume')
