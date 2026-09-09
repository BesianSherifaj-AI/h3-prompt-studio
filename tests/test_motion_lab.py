import copy
import uuid

import pytest

from backend.motion_lab import MotionLabError, MotionLabManager, prepare_experiment, recipes
from backend.projects import new_project
from backend.video_timing import frame_budget
from backend.comfy_transfer import _settings, TransferError


def project():
    p = new_project()
    p.update(mode='t2va', duration=3)
    p['shots'][0].update(duration=3, action='A pixel-art explorer stands on a stone road.')
    p['comfy_render'] = {'experimental_preview': True, 'resolution': '0.2', 'steps': 4}
    return p


def test_experimental_preview_uses_native_grid_and_explicit_opt_in():
    p = project()
    config = _settings(p, p['comfy_render'])
    assert (config['width'], config['height'], config['frames']) == (608, 320, 73)
    assert config['experimental_preview'] is True
    assert frame_budget(3, {'experimental_preview': True, 'duration_basis': 'new_footage', 'continuation_source': 'source'})['frames'] == 124
    with pytest.raises(TransferError):
        _settings(p, {'resolution': '0.2'})
    p['duration'] = 5
    with pytest.raises(TransferError):
        _settings(p, {'resolution': '0.2'})


def test_motion_experiments_pair_seeds_keep_original_and_compile_instructions():
    p = project()
    before = copy.deepcopy(p)
    ident = str(uuid.uuid4())
    rows = prepare_experiment(ident, p, {}, ['slow', 'fast'], [7, 8])
    assert p == before
    assert [r['seed'] for r in rows] == [7, 7, 8, 8]
    assert len({r['request_id'] for r in rows}) == 4
    assert rows == prepare_experiment(ident, p, {}, ['slow', 'fast'], [7, 8])
    assert 'walks slowly forward' in rows[0]['prompt']
    assert 'runs forward quickly' in rows[1]['prompt']
    assert rows[0]['prompt_sha256'] != rows[1]['prompt_sha256']
    assert len(recipes()) >= 8


class Video:
    def __init__(self):
        self.runs, self.submissions = {}, []
        self.lose_response = False
    def get(self, ident):
        if ident not in self.runs: raise ValueError('missing')
        return self.runs[ident]
    def submit(self, ident, project, prompt):
        self.submissions.append(ident)
        self.runs[ident] = {'id': ident, 'status': 'running', 'elapsed_seconds': .1}
        if self.lose_response: raise RuntimeError('accepted but caller response lost')
        return self.runs[ident]


def test_refresh_and_lost_response_do_not_duplicate_or_advance(tmp_path):
    video = Video()
    manager = MotionLabManager(tmp_path, lambda: video)
    ident = str(uuid.uuid4())
    experiment = manager.create(ident, project(), {}, ['slow', 'fast'], [5])
    assert not video.submissions
    manager.get(ident)
    assert not video.submissions
    video.lose_response = True
    with pytest.raises(RuntimeError): manager.advance(ident)
    restored = MotionLabManager(tmp_path, lambda: video)
    restored.advance(ident)
    assert len(video.submissions) == 1
    first = experiment['items'][0]['request_id']
    video.runs[first]['status'] = 'succeeded'
    video.lose_response = False
    result = restored.advance(ident)
    assert len(video.submissions) == 2
    assert result['items'][0]['status'] == 'succeeded'
    restored.advance(ident)
    assert len(video.submissions) == 2


def test_paused_comparison_and_failed_run_require_explicit_action(tmp_path):
    video = Video()
    manager = MotionLabManager(tmp_path, lambda: video)
    ident = str(uuid.uuid4())
    manager.create(ident, project(), {}, ['still', 'medium'], [1])
    manager.set_paused(ident, True)
    with pytest.raises(MotionLabError): manager.advance(ident)
    manager.set_paused(ident, False)
    current = manager.advance(ident)
    rid = current['items'][0]['request_id']
    video.runs[rid]['status'] = 'failed'
    manager.advance(ident)
    assert len(video.submissions) == 1
    rated = manager.rate(ident, rid, {'direction': 'uncertain'}, 'No completed video.')
    assert rated['items'][0]['ratings']['direction'] == 'uncertain'


@pytest.mark.parametrize('ids,seeds', [(['unknown'], [1]), (['slow'], [True]), (['slow', 'slow'], [1]), (['slow'], [1, 1]), (['slow', 'fast'], list(range(7))), ([{}], [1]), (['slow'], [{}])])
def test_bounded_comparison_inputs(ids, seeds):
    with pytest.raises(MotionLabError):
        prepare_experiment(str(uuid.uuid4()), project(), {}, ids, seeds)
