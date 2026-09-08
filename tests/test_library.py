import copy
from test_app import server, no_hardware
from backend.projects import new_project

def headers(module):
    return {'X-H3-Token':module.TOKEN}

def test_setup_roundtrip_is_independent_and_does_not_change_last_project(server):
    module, client, _ = server
    project = new_project()
    project['story']['text'] = 'A quiet garden.'
    before = module.SETTINGS['last_project']
    response = client.post('/api/library/templates', headers=headers(module), json={'name':'Garden setup','project':project})
    assert response.status_code == 200
    record = response.json()
    project['story']['text'] = 'Changed draft.'
    stored = client.get('/api/library/templates/' + record['id']).json()
    assert stored['project']['story']['text'] == 'A quiet garden.'
    assert module.SETTINGS['last_project'] == before
    index = client.get('/api/library').json()['templates']
    assert index[0]['name'] == 'Garden setup' and 'project' not in index[0]

def test_saved_version_notes_rating_leave_snapshot_unchanged(server):
    module, client, _ = server
    p = new_project(); p['mode'] = 't2va'; p['story']['text'] = 'A lamp lights up.'
    prompt = client.post('/api/compile', headers=headers(module), json={'project':p}).json()['prompt']
    record = client.post('/api/library/versions', headers=headers(module), json={'name':'A','project':p,'prompt':prompt}).json()
    response = client.patch('/api/library/versions/' + record['id'], headers=headers(module), json={'name':'A preferred','notes':'Readable movement.','rating':5})
    assert response.status_code == 200
    revised = response.json()
    assert revised['project'] == p and revised['prompt'] == prompt
    assert revised['rating'] == 5 and revised['notes'] == 'Readable movement.'
    assert client.patch('/api/library/versions/' + record['id'], headers=headers(module), json={'project':p}).status_code == 400

def test_library_rejects_stale_prompt_and_bad_names_without_writes(server):
    module, client, _ = server
    p = new_project()
    assert client.post('/api/library/versions', headers=headers(module), json={'name':'A','project':p,'prompt':'not compiled'}).status_code == 400
    assert client.post('/api/library/templates', headers=headers(module), json={'name':'','project':p}).status_code == 400
    assert client.post('/api/library/templates', headers=headers(module), json={'name':'A','project':p,'rating':6}).status_code == 400
    assert client.get('/api/library').json() == {'templates':[],'versions':[]}

def test_library_mutations_require_session_and_ids_are_constrained(server):
    module, client, _ = server
    assert client.post('/api/library/templates', json={'name':'A','project':new_project()}).status_code == 403
    assert client.get('/api/library/anything/not-an-id').status_code == 400
    assert client.get('/api/library/templates/not-an-id').status_code == 400

def test_missing_photo_tag_is_reported_before_any_model_request(server):
    module, client, _ = server
    p = new_project(); p['mode']='t2va'; p['story']['text']='Use @missing-photo.'
    response=client.post('/api/ai/plan',headers=headers(module),json={'project':p,'vision':True})
    assert response.status_code == 400
    assert '@missing-photo' in response.json()['detail']
