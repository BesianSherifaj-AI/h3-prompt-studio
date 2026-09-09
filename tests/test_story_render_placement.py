"""Canonical inventory reaches the actual H3 prompt without replaying stale placement."""
import copy

import pytest

from backend.compiler import compile_project
from backend.stories import _render_placements, _stage_render_placements, story_author_instructions
from backend.world import validate_effects, validate_world
from test_stories import plan, reference, rig, story, uid


def prepared_turn(rig, *, action='Nora answers Mira.', effects=(), two_shots=False, object_image=False, newcomer=False):
    session = story(rig)
    record = copy.deepcopy(rig.manager._story(session['id']))
    source = copy.deepcopy(rig.project)
    mira, nora = [character['id'] for character in source['subjects']]
    entities = [
        {'id': 'coin', 'name': 'Gold Coin', 'kind': 'object', 'holder_id': mira, 'owner_id': mira,
         'location_id': 'street', 'description': 'A small gold coin lying on the cobblestones. Its face bears an engraved bird.',
         'state': {'color': 'gold', 'material': 'metal'}},
        {'id': 'scarf', 'name': 'Red Scarf', 'kind': 'wardrobe', 'worn_by_id': mira, 'location_id': 'street',
         'description': 'A red wool scarf resting on the workbench.'},
        {'id': 'secret', 'name': 'Hidden Ring', 'kind': 'object', 'holder_id': mira,
         'location_id': 'street', 'state': {'hidden': True}},
    ]
    if object_image:
        image = reference('Gold Coin', 'object', 'coin-image')
        image.update(description='A small gold coin lying on the cobblestones.',
                     approved_observation='The coin is lying on the pavement. Its face bears an engraved bird.')
        source['assets'].append(image)
        entities[0]['asset_ids'] = [image['id']]
    record['narrative_version'] = 2
    record['world'] = validate_world({'schema_version': 1, 'current_location_id': 'street',
        'locations': [{'id': 'street', 'name': 'City Street', 'exits': [{'target_id': 'shop', 'label': 'Enter shop'}]},
                      {'id': 'shop', 'name': 'Shop'}],
        'characters': [{**copy.deepcopy(character), 'control': 'player' if character['id'] == mira else 'npc',
                        'location_id': 'street'} for character in source['subjects']], 'entities': entities})
    resolved = [{**effect, **({'character_id': mira if effect['character_id'] == 'mira' else nora}
                             if effect.get('character_id') in ('mira', 'nora') else {})} for effect in effects]
    authored = plan(action=action, final_state='The approved action is complete.', dialogue=[], effects=resolved)
    if newcomer:
        authored['characters'].append({'id': 'new-elena', 'name': 'Elena', 'description': 'A woman in a green coat.', 'voice': 'quiet'})
    authored['beats'] = [{'id': 'beat-1', 'action': action, 'setting': 'City Street', 'final_state': authored['final_state']}]
    shot = {'beat_id': 'beat-1', 'duration': 5,
        'camera': {'framing': 'medium', 'movement': 'static', 'height': 'eye level', 'speed': 'still', 'focus': 'Mira and Nora'},
        'performance': action, 'sound': 'Quiet street ambience.', 'visible_subject_ids': [mira, nora],
        'offscreen_subject_ids': [], 'dialogue_indices': [], 'transition': 'continuous'}
    authored['direction'] = {'shots': [shot]}
    if newcomer:
        shot['visible_subject_ids'].append('new-elena')
    if two_shots:
        authored['beats'].append({**authored['beats'][0], 'id': 'beat-2'})
        shot['duration'] = 3
        authored['direction']['shots'].append({**copy.deepcopy(shot), 'beat_id': 'beat-2', 'duration': 2})
    turn = {'id': uid(), 'render_request_id': uid(), 'plan': authored, 'duration': 5, 'parent_run_id': None}
    original = copy.deepcopy((record, turn, source))
    result = rig.manager._project(record, turn, source, None)
    assert (record, turn, source) == original
    compiled = compile_project(result)
    assert compiled['valid'], compiled['issues']
    return result, compiled['prompt']


@pytest.mark.parametrize('action', ['Nora answers Mira.', 'Mira examines the shop door.'])
@pytest.mark.parametrize('object_image', [False, True])
def test_talking_and_inspecting_keep_held_coin_and_worn_scarf_in_compiled_shot(rig, action, object_image):
    result, prompt = prepared_turn(rig, action=action, object_image=object_image)
    performance = result['shots'][0]['performance']
    assert 'Gold Coin starts held by Mira in City Street.' in performance
    assert 'Gold Coin remains held by Mira throughout this shot' in performance
    assert 'Red Scarf remains worn by Mira throughout this shot' in performance
    assert 'Gold Coin ends held by Mira in City Street.' in result['shots'][0]['final_state']
    assert 'lying on the cobblestones' not in prompt
    assert 'lying on the pavement' not in prompt
    assert 'resting on the workbench' not in prompt
    assert 'small gold coin' in prompt and 'engraved bird' in prompt
    assert 'Hidden Ring' not in prompt


def test_give_changes_final_holder_without_forcing_old_possession_throughout(rig):
    result, prompt = prepared_turn(rig, action='Mira gives the Gold Coin to Nora.',
        effects=[{'kind': 'holder', 'entity_id': 'coin', 'character_id': 'nora'}])
    assert 'Gold Coin starts held by Mira in City Street.' in result['shots'][0]['performance']
    assert 'Gold Coin ends held by Nora in City Street.' in result['shots'][0]['final_state']
    assert 'Gold Coin remains held by Mira' not in prompt
    assert 'Gold Coin ends held by Mira' not in prompt
    assert 'Red Scarf remains worn by Mira' in prompt


def test_drop_removes_final_holder_without_inventing_a_new_pickup_or_support_surface(rig):
    result, prompt = prepared_turn(rig, action='Mira drops the Gold Coin onto the shop counter.', effects=[
        {'kind': 'holder', 'entity_id': 'coin', 'character_id': None},
        {'kind': 'entity_location', 'entity_id': 'coin', 'location_id': 'shop'}])
    assert 'Gold Coin starts held by Mira in City Street.' in result['shots'][0]['performance']
    assert 'Gold Coin ends held by nobody and worn by nobody in Shop.' in result['shots'][0]['final_state']
    assert 'onto the shop counter' in prompt
    assert 'Gold Coin remains held' not in prompt
    assert 'lying on the cobblestones' not in prompt


def test_movement_carries_held_and_worn_items_to_the_characters_final_location(rig):
    result, prompt = prepared_turn(rig, action='Mira walks through the door into the shop.', effects=[
        {'kind': 'character_location', 'character_id': 'mira', 'location_id': 'shop'}])
    assert 'Gold Coin remains held by Mira throughout this shot' in result['shots'][0]['performance']
    assert 'Gold Coin ends held by Mira in Shop.' in result['shots'][0]['final_state']
    assert 'Red Scarf ends worn by Mira in Shop.' in prompt
    assert 'Gold Coin ends held by Mira in City Street' not in prompt


def test_multishot_transfer_only_states_old_holder_at_start_and_new_holder_at_end(rig):
    result, _ = prepared_turn(rig, action='Mira gives the Gold Coin to Nora.', two_shots=True,
        effects=[{'kind': 'holder', 'entity_id': 'coin', 'character_id': 'nora'}])
    first, last = result['shots']
    assert 'Gold Coin starts held by Mira' in first['performance']
    assert 'Gold Coin starts held by Mira' not in last['performance']
    assert 'Gold Coin ends held by Nora' not in first['final_state']
    assert 'Gold Coin ends held by Nora' in last['final_state']


def test_generated_placement_context_is_removed_before_reusing_project_as_author_instructions(rig):
    result, _ = prepared_turn(rig)
    clean = story_author_instructions('Keep the camera low.\n' + result['custom_instructions'])
    assert 'Keep the camera low.' in clean
    assert 'Canonical object placement' not in clean
    assert 'Starting visible state' not in clean
    assert 'Gold Coin' not in clean


def test_plain_newcomer_keeps_existing_inventory_without_needing_a_new_world_actor_first(rig):
    result, prompt = prepared_turn(rig, action='Elena introduces herself to Mira.', newcomer=True)
    assert any(character['id'] == 'new-elena' for character in result['subjects'])
    assert 'Gold Coin remains held by Mira' in prompt


def test_newcomer_assignment_matches_existing_world_validator_boundary():
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Mira'}],
                            'entities': [{'id': 'coin', 'name': 'Coin', 'holder_id': 'player'}]})
    effects = [{'kind': 'holder', 'entity_id': 'coin', 'character_id': 'new-elena'}]
    for operation in (lambda: validate_effects(world, effects), lambda: _render_placements(world, effects, {'player'})):
        with pytest.raises(ValueError, match='unknown character'):
            operation()


def test_preview_identifier_cannot_collide_with_a_non_event_world_record(monkeypatch):
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Mira'}],
                            'entities': [{'id': 'coin', 'name': 'Coin', 'holder_id': 'player'}]})
    identifiers = iter(['coin', 'player', 'safe-preview-id'])
    monkeypatch.setattr('backend.stories.ident', lambda: next(identifiers))
    assert _render_placements(world, [], {'player'})[0]['start'] == 'held by Mira'


def test_large_inventory_additions_are_bounded_and_prioritize_changed_or_named_props():
    world = validate_world({'schema_version': 1, 'characters': [{'id': 'player', 'name': 'Mira'}, {'id': 'npc', 'name': 'Nora'}],
        'entities': [{'id': 'item-' + str(i), 'name': 'Prop ' + str(i), 'holder_id': 'player'} for i in range(200)]})
    effects = [{'kind': 'holder', 'entity_id': 'item-199', 'character_id': 'npc'}]
    rows = _render_placements(world, effects, {'player', 'npc'}, 'Mira inspects Prop 198.')
    project = {'custom_instructions': '', 'shots': [{'performance': 'x' * 2400, 'final_state': 'y' * 2400,
                                                    'visible_subject_ids': ['player', 'npc'], 'offscreen_subject_ids': []}]}
    _stage_render_placements(project, rows)
    assert len(project['shots'][0]['performance']) < 6000
    assert len(project['shots'][0]['final_state']) < 6000
    assert len(project['custom_instructions']) < 6000
    assert 'Prop 199 ends held by Nora' in project['shots'][0]['final_state']
    assert 'Prop 198 remains held by Mira' in project['shots'][0]['performance']
