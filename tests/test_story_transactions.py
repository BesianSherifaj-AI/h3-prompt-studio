"""Editor failures and outcome review must preserve the playable story."""
import copy

import pytest

from test_stories import rig, story, render, plan, uid


@pytest.mark.parametrize('invalid', [
    {'premise': None},
    {'settings': {'duration': 99}},
    {'settings': None},
    {'world': {'schema_version': 0}},
    {'expected_configuration_revision': 999},
])
def test_rejected_edit_preserves_live_record_disk_and_active_turn(rig, invalid):
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I wait.'})
    record = rig.manager._story(session['id'])
    active = rig.manager._turn(record, public['id'])
    before = copy.deepcopy(record)
    path = rig.manager.directory / (session['id'] + '.json')
    saved = path.read_bytes()

    with pytest.raises(ValueError):
        rig.manager.update(session['id'], {'title': 'Rejected title', **invalid})

    assert record == before
    assert path.read_bytes() == saved
    assert rig.manager._turn(record, active['id']) is active
    # A worker holding this object must still see cancellation after an edit fails.
    rig.manager.action(session['id'], active['id'], 'cancel')
    with pytest.raises(InterruptedError):
        rig.manager._check_cancel(active)


def test_failed_editor_save_does_not_publish_unpersisted_configuration(rig, monkeypatch):
    session = story(rig)
    record = rig.manager._story(session['id'])
    before = copy.deepcopy(record)

    def failed_save(_):
        raise OSError('The destination is unavailable.')

    monkeypatch.setattr(rig.manager, '_save', failed_save)
    with pytest.raises(OSError, match='unavailable'):
        rig.manager.update(session['id'], {'premise': 'An unsaved revision.'})
    assert record == before


def test_successful_edit_keeps_worker_turn_and_frozen_snapshot(rig):
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I wait.'})
    record = rig.manager._story(session['id'])
    active = rig.manager._turn(record, public['id'])
    snapshot = copy.deepcopy(active['snapshot'])
    updated = rig.manager.update(session['id'], {'premise': 'The next turn uses this revision.'})
    assert updated['premise'] == 'The next turn uses this revision.'
    assert rig.manager._turn(record, active['id']) is active
    assert active['snapshot'] == snapshot
    assert rig.reopen().get(session['id'])['premise'] == updated['premise']


@pytest.mark.parametrize('acceptance', [None, 'accept-intended', 'accept-visible'])
def test_next_moves_follow_the_selected_outcome_not_an_ambiguous_frame(rig, acceptance):
    intended = [
        {'title': 'Check inventory', 'message': 'I check the key in my inventory.'},
        {'title': 'Look around', 'message': 'I look around.'},
        {'title': 'Wait', 'message': 'I wait.'},
    ]
    visible = [
        {'title': 'Take key', 'message': 'I pick up the key from the floor.'},
        {'title': 'Look at drawer', 'message': 'I look at the drawer.'},
        {'title': 'Step closer', 'message': 'I take one step closer.'},
    ]
    rig.client.observation['choices'] = copy.deepcopy(visible)
    session = story(rig, settings={'review_before_render': acceptance is not None})
    turn = render(rig, session, planned=plan(choices=intended))
    if acceptance:
        assert turn['status'] == 'awaiting_review'
        rig.manager.action(session['id'], turn['id'], 'approve')
        rig.manager.process(session['id'], turn['id'])
        assert rig.manager.get(session['id'])['turns'][-1]['status'] == 'awaiting_acceptance'
        rig.manager.action(session['id'], turn['id'], acceptance)
        rig.manager.process(session['id'], turn['id'])

    result = rig.manager.get(session['id'])
    assert result['turns'][-1]['status'] == 'succeeded'
    assert result['choices'] == (visible if acceptance == 'accept-visible' else intended)
    assert result['observed_state']['choices'] == visible, 'Keep observation evidence for review.'
    assert len(rig.videos.queues) == len(result['world']['events']) == 1


@pytest.mark.parametrize('child', ['image', 'video'])
def test_cancel_during_submission_cancels_the_late_child_receipt(rig, monkeypatch, child):
    session = story(rig)
    asset = {'name': 'Empty courtyard', 'prompt': 'One empty stone courtyard.',
             'semantic_role': 'background', 'person_name': '', 'prompt_tag': 'courtyard'}
    planned = plan(asset_requests=[asset] if child == 'image' else [])
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I wait.', 'planned': planned})
    worker = rig.assets if child == 'image' else rig.videos
    worker.next_status = 'running'
    original = worker.submit
    returned = []

    def submit(*args, **kwargs):
        job = original(*args, **kwargs)
        returned.append(job['id'])
        # The job exists, but the story has not received its ID yet.
        rig.manager.action(session['id'], public['id'], 'cancel')
        assert job['id'] not in worker.cancelled
        return job

    monkeypatch.setattr(worker, 'submit', submit)
    rig.manager.process(session['id'], public['id'])
    result = rig.manager.get(session['id'])
    assert worker.cancelled == returned
    assert result['turns'][-1]['status'] == 'cancelled'
    assert not result['active_run_id'] and not result['world']['events']
    internal = rig.manager._turn(rig.manager._story(session['id']), public['id'])
    assert returned[0] in (internal['asset_jobs'] if child == 'image' else [internal['run_id']])


def test_accept_intended_after_reroll_inspection_failure_does_not_reinspect(rig):
    session = story(rig)
    first = render(rig, session)
    rig.client.observation = RuntimeError('Vision is unavailable.')
    rig.manager.action(session['id'], first['id'], 'reroll')
    rig.manager.process(session['id'], first['id'])
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, first['id'])
    assert turn['status'] == 'inspection_failed' and turn['observation'] is None
    calls = len(rig.client.calls)
    rig.manager.action(session['id'], first['id'], 'accept-intended')
    rig.manager.process(session['id'], first['id'])
    result = rig.manager.get(session['id'])
    assert result['turns'][-1]['status'] == 'succeeded'
    assert result['active_run_id'] != first['run_id']
    assert len(rig.client.calls) == calls
    assert len(result['world']['events']) == 1 and len(rig.videos.queues) == 2


@pytest.mark.parametrize('null_observation', [False, True])
def test_accept_visible_without_inspection_leaves_plan_and_approval_unchanged(rig, null_observation):
    rig.client.observation = RuntimeError('Vision is unavailable.')
    session = story(rig)
    failed = render(rig, session)
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, failed['id'])
    if null_observation:
        turn['observation'] = None
    before = copy.deepcopy(turn)
    with pytest.raises(ValueError, match='inspection'):
        rig.manager.action(session['id'], turn['id'], 'accept-visible')
    assert turn == before
    assert record['active_run_id'] is None
