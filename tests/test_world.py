import copy
import pytest

from backend.projects import new_project
from backend.world import (WorldError, actor_context, apply_effects, available_actions,
                           project_from_world, resolve_intent, trim_context, validate_world,
                           world_from_project)


def sample_world():
    return validate_world({'schema_version': 1, 'current_location_id': 'room',
        'locations': [{'id': 'room', 'name': 'Room', 'exits': [{'target_id': 'hall', 'direction': 'north'}]},
                      {'id': 'hall', 'name': 'Hall', 'exits': ['room']}],
        'characters': [{'id': 'player', 'name': 'Alex', 'control': 'player'},
                       {'id': 'npc', 'name': 'Mira', 'private_knowledge': ['The safe code is 71.']}],
        'entities': [{'id': 'key', 'name': 'Brass key', 'kind': 'object', 'owner_id': 'npc',
                      'affordances': ['take', 'give', 'drop']},
                     {'id': 'box', 'name': 'Box', 'affordances': ['open', 'close'], 'state': {'locked': False}}],
        'objectives': [{'id': 'quest', 'title': 'Find the note', 'status': 'active'}]})


def test_blank_world_and_project_are_not_a_prefilled_story():
    project = new_project()
    world = world_from_project(project)
    assert world['characters'] == world['locations'] == world['entities'] == []
    assert world['current_location_id'] is None
    assert project_from_world(project, world) == project


def test_import_ids_stable_owner_is_not_holder_and_history_retained():
    p = new_project()
    p['subjects'] = [{'id': 'player', 'name': 'Alex', 'description': '', 'asset_ids': ['coat']}]
    p['assets'] = [{'id': 'key-photo', 'name': 'Key', 'media_type': 'image', 'semantic_role': 'object', 'simple_owner_id': 'player'},
                   {'id': 'coat', 'name': 'Coat', 'media_type': 'image', 'semantic_role': 'wardrobe'},
                   {'id': 'room-photo', 'name': 'Room', 'media_type': 'image', 'semantic_role': 'background'}]
    w = world_from_project(p, player_character_id='player')
    assert w == world_from_project(p, w, 'player')
    key, coat = w['entities']
    assert key['owner_id'] == 'player' and key['holder_id'] is None
    assert coat['worn_by_id'] == 'player'
    assert w['characters'][0]['location_id'] == w['current_location_id']
    accepted = apply_effects(w, [], event_id='accepted', actor_id='player', summary='The door rang.')
    assert world_from_project(p, accepted, 'player')['events'] == accepted['events']
    p['comfy_render'] = {'loras': [{'name': 'x', 'strength': .7}], 'seed': 42}
    synced = project_from_world(p, w)
    assert synced['comfy_render'] == p['comfy_render']
    assert 'current_holder_id' in synced['assets'][0]
    assert 'current_holder_id' not in synced['assets'][1]


def test_take_give_drop_apply_only_on_acceptance_and_only_once():
    w = sample_world()
    proposal = resolve_intent(w, 'player', {'kind': 'take', 'target_id': 'key'})
    assert w['entities'][0]['holder_id'] is None
    accepted = apply_effects(w, proposal['effects'], event_id='take-1', actor_id='player', summary='Alex took the key.', witness_ids=['player', 'npc'])
    assert accepted['entities'][0]['holder_id'] == 'player'
    assert accepted['entities'][0]['owner_id'] == 'npc'
    assert apply_effects(accepted, proposal['effects'], event_id='take-1', actor_id='player') == accepted
    with pytest.raises(WorldError, match='different effects'):
        apply_effects(accepted, [], event_id='take-1', actor_id='player')
    give = resolve_intent(accepted, 'player', {'kind': 'give', 'target_id': 'key', 'recipient_id': 'npc'})
    final = apply_effects(accepted, give['effects'], event_id='give-1', actor_id='player')
    assert final['entities'][0]['holder_id'] == 'npc'
    with pytest.raises(WorldError, match='holding'):
        resolve_intent(final, 'player', {'kind': 'drop', 'target_id': 'key'})


def test_open_locked_and_unknown_targets_fail_before_render():
    w = sample_world()
    w['entities'][1]['state']['locked'] = True
    with pytest.raises(WorldError, match='locked'):
        resolve_intent(w, 'player', {'kind': 'open', 'target_id': 'box'})
    with pytest.raises(WorldError, match='not available'):
        resolve_intent(w, 'player', {'kind': 'take', 'target_id': 'invented'})
    with pytest.raises(WorldError, match='unknown object'):
        apply_effects(w, [{'kind': 'holder', 'entity_id': 'invented', 'character_id': 'player'}], event_id='oops')


def test_grounded_move_step_camera_and_return_have_different_effects():
    w = sample_world()
    step = resolve_intent(w, 'player', {'kind': 'move', 'target_id': 'hall', 'extent': 'step', 'speed': 'fast'})
    assert step['effects'] == [] and 'one step quickly' in step['action']
    camera = resolve_intent(w, 'player', {'kind': 'move', 'target_id': 'hall', 'camera': 'camera'})
    assert camera['effects'] == [] and 'camera' in camera['action']
    move = resolve_intent(w, 'player', {'kind': 'move', 'target_id': 'hall'})
    next_world = apply_effects(w, move['effects'], event_id='walk', actor_id='player')
    assert next_world['current_location_id'] == 'hall'
    back = resolve_intent(next_world, 'player', {'kind': 'return', 'target_id': 'room'})
    assert back['effects'][0]['location_id'] == 'room'
    assert 'npc' not in {x['id'] for x in available_actions(next_world, 'player')['targets']}


def test_actor_does_not_read_other_secret_or_unwitnessed_future():
    w = sample_world()
    w = apply_effects(w, [{'kind': 'knowledge', 'character_id': 'npc', 'fact': 'Hidden clue.'}],
                      event_id='private', actor_id='npc', summary='Mira found a clue.', witness_ids=['npc'])
    player_context = actor_context(w, 'player')
    assert 'safe code' not in str(player_context) and 'Hidden clue' not in str(player_context)
    assert player_context['recent_events'] == []
    npc_context = actor_context(w, 'npc')
    assert 'safe code' in str(npc_context)
    # An older branch only includes the events saved at its actual endpoint.
    assert actor_context(sample_world(), 'npc')['recent_events'] == []


def test_public_event_redacts_another_actors_knowledge_effect():
    w = apply_effects(sample_world(), [{'kind': 'knowledge', 'character_id': 'npc', 'fact': 'The hidden token.'}],
                      event_id='shared', actor_id='npc', summary='Mira inspected the box.', witness_ids=['player', 'npc'])
    assert actor_context(w, 'player')['recent_events'][0]['effects'] == []
    assert actor_context(w, 'npc')['recent_events'][0]['effects'][0]['fact'] == 'The hidden token.'


def test_friendly_text_fields_normalize_and_stale_refs_reject():
    w = sample_world()
    w['characters'][0].update(goals='Find the road', private_knowledge='I am lost', relationships='Mira is a friend')
    clean = validate_world(w)
    assert clean['characters'][0]['goals'] == ['Find the road']
    assert clean['characters'][0]['relationships']['_notes'] == 'Mira is a friend'
    w['characters'][0]['witnessed_events'] = ['abandoned-future']
    with pytest.raises(WorldError, match='missing from this branch'):
        validate_world(w)


def test_budget_trims_history_not_required_speech_and_never_mutates():
    context = {'exact_speech': 'Do not drop these words.', 'rules': ['Do not change my identity.'], 'recent_events': ['x' * 400] * 3}
    original = copy.deepcopy(context)
    trimmed, report = trim_context(context, len, 180)
    assert context == original and trimmed['exact_speech'] == original['exact_speech']
    assert report['removed']['recent_events'] == 3
    with pytest.raises(WorldError, match='do not fit'):
        trim_context(context, len, 10)


def test_empty_browser_selectors_are_null_but_invalid_types_are_not():
    w = sample_world()
    w['current_location_id'] = ''
    w['characters'][0]['location_id'] = ''
    w['entities'][0].update(location_id='', holder_id='', worn_by_id='', owner_id='')
    clean = validate_world(w)
    assert clean['current_location_id'] is None and clean['entities'][0]['holder_id'] is None
    w['entities'][0]['owner_id'] = False
    with pytest.raises(WorldError, match='identifier'):
        validate_world(w)


def test_private_fact_from_an_abandoned_future_cannot_enter_branch_context():
    w = sample_world()
    w['characters'][1]['private_knowledge'] = [{'fact': 'The player revealed the code.', 'source_event_id': 'future'}]
    with pytest.raises(WorldError, match='outside this branch'):
        actor_context(w, 'npc')


def test_accepted_dialogue_is_known_only_to_recorded_witnesses():
    w = apply_effects(sample_world(), [], event_id='question', actor_id='player', summary='Alex asked about the room.',
                      dialogue=[{'speaker_id': 'player', 'text': 'Where are we?', 'language': 'English'}],
                      witness_ids=['player', 'npc'])
    assert actor_context(w, 'npc')['recent_events'][0]['dialogue'][0]['text'] == 'Where are we?'


def test_local_directional_steps_and_camera_moves_do_not_change_location():
    for camera in ('camera', 'player'):
        w = sample_world()
        result = resolve_intent(w, 'player', {'kind': 'move', 'direction': 'left', 'extent': 'step', 'camera': camera, 'speed': 'fast'})
        assert result['effects'] == [] and 'left' in result['action'] and 'quickly' in result['action']
    with pytest.raises(WorldError, match='destination for travel'):
        resolve_intent(sample_world(), 'player', {'kind': 'move', 'extent': 'travel'})
