"""No real server, model, queue or user's library is used by transfer tests."""
import copy
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from backend.comfy_transfer import (
    FL_LORA, HERETIC, REF_LORA, TransferError, _fields, _link, _settings,
    _template, _type, _validate_graph, _widget, build_transfer, installed_transfer_options, transfer_options,
)
from backend.projects import new_project


def node_schema(required, outputs, optional=None, output_node=False):
    return {'input': {'required': required, 'optional': optional or {}},
            'output': outputs, 'output_node': output_node}


@pytest.fixture
def schema():
    def enum(*values):
        return [list(values)]
    def spec(kind):
        return [kind, {}]
    def model_inputs(**kwargs):
        return {'model': spec('MODEL'), **kwargs}
    inputs = {'clip': spec('CLIP'), 'vae': spec('VAE'), 'prompt': spec('STRING'),
              'width': ['INT', {'min': 32, 'max': 16384}], 'height': ['INT', {'min': 32, 'max': 16384}],
              'length': ['INT', {'min': 5, 'max': 3600}]}
    ref_inputs = {**inputs, 'ref_image_size': ['COMBO', {'options': ['match', 'max'], 'default': 'match'}]}
    del ref_inputs['vae']
    autogrow = ['COMFY_AUTOGROW_V3', {'template': {'input': {'required': {'ref_image': spec('IMAGE')}}},
                                     'prefix': 'ref_image_', 'min': 0, 'max': 9}]
    codec = ['COMFY_DYNAMICCOMBO_V3', {'options': [
        {'key': 'h264', 'inputs': {'optional': {'encoding': ['COMFY_DYNAMICCOMBO_V3', {'options': [{'key': 'auto', 'inputs': {'required': {}}}]}]}}}]}]
    format_spec = ['COMFY_DYNAMICCOMBO_V3', {'options': [{'key': 'mp4', 'inputs': {'required': {'codec': codec}}}]}]
    return {
        'UNETLoader': node_schema({'unet_name': enum('minimax_h3_ref2va_pruned_int8_convrot.safetensors', 'minimax_h3_fl2va_pruned_int8_convrot.safetensors'), 'weight_dtype': enum('default')}, ['MODEL']),
        'CLIPLoader': node_schema({'clip_name': enum(HERETIC), 'type': enum('minimax'), 'device': enum('default')}, ['CLIP']),
        'VAELoader': node_schema({'vae_name': enum('minimax_h3_video_vae_fp16.safetensors', 'minimax_h3_audio_vae_fp32.safetensors')}, ['VAE']),
        'LoraLoaderModelOnly': node_schema(model_inputs(lora_name=enum(REF_LORA, FL_LORA), strength_model=spec('FLOAT')), ['MODEL']),
        'MiniMaxH3SigmaShift': node_schema(model_inputs(shift_video=spec('FLOAT'), shift_audio=spec('FLOAT')), ['MODEL']),
        'H3SLAAttention': node_schema(model_inputs(sparsity_ratio=spec('FLOAT'), block_size=enum('32'), min_seq_len=spec('INT'),
            dense_last_steps=spec('INT'), protect_audio=spec('BOOLEAN'), enabled=spec('BOOLEAN'), dense_steps=spec('STRING'),
            dense_backend=enum('comfy_kitchen'), disable_fp16_accum=spec('BOOLEAN'), stabilize_motion=spec('BOOLEAN'),
            reference_protection=enum('Heavy Enforcement'), tail_correction=spec('BOOLEAN'), use_int8_qk=spec('BOOLEAN'), engine=enum('comfy_kitchen')), ['MODEL']),
        'MiniMaxH3ReferenceToVideo': node_schema(ref_inputs, ['CONDITIONING', 'LATENT'], {'vae': spec('VAE'), 'audio_vae': spec('VAE'), 'ref_images': autogrow}),
        'MiniMaxH3ImageToVideo': node_schema(inputs, ['CONDITIONING', 'LATENT'], {'first_frame': spec('IMAGE'), 'last_frame': spec('IMAGE')}),
        'LoadImage': node_schema({'image': enum('unrelated-user-photo.png')}, ['IMAGE', 'MASK']),
        'BasicGuider': node_schema(model_inputs(conditioning=spec('CONDITIONING')), ['GUIDER']),
        'RandomNoise': node_schema({'noise_seed': ['INT', {'control_after_generate': True, 'min': 0}]}, ['NOISE']),
        'KSamplerSelect': node_schema({'sampler_name': enum('euler')}, ['SAMPLER']),
        'BasicScheduler': node_schema(model_inputs(scheduler=enum('simple'), steps=['INT', {'min': 1, 'max': 10000}], denoise=spec('FLOAT')), ['SIGMAS']),
        'SamplerCustomAdvanced': node_schema({'noise': spec('NOISE'), 'guider': spec('GUIDER'), 'sampler': spec('SAMPLER'), 'sigmas': spec('SIGMAS'), 'latent_image': spec('LATENT')}, ['LATENT', 'LATENT']),
        'VAEDecode': node_schema({'samples': spec('LATENT'), 'vae': spec('VAE')}, ['IMAGE']),
        'VAEDecodeAudio': node_schema({'samples': spec('LATENT'), 'vae': spec('VAE')}, ['AUDIO']),
        'CreateVideo': node_schema({'images': spec('IMAGE'), 'audio': spec('AUDIO'), 'fps': spec('FLOAT'), 'bit_depth': [ [8, 10] ], 'color_space': enum('sRGB')}, ['VIDEO']),
        'SaveVideo': node_schema({'video': spec('VIDEO'), 'filename_prefix': spec('STRING'), 'format': format_spec}, [], output_node=True),
    }


@pytest.fixture
def library(tmp_path):
    files, assets = {}, []
    for index in range(10):
        path = tmp_path / f'image-{index}.png'
        Image.new('RGB', (32 + index, 32), (index * 20, 80, 140)).save(path)
        asset = {'id': f'photo-{index}', 'name': f'Photo {index}', 'media_type': 'image', 'role': 'reference_image',
                 'semantic_role': 'face' if index < 2 else 'other', 'enabled': True, 'prompt_tag': f'photo-{index}',
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        files[asset['id']] = path
        assets.append(asset)
    return files, assets


class FakeComfy:
    def __init__(self, schema):
        self.schema, self.calls, self.uploads = schema, [], {}
        self.corrupt = False
        self.wrong_location = False
        self.offline = False

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        req = httpx.Request('GET', url)
        if self.offline:
            raise httpx.ConnectError('Offline', request=req)
        if url.endswith('/object_info'):
            return httpx.Response(200, json=self.schema, request=req)
        if url.endswith('/view'):
            p = kwargs['params']
            data = self.uploads[(p['subfolder'], p['filename'])]
            return httpx.Response(200, content=b'changed' if self.corrupt else data, request=req)
        raise AssertionError(f'Unexpected read: {url}')

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        assert url.endswith('/upload/image'), 'Transfer must never call prompt, free, queue or interrupt'
        filename, data, mime = kwargs['files']['image']
        options = kwargs['data']
        assert options['overwrite'] == 'false'
        assert options['type'] == 'input'
        self.uploads[(options['subfolder'], filename)] = data
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'name': filename, 'subfolder': 'unexpected' if self.wrong_location else options['subfolder'], 'type': 'input'})


def make_project(assets, mode='ref2va'):
    project = new_project()
    project.update(mode=mode, title='Transfer test', assets=copy.deepcopy(assets))
    return project


def build(project, library, schema, tmp_path, **settings):
    files, _ = library
    client = FakeComfy(schema)
    result = build_transfer(project, 'The exact compiled prompt. <Picture 1>', settings,
                            lambda asset: files[asset['id']], client=client, template_dir=tmp_path / 'no-templates')
    return result, client


def assert_ui_api_parity(result, schema):
    ui, api = result['workflow'], result['prompt']
    links = {link[0]: link for link in ui['links']}
    recovered = {}
    for node in ui['nodes']:
        ident = str(node['id'])
        fields = _fields(schema[node['type']], api[ident]['inputs'])
        values = iter(node['widgets_values'])
        inputs = {}
        for ui_input in node['inputs']:
            name = ui_input['name']
            spec = fields[name][0]
            value = None
            if _widget(spec):
                value = next(values)
                if len(spec) > 1 and spec[1].get('control_after_generate'):
                    assert next(values) == 'fixed'
            if ui_input['link'] is not None:
                link = links[ui_input['link']]
                value = [str(link[1]), link[2]]
            if name in api[ident]['inputs']:
                inputs[name] = value
        assert inputs == api[ident]['inputs']
        recovered[ident] = inputs
    assert set(recovered) == set(api)


def test_nine_images_exact_order_bytes_tags_and_no_queue(library, schema, tmp_path):
    files, assets = library
    selected = [assets[i] for i in [4, 0, 7, 1, 2, 8, 3, 6, 5]]
    project = make_project(selected + [{**assets[9], 'enabled': False}])
    before = copy.deepcopy(project)
    result, client = build(project, library, schema, tmp_path)
    assert project == before
    assert result['manifest']['steps'] == 8
    assert result['manifest']['lora'] == REF_LORA
    assert result['manifest']['frames'] == 124
    assert result['manifest']['actual_duration'] == pytest.approx(124 / 24)
    assert result['manifest']['queued'] is False
    assert result['manifest']['image_bytes_verified'] is True
    assert len(client.uploads) == 9
    refs = result['manifest']['images']
    assert [r['asset_id'] for r in refs] == [a['id'] for a in selected]
    assert [r['token'] for r in refs] == [f'<Picture {i}>' for i in range(1, 10)]
    assert [r['input_slot'] for r in refs] == [f'ref_images.ref_image_{i}' for i in range(9)]
    for asset, ref in zip(selected, refs):
        assert ref['sha256'] == hashlib.sha256(files[asset['id']].read_bytes()).hexdigest()
        assert ref['tag'] == asset['prompt_tag']
    assert all(method == 'GET' or url.endswith('/upload/image') for method, url, _ in client.calls)
    assert_ui_api_parity(result, schema)


@pytest.mark.parametrize('mode,roles', [('i2va', ['first_frame']), ('fl2va', ['last_frame', 'first_frame']), ('l2va', ['last_frame']), ('t2va', [])])
def test_optional_keyframes_exact_mode_no_extra_reference_upload(library, schema, tmp_path, mode, roles):
    _, assets = library
    selected = [{**assets[i], 'role': role} for i, role in enumerate(roles)]
    selected += [{**assets[8], 'role': 'context'}, {**assets[9], 'enabled': False}]
    project = make_project(selected, mode)
    result, client = build(project, library, schema, tmp_path, quality='detailed')
    inputs = result['prompt'][result['manifest']['conditioning_node_id']]['inputs']
    assert ('first_frame' in inputs) == ('first_frame' in roles)
    assert ('last_frame' in inputs) == ('last_frame' in roles)
    assert not any(k.startswith('ref_images') for k in inputs)
    assert len(client.uploads) == len(roles)
    assert result['manifest']['steps'] == 8
    assert result['manifest']['lora'] == FL_LORA
    assert result['manifest']['shift_video'] == 6
    assert_ui_api_parity(result, schema)
    if mode == 'fl2va':
        assert [r['role'] for r in result['manifest']['images']] == ['first_frame', 'last_frame']
        assert result['manifest']['images'][0]['asset_id'] == assets[1]['id']


@pytest.mark.parametrize('resolution,shape,expected', [('0.3', '16:9', (736, 416)), ('0.5', '9:16', (544, 960)),
    ('0.7', '16:9', (1152, 640)), ('1.0', '9:16', (768, 1344)), ('0.3', '1:1', (544, 544)),
    ('0.3', '4:3', (640, 480)), ('0.3', '3:4', (480, 640))])
def test_native_resolution_duration_and_config(library, schema, tmp_path, resolution, shape, expected):
    project = make_project(library[1][:1])
    project.update(duration=15, aspect_ratio=shape)
    result, _ = build(project, library, schema, tmp_path, resolution=resolution, quality='detailed', seed=123)
    manifest = result['manifest']
    assert (manifest['width'], manifest['height']) == expected
    assert manifest['frames'] == 362
    assert manifest['actual_duration'] == pytest.approx(15.0833333)
    assert manifest['megapixels'] == expected[0] * expected[1] / 1_000_000
    assert manifest['steps'] == 16 and manifest['seed'] == 123


@pytest.mark.parametrize('changes,error', [({'duration': 16}, '4–15'), ({'duration': 5.5}, 'whole seconds'),
    ({'duration': True}, 'whole seconds'), ({'duration': float('nan')}, 'whole seconds'), ({'mode': 'unknown'}, 'supported H3 mode')])
def test_invalid_project_config_rejected_before_network(library, schema, tmp_path, changes, error):
    project = make_project(library[1][:1])
    project.update(changes)
    client = FakeComfy(schema)
    with pytest.raises(TransferError, match=error):
        build_transfer(project, 'prompt', {}, lambda a: library[0][a['id']], client=client)
    assert not client.calls


@pytest.mark.parametrize('settings', [{'resolution': '8'}, {'steps': 3}, {'steps': '8'}, {'seed': -1},
    {'seed': 2**53}, {'seed': True}, {'quality': 'unbounded'}, {'aspect_ratio': 'x:y'}])
def test_invalid_settings_do_not_upload(library, schema, settings):
    client = FakeComfy(schema)
    with pytest.raises(TransferError):
        build_transfer(make_project(library[1][:1]), 'prompt', settings, lambda a: library[0][a['id']], client=client)
    assert not client.calls


@pytest.mark.parametrize('bad_url', ['https://127.0.0.1:8010', 'http://example.com:8010', 'http://127.0.0.1:8010/private',
    'http://user:secret@127.0.0.1:8010', 'file:///tmp', 'http://127.0.0.1:8010/?x=1'])
def test_endpoint_boundaries(library, schema, bad_url):
    client = FakeComfy(schema)
    with pytest.raises(TransferError, match='local ComfyUI'):
        build_transfer(make_project(library[1][:1]), 'prompt', {'comfy_urls': [bad_url]}, lambda a: library[0][a['id']], client=client)
    assert not client.calls


def test_video_audio_never_silently_dropped(library, schema):
    for media in ('audio', 'video'):
        project = make_project([{**library[1][0], 'media_type': media, 'role': 'reference_' + media}])
        client = FakeComfy(schema)
        with pytest.raises(TransferError, match='currently supports photos'):
            build_transfer(project, 'prompt', {}, lambda a: library[0][a['id']], client=client)
        assert not client.calls


def test_first_frame_error_does_not_request_an_end_photo(library, schema):
    project = make_project([], 'i2va')
    with pytest.raises(TransferError, match='one starting photo and no ending photo'):
        build_transfer(project, 'prompt', {}, lambda a: library[0][a['id']], client=FakeComfy(schema))


def test_missing_model_or_node_fails_before_upload(library, schema):
    for changed in ('model', 'node', 'keyframe'):
        installed = copy.deepcopy(schema)
        if changed == 'model':
            installed['CLIPLoader']['input']['required']['clip_name'] = [['some-other-model']]
        elif changed == 'node':
            del installed['H3SLAAttention']
        else:
            installed['MiniMaxH3ImageToVideo']['input']['required']['last_frame'] = installed['MiniMaxH3ImageToVideo']['input']['optional'].pop('last_frame')
        project = make_project([{**library[1][0], 'role': 'first_frame'}], 'i2va')
        client = FakeComfy(installed)
        with pytest.raises(TransferError):
            build_transfer(project, 'prompt', {}, lambda a: library[0][a['id']], client=client)
        assert not client.uploads


def test_hash_mismatch_invalid_image_and_trusted_resolver(library, schema, tmp_path):
    files, assets = library
    for corruption in ('hash', 'image'):
        project = make_project(assets[:1])
        if corruption == 'hash':
            project['assets'][0]['sha256'] = '0' * 64
            resolver = lambda a: files[a['id']]
        else:
            path = tmp_path / 'not-image.png'
            path.write_text('not an image')
            resolver = lambda a: path
        client = FakeComfy(schema)
        with pytest.raises(TransferError):
            build_transfer(project, 'prompt', {}, resolver, client=client)
        assert not client.calls
    project = make_project(assets[:1])
    project['assets'][0]['filename'] = 'C:/sensitive.txt'
    result, _ = build(project, library, schema, tmp_path)
    assert result['manifest']['images'][0]['sha256'] == assets[0]['sha256']


def test_verifies_comfy_saved_bytes_and_location(library, schema):
    for corruption in ('corrupt', 'wrong_location'):
        client = FakeComfy(schema)
        setattr(client, corruption, True)
        with pytest.raises(TransferError):
            build_transfer(make_project(library[1][:1]), 'prompt', {}, lambda a: library[0][a['id']], client=client)
        assert len(client.uploads) == 1
        assert all(method == 'GET' or url.endswith('/upload/image') for method, url, _ in client.calls)


def test_offline_friendly_error_and_no_model_or_queue_calls(library, schema):
    client = FakeComfy(schema)
    client.offline = True
    with pytest.raises(TransferError, match='Open ComfyUI Desktop'):
        build_transfer(make_project(library[1][:1]), 'prompt', {}, lambda a: library[0][a['id']], client=client)
    assert not client.uploads


def test_template_is_cloned_no_demo_images_and_fallback_calculation(library, schema, tmp_path):
    graph, _ = _template('ref2va', tmp_path / 'missing')
    graph['90'] = {'class_type': 'LoadImage', 'inputs': {'image': 'old-demo.png'}, '_meta': {'title': 'old demo'}}
    cond_id = next(key for key, node in graph.items() if node['class_type'] == 'MiniMaxH3ReferenceToVideo')
    graph[cond_id]['inputs']['ref_images.ref_image_0'] = ['90', 0]
    folder = tmp_path / 'templates'
    folder.mkdir()
    path = folder / '01_Ref2VA_Balanced_0p3_to_0p7.api.json'
    original = json.dumps(graph)
    path.write_text(original)
    client = FakeComfy(schema)
    result = build_transfer(make_project(library[1][:2]), 'new prompt', {}, lambda a: library[0][a['id']], client=client, template_dir=folder)
    assert path.read_text() == original
    assert 'old-demo.png' not in json.dumps(result)
    assert len([n for n in result['prompt'].values() if n['class_type'] == 'LoadImage']) == 2
    assert result['workflow']['id'] == result['id']
    assert_ui_api_parity(result, schema)


def test_every_transfer_gets_separate_non_overwriting_image_folder(library, schema, tmp_path):
    first, _ = build(make_project(library[1][:1]), library, schema, tmp_path)
    second, _ = build(make_project(library[1][:1]), library, schema, tmp_path)
    assert first['id'] != second['id']
    assert first['manifest']['images'][0]['comfy_image'] != second['manifest']['images'][0]['comfy_image']
    assert first['manifest']['output_prefix'] != second['manifest']['output_prefix']


def test_graph_preflight_checks_types_links_choices_before_writes(schema, tmp_path):
    graph, _ = _template('t2va', tmp_path / 'missing')
    _validate_graph(graph, schema, set())
    guide = next(n for n in graph.values() if n['class_type'] == 'BasicGuider')
    guide['inputs']['model'] = ['999', 0]
    with pytest.raises(TransferError, match='connection'):
        _validate_graph(graph, schema, set())


def test_catalog_is_copy_safe():
    opts = transfer_options()
    opts['steps'].append(100)
    assert 100 not in transfer_options()['steps']
    assert transfer_options()['max_duration'] == 15


def test_multiple_loras_preserve_order_strength_and_downstream_chain(library, schema, tmp_path):
    schema['LoraLoaderModelOnly']['input']['required']['lora_name'][0] += ['Extra look.safetensors', 'Other style.safetensors']
    selections = [{'name': REF_LORA, 'strength': 0.9}, {'name': 'Extra look.safetensors', 'strength': -0.4},
                  {'name': 'not-installed-disabled.safetensors', 'strength': 0.5, 'enabled': False},
                  {'name': 'Other style.safetensors', 'strength': 0.65}]
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path, loras=selections)
    graph = result['prompt']
    chain = [(key, node) for key, node in graph.items() if node['class_type'] == 'LoraLoaderModelOnly']
    assert len(chain) == 3
    last_id, sigma = next((key, node) for key, node in graph.items() if node['class_type'] == 'MiniMaxH3SigmaShift')
    reverse = []
    source = sigma['inputs']['model']
    while graph[source[0]]['class_type'] == 'LoraLoaderModelOnly':
        node = graph[source[0]]
        reverse.append((node['inputs']['lora_name'], node['inputs']['strength_model']))
        source = node['inputs']['model']
    assert list(reversed(reverse)) == [(REF_LORA, 0.9), ('Extra look.safetensors', -0.4), ('Other style.safetensors', 0.65)]
    assert graph[source[0]]['class_type'] == 'UNETLoader'
    assert [x['name'] for x in result['manifest']['loras']] == [REF_LORA, 'Extra look.safetensors', 'Other style.safetensors']
    assert result['manifest']['recipe_modified'] is True
    assert len(result['manifest']['lora_warnings']) == 2
    assert_ui_api_parity(result, schema)


@pytest.mark.parametrize('loras', [[], [{'name': REF_LORA, 'strength': float('nan')}],
    [{'name': REF_LORA, 'strength': True}], [{'name': REF_LORA, 'strength': 5}],
    [{'name': REF_LORA, 'enabled': False}], [{'name': REF_LORA, 'enabled': 'yes'}],
    [{'name': FL_LORA}], [{'name': 'not-installed.safetensors'}]])
def test_invalid_or_wrong_mode_loras_fail_before_upload(library, schema, loras):
    client = FakeComfy(schema)
    with pytest.raises(TransferError):
        build_transfer(make_project(library[1][:1]), 'prompt', {'loras': loras}, lambda a: library[0][a['id']], client=client)
    assert not client.uploads


def test_unverified_only_stack_is_explicit_override_with_warning(library, schema, tmp_path):
    schema['LoraLoaderModelOnly']['input']['required']['lora_name'][0] += ['User selected adapter.safetensors']
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path,
                      loras=[{'name': 'User selected adapter.safetensors', 'strength': 0.8}])
    assert [x['name'] for x in result['manifest']['loras']] == ['User selected adapter.safetensors']
    assert any('speed adapter is absent' in item for item in result['manifest']['lora_warnings'])
    assert result['manifest']['recipe_modified'] is True


def test_installed_catalog_is_read_only_and_does_not_infer_unknown_compatibility(schema):
    schema['LoraLoaderModelOnly']['input']['required']['lora_name'][0] += ['Some_h3_style.safetensors']
    client = FakeComfy(schema)
    options = installed_transfer_options({}, client=client)
    assert options['online'] is True
    assert options['comfy_url'] == 'http://127.0.0.1:8188'
    assert options['default_loras']['i2va'][0]['name'] == FL_LORA
    by_name = {x['name']: x for x in options['available_loras']}
    assert by_name[REF_LORA]['compatible_modes'] == ['ref2va']
    assert by_name['Some_h3_style.safetensors']['compatibility'] == 'unverified'
    assert by_name['Some_h3_style.safetensors']['compatible_modes'] == []
    assert [(method, url.rsplit('/', 1)[1]) for method, url, _ in client.calls] == [('GET', 'object_info')]
    client.offline = True
    options = installed_transfer_options({}, client=client)
    assert options['online'] is False and options['resolutions'] and options['qualities']
    assert 'Open ComfyUI Desktop' in options['error']


def test_current_v3_nested_autogrow_template_keeps_all_images(library, schema, tmp_path):
    options = schema['MiniMaxH3ReferenceToVideo']['input']['optional']['ref_images'][1]
    for key in ('prefix', 'min', 'max'):
        options['template'][key] = options.pop(key)
    result, _ = build(make_project(library[1][:9]), library, schema, tmp_path)
    assert len(result['manifest']['images']) == 9
    assert_ui_api_parity(result, schema)


def test_optional_frontend_defaults_are_explicit_without_inventing_connections(library, schema, tmp_path):
    schema['SaveVideo']['input']['optional']['codec'] = ['COMFY_DYNAMICCOMBO_V3', {
        'hidden': True, 'options': [{'key': 'auto', 'inputs': {'required': {}}}]}]
    schema['SaveVideo']['input']['optional']['metadata_json'] = ['STRING', {'default': ''}]
    schema['SaveVideo']['input']['optional']['other_video'] = ['VIDEO', {}]
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path)
    saver = next(node for node in result['prompt'].values() if node['class_type'] == 'SaveVideo')
    assert saver['inputs']['codec'] == 'auto'
    assert saver['inputs']['metadata_json'] == ''
    assert 'other_video' not in saver['inputs']
    assert_ui_api_parity(result, schema)


def test_named_autogrow_preserves_declared_segment_order_and_minimum():
    definition = node_schema({'segments': ['COMFY_AUTOGROW_V3', {'template': {
        'input': {'required': {'segment': ['MMH3_MEDIA', {}]}},
        'names': ['segment_1', 'segment_2', 'segment_10'], 'min': 2}}]}, ['MMH3_MEDIA'])
    inputs = {'segments.segment_10': ['3', 0], 'segments.segment_2': ['2', 0], 'segments.segment_1': ['1', 0]}
    fields = _fields(definition, inputs)
    assert list(fields) == ['segments.segment_1', 'segments.segment_2', 'segments.segment_10']
    assert all(spec[0][0] == 'MMH3_MEDIA' for spec in fields.values())
    with pytest.raises(TransferError, match='between 2 and 3 connections'):
        _fields(definition, {'segments.segment_1': ['1', 0]})
    assert 'segments.segment_99' not in _fields(definition, {**inputs, 'segments.segment_99': ['99', 0]})
