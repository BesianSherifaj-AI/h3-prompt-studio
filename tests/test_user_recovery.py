"""Regressions for an invalid roleplay response before any render is submitted."""
import copy

import pytest

from backend.lmstudio import LMStudioError
from test_stories import rig, story, uid, reference, plan


SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer'], 'additionalProperties': False}


def saved_turn(rig, *, review=True):
    parent = rig.videos.add(rig.project)
    session = story(rig, source=parent['id'], settings={'review_before_render': review, 'duration': 5})
    body = {'request_id': uid(), 'message': 'I pick up the key and inspect it.'}
    public = rig.manager.submit(session['id'], body)
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    return session, record, turn, body


def test_failed_assistant_request_is_terminal_and_has_a_useful_saved_error(rig):
    session, record, turn, _ = saved_turn(rig)
    def fail(*args, **kwargs):
        raise LMStudioError('The roleplay response does not match its schema.', code='invalid_model_output', detail='dialogue must be an array')
    rig.client.complete_json_result = fail
    with pytest.raises(LMStudioError):
        rig.manager._predict(rig.manager._execution(record, turn), turn, 'roleplay', 'world', 'Return the scene.', {'location': 'room'}, SCHEMA)
    request = next(iter(turn['assistant_requests'].values()))
    assert request['status'] == 'failed', 'A terminal schema failure must not remain pending.'
    assert request.get('error') or request.get('diagnostics'), 'Recovery needs the actual failure cause.'
    assert not rig.videos.queues and not rig.assets.requests
    assert rig.manager.get(session['id'])['active_run_id'] == turn['parent_run_id']


def test_resume_before_a_plan_does_not_silently_approve_future_rendering(rig):
    session, record, turn, _ = saved_turn(rig, review=True)
    rig.manager._change(record, turn, status='failed', error='Roleplay response rejected.')
    rig.manager.action(session['id'], turn['id'], 'resume', {'request_id': uid()})
    assert turn['snapshot']['settings']['review_before_render'] is True
    assert not turn.get('approved'), 'Resume should honor Review before rendering until a plan is explicitly approved.'
    assert not turn.get('assets_approved'), 'Resume is not permission to skip an unseen asset review.'
    assert not rig.videos.queues and not rig.assets.requests


def test_retry_reuses_valid_actor_result_and_exact_parent_without_requeuing(rig):
    session, record, turn, original_body = saved_turn(rig, review=False)
    calls, fail_roleplay = [], [True]
    def answer(model, system, content, schema, **kwargs):
        calls.append((system, kwargs.get('request_id')))
        if system == 'Roleplay' and fail_roleplay[0]:
            raise LMStudioError('Roleplay schema error.', code='invalid_model_output')
        return {'result': {'answer': 'Ready'}, 'diagnostics': {'model': model, 'locally_validated': True}}
    rig.client.complete_json_result = answer
    execution = rig.manager._execution(record, turn)
    rig.manager._predict(execution, turn, 'actor', 'npc', 'Actor', {'location': 'room'}, SCHEMA)
    with pytest.raises(LMStudioError):
        rig.manager._predict(execution, turn, 'roleplay', 'world', 'Roleplay', {'location': 'room'}, SCHEMA)
    rig.manager._change(record, turn, status='failed', error='Roleplay schema error.')
    frozen, render_id = copy.deepcopy(turn['snapshot']), turn['render_request_id']
    # A lost POST response replays the same request, not a duplicate story turn.
    assert rig.manager.submit(session['id'], copy.deepcopy(original_body))['id'] == turn['id']
    assert len(record['turns']) == 1
    action_id = uid()
    rig.manager.action(session['id'], turn['id'], 'resume', {'request_id': action_id})
    rig.manager.action(session['id'], turn['id'], 'resume', {'request_id': action_id})
    fail_roleplay[0] = False
    rig.manager._predict(execution, turn, 'actor', 'npc', 'Actor', {'location': 'room'}, SCHEMA)
    rig.manager._predict(execution, turn, 'roleplay', 'world', 'Roleplay', {'location': 'room'}, SCHEMA)
    assert [stage for stage, _ in calls] == ['Actor', 'Roleplay', 'Roleplay']
    assert calls[1][1] != calls[2][1]
    assert turn['snapshot'] == frozen and turn['render_request_id'] == render_id
    assert record['active_run_id'] == turn['parent_run_id']
    assert not rig.videos.queues and not rig.assets.requests


def test_public_recovery_receipts_do_not_include_private_prompt_or_schema_bodies(rig):
    session, record, turn, _ = saved_turn(rig)
    turn['assistant_attempt_history'] = [{'id': uid(), 'stage': 'roleplay', 'status': 'failed',
        'system': 'private instructions', 'content': {'private': 'full role knowledge'},
        'schema': SCHEMA, 'rejected_reason': 'The roleplay response was invalid.'}]
    rig.manager._change(record, turn, status='failed', error='Roleplay response rejected.')
    public = next(row for row in rig.manager.get(session['id'])['turns'] if row['id'] == turn['id'])
    for attempt in public.get('assistant_attempt_history', []):
        assert not {'system', 'content', 'schema', 'images'} & set(attempt)
    assert turn['assistant_attempt_history'][0]['system'] == 'private instructions'


def test_new_turn_keeps_its_assistant_model_when_global_selection_changes(rig):
    selected = {'model': 'assistant-a'}
    rig.manager.get_settings = lambda: copy.deepcopy(selected)
    _, record, turn, _ = saved_turn(rig)
    selected['model'] = 'assistant-b'
    calls = []
    def answer(model, *args, **kwargs):
        calls.append(model)
        return {'result': {'answer': 'Ready'}, 'diagnostics': {'model': model}}
    rig.client.complete_json_result = answer
    rig.manager._predict(rig.manager._execution(record, turn), turn, 'roleplay', 'world', 'Roleplay', {}, SCHEMA)
    assert calls == ['assistant-a']
    assert turn['assistant_model'] == 'assistant-a'


def test_legacy_turn_captures_current_assistant_once_then_reuses_it(rig):
    selected = {'model': 'initial'}
    rig.manager.get_settings = lambda: copy.deepcopy(selected)
    _, record, turn, _ = saved_turn(rig)
    turn.pop('assistant_model', None)
    selected['model'] = 'retry-assistant'
    calls = []
    def answer(model, *args, **kwargs):
        calls.append(model)
        return {'result': {'answer': 'Ready'}, 'diagnostics': {'model': model}}
    rig.client.complete_json_result = answer
    execution = rig.manager._execution(record, turn)
    rig.manager._predict(execution, turn, 'actor', 'npc', 'Actor', {}, SCHEMA)
    selected['model'] = 'different-assistant'
    rig.manager._predict(execution, turn, 'roleplay', 'world', 'Roleplay', {}, SCHEMA)
    assert calls == ['retry-assistant', 'retry-assistant']
    assert turn['assistant_model'] == 'retry-assistant'


def test_cancelled_prediction_records_discarded_output_and_preserves_ending(rig):
    session, record, turn, _ = saved_turn(rig)
    def late_answer(*args, **kwargs):
        rig.manager.action(session['id'], turn['id'], 'cancel')
        return {'result': {'answer': 'Too late'}, 'diagnostics': {'model': 'test'}}
    rig.client.complete_json_result = late_answer
    with pytest.raises((InterruptedError, LMStudioError)):
        rig.manager._predict(rig.manager._execution(record, turn), turn, 'roleplay', 'world', 'Roleplay', {}, SCHEMA)
    request = next(iter(turn['assistant_requests'].values()))
    assert request['status'] == 'cancelled'
    assert 'result' not in request
    assert turn['status'] == 'cancelled'
    assert record['active_run_id'] == turn['parent_run_id']
    assert not rig.videos.queues and not rig.assets.requests


def test_explicit_preplan_retry_resets_the_exhausted_semantic_repair_budget(rig):
    session, record, turn, _ = saved_turn(rig)
    turn.update(automatic_repair_used=True, assistant_repair={'stage': 'roleplay', 'reason': 'Earlier invalid response'})
    rig.manager._change(record, turn, status='failed', error='Earlier invalid response')
    rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    assert not turn.get('automatic_repair_used')
    assert not rig.videos.queues and not rig.assets.requests


def test_retry_of_older_failed_attempt_does_not_start_work_after_story_advanced(rig):
    session, record, turn, _ = saved_turn(rig)
    rig.manager._change(record, turn, status='failed', error='Old response failed.')
    record['active_run_id'] = rig.videos.add(rig.project)['id']
    with pytest.raises(ValueError, match='moved beyond'):
        rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    assert turn['status'] == 'failed'
    assert not rig.client.calls and not rig.videos.queues and not rig.assets.requests


def test_first_frame_story_continues_saved_motion_without_replaying_its_old_opening(rig):
    from backend.comfy_transfer import FL_LORA, _settings, _lora_selections
    from backend.compiler import compile_project
    opening = reference('Old courtyard opening', 'background', 'courtyard-opening')
    opening['role'] = 'first_frame'
    for asset in rig.project['assets']:
        asset['role'] = 'context'
    rig.project.update(mode='i2va', assets=rig.project['assets'] + [opening])
    parent = rig.videos.add(rig.project)
    session = story(rig, source=parent['id'], settings={'resolution': '0.2', 'duration': 3, 'steps': 4, 'experimental_preview': True})
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I inspect the key.'})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    turn['plan'] = plan(transition='continue', dialogue=[])
    ending = rig.manager.ending_asset(parent['id'])
    prepared = rig.manager._project(rig.manager._execution(record, turn), turn, turn['snapshot']['project'], ending)
    assert prepared['mode'] == 't2va'
    assert next(asset for asset in prepared['assets'] if asset['id'] == opening['id'])['role'] == 'context'
    assert not any(asset.get('enabled', True) and asset.get('role') in ('first_frame', 'last_frame') for asset in prepared['assets'])
    assert next(asset for asset in prepared['assets'] if asset['id'] == ending['id'])['video_run_ending'] == parent['id']
    render = prepared['comfy_render']
    assert render['continuation_source'] == parent['continuation_source']
    assert render['continuation_overlap_frames'] == 39 and render['duration_basis'] == 'new_footage'
    config = _settings(prepared, render)
    assert config['steps'] == 4 and config['frames'] == 124
    assert _lora_selections(render, prepared['mode'])[0]['name'] == FL_LORA
    assert compile_project(prepared)['valid']
    assert not rig.videos.queues and not rig.assets.requests


def test_next_turn_replaces_stale_generated_state_and_keeps_author_notes(rig):
    from backend.compiler import compile_project
    from backend.world import validate_world
    _, record, turn, _ = saved_turn(rig)
    turn['plan'] = plan(dialogue=[])
    execution = rig.manager._execution(record, turn)
    execution['narrative_version'] = 2
    source = copy.deepcopy(turn['snapshot']['project'])
    prefix = 'Starting visible state before the new action (descriptive data, not dialogue; only the approved action changes it): '
    policy = 'Only the new action happens now. Do not repeat old speech or completed events. No subtitles or text overlays.'
    author = 'Keep the hand movements readable.\nUse a muted blue palette throughout.'
    stale = prefix + '{"objects_and_outfits": [{"name": "Gold key", "holder": null}]} Exactly 2 distinct people appear: Mira, Nora.'
    source['custom_instructions'] = author + ('\n' + policy + '\n' + stale) * 2
    cast = {person['name']: person['id'] for person in execution['world']['characters']}
    key = {'id': uid(), 'name': 'Gold key', 'kind': 'object', 'holder_id': cast['Mira']}
    execution['world']['entities'].append(key)
    ending = rig.manager.ending_asset(turn['parent_run_id'])
    # Each next render starts from the prior render snapshot, as a real branch does.
    for current, previous in [('Mira', None), ('Nora', 'Mira'), ('Mira', 'Nora')]:
        key['holder_id'] = cast[current]
        execution['world'] = validate_world(execution['world'])
        key = next(entity for entity in execution['world']['entities'] if entity['name'] == 'Gold key')
        prepared = rig.manager._project(execution, turn, source, ending)
        compiled = compile_project(prepared)
        assert compiled['valid']
        for text in (prepared['custom_instructions'], compiled['prompt']):
            assert text.count(prefix) == 0
            assert text.count(policy) == 1
            assert '"holder": null' not in text
            for note in author.splitlines():
                assert note in text
        placement = next(row for row in prepared['shots'][0]['scene_contract']['objects'] if row['entity_id'] == key['id'])
        assert f'held by {current}' in placement['start'] and placement['start'] == placement['end']
        assert f'At the end: held by {current}' in compiled['prompt']
        if previous:
            assert f'held by {previous}' not in compiled['prompt']
        source = prepared
    assert not rig.videos.queues and not rig.assets.requests
