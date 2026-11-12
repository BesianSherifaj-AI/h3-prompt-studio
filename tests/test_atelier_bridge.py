"""Reference handoff is local, idempotent and leaves active projects alone."""
import base64, io, uuid
from PIL import Image
from test_app import auth, no_hardware, server

def payload():
    data=io.BytesIO();Image.new('RGB',(64,48),'blue').save(data,format='PNG')
    return {'request_id':str(uuid.uuid4()),'title':'Atelier reference','idea':'Slow camera move',
            'data_url':'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()}

def test_handoff_requires_token_and_cannot_submit_render(server):
    module,client,_=server
    assert client.get('/api/atelier/status').json()['bridge']==1
    assert client.post('/api/atelier/reference',json=payload()).status_code==403
    assert module.VIDEO_RUNS is None

def test_handoff_is_idempotent_and_preserves_active_project_and_edits(server):
    module,client,_=server
    original=client.post('/api/projects/new',headers=auth(module)).json()
    request=payload()
    first=client.post('/api/atelier/reference',headers=auth(module),json=request);first.raise_for_status()
    result=first.json();assert result['id']!=original['id']
    assert module.SETTINGS['last_video_project']==original['id']
    project=client.get('/api/projects/'+result['id']).json()
    assert project['story']['text']=='Slow camera move' and len(project['assets'])==1
    assert project['assets'][0]['width']==64
    project['story']['text']='User edited this later'
    client.post('/api/projects',headers=auth(module),json=project).raise_for_status()
    again=client.post('/api/atelier/reference',headers=auth(module),json=request)
    assert again.json()==result
    assert client.get('/api/projects/'+result['id']).json()['story']['text']=='User edited this later'
    assert len(client.get('/api/projects').json())==2
    assert len(list((module.DATA/'assets').iterdir()))==1
    assert module.VIDEO_RUNS is None

def test_handoff_refuses_reuse_for_different_content_and_bad_paths(server):
    module,client,_=server
    request=payload();client.post('/api/atelier/reference',headers=auth(module),json=request).raise_for_status()
    request['idea']='Different request'
    assert client.post('/api/atelier/reference',headers=auth(module),json=request).status_code==400
    request['request_id']='../../outside'
    assert client.post('/api/atelier/reference',headers=auth(module),json=request).status_code==400

def test_handoff_rejects_invalid_images_without_creating_project(server):
    module,client,_=server
    request=payload();request['data_url']='data:image/png;base64,bm90LWEtcG5n'
    assert client.post('/api/atelier/reference',headers=auth(module),json=request).status_code==400
    assert client.get('/api/projects').json()==[]
