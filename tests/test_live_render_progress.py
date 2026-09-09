import copy
import uuid

from backend.projects import atomic_json, new_project
from backend.video_runs import VideoRunManager
from backend.video_runs import _progress_event
import contextlib
import json
import queue


def test_progress_descriptor_reads_only_own_saved_node_labels_without_queue_or_network(tmp_path):
    def denied(*args, **kwargs):
        raise AssertionError('Progress descriptors must not contact or change any queue.')
    manager = VideoRunManager(tmp_path, denied, None, client_factory=denied, start_workers=False)
    project = new_project()
    ident = str(uuid.uuid4())
    manager.submit(ident, project, 'A saved private prompt')
    assert manager.live_progress(ident)['available'] is False
    record = manager.records[ident]
    record.update(status='running', comfy_url='http://127.0.0.1:8010', prompt_id=ident)
    atomic_json(manager.directory / ident / 'transfer.json', {'prompt': {'7': {'class_type': 'SamplerCustomAdvanced', 'inputs': {'private': 'never exposed'}}}})
    before = copy.deepcopy(record)
    result = manager.live_progress(ident)
    assert result['websocket_url'] == f'ws://127.0.0.1:8010/ws?clientId=h3studio-video-{ident}'
    assert result['prompt_id'] == ident and result['node_labels'] == {'7': 'Rendering the scene'}
    assert result['preview_available'] is False
    assert 'private' not in str(result) and record == before
    record['status'] = 'succeeded'
    assert manager.live_progress(ident)['websocket_url'] is None


def test_relay_filters_other_prompts_and_private_fields_and_closes_on_disconnect(tmp_path):
    closed = []
    class Client:
        def close(self): closed.append('client')
    manager = VideoRunManager(tmp_path, None, None, client_factory=Client, start_workers=False)
    ident = str(uuid.uuid4())
    manager.submit(ident, new_project(), 'private prompt')
    manager.records[ident].update(status='running', comfy_url='http://127.0.0.1:8010', prompt_id=ident)
    messages = iter([json.dumps({'type': 'progress', 'data': {'prompt_id': 'other', 'value': 1, 'max': 4}}),
                     json.dumps({'type': 'progress', 'data': {'prompt_id': ident, 'node': '7', 'value': 2, 'max': 4, 'private': 'never relay'}})])
    class Socket:
        def receive_text(self, timeout):
            assert timeout == 2
            return next(messages)
    @contextlib.contextmanager
    def connector(url, client, **kwargs):
        assert url.startswith('http://127.0.0.1:8010/ws?clientId=h3studio-video-')
        try:
            yield Socket()
        finally:
            closed.append('socket')
    stream = manager.live_progress_events(ident, connector=connector)
    assert 'connected' in next(stream)
    assert 'already open' in next(manager.live_progress_events(ident, connector=connector))
    result = next(stream)
    assert '"value":2' in result and 'never relay' not in result and 'other' not in result
    stream.close()
    assert closed == ['socket', 'client']
    assert not manager.progress_observers[ident].locked()


def test_invalid_progress_values_are_not_relayed():
    assert _progress_event({'type': 'progress', 'data': {'prompt_id': 'own', 'value': float('nan'), 'max': 8}}, 'own') is None
    assert _progress_event({'type': 'execution_error', 'data': {'prompt_id': 'own', 'private': 'traceback'}}, 'own') is None
