"""Text-only Game turns do not acquire optional image-generator dependencies."""
import copy
import json

import pytest

from backend.compiler import compile_project
from backend.stories import settings_for
from backend.world import validate_world
from test_game_director import coordinator, direction, narrative, setup_scene
from test_stories import observed_for_schema, plan, reference, render, rig, story, uid


def image_request():
    return {'name': 'City street', 'prompt': 'One pixel-art cobblestone city street.',
            'semantic_role': 'background', 'person_name': '', 'prompt_tag': 'city-street'}


def text_game(rig, *, new_person=False, generate=False, existing=False):
    project, _ = setup_scene()
    project['subjects'] = project['subjects'][:1]
    if existing:
        asset = reference('Alex blue coat', 'character', 'alex-look')
        project['assets'] = [asset]
        project['subjects'][0]['asset_ids'] = [asset['id']]
    world = validate_world({'schema_version': 1, 'characters': [
        {'id': 'player', 'name': 'Alex', 'control': 'player', 'description': 'Blue coat'}]})
    session = rig.manager.create({'request_id': uid(), 'project': project, 'mode': 'game',
        'player_name': 'Alex', 'player_character_id': 'player', 'world': world,
        'settings': {'generate_references': generate, 'duration': 3, 'resolution': '0.2'}})
    authored = coordinator()
    authored['beats'] = [{'id': 'beat-1', 'action': 'Alex stands on a cobblestone city street.' + (' A woman in a green coat stops nearby.' if new_person else ''),
                         'setting': 'A pixel-art city street.', 'final_state': 'Alex stands beside the shop door.'}]
    authored['asset_requests'] = [image_request()] if generate else []
    if new_person:
        authored['new_characters'] = [{'id': 'new-npc', 'name': 'Elara', 'description': 'An adult woman in a green coat.', 'voice': ''}]
    calls = []
    def complete(model, system, content, schema, **options):
        if 'new_characters' in schema['properties']:
            context = json.loads(content[0]['text'])
            assert context['generate_references'] is generate
            assert schema['properties']['asset_requests']['maxItems'] == (6 if generate else 0)
            calls.append('writer')
            result = authored
        elif 'shots' in schema['properties']:
            calls.append('director')
            result = direction(3)
            result['shots'][0].update(visible_subject_ids=['player'] + (['new-npc'] if new_person else []), dialogue_indices=[])
        elif 'observed_state' in schema['properties']:
            calls.append('inspection')
            result = observed_for_schema(rig.client.observation, schema)
        elif 'observation' in schema['properties']:
            calls.append('reference inspection')
            result = {'observation': 'A cobblestone city street.'}
        else:
            raise AssertionError(schema)
        return {'result': copy.deepcopy(result), 'diagnostics': {}}
    rig.client.complete_json_result = complete
    return session, calls


@pytest.mark.parametrize('new_person,existing', [(False, False), (True, False), (True, True)])
def test_automatic_first_game_and_new_npc_render_without_image_manager(rig, new_person, existing):
    session, calls = text_game(rig, new_person=new_person, existing=existing)
    result = render(rig, session, message='I appear in a city street.')
    assert result['status'] == 'succeeded', result.get('error')
    assert calls == ['writer', 'director', 'inspection']
    internal = rig.manager._turn(rig.manager._story(session['id']), result['id'])
    assert not rig.assets.requests and internal['asset_specs'] == []
    assert result['plan_origin'] == 'automatic'
    assert len(rig.videos.queues) == 1
    project = rig.videos.snapshot(result['run_id'])
    assert project['mode'] == ('ref2va' if existing else 't2va')
    assert len(project['assets']) == int(existing)
    assert compile_project(project)['valid']
    if new_person:
        assert any(person['id'] == 'new-npc' and not person['asset_ids'] for person in project['subjects'])


def test_explicit_opt_in_keeps_automatic_image_generation(rig):
    session, _ = text_game(rig, generate=True)
    result = render(rig, session, message='I appear in a city street.')
    assert result['status'] == 'succeeded', result.get('error')
    assert len(rig.assets.requests) == 1
    assert rig.videos.snapshot(result['run_id'])['mode'] == 'ref2va'


@pytest.mark.parametrize('status', ['succeeded', 'failed'])
def test_explicit_authored_images_are_preserved_when_automatic_generation_is_off(rig, status):
    session = story(rig)
    rig.assets.next_status = status
    result = render(rig, session, planned=plan(asset_requests=[image_request()]))
    assert result['status'] == status, result.get('error')
    assert result['plan_origin'] == 'authored'
    assert len(rig.assets.requests) == 1
    assert result['plan']['asset_requests'] == [image_request()]
    assert not result.get('reference_policy_history')


def test_admission_omits_schema_ignoring_automatic_suggestion_and_audits_it(rig):
    # Legacy adapters may return JSON without enforcing the requested schema.
    session = story(rig)
    rig.client.plans.append(plan(asset_requests=[image_request()]))
    result = render(rig, session)
    assert result['status'] == 'succeeded', result.get('error')
    assert not rig.assets.requests
    assert result['plan']['asset_requests'] == []
    assert result['reference_policy_history'][0]['asset_requests'] == [image_request()]
    assert result['reference_policy_history'][0]['asset_jobs'] == []


def test_user_edit_can_explicitly_add_images_without_enabling_automatic_images(rig):
    session = story(rig, settings={'review_before_render': True})
    result = render(rig, session)
    assert result['status'] == 'awaiting_review'
    edited = {**copy.deepcopy(result['plan']), 'asset_requests': [image_request()]}
    rig.manager.action(session['id'], result['id'], 'edit', {'request_id': uid(), 'plan': edited})
    rig.manager.process(session['id'], result['id'])
    turn = rig.manager._turn(rig.manager._story(session['id']), result['id'])
    assert turn['status'] == 'awaiting_acceptance', turn.get('error')
    assert turn['plan_origin'] == 'authored' and len(rig.assets.requests) == 1


def test_studio_automatic_images_keep_existing_behavior(rig):
    session = story(rig, mode='studio')
    rig.client.plans.append(plan(asset_requests=[image_request()]))
    result = render(rig, session)
    assert result['status'] == 'succeeded', result.get('error')
    assert len(rig.assets.requests) == 1


def saved_optional_failure(rig, *, status='failed', submitted=False, origin=None):
    session, calls = text_game(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I appear in a city street.'})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    value = narrative()
    value.update(characters=[{'id': 'player', 'name': 'Alex', 'description': 'Blue coat', 'voice': ''}], dialogue=[],
                 action='Alex stands on the city street.', setting='A city street.', final_state='Alex remains on the pavement.', asset_requests=[image_request()])
    value['beats'] = [{'id': 'beat-1', **{key: value[key] for key in ('action', 'setting', 'final_state')}}]
    value['direction'] = direction(3)
    value['direction']['shots'][0].update(visible_subject_ids=['player'], dialogue_indices=[])
    spec = {'request_id': uid(), **image_request(), 'model': 'z_image_turbo_bf16.safetensors',
            'person_id': None, 'width': 608, 'height': 320, 'seed': 42}
    turn.update(plan=value, asset_specs=[spec], asset_jobs=[spec['request_id']], status='failed', stage='Image needs attention')
    if origin is None:
        turn.pop('plan_origin', None)
    else:
        turn['plan_origin'] = origin
    raw = {key: copy.deepcopy(value[key]) for key in ('beats', 'asset_requests', 'transition', 'effects', 'choices')}
    raw['new_characters'] = []
    turn['assistant_requests'] = {'saved-raw': {'id': uid(), 'stage': 'roleplay', 'status': 'completed', 'result': raw}}
    job = {'id': spec['request_id'], 'status': status, 'submission_intent': submitted,
           'prompt_id': 'comfy-ticket' if submitted else None, 'asset': None}
    rig.assets.records[job['id']] = job
    rig.manager._save(record)
    return session, turn, copy.deepcopy(raw), copy.deepcopy(job)


def test_exact_legacy_presubmit_failure_retries_as_text_without_replacing_image_or_raw_receipt(rig):
    session, turn, raw, job = saved_optional_failure(rig)
    old_ticket = turn['render_request_id']
    rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    assert turn['plan_origin'] == 'automatic'
    assert turn['plan']['asset_requests'] == turn['asset_specs'] == turn['asset_jobs'] == []
    assert turn['reference_policy_history'][0]['asset_jobs'] == [job]
    assert turn['assistant_requests']['saved-raw']['result'] == raw
    rig.manager.process(session['id'], turn['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    assert not rig.assets.requests and len(rig.videos.queues) == 1
    assert turn['render_request_id'] == old_ticket and turn['project']['mode'] == 't2va'
    assert rig.assets.records[job['id']] == job
    assert turn['assistant_requests']['saved-raw']['result'] == raw


@pytest.mark.parametrize('status,submitted', [('uncertain', False), ('failed', True), ('failed', None), ('queued', True), ('succeeded', True)])
def test_recovery_never_bypasses_submitted_or_unverified_optional_child(rig, status, submitted):
    session, turn, raw, job = saved_optional_failure(rig, status=status, submitted=submitted)
    specs = copy.deepcopy(turn['asset_specs'])
    with pytest.raises(ValueError, match='may already have been submitted'):
        rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    assert turn['asset_specs'] == specs and turn['asset_jobs'] == [job['id']]
    assert turn['assistant_requests']['saved-raw']['result'] == raw
    assert not rig.assets.requests and not rig.videos.queues
    assert not turn.get('reference_policy_history')


def test_authored_failed_image_retry_still_issues_explicit_replacement(rig):
    session, turn, raw, job = saved_optional_failure(rig, origin='authored')
    rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    assert turn['asset_specs'][0]['request_id'] != job['id']
    assert turn['plan']['asset_requests'] == [image_request()]
    assert not turn.get('reference_policy_history')


@pytest.mark.parametrize('extra', [{'prompt_id': 'already-submitted'}, {'asset': {'id': 'imported-image'}}])
def test_contradictory_submission_or_created_asset_evidence_cannot_be_omitted(rig, extra):
    session, turn, _, job = saved_optional_failure(rig)
    rig.assets.records[job['id']].update(extra)
    with pytest.raises(ValueError, match='may already have been submitted'):
        rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    assert turn['plan']['asset_requests'] and not turn.get('reference_policy_history')


def test_retry_preserves_reference_opt_in_frozen_at_submission(rig):
    session, turn, _, job = saved_optional_failure(rig, origin='automatic')
    turn['snapshot']['settings']['generate_references'] = True
    # Current editor preference does not retroactively rewrite this queued turn.
    rig.manager._state(rig.manager._story(session['id']))['settings']['generate_references'] = False
    rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    assert turn['asset_specs'][0]['request_id'] != job['id']
    assert turn['plan']['asset_requests'] and not turn.get('reference_policy_history')


@pytest.mark.parametrize('invalid', ['false', 0, 1, None, []])
def test_reference_generation_setting_requires_boolean(invalid):
    assert settings_for({})['generate_references'] is False
    with pytest.raises(ValueError, match='must be on or off'):
        settings_for({'generate_references': invalid})
