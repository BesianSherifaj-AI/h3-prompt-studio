"""Story/asset boundary regressions; no real HTTP, models, or GPU workers."""
import copy
import re

import pytest

from backend.asset_runs import _spec
from backend.stories import asset_tag
from test_stories import Assets, plan, render, rig, story, uid


class ContractAssets(Assets):
    """The real worker's strict admission shape with controlled durable states."""
    def __init__(self):
        super().__init__()
        self.resumed, self.refreshed, self.resolve_to = [], [], {}

    def submit(self, request_id, spec):
        _spec(spec)  # Match actual worker validation, including tags and bindings.
        return super().submit(request_id, spec)

    def resume(self, rid):
        self.resumed.append(rid)
        if self.records[rid]['status'] == 'paused':
            self.records[rid]['status'] = 'succeeded'
        return self.refresh(rid)

    def refresh(self, rid):
        self.refreshed.append(rid)
        if rid in self.resolve_to:
            self.records[rid]['status'] = self.resolve_to[rid]
        return super().refresh(rid)


@pytest.fixture
def assets(rig):
    result = ContractAssets()
    rig.assets = result
    rig.manager.assets = lambda: result
    return result


def need(tag='nora-key', **changes):
    return {'name': 'Nora key', 'prompt': 'A small brass key on a plain background.',
            'semantic_role': 'object', 'person_name': 'Nora', 'prompt_tag': tag, **changes}


def saved_child(rig, assets, status, *, linked=True):
    session = story(rig)
    planned = plan(asset_requests=[need()], transition='cut')
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look at the key.', 'planned': planned})
    record = rig.manager.records[session['id']]
    turn = record['turns'][0]
    request_id = uid()
    spec = {'request_id': request_id, **need(), 'model': session['settings']['image_model'],
            'seed': 789, 'width': 512, 'height': 512}
    assets.submit(request_id, {k: v for k, v in spec.items() if k not in ('request_id', 'person_name')})
    assets.records[request_id]['status'] = status
    rig.manager._change(record, turn, status='uncertain', asset_specs=[spec],
                        asset_jobs=[request_id] if linked else [])
    return session, public, turn, request_id


@pytest.mark.parametrize('value', ['123-photo', '@Girl___Dress!!!', 'coat---blue', '---', '', '衣装', 'a' * 63 + '-tail'])
def test_tag_normalization_is_stable_and_worker_compatible(value):
    tag = asset_tag(value, 'Reference 1')
    assert tag == asset_tag(value, 'Reference 1')
    assert len(tag) <= 64 and re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', tag)
    assert _spec({'prompt': 'A reference.', 'seed': 1, 'prompt_tag': tag})['prompt_tag'] == tag


def test_normalized_new_tags_are_unique_and_preserve_character_ownership(rig, assets):
    requests = [need('123_Key'), need('123 key', name='Nora coat', semantic_role='wardrobe', prompt='A blue wool coat.'),
                need('123_Key')]  # Exact repeated request is the same asset.
    session = story(rig)
    result = render(rig, session, planned=plan(asset_requests=requests))
    assert result['status'] == 'succeeded', result.get('error')
    assert [request['prompt_tag'] for request in assets.requests] == ['ref-123-key', 'ref-123-key-2']
    nora = next(p for p in rig.project['subjects'] if p['name'] == 'Nora')
    assert all(request['person_id'] == nora['id'] for request in assets.requests)
    p = rig.videos.snapshot(result['run_id'])
    assert len({a['prompt_tag'] for a in p['assets']}) == len(p['assets'])


def test_paused_child_resumes_same_saved_request_even_when_ui_resends_plan(rig, assets):
    session, public, internal, request_id = saved_child(rig, assets, 'paused')
    rig.manager.action(session['id'], public['id'], 'retry',
                       {'request_id': uid(), 'plan': copy.deepcopy(internal['plan'])})
    assert internal['asset_specs'][0]['request_id'] == request_id
    rig.manager.process(session['id'], public['id'])
    assert assets.resumed == [request_id] and len(assets.requests) == 1
    assert rig.manager.get(session['id'])['turns'][0]['status'] == 'succeeded'


def test_lost_story_link_recovers_child_without_submitting_replacement(rig, assets):
    session, public, internal, request_id = saved_child(rig, assets, 'uncertain', linked=False)
    assets.resolve_to[request_id] = 'succeeded'
    rig.manager.action(session['id'], public['id'], 'retry', {'request_id': uid()})
    assert internal['asset_jobs'] == [request_id]
    rig.manager.process(session['id'], public['id'])
    assert len(assets.requests) == 1 and internal['asset_specs'][0]['request_id'] == request_id
    assert rig.manager.get(session['id'])['turns'][0]['status'] == 'succeeded'


def test_uncertain_child_keeps_original_ticket_through_repeated_recovery(rig, assets):
    session, public, internal, request_id = saved_child(rig, assets, 'uncertain', linked=False)
    for _ in range(2):
        rig.manager.action(session['id'], public['id'], 'retry', {'request_id': uid()})
        rig.manager.process(session['id'], public['id'])
        assert internal['asset_specs'][0]['request_id'] == request_id
        assert internal['status'] == 'uncertain'
    assert len(assets.requests) == 1 and not rig.videos.queues and not assets.resumed


@pytest.mark.parametrize('status', ['uncertain', 'paused', 'queued', 'running', 'cancelling'])
def test_plan_edit_cannot_abandon_an_outstanding_asset_request(rig, assets, status):
    session, public, internal, request_id = saved_child(rig, assets, status, linked=False)
    before = copy.deepcopy(internal)
    changed = plan(action='Nora carries a lantern.', asset_requests=[need('new-lantern')], transition='cut')
    with pytest.raises(ValueError, match='original image job'):
        rig.manager.action(session['id'], public['id'], 'retry', {'request_id': uid(), 'plan': changed})
    assert internal == before and len(assets.requests) == 1
    assert internal['asset_specs'][0]['request_id'] == request_id


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_explicit_terminal_retry_persists_exactly_one_new_ticket(rig, assets, status):
    session, public, internal, request_id = saved_child(rig, assets, status, linked=False)
    receipt = {'request_id': uid()}
    rig.manager.action(session['id'], public['id'], 'retry', receipt)
    replacement = internal['asset_specs'][0]['request_id']
    assert replacement != request_id and len(assets.requests) == 1  # No child POST during action.
    rig.manager.action(session['id'], public['id'], 'retry', receipt)
    assert internal['asset_specs'][0]['request_id'] == replacement
    rig.manager.process(session['id'], public['id'])
    assert internal['status'] == 'succeeded' and len(assets.requests) == 2
    assert assets.records[request_id]['status'] == status
    assert internal['asset_jobs'] == [request_id, replacement]


def test_old_invalid_unsubmitted_tag_is_repaired_only_before_admission(rig, assets):
    session, public, internal, request_id = saved_child(rig, assets, 'failed')
    del assets.records[request_id]
    internal['asset_jobs'] = []
    internal['asset_specs'][0]['prompt_tag'] = '@9___Coat'
    rig.manager.action(session['id'], public['id'], 'retry', {'request_id': uid()})
    assert internal['asset_specs'][0]['prompt_tag'] == 'ref-9-coat'
    assert internal['asset_specs'][0]['request_id'] == request_id
    rig.manager.process(session['id'], public['id'])
    assert internal['status'] == 'succeeded'
