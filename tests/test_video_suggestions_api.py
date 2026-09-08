"""Suggestions read one verified run; they never submit or alter a project."""
import copy
import uuid

import pytest
from test_video_api import auth, manager, no_hardware, server


def test_suggestions_are_authenticated_and_validate_before_any_work(server, manager):
    module, client, _ = server
    route = '/api/video/runs/' + manager.ident + '/suggest'
    assert client.post(route, json={}).status_code == 403
    for body in ({'duration': True}, {'duration': 0}, {'duration': 16}, {'direction': []}, {'direction': 'x' * 2001}):
        assert client.post(route, headers=auth(module), json=body).status_code == 400
    assert manager.calls == []


def test_suggestions_use_combined_final_take_and_immutable_story(server, manager, monkeypatch):
    import backend.continuation_suggestions as suggestions
    module, client, lm = server
    final_id = str(uuid.uuid4())
    manager.job.update(operation='combine', can_continue=True, continue_from_run_id=final_id)
    def refresh(ident):
        if ident == manager.ident:
            return manager.job
        assert ident == final_id
        return {'id': final_id, 'status': 'succeeded', 'continuation_source': 'saved-state.mmh3'}
    monkeypatch.setattr(manager, 'refresh', refresh)
    ending = {'id': str(uuid.uuid4())}
    calls = []
    monkeypatch.setattr(module, 'video_run_ending_image', lambda ident: calls.append(('ending', ident)) or ending)
    monkeypatch.setattr(module, 'image_data', lambda ident: 'actual-image')
    monkeypatch.setattr(module.RESOURCES, 'run_ai', lambda model, fn: calls.append(('ai', model)) or fn('exact-loaded-instance'))
    original = copy.deepcopy(manager.project)
    def suggest(actual_client, instance, project, duration, image, direction, *, small_model=False):
        assert instance == 'exact-loaded-instance' and project == original
        assert duration == 5 and image == 'actual-image' and direction == 'Keep the lantern glowing.'
        return {'suggestions': [{'title': 'Gentle motion', 'idea': 'The lantern sways.'}]}
    monkeypatch.setattr(suggestions, 'suggest_continuations', suggest)
    response = client.post('/api/video/runs/'+manager.ident+'/suggest', headers=auth(module), json={'duration': 5, 'direction': 'Keep the lantern glowing.'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['source_run_id'] == final_id and result['ending_asset'] == ending
    assert result['ending_image_url'] == '/api/assets/'+ending['id']+'/file'
    assert ('ending', final_id) in calls
    assert manager.project == original and manager.calls == [('snapshot', final_id)]
    assert not lm.loads and not lm.unloads


@pytest.mark.parametrize('status, source', [('running', 'state'), ('failed', 'state'), ('succeeded', None)])
def test_suggestions_reject_unfinished_or_uncontinuable_take(server, manager, status, source):
    module, client, _ = server
    manager.job.update(status=status, continuation_source=source)
    response = client.post('/api/video/runs/'+manager.ident+'/suggest', headers=auth(module), json={'duration': 5})
    assert response.status_code == 400
    assert manager.calls == [('refresh', manager.ident)]
