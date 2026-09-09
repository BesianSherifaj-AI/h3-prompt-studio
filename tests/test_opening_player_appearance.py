"""A new Game establishes its existing player visually in the ordinary writer call."""
import copy
import json

import pytest

from backend.compiler import compile_project
from backend.game_director import _prepare_project, direct_plan, plan_turn, validate_narrative
from backend.projects import new_project
from backend.world import validate_world
from test_game_director import coordinator, direction


APPEARANCE = 'Short silver hair, a rust-red canvas coat with a triangular cream shoulder patch, and dark trousers.'


def opening():
    project = new_project()
    project.update(mode='t2va', game_player_id='player')
    project['subjects'] = [{'id': 'player', 'name': 'Alex', 'description': '', 'asset_ids': []}]
    world = validate_world({'schema_version': 1, 'characters': [
        {'id': 'player', 'name': 'Alex', 'control': 'player'}]})
    return project, world


def writer(appearance=APPEARANCE):
    value = coordinator()
    value['beats'][0].update(action='Alex walks to the shop entrance and stops.',
        setting='A cobblestone shopping street.', final_state='Alex stands beside the shop door.')
    if appearance is not None:
        value['player_appearance'] = appearance
    return value


def planner(project, world, appearance=APPEARANCE, *, allowed=True, premise='', extra_writer=None):
    calls = []
    def predict(stage, actor, system, content, schema):
        context = json.loads(content)
        calls.append((stage, context, schema, system))
        if stage == 'roleplay':
            return {**writer(appearance), **(extra_writer or {})}
        assert stage == 'director', 'Establish identity within the existing writer, without another stage.'
        result = direction()
        scene = result['shots'][0]
        scene.update(dialogue_indices=[], visible_subject_ids=['player'], offscreen_subject_ids=[])
        if project.get('game_viewpoint') == 'pov':
            scene.update(visible_subject_ids=[], offscreen_subject_ids=['player'])
            scene['camera']['framing'] = 'first person'
        return result
    result = plan_turn(project=project, world=world, player_character_id='player',
        message='Begin the scene.', duration=5, predict=predict, premise=premise,
        allow_establish_player_appearance=allowed)
    return result, calls


def test_first_text_scene_establishes_existing_player_before_director_and_compiler():
    project, world = opening()
    before = copy.deepcopy((project, world))
    result, calls = planner(project, world)
    assert [stage for stage, *_ in calls] == ['roleplay', 'director']
    assert 'player_appearance' in calls[0][2]['required']
    assert calls[0][1]['establish_player_appearance']['basis'] == 'new_text_scene'
    assert calls[1][1]['subjects'][0]['description'] == APPEARANCE
    assert result['player_appearance'] == {'character_id': 'player', 'description': APPEARANCE}
    assert result['characters'] == [{'id': 'player', 'name': 'Alex', 'description': APPEARANCE, 'voice': ''}]
    prepared = direct_plan(result, project, duration=5)
    compiled = compile_project(prepared)
    assert compiled['valid'], compiled['issues']
    assert APPEARANCE in compiled['prompt']
    assert (project, world) == before, 'Planning must not commit the new identity to accepted state.'


@pytest.mark.parametrize('invalid', [None, '', 'Alex', 'A generic player character.', 'Player appearance is to be determined later.'])
def test_missing_or_placeholder_identity_fails_before_director(invalid):
    project, world = opening()
    calls = []
    def predict(stage, actor, system, content, schema):
        calls.append(stage)
        return writer(invalid)
    with pytest.raises(ValueError, match='appearance|schema'):
        plan_turn(project=project, world=world, player_character_id='player',
            message='Begin the scene.', duration=5, predict=predict, allow_establish_player_appearance=True)
    assert calls == ['roleplay']


def test_no_automatic_appearance_for_legacy_or_authored_descriptions():
    project, world = opening()
    result, calls = planner(project, world, None, allowed=False)
    assert 'player_appearance' not in result
    assert 'player_appearance' not in calls[0][2]['properties']
    project['subjects'][0]['description'] = world['characters'][0]['description'] = 'Authored robot: purple shell and white wheel hubs.'
    result, calls = planner(project, world, None)
    assert calls[0][1]['establish_player_appearance'] is None
    assert result['characters'][0]['description'] == world['characters'][0]['description']


def test_bound_photo_supplies_evidence_without_regenerating_or_detaching_identity():
    project, world = opening()
    photo = {'id': 'player-photo', 'name': 'My player reference', 'media_type': 'image',
             'role': 'reference_image', 'semantic_role': 'face', 'prompt_tag': 'player-face',
             'approved_observation': 'Short silver hair and a rust-red coat with a triangular cream patch.'}
    project.update(mode='ref2va', assets=[photo])
    project['subjects'][0]['asset_ids'] = world['characters'][0]['asset_ids'] = ['player-photo']
    result, calls = planner(project, world)
    evidence = calls[0][1]['establish_player_appearance']
    assert evidence['basis'] == 'bound_identity_reference'
    assert evidence['bound_reference_ids'] == ['player-photo']
    assert evidence['reference_observations'][0]['description'] == photo['approved_observation']
    assert result['asset_requests'] == []
    prepared = direct_plan(result, project, duration=5)
    assert prepared['assets'] == [photo]
    assert prepared['subjects'][0]['asset_ids'] == ['player-photo']
    assert compile_project(prepared)['valid']


@pytest.mark.parametrize('role,ending', [('reference_image', False), ('first_frame', False), ('reference_image', True)])
def test_unbound_or_existing_scene_images_cannot_be_relabelled_as_new_avatar(role, ending):
    project, world = opening()
    project['assets'] = [{'id': 'scene', 'name': 'Existing street', 'media_type': 'image',
        'role': role, 'semantic_role': 'background', 'video_run_ending': ending}]
    project['mode'] = 'i2va' if role == 'first_frame' else 'ref2va'
    result, calls = planner(project, world, None)
    assert calls[0][1]['establish_player_appearance'] is None
    assert 'player_appearance' not in result


def test_nonhuman_form_and_pov_features_reach_direction_without_human_avatar_defaults():
    project, world = opening()
    project['game_viewpoint'] = 'pov'
    appearance = 'Copper-plated forelimbs with turquoise joint rings and three blunt articulated claws on each limb.'
    premise = 'The player is a small clockwork beetle viewed through its own eyes.'
    result, calls = planner(project, world, appearance, premise=premise)
    assert calls[0][1]['story_premise'] == premise
    assert calls[1][1]['viewpoint'] == 'pov'
    assert calls[1][1]['subjects'][0]['description'] == appearance
    assert result['characters'][0]['description'] == appearance
    prepared = direct_plan(result, project, duration=5)
    assert prepared['shots'][0]['visible_subject_ids'] == []
    assert prepared['shots'][0]['offscreen_subject_ids'] == ['player']
    assert compile_project(prepared)['valid']


def test_text_player_cannot_repeat_new_npc_appearance_exactly():
    project, world = opening()
    with pytest.raises(ValueError, match='distinguishing details'):
        planner(project, world, extra_writer={'new_characters': [
            {'id': 'other', 'name': 'Robin', 'description': APPEARANCE, 'voice': ''}]})


def test_external_metadata_cannot_target_another_person_or_overwrite_authored_appearance():
    project, world = opening()
    result, _ = planner(project, world)
    wrong = copy.deepcopy(result)
    wrong['player_appearance']['character_id'] = 'somebody-else'
    with pytest.raises(ValueError, match='selected player'):
        validate_narrative(wrong, world=world, player_character_id='player', message='Begin the scene.', duration=5)
    project['subjects'][0]['description'] = 'An authored tortoise with a green shell.'
    with pytest.raises(ValueError, match='already described'):
        _prepare_project(result, project, 5)
    world['characters'][0]['description'] = project['subjects'][0]['description']
    with pytest.raises(ValueError, match='established player appearance'):
        validate_narrative(result, world=world, player_character_id='player', message='Begin the scene.', duration=5)
