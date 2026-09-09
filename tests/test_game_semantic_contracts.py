"""Regressions for observed valid-JSON failures; no model or render calls."""
import copy
import json

import pytest

from backend.game_director import _author_instructions, plan_turn, validate_narrative
from backend.gameplay import infer_simple_intent, read_only_inspection
from backend.world import validate_world
from test_game_director_robustness import scene, predictor


def inspection_scene():
    project, world = scene()
    world['entities'].append({'id': 'key', 'name': 'Brass key', 'affordances': ['examine', 'take', 'give']})
    world = validate_world(world)
    return project, world


def run_case(*, message='I examine the brass key currently on the workbench.', npc_action='Mira watches the key.',
             npc_intent='Keep watch.', effects=None, exception=None, customize=None, dialogue=()):
    project, world = inspection_scene()
    kwargs = {}
    if customize:
        customize(project, world, kwargs)
    calls = []
    base = predictor(calls)
    def predict(stage, actor_id, system, content, schema):
        value = base(stage, actor_id, system, content, schema)
        if stage == 'actor':
            value.update(action=npc_action, intent=npc_intent, dialogue=copy.deepcopy(list(dialogue)))
        elif stage == 'roleplay':
            value['effects'] = effects or []
            if exception:
                value['inspection_exception'] = copy.deepcopy(exception)
        return value
    result = plan_turn(project=project, world=world, player_character_id='player', message=message,
                       duration=5, predict=predict, **kwargs)
    return result, calls, project, world


@pytest.mark.parametrize('intent', ['Provide the code to Alex so he can enter.', 'Answer the question.', 'Tell Alex the code.',
    'Mira provides the door code to Alex to facilitate entry.', 'Providing the code helps Alex.', 'Mira answers the question.',
    'Mira tells Alex the code.', 'Mira explains the code.'])
def test_private_intent_to_answer_is_not_an_actual_npc_answer(intent):
    with pytest.raises(ValueError, match='no spoken words'):
        run_case(message='I ask Mira, "What is the code?"', npc_action='Mira gestures toward the door.', npc_intent=intent)


def test_a_drawer_is_not_a_drawn_or_written_nonverbal_answer():
    with pytest.raises(ValueError, match='no spoken words'):
        run_case(message='I ask Mira, "What is the code?"', npc_action='Mira gestures at the drawer.', npc_intent='Provide the code.')


def test_incidental_nod_is_not_an_answer_to_a_factual_question():
    with pytest.raises(ValueError, match='no spoken words'):
        run_case(message='I ask Mira, "What is the code?"', npc_action='Mira gestures toward the door with a nod.',
                 npc_intent='Provide the code to Alex quickly.')


def test_deliberating_about_an_answer_is_not_forced_cooperation():
    run_case(message='I ask Mira, "What is the code?"', npc_action='Mira pauses and considers the question.',
             npc_intent='Decide whether to answer the question.')


@pytest.mark.parametrize(('action', 'intent'), [
    ('Mira folds her arms.', 'Refuse to answer.'),
    ('Mira answers with a nod.', 'Answer the yes-or-no question.'),
    ('Mira writes 4729 on the board.', 'Provide the code.'),
    ('Mira answers by pointing at the key.', 'Answer where the key is.'),
    ('Mira silently gestures.', 'Provide a nonverbal answer.'),
])
def test_intentional_refusal_and_performed_nonverbal_answers_remain_valid(action, intent):
    result, *_ = run_case(message='I ask Mira, "Can you answer?"', npc_action=action, npc_intent=intent)
    assert [line['speaker_id'] for line in result['dialogue']] == ['player']


def test_silent_character_is_not_forced_to_speak():
    def silent(project, world, kwargs):
        world['characters'][1]['speaking_style'] = 'Mute; communicates through lights.'
    run_case(message='I ask Mira, "Can you answer?"', npc_intent='Provide the answer.', customize=silent)


def test_actual_npc_words_preserve_actor_knowledge_and_private_intent_is_not_leaked():
    result, calls, *_ = run_case(message='I ask Mira, "What is the code?"', npc_intent='Provide the code.',
        dialogue=[{'text': 'The code is 4729.', 'language': 'English', 'delivery': 'quiet'}])
    assert result['dialogue'][-1]['text'] == 'The code is 4729.'
    assert 'Provide the code.' not in json.dumps([call[2] for call in calls if call[0] != 'actor'])


@pytest.mark.parametrize('message', ['I examine the brass key.', 'Inspect the brass key currently on the workbench.',
                                  'I look at the brass key in her hand.'])
def test_whole_inspections_with_simple_placement_are_recognized(message):
    _, world = inspection_scene()
    assert infer_simple_intent(world, 'player', message) == {'kind': 'examine', 'target_id': 'key'}


@pytest.mark.parametrize('message', ['I pick up the brass key to inspect it.', 'I examine the brass key and pick it up.',
    'I examine the brass key while touching it.', 'I examine the brass key on the workbench and take it.',
    'I examine the brass key before opening the door.', 'I examine it.', 'I ask "examine the brass key".'])
def test_manipulative_compound_and_uncertain_inspections_are_not_frozen(message):
    _, world = inspection_scene()
    assert read_only_inspection(world, 'player', message) is None
    run_case(message=message, effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])


@pytest.mark.parametrize('effect', [
    {'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'},
    {'kind': 'owner', 'entity_id': 'key', 'character_id': 'player'},
    {'kind': 'entity_location', 'entity_id': 'key', 'location_id': None},
])
def test_observed_unrequested_pickup_is_rejected_before_direction(effect):
    with pytest.raises(ValueError, match='read-only inspection'):
        run_case(effects=[effect])


def test_npc_can_independently_take_the_inspected_object():
    effect = {'kind': 'holder', 'entity_id': 'key', 'character_id': 'npc-0'}
    result, *_ = run_case(npc_action='Mira picks up the brass key.', effects=[effect])
    assert result['effects'] == [effect]


@pytest.mark.parametrize('action', ['Mira rests her hand beside the brass key.',
    'Mira moves her gaze toward the brass key.', 'Mira lifts her gaze from the brass key.',
    'Mira picks up the brass key.'])
def test_npc_nearby_hand_or_own_pickup_cannot_authorize_player_pickup(action):
    with pytest.raises(ValueError, match='read-only inspection'):
        run_case(npc_action=action, effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])


def test_npc_can_explicitly_hand_the_inspected_key_to_the_player():
    effect = {'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}
    result, *_ = run_case(npc_action='Mira hands the brass key to Alex.', effects=[effect])
    assert result['effects'] == [effect]


@pytest.mark.parametrize('location', [None, 'room'])
def test_explicit_npc_transfer_can_clear_ground_location_or_use_recipient_location(location):
    effects = [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'},
               {'kind': 'entity_location', 'entity_id': 'key', 'location_id': location}]
    result, *_ = run_case(npc_action='Mira hands the brass key to Alex.', effects=effects)
    assert result['effects'] == effects


def test_explicit_npc_transfer_cannot_teleport_the_object_to_an_unrelated_place():
    def customize(project, world, kwargs):
        world['locations'].append({'id': 'remote', 'name': 'Remote room'})
    with pytest.raises(ValueError, match='read-only inspection'):
        run_case(customize=customize, npc_action='Mira hands the brass key to Alex.', effects=[
            {'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'},
            {'kind': 'entity_location', 'entity_id': 'key', 'location_id': 'remote'}])


def test_npc_private_intent_or_negated_handling_cannot_authorize_transfer():
    with pytest.raises(ValueError, match='read-only inspection'):
        run_case(npc_action='Mira does not pick up the brass key.', npc_intent='I will take it later.',
                 effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'npc-0'}])


@pytest.mark.parametrize('source', ['rules', 'guide', 'premise', 'custom'])
def test_exact_authored_inspection_consequence_preserves_reviewable_basis(source):
    instruction = 'Inspecting the brass key makes it teleport into Alex\'s hand.'
    def customize(project, world, kwargs):
        if source == 'rules':
            world['rules'].append(instruction)
        elif source == 'guide':
            kwargs['guides'] = [{'enabled': True, 'text': instruction}]
        elif source == 'premise':
            kwargs['premise'] = instruction
        else:
            project['custom_instructions'] = instruction
    exception = {'instruction': instruction, 'reason': 'The established inspection spell transfers the key.'}
    result, _, _, world = run_case(customize=customize, exception=exception,
        effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])
    assert result['inspection_exception'] == exception
    admitted = validate_narrative(result, world=world, player_character_id='player',
                                 message='I examine the brass key.', duration=5)
    assert admitted['inspection_exception'] == exception


def test_fabricated_authored_exception_is_rejected():
    with pytest.raises(ValueError, match='exact supplied authored instruction'):
        run_case(exception={'instruction': 'Inspecting magically picks things up.', 'reason': 'A magic transfer occurs.'},
                 effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])


def test_long_authored_premise_can_cite_the_exact_relevant_excerpt():
    excerpt = 'Inspecting the brass key makes it teleport into Alex\'s hand.'
    def customize(project, world, kwargs):
        kwargs['premise'] = ('The workshop is quiet. ' * 140) + excerpt
    result, *_ = run_case(customize=customize, exception={'instruction': excerpt, 'reason': 'The established spell transfers the key.'},
        effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])
    assert result['inspection_exception']['instruction'] == excerpt


def test_unused_bad_optional_citation_does_not_discard_corrected_inspection():
    result, *_ = run_case(exception={'instruction': 'Looking does not pick up the object.',
                                    'reason': 'The requested inspection changes nothing.'})
    assert result['effects'] == []
    assert 'inspection_exception' not in result


@pytest.mark.parametrize('reason', ['The door is currently unlocked, so no special rule applies to the inspection of its state.',
                                  'The ordinary rule allows Alex to take the key.'])
def test_real_unrelated_door_citation_cannot_authorize_key_pickup(reason):
    excerpt = 'A locked door stays locked until an established unlocking action succeeds.'
    def customize(project, world, kwargs):
        world['rules'].append(excerpt)
    with pytest.raises(ValueError, match='does not establish an inspection-triggered consequence'):
        run_case(customize=customize, exception={'instruction': excerpt, 'reason': reason},
                 effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])


def test_explicit_no_special_rule_reason_cannot_authorize_pickup_even_with_relevant_quote():
    excerpt = 'Inspecting the brass key leaves it on the table.'
    def customize(project, world, kwargs):
        world['rules'].append(excerpt)
    with pytest.raises(ValueError, match='does not establish an inspection-triggered consequence'):
        run_case(customize=customize, exception={'instruction': excerpt, 'reason': 'No special rule applies.'},
                 effects=[{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])


def test_actor_receives_inspection_contract_before_proposing_its_own_action():
    _, calls, *_ = run_case()
    actor = next(call for call in calls if call[0] == 'actor')
    assert actor[2]['read_only_inspection'] == {'id': 'key', 'name': 'Brass key', 'holder_id': None, 'location_id': 'room'}
    assert 'Do not claim they handle, lift, take or move it.' in actor[4]


def test_canonical_generated_state_is_not_an_authored_instruction_or_exception():
    project, _ = scene()
    original = project['custom_instructions']
    project['custom_instructions'] += '\nCanonical object placement for this turn: Brass key is held by Mira.'
    assert _author_instructions(project) == original


def test_inspection_contract_is_not_added_to_other_response_schemas():
    _, calls, *_ = run_case(message='I wait.')
    schema = next(call[3] for call in calls if call[0] == 'roleplay')
    assert 'inspection_exception' not in schema['properties']
