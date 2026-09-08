import copy
import json
import uuid
from test_app import server, no_hardware
from backend.projects import new_project
from backend.compiler import compile_project
from backend.prompts import project_context, plan_prompt
from backend import comfy_transfer

def fake_transfer(project, prompt, settings, resolver):
    return {'id':str(uuid.uuid4()),'workflow':{'nodes':[],'links':[]},'prompt':{'1':{'class_type':'Example','inputs':{}}},'manifest':{'queued':False},'comfy_url':'http://127.0.0.1:8010'}

def prepare(server, monkeypatch):
    module, client, _ = server
    monkeypatch.setattr(comfy_transfer,'build_transfer',fake_transfer)
    p=new_project();p['mode']='t2va';p['story']['text']='A paper lantern glows in a quiet garden.'
    prompt=compile_project(p)['prompt']
    response=client.post('/api/comfy/prepare',headers={'X-H3-Token':module.TOKEN},json={'project':p,'prompt':prompt})
    assert response.status_code==200,response.text
    return p,prompt,response.json()

def test_transfer_scoped_ticket_roundtrip_and_export(server, monkeypatch):
    module, client, _=server
    before=copy.deepcopy(module.SETTINGS)
    _,_,result=prepare(server,monkeypatch)
    ticket=result['ticket']; assert len(ticket)==43
    response=client.get('/api/comfy/transfers/'+ticket,headers={'Origin':'http://127.0.0.1:8010'})
    assert response.status_code==200
    assert response.headers['access-control-allow-origin']=='http://127.0.0.1:8010'
    value=response.json();assert value['resource_token']==module.BRIDGE_TOKEN
    assert value['manifest']['queued'] is False
    assert module.SETTINGS==before and not list((module.DATA/'projects').glob('*.json'))
    exports=list((module.DATA/'exports').glob('comfy-*'))
    assert len(exports)==1 and {p.name for p in exports[0].iterdir()}=={'workflow.json','api-workflow.json','manifest.json'}
    assert ticket not in ''.join(p.read_text() for p in exports[0].iterdir())
    assert client.get('/api/comfy/transfers/'+ticket).status_code==200

def test_expired_wrong_origin_unknown_ticket_and_mutation_block(server,monkeypatch):
    module,client,_=server
    _,_,result=prepare(server,monkeypatch);path='/api/comfy/transfers/'+result['ticket']
    assert client.get(path,headers={'Origin':'http://127.0.0.1:8000'}).status_code==403
    assert client.get(path,headers={'Origin':'http://127.0.0.1:8188'}).status_code==403
    assert client.get('/api/bootstrap',headers={'Origin':'http://127.0.0.1:8010'}).status_code==403
    assert client.get('/api/bootstrap',headers={'Origin':'http://127.0.0.1:8188'}).status_code==403
    assert client.get('/api/comfy/transfers/'+'a'*43).status_code==404
    assert client.post('/api/comfy/prepare',json={}).status_code==403
    module.TRANSFERS[result['ticket']]['expiry']=0
    assert client.get(path).status_code==404

def test_stale_prompt_rejected_before_upload(server,monkeypatch):
    module,client,_=server
    def denied(*a,**k):raise AssertionError('Must not upload')
    monkeypatch.setattr(comfy_transfer,'build_transfer',denied)
    p=new_project();p['mode']='t2va';p['story']['text']='A garden.'
    response=client.post('/api/comfy/prepare',headers={'X-H3-Token':module.TOKEN},json={'project':p,'prompt':'old'})
    assert response.status_code==400 and not module.TRANSFERS

def test_settings_model_validation_and_no_hardware_switch_on_save(server):
    module,client,fake=server
    for bad in [' ',None,{},'x'*501]:
        assert client.post('/api/settings',headers={'X-H3-Token':module.TOKEN},json={'model':bad}).status_code==400
    assert client.post('/api/settings',headers={'X-H3-Token':module.TOKEN},json={'model':'','comfy_urls':['http://127.0.0.1:8188']}).status_code==200
    assert client.post('/api/gpu/prepare-ai',headers={'X-H3-Token':module.TOKEN},json={}).status_code==409
    assert not fake.loads and not fake.unloads
    assert client.post('/api/settings',headers={'X-H3-Token':module.TOKEN},json={'model':'another-model'}).status_code==200
    assert module.SETTINGS['model']=='another-model' and not fake.loads and not fake.unloads


def test_standard_comfy_port_has_only_scoped_transfer_access(server, monkeypatch):
    module, client, _ = server
    def standard_transfer(*args):
        return {**fake_transfer(*args), 'comfy_url': 'http://127.0.0.1:8188'}
    monkeypatch.setattr(comfy_transfer, 'build_transfer', standard_transfer)
    project = new_project(); project['mode'] = 't2va'; project['story']['text'] = 'A quiet garden.'
    response = client.post('/api/comfy/prepare', headers={'X-H3-Token': module.TOKEN},
                           json={'project': project, 'prompt': compile_project(project)['prompt']})
    assert response.status_code == 200
    path = '/api/comfy/transfers/' + response.json()['ticket']
    response = client.get(path, headers={'Origin': 'http://127.0.0.1:8188'})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'http://127.0.0.1:8188'
    assert client.get(path, headers={'Origin': 'http://127.0.0.1:8010'}).status_code == 403

def test_continuation_context_is_scoped_and_clip_local():
    p=new_project();p['simple']={'continuation':{'previous_ending':'Nora holds the box.','request':'She opens it.','sequence_start':15,'previous_duration':15,'unrelated':'do not send'}}
    result=project_context(p)
    assert result['continuation']['previous_ending']=='Nora holds the box.'
    assert 'unrelated' not in result['continuation']
    system=plan_prompt(p)[0]
    assert 'local to this clip' in system and 'Do not repeat the previous actions or speech' in system

def test_actual_continuation_planning_reserves_copied_context():
    p=new_project();p['duration']=15;p['comfy_render']={'continuation_source':'mmh3/example.mmh3'}
    context=project_context(p)['continuation_render']
    assert context['preserved_context_seconds']==1.625
    assert abs(context['new_action_seconds']-13.4583333333333)<0.00001
    assert 'example.mmh3' not in json.dumps(context)
