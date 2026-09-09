"""No GPU or live app calls: authored plans, durable API receipts, and media QA."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from scripts import render_story_examples as examples


RUN_KEY = 'af54869e-a599-4bd2-a79f-30a976b7c814'


@pytest.mark.parametrize(('kind', 'count', 'steps'), [('chase', 3, 4), ('parody', 6, 8)])
def test_authored_scripts_have_exact_duration_pixel_budget_and_no_image_generation(kind, count, steps):
    example = examples.authored_example(kind, 'test', RUN_KEY)
    assert len(example['plans']) == count
    assert example['target_seconds'] == count * 10
    assert example['create_body']['settings']['resolution'] == '0.2'
    assert example['create_body']['settings']['steps'] == steps
    assert example['create_body']['project']['assets'] == []
    assert len(example['create_body']['project']['subjects']) == 2
    assert example['create_body']['mode'] == 'studio'
    for index, plan in enumerate(example['plans']):
        assert plan['transition'] == ('continue' if index else 'cut')
        assert plan['asset_requests'] == plan['dialogue'] == []
        assert sum(shot['duration'] for shot in plan['direction']['shots']) == 10
    assert 'not autonomous LLM' in example['classification']
    assert example == examples.authored_example(kind, 'test', RUN_KEY)


@pytest.mark.parametrize('value', ['https://remote.example:8768', 'http://127.0.0.1:8765',
                                  'http://127.0.0.1:8768/settings', 'http://user:pass@localhost:8768',
                                  'http://localhost:8768?redirect=remote', 'https://localhost:8768'])
def test_endpoint_cannot_target_remote_or_default_user_app(value):
    with pytest.raises(ValueError):
        examples.endpoint_url(value)


def test_default_command_only_prepares_files_and_never_initializes_network(tmp_path, monkeypatch):
    monkeypatch.setattr(examples, 'LocalAPI', lambda *_: pytest.fail('Preparation must not contact an app.'))
    output = tmp_path / 'fresh'
    assert examples.main(['--example', 'chase', '--name', 'Unit test', '--output', str(output)]) == 0
    state = json.loads((output / 'run.json').read_text(encoding='utf-8'))
    assert state['status'] == 'prepared' and not state['operations']
    with pytest.raises(FileExistsError):
        examples.prepare(output, example='chase', name='Unit test', endpoint='http://127.0.0.1:8768')
    _, resumed = examples.prepare(output, example='chase', name='Unit test', endpoint='http://127.0.0.1:8768', resume=True)
    assert resumed['run_key'] == state['run_key']
    with pytest.raises(ValueError, match='exact original'):
        examples.prepare(output, example='parody', name='Unit test', endpoint='http://127.0.0.1:8768', resume=True)


class FakeAPI:
    def __init__(self, directory):
        self.directory = directory
        self.posts, self.gets, self.runs = [], [], {}
        self.story = None

    def post(self, path, body):
        durable = json.loads((self.directory / 'run.json').read_text(encoding='utf-8'))
        record = next(row for row in durable['operations'].values() if row['request_id'] == body['request_id'])
        assert record['status'] == 'dispatched' and record['body'] == body
        self.posts.append((path, copy.deepcopy(body)))
        if path == '/api/stories':
            self.story = {'id': str(uuid.uuid4()), 'create_request_id': body['request_id'],
                          'active_run_id': None, 'settings': copy.deepcopy(body['settings']), 'turns': []}
            return copy.deepcopy(self.story)
        run_id = str(uuid.uuid4())
        turn = {'id': body['request_id'], 'request_id': body['request_id'], 'status': 'succeeded',
                'parent_run_id': self.story['active_run_id'], 'run_id': run_id}
        self.runs[run_id] = {'id': run_id, 'parent_run_id': self.story['active_run_id'], 'status': 'succeeded',
                             'width': 608, 'height': 320, 'continuation_source': run_id + '.mmh3'}
        self.story['turns'].append(turn)
        self.story['active_run_id'] = run_id
        return copy.deepcopy(turn)

    def get(self, path):
        self.gets.append(path)
        if path == '/api/stories':
            return {'stories': [copy.deepcopy(self.story)] if self.story else []}
        if path.startswith('/api/stories/'):
            return copy.deepcopy(self.story)
        return copy.deepcopy(self.runs[path.rsplit('/', 1)[-1]])


def prepared(tmp_path, kind='chase'):
    directory, state = examples.prepare(tmp_path / 'run', example=kind, name='Unit test', endpoint='http://127.0.0.1:8768')
    api = FakeAPI(directory)
    return examples.Runner(directory, state, api), state['examples'][0]


def test_request_ids_are_durable_and_completed_resume_never_posts_again(tmp_path):
    runner, example = prepared(tmp_path)
    result = runner.render(example)
    assert len(runner.api.posts) == 4
    assert result['clips'][1]['parent_run_id'] == result['clips'][0]['run_id']
    assert result['clips'][2]['parent_run_id'] == result['clips'][1]['run_id']
    assert {path for path, _ in runner.api.posts} == {'/api/stories', '/api/stories/' + runner.api.story['id'] + '/turns'}
    runner.render(example)
    assert len(runner.api.posts) == 4


def test_uncertain_post_is_reconciled_by_get_without_resubmitting(tmp_path):
    runner, example = prepared(tmp_path)
    original_post = runner.api.post
    def accepted_but_connection_lost(path, body):
        original_post(path, body)
        raise TimeoutError('Response lost after acceptance.')
    runner.api.post = accepted_but_connection_lost
    body = example['create_body']
    with pytest.raises(examples.NeedsAttention, match='no automatic resubmit'):
        runner.post_once('create', '/api/stories', body, runner.recover_story)
    recovered = runner.post_once('create', '/api/stories', body, runner.recover_story)
    assert recovered['id'] == runner.api.story['id']
    assert len(runner.api.posts) == 1
    assert runner.state['operations']['create']['recovered_by_get']


def test_uncertain_request_without_receipt_stops_without_post(tmp_path):
    runner, example = prepared(tmp_path)
    attempts = []
    def failed(path, body):
        attempts.append(body['request_id'])
        raise TimeoutError('The server outcome is unknown.')
    runner.api.post = failed
    with pytest.raises(examples.NeedsAttention):
        runner.post_once('create', '/api/stories', example['create_body'], runner.recover_story)
    with pytest.raises(examples.NeedsAttention, match='No request was repeated'):
        runner.post_once('create', '/api/stories', example['create_body'], runner.recover_story)
    assert len(attempts) == 1


@pytest.mark.parametrize('status', ['failed', 'uncertain', 'awaiting_review', 'awaiting_assistant',
                                  'awaiting_acceptance', 'inspection_failed', 'cancelled'])
def test_review_and_failure_statuses_never_get_approved_or_retried(tmp_path, status):
    runner, _ = prepared(tmp_path)
    runner.api.story = {'id': 'story', 'turns': [{'id': 'turn', 'status': status}]}
    with pytest.raises(examples.NeedsAttention, match=status):
        runner.wait_turn('story', 'turn')
    assert runner.api.posts == []


def test_polling_timeout_keeps_the_existing_job_and_never_cancels_or_retries(tmp_path):
    runner, _ = prepared(tmp_path)
    runner.api.story = {'id': 'story', 'turns': [{'id': 'turn', 'status': 'rendering'}]}
    now = [0.0]
    runner.clock = lambda: now[0]
    runner.sleep = lambda delay: now.__setitem__(0, now[0] + delay)
    runner.timeout_seconds = 3
    with pytest.raises(examples.NeedsAttention, match='may still be running'):
        runner.wait_turn('story', 'turn')
    assert now[0] == 3 and runner.api.posts == []


def test_changed_story_ending_is_detected_before_the_next_post(tmp_path):
    runner, example = prepared(tmp_path)
    original_get = runner.api.get
    def changed_get(path):
        result = original_get(path)
        if path.startswith('/api/stories/') and len(result['turns']) == 1:
            result['active_run_id'] = 'different-ending'
        return result
    runner.api.get = changed_get
    with pytest.raises(examples.NeedsAttention, match='changed before submission'):
        runner.render(example)
    assert len(runner.api.posts) == 2  # Creation and first scene only.


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='CPU media utilities unavailable')
def test_cpu_media_verification_keeps_sources_and_assembles_exact_thirty_seconds(tmp_path):
    runner, example = prepared(tmp_path)
    result = runner.render(example)  # Fake API only; no actual app or GPU.
    synthetic = tmp_path / 'synthetic-test-fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24:d=10.25',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=10.25',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(synthetic)], check=True, timeout=30)
    runner.api.download = lambda _, destination: shutil.copyfile(synthetic, destination)
    examples.verify_and_assemble(runner, example, result)
    assert result['status'] == 'media_verified'
    assert result['probe']['frames'] == 720
    assert abs(result['probe']['duration'] - 30) <= .05
    assert all(Path(clip['generated']['path']).exists() for clip in result['clips'])
    assert all(Path(clip['new-footage']['path']).exists() for clip in result['clips'])
    assert len(list((runner.directory / 'chase').glob('*exact-10s.mp4'))) == 3
    assert 'Manual viewing required' in result['visual_quality']
