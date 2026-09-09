"""Explicit role evidence is measured without requiring one canned utterance."""
import copy
import json

import pytest

from scripts.evaluate_game_assistant import evaluate_case, fixtures, summarize
from test_game_assistant_evaluation import Client


class RoleClient(Client):
    def __init__(self, *, reply=None, effects=None):
        super().__init__()
        self.reply, self.effects = reply, effects

    def complete_json_result(self, model, system, content, schema, **options):
        answer = super().complete_json_result(model, system, content, schema, **options)
        value = answer['result']
        if 'character_id' in schema['properties'] and self.reply is not None:
            value['dialogue'] = [{'text': self.reply, 'language': 'English', 'delivery': 'brief and direct'}]
        elif 'shots' not in schema['properties'] and 'character_id' not in schema['properties'] and self.effects is not None:
            value['effects'] = copy.deepcopy(self.effects)
            if self.effects:
                value['beats'][0].update(action='Alex strikes the exposed core and the Clockwork Bandit collapses.',
                                        final_state='The Clockwork Bandit is defeated; Alex and Mira remain unharmed.')
        answer['diagnostics']['attempt_records'][0]['response_text'] = json.dumps(value)
        return answer


@pytest.mark.parametrize('reply', ['4729.', 'The code is 4729.', 'Try four seven two nine.', 'It is 4-7-2-9.'])
def test_factual_npc_answer_accepts_different_phrasings_and_spoken_digit_forms(reply):
    case = next(case for case in fixtures() if case['id'] == 'npc_direct_factual_answer')
    original = copy.deepcopy(case)
    result = evaluate_case(case, RoleClient(reply=reply), 'mock-only')
    assert result['status'] == 'validated', result.get('error') or result['automatic_checks']
    assert result['automatic_checks']['addressed_npc_supplied_spoken_answer']
    assert result['automatic_checks']['spoken_answer_contains_established_fact']
    assert case == original and result['manual_review'] == 'required'


@pytest.mark.parametrize(('reply', 'failed_check'), [
    (None, 'addressed_npc_supplied_spoken_answer'),
    ('How can I help?', 'spoken_answer_contains_established_fact'),
    ('8642.', 'spoken_answer_contains_established_fact'),
    ("4729. Ben's private code is 8642.", 'unrelated_private_fact_not_spoken'),
])
def test_nodding_generic_help_wrong_answers_and_private_fact_leaks_fail(reply, failed_check):
    case = next(case for case in fixtures() if case['id'] == 'npc_direct_factual_answer')
    result = evaluate_case(case, RoleClient(reply=reply), 'mock-only')
    assert result['status'] == 'invariant_failed', result.get('error')
    assert result['automatic_checks'][failed_check] is False
    assert summarize([result])['validated_model_cases'] == 0


def state_effect(target, key, value):
    return {'kind': 'character_state', 'character_id': target, 'key': key, 'value': value}


@pytest.mark.parametrize('terminal', [state_effect('bandit', 'defeated', True), state_effect('bandit', 'dead', True),
                                    state_effect('bandit', 'status', 'defeated')])
def test_weakened_enemy_outcome_accepts_supported_terminal_state_representations(terminal):
    case = next(case for case in fixtures() if case['id'] == 'weakened_enemy_finish')
    before = copy.deepcopy(case)
    result = evaluate_case(case, RoleClient(effects=[state_effect('bandit', 'health', 0), terminal]), 'mock-only')
    assert result['status'] == 'validated', result.get('error') or result['automatic_checks']
    assert result['automatic_checks']['target_health_resolved_to_zero']
    assert result['automatic_checks']['target_defeated_or_dead']
    assert result['automatic_checks']['no_unrelated_character_harmed']
    assert case == before and result['manual_review'] == 'required'


@pytest.mark.parametrize(('effects', 'failed_check'), [
    ([], 'target_health_resolved_to_zero'),
    ([state_effect('bandit', 'health', 0)], 'target_defeated_or_dead'),
    ([state_effect('bandit', 'dead', True)], 'target_health_resolved_to_zero'),
    ([state_effect('bandit', 'health', 0), state_effect('bandit', 'dead', True), state_effect('mira', 'dead', True)], 'no_unrelated_character_harmed'),
    ([state_effect('bandit', 'health', 0), state_effect('bandit', 'defeated', True), state_effect('alex', 'health', 1)], 'no_unrelated_character_harmed'),
])
def test_setup_only_unrecorded_defeat_and_arbitrary_victims_are_not_success(effects, failed_check):
    case = next(case for case in fixtures() if case['id'] == 'weakened_enemy_finish')
    result = evaluate_case(case, RoleClient(effects=effects), 'mock-only')
    assert result['status'] == 'invariant_failed', result.get('error')
    assert result['automatic_checks'][failed_check] is False


def test_new_role_contracts_do_not_change_baseline_case_semantics():
    cases = fixtures()
    assert all('expected_spoken_fact' not in case and 'expected_combat_outcome' not in case for case in cases[:12])
    assert {case['id'] for case in cases[12:]} == {'npc_direct_factual_answer', 'weakened_enemy_finish'}
    assert all(case.get('evaluation_contract', '').endswith('_v1') for case in cases[12:])
