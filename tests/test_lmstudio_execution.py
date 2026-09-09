"""New request isolation and owned-instance contracts, without model inference."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.lmstudio import ASSISTANT_PREFIX, LMStudioClient, LMStudioError


SCHEMA = {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value'], 'additionalProperties': False}


class Struct:
    def __init__(self, data): self.data = data
    def to_dict(self): return copy.deepcopy(self.data)


class Stream:
    def __init__(self, stop=None):
        self.stop = stop
        self.cancelled = threading.Event()
        self.cancel_calls = 0
    def __iter__(self):
        if self.stop is not None:
            self.stop.set()
            assert self.cancelled.wait(2), 'Scoped cancellation should reach the live prediction stream'
        yield SimpleNamespace(content='{"value":"ok"}')
    def cancel(self):
        self.cancel_calls += 1
        self.cancelled.set()
    def result(self):
        return SimpleNamespace(content='{"value":"ok"}', stats=Struct({'stopReason': 'userStopped' if self.cancelled.is_set() else 'eosFound', 'predictedTokensCount': 8}))


class SDK:
    def __init__(self, *, loaded=True, stop=None):
        self.llm = self
        self.key, self.path = 'model', 'model/model-q4.gguf'
        self.identifier = ASSISTANT_PREFIX + 'test'
        self.config = {'contextLength': 8192, 'flashAttention': True}
        self.loaded = loaded
        self.calls = []
        self.stream = Stream(stop)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def list_downloaded(self): return [SimpleNamespace(model_key=self.key, path=self.path)]
    def list_loaded(self): return [self.handle()] if self.loaded else []
    def handle(self):
        return SimpleNamespace(identifier=self.identifier,
            get_info=lambda: Struct({'identifier': self.identifier, 'modelKey': self.key, 'path': self.path}),
            get_load_config=lambda: Struct(self.config), respond_stream=self.respond_stream,
            apply_prompt_template=lambda chat: '<system>' + str(chat), count_tokens=lambda text: 250,
            get_context_length=lambda: 8192)
    def load_new_instance(self, path, identifier, **kwargs):
        self.calls.append(('load', path, identifier, kwargs))
        assert not self.loaded
        self.identifier, self.loaded = identifier, True
        self.config = kwargs['config']
        return self.handle()
    def respond_stream(self, chat, **kwargs):
        self.calls.append(('predict', kwargs))
        kwargs['on_prompt_processing_progress'](.5)
        return self.stream
    def native(self):
        return [{'key': self.key, 'type': 'llm', 'capabilities': {'vision': True},
                 'loaded_instances': [{'id': self.identifier, 'config': {'context_length': 8192, 'parallel': 4}}] if self.loaded else []}]


def client(sdk):
    result = LMStudioClient(sdk_factory=lambda: sdk)
    result.native_models = sdk.native
    return result


def test_new_physical_instance_uses_exact_owned_id_and_verified_file():
    sdk = SDK(loaded=False)
    result = client(sdk).load_owned_model('model', instance_id=ASSISTANT_PREFIX + 'unique')
    assert result['instance_id'] == ASSISTANT_PREFIX + 'unique'
    assert sdk.calls == [('load', sdk.path, result['instance_id'], {'config': {'contextLength': 8192, 'flashAttention': True, 'offloadKVCacheToGpu': True}, 'ttl': None})]
    assert result['load_config']['offloadKVCacheToGpu'] is True


def test_owned_load_rejects_a_server_ignoring_gpu_kv_configuration():
    sdk = SDK(loaded=False)
    load = sdk.load_new_instance
    def ignore_gpu_cache(*args, **kwargs):
        handle = load(*args, **kwargs)
        sdk.config['offloadKVCacheToGpu'] = False
        return handle
    sdk.load_new_instance = ignore_gpu_cache
    with pytest.raises(LMStudioError) as error:
        client(sdk).load_owned_model('model')
    assert error.value.code == 'model_unverified'
    assert len(sdk.calls) == 1  # Never retry or unload a load with uncertain configuration.


def test_authenticated_owned_load_requests_and_verifies_gpu_cache():
    sdk = SDK(loaded=False)
    c = client(sdk); c.api_key = 'test-key'
    calls = []
    def request(method, path, payload=None):
        calls.append(payload)
        return {'status': 'loaded', 'instance_id': 'rest-instance',
                'load_config': {'context_length': 8192, 'offload_kv_cache_to_gpu': True}}
    c._request = request
    result = c.load_owned_model('model')
    assert result['ownership_transport'] == 'rest_confirmed_response'
    assert calls[-1]['offload_kv_cache_to_gpu'] is True


def test_authenticated_owned_load_rejects_unverified_gpu_cache():
    sdk = SDK(loaded=False)
    c = client(sdk); c.api_key = 'test-key'
    c._request = lambda *args: {'status': 'loaded', 'instance_id': 'rest-instance',
                              'load_config': {'context_length': 8192, 'offload_kv_cache_to_gpu': False}}
    with pytest.raises(LMStudioError) as error:
        c.load_owned_model('model')
    assert error.value.code == 'model_unverified'


def test_user_loaded_matching_model_is_not_reused_or_unloaded_by_owned_loader():
    sdk = SDK()
    with pytest.raises(LMStudioError, match='left unchanged'):
        client(sdk).load_owned_model('model')
    assert sdk.calls == []


def test_new_sdk_stream_returns_structured_result_and_reports_real_transport():
    sdk = SDK(); c = client(sdk); progress = []
    assert c.complete_json_stream('model', 'System', 'User', SCHEMA, request_id='turn-actor', on_progress=progress.append) == {'value': 'ok'}
    assert c.last_completion_info['cancellation'] == 'scoped_prediction'
    assert c.last_completion_info['reasoning_effort'] == 'model_default'
    assert progress[0] == {'stage': 'processing_context', 'progress': .5, 'request_id': 'turn-actor'}
    assert sdk.calls[0][1]['config']['contextOverflowPolicy'] == 'stopAtLimit'


def test_cancel_reaches_exact_sdk_stream_then_drains_without_output():
    stop = threading.Event(); sdk = SDK(stop=stop); c = client(sdk)
    with pytest.raises(LMStudioError) as failure:
        c.complete_json_stream('model', 'System', 'User', SCHEMA, cancel_event=stop)
    assert failure.value.code == 'cancelled'
    assert sdk.stream.cancel_calls >= 1 and c.last_completion_info is None
    assert all(call[0] != 'unload' for call in sdk.calls)


def test_sdk_context_uses_loaded_context_and_reports_image_uncertainty():
    sdk = SDK(); c = client(sdk)
    report = c.context_budget('model', 'System', 'User', 1500)
    assert report['context_length'] == 8192 and report['text_tokens'] == 250
    assert report['remaining_for_images_and_text'] == 8192 - 1500 - 256 - 250
    assert report['fully_measured'] and report['image_tokens'] == 0


def test_legacy_diagnostics_are_isolated_across_concurrent_requests():
    sdk = SDK(); c = client(sdk); barrier = threading.Barrier(2)
    def request(method, path, payload=None):
        assert path == '/v1/chat/completions'
        name = payload['messages'][1]['content']
        barrier.wait(2)
        return {'choices': [{'message': {'content': json.dumps({'value': name})}, 'finish_reason': 'stop'}], 'usage': {'name': name}}
    c._request = request
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(c.complete_json_result, 'model', 'System', name, SCHEMA, request_id=name) for name in ('one', 'two')]
        results = [task.result() for task in tasks]
    assert [(r['result']['value'], r['diagnostics']['request_id'], r['diagnostics']['usage']['name']) for r in results] == [('one', 'one', 'one'), ('two', 'two', 'two')]
    assert c.last_completion_info is None


def test_rest_cancel_waits_for_response_and_never_retries_it():
    sdk = SDK(); c = client(sdk); stop = threading.Event(); calls = []
    def request(method, path, payload=None):
        calls.append(path); stop.set()
        return {'choices': [{'message': {'content': '{"value":"late"}'}, 'finish_reason': 'stop'}]}
    c._request = request
    with pytest.raises(LMStudioError) as failure:
        c.complete_json('model', 'System', 'User', SCHEMA, cancel_event=stop)
    assert failure.value.code == 'cancelled' and calls == ['/v1/chat/completions']


def test_cancelled_request_never_performs_discovery_or_prediction():
    c = LMStudioClient(); stop = threading.Event(); stop.set()
    c._request = lambda *args: pytest.fail('Cancellation must precede any request')
    with pytest.raises(LMStudioError) as failure:
        c.complete_json('model', 'System', 'User', SCHEMA, cancel_event=stop)
    assert failure.value.code == 'cancelled'
