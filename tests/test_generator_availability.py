"""A stopped ComfyUI must never be diagnosed as missing model files."""
import copy

import httpx
import pytest

from backend.asset_runs import ENCODER, H3_FRAME_MODEL, MODELS
from test_asset_runs import setup, spec, uid
from test_h3_frame_assets import install_h3


def transport(manager, handler):
    manager.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('failure', ['offline', 'timeout', 'http_error', 'invalid_inventory'])
def test_unreachable_inventory_does_not_claim_missing_models_or_submit(setup, failure):
    manager, server, resources, _, settings, _ = setup
    settings['comfy_urls'].append('http://127.0.0.1:8000')
    def handle(request):
        assert request.method == 'GET' and request.url.path == '/object_info'
        if failure == 'offline':
            raise httpx.ConnectError('connection refused', request=request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('slow server', request=request)
        return httpx.Response(503 if failure == 'http_error' else 200, json=[])
    transport(manager, handle)
    options = manager.options()
    assert options['inventory_available'] is False
    assert options['servers'] == [] and options['models'] == []
    assert len(options['unavailable_servers']) == 2
    ident = uid()
    manager.submit(ident, spec())
    failed = manager.process(ident)
    assert failed['status'] == 'failed' and failed['submission_intent'] is False
    assert failed['prompt_id'] is None and not resources.handoffs
    assert 'could not be checked' in failed['error']
    assert 'missing' not in failed['error'].lower()
    assert '8010' in failed['error'] and '8000' in failed['error']


def test_selected_missing_weights_names_exact_model_and_preserves_choice(setup):
    manager, server, resources, *_ = setup
    server.info['UNETLoader']['input']['required']['unet_name'][0].remove(MODELS[0])
    ident = uid()
    manager.submit(ident, spec())
    result = manager.process(ident)
    assert result['status'] == 'failed' and not server.posts and not resources.handoffs
    assert f'UNETLoader: {MODELS[0]}' in result['error']
    assert 'Available alternatives: ' + MODELS[1] in result['error']
    assert result['model'] == MODELS[0]
    assert ENCODER not in result['error']


def test_requirements_cannot_be_combined_across_servers(setup):
    manager, server, resources, _, settings, _ = setup
    settings['comfy_urls'].append('http://127.0.0.1:8000')
    first, second = copy.deepcopy(server.info), copy.deepcopy(server.info)
    first['CLIPLoader']['input']['required']['clip_name'][0].remove(ENCODER)
    del second['SaveImage']
    def handle(request):
        assert request.method == 'GET' and request.url.path == '/object_info'
        return httpx.Response(200, json=first if request.url.port == 8010 else second)
    transport(manager, handle)
    ident = uid()
    manager.submit(ident, spec())
    result = manager.process(ident)
    assert result['status'] == 'failed' and not resources.handoffs
    assert '8010: CLIPLoader: ' + ENCODER in result['error']
    assert '8000: SaveImage' in result['error']


def test_unreachable_secondary_server_does_not_block_complete_primary(setup):
    manager, server, _, _, settings, _ = setup
    settings['comfy_urls'].append('http://127.0.0.1:8000')
    def handle(request):
        if request.url.port == 8000:
            raise httpx.ConnectError('offline', request=request)
        return server.handle(request)
    transport(manager, handle)
    ident = uid()
    manager.submit(ident, spec())
    assert manager.process(ident)['status'] == 'queued'
    assert len(server.posts) == 1


def test_h3_missing_node_does_not_blame_zimage_encoder(setup):
    manager, server, *_ = setup
    install_h3(server)
    del server.info['ImageFromBatch']
    ident = uid()
    manager.submit(ident, spec(model=H3_FRAME_MODEL))
    result = manager.process(ident)
    assert result['status'] == 'failed'
    assert 'ImageFromBatch' in result['error'] and ENCODER not in result['error']
    assert not server.posts
