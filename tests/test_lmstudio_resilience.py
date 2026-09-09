"""Fault injection for server compatibility, bounded repair, and honest failures."""
import copy
import http.client
import io
import json
import threading
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from backend.lmstudio import LMStudioClient, LMStudioError


SCHEMA = {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value'], 'additionalProperties': False}


def reply(content, finish='stop'):
    return {'choices': [{'message': {'content': content}, 'finish_reason': finish}]}


def fake_client(*responses):
    client = LMStudioClient()
    client.native_models = lambda: [{'key': 'local-model', 'type': 'llm', 'capabilities': {'vision': True},
                                    'loaded_instances': [{'id': 'loaded-instance'}]}]
    pending = iter(responses)
    calls = []
    def request(method, path, payload=None):
        calls.append(copy.deepcopy(payload))
        response = next(pending)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)
    client._request = request
    return client, calls


def predict(client, schema=SCHEMA):
    return client.complete_json('local-model', 'Follow your assigned role.', 'Continue the scene.', schema, request_id='turn-test')


def rejection(status=400, detail='response_format json_schema unsupported'):
    return LMStudioError('Server rejected the format.', status_code=status, detail=detail)


def test_schema_fallback_keeps_one_actual_answer_repair_and_bounds_the_loop():
    client, calls = fake_client(rejection(), reply('{"wrong":"field"}'), reply('{"value":"repaired"}'))
    assert predict(client) == {'value': 'repaired'}
    assert len(calls) == 3
    assert 'response_format' not in calls[1] and 'response_format' not in calls[2]
    assert calls[2]['temperature'] == .2
    assert 'required property' in calls[2]['messages'][-1]['content']
    assert client.last_completion_info['attempts'] == 3
    assert len(client.last_completion_info['attempt_records']) == 3
    assert client.last_completion_info['attempt_records'][0]['status_code'] == 400
    client, calls = fake_client(rejection(), reply('bad'), reply('still bad'))
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert error.value.code == 'invalid_model_output' and len(calls) == 3
    assert error.value.diagnostics['locally_validated'] is False


def test_known_schema_rejection_is_cached_only_for_the_exact_instance_schema(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('backend.lmstudio.time.monotonic', lambda: clock[0])
    client, calls = fake_client(rejection(), reply('{"value":"first"}'), reply('{"value":"next"}'),
                               reply('{"value":"new schema"}'), reply('{"value":"expired"}'))
    predict(client)
    assert predict(client)['value'] == 'next'
    assert 'response_format' not in calls[-1]
    assert client.last_completion_info['schema_fallback_cached'] is True
    predict(client, dict(SCHEMA, description='A different grammar'))
    assert 'response_format' in calls[-1]
    clock[0] += 301
    predict(client)
    assert 'response_format' in calls[-1]


@pytest.mark.parametrize('status', [400, 422, 500])
def test_unsupported_valid_json_schema_conversion_uses_locally_validated_fallback(status):
    # Actual LM Studio grammar-converter error from a character-only Studio
    # scene. Boolean subschemas are legal JSON Schema but not this converter.
    schema = {'type': 'object', 'properties': {'effects': {'type': 'array', 'maxItems': 0, 'items': False}},
              'required': ['effects'], 'additionalProperties': False}
    detail = json.dumps({'error': 'Engine protocol predict request returned 400: JSON schema conversion failed: Unrecognized schema: false'})
    client, calls = fake_client(rejection(status, detail), reply('{"effects":[]}'))
    assert predict(client, schema) == {'effects': []}
    assert len(calls) == 2 and 'response_format' not in calls[1]
    assert client.last_completion_info['locally_validated'] is True
    assert '"items":false' in calls[1]['messages'][0]['content']


def test_schema_conversion_fallback_does_not_weaken_the_empty_effect_constraint():
    schema = {'type': 'object', 'properties': {'effects': {'type': 'array', 'maxItems': 0, 'items': False}},
              'required': ['effects'], 'additionalProperties': False}
    client, calls = fake_client(rejection(400, 'JSON schema conversion failed: Unrecognized schema: false'),
                               reply('{"effects":[{"kind":"holder"}]}'), reply('{"effects":[]}'))
    assert predict(client, schema) == {'effects': []}
    assert len(calls) == 3
    assert 'response_format' not in calls[1] and 'response_format' not in calls[2]
    assert 'Correct this specific validation error' in calls[2]['messages'][-1]['content']


@pytest.mark.parametrize('detail', [
    'out of memory while compiling grammar',
    'response_format failed because context length exceeded',
    'grammar internal execution failure',
])
def test_unrelated_server_failure_never_triggers_a_second_prediction(detail):
    client, calls = fake_client(rejection(500, detail))
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert len(calls) == 1
    assert error.value.diagnostics['attempts'] == 1


@pytest.mark.parametrize('envelope', [None, [], {}, {'choices': []}, {'choices': [None]},
    {'choices': [{'message': None}]}, {'choices': [{'message': {}}]},
    {'choices': [reply('x')['choices'][0], reply('y')['choices'][0]]}])
def test_malformed_protocol_envelope_never_leaks_a_python_exception_or_retries(envelope):
    client, calls = fake_client(envelope)
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert error.value.code == 'invalid_response'
    assert error.value.diagnostics['request_id'] == 'turn-test'
    assert len(calls) == 1


@pytest.mark.parametrize('content', ['\ufeff{"value":"ok"}', '```JSON\n{"value":"ok"}\n```', '```json {"value":"ok"}```'])
def test_unambiguous_format_wrappers_do_not_waste_a_generation(content):
    client, calls = fake_client(reply(content))
    assert predict(client) == {'value': 'ok'}
    assert len(calls) == 1


@pytest.mark.parametrize('content', ['{"value":"one"}{"value":"two"}', 'Here is JSON: {"value":"one"}', '```json\n{"value":"ok"}\n``` trailing'])
def test_wrapper_handling_never_extracts_arbitrary_json_from_ambiguous_text(content):
    client, calls = fake_client(reply(content), reply(content))
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert error.value.code == 'invalid_model_output' and len(calls) == 2


def test_overflowing_json_number_is_rejected_even_though_json_parser_accepts_it():
    schema = dict(SCHEMA, properties={'value': {'type': 'number'}})
    client, calls = fake_client(reply('{"value":1e999}'), reply('{"value":1e999}'))
    with pytest.raises(LMStudioError) as error:
        predict(client, schema)
    assert error.value.code == 'invalid_model_output' and len(calls) == 2


@pytest.mark.parametrize('finish,extra,code', [
    ('content_filter', {}, 'model_refusal'),
    ('stop', {'refusal': 'Cannot answer'}, 'model_refusal'),
    ('tool_calls', {}, 'response_incomplete'),
    ('stop', {'tool_calls': [{'function': {'name': 'unexpected'}}]}, 'response_incomplete'),
])
def test_incomplete_or_declined_generation_is_never_applied_even_with_valid_json(finish, extra, code):
    response = reply('{"value":"looks valid"}', finish)
    response['choices'][0]['message'].update(extra)
    client, calls = fake_client(response)
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert error.value.code == code and len(calls) == 1


def test_large_invalid_reply_is_not_duplicated_into_a_small_context_for_repair():
    client, calls = fake_client(reply('bad JSON ' * 1000), reply('{"value":"corrected"}'))
    predict(client)
    assert len(calls[-1]['messages']) == 3
    assert all(m['role'] != 'assistant' for m in calls[-1]['messages'])


def test_server_error_in_http_success_is_not_treated_as_a_model_json_repair():
    client, calls = fake_client({'error': {'message': 'context length exceeded'}})
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert error.value.code == 'context_length_exceeded' and len(calls) == 1


@pytest.mark.parametrize('status,detail,code', [
    (401, 'API token required', 'authentication_required'),
    (500, 'CUDA out of memory', 'model_out_of_memory'),
    (400, 'Input exceeds loaded context length', 'context_length_exceeded'),
    (429, 'Too many requests', 'server_busy'),
    (503, 'Unavailable', 'server_busy'),
])
def test_http_errors_offer_the_actual_recovery_category(status, detail, code):
    client = LMStudioClient()
    client._opener.open = Mock(side_effect=HTTPError('http://127.0.0.1:1234', status, '', {}, io.BytesIO(detail.encode())))
    with pytest.raises(LMStudioError) as error:
        client._request('POST', '/v1/chat/completions', {})
    assert error.value.code == code and error.value.status_code == status


@pytest.mark.parametrize('exception,code', [
    (TimeoutError('slow'), 'request_timeout'),
    (URLError(TimeoutError('slow connect')), 'request_timeout'),
    (http.client.IncompleteRead(b'partial', 100), 'connection_error'),
    (http.client.RemoteDisconnected(), 'connection_error'),
])
def test_interrupted_transport_does_not_leak_low_level_exceptions(exception, code):
    client = LMStudioClient()
    client._opener.open = Mock(side_effect=exception)
    with pytest.raises(LMStudioError) as error:
        client._request('POST', '/v1/chat/completions', {})
    assert error.value.code == code


def test_discovery_timeout_is_short_without_shortening_generation_timeout():
    client = LMStudioClient(timeout=180)
    calls = []
    def open_request(req, timeout):
        calls.append(timeout)
        raise TimeoutError()
    client._opener.open = open_request
    for method in ('GET', 'POST'):
        with pytest.raises(LMStudioError):
            client._request(method, '/api/v1/models')
    assert calls == [10, 180]


def test_http_status_is_preserved_when_the_error_body_disconnects():
    failure = HTTPError('http://127.0.0.1:1234', 401, '', {}, None)
    failure.read = Mock(side_effect=http.client.IncompleteRead(b'', 100))
    client = LMStudioClient()
    client._opener.open = Mock(side_effect=failure)
    with pytest.raises(LMStudioError) as error:
        client.native_models()
    assert error.value.code == 'authentication_required' and error.value.status_code == 401


def test_timeout_has_attempt_diagnostics_and_never_retries():
    client, calls = fake_client(LMStudioError('Still running', code='request_timeout'))
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert len(calls) == 1 and error.value.diagnostics['attempt_records'][0]['error_code'] == 'request_timeout'


def test_cancellation_after_compatibility_rejection_never_starts_fallback():
    client, calls = fake_client()
    stop = threading.Event()
    def request(*args):
        calls.append(args)
        stop.set()
        raise rejection()
    client._request = request
    with pytest.raises(LMStudioError) as error:
        client.complete_json('local-model', 'System', 'User', SCHEMA, cancel_event=stop)
    assert error.value.code == 'cancelled' and len(calls) == 1


def test_null_discovery_capabilities_and_instances_are_normalized_without_crashing():
    client = LMStudioClient()
    client._request = lambda *args: {'models': [{'key': 'not-loaded', 'type': 'llm', 'capabilities': None, 'loaded_instances': None}]}
    assert client.models()[0]['loaded'] is False
    assert client.models()[0]['vision'] is False
    assert client.loaded_instances() == []


def test_embedding_instances_cannot_be_selected_for_chat_inference():
    client, calls = fake_client()
    client.native_models = lambda: [{'key': 'local-model', 'type': 'embedding', 'loaded_instances': [{'id': 'embed'}]}]
    with pytest.raises(LMStudioError) as error:
        predict(client)
    assert error.value.code == 'model_not_loaded' and calls == []
