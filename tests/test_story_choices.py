"""AI choice cards are the player's possible moves, never preselected NPC decisions."""
import copy

import pytest

from backend.stories import StoryManager, player_choices, validate_plan


CAST = [{'name': 'Elira'}, {'name': 'Arin'}]
VALID = [
    {'title': 'Ask him', 'message': 'I ask Arin about the box.'},
    {'title': 'Look closer', 'message': 'You look at the blue box.'},
    {'title': 'Speak', 'message': 'I say, "Arin takes care of that. What is inside?"'},
]


@pytest.mark.parametrize('message', [
    'Arin takes a slow step toward the table, leaning in.',
    'arin takes a slow step toward the table.',
    'I study the lid. Arin opens the box.',
    'I wait, then Arin opens the box.',
    'I look up and Arin hands me the box.',
    'I step back while Arin speaks.',
])
def test_known_npc_subject_is_replaced_with_a_player_move(message):
    choices = [*VALID[:2], {'title': 'Step closer', 'message': message}]
    result = player_choices(choices, 'Elira', CAST)
    assert len(result) == 3
    assert result[:2] == choices[:2]
    assert message not in [c['message'] for c in result]
    assert result[2]['message'].startswith('I ')
    assert choices[2]['message'] == message


@pytest.mark.parametrize('message', [
    'I ask Arin to explain.',
    'Ask Arin about the key.',
    'Elira walks toward Arin.',
    "I examine Arin's coat.",
    'I ask whether Arin knows the answer.',
    'I say, "Arin opens the box in my dream. Why?"',
    'I ask, “Arin opens the box, right?”',
    "I say, 'Arin opens the box in the story.'",
])
def test_player_moves_and_quoted_speech_are_preserved(message):
    choices = [*VALID[:2], {'title': 'My move', 'message': message}]
    assert player_choices(choices, 'Elira', CAST) == choices


def test_full_npc_name_and_unique_first_name_are_detected():
    cast = [{'name': 'Elira Sherifaj'}, {'name': 'Arin Marku'}]
    for name in ('Arin', 'Arin Marku'):
        result = player_choices([{'title': 'Move', 'message': name + ' walks to the door.'}], 'Elira Sherifaj', cast)
        assert all(not c['message'].startswith(name + ' ') for c in result)
    player_move = {'title': 'Move', 'message': 'Elira walks to the door.'}
    assert player_choices([player_move], 'Elira Sherifaj', cast)[0] == player_move


def test_exactly_three_distinct_choices_with_malformed_legacy_input():
    result = player_choices([None, {'title': '', 'message': ''}, VALID[0], VALID[0],
                             {'title': 'Legacy', 'action': 'I examine the table.'}], 'Elira', CAST)
    assert len(result) == len({c['message'].casefold() for c in result}) == 3
    assert result[:2] == [VALID[0], {'title': 'Legacy', 'message': 'I examine the table.'}]


def plan(choices):
    return {'action': 'Arin points to the box.', 'setting': 'Greenhouse', 'final_state': 'Both wait.',
            'transition': 'continue', 'dialogue': [], 'asset_requests': [], 'choices': choices,
            'characters': [{'name': p['name'], 'description': 'An adult.', 'voice': 'Calm.'} for p in CAST]}


def test_planned_game_choices_are_guarded_without_restricting_studio():
    choices = [*VALID[:2], {'title': 'Step closer', 'message': 'Arin steps closer.'}]
    assert validate_plan(plan(choices), 'Elira')['choices'][2]['message'].startswith('I ')
    assert validate_plan(plan(choices), 'Elira', mode='studio')['choices'] == choices


def test_public_read_repairs_saved_game_choices_without_mutating_memory():
    choices = [*VALID[:2], {'title': 'Step closer', 'message': 'Arin steps closer.'}]
    story = {'id': 'story', 'mode': 'game', 'player_name': 'Elira', 'active_branch_id': 'branch',
             'branches': {'branch': []}, 'active_run_id': None, 'choices': choices,
             'base_project': {'subjects': CAST},
             'turns': [{'plan': plan(choices), 'observation': {'choices': choices}}],
             'observed_by_run': {None: {'observed_state': 'A greenhouse.', 'choices': choices}}}
    before = copy.deepcopy(story)
    manager = object.__new__(StoryManager)
    manager.videos = lambda: pytest.fail('Reading an empty branch must not access render jobs.')
    result = manager.public(story)
    assert story == before
    for target in (result, result['observed_state'], result['turns'][0]['plan'], result['turns'][0]['observation']):
        assert len(target['choices']) == 3
        assert target['choices'][:2] == choices[:2]
        assert target['choices'][2]['message'].startswith('I ')


def test_empty_game_setup_and_studio_saved_choices_remain_unchanged():
    manager = object.__new__(StoryManager)
    story = {'mode': 'game', 'player_name': 'Elira', 'active_branch_id': 'branch', 'branches': {'branch': []},
             'active_run_id': None, 'choices': [], 'turns': [], 'observed_by_run': {}}
    assert manager.public(story)['choices'] == []
    story.update(mode='studio', choices=[{'title': 'Direct Arin', 'message': 'Arin walks closer.'}])
    assert manager.public(story)['choices'] == story['choices']
