import copy

import pytest

from backend.game_director import direct_plan, validate_narrative
from backend.movement import deterministic_movement, movement_observation
from backend.projects import new_project
from backend.world import project_from_world, validate_world


def scene():
    world = validate_world({'schema_version': 1, 'characters': [
        {'id': 'player', 'name': 'Besi', 'control': 'player', 'description': 'Long black hair, white and orange jacket, blue shirt.'},
        {'id': 'bystander', 'name': 'Person in purple', 'description': 'Purple shirt and glasses.'}]})
    project = project_from_world(new_project(), world)
    project['game_player_id'] = 'player'
    project['shots'][0]['setting'] = 'Cobblestone street with orange shopfronts.'
    return project, world


@pytest.mark.parametrize(('direction', 'axis'), [('forward', 'away from the viewer'), ('backward', 'toward the viewer'),
                                               ('left', 'left side'), ('right', 'right side')])
def test_unmapped_street_moves_are_fully_directed_without_a_model(direction, axis):
    project, world = scene()
    before = copy.deepcopy((project, world))
    plan = deterministic_movement(project, world, 'player', {'kind': 'move', 'direction': direction}, 3)
    assert axis in plan['action'] and 'orange jacket' in plan['action']
    assert plan['asset_requests'] == [] and plan['effects'] == []
    shot = plan['direction']['shots'][0]
    assert shot['camera']['movement'] == 'static'
    assert shot['scene_contract']['actors'][1]['activity'] == 'hold'
    assert 'source frame' in shot['scene_contract']['background_activity']
    validate_narrative(plan, world=world, player_character_id='player', message='I move ' + direction, duration=3, mode='game')
    compiled = direct_plan(plan, project, duration=3)  # no predictor provided or needed
    assert compiled['shots'][0]['camera']['movement'] == 'static'
    assert (project, world) == before


def test_unbound_player_is_not_guessed_from_a_multi_person_frame():
    project, world = scene()
    world['characters'][0]['description'] = ''
    with pytest.raises(ValueError, match='This is me'):
        deterministic_movement(project, world, 'player', {'kind': 'move', 'direction': 'forward'}, 3)


@pytest.mark.parametrize('pov', [False, True])
def test_viewpoint_moves_do_not_animate_an_unidentified_person(pov):
    project, world = scene()
    world['characters'][0]['description'] = ''
    if pov:
        project['game_viewpoint'] = 'pov'
    plan = deterministic_movement(project, world, 'player', {'kind': 'move', 'camera': 'player' if pov else 'camera', 'direction': 'left'}, 3)
    shot = plan['direction']['shots'][0]
    assert shot['camera']['movement'] == 'truck left'
    assert all(actor['activity'] == 'hold' for actor in shot['scene_contract']['actors'])
    if pov:
        assert 'player' in shot['offscreen_subject_ids']


def test_authored_rules_and_camera_locks_do_not_get_bypassed():
    project, world = scene()
    intent = {'kind': 'move', 'direction': 'forward'}
    world['rules'] = ['The player cannot walk until the guard permits it.']
    assert deterministic_movement(project, world, 'player', intent, 3) is None
    world['rules'] = []
    project['shots'][0]['director_locks'] = ['camera.movement']
    assert deterministic_movement(project, world, 'player', intent, 3) is None


def test_selected_visual_anchor_is_used_without_disabling_the_shortcut():
    project, world = scene()
    player = world['characters'][0]
    player['description'] = ''
    player['state'] = {'visual_anchor': 'White and orange jacket. ' * 60, 'visual_anchor_run_id': 'saved-frame'}
    plan = deterministic_movement(project, world, 'player', {'kind': 'move', 'direction': 'right'}, 3)
    assert 'White and orange jacket.' in plan['action']
    direct_plan(plan, project, duration=3)


def test_cached_scene_never_claims_the_new_ending_was_inspected():
    previous = {'observed_state': 'A door on the left.', 'visible_scene': {'setting': 'Street', 'candidates': []}}
    result = movement_observation(previous, {'choices': []}, 'original-frame')
    assert result['inspection_status'] == 'not_run' and result['observed_state'] == ''
    assert 'visible_scene' not in result and result['last_inspected_scene'] == previous['visible_scene']
    assert result['cached_scene_run_id'] == 'original-frame'
    assert movement_observation(result, {'choices': []}, 'next-frame')['cached_scene_run_id'] == 'original-frame'


def test_unregistered_bystanders_get_individual_hold_constraints_without_becoming_world_characters():
    project, world = scene()
    before = copy.deepcopy(world)
    observed = {'visible_scene': {'setting': 'Street', 'candidates': [
        {'kind': 'person', 'known_id': None, 'label': 'orange jacket player',
         'description': world['characters'][0]['description'], 'position': 'center'},
        {'kind': 'person', 'known_id': None, 'label': 'tall person with black hair and grey shirt',
         'description': 'Grey shirt.', 'position': 'mid-left'}]}}
    plan = deterministic_movement(project, world, 'player', {'kind': 'move', 'direction': 'forward'}, 3, observed_state=observed)
    text = plan['direction']['shots'][0]['scene_contract']['background_activity']
    assert 'grey shirt at mid-left' in text and 'feet planted' in text
    assert 'orange jacket player' not in text and world == before
    cached = movement_observation(observed, plan, 'previous')
    plan = deterministic_movement(project, world, 'player', {'kind': 'move', 'direction': 'forward'}, 3, observed_state=cached)
    assert 'mid-left' not in plan['direction']['shots'][0]['scene_contract']['background_activity']
