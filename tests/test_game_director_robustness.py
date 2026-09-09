"""Game-turn boundary regressions: roles, language, cast size and user actions."""
import copy
import json

import pytest

from backend.game_director import (
    _active_npcs, _concrete, _effect_schema, direct_plan, plan_turn, quoted_speech,
)
from backend.projects import new_project
from backend.world import apply_discoveries, apply_effects, validate_world


def scene(names=('Mira',)):
    project = new_project()
    project['custom_instructions'] = 'Mira is a suspicious mechanic who wants to repair the lift.'
    characters = [{'id': 'player', 'name': 'Alex', 'control': 'player', 'speaking_style': 'English'}]
    characters.extend({'id': f'npc-{index}', 'name': name, 'control': 'npc'} for index, name in enumerate(names))
    world = validate_world({'schema_version': 1, 'current_location_id': 'room',
                            'locations': [{'id': 'room', 'name': 'Workshop'}], 'characters': characters})
    project['subjects'] = [{'id': c['id'], 'name': c['name'], 'description': '', 'asset_ids': []} for c in characters]
    return project, world


def predictor(requests, *, action='Mira nods.', secret='A private plan to trick the player.'):
    def predict(stage, actor_id, system, content, schema):
        request = json.loads(content)
        requests.append((stage, actor_id, request, schema, system))
        if stage == 'actor':
            dialogue = [] if schema['properties']['dialogue']['maxItems'] == 0 else [
                {'text': 'Understood.', 'language': 'English', 'delivery': 'quiet'}]
            return {'character_id': actor_id, 'action': action, 'dialogue': dialogue, 'intent': secret}
        if stage == 'roleplay':
            return {'transition': 'continue', 'effects': [], 'asset_requests': [], 'new_characters': [],
                    'beats': [{'id': 'beat-1', 'action': action, 'setting': 'Inside the workshop.',
                               'final_state': 'Mira stands beside the workbench.'}],
                    'choices': [{'title': title, 'message': message} for title, message in (
                        ('Look', 'I examine the workbench.'), ('Ask', 'I ask about the lift.'), ('Wait', 'I wait beside the window.'))]}
        return {'shots': [{'beat_id': 'beat-1', 'duration': request['new_seconds'],
                          'camera': {'framing': 'medium', 'movement': 'static', 'height': 'eye level',
                                     'speed': 'still', 'focus': 'Mira'},
                          'performance': 'Mira rests a hand on the workbench.', 'sound': 'Workshop ambience.',
                          'visible_subject_ids': list(dict.fromkeys(['player', *[d['speaker_id'] for d in request['dialogue']]])),
                          'offscreen_subject_ids': [], 'dialogue_indices': list(range(len(request['dialogue']))),
                          'transition': 'continuous'}]}
    return predict


@pytest.mark.parametrize('action', ['Mira nods.', 'Alex kneels.', '米拉点头。', 'ミラがうなずく。', 'ميرا تبتسم.'])
def test_concise_and_unspaced_languages_are_valid_actions(action):
    project, world = scene()
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I wait.', duration=5, predict=predictor([], action=action))
    assert result['action'] == action


@pytest.mark.parametrize('action', ['', '...', 'Continue the story.', 'Surprise me', 'next turn'])
def test_empty_or_placeholder_actions_still_fail(action):
    assert not _concrete(action)


@pytest.mark.parametrize(('message', 'intent', 'expected'), [
    ('I ask Mira about the lift.', None, ['Mira']),
    ('"Mira, can you help?"', None, ['Mira']),
    ('I speak to the mechanic.', {'kind': 'talk', 'target_id': 'npc-2'}, ['Mira']),
    ('Ben and Mira, look here.', None, ['Ben', 'Mira']),
    ('I give her the wrench.', {'kind': 'give', 'target_id': 'wrench', 'recipient_id': 'npc-2'}, ['Mira']),
])
def test_addressed_npcs_are_not_displaced_by_bystander_ids(message, intent, expected):
    _, world = scene(('Aaron', 'Ben', 'Mira'))
    assert [c['name'] for c in _active_npcs(world, world['characters'][0], message, intent)] == expected


def test_name_matching_uses_boundaries_and_excludes_absent_characters():
    _, world = scene(('Ann', 'Joann', 'Mira'))
    world['characters'][-1]['location_id'] = 'elsewhere'
    assert [c['name'] for c in _active_npcs(world, world['characters'][0], 'Joann asks about Mira.', None)] == ['Joann']


def test_directed_exchange_uses_one_actor_call_and_passes_author_role_instructions():
    project, world = scene(('Aaron', 'Ben', 'Mira'))
    calls = []
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I ask Mira, "Can you help?"', duration=5, predict=predictor(calls))
    assert [(stage, actor_id) for stage, actor_id, *_ in calls] == [
        ('actor', 'npc-2'), ('roleplay', 'world'), ('director', 'director')]
    assert calls[0][2]['user_instructions'] == project['custom_instructions']
    assert result['dialogue'][-1]['speaker_id'] == 'npc-2'


def test_private_actor_intent_and_hidden_object_state_never_reach_other_stages():
    project, world = scene(('Mira', 'Ben'))
    world['entities'] = [{'id': 'hidden-safe', 'name': 'A secret safe', 'kind': 'object',
                          'state': {'hidden': True, 'code': 'SECRET-493'}}]
    calls = []
    secret = 'SECRET: I intend to steal the key later.'
    plan_turn(project=project, world=world, player_character_id='player',
              message='I wait.', duration=5, predict=predictor(calls, secret=secret))
    for stage, _, request, _, _ in calls:
        assert secret not in json.dumps(request)
        assert 'SECRET-493' not in json.dumps(request)
        if stage == 'roleplay':
            assert all(set(response) == {'character_id', 'action', 'dialogue'}
                       for response in request['character_responses'])


def test_established_cast_can_exceed_the_six_new_character_generation_budget():
    project, world = scene(('Mira', 'Ben', 'Cara', 'Dina', 'Eli', 'Fran', 'Gina'))
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I ask Mira for directions.', duration=5, predict=predictor([]))
    assert len(result['characters']) == 8


@pytest.mark.parametrize('quotation_count', [5, 6])
def test_npc_speech_respects_remaining_line_budget_without_dropping_player_words(quotation_count):
    project, world = scene(('Mira', 'Ben'))
    calls = []
    message = ' '.join(f'"{word}"' for word in ['One.', 'Two.', 'Three.', 'Four.', 'Five.', 'Six.'][:quotation_count])
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message=message, duration=5, predict=predictor(calls))
    assert len(result['dialogue']) == 6
    assert [line['text'] for line in result['dialogue'][:quotation_count]] == quoted_speech(message)
    actor_calls = [call for call in calls if call[0] == 'actor']
    assert actor_calls[-1][3]['properties']['dialogue']['maxItems'] == 0


@pytest.mark.parametrize(('message', 'expected'), [
    ('Ich frage „Wo sind wir?“', ['Wo sind wir?']),
    ('Je demande «Où allons-nous ?»', ['Où allons-nous ?']),
    ('私は「どこですか？」と聞く。', ['どこですか？']),
    ('I say "Ready." Then „Los!“ Then «Oui.»', ['Ready.', 'Los!', 'Oui.']),
])
def test_international_quotation_styles_preserve_exact_speech(message, expected):
    assert quoted_speech(message) == expected


@pytest.mark.parametrize('override', [
    {'duration': True}, {'duration': None}, {'duration': '5'}, {'duration': float('nan')},
    {'duration': float('inf')}, {'duration': 0}, {'duration': -1}, {'message': None},
    {'message': ''}, {'message': 3}, {'intent': []}, {'intent': 'open'},
    {'intent': {'kind': []}}, {'intent': {'kind': {'name': 'talk'}}},
    {'intent': {'target_id': []}}, {'intent': {'recipient_id': {}}}, {'mode': 'unknown'},
    {'message': '"1" "2" "3" "4" "5" "6" "7"'},
])
def test_invalid_user_boundaries_fail_before_any_inference(override):
    project, world = scene()
    calls = []
    arguments = dict(project=project, world=world, player_character_id='player',
                     message='I wait.', duration=5, predict=predictor(calls))
    arguments.update(override)
    with pytest.raises(ValueError):
        plan_turn(**arguments)
    assert calls == []


def test_director_cannot_reverse_accepted_events_even_without_dialogue():
    project, world = scene()
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I wait.', duration=5, predict=predictor([]))
    result['dialogue'] = []
    result['beats'].append({**result['beats'][0], 'id': 'beat-2', 'action': 'Mira sits.'})
    first = {**result['direction']['shots'][0], 'duration': 2.5, 'dialogue_indices': []}
    second = {**copy.deepcopy(first), 'beat_id': 'beat-2'}
    result['direction']['shots'] = [second, first]
    with pytest.raises(ValueError, match='reordered'):
        direct_plan(result, project, duration=5)


def augmented_predictor(calls, edit_narrative):
    base = predictor(calls)
    def predict(stage, actor_id, system, content, schema):
        result = base(stage, actor_id, system, content, schema)
        if stage == 'roleplay':
            edit_narrative(result)
        return result
    return predict


@pytest.mark.parametrize('condition', [{'dead': True}, {'defeated': True}, {'status': 'dead'}, {'alive': False}])
def test_defeated_npcs_do_not_speak_or_take_another_turn(condition):
    project, world = scene()
    world['characters'][1]['state'] = condition
    calls = []
    plan_turn(project=project, world=world, player_character_id='player', message='I look at Mira.',
              duration=5, predict=predictor(calls, action='Alex kneels.'))
    assert [call[0] for call in calls] == ['roleplay', 'director']
    assert 'Mira current condition:' in str(calls[-1][2]['current_scene_facts'])


def test_player_is_never_selected_as_an_npc_with_stale_control_metadata():
    _, world = scene()
    world['characters'][0]['control'] = 'npc'
    assert [c['id'] for c in _active_npcs(world, world['characters'][0], 'Alex waits.', None)] == ['npc-0']


def test_absent_cast_is_not_attached_to_a_local_scene():
    project, world = scene(('Mira', 'Absent spy'))
    world['locations'].append({'id': 'distant', 'name': 'Distant hideout'})
    world['characters'][2].update(location_id='distant', description='SECRET-SPY-APPEARANCE')
    calls = []
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I ask Mira for help.', duration=5, predict=predictor(calls))
    assert {c['id'] for c in result['characters']} == {'player', 'npc-0'}
    coordinator_request = next(call[2] for call in calls if call[0] == 'roleplay')
    assert 'SECRET-SPY-APPEARANCE' not in json.dumps(coordinator_request)
    director_request = next(call[2] for call in calls if call[0] == 'director')
    assert 'SECRET-SPY-APPEARANCE' not in json.dumps(director_request)


def test_attack_can_propose_persistent_defeat_without_automatically_applying_it():
    project, world = scene()
    world['rules'] = ['A successful clean hit defeats a target with one remaining health.']
    world['characters'][1]['state'] = {'health': 1}
    before = copy.deepcopy(world)
    effects = [{'kind': 'character_state', 'character_id': 'npc-0', 'key': 'health', 'value': 0},
               {'kind': 'character_state', 'character_id': 'npc-0', 'key': 'defeated', 'value': True}]
    def edit(plan):
        plan['effects'] = effects
        plan['beats'][0].update(action='Alex lands a strike and Mira falls.', final_state='Mira is defeated on the floor.')
    result = plan_turn(project=project, world=world, player_character_id='player', message='I attack Mira.',
                       intent={'kind': 'attack', 'target_id': 'npc-0'}, duration=5,
                       predict=augmented_predictor([], edit))
    assert world == before
    assert result['effects'] == effects
    accepted = apply_effects(world, effects, event_id='accepted-combat', actor_id='player', witness_ids=['player'])
    assert _active_npcs(accepted, accepted['characters'][0], 'I wait.', None) == []


def test_character_effect_schema_rejects_unknown_targets_and_object_fields():
    from jsonschema import Draft202012Validator
    _, world = scene()
    validator = Draft202012Validator(_effect_schema(world))
    assert validator.is_valid([{'kind': 'character_state', 'character_id': 'npc-0', 'key': 'defeated', 'value': True}])
    assert not validator.is_valid([{'kind': 'character_state', 'character_id': 'invented', 'key': 'dead', 'value': True}])
    assert not validator.is_valid([{'kind': 'character_state', 'entity_id': 'npc-0', 'key': 'dead', 'value': True}])


def test_text_only_start_discovers_street_and_interactive_door_without_reference_images():
    project, world = scene(())
    world['locations'] = []
    world['current_location_id'] = None
    world['characters'][0]['location_id'] = None
    before = copy.deepcopy(world)
    discoveries = {'locations': [{'id': 'street', 'name': 'City street', 'description': 'A pixel art street.'}],
                   'entities': [{'id': 'door', 'name': 'Shop door', 'description': 'A blue door on the street.',
                                 'kind': 'door', 'location_id': 'street', 'affordances': ['examine', 'open', 'close']}]}
    def edit(plan):
        plan.update(discoveries=discoveries, transition='cut')
        plan['beats'][0].update(action='Alex walks along a pixel art city street.', setting='On a pixel art street.',
                                final_state='Alex stands near a blue shop door.')
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I am in a city street, pixel art.', duration=5,
                       predict=augmented_predictor([], edit))
    assert result['discoveries'] == discoveries
    assert result['asset_requests'] == []
    assert world == before
    accepted = apply_discoveries(world, discoveries, player_character_id='player')
    assert accepted['current_location_id'] == accepted['characters'][0]['location_id'] == 'street'
    assert accepted['entities'][0]['id'] == 'door'


def test_discovered_item_can_be_picked_up_and_remembered_in_the_same_turn():
    project, world = scene(())
    discoveries = {'entities': [{'id': 'brass-key', 'name': 'Brass key', 'description': 'A small brass key on the table.', 'kind': 'key'}]}
    effects = [{'kind': 'holder', 'entity_id': 'brass-key', 'character_id': 'player'}]
    def edit(plan):
        plan.update(discoveries=discoveries, effects=effects)
        plan['beats'][0].update(action='Alex picks up the brass key.', final_state='Alex holds the brass key.')
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I pick up the key on the table.', duration=5,
                       predict=augmented_predictor([], edit))
    provisional = apply_discoveries(world, result['discoveries'], player_character_id='player')
    accepted = apply_effects(provisional, result['effects'], event_id='pickup', actor_id='player',
                             summary=result['action'], witness_ids=['player'])
    assert accepted['entities'][0]['holder_id'] == 'player'
    calls = []
    plan_turn(project=project, world=accepted, player_character_id='player',
              message='I examine my inventory.', intent={'kind': 'inventory'}, duration=5,
              predict=predictor(calls, action='Alex examines the key.'))
    assert 'ALREADY HELD by Alex' in str(calls[0][2]['current_scene_facts'])
    assert calls[0][2]['world']['recent_events'][0]['effects'] == effects


@pytest.mark.parametrize('edit', [
    lambda plan: plan.update(discoveries={'entities': [{'id': 'player', 'name': 'Bad', 'description': '', 'kind': 'key'}]}),
    lambda plan: plan.update(effects=[{'kind': 'holder', 'entity_id': 'not-discovered', 'character_id': 'player'}]),
])
def test_discovery_collisions_and_unresolved_effect_ids_fail_before_direction(edit):
    project, world = scene(())
    calls = []
    with pytest.raises(ValueError):
        plan_turn(project=project, world=world, player_character_id='player',
                  message='I inspect the table.', duration=5, predict=augmented_predictor(calls, edit))
    assert [call[0] for call in calls] == ['roleplay']


@pytest.mark.parametrize('request_identity_image', [False, True])
def test_a_new_npc_can_enter_and_speak_without_an_extra_actor_call(request_identity_image):
    project, world = scene(())
    calls = []
    def edit(plan):
        plan['new_characters'] = [{'name': 'Courier', 'description': 'A courier in a red coat.', 'voice': 'quiet',
                                   'dialogue': [{'text': 'A message for you.', 'language': 'en', 'delivery': 'quiet'}]}]
        if request_identity_image:
            plan['asset_requests'] = [{'name': 'Courier identity', 'prompt': 'A pixel art courier in a red coat.',
                                       'semantic_role': 'character', 'person_name': 'Courier', 'prompt_tag': 'courier'}]
        plan['beats'][0].update(action='A courier approaches Alex.', final_state='The courier stands beside Alex.')
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='A random NPC appears on the street.', duration=5,
                       predict=augmented_predictor(calls, edit))
    assert [call[0] for call in calls] == ['roleplay', 'director']
    assert result['dialogue'][0]['text'] == 'A message for you.'
    assert result['dialogue'][0]['language'] == 'English'
    newcomer = next(c for c in result['characters'] if c['name'] == 'Courier')
    assert result['dialogue'][0]['speaker_id'] == newcomer['id']
    assert 'dialogue' not in newcomer
    assert bool(result['asset_requests']) is request_identity_image
    assert result['transition'] == 'continue'
    from backend.game_director import validate_narrative
    checked = validate_narrative(result, world=world, player_character_id='player',
                                 message='A random NPC appears on the street.', duration=5)
    assert next(c for c in checked['characters'] if c['name'] == 'Courier')['id'] == newcomer['id']


def test_studio_uses_authored_script_and_does_not_invoke_npc_roleplay():
    project, world = scene()
    calls = []
    def edit(plan):
        plan.pop('new_characters')
        plan.update(action='Mira opens the workshop window.', setting='In the workshop.',
                    final_state='The window is open.', characters=[], dialogue=[])
    result = plan_turn(project=project, world=world, player_character_id=None, mode='studio',
                       message='Film a comic chase through the workshop.', duration=5,
                       predict=augmented_predictor(calls, edit))
    assert [call[0] for call in calls] == ['roleplay', 'director']
    assert result['action'] == 'Mira opens the workshop window.'


def test_original_premise_reaches_the_world_coordinator_without_replaying_old_project_prose():
    project, world = scene()
    project['story']['text'] = 'OLD-RENDER: Alex picked up an unrelated letter.'
    premise = 'The workshop has a brass coin lying on the floor near the blue door.'
    calls = []
    plan_turn(project=project, world=world, player_character_id='player',
              message='I look around.', premise=premise, duration=5, predict=predictor(calls))
    coordinator = next(call for call in calls if call[0] == 'roleplay')
    assert coordinator[2]['story_premise'] == premise
    assert 'OLD-RENDER' not in json.dumps(coordinator[2])
    assert 'does not pick it up' in coordinator[4]
    assert 'story_premise' not in next(call[2] for call in calls if call[0] == 'actor')
