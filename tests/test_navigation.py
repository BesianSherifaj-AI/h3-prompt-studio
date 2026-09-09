"""Saved image anchors must never roll back gameplay or cross sibling branches."""
import copy

import pytest

from backend.navigation import MAX_VIEWS, commit_navigation, navigation_return, prepare_navigation
from backend.projects import new_project
from backend.world import apply_effects, project_from_world, resolve_intent, validate_world


def state():
    world = validate_world({'schema_version': 1, 'current_location_id': 'street',
        'locations': [{'id': 'street', 'name': 'Street', 'exits': ['hall']},
                      {'id': 'hall', 'name': 'Hall', 'exits': ['street']}],
        'characters': [{'id': 'player', 'name': 'Alex', 'description': 'Adult in an orange coat.',
                        'control': 'player', 'location_id': 'street'},
                       {'id': 'enemy', 'name': 'Clockwork guard', 'location_id': 'street',
                        'state': {'health': 1, 'defeated': False}}],
        'entities': [{'id': 'coin', 'name': 'Coin', 'location_id': 'street'}]})
    return {'world': world, 'project': project_from_world(new_project(), world),
            'player_character_id': 'player', 'settings': {'style': 'pixel art'},
            'configuration_revision': 1}


def session():
    initial = state()
    return {'active_branch_id': 'main', 'active_run_id': 'origin',
            'branches': {'main': ['origin']}, 'state_by_run': {'origin': copy.deepcopy(initial)},
            'branch_states': {'main': initial}}


def prepare(story, direction='forward', **intent):
    snapshot = copy.deepcopy(story['branch_states'][story['active_branch_id']])
    move = {'kind': 'move', 'direction': direction, 'extent': 'step',
            'camera': 'player', 'presentation': 'continuous', **intent}
    if move.get('target_id'):
        move.pop('direction', None)
    turn_id = 'turn-' + str(len(story['branches'][story['active_branch_id']]))
    turn = {'id': turn_id, 'logical_turn_id': turn_id, 'branch_id': story['active_branch_id'],
            'parent_run_id': story['active_run_id'], 'snapshot': snapshot, 'intent': move,
            'resolved_intent': resolve_intent(snapshot['world'], 'player', move),
            'plan': {'effects': []}, 'project': copy.deepcopy(snapshot['project'])}
    turn['plan']['effects'] = copy.deepcopy(turn['resolved_intent']['effects'])
    prepare_navigation(story, turn, snapshot)
    return turn


def accept(story, turn, run_id):
    before = turn['snapshot']
    accepted = copy.deepcopy(before)
    accepted['world'] = apply_effects(before['world'], turn['plan']['effects'],
                                      event_id=turn['id'], actor_id='player', witness_ids=['player'])
    accepted['project'] = copy.deepcopy(turn['project'])
    commit_navigation(before, turn, run_id, accepted)
    story['branch_states'][turn['branch_id']] = accepted
    story['state_by_run'][run_id] = copy.deepcopy(accepted)
    story['branches'][turn['branch_id']].append(run_id)
    story['active_run_id'] = run_id
    return accepted


def test_forward_back_uses_original_accepted_image_without_restoring_world():
    story = session()
    before = copy.deepcopy(story)
    forward = prepare(story)
    assert story == before, 'Preparation cannot commit or mutate the frozen input.'
    assert navigation_return(story, forward) is None
    accepted = accept(story, forward, 'forward')
    assert accepted['navigation']['position'] == [0, 1]
    back = prepare(story, 'backward')
    saved = copy.deepcopy(story)
    assert navigation_return(story, back) == 'origin'
    assert story == saved, 'An image lookup never rewinds the accepted event history.'
    final = accept(story, back, 'back')
    assert final['navigation']['position'] == [0, 0]
    assert [event['id'] for event in final['world']['events']] == [forward['id'], back['id']]


@pytest.mark.parametrize('direction,opposite,extent,expected', [
    ('right', 'left', 'step', [1, 0]), ('left', 'right', 'nearby', [-3, 0]),
    ('forward', 'backward', 'nearby', [0, 3]), ('backward', 'forward', 'step', [0, -1])])
def test_direction_extent_and_inverse(direction, opposite, extent, expected):
    story = session()
    turn = prepare(story, direction, extent=extent)
    assert accept(story, turn, 'moved')['navigation']['position'] == expected
    assert navigation_return(story, prepare(story, opposite, extent=extent)) == 'origin'


@pytest.mark.parametrize('effect', [
    {'kind': 'holder', 'entity_id': 'coin', 'character_id': 'player'},
    {'kind': 'character_state', 'character_id': 'enemy', 'key': 'health', 'value': 0},
    {'kind': 'character_state', 'character_id': 'enemy', 'key': 'defeated', 'value': True},
    {'kind': 'entity_state', 'entity_id': 'coin', 'key': 'broken', 'value': True}])
def test_changed_inventory_enemy_or_prop_state_prevents_stale_ending(effect):
    story = session()
    turn = prepare(story)
    turn['plan']['effects'] = [effect]
    accept(story, turn, 'changed')
    back = prepare(story, 'backward')
    assert navigation_return(story, back) is None
    assert 'different world state' in back['navigation_move']['reason']
    assert story['branch_states']['main']['world']['events'][-1]['effects'] == [effect]


def test_pending_effects_and_discoveries_also_prevent_stale_ending():
    story = session()
    accept(story, prepare(story), 'forward')
    back = prepare(story, 'backward')
    back['plan']['effects'] = [{'kind': 'holder', 'entity_id': 'coin', 'character_id': 'player'}]
    assert navigation_return(story, back) is None
    back['plan']['effects'] = []
    back['plan']['discoveries'] = {'entities': [{'id': 'new-prop', 'name': 'A new prop',
                                               'kind': 'object', 'location_id': 'street'}]}
    assert navigation_return(story, back) is None


@pytest.mark.parametrize('change', ['style', 'viewpoint', 'aspect', 'reference', 'explicit_frame', 'appearance'])
def test_changed_presentation_or_identity_prevents_ending_reuse(change):
    story = session()
    accept(story, prepare(story), 'forward')
    back = prepare(story, 'backward')
    project = back['project']
    if change == 'style':
        project['style']['visual_style'] = 'Photorealistic'
    elif change == 'viewpoint':
        project['game_viewpoint'] = 'pov'
    elif change == 'aspect':
        project['aspect_ratio'] = '9:16'
    elif change == 'appearance':
        project['subjects'][0]['description'] = 'Adult in a purple suit.'
    else:
        project['assets'].append({'id': 'new-ref', 'name': 'New design', 'media_type': 'image',
                                  'role': 'first_frame' if change == 'explicit_frame' else 'reference_image'})
    assert navigation_return(story, back) is None


def test_reference_from_latest_ending_is_not_a_new_design_constraint():
    story = session()
    accept(story, prepare(story), 'forward')
    back = prepare(story, 'backward')
    back['project']['assets'].append({'id': 'ending-image', 'name': 'Latest ending',
                                     'media_type': 'image', 'role': 'first_frame', 'video_run_ending': True})
    assert navigation_return(story, back) == 'origin'


def test_materializing_unchanged_game_style_preserves_return_but_new_setting_does_not():
    story = session()
    forward = prepare(story)
    # This is the same style assignment used by actual project assembly.
    forward['project']['style']['notes'] = forward['snapshot']['settings']['style']
    accept(story, forward, 'forward')
    back = prepare(story, 'backward')
    assert navigation_return(story, back) == 'origin'
    back['snapshot']['settings']['style'] = 'Photorealistic'
    back['project']['style']['notes'] = 'Photorealistic'
    assert navigation_return(story, back) is None


def test_legacy_origin_uses_actual_saved_state_not_edited_inventory():
    story = session()
    story['branch_states']['main']['world']['entities'][0]['holder_id'] = 'player'
    accept(story, prepare(story), 'forward')
    assert navigation_return(story, prepare(story, 'backward')) is None


def test_snapshot_freezes_navigation_and_later_draft_changes():
    story = session()
    accept(story, prepare(story), 'forward')
    back = prepare(story, 'backward')
    story['branch_states']['main']['navigation']['views'].clear()
    story['branch_states']['main']['project']['style']['notes'] = 'New draft'
    assert navigation_return(story, back) == 'origin'


def test_branch_fork_can_reuse_ancestors_but_not_sibling_future():
    story = session()
    accept(story, prepare(story), 'forward')
    accepted = accept(story, prepare(story), 'forward-two')
    # The fork restores the state at its actual endpoint, including its nav history.
    story['branches']['fork'] = ['origin', 'forward']
    story['branch_states']['fork'] = copy.deepcopy(story['state_by_run']['forward'])
    story.update(active_branch_id='fork', active_run_id='forward')
    assert navigation_return(story, prepare(story, 'backward')) == 'origin'
    # Even corrupt cross-branch cached rows cannot authorize a sibling endpoint.
    story['branch_states']['fork']['navigation']['views'].append(accepted['navigation']['views'][-1])
    forward = prepare(story)
    assert navigation_return(story, forward) is None


def test_newly_accepted_endpoint_invalidates_pending_return():
    story = session()
    accept(story, prepare(story), 'forward')
    back = prepare(story, 'backward')
    story['branches']['main'].append('different-new-ending')
    assert navigation_return(story, back) is None
    assert 'after this movement' in back['navigation_move']['reason']


def test_reroll_replaces_anchor_without_applying_movement_twice():
    story = session()
    turn = prepare(story)
    original = accept(story, turn, 'first-take')
    replacement = copy.deepcopy(original)
    turn['reroll_of'] = 'first-take'
    commit_navigation(turn['snapshot'], turn, 'second-take', replacement)
    assert replacement['navigation']['position'] == [0, 1]
    assert [row['run_id'] for row in replacement['navigation']['views']] == ['origin', 'second-take']
    assert replacement['world'] == original['world']
    again = copy.deepcopy(replacement)
    commit_navigation(turn['snapshot'], turn, 'second-take', replacement)
    assert replacement == again


def test_known_location_return_targets_saved_place_without_creating_places():
    story = session()
    travel = prepare(story, target_id='hall', extent='travel')
    assert travel['navigation_move']['kind'] == 'location'
    assert navigation_return(story, travel) is None
    accept(story, travel, 'hall-ending')
    back = prepare(story, kind='return', target_id='street', extent='travel')
    assert navigation_return(story, back) == 'origin'
    accepted = accept(story, back, 'street-return')
    assert accepted['navigation']['position'] == [0, 0]
    assert {row['id'] for row in accepted['world']['locations']} == {'street', 'hall'}
    assert accepted['world']['current_location_id'] == 'street'


@pytest.mark.parametrize('intent', [{'camera': 'camera'}, {'presentation': 'cut'}])
def test_unsupported_local_motion_breaks_relative_frame(intent):
    story = session()
    accept(story, prepare(story), 'forward')
    turn = prepare(story, **intent)
    assert turn['navigation_move']['kind'] == 'reset'
    assert navigation_return(story, turn) is None
    accept(story, turn, 'new-frame')
    assert navigation_return(story, prepare(story, 'backward')) is None


def test_wait_records_current_endpoint_but_does_not_move():
    story = session()
    accept(story, prepare(story), 'forward')
    wait = prepare(story, kind='wait')
    accepted = accept(story, wait, 'wait-ending')
    assert accepted['navigation']['position'] == [0, 1]
    assert navigation_return(story, prepare(story, 'backward')) == 'origin'


def test_anchor_storage_is_bounded():
    story = session()
    for index in range(MAX_VIEWS + 3):
        accept(story, prepare(story), f'ending-{index}')
    views = story['branch_states']['main']['navigation']['views']
    assert len(views) == MAX_VIEWS
    assert views[-1]['run_id'] == f'ending-{MAX_VIEWS + 2}'
    assert all(row['run_id'] != 'origin' for row in views)


def test_malformed_cached_coordinates_do_not_become_a_return_anchor():
    story = session()
    accept(story, prepare(story), 'forward')
    story['branch_states']['main']['navigation']['position'] = [True, 'bad']
    back = prepare(story, 'backward')
    assert navigation_return(story, back) is None
    assert back['navigation_move']['from_position'] == [0, 0]
