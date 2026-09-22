import copy
import json
import uuid

import pytest

from backend.asset_runs import AssetRunError
from backend.production import ProductionManager, ProductionError
from backend.projects import new_project
from backend.video_runs import VideoRunError


def uid():
    return str(uuid.uuid4())


class Jobs:
    def __init__(self, asset=False):
        self.asset = asset
        self.records, self.projects, self.submissions, self.cancelled = {}, {}, [], []
        self.next_status = 'succeeded'
        self.refresh_status = None

    def get(self, ident):
        if ident not in self.records:
            if self.asset:
                raise AssetRunError('That image job was not found.')
            raise VideoRunError('This video run was not found.')
        return copy.deepcopy(self.records[ident])

    def snapshot(self, ident):
        return copy.deepcopy(self.projects[ident])

    def submit(self, ident, project, prompt=None):
        assert ident not in self.records, 'A saved request must be reconciled, never resubmitted'
        self.submissions.append(ident)
        self.projects[ident] = copy.deepcopy(project)
        record = {'id': ident, 'status': self.next_status, 'stage': self.next_status, 'error': None,
                  'video_url': None if self.asset else f'/api/video/runs/{ident}/video'}
        if self.asset:
            record['asset'] = {'id': uid(), 'name': project['name'], 'media_type': 'image', 'description': project['prompt']}
        self.records[ident] = record
        return copy.deepcopy(record)

    def refresh(self, ident):
        if self.refresh_status:
            self.records[ident]['status'] = self.refresh_status
        return self.get(ident)

    def cancel(self, ident):
        self.cancelled.append(ident)
        self.records[ident]['status'] = 'cancelled'
        return self.get(ident)


@pytest.fixture
def rig(tmp_path):
    project = new_project()
    project['mode'] = 't2va'
    project['story']['text'] = 'A paper boat drifts.'
    project['shots'][0].update(action='A paper boat drifts across a pond.', setting='A quiet pond')
    projects = {project['id']: project}
    videos, assets = Jobs(), Jobs(asset=True)
    manager = ProductionManager(tmp_path, lambda ident: projects[ident], lambda: videos, lambda: assets,
                                start_workers=False, poll_interval=0)
    body = {'request_id': uid(), 'name': 'Daily shorts', 'project_ids': [project['id']]}
    return manager, body, projects, videos, assets


def run(rig, body=None):
    manager, original, *_ = rig
    batch = manager.create(body or original)
    manager.start(batch['id'])
    manager.process(batch['id'])
    return manager.get(batch['id'])


def test_create_does_not_submit_and_freezes_project(rig):
    manager, body, projects, videos, _ = rig
    batch = manager.create(body)
    assert batch['status'] == 'draft' and not videos.submissions
    projects[body['project_ids'][0]]['story']['text'] = 'Mutated after creation'
    manager.start(batch['id']); manager.process(batch['id'])
    assert next(iter(videos.projects.values()))['story']['text'] == 'A paper boat drifts.'
    assert manager.get(batch['id'])['completed'] == 1


def test_create_is_idempotent_and_rejects_changed_request(rig):
    manager, body, *_ = rig
    batch = manager.create(body)
    assert manager.create(body)['id'] == batch['id']
    with pytest.raises(ProductionError, match='different settings'):
        manager.create({**body, 'name': 'Other'})


@pytest.mark.parametrize('change', [{'project_ids': []}, {'project_ids': [uid()] * 101}, {'render_options': {'duration': 99}}, {'stop_on_error': 'false'}])
def test_bounded_inputs(rig, change):
    manager, body, *_ = rig
    with pytest.raises(ProductionError):
        manager.create({**body, **change})


def test_duration_limit_and_invalid_project_reject_before_start(rig):
    manager, body, projects, videos, _ = rig
    projects[body['project_ids'][0]]['duration'] = 30
    with pytest.raises(ValueError, match='4–15'):
        manager.create(body)
    assert not videos.submissions


def test_optional_image_uses_asset_manager_and_binds_first_frame(rig):
    manager, body, _, videos, assets = rig
    body.pop('project_ids')
    body['items'] = [{'project_id': next(iter(rig[2])), 'image_spec': {'prompt': 'Paper boat on a pond.', 'name': 'Boat', 'seed': 42}}]
    batch = run(rig, body)
    assert batch['status'] == 'succeeded'
    assert len(assets.submissions) == len(videos.submissions) == 1
    project = next(iter(videos.projects.values()))
    assert project['mode'] == 'i2va' and project['assets'][0]['role'] == 'first_frame'
    assert batch['items'][0]['asset_id'] == project['assets'][0]['id']


def test_restart_pauses_and_reconciles_saved_request_without_repost(rig):
    manager, body, projects, videos, assets = rig
    batch = manager.create(body)
    manager.start(batch['id'])
    item = batch['items'][0]
    videos.submit(item['run_id'], projects[item['project_id']], 'prompt')
    restarted = ProductionManager(manager.directory.parent, lambda ident: projects[ident], lambda: videos,
                                  lambda: assets, start_workers=False, poll_interval=0)
    assert restarted.get(batch['id'])['status'] == 'paused'
    restarted.start(batch['id']); restarted.process(batch['id'])
    assert restarted.get(batch['id'])['status'] == 'succeeded'
    assert videos.submissions == [item['run_id']]


def test_uncertain_receipt_stops_later_items_without_retry(rig):
    manager, body, _, videos, _ = rig
    body['project_ids'] *= 2
    body['stop_on_error'] = False
    videos.next_status = 'uncertain'
    batch = run(rig)
    assert batch['status'] == 'needs_attention'
    assert len(videos.submissions) == 1 and batch['items'][1]['status'] == 'pending'
    manager.start(batch['id']); manager.process(batch['id'])
    assert len(videos.submissions) == 1
    with pytest.raises(ProductionError, match='definite'):
        manager.retry(batch['id'], 0)
    videos.refresh_status = 'succeeded'; videos.next_status = 'succeeded'
    manager.start(batch['id']); manager.process(batch['id'])
    assert manager.get(batch['id'])['status'] == 'succeeded'


def test_definite_failed_take_requires_explicit_new_id(rig):
    manager, _, _, videos, _ = rig
    videos.next_status = 'failed'
    batch = run(rig)
    with pytest.raises(ProductionError, match='Explicitly retry'):
        manager.start(batch['id'])
    old = batch['items'][0]['run_id']
    retried = manager.retry(batch['id'], 0)
    assert retried['items'][0]['run_id'] != old
    videos.next_status = 'succeeded'
    manager.start(batch['id']); manager.process(batch['id'])
    assert len(videos.submissions) == 2


def test_continue_only_definite_failures_when_explicitly_configured(rig):
    _, body, _, videos, _ = rig
    body['project_ids'] *= 2
    body['stop_on_error'] = False
    videos.next_status = 'failed'
    batch = run(rig)
    assert len(videos.submissions) == 2
    assert batch['status'] == 'needs_attention' and batch['completed'] == 0


def test_cancel_targets_only_current_receipt_and_never_submits(rig):
    manager, body, projects, videos, _ = rig
    batch = manager.create(body)
    manager.start(batch['id'])
    record = manager.records[batch['id']]
    item = record['items'][0]
    videos.next_status = 'running'
    videos.submit(item['run_id'], projects[item['project_id']], 'prompt')
    item['status'] = 'running'
    unrelated = uid(); videos.records[unrelated] = {'status': 'running'}
    result = manager.cancel(batch['id'])
    assert result['status'] == 'cancelled'
    assert videos.cancelled == [item['run_id']]
    assert videos.records[unrelated]['status'] == 'running'
    manager.process(batch['id'])
    assert len(videos.submissions) == 1


def test_adoption_requires_exact_snapshot(rig):
    manager, body, projects, videos, _ = rig
    project = copy.deepcopy(next(iter(projects.values())))
    project['comfy_render'] = {}
    existing = videos.submit(uid(), project, 'prompt')
    body['items'] = [{'project_id': project['id'], 'existing_run_id': existing['id']}]
    body.pop('project_ids')
    batch = run(rig, body)
    assert batch['items'][0]['adopted'] and len(videos.submissions) == 1
    body['request_id'] = uid(); body['render_options'] = {'seed': 123}
    with pytest.raises(ProductionError, match='entire frozen'):
        manager.create(body)


def test_batch_serial_admission_and_playlist_does_not_approve(rig):
    manager, body, *_ = rig
    first = manager.create(body); manager.start(first['id'])
    second = manager.create({**body, 'request_id': uid()})
    with pytest.raises(ProductionError, match='Another production'):
        manager.start(second['id'])
    manager.process(first['id'])
    assert manager.playlist(first['id'])['review_required'] is True


def test_all_images_finish_before_first_video_and_do_not_count_as_completed(rig):
    manager, body, projects, videos, assets = rig
    body.pop('project_ids')
    body['items'] = [{'project_id': next(iter(projects)), 'image_spec': {'prompt': 'Paper boat on pond', 'name': 'Boat', 'seed': seed}}
                     for seed in (1, 2)]
    original = videos.submit
    def submit(*args):
        assert len(assets.submissions) == 2
        assert manager.get(body['request_id'])['completed'] == len(videos.submissions)
        return original(*args)
    videos.submit = submit
    result = run(rig, body)
    assert result['completed'] == 2
