"""Integration tests use temporary storage and mocked LM/Comfy/GPU only."""
import base64
import copy
import importlib
import io
import json
import sys
import uuid
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.projects import new_project, merge_plan, merge_assist, check_project
from backend import resources
from backend.lmstudio import LMStudioClient


class FakeLM:
    def __init__(self, loaded=None):
        self.loaded = copy.deepcopy(loaded or [])
        self.loads = []
        self.unloads = []

    def loaded_instances(self):
        return copy.deepcopy(self.loaded)

    def models(self):
        return [{"id": "vision", "model_key": "vision"}]

    def load_model(self, model, **kwargs):
        self.loads.append((model, kwargs))
        self.loaded.append({"id": "owned", "model_key": model})
        return {"instance_id": "owned"}

    def unload_model(self, instance):
        self.unloads.append(instance)
        self.loaded = [m for m in self.loaded if m["id"] != instance]
        return {"unloaded": True}


def denied(*args, **kwargs):
    raise AssertionError("Unexpected hardware/network operation in mocked integration test")


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setattr(resources, "tcp_listener_ports", lambda: None)
    monkeypatch.setattr(resources.subprocess, "run", denied)
    monkeypatch.setattr(httpx, "get", denied)
    monkeypatch.setattr(httpx, "post", denied)
    monkeypatch.setattr(LMStudioClient, "_request", denied)


@pytest.fixture
def server(tmp_path, monkeypatch):
    # Import app only after isolated data location is configured.
    monkeypatch.setenv("H3_STUDIO_DATA", str(tmp_path / "studio-data"))
    sys.modules.pop("backend.app", None)
    module = importlib.import_module("backend.app")
    monkeypatch.setattr(module, "gpu_snapshot", lambda: {"used_mib": 1000})
    monkeypatch.setattr(resources, "gpu_snapshot", lambda: {"used_mib": 1000})
    fake = FakeLM()
    monkeypatch.setattr(module, "client", lambda: fake)
    monkeypatch.setattr(module.RESOURCES, "get_client", lambda: fake)
    monkeypatch.setattr(module.RESOURCES, "queues", lambda: [])
    with TestClient(module.app, base_url="http://127.0.0.1:8766", raise_server_exceptions=False) as client:
        yield module, client, fake


def auth(module):
    return {"X-H3-Token": module.TOKEN, "Origin": "http://127.0.0.1:8766"}


def test_file_locations_describe_portable_storage(server, monkeypatch, tmp_path):
    module, client, _ = server
    monkeypatch.delenv('H3_STUDIO_COMFY_OUTPUT', raising=False)
    result = client.get('/api/files')
    assert result.status_code == 200
    locations = {item['id']: item for item in result.json()['locations']}
    assert set(locations) == {'examples', 'projects', 'exports', 'videos'}
    assert locations['projects']['path'] == str(module.DATA)
    assert locations['exports']['path'] == str(module.DATA / 'exports')
    assert locations['projects']['available'] is True
    assert locations['videos']['path'] == str(module.DATA / 'video_runs')
    assert locations['examples']['path'] == str(module.ROOT / 'demo')
    monkeypatch.setenv('H3_STUDIO_COMFY_OUTPUT', str(tmp_path / 'comfy-output'))
    configured = {item['id']: item for item in client.get('/api/files').json()['locations']}
    assert configured['comfy-videos']['path'] == str(tmp_path / 'comfy-output' / 'h3_prompt_studio')
    assert configured['comfy-states']['path'] == str(tmp_path / 'comfy-output' / 'mmh3')
    assert 'browser' in locations['exports']['description']


def test_open_folder_uses_only_known_absolute_path_and_requires_session(server, monkeypatch):
    module, client, _ = server
    opened = []
    monkeypatch.setattr(module.os, 'startfile', lambda path: opened.append(path), raising=False)
    assert client.post('/api/files/open', json={'id': 'exports'}).status_code == 403
    bridge = {'X-H3-Bridge': module.BRIDGE_TOKEN, 'Origin': 'http://127.0.0.1:8010'}
    assert client.post('/api/files/open', headers=bridge, json={'id': 'exports'}).status_code == 403
    assert opened == []
    result = client.post('/api/files/open', headers=auth(module), json={'id': 'exports'})
    assert result.status_code == 200
    assert result.json()['opened'] is True
    assert opened == [str((module.DATA / 'exports').resolve())]


@pytest.mark.parametrize('body', [{}, {'id': '../projects'}, {'id': 'C:\\Windows'},
                                    {'id': 'exports', 'path': 'C:\\Windows'}, {'id': []}])
def test_open_folder_rejects_paths_unknown_ids_and_extra_fields(server, monkeypatch, body):
    module, client, _ = server
    monkeypatch.setattr(module.os, 'startfile', denied, raising=False)
    assert client.post('/api/files/open', headers=auth(module), json=body).status_code == 400


def test_missing_output_folder_does_not_open_or_create_it(server, monkeypatch, tmp_path):
    module, client, _ = server
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'portable-studio')
    monkeypatch.setattr(module.os, 'startfile', denied, raising=False)
    result = client.post('/api/files/open', headers=auth(module), json={'id': 'examples'})
    assert result.status_code == 404
    assert not module.ROOT.exists()


def test_folder_open_failure_keeps_manual_path_available(server, monkeypatch):
    module, client, _ = server
    def fail(path):
        raise OSError('File Explorer unavailable')
    monkeypatch.setattr(module.os, 'startfile', fail, raising=False)
    result = client.post('/api/files/open', headers=auth(module), json={'id': 'projects'})
    assert result.status_code == 409
    assert 'manually' in result.json()['detail']


def test_system_prompt_export_modes_and_invalid_inputs(server):
    module, client, _ = server
    ref = client.get('/api/system-prompt?persona=product&mode=ref2va')
    first_last = client.get('/api/system-prompt?persona=product&mode=fl2va')
    assert ref.status_code == first_last.status_code == 200
    assert ref.json()['prompt'] != first_last.json()['prompt']
    assert 'product' in ref.json()['prompt'].lower()
    assert client.get('/api/system-prompt?persona=unknown').status_code == 400
    assert client.get('/api/system-prompt?mode=unknown').status_code == 400


def png():
    data = io.BytesIO()
    Image.new("RGB", (96, 64), "navy").save(data, "PNG")
    return data.getvalue()


def upload(module, client):
    response = client.post("/api/assets", headers=auth(module), files={"file": ("portrait.png", png(), "image/png")})
    assert response.status_code == 200
    return response.json()


def saved_project(module, client):
    p = client.post("/api/projects/new", headers=auth(module)).json()
    a = upload(module, client)
    a.update(semantic_role="face", description="User supplied facts", approved_observation="Approved image detail", observation="Unapproved detail")
    sid = str(uuid.uuid4())
    p["assets"] = [a]
    p["subjects"] = [{"id": sid, "name": "Narrator", "description": "", "asset_ids": [a["id"]]}]
    p["story"] = {"text": "Locked original story", "locked": True}
    p["shots"][0].update(action="A light moves slowly.", visible_subject_ids=[], offscreen_subject_ids=[sid],
                          dialogue=[dict(id=str(uuid.uuid4()), speaker_id=sid, language="Albanian", text="  Përshëndetje!\nExact words.  ", locked=True, delivery="softly")])
    assert client.post("/api/projects", headers=auth(module), json=p).status_code == 200
    return p


def test_bootstrap_storage_security_headers_and_history(server):
    module, client, _ = server
    boot = client.get("/api/bootstrap")
    assert boot.status_code == 200 and boot.json()["token"] == module.TOKEN
    assert boot.headers["cache-control"] == "no-store"
    assert boot.headers["x-content-type-options"] == "nosniff"
    p = saved_project(module, client)
    p["title"] = "Saved locally"
    assert client.post("/api/projects", headers=auth(module), json=p).status_code == 200
    assert client.get("/api/projects/" + p["id"]).json() == p
    assert list((module.DATA / "history" / p["id"]).glob("*.json"))
    assert str(module.DATA).startswith(str(module.DATA.parent))


@pytest.mark.parametrize("headers", [{}, {"X-H3-Token": "wrong"}, {"X-H3-Bridge": "wrong"}])
def test_mutations_require_session_token(server, headers):
    _, client, _ = server
    assert client.post("/api/projects/new", headers=headers).status_code == 403


@pytest.mark.parametrize("headers", [{"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Origin": "http://127.0.0.1:9999"}])
def test_host_and_origin_boundary(server, headers):
    module, client, _ = server
    assert client.get("/api/bootstrap", headers=headers).status_code == 403
    assert client.post("/api/projects/new", headers={**auth(module), **headers}).status_code == 403


def test_bridge_token_has_only_prepare_h3_scope(server):
    module, client, _ = server
    headers = {"X-H3-Bridge": module.BRIDGE_TOKEN, "Origin": "http://127.0.0.1:8010"}
    assert client.post("/api/gpu/prepare-h3", headers=headers, json={}).status_code == 200
    assert client.get('/api/bootstrap', headers=headers).status_code == 403
    for path, body in [("/api/projects/new", {}), ("/api/settings", {}), ("/api/assets/from-data", {}), ("/api/compile", {})]:
        assert client.post(path, headers=headers, json=body).status_code == 403


def test_images_reencoded_and_metadata_is_local(server):
    module, client, _ = server
    a = upload(module, client)
    assert a["media_type"] == "image" and a["filename"] == "source.png"
    assert (a["width"], a["height"]) == (96, 64)
    image = client.get(f"/api/assets/{a['id']}/source")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(image.content)) as im:
        assert im.mode == "RGB"
    assert client.get(f"/api/assets/{a['id']}/thumbnail").status_code == 200
    assert client.post("/api/assets", headers=auth(module), files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 400
    assert client.post("/api/assets", headers=auth(module), files={"file": ("script.svg", b'<svg onload="alert(1)"/>', "image/svg+xml")}).status_code == 400


def test_data_url_accepts_real_images_and_rejects_remote_or_invalid_data(server):
    module, client, _ = server
    data_url = "data:image/png;base64," + base64.b64encode(png()).decode()
    response = client.post("/api/assets/from-data", headers=auth(module), json={"data_url": data_url, "name": "From Comfy.png"})
    assert response.status_code == 200
    for bad in ("https://example.com/a.png", "file:///C:/secret.png", "data:image/png;base64,%%%", "data:text/html;base64,PHN2Zz4="):
        assert client.post("/api/assets/from-data", headers=auth(module), json={"data_url": bad}).status_code == 400


def test_mocked_media_probe_and_vision_refusal_for_audio(server, monkeypatch):
    module, client, _ = server
    monkeypatch.setattr(module, "media_probe", lambda p: {"streams": [{"codec_type": "audio"}], "format": {"duration": "4.0"}})
    response = client.post("/api/assets", headers=auth(module), files={"file": ("voice.wav", b"mock media bytes", "audio/wav")})
    assert response.status_code == 200 and response.json()["media_type"] == "audio"
    response = client.post("/api/ai/analyse", headers=auth(module), json={"asset": response.json()})
    assert response.status_code == 400 and "does not hear" in response.json()["detail"]


def test_portable_roundtrip_rewrites_asset_ids_and_preserves_story_dialogue(server):
    module, client, _ = server
    p = saved_project(module, client)
    exported = client.get(f"/api/projects/{p['id']}/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "project.json" in archive.namelist()
        assert all(not name.startswith(("/", "..")) for name in archive.namelist())
    response = client.post("/api/projects/import", headers=auth(module), files={"file": ("film.h3studio.zip", exported.content, "application/zip")})
    assert response.status_code == 200
    restored = response.json()
    assert restored["id"] != p["id"]
    assert restored["assets"][0]["id"] != p["assets"][0]["id"]
    assert restored["subjects"][0]["asset_ids"] == [restored["assets"][0]["id"]]
    assert restored["subjects"][0]["id"] == p["subjects"][0]["id"]
    assert restored["story"] == p["story"] and restored["shots"] == p["shots"]
    assert restored["assets"][0]["approved_observation"] == "Approved image detail"
    assert client.post("/api/compile", headers=auth(module), json={"project": restored}).json()["valid"]


def test_portable_zip_cannot_escape_with_asset_filename(server):
    module, client, _ = server
    p = saved_project(module, client)
    aid = p["assets"][0]["id"]
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("project.json", json.dumps(p))
        archive.writestr(f"assets/{aid}/metadata.json", json.dumps({"filename": "../../outside.png", "name": "bad", "mime": "image/png"}))
        archive.writestr("outside.png", png())
    response = client.post("/api/projects/import", headers=auth(module), files={"file": ("bad.zip", data.getvalue(), "application/zip")})
    assert response.status_code == 400


def test_portable_import_rejects_image_disguised_as_audio(server):
    module, client, _ = server
    p = saved_project(module, client)
    exported = client.get(f"/api/projects/{p['id']}/export")
    data = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(exported.content)) as original, zipfile.ZipFile(data, 'w') as altered:
        for name in original.namelist():
            content = original.read(name)
            if name == 'project.json':
                payload = json.loads(content)
                payload['assets'][0].update(media_type='audio', role='reference_audio')
                content = json.dumps(payload).encode()
            altered.writestr(name, content)
    response = client.post('/api/projects/import', headers=auth(module), files={'file': ('wrong.zip', data.getvalue(), 'application/zip')})
    assert response.status_code == 400
    assert 'actual file' in response.json()['detail']
    assert not (module.DATA.parent / "outside.png").exists()


def test_json_import_remints_project_and_rejects_path_identifier(server):
    module, client, _ = server
    p = new_project()
    r = client.post("/api/projects/import", headers=auth(module), files={"file": ("p.json", json.dumps(p).encode(), "application/json")})
    assert r.status_code == 200 and r.json()["id"] != p["id"]
    p["id"] = "../../escape"
    assert client.post("/api/projects", headers=auth(module), json=p).status_code == 400


def shape_project():
    """A complete draft with no local media needed for structure-only checks."""
    p = new_project()
    aid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    p['assets'] = [{'id': aid, 'name': 'Reference', 'media_type': 'image', 'role': 'reference_image',
                    'semantic_role': 'face', 'enabled': True, 'locked_order': False, 'description': '',
                    'observation': '', 'approved_observation': '', 'duration': None, 'width': 96, 'height': 64}]
    p['subjects'] = [{'id': sid, 'name': 'Subject', 'description': '', 'asset_ids': [aid]}]
    p['shots'][0]['visible_subject_ids'] = [sid]
    p['shots'][0]['dialogue'] = [{'id': str(uuid.uuid4()), 'speaker_id': sid, 'language': 'Albanian',
                                 'text': '  Përshëndetje!\nExact words.  ', 'delivery': '', 'locked': True}]
    return p


def set_shape_value(project, path, value):
    target = project
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


@pytest.mark.parametrize('path,value', [
    (('title',), {}), (('mode',), []), (('profile',), {}), (('aspect_ratio',), 16),
    (('authoring_mode',), True), (('soundscape',), {}), (('music',), []), (('custom_instructions',), False),
    (('duration',), '5'), (('duration',), True), (('story',), []), (('story', 'text'), {}),
    (('story', 'locked'), 'true'), (('style',), None), (('style', 'genre'), []),
    (('assets',), {}), (('assets', 0), None), (('assets', 0, 'id'), {}),
    (('assets', 0, 'name'), {}), (('assets', 0, 'media_type'), []), (('assets', 0, 'role'), {}),
    (('assets', 0, 'description'), []), (('assets', 0, 'observation'), {}),
    (('assets', 0, 'approved_observation'), []), (('assets', 0, 'enabled'), 'false'),
    (('assets', 0, 'audio_enabled'), 1), (('assets', 0, 'locked_order'), None),
    (('assets', 0, 'duration'), '3'), (('assets', 0, 'width'), True),
    (('subjects',), ''), (('subjects', 0), []), (('subjects', 0, 'name'), None),
    (('subjects', 0, 'description'), {}), (('subjects', 0, 'asset_ids'), 'id'),
    (('subjects', 0, 'asset_ids'), [{}]), (('shots',), []), (('shots', 0), None),
    (('shots', 0, 'duration'), '5'), (('shots', 0, 'duration'), False),
    (('shots', 0, 'camera'), []), (('shots', 0, 'camera', 'framing'), {}),
    (('shots', 0, 'action'), {}), (('shots', 0, 'setting'), []),
    (('shots', 0, 'visible_subject_ids'), None), (('shots', 0, 'offscreen_subject_ids'), [1]),
    (('shots', 0, 'dialogue'), 'Hello'), (('shots', 0, 'dialogue', 0), 'Hello'),
    (('shots', 0, 'dialogue', 0, 'speaker_id'), {}), (('shots', 0, 'dialogue', 0, 'text'), []),
    (('shots', 0, 'dialogue', 0, 'language'), {}), (('shots', 0, 'dialogue', 0, 'delivery'), []),
    (('shots', 0, 'dialogue', 0, 'locked'), 'true'), (('shots', 0, 'dialogue', 0, 'voiceover'), 1),
])
def test_project_shape_rejects_client_unrenderable_types(path, value):
    p = shape_project()
    set_shape_value(p, path, value)
    with pytest.raises(ValueError):
        check_project(p)


@pytest.mark.parametrize('version', [True, 1.0, '1', None, 2])
def test_project_shape_requires_integer_schema_version(version):
    p = new_project(); p['schema_version'] = version
    with pytest.raises(ValueError, match='version 1'):
        check_project(p)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), 10 ** 400])
def test_project_shape_rejects_numbers_the_client_cannot_represent_as_finite(value):
    p = new_project(); p['shots'][0]['duration'] = value
    with pytest.raises(ValueError, match='finite'):
        check_project(p)


def test_project_shape_accepts_drafts_without_modifying_facts_or_extensions():
    assert check_project(new_project())['shots']
    p = shape_project()
    p.update(mode='draft-mode', profile='draft-profile', duration=0)
    p['assets'][0].update(name='', role='draft-role', semantic_role='', duration=0)
    p['subjects'][0].update(name='', asset_ids=['missing-binding', 'missing-binding'])
    p['shots'][0].update(duration=-.5, visible_subject_ids=['missing-subject'], offscreen_subject_ids=['missing-subject'])
    p['shots'][0]['dialogue'][0]['speaker_id'] = ''
    p['shots'][0]['camera'] = {}
    p['comfy_source'] = {'node_id': '10', 'prompt': '  Exact source.\n', 'unknown_future_field': [1, 2]}
    p['extension'] = {'notes': ['preserved', {'value': True}]}
    original = copy.deepcopy(p)
    assert check_project(p) is p and p == original


def test_project_shape_preserves_optional_text_defaults_and_checks_values_if_present():
    p = shape_project()
    # These omissions are renderable and already have compiler defaults. Required
    # containers/arrays and values used with string/number methods are different.
    del p['subjects'][0]['description']
    del p['shots'][0]['dialogue'][0]['language']
    del p['shots'][0]['dialogue'][0]['delivery']
    del p['shots'][0]['action']
    assert check_project(p) is p
    for path in [('story', 'text'), ('shots', 0, 'duration'), ('shots', 0, 'dialogue'),
                 ('subjects', 0, 'asset_ids'), ('assets', 0, 'name')]:
        candidate = copy.deepcopy(p); target = candidate
        for part in path[:-1]: target = target[part]
        del target[path[-1]]
        with pytest.raises(ValueError): check_project(candidate)


@pytest.mark.parametrize('key,limit', [('assets', 200), ('subjects', 32), ('shots', 24)])
def test_project_shape_collection_limits(key, limit):
    p = shape_project(); template = copy.deepcopy(p[key][0])
    p[key] = [{**copy.deepcopy(template), 'id': str(uuid.uuid4())} for _ in range(limit)]
    assert check_project(p) is p
    p[key].append({**copy.deepcopy(template), 'id': str(uuid.uuid4())})
    with pytest.raises(ValueError, match=f'at most {limit}'):
        check_project(p)


@pytest.mark.parametrize('path,value', [
    (('story', 'text'), {}), (('shots', 0, 'duration'), '5'),
    (('shots', 0, 'camera'), []), (('shots', 0, 'dialogue'), 'Hello'),
    (('assets', 0, 'name'), {}), (('subjects', 0, 'asset_ids'), [{}]),
])
def test_malformed_save_and_json_or_zip_import_return_400_without_writes(server, path, value):
    module, client, _ = server
    good = saved_project(module, client); p = shape_project(); set_shape_value(p, path, value)
    files_before = sorted(str(path.relative_to(module.DATA)) for path in module.DATA.rglob('*') if path.is_file())
    assert client.post('/api/projects', headers=auth(module), json=p).status_code == 400
    encoded = json.dumps(p).encode()
    assert client.post('/api/projects/import', headers=auth(module), files={'file': ('bad.json', encoded, 'application/json')}).status_code == 400
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('project.json', encoded)
    assert client.post('/api/projects/import', headers=auth(module), files={'file': ('bad.zip', data.getvalue(), 'application/zip')}).status_code == 400
    assert client.get('/api/projects/' + good['id']).json() == good
    files_after = sorted(str(path.relative_to(module.DATA)) for path in module.DATA.rglob('*') if path.is_file())
    assert files_before == files_after


def test_editable_zero_duration_draft_saves_and_imports_with_compiler_feedback(server):
    module, client, _ = server
    p = new_project(); p['duration'] = 0; p['shots'][0]['duration'] = 0
    p['story']['text'] = '  Unfinished source story.\n'
    assert client.post('/api/projects', headers=auth(module), json=p).status_code == 200
    response = client.post('/api/projects/import', headers=auth(module), files={'file': ('draft.json', json.dumps(p).encode(), 'application/json')})
    assert response.status_code == 200
    imported = response.json()
    assert imported['duration'] == 0 and imported['shots'][0]['duration'] == 0 and imported['story'] == p['story']
    compiled = client.post('/api/compile', headers=auth(module), json={'project': imported})
    assert compiled.status_code == 200 and not compiled.json()['valid']
    assert 'invalid_duration' in {issue['code'] for issue in compiled.json()['issues']}


def test_plan_endpoint_keeps_all_source_facts_and_offscreen_dialogue(server, monkeypatch):
    module, client, fake = server
    p = saved_project(module, client)
    sid = p["subjects"][0]["id"]
    proposal = {"story": {"text": "HIJACK"}, "assets": [], "subjects": [], "duration": 99,
                "shots": [{"duration": 5, "action": "A slow light sweep.", "visible_subject_ids": [sid],
                           "dialogue": [{"text": "INVENTED"}]}]}
    monkeypatch.setattr(fake, "propose_plan", lambda *a: proposal, raising=False)
    monkeypatch.setattr(module.RESOURCES, "run_ai", lambda model, operation=None: operation("mock-loaded"))
    response = client.post("/api/ai/plan", headers=auth(module), json={"project": p})
    assert response.status_code == 200
    data = response.json(); candidate = data["candidate"]
    assert candidate["story"] == p["story"] and candidate["assets"] == p["assets"]
    assert candidate["subjects"] == p["subjects"] and candidate["duration"] == 5
    assert candidate["shots"][0]["dialogue"] == p["shots"][0]["dialogue"]
    assert candidate["shots"][0]["offscreen_subject_ids"] == [sid]
    assert candidate["shots"][0]["visible_subject_ids"] == []
    assert data["compiled"]["valid"]
    assert client.get("/api/projects/" + p["id"]).json() == p  # Proposal does not save itself.


def test_assist_protected_field_is_rejected_before_gpu(server, monkeypatch):
    module, client, _ = server
    p = saved_project(module, client)
    monkeypatch.setattr(module.RESOURCES, "run_ai", denied)
    for field in ("story", "dialogue", "asset_ids", "duration", "visible_subject_ids"):
        response = client.post("/api/ai/assist", headers=auth(module), json={"project": p, "shot_id": p["shots"][0]["id"], "field": field})
        assert response.status_code == 400


def test_assist_updates_only_allowed_field_without_saving(server, monkeypatch):
    module, client, fake = server
    p = saved_project(module, client)
    monkeypatch.setattr(fake, "assist", lambda *a: {"field": "action", "value": "A revised light movement.", "reason": "Clearer"}, raising=False)
    monkeypatch.setattr(module.RESOURCES, "run_ai", lambda model, operation=None: operation("mock-loaded"))
    response = client.post("/api/ai/assist", headers=auth(module), json={"project": p, "shot_id": p["shots"][0]["id"], "field": "action"})
    assert response.status_code == 200
    expected = copy.deepcopy(p); expected["shots"][0]["action"] = "A revised light movement."
    assert response.json()["candidate"] == expected
    assert client.get("/api/projects/" + p["id"]).json() == p


def test_plan_rounding_cannot_create_negative_or_zero_final_shot():
    p = new_project()
    for weights in ([1, 1, 1, 1e-15], [1e-100, 1e100], [1] * 8):
        candidate = merge_plan(p, {"shots": [{"duration": v} for v in weights]})
        ds = [s["duration"] for s in candidate["shots"]]
        assert min(ds) >= .001 and round(sum(ds), 3) == p["duration"]
    for bad in (True, float("nan"), 0, -1, "five"):
        with pytest.raises(ValueError): merge_plan(p, {"shots": [{"duration": bad}]})


def test_plan_rejects_combining_opposite_retained_speaker_visibility():
    p = new_project(); sid = str(uuid.uuid4())
    p["subjects"] = [{"id": sid, "name": "Speaker", "asset_ids": []}]
    first = p["shots"][0]; first.update(duration=2, visible_subject_ids=[sid], dialogue=[{"id": "d1", "speaker_id": sid, "text": "One."}])
    second = copy.deepcopy(first); second.update(id=str(uuid.uuid4()), duration=3, visible_subject_ids=[], offscreen_subject_ids=[sid], dialogue=[{"id": "d2", "speaker_id": sid, "text": "Two."}])
    p["shots"].append(second)
    with pytest.raises(ValueError, match="different visible/off-screen"):
        merge_plan(p, {"shots": [{"duration": 5}]})


@pytest.mark.parametrize("field", ["story", "assets", "dialogue", "id", "duration", "visible_subject_ids"])
def test_assist_merge_allowlist(field):
    p = new_project()
    with pytest.raises(ValueError): merge_assist(p, p["shots"][0]["id"], field, "Injected")


def manager(fake=None):
    fake = fake or FakeLM()
    return resources.ResourceManager(lambda: {"comfy_urls": ["http://127.0.0.1:8010"], "context_length": 8192}, lambda: fake), fake


def response(url, data):
    return httpx.Response(200, json=data, request=httpx.Request("GET", url))


@pytest.mark.parametrize("running,pending", [(1, 0), (0, 1)])
def test_busy_comfy_refuses_load_and_does_not_free(monkeypatch, running, pending):
    rm, lm = manager()
    monkeypatch.setattr(httpx, "get", lambda url, **kw: response(url, {"queue_running": [1] * running, "queue_pending": [1] * pending}))
    with pytest.raises(resources.ResourceError, match="running or queued"):
        rm.run_ai("vision")
    assert lm.loads == [] and not rm.lock.locked()


class FakeClock:
    def __init__(self): self.now = 0; self.sleeps = []
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds; self.sleeps.append(seconds)


def test_deferred_comfy_free_waits_for_memory_then_loads(monkeypatch):
    rm, lm = manager(); clock = FakeClock(); freed = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: response(url, {"queue_running": [], "queue_pending": []}))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: (freed.append((url, kw["json"])) or response(url, {})))
    memory = iter([24000, 23000, 3000, 3000])
    monkeypatch.setattr(resources, "gpu_snapshot", lambda: {"used_mib": next(memory, 3000)})
    monkeypatch.setattr(resources.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(resources.time, "sleep", clock.sleep)
    result = rm.run_ai("vision")
    assert result["ready"] and lm.loads == [("vision", {"context_length": 8192})]
    assert freed == [("http://127.0.0.1:8010/free", {"unload_models": True, "free_memory": True})]
    assert clock.now >= 3.25 and rm.stage == "AI ready"


def test_unverified_deferred_free_times_out_without_loading(monkeypatch):
    rm, lm = manager(); clock = FakeClock()
    monkeypatch.setattr(httpx, "get", lambda url, **kw: response(url, {"queue_running": [], "queue_pending": []}))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: response(url, {}))
    monkeypatch.setattr(resources, "gpu_snapshot", lambda: {"used_mib": 23000})
    monkeypatch.setattr(resources.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(resources.time, "sleep", clock.sleep)
    with pytest.raises(resources.ResourceError, match="could not be verified"):
        rm.run_ai("vision")
    assert not lm.loads and not rm.lock.locked()


def test_new_comfy_job_during_release_prevents_lm_load(monkeypatch):
    rm, lm = manager(); calls = []
    def get(url, **kw):
        calls.append(url)
        return response(url, {"queue_running": [] if len(calls) == 1 else [1], "queue_pending": []})
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: response(url, {}))
    monkeypatch.setattr(resources.time, "sleep", lambda s: None)
    with pytest.raises(resources.ResourceError, match="running or queued"):
        rm.run_ai("vision")
    assert lm.loads == []


def test_prepare_h3_does_not_demand_h3_unload_when_no_lm(monkeypatch):
    rm, lm = manager()
    monkeypatch.setattr(resources, "gpu_snapshot", lambda: {"used_mib": 23600})
    monkeypatch.setattr(resources.time, "sleep", denied)
    result = rm.prepare_h3()
    assert result["ready"] and lm.unloads == [] and rm.stage == "H3 ready"


def test_prepare_h3_unloads_only_owned_instance_and_serializes(monkeypatch):
    lm = FakeLM([{"id": "owned", "model_key": "vision"}]); rm, _ = manager(lm)
    rm.instance_id = "owned"; rm.model_key = "vision"
    snapshots = iter([6000, 2000, 2000])
    monkeypatch.setattr(resources, "gpu_snapshot", lambda: {"used_mib": next(snapshots, 2000)})
    assert rm.prepare_h3()["ready"] and lm.unloads == ["owned"]
    rm.lock.acquire()
    try:
        with pytest.raises(resources.ResourceError): rm.prepare_h3()
        with pytest.raises(resources.ResourceError): rm.run_ai("vision")
    finally: rm.lock.release()


def test_prepare_h3_refuses_other_loaded_model(monkeypatch):
    rm, lm = manager(FakeLM([{"id": "foreign", "model_key": "other"}]))
    monkeypatch.setattr(resources, "gpu_snapshot", lambda: {"used_mib": 10000})
    with pytest.raises(resources.ResourceError, match="outside this Studio"):
        rm.prepare_h3()
    assert lm.unloads == []


def test_closed_comfy_does_not_allow_loading_into_occupied_gpu(monkeypatch):
    rm, lm = manager()
    monkeypatch.setattr(rm, 'queues', lambda: [])
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 23000})
    with pytest.raises(resources.ResourceError, match='occupied by another process'):
        rm.run_ai('vision')
    assert lm.loads == []


def test_owned_large_ai_repeat_uses_fixed_baseline_after_comfy_free(monkeypatch):
    rm, lm = manager(FakeLM([{'id': 'owned', 'model_key': 'vision'}]))
    rm.instance_id = rm.baseline_instance_id = 'owned'
    rm.model_key = 'vision'; rm.ai_idle_memory_mib = 9200
    clock = FakeClock(); freed = []
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: response(url, {'queue_running': [], 'queue_pending': []}))
    monkeypatch.setattr(httpx, 'post', lambda url, **kw: (freed.append(url) or response(url, {})))
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 9700})
    monkeypatch.setattr(resources.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(resources.time, 'sleep', clock.sleep)
    assert rm.run_ai('vision')['ready']
    assert lm.loads == lm.unloads == []
    assert freed == ['http://127.0.0.1:8010/free'] and clock.now == 1.25
    assert rm.ai_idle_memory_mib == 9200  # Never grow the allowance after inference.


def test_owned_ai_baseline_does_not_ignore_resident_h3(monkeypatch):
    rm, lm = manager(FakeLM([{'id': 'owned', 'model_key': 'vision'}]))
    rm.instance_id = rm.baseline_instance_id = 'owned'
    rm.model_key = 'vision'; rm.ai_idle_memory_mib = 9200
    clock = FakeClock()
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: response(url, {'queue_running': [], 'queue_pending': []}))
    monkeypatch.setattr(httpx, 'post', lambda url, **kw: response(url, {}))
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 21000})
    monkeypatch.setattr(resources.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(resources.time, 'sleep', clock.sleep)
    with pytest.raises(resources.ResourceError, match='could not be verified'):
        rm.run_ai('vision')
    assert lm.loads == lm.unloads == [] and rm.ai_idle_memory_mib == 9200


def test_unowned_matching_model_with_online_comfy_is_reestablished_once(monkeypatch):
    rm, lm = manager(FakeLM([{'id': 'foreign-same-model', 'model_key': 'vision'}]))
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: response(url, {'queue_running': [], 'queue_pending': []}))
    monkeypatch.setattr(httpx, 'post', lambda url, **kw: response(url, {}))
    monkeypatch.setattr(resources.time, 'sleep', lambda seconds: None)
    memory = iter([1000, 1000, 9400, 9400])
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': next(memory, 9400)})
    assert rm.run_ai('vision')['ready']
    assert lm.unloads == ['foreign-same-model']
    assert lm.loads == [('vision', {'context_length': 8192})]
    assert rm.instance_id == rm.baseline_instance_id == 'owned'
    assert rm.ai_idle_memory_mib == 9400
    assert rm.run_ai('vision')['ready']
    assert len(lm.loads) == len(lm.unloads) == 1


def test_closed_comfy_adoption_does_not_invent_a_memory_baseline(monkeypatch):
    rm, lm = manager(FakeLM([{'id': 'user-owned', 'model_key': 'vision'}]))
    monkeypatch.setattr(rm, 'queues', lambda: [])
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 12500})
    assert rm.run_ai('vision')['ready']
    assert rm.instance_id == 'user-owned' and rm.ai_idle_memory_mib is None
    assert rm.baseline_instance_id is None and lm.loads == lm.unloads == []


def test_stale_baseline_cannot_be_applied_to_another_instance(monkeypatch):
    rm, lm = manager(FakeLM([{'id': 'new-instance', 'model_key': 'vision'}]))
    rm.instance_id = rm.baseline_instance_id = 'old-instance'
    rm.model_key = 'vision'; rm.ai_idle_memory_mib = 16000
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: response(url, {'queue_running': [], 'queue_pending': []}))
    monkeypatch.setattr(httpx, 'post', lambda url, **kw: response(url, {}))
    clock = FakeClock()
    monkeypatch.setattr(resources.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(resources.time, 'sleep', clock.sleep)
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 12000})
    with pytest.raises(resources.ResourceError, match='could not be verified'):
        rm.run_ai('vision')
    assert lm.unloads == ['new-instance'] and lm.loads == []
    assert rm.ai_idle_memory_mib is None and rm.baseline_instance_id is None


def test_unload_clears_owned_memory_baseline(monkeypatch):
    rm, lm = manager(FakeLM([{'id': 'owned', 'model_key': 'vision'}]))
    rm.instance_id = rm.baseline_instance_id = 'owned'
    rm.model_key = 'vision'; rm.ai_idle_memory_mib = 9200
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: {'used_mib': 1000})
    assert rm.prepare_h3()['ready']
    assert rm.ai_idle_memory_mib is None and rm.baseline_instance_id is None


@pytest.mark.parametrize("url", ["https://127.0.0.1:1234", "http://evil.example:1234", "file:///tmp/a", "http://u:p@127.0.0.1:1234", "http://localhost:1234/?remote=1", "http://localhost"])
def test_local_url_rejects_remote_or_ambiguous_endpoints(url):
    with pytest.raises(ValueError): resources.local_url(url)
