"""Story lifecycle contracts with durable temp storage and no model/render workers."""
import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.compiler import compile_project
from backend.projects import new_project, shot
from backend.stories import OBSERVE_SYSTEM, PLAN_SYSTEM, StoryManager, settings_for, validate_plan


def uid():
    return str(uuid.uuid4())


def reference(name, role, tag):
    return {'id': uid(), 'name': name, 'media_type': 'image', 'role': 'reference_image',
            'semantic_role': role, 'prompt_tag': tag, 'enabled': True,
            'description': name, 'approved_observation': '', 'observation': ''}


def project():
    p = new_project()
    p.update(title='A quiet conversation', mode='ref2va', duration=5,
             comfy_render={'seed': 42, 'save_mmh3': True, 'resolution': '0.3', 'steps': 8})
    mira, nora = reference('Mira portrait', 'face', 'mira-face'), reference('Nora portrait', 'face', 'nora-face')
    p['assets'] = [mira, nora]
    p['subjects'] = [{'id': uid(), 'name': name, 'description': 'An adult visitor.', 'asset_ids': [asset['id']]}
                     for name, asset in [('Mira', mira), ('Nora', nora)]]
    p['story']['text'] = 'Mira enters the quiet room and stops beside Nora.'
    p['shots'] = [shot(5)]
    p['shots'][0].update(action=p['story']['text'], setting='A quiet room', final_state='Both visitors stand by the drawer.',
                         visible_subject_ids=[person['id'] for person in p['subjects']])
    return p


def choices():
    return [{'title': 'Ask about it', 'message': 'I ask about the key.'},
            {'title': 'Look closer', 'message': 'I look at the drawer.'},
            {'title': 'Wait', 'message': 'I wait for Nora to speak.'}]


def plan(**changes):
    result = {'action': 'Nora points to the locked drawer.', 'setting': 'A quiet room',
              'final_state': 'Nora stands beside the closed drawer.', 'transition': 'continue',
              'dialogue': [{'speaker': 'Nora', 'text': 'The spare key is inside.'}],
              'characters': [{'name': name, 'description': 'An adult visitor.', 'voice': 'Calm conversational voice.'}
                             for name in ('Mira', 'Nora')], 'asset_requests': [], 'choices': choices()}
    result.update(changes)
    return result


class Videos:
    def __init__(self):
        self.records, self.projects, self.requests = {}, {}, {}
        self.queues, self.submissions, self.cancelled, self.resolved = [], [], [], []
        self.next_status = 'succeeded'

    def add(self, p, *, source=None, status='succeeded', operation='generate', parent=None):
        p = copy.deepcopy(p)
        if source:
            p['comfy_render']['continuation_source'] = source
        else:
            p['comfy_render'].pop('continuation_source', None)
        rid = uid()
        self.records[rid] = {'id': rid, 'project_id': p['id'], 'status': status, 'operation': operation,
                             'continuation_source': 'mmh3/' + rid + '.mmh3', 'video_url': '/video/' + rid,
                             'seed': p['comfy_render']['seed'], 'has_snapshot': True, 'parent_run_id': parent}
        self.projects[rid] = p
        return self.get(rid)

    def get(self, rid):
        return copy.deepcopy(self.records[rid])

    def list(self):
        return [self.get(rid) for rid in self.records]

    def snapshot(self, rid):
        return copy.deepcopy(self.projects[rid])

    def submit(self, request_id, p, prompt, parent_run_id=None):
        self.submissions.append((request_id, copy.deepcopy(p), prompt, parent_run_id))
        if request_id not in self.requests:
            run = self.add(p, source=p.get('comfy_render', {}).get('continuation_source'),
                           status=self.next_status, operation='continue' if parent_run_id else 'generate', parent=parent_run_id)
            self.requests[request_id] = run['id']
            self.queues.append(run['id'])
        return self.get(self.requests[request_id])

    def reroll(self, request_id, run_id):
        if request_id not in self.requests:
            p = self.snapshot(run_id)
            p['comfy_render']['seed'] += 1
            run = self.add(p, source=p['comfy_render'].get('continuation_source'), operation='reroll', parent=run_id)
            self.requests[request_id] = run['id']
            self.queues.append(run['id'])
        return self.get(self.requests[request_id])

    def refresh(self, rid):
        return self.get(rid)

    def resolve_missing(self, rid):
        self.resolved.append(rid)
        return self.get(rid)

    def cancel(self, rid):
        self.cancelled.append(rid)
        self.records[rid].update(status='failed', cancelled=True)


class Assets:
    def __init__(self):
        self.records, self.requests, self.cancelled = {}, [], []
        self.next_status = 'succeeded'

    def submit(self, request_id, spec):
        if request_id not in self.records:
            self.requests.append(copy.deepcopy(spec))
            self.records[request_id] = {'id': request_id, 'status': self.next_status,
                                        'asset': reference(spec['name'], spec['semantic_role'], spec['prompt_tag'])}
        return copy.deepcopy(self.records[request_id])

    def refresh(self, rid):
        return copy.deepcopy(self.records[rid])

    def cancel(self, rid):
        self.cancelled.append(rid)
        self.records[rid]['status'] = 'cancelled'


class Client:
    def __init__(self):
        self.plans, self.calls, self.inspections = [], [], []
        self.observation = {'observed_state': 'Nora stands beside the closed drawer.',
                            'uncertainties': 'The drawer contents are not visible.', 'choices': choices()}

    def complete_json(self, model, system, content, schema, **kwargs):
        self.calls.append({'model': model, 'system': system, 'content': copy.deepcopy(content), 'schema': schema})
        if system == PLAN_SYSTEM:
            result = self.plans.pop(0) if self.plans else plan()
        elif system == OBSERVE_SYSTEM:
            result = self.observation
        else:
            raise AssertionError('Unexpected inference request')
        if isinstance(result, Exception):
            raise result
        if system == OBSERVE_SYSTEM:
            result = observed_for_schema(result, schema)
        return copy.deepcopy(result)

    def analyse_image(self, model, image, asset):
        self.inspections.append(asset['id'])
        return {'observation': 'One clearly visible ' + asset['name'] + '.'}


def observed_for_schema(observation, schema):
    """Default fake vision explicitly marks requested identities unverified.

    Supplied checklists remain untouched so malformed/partial fault fixtures
    still exercise production rejection rather than being repaired by a fake.
    """
    result = copy.deepcopy(observation)
    if 'continuity_checks' in schema.get('required', []) and 'continuity_checks' not in result:
        variants = schema['properties']['continuity_checks']['items'].get('anyOf', [])
        result['continuity_checks'] = [{'kind': variant['properties']['kind']['const'], 'id': identity,
            'status': 'uncertain', 'detail': 'Fake image evidence does not verify this identity.'}
            for variant in variants for identity in variant['properties']['id']['enum']]
    return result


@pytest.fixture
def rig(tmp_path):
    videos, assets, client = Videos(), Assets(), Client()
    resources = SimpleNamespace(calls=[])

    def run_ai(model, operation):
        resources.calls.append(model)
        return operation(model)

    resources.run_ai = run_ai
    saved, endings, image_reads = {}, [], []

    def ending(run_id):
        endings.append(run_id)
        asset = reference('Actual video ending', 'pose', 'ending-frame')
        asset.update(role='context', video_run_ending=run_id)
        return asset

    def image_data(asset_id):
        image_reads.append(asset_id)
        return 'data:image/png;base64,AA=='

    def manager():
        return StoryManager(tmp_path, lambda: videos, resources, lambda: client,
                            lambda: {'model': 'fake-local-vision'}, lambda p: saved.update({p['id']: copy.deepcopy(p)}),
                            ending, image_data, lambda: assets, start_workers=False, poll_interval=0)

    result = SimpleNamespace(manager=manager(), reopen=manager, videos=videos, assets=assets, client=client,
                             resources=resources, saved=saved, endings=endings, image_reads=image_reads, project=project())
    yield result
    result.manager.close()


def story(rig, *, source=None, **changes):
    return rig.manager.create({'request_id': uid(), 'project': rig.project, 'mode': 'game', 'player_name': 'Mira',
                               **({'source_run_id': source} if source else {}), **changes})


def render(rig, session, *, message='I ask what is in the drawer.', planned=None):
    body = {'request_id': uid(), 'message': message}
    if planned is not None:
        body['planned'] = planned
    turn = rig.manager.submit(session['id'], body)
    rig.manager.process(session['id'], turn['id'])
    return rig.manager.get(session['id'])['turns'][-1]


def test_create_and_submit_are_idempotent_and_do_not_start_workers(rig):
    body = {'request_id': uid(), 'project': rig.project, 'mode': 'game', 'player_name': 'Mira'}
    first = rig.manager.create(body)
    assert rig.manager.create(copy.deepcopy(body))['id'] == first['id']
    assert rig.manager.list()[0]['create_request_id'] == body['request_id']
    with pytest.raises(ValueError, match='another story'):
        rig.manager.create({**body, 'title': 'Changed request'})
    request = {'request_id': uid(), 'message': 'Surprise me.'}
    turn = rig.manager.submit(first['id'], request)
    assert rig.manager.submit(first['id'], copy.deepcopy(request))['id'] == turn['id']
    with pytest.raises(ValueError, match='another message'):
        rig.manager.submit(first['id'], {**request, 'message': 'A different message.'})
    assert not rig.manager.workers and not rig.videos.queues and not rig.resources.calls


def test_lineage_uses_exact_motion_parent_and_excludes_the_original_reroll(rig):
    opening = rig.videos.add(rig.project)
    child = rig.videos.add(rig.project, source=opening['continuation_source'])
    alternate = rig.videos.add(rig.project, source=opening['continuation_source'], operation='reroll', parent=child['id'])
    session = story(rig, source=alternate['id'])
    assert [clip['id'] for clip in session['clips']] == [opening['id'], alternate['id']]
    assert child['id'] not in session['branches'][session['active_branch_id']]
    request = uid()
    branch = rig.manager.branch(session['id'], opening['id'], request)
    assert rig.manager.branch(session['id'], opening['id'], request)['active_branch_id'] == branch['active_branch_id']
    assert [clip['id'] for clip in branch['clips']] == [opening['id']]
    assert len(branch['branches']) == 2


def test_vague_intent_gets_planned_into_a_concrete_new_event(rig):
    turn = render(rig, story(rig), message='Choose whatever happens next.')
    assert turn['status'] == 'succeeded', turn.get('error')
    prepared = rig.videos.snapshot(turn['run_id'])
    assert prepared['story']['text'] == plan()['action']
    assert 'Choose whatever' not in rig.videos.submissions[-1][2]
    context = json.loads(next(call for call in rig.client.calls if call['system'] == PLAN_SYSTEM)['content'][0]['text'])
    assert context['message'] == 'Choose whatever happens next.'


def test_game_response_compiles_new_speaker_bound_dialogue(rig):
    turn = render(rig, story(rig))
    assert turn['status'] == 'succeeded', turn.get('error')
    prepared = rig.videos.snapshot(turn['run_id'])
    nora = next(person for person in prepared['subjects'] if person['name'] == 'Nora')
    assert prepared['shots'][0]['dialogue'][0]['speaker_id'] == nora['id']
    assert prepared['shots'][0]['dialogue'][0]['text'] == 'The spare key is inside.'
    compiled = compile_project(prepared)
    assert compiled['valid'] and 'The spare key is inside.' in compiled['prompt']
    assert rig.manager.get(next(iter(rig.manager.records)))['choices'] == choices()


@pytest.mark.parametrize('message', ['I say "Who is there?"', 'I say “Who is there?”'])
def test_exact_quoted_player_words_cannot_be_changed(message):
    exact = plan(dialogue=[{'speaker': 'Mira', 'text': 'Who is there?'}, {'speaker': 'Nora', 'text': 'It is me.'}])
    assert validate_plan(exact, 'Mira', message)['dialogue'] == exact['dialogue']
    changed = copy.deepcopy(exact)
    changed['dialogue'][0]['text'] = 'Who are you?'
    with pytest.raises(ValueError, match='changed your quoted speech'):
        validate_plan(changed, 'Mira', message)


def test_changed_player_speech_fails_before_rendering(rig):
    rig.client.plans.append(plan(dialogue=[{'speaker': 'Mira', 'text': 'Who are you?'}]))
    turn = render(rig, story(rig), message='I say "Who is there?"')
    assert turn['status'] == 'failed' and 'quoted speech' in turn['error']
    assert not rig.videos.queues


def test_default_automatic_flow_and_explicit_review(rig):
    assert settings_for({})['review_before_render'] is False
    session = story(rig, settings={'review_before_render': True})
    turn = render(rig, session)
    assert turn['status'] == 'awaiting_review'
    assert not rig.videos.queues and not rig.assets.requests
    receipt = {'request_id': uid()}
    rig.manager.action(session['id'], turn['id'], 'approve', receipt)
    rig.manager.action(session['id'], turn['id'], 'approve', receipt)
    rig.manager.process(session['id'], turn['id'])
    assert rig.manager.get(session['id'])['turns'][0]['status'] == 'awaiting_acceptance'
    rig.manager.action(session['id'], turn['id'], 'accept-intended', {'request_id': uid()})
    rig.manager.process(session['id'], turn['id'])
    assert rig.manager.get(session['id'])['turns'][0]['status'] == 'succeeded'
    assert len(rig.videos.queues) == 1


def test_uncertain_recovery_keeps_same_render_ticket_and_never_requeues(rig):
    rig.videos.next_status = 'uncertain'
    session = story(rig)
    turn = render(rig, session)
    assert turn['status'] == 'uncertain' and not rig.manager.get(session['id'])['active_run_id']
    first_run = turn['run_id']
    rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    rig.manager.process(session['id'], turn['id'])
    assert len(rig.videos.queues) == 1
    assert rig.manager.get(session['id'])['turns'][0]['run_id'] == first_run
    rig.videos.records[first_run]['status'] = 'succeeded'
    rig.manager.action(session['id'], turn['id'], 'retry', {'request_id': uid()})
    rig.manager.process(session['id'], turn['id'])
    ready = rig.manager.get(session['id'])
    assert ready['active_run_id'] == first_run and len(ready['clips']) == 1
    assert len(rig.videos.queues) == 1 and len({call[0] for call in rig.videos.submissions}) == 1


@pytest.mark.parametrize('failure', ['planner', 'render', 'cancel'])
def test_failure_or_cancel_never_advances_the_previous_ending(rig, failure):
    opening = rig.videos.add(rig.project)
    session = story(rig, source=opening['id'])
    if failure == 'planner':
        rig.client.plans.append(ValueError('Assistant offline'))
    elif failure == 'render':
        rig.videos.next_status = 'failed'
    turn = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look at the drawer.'})
    if failure == 'cancel':
        rig.manager.action(session['id'], turn['id'], 'cancel')
    rig.manager.process(session['id'], turn['id'])
    result = rig.manager.get(session['id'])
    assert result['active_run_id'] == opening['id']
    assert [clip['id'] for clip in result['clips']] == [opening['id']]
    assert result['turns'][0]['status'] == ('cancelled' if failure == 'cancel' else 'failed')
    if failure != 'render':
        assert not rig.videos.queues


def test_reroll_replaces_current_event_and_keeps_original_as_a_branch(rig):
    opening = rig.videos.add(rig.project)
    session = story(rig, source=opening['id'])
    first = render(rig, session)
    assert first['status'] == 'succeeded', first.get('error')
    receipt = {'request_id': uid()}
    rig.manager.action(session['id'], first['id'], 'reroll', receipt)
    rig.manager.action(session['id'], first['id'], 'reroll', receipt)
    rig.manager.process(session['id'], first['id'])
    result = rig.manager.get(session['id'])
    assert len(result['turns']) == 1 and len(result['clips']) == 2
    alternate = result['active_run_id']
    assert alternate != first['run_id'] and result['turns'][0]['alternate_run_ids'] == [first['run_id']]
    assert len(rig.videos.queues) == 2
    assert rig.videos.snapshot(alternate)['shots'] == rig.videos.snapshot(first['run_id'])['shots']
    branch = rig.manager.branch(session['id'], first['run_id'])
    assert [clip['id'] for clip in branch['clips']] == [opening['id'], first['run_id']]


def test_generated_assets_keep_tags_owners_and_established_identity(rig):
    needs = [{'name': 'Nora replacement face', 'prompt': 'A portrait of Nora.', 'semantic_role': 'face', 'person_name': 'Nora', 'prompt_tag': 'new-nora-face'},
             {'name': 'Nora lantern', 'prompt': 'One small brass lantern.', 'semantic_role': 'object', 'person_name': 'Nora', 'prompt_tag': 'nora-lantern'},
             {'name': 'Nora coat', 'prompt': 'One blue wool coat.', 'semantic_role': 'wardrobe', 'person_name': 'Nora', 'prompt_tag': 'nora-coat'}]
    rig.client.plans.append(plan(asset_requests=needs))
    session = story(rig, settings={'generate_references': True})
    first = render(rig, session)
    assert first['status'] == 'succeeded', first.get('error')
    prepared = rig.videos.snapshot(first['run_id'])
    nora = next(person for person in prepared['subjects'] if person['name'] == 'Nora')
    original_nora = next(person for person in rig.project['subjects'] if person['name'] == 'Nora')
    by_tag = {asset['prompt_tag']: asset for asset in prepared['assets']}
    assert nora['id'] == original_nora['id'] and set(original_nora['asset_ids']).issubset(nora['asset_ids'])
    assert 'new-nora-face' not in by_tag and len(rig.assets.requests) == 2
    assert by_tag['nora-lantern']['simple_owner_id'] == nora['id']
    assert by_tag['nora-coat']['id'] in nora['asset_ids']
    assert len(rig.client.inspections) == 2 and by_tag['nora-lantern']['approved_observation']
    rig.client.plans.append(plan(asset_requests=needs, action='Nora points to her lantern.'))
    second = render(rig, session)
    assert second['status'] == 'succeeded', second.get('error')
    later = {asset.get('prompt_tag'): asset for asset in rig.videos.snapshot(second['run_id'])['assets']}
    assert later['nora-lantern']['id'] == by_tag['nora-lantern']['id']
    assert len(rig.assets.requests) == 2


def test_state_reload_and_read_are_inference_free_and_preserve_recovery(rig):
    session = story(rig)
    turn = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I wait.'})
    counts = (len(rig.videos.queues), len(rig.resources.calls), len(rig.endings), len(rig.image_reads))
    reopened = rig.reopen()
    try:
        recovered = reopened.get(session['id'])
        assert recovered['turns'][0]['id'] == turn['id'] and recovered['turns'][0]['status'] == 'uncertain'
        assert recovered['active_run_id'] is None and not reopened.workers
        assert reopened.list()[0]['id'] == session['id']
        assert reopened.list()[0]['create_request_id'] == session['create_request_id']
        assert counts == (len(rig.videos.queues), len(rig.resources.calls), len(rig.endings), len(rig.image_reads))
    finally:
        reopened.close()


def test_new_character_and_place_are_bound_once_in_an_explicit_scene_cut(rig):
    old_place = reference('Old room', 'background', 'old-room')
    rig.project['assets'].append(old_place)
    opening = rig.videos.add(rig.project)
    cast = plan()['characters'] + [{'name': 'Lio', 'description': 'An adult guide in a green jacket.', 'voice': 'Soft tenor.'}]
    needs = [{'name': 'Lio portrait', 'prompt': 'One full portrait of an adult guide in a green jacket.',
              'semantic_role': 'character', 'person_name': 'Lio', 'prompt_tag': 'lio-look'},
             {'name': 'Garden', 'prompt': 'A quiet walled garden in afternoon light.',
              'semantic_role': 'background', 'person_name': '', 'prompt_tag': 'garden'}]
    rig.client.plans.append(plan(characters=cast, asset_requests=needs, action='Lio meets the visitors in the garden.',
                                 setting='Walled garden', dialogue=[{'speaker': 'Lio', 'text': 'Welcome to the garden.'}]))
    session = story(rig, source=opening['id'], settings={'generate_references': True})
    turn = render(rig, session)
    assert turn['status'] == 'awaiting_review'
    assert not rig.assets.requests
    rig.manager.action(session['id'], turn['id'], 'approve', {'request_id': uid()})
    rig.manager.process(session['id'], turn['id'])
    turn = rig.manager.get(session['id'])['turns'][-1]
    assert turn['status'] == 'awaiting_review' and turn['created_assets']
    assert not rig.videos.queues
    rig.manager.action(session['id'], turn['id'], 'approve', {'request_id': uid()})
    rig.manager.process(session['id'], turn['id'])
    turn = rig.manager.get(session['id'])['turns'][-1]
    assert turn['status'] == 'succeeded', turn.get('error')
    prepared = rig.videos.snapshot(turn['run_id'])
    lio = next(person for person in prepared['subjects'] if person['name'] == 'Lio')
    by_tag = {asset['prompt_tag']: asset for asset in prepared['assets']}
    assert lio['asset_ids'] == [by_tag['lio-look']['id']]
    assert prepared['shots'][0]['dialogue'][0]['speaker_id'] == lio['id']
    assert by_tag['old-room']['role'] == 'context' and by_tag['garden']['role'] == 'reference_image'
    assert 'continuation_source' not in prepared['comfy_render']
    assert rig.videos.submissions[-1][3] is None
    assert [clip['id'] for clip in rig.manager.get(session['id'])['clips']] == [opening['id'], turn['run_id']]


def test_cancel_a_submitted_turn_stops_its_run_and_retry_uses_one_new_ticket(rig):
    opening = rig.videos.add(rig.project)
    rig.videos.next_status = 'uncertain'
    session = story(rig, source=opening['id'])
    turn = render(rig, session)
    rig.manager.action(session['id'], turn['id'], 'cancel')
    assert rig.videos.cancelled == [turn['run_id']]
    assert rig.manager.get(session['id'])['active_run_id'] == opening['id']
    rig.videos.next_status = 'succeeded'
    receipt = {'request_id': uid()}
    rig.manager.action(session['id'], turn['id'], 'retry', receipt)
    rig.manager.action(session['id'], turn['id'], 'retry', receipt)
    rig.manager.process(session['id'], turn['id'])
    ready = rig.manager.get(session['id'])
    assert ready['turns'][0]['status'] == 'succeeded'
    assert ready['active_run_id'] != turn['run_id'] and len(ready['clips']) == 2
    assert len(rig.videos.queues) == 2 and len(ready['turns']) == 1


def test_completed_memory_uses_active_branch_observations_and_never_speculative_choices(rig):
    session = story(rig)
    first = render(rig, session)
    assert first['status'] == 'succeeded', first.get('error')
    next_turn = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I wait.'})
    stored = rig.manager.records[session['id']]
    internal = next(t for t in stored['turns'] if t['id'] == next_turn['id'])
    context = rig.manager.context(stored, internal, rig.videos.snapshot(first['run_id']))
    assert context['observed_current_state'] == first['observation']
    assert {key: context['observed_current_state'][key] for key in rig.client.observation} == rig.client.observation
    events = context['completed_events_do_not_repeat']
    assert len(events) == 1 and events[0]['dialogue'] == plan()['dialogue']
    assert all(choice['message'] not in events[0]['action'] for choice in choices())


def test_imported_game_clones_supplied_cast_and_references_without_changing_studio(rig):
    opening = rig.videos.add(rig.project)
    original = copy.deepcopy(rig.project)
    rig.saved[original['id']] = copy.deepcopy(original)
    supplied = copy.deepcopy(original)
    lio_face = reference('Lio portrait', 'face', 'lio-face')
    lio = {'id': uid(), 'name': 'Lio', 'description': 'An adult visitor carrying a lantern.',
           'asset_ids': [lio_face['id']]}
    lantern = reference('Lio lantern', 'object', 'lio-lantern')
    lantern['simple_owner_id'] = lio['id']
    coat = reference('Mira coat', 'wardrobe', 'mira-coat')
    supplied['assets'].extend([lio_face, lantern, coat])
    supplied['subjects'].append(lio)
    supplied['subjects'][0]['asset_ids'].append(coat['id'])
    supplied['subjects'][0]['description'] = 'An adult visitor in a blue coat.'
    supplied['story']['text'] = 'An unsaved Studio draft must not replace the actual ending.'
    supplied_before = copy.deepcopy(supplied)

    session = story(rig, source=opening['id'], project=supplied)
    base = rig.manager.records[session['id']]['base_project']
    assert base['assets'] == supplied['assets'] and base['subjects'] == supplied['subjects']
    assert base['story'] == original['story']
    assert session['initial_reference_change'] is True

    first = render(rig, session)
    assert first['status'] == 'succeeded', first.get('error')
    assert first['plan']['transition'] == 'cut' and first.get('transition_reason')
    prepared = rig.videos.snapshot(first['run_id'])
    assert prepared['id'] != original['id'] and prepared['story_session_id'] == session['id']
    assert prepared['subjects'] == supplied['subjects']
    assert prepared['assets'] == supplied['assets']
    assert 'continuation_source' not in prepared['comfy_render']
    assert rig.videos.submissions[-1][3] is None
    context = json.loads(next(call for call in rig.client.calls if call['system'] == PLAN_SYSTEM)['content'][0]['text'])
    by_tag = {asset['tag']: asset for asset in context['references']}
    assert by_tag['lio-face']['person'] == by_tag['lio-lantern']['person'] == 'Lio'
    assert by_tag['mira-coat']['person'] == 'Mira'
    assert [clip['id'] for clip in rig.manager.get(session['id'])['clips']] == [opening['id'], first['run_id']]

    # Once those references are present in a finished take, the next turn can
    # reuse that take's exact motion state instead of repeatedly forcing cuts.
    second = render(rig, session)
    assert second['status'] == 'succeeded', second.get('error')
    assert second['plan']['transition'] == 'continue'
    next_project = rig.videos.snapshot(second['run_id'])
    assert next_project['comfy_render']['continuation_source'] == rig.videos.get(first['run_id'])['continuation_source']
    assert rig.videos.submissions[-1][3] == first['run_id']
    assert supplied == supplied_before
    assert rig.project == rig.saved[original['id']] == rig.videos.snapshot(opening['id']) == original


@pytest.mark.parametrize('extra', [None, 'context', 'disabled'])
def test_imported_game_with_unchanged_video_references_keeps_exact_motion(rig, extra):
    opening = rig.videos.add(rig.project)
    original = copy.deepcopy(rig.project)
    supplied = copy.deepcopy(original)
    if extra:
        image = reference('Lighting idea', 'style', 'lighting-idea')
        image.update(role='context' if extra == 'context' else 'reference_image', enabled=extra != 'disabled')
        supplied['assets'].append(image)
    session = story(rig, source=opening['id'], project=supplied)
    assert session['initial_reference_change'] is False

    turn = render(rig, session)
    assert turn['status'] == 'succeeded', turn.get('error')
    assert turn['plan']['transition'] == 'continue'
    prepared = rig.videos.snapshot(turn['run_id'])
    assert prepared['id'] != original['id']
    assert prepared['subjects'] == supplied['subjects']
    assert [asset for asset in prepared['assets'] if not asset.get('video_run_ending')] == supplied['assets']
    assert prepared['comfy_render']['continuation_source'] == opening['continuation_source']
    assert prepared['comfy_render']['continuation_overlap_frames'] == 39
    assert prepared['comfy_render']['duration_basis'] == 'new_footage'
    assert prepared['simple']['continuation']['previous_video_run_id'] == opening['id']
    assert rig.videos.submissions[-1][3] == opening['id']
    assert rig.project == rig.videos.snapshot(opening['id']) == original


@pytest.mark.parametrize('conflicting', [False, True])
def test_simultaneous_create_rechecks_request_receipt_before_persisting(rig, monkeypatch, conflicting):
    opening = rig.videos.add(rig.project)
    body = {'request_id': uid(), 'project': rig.project, 'mode': 'game',
            'player_name': 'Mira', 'source_run_id': opening['id']}
    second_body = copy.deepcopy(body)
    if conflicting:
        second_body['title'] = 'A different story with the same request receipt'
    barrier = threading.Barrier(2)
    lineage = rig.manager._lineage

    def simultaneous_lineage(run_id):
        result = lineage(run_id)
        # Both calls have passed the initial receipt lookup before either may
        # reach the second lookup and write its story.
        barrier.wait(timeout=5)
        return result

    def create(request):
        try:
            return rig.manager.create(request)
        except ValueError as error:
            return error

    monkeypatch.setattr(rig.manager, '_lineage', simultaneous_lineage)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, [body, second_body]))
    created = [result for result in results if isinstance(result, dict)]
    errors = [result for result in results if isinstance(result, ValueError)]
    assert len(rig.manager.records) == 1
    assert len(list(rig.manager.directory.glob('*.json'))) == 1
    if conflicting:
        assert len(created) == len(errors) == 1
        assert 'another story' in str(errors[0])
    else:
        assert not errors and created[0]['id'] == created[1]['id']
    assert not rig.manager.workers and not rig.videos.queues and not rig.resources.calls


@pytest.mark.parametrize('status', ['queued', 'running', 'succeeded'])
def test_lazy_studio_registers_reroll_once_without_advancing_the_ending(rig, status):
    opening = rig.videos.add(rig.project)
    alternate = rig.videos.add(rig.project, operation='reroll', parent=opening['id'], status=status)
    session = story(rig, mode='studio', source=opening['id'])
    assert not session['turns']
    for _ in range(2):
        registered = rig.manager.register_alternate(session['id'], alternate['id'], opening['id'])
        assert registered['active_branch_id'] == session['active_branch_id']
        assert registered['active_run_id'] == opening['id']
        assert [clip['id'] for clip in registered['clips']] == [opening['id']]
        assert registered['studio_alternates'] == {alternate['id']: {'original_run_id': opening['id']}}
        assert [job['id'] for job in registered['jobs']] == [opening['id'], alternate['id']]
        assert registered['jobs'][1]['status'] == status
    reopened = rig.reopen()
    try:
        assert reopened.get(session['id'])['studio_alternates'] == registered['studio_alternates']
        assert reopened.get(session['id'])['active_run_id'] == opening['id']
    finally:
        reopened.close()
    assert not rig.manager.workers and not rig.videos.queues and not rig.resources.calls


@pytest.mark.parametrize('position', ['opening', 'continuation'])
def test_studio_alternate_branch_replaces_one_scene_and_drops_its_old_descendants(rig, position):
    opening = rig.videos.add(rig.project)
    middle = rig.videos.add(rig.project, source=opening['continuation_source'])
    ending = rig.videos.add(rig.project, source=middle['continuation_source'])
    session = story(rig, mode='studio', source=ending['id'])
    original = opening if position == 'opening' else middle
    alternate = rig.videos.add(rig.project, operation='reroll', parent=original['id'],
                               source=rig.videos.snapshot(original['id'])['comfy_render'].get('continuation_source'))
    rig.manager.register_alternate(session['id'], alternate['id'], original['id'])
    receipt = uid()
    branch = rig.manager.branch(session['id'], alternate['id'], receipt)
    expected = ([] if position == 'opening' else [opening['id']]) + [alternate['id']]
    assert [clip['id'] for clip in branch['clips']] == expected
    assert branch['active_run_id'] == alternate['id'] and not branch['turns']
    assert branch['branches'][session['active_branch_id']] == [opening['id'], middle['id'], ending['id']]
    assert {job['id'] for job in branch['jobs']} == {opening['id'], middle['id'], ending['id'], alternate['id']}
    assert len(branch['jobs']) == 4
    assert len(branch['branches']) == 2
    repeated = rig.manager.branch(session['id'], alternate['id'], receipt)
    assert repeated['active_branch_id'] == branch['active_branch_id'] and len(repeated['branches']) == 2
    restored = rig.manager.branch(session['id'], original['id'])
    assert restored['active_run_id'] == original['id']
    assert [clip['id'] for clip in restored['clips']] == ([] if position == 'opening' else [opening['id']]) + [original['id']]


def test_studio_rerolls_of_alternates_resolve_to_the_same_original_scene_position(rig):
    opening = rig.videos.add(rig.project)
    original = rig.videos.add(rig.project, source=opening['continuation_source'])
    session = story(rig, mode='studio', source=original['id'])
    preceding = original
    alternate_ids = []
    for _ in range(3):
        alternate = rig.videos.add(rig.project, source=opening['continuation_source'],
                                   operation='reroll', parent=preceding['id'])
        registered = rig.manager.register_alternate(session['id'], alternate['id'], preceding['id'])
        assert registered['active_run_id'] == original['id']
        alternate_ids.append(alternate['id'])
        preceding = alternate
    branched = rig.manager.branch(session['id'], preceding['id'])
    assert [clip['id'] for clip in branched['clips']] == [opening['id'], preceding['id']]
    assert branched['branches'][session['active_branch_id']] == [opening['id'], original['id']]
    assert set(alternate_ids).issubset({job['id'] for job in branched['jobs']})
    assert len({job['id'] for job in branched['jobs']}) == len(branched['jobs'])
    assert not rig.videos.queues and not rig.resources.calls


@pytest.mark.parametrize('invalid', ['operation', 'parent', 'unrelated_story', 'game'])
def test_studio_alternate_registration_rejects_invalid_ownership_without_mutating_story(rig, invalid):
    opening = rig.videos.add(rig.project)
    other = rig.videos.add(rig.project)
    mode = 'game' if invalid == 'game' else 'studio'
    session = story(rig, mode=mode, source=opening['id'])
    original_id = other['id'] if invalid == 'unrelated_story' else opening['id']
    parent = other['id'] if invalid == 'parent' else original_id
    alternate = rig.videos.add(rig.project, operation='generate' if invalid == 'operation' else 'reroll', parent=parent)
    before = copy.deepcopy(rig.manager.records[session['id']])
    with pytest.raises(ValueError, match='Game turn controls|not an alternate'):
        rig.manager.register_alternate(session['id'], alternate['id'], original_id)
    assert rig.manager.records[session['id']] == before


@pytest.mark.parametrize('status', ['queued', 'running', 'uncertain', 'failed'])
def test_registered_studio_alternate_cannot_be_branched_before_success(rig, status):
    opening = rig.videos.add(rig.project)
    alternate = rig.videos.add(rig.project, operation='reroll', parent=opening['id'], status=status)
    session = story(rig, mode='studio', source=opening['id'])
    rig.manager.register_alternate(session['id'], alternate['id'], opening['id'])
    before = copy.deepcopy(rig.manager.records[session['id']])
    with pytest.raises(ValueError, match='finish before branching'):
        rig.manager.branch(session['id'], alternate['id'], uid())
    assert rig.manager.records[session['id']] == before
    rig.videos.records[alternate['id']]['status'] = 'succeeded'
    branched = rig.manager.branch(session['id'], alternate['id'], uid())
    assert [clip['id'] for clip in branched['clips']] == [alternate['id']]
