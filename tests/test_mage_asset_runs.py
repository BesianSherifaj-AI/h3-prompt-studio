"""Reference editing keeps immutable inputs and the existing at-most-once queue contract."""
import email
import hashlib
import json

import httpx
import pytest

from backend.asset_runs import AssetRunError, MAGE_MODEL, MAGE_ENCODER, MAGE_VAE, _spec
from test_asset_runs import setup, spec, uid


def install(server):
    for name in ('LoadImage', 'TextEncodeMageFlowEdit'):
        server.info[name] = {'input': {'required': {}}}
    for node, field, value in [('UNETLoader', 'unet_name', MAGE_MODEL),
                               ('CLIPLoader', 'clip_name', MAGE_ENCODER), ('CLIPLoader', 'type', 'mage'),
                               ('VAELoader', 'vae_name', MAGE_VAE), ('KSampler', 'sampler_name', 'euler')]:
        server.info[node]['input']['required'][field][0].append(value)
    original = server.handle
    server.uploads, server.input_images = [], {}
    def handle(request):
        if request.url.path == '/view' and request.url.params.get('type') == 'input':
            key = (request.url.params['subfolder'], request.url.params['filename'])
            return httpx.Response(200, content=server.input_images[key]) if key in server.input_images else httpx.Response(404)
        if request.url.path == '/upload/image':
            assert server.guard_held
            message = email.message_from_bytes(('Content-Type: ' + request.headers['content-type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + request.read())
            fields = {part.get_param('name', header='content-disposition'): part for part in message.get_payload()}
            subfolder = fields['subfolder'].get_payload(decode=True).decode()
            filename = fields['image'].get_filename()
            assert fields['overwrite'].get_payload(decode=True) == b'false'
            server.input_images[(subfolder, filename)] = fields['image'].get_payload(decode=True)
            server.uploads.append((subfolder, filename))
            return httpx.Response(200, json={'name': filename, 'subfolder': subfolder, 'type': 'input'})
        return original(request)
    server.handle = handle


def reference(manager, server, **changes):
    ident = uid()
    folder = manager.asset_directory / ident
    folder.mkdir(parents=True)
    (folder / 'source.png').write_bytes(server.image)
    metadata = {'id': ident, 'media_type': 'image', 'filename': 'source.png', 'width': 512, 'height': 512,
                'sha256': hashlib.sha256(server.image).hexdigest(), **changes}
    (folder / 'metadata.json').write_text(json.dumps(metadata))
    return ident


def test_catalog_requires_the_complete_editing_recipe(setup):
    manager, server, *_ = setup
    assert MAGE_MODEL not in manager.options()['models']
    install(server)
    choice = next(x for x in manager.options()['generators'] if x['model'] == MAGE_MODEL)
    assert choice['supports_references'] and choice['steps'] == 4 and choice['max_dimension'] == 2048
    del server.info['TextEncodeMageFlowEdit']
    assert MAGE_MODEL not in manager.options()['models']


@pytest.mark.parametrize('references', [None, [], ['../bad'], [uid()] * 2, [uid() for _ in range(5)], 'not-a-list'])
def test_reference_spec_bounds(references):
    with pytest.raises(AssetRunError):
        _spec(spec(model=MAGE_MODEL, reference_asset_ids=references))


def test_reference_field_is_not_silently_ignored_by_text_generators():
    with pytest.raises(AssetRunError, match='only'):
        _spec(spec(reference_asset_ids=[]))
    assert _spec(spec(model=MAGE_MODEL, reference_asset_ids=[uid()], width=768, height=1344))['height'] == 1344
    for size in (496, 2056, 513):
        with pytest.raises(AssetRunError):
            _spec(spec(model=MAGE_MODEL, reference_asset_ids=[uid()], width=size))


@pytest.mark.parametrize('changes', [{'media_type': 'video'}, {'sha256': 'wrong'}, {'filename': '../../outside.png'}, {'width': 999}, {'id': uid()}])
def test_invalid_library_reference_cannot_create_a_job(setup, changes):
    manager, server, *_ = setup
    ident = reference(manager, server, **changes)
    with pytest.raises(AssetRunError, match='reference'):
        manager.submit(uid(), spec(model=MAGE_MODEL, reference_asset_ids=[ident]))
    assert not list(manager.directory.iterdir()) and not server.requests


def test_inputs_are_frozen_before_restart_and_native_graph_uses_flat_reference_keys(setup):
    manager, server, _, _, _, create = setup
    install(server)
    first, second = reference(manager, server), reference(manager, server)
    ident = uid()
    request = spec(model=MAGE_MODEL, reference_asset_ids=[first, second])
    manager.submit(ident, request)
    (manager.asset_directory / first / 'source.png').write_bytes(b'changed after submission')
    recovered = create()
    recovered.resume(ident)
    assert recovered.process(ident)['status'] == 'queued'
    assert len(server.uploads) == 2 and len(server.posts) == 1
    graph = server.posts[0]['prompt']
    assert graph['2']['inputs']['type'] == 'mage' and graph['2']['inputs']['clip_name'] == MAGE_ENCODER
    assert graph['5']['inputs']['images.image_1'] == ['11', 0]
    assert graph['5']['inputs']['images.image_2'] == ['12', 0]
    assert graph['8']['inputs']['steps'] == 4 and graph['8']['inputs']['cfg'] == 1
    assert graph['8']['inputs']['sampler_name'] == 'euler' and graph['8']['inputs']['latent_image'] == ['5', 2]
    assert graph['11']['inputs']['image'].startswith(f'h3_prompt_studio/asset_inputs/{ident}/')
    assert not any(node['class_type'] == 'ModelSamplingAuraFlow' for node in graph.values())
    server.finish(ident)
    result = recovered.refresh(ident)
    assert result['status'] == 'succeeded'
    provenance = result['asset']['generated_by']
    assert provenance['kind'] == 'mage_flow_edit' and provenance['steps'] == 4
    assert [r['asset_id'] for r in provenance['references']] == [first, second]
    assert all(r['sha256'] == hashlib.sha256(server.image).hexdigest() for r in provenance['references'])


def test_changed_frozen_reference_blocks_before_upload_or_submission(setup):
    manager, server, *_ = setup
    install(server)
    ident = uid()
    manager.submit(ident, spec(model=MAGE_MODEL, reference_asset_ids=[reference(manager, server)]))
    next((manager.directory / ident / 'references').glob('*.png')).write_bytes(b'changed')
    result = manager.process(ident)
    assert result['status'] == 'failed' and not result['submission_intent']
    assert 'reference changed' in result['error']
    assert not server.uploads and not server.posts


def test_lost_edit_submission_recovers_without_reupload_or_second_prompt(setup):
    manager, server, _, _, _, create = setup
    install(server)
    ident = uid()
    manager.submit(ident, spec(model=MAGE_MODEL, reference_asset_ids=[reference(manager, server)]))
    server.lost_response = True
    assert manager.process(ident)['status'] == 'uncertain'
    recovered = create()
    assert recovered.resume(ident)['status'] == 'queued'
    assert len(server.uploads) == 1 and len(server.posts) == 1


def test_upload_collision_with_different_bytes_blocks_submission(setup):
    manager, server, *_ = setup
    install(server)
    ident = uid()
    manager.submit(ident, spec(model=MAGE_MODEL, reference_asset_ids=[reference(manager, server)]))
    record = json.loads((manager.directory / ident / 'record.json').read_text())
    server.input_images[(f'h3_prompt_studio/asset_inputs/{ident}', record['references'][0]['filename'])] = b'different'
    assert manager.process(ident)['status'] == 'failed'
    assert not server.uploads and not server.posts
