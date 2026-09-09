"""First-frame character design belongs to its accepted take, not the draft."""
import copy
import json

import pytest

from backend.compiler import compile_project
from backend.world import validate_world
from test_game_director import coordinator, direction, setup_scene
from test_stories import observed_for_schema, reference, rig, uid


APPEARANCE = 'Short black hair, a white jacket over a blue shirt, and black trousers.'


def opening(rig, *, description='', photo=False, review=False, imported=False):
    project, _ = setup_scene()
    project['subjects'] = project['subjects'][:1]
    project['subjects'][0]['description'] = description
    project['comfy_render'] = {'seed': 42, 'resolution': '0.2', 'steps': 8}
    if photo:
        asset = reference('Selected player portrait', 'character', 'player-portrait')
        project['assets'] = [asset]
        project['subjects'][0]['asset_ids'] = [asset['id']]
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Alex',
        'control': 'player', 'description': description, 'asset_ids': project['subjects'][0]['asset_ids']}]})
    source = rig.videos.add(project) if imported else None
    session = rig.manager.create({'request_id': uid(), 'project': project, 'mode': 'game',
        'player_name': 'Alex', 'player_character_id': 'player', 'world': world,
        **({'source_run_id': source['id']} if source else {}),
        'premise': 'A quiet cobblestone city street in pixel art.',
        'settings': {'duration': 3, 'resolution': '0.2', 'review_before_render': review}})
    return rig.manager._story(session['id'])


def install_planner(rig, *, mismatch=False):
    calls = []
    def complete(model, system, content, schema, **options):
        context = json.loads(content[0]['text'])
        if 'new_characters' in schema['properties']:
            calls.append(('roleplay', context, schema))
            result = coordinator()
            result['transition'] = 'cut'
            result['beats'][0].update(action='Alex looks toward the shop door.', setting='A cobblestone city street.', final_state='Alex stands beside the shopfront.')
            if 'player_appearance' in schema['properties']:
                result['player_appearance'] = APPEARANCE
        elif 'shots' in schema['properties']:
            calls.append(('director', context, schema))
            result = direction(3)
            result['shots'][0].update(visible_subject_ids=['player'], dialogue_indices=[],
                performance='Alex keeps still beside the shopfront.', sound='Quiet street ambience.')
        else:
            calls.append(('ending', context, schema))
            result = observed_for_schema({'observed_state': 'Alex stands beside the shopfront.',
                'uncertainties': 'Only the final frame is available.', 'choices': coordinator()['choices']}, schema)
            if mismatch:
                for check in result.get('continuity_checks', []):
                    check.update(status='mismatch', detail='The requested jacket color differs.')
        return {'result': result, 'diagnostics': {}}
    rig.client.complete_json_result = complete
    return calls


def submit_opening(rig, story):
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I look toward the shop door.'})
    rig.manager.process(story['id'], public['id'])
    return rig.manager._turn(story, public['id'])


@pytest.mark.parametrize('photo', [False, True])
def test_first_plan_fixes_appearance_before_render_and_acceptance_unlocks_assistant_free_arrows(rig, photo):
    story = opening(rig, photo=photo)
    before = copy.deepcopy(rig.manager._state(story))
    calls = install_planner(rig)
    turn = submit_opening(rig, story)
    assert turn['status'] == 'succeeded', turn.get('error')
    assert [stage for stage, _, _ in calls] == ['roleplay', 'director', 'ending']
    assert 'player_appearance' in calls[0][2]['required']
    assert APPEARANCE in json.dumps(calls[1][1]) and APPEARANCE in json.dumps(calls[2][1])
    assert turn['snapshot']['world'] == before['world']
    assert turn['project']['subjects'][0]['description'] == APPEARANCE
    assert APPEARANCE in compile_project(turn['project'])['prompt']
    state = rig.manager._state(story)
    assert state['world']['characters'][0]['description'] == APPEARANCE
    assert state['project']['subjects'][0]['description'] == APPEARANCE
    assert state['world']['characters'][0]['state']['visual_anchor'] == APPEARANCE
    assert state['player_visual_anchor']['source'] == 'initial_render_description'
    assert state['player_visual_anchor']['run_id'] == turn['run_id']
    assert state['world']['characters'][0]['asset_ids'] == before['world']['characters'][0]['asset_ids']
    def forbidden(*args, **kwargs):
        raise AssertionError('Accepted appearance must allow a plain arrow without the assistant.')
    rig.resources.run_ai = forbidden
    rig.client.complete_json_result = forbidden
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I walk forward.',
        'intent': {'kind': 'move', 'direction': 'forward', 'extent': 'step', 'camera': 'player'}})
    rig.manager.process(story['id'], public['id'])
    move = rig.manager._turn(story, public['id'])
    assert move['status'] == 'succeeded', move.get('error')
    assert move['planning_mode'] == 'deterministic_movement'
    assert move['project']['subjects'][0]['description'] == APPEARANCE
    assert not rig.assets.requests


@pytest.mark.parametrize('outcome', ['failed_render', 'mismatch', 'review'])
def test_unaccepted_appearance_never_becomes_the_current_player(rig, outcome):
    story = opening(rig, review=outcome == 'review')
    before = copy.deepcopy(rig.manager._state(story))
    install_planner(rig, mismatch=outcome == 'mismatch')
    if outcome == 'failed_render':
        rig.videos.next_status = 'failed'
    turn = submit_opening(rig, story)
    assert turn['status'] == {'failed_render': 'failed', 'mismatch': 'awaiting_acceptance', 'review': 'awaiting_review'}[outcome], turn.get('error')
    assert rig.manager._state(story) == before
    assert story['active_run_id'] is None and story['state_by_run'] == {}
    assert turn['plan']['player_appearance']['description'] == APPEARANCE
    if outcome == 'review':
        rig.manager.action(story['id'], turn['id'], 'approve', {'request_id': uid()})
        rig.manager.process(story['id'], turn['id'])
        assert turn['status'] == 'awaiting_acceptance', turn.get('error')
        assert rig.manager._state(story) == before
        rig.manager.action(story['id'], turn['id'], 'accept-intended', {'request_id': uid()})
        rig.manager.process(story['id'], turn['id'])
        assert turn['status'] == 'succeeded', turn.get('error')
        assert rig.manager._state(story)['world']['characters'][0]['description'] == APPEARANCE


@pytest.mark.parametrize('case', ['authored_description', 'imported_frame', 'unbound_image'])
def test_existing_authored_or_unbound_image_identity_is_not_automatically_reassigned(rig, case):
    authored = 'A silver robot with square violet eyes.' if case == 'authored_description' else ''
    story = opening(rig, description=authored, imported=case == 'imported_frame')
    if case == 'unbound_image':
        rig.manager._state(story)['project']['assets'] = [reference('Two people in a street', 'background', 'street')]
    calls = install_planner(rig)
    turn = submit_opening(rig, story)
    assert turn['status'] == 'succeeded', turn.get('error')
    assert 'player_appearance' not in calls[0][2]['properties']
    assert 'player_appearance' not in turn['plan']
    assert rig.manager._state(story)['world']['characters'][0]['description'] == authored
    assert 'player_visual_anchor' not in rig.manager._state(story)


def test_first_appearance_review_edit_updates_hidden_metadata_without_changing_player_id(rig):
    story = opening(rig, review=True)
    install_planner(rig)
    turn = submit_opening(rig, story)
    assert turn['status'] == 'awaiting_review', turn.get('error')
    revised = copy.deepcopy(turn['plan'])
    revised['characters'][0]['description'] = 'A silver robot with square violet eyes and a blue scarf.'
    edited = rig.manager._edited_plan(story, turn, revised)
    assert edited['player_appearance'] == {'character_id': 'player', 'description': revised['characters'][0]['description']}
    assert rig.manager._state(story)['world']['characters'][0]['description'] == ''


def test_concurrent_authored_appearance_does_not_inherit_a_different_render_anchor(rig):
    story = opening(rig)
    install_planner(rig, mismatch=True)
    turn = submit_opening(rig, story)
    assert turn['status'] == 'awaiting_acceptance', turn.get('error')
    world = copy.deepcopy(rig.manager._state(story)['world'])
    world['characters'][0]['description'] = 'A silver robot with square violet eyes.'
    rig.manager.edit_state(story, {'world': world})
    rig.manager.action(story['id'], turn['id'], 'accept-intended', {'request_id': uid()})
    rig.manager.process(story['id'], turn['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    assert turn['accepted_state']['world']['characters'][0]['description'] == APPEARANCE
    current = rig.manager._state(story)['world']['characters'][0]
    assert current['description'] == world['characters'][0]['description']
    assert 'visual_anchor' not in current['state'] and 'player_visual_anchor' not in rig.manager._state(story)


def test_assigned_player_portrait_with_background_and_prop_still_establishes_identity(rig):
    story = opening(rig, photo=True)
    state = rig.manager._state(story)
    portrait_id = state['project']['assets'][0]['id']
    background = reference('Cobblestone shopfront', 'background', 'shopfront')
    prop = reference('Brass lamp', 'object', 'lamp')
    state['project']['assets'].extend([background, prop])
    before_assets = copy.deepcopy(state['project']['assets'])
    calls = install_planner(rig)
    turn = submit_opening(rig, story)
    assert turn['status'] == 'succeeded', turn.get('error')
    request = calls[0][1]['establish_player_appearance']
    assert request['basis'] == 'bound_identity_reference'
    assert request['bound_reference_ids'] == [portrait_id]
    assert 'player_appearance' in calls[0][2]['required']
    assert rig.manager._state(story)['world']['characters'][0]['description'] == APPEARANCE
    assert rig.manager._state(story)['player_visual_anchor']['description'] == APPEARANCE
    assert turn['project']['subjects'][0]['asset_ids'] == [portrait_id]
    assert {asset['id'] for asset in turn['project']['assets']} == {asset['id'] for asset in before_assets}
    assert not rig.assets.requests


def test_authored_player_description_allows_arrow_when_scan_leaves_people_unidentified(rig):
    from test_visible_scene_grounding import visible_scene
    story = opening(rig, description=APPEARANCE, imported=True)
    source = story['active_run_id']
    story['observed_by_run'][source] = {'visible_scene': visible_scene()}
    def forbidden(*args, **kwargs):
        raise AssertionError('An explicitly described player does not need a new assistant identity decision.')
    rig.resources.run_ai = forbidden
    rig.client.complete_json_result = forbidden
    public = rig.manager.submit(story['id'], {'request_id': uid(), 'message': 'I walk forward.',
        'intent': {'kind': 'move', 'direction': 'forward', 'extent': 'step', 'camera': 'player'}})
    rig.manager.process(story['id'], public['id'])
    turn = rig.manager._turn(story, public['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    assert turn['planning_mode'] == 'deterministic_movement'
    assert turn['project']['subjects'][0]['description'] == APPEARANCE
    assert all(row['known_id'] is None for row in story['observed_by_run'][source]['visible_scene']['candidates'])
    assert rig.manager._state(story).get('scene_target_bindings', {}) == {}
