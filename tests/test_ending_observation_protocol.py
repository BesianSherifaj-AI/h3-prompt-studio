"""Fresh inspection covers every intended identity without rewriting old evidence."""
import copy

import pytest
from jsonschema import Draft202012Validator

from backend.ending_observation import ending_output_budget, observation_request, validate_observation
from backend.world import WorldError
from test_ending_observation import base_schema, observed, scene
from test_stories import plan, reference, render, rig, story, uid
from test_story_rewrite_stop import directed


def contract():
    return {'actors': [{'subject_id': cid, 'activity': 'hold', 'start': 'standing', 'action': '', 'end': 'standing'}
                       for cid in ('player', 'npc')],
            'objects': [{'entity_id': eid, 'name': eid, 'description': '', 'count': 1, 'start': '', 'end': ''}
                        for eid in ('key', 'coat')]}


def complete():
    return {**observed(), 'continuity_checks': [
        {'kind': kind, 'id': cid, 'status': 'uncertain', 'detail': 'Partly occluded; not enough visible evidence.'}
        for kind, cid in [('actor', 'player'), ('actor', 'npc'), ('object', 'key'), ('object', 'coat')]]}


def test_strict_new_schema_and_validator_require_full_coverage_allowing_uncertainty():
    world, intended = scene(), contract()
    before = copy.deepcopy((world, intended))
    context, schema = observation_request(world, {}, 'player', base_schema(), intended, require_coverage=True)
    assert 'continuity_checks' in schema['required']
    assert schema['properties']['continuity_checks']['minItems'] == schema['properties']['continuity_checks']['maxItems'] == 4
    assert 'EVERY actor and object identity' in context['continuity_check_rules']
    assert 'cannot establish continuous stillness' in context['continuity_check_rules']
    Draft202012Validator(schema).validate(complete())
    assert validate_observation(complete(), world, {}, 'player', intended, require_coverage=True) == complete()
    assert (world, intended) == before


@pytest.mark.parametrize('fault', ['missing', 'empty', 'partial', 'duplicate', 'unknown', 'too_long'])
def test_strict_inspection_rejects_unassessed_identities_and_invalid_rows(fault):
    result = complete()
    if fault == 'missing':
        result.pop('continuity_checks')
    elif fault == 'empty':
        result['continuity_checks'] = []
    elif fault == 'partial':
        result['continuity_checks'].pop()
    elif fault == 'duplicate':
        result['continuity_checks'][-1] = copy.deepcopy(result['continuity_checks'][0])
    elif fault == 'unknown':
        result['continuity_checks'][-1]['id'] = 'invented'
    else:
        result['continuity_checks'][0]['detail'] = 'x' * 241
    with pytest.raises(WorldError, match='Continuity checks|Ending inspection'):
        validate_observation(result, scene(), {}, 'player', contract(), require_coverage=True)


def test_legacy_optional_partial_and_long_detail_observations_are_unchanged():
    value = complete()
    value['continuity_checks'] = value['continuity_checks'][:1]
    value['continuity_checks'][0]['detail'] = 'x' * 400
    _, schema = observation_request(scene(), {}, 'player', base_schema(), contract())
    assert 'continuity_checks' not in schema['required']
    assert 'minItems' not in schema['properties']['continuity_checks']
    for legacy in (observed(), value):
        Draft202012Validator(schema).validate(legacy)
        assert validate_observation(legacy, scene(), {}, 'player', contract()) == legacy


@pytest.mark.parametrize('count,expected', [(0, 1400), (4, 1400), (20, 2400), (36, 4000), (56, 4096)])
def test_new_ending_budget_scales_with_required_identity_count_and_stays_bounded(count, expected):
    assert ending_output_budget({'properties': {'continuity_checks': {'minItems': count}}}) == expected
    assert ending_output_budget({}, 5000) == 4096


@pytest.mark.parametrize('partial', [False, True])
def test_fresh_incomplete_inspection_preserves_previous_branch_and_requires_recovery(rig, partial):
    session = story(rig, settings={'review_before_render': False})
    first = render(rig, session)
    before = copy.deepcopy(rig.manager._state(rig.manager._story(session['id'])))
    calls = []
    def incomplete(model, system, content, schema, **options):
        calls.append((schema, options))
        assert 'continuity_checks' in schema['required']
        result = copy.deepcopy(rig.client.observation)
        if partial:
            result['continuity_checks'] = [{'kind': 'actor', 'id': rig.project['subjects'][0]['id'],
                'status': 'uncertain', 'detail': 'Occluded.'}]
        return {'result': result, 'diagnostics': {}}
    rig.client.complete_json_result = incomplete
    turn = render(rig, session, planned=plan(dialogue=[]))
    record = rig.manager._story(session['id'])
    internal = rig.manager._turn(record, turn['id'])
    assert turn['status'] == 'inspection_failed'
    assert record['active_run_id'] == first['run_id'] and rig.manager._state(record) == before
    request = next(request for request in internal['assistant_requests'].values() if request['stage'] == 'ending-inspection')
    assert internal['ending_observation_protocol'] == request['observation_protocol_version'] == 2
    assert len(calls) == 1 and calls[0][1]['max_tokens'] == ending_output_budget(calls[0][0])
    assert len(rig.videos.queues) == 2, 'The incomplete check must not trigger another render.'
    # Retrying the inspection creates a new explicit strict request only.
    rig.manager.action(session['id'], turn['id'], 'retry-inspection', {'request_id': uid()})
    assert 'ending_observation_protocol' not in internal
    assert not any(request['stage'] == 'ending-inspection' for request in internal['assistant_requests'].values())


def test_saved_legacy_inspection_keeps_original_request_hash_schema_and_cached_answer_on_recovery(rig):
    session = story(rig, settings={'assistant_provider': 'supervised', 'review_before_render': False, 'duration': 5})
    value = directed(rig)
    value.update(dialogue=[], effects=[])
    value['direction']['shots'][0]['dialogue_indices'] = []
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look toward Nora.', 'planned': value})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    turn['ending_observation_protocol'] = 1
    ending = reference('Saved ending', 'pose', 'saved-ending')
    rig.manager.ending_asset = lambda run_id: copy.deepcopy(ending)
    rig.manager.process(session['id'], turn['id'])
    assert turn['status'] == 'awaiting_assistant', turn.get('error')
    old_hash, request = next((key, request) for key, request in turn['assistant_requests'].items() if request['stage'] == 'ending-inspection')
    assert 'continuity_checks' not in request['schema']['required']
    request.update(status='completed', result=copy.deepcopy(rig.client.observation))
    request.pop('observation_protocol_version')  # Simulate an actual pre-protocol saved request.
    turn.pop('ending_observation_protocol')
    original_request = copy.deepcopy(request)
    rig.manager._save(record)
    restored = rig.reopen()
    restored.ending_asset = lambda run_id: copy.deepcopy(ending)
    try:
        restored.process(session['id'], turn['id'])
        saved = restored._turn(restored._story(session['id']), turn['id'])
        assert saved['status'] == 'succeeded', saved.get('error')
        assert saved['ending_observation_protocol'] == 1
        assert saved['last_assistant_hash'] == old_hash
        assert saved['assistant_requests'][old_hash] == original_request
        assert saved['observation'] == rig.client.observation and 'continuity_checks' not in saved['observation']
        assert not rig.client.calls, 'Recovery must reuse the saved supervised response.'
        restored.action(session['id'], turn['id'], 'reroll', {'request_id': uid()})
        restored.process(session['id'], turn['id'])
        assert saved['status'] == 'awaiting_assistant' and saved['ending_observation_protocol'] == 2
        fresh = next(request for request in saved['assistant_requests'].values() if request['stage'] == 'ending-inspection')
        assert fresh['observation_protocol_version'] == 2 and 'continuity_checks' in fresh['schema']['required']
        assert fresh['context_hash'] != old_hash
    finally:
        restored.close()
