"""Scene staging retains actual participant IDs and never replays a prior shot."""
import copy
import hashlib
import json

import pytest

from backend.game_director import _prepare_project, direct_plan, plan_turn
from backend.compiler import compile_project
from backend.world import validate_world
from test_game_director import coordinator, direction, narrative, setup_scene


def contract(action='Mira remains seated on the bench.'):
    return {'actors': [{'subject_id': 'npc', 'activity': 'hold', 'start': 'seated on the bench',
                        'action': action, 'end': 'seated on the bench'}],
            'objects': [], 'environment': 'Workshop, blue bench.', 'background_activity': ''}


def test_game_participant_ids_complete_visible_roster_and_hold_uncalled_bystander():
    project, world = setup_scene()
    project['subjects'].append({'id': 'ben', 'name': 'Ben', 'description': 'Red coat', 'asset_ids': []})
    world['characters'].append({'id': 'ben', 'name': 'Ben', 'location_id': 'room', 'state': {'posture': 'seated'}})
    world = validate_world(world)
    calls = []
    def predict(stage, actor, system, content, schema):
        calls.append((stage, actor, json.loads(content)))
        if stage == 'actor':
            return {'character_id': 'npc', 'action': 'Mira points toward the window.',
                    'dialogue': [{'text': 'Në punishten time.', 'language': 'Albanian', 'delivery': 'soft'}],
                    'intent': 'Answer Alex.'}
        if stage == 'roleplay':
            return coordinator()
        result = direction()
        result['shots'][0]['visible_subject_ids'].append('ben')
        result['shots'][0]['scene_contract'] = {'actors': [{'subject_id': 'ben', 'activity': 'act',
            'start': 'seated on the bench', 'action': 'Ben gets up and waves.', 'end': 'standing'}],
            'objects': [], 'environment': '', 'background_activity': ''}
        return result
    result = plan_turn(project=project, world=world, player_character_id='player',
        message='I ask Mira "Ku jemi?"', duration=5, predict=predict)
    assert [(stage, actor) for stage, actor, _ in calls] == [('actor', 'npc'), ('roleplay', 'world'), ('director', 'director')]
    assert {row['subject_id'] for row in result['actor_actions']} == {'player', 'npc'}
    rows = {row['subject_id']: row for row in result['direction']['shots'][0]['scene_contract']['actors']}
    assert set(rows) == {'player', 'npc', 'ben'}
    assert rows['ben']['activity'] == 'hold' and rows['ben']['start'] == rows['ben']['end'] == 'seated on the bench'
    assert 'gets up' not in rows['ben']['action']
    assert "Mira's physical action assigned in this beat" in rows['npc']['action']
    assert result['actor_actions'][1]['action'] == 'Mira points toward the window.'
    assert result['dialogue'][0]['text'] == 'Ku jemi?'


def test_authored_studio_contract_survives_director_without_muting_approved_speech():
    project, _ = setup_scene()
    source = project['shots'][0]
    source.update(scene_contract=contract(), visible_subject_ids=['player', 'npc'])
    value = narrative()
    captured = []
    def predict(stage, actor, system, content, schema):
        captured.append(json.loads(content))
        return direction()
    prepared = direct_plan(value, project, duration=5, predict=predict, game_mode=False)
    assert captured[0]['approved_actor_actions'] is None
    assert prepared['shots'][0]['scene_contract'] == contract()
    assert [line['text'] for line in prepared['shots'][0]['dialogue']] == [line['text'] for line in value['dialogue']]
    assert 'scene_contract' not in value


def test_generated_contract_is_refreshed_even_when_camera_is_locked():
    project, _ = setup_scene()
    project['shots'][0].update(scene_contract=contract('Obsolete completed gesture.'),
        scene_contract_source='generated', director_locks=['camera.movement'])
    prepared = _prepare_project(narrative(), project, 5)
    assert 'scene_contract' not in prepared['shots'][0]
    assert prepared['shots'][0]['director_locks'] == ['camera.movement']
    assert 'scene_contract' in project['shots'][0]


@pytest.mark.parametrize('edited', [False, True])
def test_consumed_authored_contract_is_not_replayed_but_new_draft_edits_survive(edited):
    project, _ = setup_scene()
    scene = project['shots'][0]
    scene.update(scene_contract=contract('Mira raises the coin.'), scene_contract_source='authored',
                 director_locks=['camera.movement', 'scene_contract'])
    project['rendered_scene_contracts'] = {scene['id']: hashlib.sha256(
        json.dumps(scene['scene_contract'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    if edited:
        scene['scene_contract']['actors'][0]['action'] = 'Mira gives the coin to Alex.'
    prepared = _prepare_project(narrative(), project, 5)
    if edited:
        assert prepared['shots'][0]['scene_contract']['actors'][0]['action'] == 'Mira gives the coin to Alex.'
    else:
        assert 'scene_contract' not in prepared['shots'][0]
        assert prepared['shots'][0]['director_locks'] == ['camera.movement']


def test_current_explicit_direction_overrides_completed_authored_contract():
    project, _ = setup_scene()
    scene = project['shots'][0]
    scene.update(scene_contract=contract('Mira raises the coin.'), scene_contract_source='authored')
    project['rendered_scene_contracts'] = {scene['id']: hashlib.sha256(
        json.dumps(scene['scene_contract'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    value = narrative()
    value['direction'] = direction()
    value['direction']['shots'][0]['scene_contract'] = contract('Mira gives the coin to Alex.')
    prepared = direct_plan(value, project, duration=5)
    assert prepared['shots'][0]['scene_contract']['actors'][0]['action'] == 'Mira gives the coin to Alex.'
    assert 'raises the coin' not in json.dumps(prepared)


def test_offscreen_actor_cannot_receive_physical_contract_row():
    project, _ = setup_scene()
    value = narrative()
    value['direction'] = direction()
    value['direction']['shots'][0].update(visible_subject_ids=['player'], offscreen_subject_ids=['npc'], scene_contract=contract())
    with pytest.raises(ValueError, match='visible character|off.screen'):
        direct_plan(value, project, duration=5)


@pytest.mark.parametrize('beat_count', [1, 2])
def test_actor_staging_never_repeats_global_speech_or_actions_across_beats(beat_count):
    project, _ = setup_scene()
    project['mode'] = 't2va'
    value = narrative()
    value['action'] = 'Alex looks toward Mira while Mira points at the window.'
    value['beats'][0]['action'] = value['action']
    value['actor_actions'] = [
        {'subject_id': 'player', 'activity': 'act', 'action': 'I ask Mira "Ku jemi?"'},
        {'subject_id': 'npc', 'activity': 'act', 'action': 'Mira points at the window, then turns toward the bench.'}]
    if beat_count == 2:
        value['beats'].append({'id': 'beat-2', 'action': 'Mira turns toward the bench while Alex listens.',
                              'setting': value['setting'], 'final_state': 'Mira faces the bench.'})
        # An authored two-beat timeline keeps its number, timing and camera lock.
        project['shots'][0].update(duration=2, director_locks=['camera.movement'])
        project['shots'].append({**copy.deepcopy(project['shots'][0]), 'id': 'second-shot', 'duration': 3})
    calls = []
    def predict(stage, actor, system, content, schema):
        calls.append(stage)
        assert 'physical props, fixtures or garments' in system
        assert 'whole rooms' in system
        result = direction(duration=2 if beat_count == 2 else 5)
        scene = result['shots'][0]
        scene['scene_contract'] = {'actors': [
            {'subject_id': 'player', 'activity': 'act', 'start': 'standing left',
             'action': 'I ask Mira "Ku jemi?"', 'end': 'standing left'},
            {'subject_id': 'npc', 'activity': 'act', 'start': 'seated on the bench',
             'action': value['actor_actions'][1]['action'], 'end': 'seated on the bench'}],
             'objects': [], 'environment': '', 'background_activity': ''}
        if beat_count == 2:
            second = copy.deepcopy(scene)
            second.update(beat_id='beat-2', duration=3, dialogue_indices=[])
            second['scene_contract']['actors'][1].update(start='seated facing the window', end='seated facing the bench')
            result['shots'].append(second)
        return result
    prepared = direct_plan(value, project, duration=5, predict=predict)
    compiled = compile_project(prepared)
    assert compiled['valid'], compiled['issues']
    assert calls == ['director'] and len(prepared['shots']) == beat_count
    assert compiled['prompt'].count('Ku jemi?') == compiled['prompt'].count('Në punishten time.') == 1
    for scene in prepared['shots']:
        for row in scene['scene_contract']['actors']:
            assert 'Ku jemi?' not in row['action'] and 'then turns' not in row['action']
            assert 'in this beat' in row['action'] and 'scheduled in this shot' in row['action']
    if beat_count == 2:
        assert [scene['duration'] for scene in prepared['shots']] == [2, 3]
        assert prepared['shots'][1]['dialogue'] == []
        assert prepared['shots'][1]['scene_contract']['actors'][1]['end'] == 'seated facing the bench'
        assert prepared['shots'][1]['action'] == value['beats'][1]['action']
