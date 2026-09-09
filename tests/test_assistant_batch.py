import threading
import time
from types import SimpleNamespace

import pytest

from backend.resources import ResourceError, ResourceManager


def manager(parallel):
    models = SimpleNamespace(loaded_instances=lambda: [{'id': 'owned', 'model': 'selected', 'config': {'parallel': parallel}}])
    rm = ResourceManager(lambda: {}, lambda: models)
    rm._prepare_ai = lambda model: {'instance_id': 'owned', 'ready': True}
    return rm


def test_batch_runs_independent_calls_under_one_model_lease():
    rm = manager(4); barrier = threading.Barrier(2); seen = []; mutex = threading.Lock()
    def operation(index):
        def run(ident, stop):
            assert ident == 'owned' and rm.lock.locked()
            with pytest.raises(ResourceError, match='still working'):
                rm.prepare_h3()
            barrier.wait(2)
            with mutex: seen.append(index)
            return index
        return run
    assert rm.run_ai_batch('selected', [operation(1), operation(2)], concurrency=2) == [1, 2]
    assert set(seen) == {1, 2} and not rm.lock.locked()
    assert rm.last_batch['actual_concurrency'] == 2


@pytest.mark.parametrize('reported,requested,expected', [(4, 1, 1), (2, 4, 2), (None, 4, 1), (True, 4, 1)])
def test_batch_uses_verified_capacity_with_safe_default(reported, requested, expected):
    rm = manager(reported)
    assert rm.run_ai_batch('selected', [lambda ident, stop: 1] * 4, concurrency=requested) == [1] * 4
    assert rm.last_batch['actual_concurrency'] == expected


def test_failure_signals_other_calls_and_waits_for_drain_before_unlock():
    rm = manager(2); other_started = threading.Event(); drained = threading.Event()
    def first(ident, stop):
        assert other_started.wait(2)
        raise ValueError('An actor response is invalid')
    def second(ident, stop):
        other_started.set()
        assert stop.wait(2)
        assert rm.lock.locked()
        drained.set()
        return 'discard me'
    with pytest.raises(ValueError, match='invalid'):
        rm.run_ai_batch('selected', [first, second], concurrency=2)
    assert drained.is_set() and not rm.lock.locked()


def test_cancelled_batch_does_not_prepare_model():
    rm = manager(4); stop = threading.Event(); stop.set()
    rm._prepare_ai = lambda model: pytest.fail('No model preparation after cancellation')
    with pytest.raises(ResourceError, match='cancelled before'):
        rm.run_ai_batch('selected', [lambda ident, stop: None], cancel_event=stop)
    assert not rm.lock.locked()
