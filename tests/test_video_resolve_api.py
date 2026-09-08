"""The explicit unlock action is authenticated and scoped to one own run."""
from test_video_api import auth, manager, no_hardware, server


def test_resolve_requires_session_and_only_calls_selected_run(server, manager, monkeypatch):
    module, client, lm = server
    calls = []
    def resolve(ident):
        calls.append(ident)
        return {**manager.job, 'status': 'failed', 'stage': 'Previous request released; no active Comfy job found'}
    monkeypatch.setattr(manager, 'resolve_missing', resolve, raising=False)
    route = '/api/video/runs/' + manager.ident + '/resolve'
    assert client.post(route, json={}).status_code == 403
    assert calls == []
    response = client.post(route, headers=auth(module), json={})
    assert response.status_code == 200 and response.json()['status'] == 'failed'
    assert calls == [manager.ident]
    assert not lm.loads and not lm.unloads


def test_resolve_rejects_invalid_id_before_reaching_manager(server, manager, monkeypatch):
    module, client, _ = server
    monkeypatch.setattr(manager, 'resolve_missing', lambda ident: (_ for _ in ()).throw(AssertionError('Unexpected resolution')), raising=False)
    response = client.post('/api/video/runs/not-a-run/resolve', headers=auth(module), json={})
    assert response.status_code == 400
