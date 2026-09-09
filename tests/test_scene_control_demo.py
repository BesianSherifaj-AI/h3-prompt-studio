import json
import uuid

from backend.compiler import compile_project
from backend.game_director import direct_plan
from scripts.render_scene_control_demo import demonstration, main


def test_demo_preserves_ids_counts_and_seated_inspector_across_three_scenes():
    example = demonstration(str(uuid.uuid4()))
    p = example['create_body']['project']
    assert example['target_seconds'] == 30
    assert example['create_body']['settings']['resolution'] == '0.2'
    assert p['assets'] == [] and p['mode'] == 't2va'
    coin_ids = []
    ivo = next(s['id'] for s in p['subjects'] if s['name'] == 'Ivo')
    for plan in example['plans']:
        project = direct_plan(plan, p, duration=10)
        compiled = compile_project(project)
        assert compiled['valid'], compiled['issues']
        contract = project['shots'][0]['scene_contract']
        actor = next(row for row in contract['actors'] if row['subject_id'] == ivo)
        assert actor['activity'] == 'hold' and 'seated' in actor['start'] and 'seated' in actor['end']
        coin = next(row for row in contract['objects'] if row['name'] == 'Brass coin')
        assert coin['count'] == 1
        coin_ids.append(coin['entity_id'])
        assert plan['dialogue'] == []
    assert len(set(coin_ids)) == 1
    assert 'Mira’s hands are empty' in example['plans'][1]['direction']['shots'][0]['scene_contract']['objects'][0]['end']
    assert 'neither character holds a coin' in example['plans'][2]['direction']['shots'][0]['scene_contract']['objects'][0]['end']


def test_default_preparation_is_offline_and_never_connects(tmp_path, monkeypatch):
    import scripts.render_scene_control_demo as demo
    monkeypatch.setattr(demo, 'LocalAPI', lambda *_: (_ for _ in ()).throw(AssertionError('No network during preparation')))
    directory = tmp_path / 'new-demo'
    assert main(['--output', str(directory)]) == 0
    state = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
    assert state['status'] == 'prepared'
    assert state['operations'] == {} and state['results'] == {}
    assert len(state['examples'][0]['plans']) == 3
