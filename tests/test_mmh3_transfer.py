"""MMH3 graph and manifest tests: no real model, queue or user files."""
import copy
import json

import httpx
import pytest

from backend.comfy_transfer import HERETIC, REF_LORA, TransferError, build_transfer
from backend.mmh3_transfer import MMH3TransferError, apply_mmh3, mmh3_options, read_mmh3_source, repair_studio_controls
from test_comfy_transfer import FakeComfy, assert_ui_api_parity, library, make_project, node_schema, schema as base_schema


@pytest.fixture
def schema():
    result = base_schema.__wrapped__()

    def s(kind, **options): return [kind, options]
    def enum(*values): return [list(values)]
    def node(required, outputs, names, optional=None, output_node=False):
        return {**node_schema(required, outputs, optional, output_node), 'output_name': names}

    grow = ['COMFY_AUTOGROW_V3', {'template': {'input': {'required': {'image': s('IMAGE')}}, 'prefix': 'image_', 'min': 0, 'max': 10}}]
    task = ['COMFY_DYNAMICCOMBO_V3', {'options': [
        {'key': 'Video (optional frames)', 'inputs': {}},
        {'key': 'References to video', 'inputs': {'optional': {'reference_images': grow}}},
    ]}]
    result.update({
        'MMH3Create': node({'prompt': s('STRING'), 'task': task, 'seed': s('INT'), 'name': s('STRING'), 'notes': s('STRING')},
                           ['MMH3_MEDIA'], ['packet'], {'first_frame': s('IMAGE'), 'last_frame': s('IMAGE')}),
        'MMH3Inspect': node({'packet': s('MMH3_MEDIA')},
                           ['STRING', 'STRING', 'STRING', 'INT', 'INT', 'STRING', 'FLOAT', 'INT', 'FLOAT', 'FLOAT',
                            'BOOLEAN', 'STRING', 'STRING', 'INT', 'BOOLEAN', 'STRING'],
                           ['summary', 'info_json', 'notes', 'width', 'height', 'aspect_ratio', 'aspect_ratio_value',
                            'frames', 'fps', 'duration', 'has_latent', 'prompt', 'task', 'seed', 'seed_recorded', 'name']),
        'MMH3PackH3Result': node({'packet': s('MMH3_MEDIA'), 'operation': s('STRING'), 'mode': s('STRING'), 'status': s('STRING'),
                                  'process_info_json': s('STRING'), 'latent_origin': enum('sampler_output', 'vae_encoded', 'derived', 'unknown')},
                                 ['MMH3_MEDIA', 'STRING', 'STRING'], ['packet', 'summary', 'result_info_json'],
                                 {'latent': s('LATENT'), 'video': s('VIDEO'), 'audio': s('AUDIO'), 'applied_loras_json': s('STRING'),
                                  'generation_settings_json': s('STRING')}),
        'MMH3Save': node({'packet': s('MMH3_MEDIA'), 'filename_prefix': s('STRING'), 'target': enum('output', 'source (in-place)'),
                          'overwrite': s('BOOLEAN')}, ['MMH3_MEDIA', 'STRING'], ['packet', 'path'], output_node=True),
        'MMH3Load': node({'file': enum('(none)', 'output::mmh3/source.mmh3', 'input::other.mmh3'),
                          'verify': enum('on_access', 'manifest', 'full'), 'path_override': s('STRING')}, ['MMH3_MEDIA'], ['packet']),
        'MMH3H3GenerationSettings': node({'packet': s('MMH3_MEDIA'), 'resolution': enum('Source'), 'duration_seconds': s('FLOAT')},
                                         ['MMH3_MEDIA', 'INT', 'INT', 'INT', 'STRING'], ['packet', 'width', 'height', 'frames', 'settings_json']),
        'MMH3H3ContinuationHandover': node({'packet': s('MMH3_MEDIA'), 'target_frames': s('INT'), 'video_handover_frames': s('INT'),
                                            'audio_handover_frames': s('INT'), 'audio_feather_frames': s('INT')},
                                           ['MMH3_MEDIA', 'LATENT', 'STRING', 'STRING'], ['packet', 'latent', 'summary', 'info_json'],
                                           {'target_width': s('INT', forceInput=True), 'target_height': s('INT', forceInput=True)}),
        'MMH3H3ContinuationCondition': node({'packet': s('MMH3_MEDIA'), 'handover_info_json': s('STRING', forceInput=True),
                                             'task_family': enum('auto', 'fl2va', 'ref2va'), 'prompt_override': s('STRING'), 'seed_override': s('INT'),
                                             'clip': s('CLIP'), 'video_vae': s('VAE'), 'width_override': s('INT', forceInput=True),
                                             'height_override': s('INT', forceInput=True), 'frames_override': s('INT', forceInput=True)},
                                            ['CONDITIONING', 'INT', 'MMH3_MEDIA', 'STRING', 'STRING', 'STRING', 'COMBO'],
                                            ['positive', 'seed', 'packet', 'mode', 'status', 'info_json', 'task_family'], {'audio_vae': s('VAE')}),
    })
    return result


class MMH3Comfy(FakeComfy):
    def __init__(self, schema):
        super().__init__(schema)
        self.card = {'id': 'source-packet-id', 'name': 'Source film', 'revision': 'saved-revision',
                     'geometry': {'width': 960, 'height': 544, 'frames': 362, 'fps': 24, 'duration': 362 / 24},
                     'has': {'latent': True, 'video': True}}

    def get(self, url, **kwargs):
        if url.endswith('/mmh3_media/file_info'):
            self.calls.append(('GET', url, kwargs))
            assert kwargs['params'] == {'file': 'output::mmh3/source.mmh3'}
            return httpx.Response(200, json=self.card, request=httpx.Request('GET', url))
        return super().get(url, **kwargs)


def build(project, library, schema, tmp_path, client=None, **settings):
    files, _ = library
    client = client or MMH3Comfy(schema)
    result = build_transfer(project, 'Exact prompt with <Picture 1>.', settings,
                            lambda asset: files[asset['id']], client=client, template_dir=tmp_path / 'no-templates')
    return result, client


def find(graph, kind):
    found = [(key, value) for key, value in graph.items() if value['class_type'] == kind]
    assert len(found) == 1
    return found[0]


def test_save_adds_no_sampling_pass_and_preserves_native_prompt_refs_audio_and_heretic(library, schema, tmp_path):
    p = make_project(library[1][:3])
    result, client = build(p, library, schema, tmp_path)
    graph = result['prompt']
    native_id, native = find(graph, 'MiniMaxH3ReferenceToVideo')
    sampler_id, sampler = find(graph, 'SamplerCustomAdvanced')
    packet_id, packet = find(graph, 'MMH3Create')
    _, pack = find(graph, 'MMH3PackH3Result')
    _, save = find(graph, 'MMH3Save')
    assert sampler['inputs']['latent_image'] == [native_id, 1]
    assert pack['inputs']['latent'] == [sampler_id, 0]
    assert pack['inputs']['packet'] == [packet_id, 0]
    for i in range(3):
        assert packet['inputs'][f'task.reference_images.image_{i}'] == native['inputs'][f'ref_images.ref_image_{i}']
    assert pack['inputs']['video'] == find(graph, 'SaveVideo')[1]['inputs']['video']
    assert pack['inputs']['audio'] == [find(graph, 'VAEDecodeAudio')[0], 0]
    assert find(graph, 'CLIPLoader')[1]['inputs']['clip_name'] == HERETIC
    assert save['inputs']['target'] == 'output' and save['inputs']['overwrite'] is False
    assert result['manifest']['mmh3']['save_enabled'] is True
    assert result['manifest']['mmh3']['saved'] is False
    assert result['manifest']['queued'] is False
    assert result['manifest']['conditioning_input_node_id'] == native_id
    assert not any('/prompt' in call[1] for call in client.calls)
    assert_ui_api_parity(result, schema)


@pytest.mark.parametrize('mode,roles', [('i2va', ['first_frame']), ('l2va', ['last_frame']), ('fl2va', ['first_frame', 'last_frame']), ('t2va', [])])
def test_save_supports_optional_first_and_last_frames(library, schema, tmp_path, mode, roles):
    assets = [{**a, 'role': role} for a, role in zip(library[1], roles)]
    result, _ = build(make_project(assets, mode), library, schema, tmp_path)
    packet = find(result['prompt'], 'MMH3Create')[1]['inputs']
    assert sorted(key for key in packet if key in ('first_frame', 'last_frame')) == sorted(roles)
    assert_ui_api_parity(result, schema)


def test_records_multi_lora_actual_order_strength_and_unknown_hash_honestly(library, schema, tmp_path):
    schema['LoraLoaderModelOnly']['input']['required']['lora_name'][0].append('style.safetensors')
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path,
                      loras=[{'name': REF_LORA, 'strength': 1}, {'name': 'style.safetensors', 'strength': 0.6}, {'name': REF_LORA, 'strength': 0.2}])
    stack = json.loads(find(result['prompt'], 'MMH3PackH3Result')[1]['inputs']['applied_loras_json'])
    assert [(x['name'], x['strength_model']) for x in stack] == [(REF_LORA, 1), ('style.safetensors', 0.6), (REF_LORA, 0.2)]
    assert all(x['sha256'] is None and x['strength_clip'] is None for x in stack)


def test_default_missing_nodes_skips_but_explicit_continuation_fails(library, tmp_path):
    schema = base_schema.__wrapped__()
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path)
    assert not result['manifest']['mmh3']['save_enabled']
    with pytest.raises(TransferError, match='MMH3 nodes'):
        build(make_project(library[1][:1]), library, schema, tmp_path, continuation_source='mmh3/source.mmh3')
    with pytest.raises(TransferError, match='Saving continuation state'):
        build(make_project(library[1][:1]), library, schema, tmp_path, save_mmh3=True)


def test_can_disable_state_saving(library, schema, tmp_path):
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path, save_mmh3=False)
    assert not any(n['class_type'].startswith('MMH3') for n in result['prompt'].values())


def _resolve_control(graph, value, schema):
    """Evaluate only the installed nodes' scalar/metadata contracts, never H3."""
    if not (isinstance(value, list) and len(value) == 2 and value[0] in graph):
        return value
    ident, slot = value
    node = graph[ident]
    inputs = node['inputs']
    output = schema[node['class_type']]['output_name'][slot]
    if node['class_type'] == 'MMH3Create' and output == 'packet':
        return {'prompt': _resolve_control(graph, inputs['prompt'], schema),
                'seed': _resolve_control(graph, inputs['seed'], schema)}
    if node['class_type'] == 'MMH3Inspect' and output in ('seed', 'prompt'):
        return _resolve_control(graph, inputs['packet'], schema)[output]
    if node['class_type'] == 'MMH3H3ContinuationCondition' and output == 'seed':
        override = _resolve_control(graph, inputs['seed_override'], schema)
        return override if override >= 0 else _resolve_control(graph, inputs['packet'], schema)['seed']
    raise AssertionError(f'Unexpected control source: {node["class_type"]}.{output}')


@pytest.mark.parametrize('mode,roles', [('ref2va', ['reference_image']), ('i2va', ['first_frame']),
                                       ('l2va', ['last_frame']), ('fl2va', ['first_frame', 'last_frame']), ('t2va', [])])
@pytest.mark.parametrize('seed', [0, 9072026])
def test_editing_archive_controls_changes_actual_native_sampling_and_prompt(library, schema, tmp_path, mode, roles, seed):
    assets = [{**a, 'role': role} for a, role in zip(library[1], roles)]
    result, _ = build(make_project(assets, mode), library, schema, tmp_path, seed=seed)
    graph = result['prompt']
    packet_id, packet = find(graph, 'MMH3Create')
    native = find(graph, 'MiniMaxH3ReferenceToVideo' if mode == 'ref2va' else 'MiniMaxH3ImageToVideo')[1]
    noise_id, noise = find(graph, 'RandomNoise')
    assert find(graph, 'SamplerCustomAdvanced')[1]['inputs']['noise'] == [noise_id, 0]
    assert _resolve_control(graph, noise['inputs']['noise_seed'], schema) == seed
    assert _resolve_control(graph, native['inputs']['prompt'], schema) == packet['inputs']['prompt']
    assert result['manifest']['prompt_control_node_id'] == result['manifest']['seed_control_node_id'] == packet_id
    assert_ui_api_parity(result, schema)
    # Reproduce an edit in ComfyUI: only the exposed Create fields change.
    packet['inputs'].update(seed=seed + 1, prompt='A different camera move and action.')
    assert _resolve_control(graph, noise['inputs']['noise_seed'], schema) == seed + 1
    assert _resolve_control(graph, native['inputs']['prompt'], schema) == packet['inputs']['prompt']
    assert sum(type(n['inputs'].get('noise_seed')) is int for n in graph.values()) == 0


@pytest.mark.parametrize('mode', ['ref2va', 't2va'])
def test_continuation_has_one_live_prompt_and_seed_control(library, schema, tmp_path, mode):
    result, _ = build(make_project(library[1][:1] if mode == 'ref2va' else [], mode), library, schema, tmp_path,
                      seed=0, continuation_source='mmh3/source.mmh3')
    graph = result['prompt']
    packet_id, packet = find(graph, 'MMH3Create')
    cond_id, condition = find(graph, 'MMH3H3ContinuationCondition')
    _, noise = find(graph, 'RandomNoise')
    assert noise['inputs']['noise_seed'] == [cond_id, 1]
    assert _resolve_control(graph, noise['inputs']['noise_seed'], schema) == 0
    assert _resolve_control(graph, condition['inputs']['prompt_override'], schema) == packet['inputs']['prompt']
    assert result['manifest']['seed_control_node_id'] == packet_id
    assert_ui_api_parity(result, schema)
    packet['inputs'].update(seed=812345, prompt='The next scene begins with a turn.')
    assert _resolve_control(graph, condition['inputs']['seed_override'], schema) == 812345
    assert _resolve_control(graph, noise['inputs']['noise_seed'], schema) == 812345
    assert _resolve_control(graph, condition['inputs']['prompt_override'], schema) == packet['inputs']['prompt']
    assert _resolve_control(graph, condition['inputs']['packet'], schema) == {'seed': 812345, 'prompt': packet['inputs']['prompt']}


def test_incomplete_mmh3_without_shared_controls_does_not_offer_dead_edit_widgets(library, schema, tmp_path):
    del schema['MMH3Inspect']
    assert not mmh3_options(schema)['mmh3_save_available']
    assert not mmh3_options(schema)['mmh3_continuation_available']
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path)
    assert not any(n['class_type'].startswith('MMH3') for n in result['prompt'].values())
    with pytest.raises(TransferError, match='MMH3 nodes'):
        build(make_project(library[1][:1]), library, schema, tmp_path, save_mmh3=True)


@pytest.mark.parametrize('continuation', [False, True])
def test_repair_legacy_graph_preserves_current_visible_controls_and_is_idempotent(library, schema, tmp_path, continuation):
    settings = {'continuation_source': 'mmh3/source.mmh3'} if continuation else {}
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path, **settings)
    graph, manifest = result['prompt'], result['manifest']
    packet_id, packet = find(graph, 'MMH3Create')
    inspect_id, _ = find(graph, 'MMH3Inspect')
    _, noise = find(graph, 'RandomNoise')
    cond_id = manifest['conditioning_node_id']
    prompt_name = 'prompt_override' if continuation else 'prompt'
    # Reproduce the older export after the user edits Create, leaving the
    # sampler's independent literal and the conditioning prompt unchanged.
    graph[cond_id]['inputs'][prompt_name] = 'Original compiled prompt.'
    noise['inputs']['noise_seed'] = 9072026
    if continuation:
        graph[cond_id]['inputs']['seed_override'] = 9072026
    packet['inputs'].update(seed=9072032, prompt='User revised prompt, kept opaque.')
    del graph[inspect_id]
    for key in ('prompt_control_node_id', 'seed_control_node_id'):
        manifest.pop(key, None)
    for key in ('shared_controls_node_id', 'inspect_node_id'):
        manifest['mmh3'].pop(key, None)
    before_graph, before_manifest = copy.deepcopy(graph), copy.deepcopy(manifest)
    repaired = repair_studio_controls(graph, schema, manifest)
    assert graph == before_graph and manifest == before_manifest
    fixed = repaired['prompt']
    assert fixed[packet_id]['inputs'] == packet['inputs']
    assert _resolve_control(fixed, find(fixed, 'RandomNoise')[1]['inputs']['noise_seed'], schema) == 9072032
    assert _resolve_control(fixed, fixed[cond_id]['inputs'][prompt_name], schema) == packet['inputs']['prompt']
    assert repaired['manifest']['seed'] == 9072032
    assert repair_studio_controls(fixed, schema, repaired['manifest']) == repaired
    # References and the saved video/source state remain exactly as selected.
    assert repaired['manifest']['images'] == manifest['images']
    assert find(fixed, 'MMH3PackH3Result')[1]['inputs'] == find(graph, 'MMH3PackH3Result')[1]['inputs']


@pytest.mark.parametrize('mutation', ['identity', 'create', 'noise', 'prompt', 'archive'])
def test_repair_refuses_unrelated_or_ambiguous_branches_without_mutation(library, schema, tmp_path, mutation):
    result, _ = build(make_project(library[1][:1]), library, schema, tmp_path)
    graph, manifest = result['prompt'], result['manifest']
    if mutation == 'identity':
        manifest['transfer_id'] = 'not-a-studio-transfer'
    elif mutation == 'create':
        manifest['mmh3']['reference_packet_node_id'] = '99999'
    elif mutation == 'noise':
        graph['99999'] = copy.deepcopy(find(graph, 'RandomNoise')[1])
    elif mutation == 'prompt':
        graph[manifest['conditioning_node_id']]['inputs']['prompt'] = ['99999', 0]
    else:
        find(graph, 'MMH3Save')[1]['inputs']['packet'] = ['99999', 0]
    before = copy.deepcopy((graph, manifest))
    with pytest.raises(MMH3TransferError):
        repair_studio_controls(graph, schema, manifest)
    assert (graph, manifest) == before


def test_f02_routes_current_refs_to_condition_and_preserved_av_prefix_to_existing_sampler(library, schema, tmp_path):
    p = make_project(library[1][:2])
    p['duration'] = 15
    result, client = build(p, library, schema, tmp_path, continuation_source='mmh3/source.mmh3')
    graph = result['prompt']
    assert not any(n['class_type'].startswith('MiniMaxH3ReferenceToVideo') for n in graph.values())
    load_id, load = find(graph, 'MMH3Load')
    packet_id, packet = find(graph, 'MMH3Create')
    hand_id, hand = find(graph, 'MMH3H3ContinuationHandover')
    cond_id, cond = find(graph, 'MMH3H3ContinuationCondition')
    _, sampler = find(graph, 'SamplerCustomAdvanced')
    _, guider = find(graph, 'BasicGuider')
    _, pack = find(graph, 'MMH3PackH3Result')
    assert load['inputs']['file'] == 'output::mmh3/source.mmh3' and load['inputs']['path_override'] == ''
    assert hand['inputs']['packet'] == [load_id, 0]
    assert cond['inputs']['packet'] == [packet_id, 0]
    assert cond['inputs']['handover_info_json'] == [hand_id, 3]
    assert cond['inputs']['task_family'] == 'ref2va'
    assert sampler['inputs']['latent_image'] == [hand_id, 1]
    assert guider['inputs']['conditioning'] == [cond_id, 0]
    assert pack['inputs']['process_info_json'] == [cond_id, 5]
    assert pack['inputs']['operation'] == 'continuation'
    assert hand['inputs']['audio_handover_frames'] == 0 and hand['inputs']['audio_feather_frames'] == 0
    assert len([key for key in packet['inputs'] if key.startswith('task.reference_images.')]) == 2
    m = result['manifest']
    assert (m['width'], m['height'], m['resolution']) == (960, 544, 'source')
    assert (m['requested_width'], m['requested_height']) == (736, 416)
    assert m['conditioning_node_id'] == cond_id
    assert m['conditioning_input_node_id'] == packet_id
    assert m['images'][0]['input_slot'] == 'task.reference_images.image_0'
    assert m['images'][0]['conditioning_input_node_id'] == packet_id
    assert m['mmh3']['new_frames'] == 323
    assert m['mmh3']['new_seconds'] == pytest.approx(323 / 24)
    assert m['mmh3']['context_seconds'] == 39 / 24
    assert '_mmh3_source_info' not in m
    assert_ui_api_parity(result, schema)
    assert client.calls[1][1].endswith('/mmh3_media/file_info')


def test_f02_text_only_uses_fl_future_without_unrelated_source_refs(library, schema, tmp_path):
    p = make_project([], 't2va')
    result, _ = build(p, library, schema, tmp_path, continuation_source='output::mmh3/source.mmh3')
    assert find(result['prompt'], 'MMH3H3ContinuationCondition')[1]['inputs']['task_family'] == 'fl2va'
    assert find(result['prompt'], 'MMH3Create')[1]['inputs']['task'] == 'Video (optional frames)'
    assert_ui_api_parity(result, schema)


@pytest.mark.parametrize('path', ['../source.mmh3', 'output::../source.mmh3', 'C:/private/source.mmh3', 'input::other.mmh3', 'mmh3/missing.mmh3', 'mmh3/source.mp4'])
def test_rejects_unlisted_or_escaping_source_before_upload(library, schema, tmp_path, path):
    client = MMH3Comfy(schema)
    with pytest.raises(TransferError):
        build(make_project(library[1][:1]), library, schema, tmp_path, client=client, continuation_source=path)
    assert client.uploads == {}


@pytest.mark.parametrize('overlap', [38, 40, 39.0, True, 141])
def test_rejects_invalid_or_overlong_context_before_upload(library, schema, tmp_path, overlap):
    client = MMH3Comfy(schema)
    with pytest.raises(TransferError, match='context'):
        build(make_project(library[1][:1]), library, schema, tmp_path, client=client,
              continuation_source='mmh3/source.mmh3', continuation_overlap_frames=overlap)
    assert client.uploads == {}


def test_source_manifest_must_have_latent_and_valid_geometry(schema):
    client = MMH3Comfy(schema)
    client.card['has']['latent'] = False
    with pytest.raises(MMH3TransferError, match='no saved H3 latent'):
        read_mmh3_source(client, 'http://127.0.0.1:8010', {'continuation_source': 'mmh3/source.mmh3'}, schema)
    client.card['has']['latent'] = True
    client.card['geometry']['frames'] = 360
    with pytest.raises(MMH3TransferError, match='frame grid'):
        read_mmh3_source(client, 'http://127.0.0.1:8010', {'continuation_source': 'mmh3/source.mmh3'}, schema)


def test_catalog_lists_only_safe_output_archives(schema):
    schema['MMH3Load']['input']['required']['file'][0].extend(['output::../escape.mmh3', 'output::wrong.mp4'])
    options = mmh3_options(schema)
    assert options['mmh3_sources'] == [{'value': 'mmh3/source.mmh3', 'selector': 'output::mmh3/source.mmh3', 'label': 'mmh3/source.mmh3'}]
    assert options['mmh3_save_available'] and options['mmh3_continuation_available']


def test_invalid_adaptation_does_not_mutate_original_graph(schema):
    from backend.comfy_transfer import _settings, _template
    p = make_project([], 't2va')
    graph, _ = _template('t2va')
    before = copy.deepcopy(graph)
    with pytest.raises(MMH3TransferError, match='Inspect'):
        apply_mmh3(graph, _settings(p, {}), p, {'continuation_source': 'mmh3/source.mmh3'}, schema)
    assert graph == before
