"""Idempotent image-editor reference imports; never start a render or activate a project."""
import base64
import hashlib
import json
import uuid
from pydantic import BaseModel, ConfigDict, Field

class Reference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str
    title: str = Field(min_length=1, max_length=160)
    idea: str = Field(default='', max_length=20000)
    data_url: str = Field(max_length=25_200_000)

def import_reference(studio, spec):
    request_id = studio.safe_id(spec.request_id)
    if not spec.title.strip() or '\x00' in spec.title + spec.idea:
        raise ValueError('Supply a valid video draft title and idea.')
    prefix = 'data:image/png;base64,'
    if not spec.data_url.startswith(prefix):
        raise ValueError('The image editor must send a PNG reference.')
    try:
        raw = base64.b64decode(spec.data_url[len(prefix):], validate=True)
    except ValueError as exc:
        raise ValueError('Invalid PNG encoding.') from exc
    if not raw or len(raw) > 18 * 1024 * 1024:
        raise ValueError('Reference images must be nonempty and at most 18 MB.')
    digest = hashlib.sha256(raw + json.dumps([spec.title, spec.idea], ensure_ascii=False).encode()).hexdigest()
    project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'atelier:project:' + request_id))
    asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'atelier:asset:' + request_id))
    folder = studio.DATA / 'atelier_handoffs'
    receipt = folder / (request_id + '.json')
    with studio.STATE_LOCK:
        folder.mkdir(exist_ok=True)
        if receipt.exists():
            saved = json.loads(receipt.read_text('utf-8'))
            if saved['digest'] != digest:
                raise ValueError('This transfer ID already belongs to a different reference.')
            if saved.get('complete'):
                return {'id': project_id, 'title': saved['title']}
        project_path = studio.DATA / 'projects' / (project_id + '.json')
        if not receipt.exists() and project_path.exists():
            raise ValueError('A project already occupies this transfer ID.')
        intent = {'digest': digest, 'id': project_id, 'title': spec.title.strip(), 'complete': False}
        studio.atomic_json(receipt, intent)
        asset_folder = studio.DATA / 'assets' / asset_id
        asset_folder.mkdir(exist_ok=True)
        asset = studio._write_asset(raw, 'Atelier reference.png', 'image/png', asset_id, asset_folder)
        project = studio.new_project('video')
        project.update(id=project_id, title=spec.title.strip(), profile='concise', assets=[asset])
        project['story']['text'] = spec.idea
        # A retry after a crash may find the already-saved draft; never overwrite it.
        if not project_path.exists():
            studio.atomic_json(project_path, studio.check_project(project))
        studio.atomic_json(receipt, {**intent, 'complete': True})
        return {'id': project_id, 'title': intent['title']}

def install(app, studio):
    @app.get('/api/atelier/status')
    def status():
        return {'bridge': 1, 'version': app.version}

    @app.post('/api/atelier/reference')
    def reference(spec: Reference):
        return import_reference(studio, spec)
