import json

import pytest

from backend.asset_runs import AssetRunError, H3_FRAME_MODEL, _h3_catalog, _spec, build_graph
from backend.comfy_transfer import FL_LORA, HERETIC
from test_asset_runs import setup, spec, uid


def install_h3(server):
    nodes = ('MiniMaxH3SigmaShift', 'MiniMaxH3ImageToVideo', 'BasicGuider', 'RandomNoise', 'KSamplerSelect',
             'BasicScheduler', 'SamplerCustomAdvanced', 'ImageFromBatch', 'LoraLoaderModelOnly')
    for name in nodes:
        server.info[name] = {'input': {'required': {}}}
    required = [('UNETLoader', 'unet_name', 'minimax_h3_fl2va_pruned_int8_convrot.safetensors'),
                ('CLIPLoader', 'clip_name', HERETIC), ('CLIPLoader', 'type', 'minimax'),
                ('VAELoader', 'vae_name', 'minimax_h3_video_vae_fp16.safetensors'),
                ('LoraLoaderModelOnly', 'lora_name', FL_LORA), ('KSamplerSelect', 'sampler_name', 'euler'),
                ('BasicScheduler', 'scheduler', 'simple')]
    for node, field, value in required:
        server.info[node]['input']['required'].setdefault(field, [[]])[0].append(value)


def test_h3_frame_is_discovered_only_with_complete_native_recipe(setup):
    manager, server, *_ = setup
    assert H3_FRAME_MODEL not in manager.options()['models']


def test_h3_catalog_accepts_native_v3_combo_samplers(setup):
    manager, server, *_ = setup
    install_h3(server)
    server.info['KSamplerSelect']['input']['required']['sampler_name'] = ['COMBO', {'options': ['euler']}]
    server.info['BasicScheduler']['input']['required']['scheduler'] = ['COMBO', {'options': ['simple']}]
    assert H3_FRAME_MODEL in manager.options()['models']
    install_h3(server)
    options = manager.options()
    assert H3_FRAME_MODEL in options['models']
    assert options['default_model'] != H3_FRAME_MODEL
    h3 = next(item for item in options['generators'] if item['id'] == H3_FRAME_MODEL)
    assert h3['experimental'] and h3['steps'] == 4
    del server.info['ImageFromBatch']
    assert H3_FRAME_MODEL not in manager.options()['models']


def test_h3_frame_extracts_exactly_one_frame_preserves_queue_output_contract():
    request = _spec(spec(model=H3_FRAME_MODEL, width=608, height=320))
    graph = build_graph(uid(), request)
    assert graph['6']['inputs']['length'] == 5
    assert graph['14']['inputs']['batch_index'] == 2 and graph['14']['inputs']['length'] == 1
    assert graph['10']['class_type'] == 'SaveImage' and graph['10']['inputs']['images'] == ['14', 0]
    assert graph['2']['inputs']['clip_name'] == HERETIC
    assert graph['4']['inputs']['lora_name'] == FL_LORA
    assert graph['11']['inputs']['steps'] == 4
    assert not any(n['class_type'] in ('SaveVideo', 'LoadImage', 'H3SLAAttention') for n in graph.values())


@pytest.mark.parametrize('width', [129, 144, 1032])
def test_h3_frame_requires_native_dimensions(width):
    with pytest.raises(AssetRunError): _spec(spec(model=H3_FRAME_MODEL, width=width))


def test_h3_job_uses_video_resource_family_and_same_durable_receipt(setup):
    manager, server, resources, _, _, _ = setup
    install_h3(server)
    families = []
    original = resources.prepare_comfy_then
    def prepare(kind, operation):
        families.append(kind)
        return original('image', operation)
    resources.prepare_comfy_then = prepare
    ident = uid()
    request = spec(model=H3_FRAME_MODEL)
    manager.submit(ident, request)
    manager.process(ident)
    assert families == ['video'] and len(server.posts) == 1
    manager.submit(ident, request)
    assert len(server.posts) == 1
    assert json.loads((manager.directory / ident / 'record.json').read_text())['submission_intent']
