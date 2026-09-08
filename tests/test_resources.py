"""CPU-only coordinator listener tests; no real model/GPU/network operations."""
from types import SimpleNamespace

import httpx
import pytest

from backend import resources


def denied(*args, **kwargs):
    raise AssertionError('Unexpected GPU/model/network operation')


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setattr(resources, 'gpu_snapshot', denied)
    monkeypatch.setattr(resources.httpx, 'get', denied)
    monkeypatch.setattr(resources.httpx, 'post', denied)


def manager():
    return resources.ResourceManager(
        lambda: {'comfy_urls': ['http://127.0.0.1:8000', 'http://127.0.0.1:8010'], 'context_length': 8192}, denied)


def queue_response(url, running=None, pending=None):
    return httpx.Response(200, request=httpx.Request('GET', url),
                          json={'queue_running': running or [], 'queue_pending': pending or []})


def test_listener_snapshot_includes_ipv4_ipv6_and_wildcards(monkeypatch):
    calls = []
    def connections(**kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(status=status, laddr=SimpleNamespace(ip=ip, port=port)) for status, ip, port in
                [('LISTEN', '127.0.0.1', 8000), ('LISTEN', '::', 8010), ('LISTEN', '0.0.0.0', 8766),
                 ('ESTABLISHED', '127.0.0.1', 1234)]]
    monkeypatch.setattr(resources.sys, 'platform', 'win32')
    monkeypatch.setattr(resources.psutil, 'net_connections', connections)
    assert resources.tcp_listener_ports() == frozenset((8000, 8010, 8766))
    assert calls == [{'kind': 'tcp'}]


@pytest.mark.parametrize('failure', [OSError('OS table unavailable'), resources.psutil.AccessDenied()])
def test_listener_discovery_failure_is_unknown(monkeypatch, failure):
    monkeypatch.setattr(resources.sys, 'platform', 'win32')
    def connections(**kwargs): raise failure
    monkeypatch.setattr(resources.psutil, 'net_connections', connections)
    assert resources.tcp_listener_ports() is None


def test_missing_dependency_or_unverified_platform_is_unknown(monkeypatch):
    monkeypatch.setattr(resources.sys, 'platform', 'linux')
    assert resources.tcp_listener_ports() is None
    monkeypatch.setattr(resources.sys, 'platform', 'win32')
    monkeypatch.setattr(resources, 'psutil', None)
    assert resources.tcp_listener_ports() is None


def test_absent_ports_skip_http_and_each_call_refreshes(monkeypatch):
    calls = []
    def listeners():
        calls.append(True)
        return frozenset()
    monkeypatch.setattr(resources, 'tcp_listener_ports', listeners)
    rm = manager()
    assert all(not item['online'] for item in rm.assert_idle())
    assert all(not item['online'] for item in rm.assert_idle())
    assert len(calls) == 2


@pytest.mark.parametrize('snapshot', [None, frozenset((8000, 8010))])
def test_present_or_unknown_ports_require_live_http_without_proxy(monkeypatch, snapshot):
    monkeypatch.setattr(resources, 'tcp_listener_ports', lambda: snapshot)
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return queue_response(url)
    monkeypatch.setattr(resources.httpx, 'get', get)
    assert all(item['online'] for item in manager().assert_idle())
    assert len(calls) == 2
    assert all(kwargs == {'timeout': 3, 'trust_env': False} for _, kwargs in calls)


def test_new_listener_and_pending_job_cannot_use_prior_offline_state(monkeypatch):
    snapshots = iter([frozenset(), frozenset((8010,))])
    monkeypatch.setattr(resources, 'tcp_listener_ports', lambda: next(snapshots))
    rm = manager()
    assert all(not item['online'] for item in rm.assert_idle())
    monkeypatch.setattr(resources.httpx, 'get', lambda url, **kwargs: queue_response(url, pending=[1]))
    with pytest.raises(resources.ResourceError, match='running or queued'):
        rm.assert_idle()


@pytest.mark.parametrize('error', [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
def test_uncertain_http_failure_is_never_idle(monkeypatch, error):
    monkeypatch.setattr(resources, 'tcp_listener_ports', lambda: None)
    def get(url, **kwargs): raise error('Unverified')
    monkeypatch.setattr(resources.httpx, 'get', get)
    with pytest.raises(resources.ResourceError, match='Cannot confirm'):
        manager().assert_idle()


@pytest.mark.parametrize('fresh', [None, frozenset((8010,))])
def test_connect_error_with_remaining_or_unknown_listener_is_not_idle(monkeypatch, fresh):
    snapshots = iter([frozenset((8010,)), fresh])
    monkeypatch.setattr(resources, 'tcp_listener_ports', lambda: next(snapshots))
    def get(url, **kwargs): raise httpx.ConnectError('Unverified')
    monkeypatch.setattr(resources.httpx, 'get', get)
    with pytest.raises(resources.ResourceError, match='closure is unverified'):
        manager().assert_idle()


def test_server_stopped_after_snapshot_requires_fresh_absence_evidence(monkeypatch):
    snapshots = iter([frozenset((8010,)), frozenset()])
    monkeypatch.setattr(resources, 'tcp_listener_ports', lambda: next(snapshots))
    def get(url, **kwargs): raise httpx.ConnectError('Stopped')
    monkeypatch.setattr(resources.httpx, 'get', get)
    assert all(not item['online'] for item in manager().assert_idle())


def test_prepare_h3_then_holds_same_ai_lock_through_submission(monkeypatch):
    rm = manager()
    calls = []
    def prepare():
        assert rm.lock.locked()
        calls.append('prepared')
        return {'ready': True}
    def submit():
        assert rm.lock.locked()
        with pytest.raises(resources.ResourceError, match='in progress'):
            rm.run_ai('test-model')
        calls.append('submitted')
        return {'prompt_id': 'own-test-id'}
    monkeypatch.setattr(rm, '_prepare_h3_locked', prepare)
    assert rm.prepare_h3_then(submit) == {'prompt_id': 'own-test-id'}
    assert calls == ['prepared', 'submitted'] and not rm.lock.locked()


@pytest.mark.parametrize('failure_stage', ['prepare', 'submit'])
def test_prepare_h3_then_exception_releases_lock_without_repeating_callback(monkeypatch, failure_stage):
    rm = manager()
    calls = []
    def prepare():
        assert rm.lock.locked()
        calls.append('prepared')
        if failure_stage == 'prepare':
            raise RuntimeError('Preparation failed')
        return {'ready': True}
    def submit():
        assert rm.lock.locked()
        calls.append('submitted')
        raise RuntimeError('Submission response lost')
    monkeypatch.setattr(rm, '_prepare_h3_locked', prepare)
    with pytest.raises(RuntimeError):
        rm.prepare_h3_then(submit)
    assert calls == (['prepared'] if failure_stage == 'prepare' else ['prepared', 'submitted'])
    assert not rm.lock.locked()
    assert rm.lock.acquire(blocking=False)
    rm.lock.release()


@pytest.mark.parametrize('previous,target', [('video', 'image'), ('image', 'video'), (None, 'image')])
def test_comfy_family_switch_waits_for_release_under_shared_lock(monkeypatch, previous, target):
    rm = manager()
    rm.comfy_kind = previous
    events = []
    monkeypatch.setattr(rm, '_prepare_h3_locked', lambda: {'ready': True})
    monkeypatch.setattr(rm, 'assert_idle', lambda: [{'url': 'http://127.0.0.1:8010', 'online': True, 'running': 0, 'pending': 0}])
    monkeypatch.setattr(resources.time, 'sleep', lambda seconds: events.append('wait'))
    memory = iter([{'used_mib': 22000}, {'used_mib': 1500}])
    def snapshot():
        assert rm.lock.locked()
        events.append('memory')
        return next(memory)
    def free(url, **kwargs):
        assert rm.lock.locked() and url == 'http://127.0.0.1:8010/free'
        assert kwargs == {'json': {'unload_models': True, 'free_memory': True}, 'timeout': 8, 'trust_env': False}
        events.append('free')
        return SimpleNamespace(raise_for_status=lambda: None)
    def submit():
        assert rm.lock.locked() and rm.comfy_kind == target
        assert events == ['free', 'wait', 'memory', 'wait', 'memory']
        events.append('submit')
        return 'own-prompt'
    monkeypatch.setattr(resources, 'gpu_snapshot', snapshot)
    monkeypatch.setattr(resources.httpx, 'post', free)
    assert rm.prepare_comfy_then(target, submit) == 'own-prompt'
    assert not rm.lock.locked() and events[-1] == 'submit'


@pytest.mark.parametrize('previous,target', [('video', 'video'), ('image', 'image'), ('empty', 'image'), ('empty', 'video'), (None, 'video')])
def test_same_comfy_family_or_verified_empty_stays_warm(monkeypatch, previous, target):
    rm = manager()
    rm.comfy_kind = previous
    monkeypatch.setattr(rm, '_prepare_h3_locked', lambda: {'ready': True})
    assert rm.prepare_comfy_then(target, lambda: 'queued') == 'queued'
    assert rm.comfy_kind == target


def test_busy_queue_prevents_family_release_and_submission(monkeypatch):
    rm = manager()
    rm.comfy_kind = 'image'
    monkeypatch.setattr(rm, '_prepare_h3_locked', lambda: {'ready': True})
    def busy():
        raise resources.ResourceError('ComfyUI has running or queued work.')
    monkeypatch.setattr(rm, 'assert_idle', busy)
    with pytest.raises(resources.ResourceError, match='queued work'):
        rm.prepare_h3_then(denied)
    assert rm.comfy_kind == 'image' and not rm.lock.locked()


def test_unknown_release_memory_never_submits_new_family(monkeypatch):
    rm = manager()
    rm.comfy_kind = 'image'
    monkeypatch.setattr(rm, '_prepare_h3_locked', lambda: {'ready': True})
    monkeypatch.setattr(rm, 'assert_idle', lambda: [{'url': 'http://127.0.0.1:8010', 'online': True}])
    monkeypatch.setattr(resources.httpx, 'post', lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr(resources.time, 'sleep', lambda *a: None)
    ticks = iter([0, 41])
    monkeypatch.setattr(resources.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(resources, 'gpu_snapshot', lambda: None)
    with pytest.raises(resources.ResourceError, match='No new image or video was queued'):
        rm.prepare_h3_then(denied)
    assert rm.comfy_kind == 'image' and not rm.lock.locked()


def test_image_switch_failure_keeps_conservative_family_after_lost_submission(monkeypatch):
    rm = manager()
    rm.comfy_kind = 'empty'
    monkeypatch.setattr(rm, '_prepare_h3_locked', lambda: {'ready': True})
    def lost():
        raise httpx.ReadTimeout('Response lost')
    with pytest.raises(httpx.ReadTimeout):
        rm.prepare_comfy_then('image', lost)
    assert rm.comfy_kind == 'image' and not rm.lock.locked()


def test_invalid_comfy_family_cannot_acquire_lock():
    rm = manager()
    with pytest.raises(ValueError, match='image or video'):
        rm.prepare_comfy_then('arbitrary', denied)
    assert not rm.lock.locked()


def test_comfy_family_is_durable_before_potentially_uncertain_post(tmp_path, monkeypatch):
    path = tmp_path / 'resource_state.json'
    rm = resources.ResourceManager(lambda: {}, denied, state_path=path)
    rm.comfy_kind = 'empty'
    monkeypatch.setattr(rm, '_prepare_h3_locked', lambda: {'ready': True})
    def uncertain_post():
        recovered = resources.ResourceManager(lambda: {}, denied, state_path=path)
        assert recovered.comfy_kind == 'image'
        raise httpx.ReadTimeout('Unknown acceptance')
    with pytest.raises(httpx.ReadTimeout):
        rm.prepare_comfy_then('image', uncertain_post)
    assert resources.ResourceManager(lambda: {}, denied, state_path=path).comfy_kind == 'image'


def test_corrupt_family_state_requires_release_instead_of_assuming_warm_h3(tmp_path, monkeypatch):
    path = tmp_path / 'resource_state.json'
    path.write_text('invalid json', encoding='utf-8')
    rm = resources.ResourceManager(lambda: {}, denied, state_path=path)
    assert rm.comfy_kind == 'unknown'
    monkeypatch.setattr(rm, '_prepare_h3_locked', lambda: {'ready': True})
    def busy():
        raise resources.ResourceError('ComfyUI has running or queued work.')
    monkeypatch.setattr(rm, 'assert_idle', busy)
    with pytest.raises(resources.ResourceError, match='queued work'):
        rm.prepare_h3_then(denied)
