"""Opt-in evaluation repair mirrors the bounded production stage retry."""
import copy
import json

import pytest

from backend.lmstudio import LMStudioError
from scripts.evaluate_game_assistant import evaluate_case, fixtures
from test_game_assistant_evaluation import Client


class RepairClient(Client):
    def __init__(self, stage='actor', *, persist=False, failure=None):
        super().__init__()
        self.target_stage, self.persist, self.repair_failure = stage, persist, failure
        self.requests = []

    def complete_json_result(self, model, system, content, schema, **options):
        repairing = isinstance(content, list)
        text = content[0]['text'] if repairing else content
        self.requests.append((system, copy.deepcopy(content), options))
        if repairing and self.repair_failure:
            raise self.repair_failure
        answer = super().complete_json_result(model, system, text, schema, **options)
        value = answer['result']
        if self.target_stage == 'actor' and 'character_id' in schema['properties']:
            value.update(action='Mira gestures toward the door.', intent='Provide the code to Alex so he can enter.', dialogue=[])
            if repairing and not self.persist:
                value['dialogue'] = [{'text': 'The code is 4729.', 'language': 'English', 'delivery': 'calm'}]
        if self.target_stage == 'roleplay' and 'beats' in schema['properties']:
            value['effects'] = [] if repairing and not self.persist else [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'alex'}]
        return answer


def case(case_id):
    return next(row for row in fixtures() if row['id'] == case_id)


def test_default_evaluation_preserves_single_pass_failure_semantics():
    result = evaluate_case(case('npc_direct_factual_answer'), RepairClient(), 'mock-only')
    assert result['status'] == 'failed' and 'no spoken words' in result['error']
    assert result['semantic_repair_policy'] == 'disabled'
    assert len(result['stage_records']) == 1 and result['semantic_repairs'] == []


def test_actor_correction_retains_rejected_output_and_never_injects_unrelated_private_fact():
    client = RepairClient()
    result = evaluate_case(case('npc_direct_factual_answer'), client, 'mock-only', semantic_repair=True)
    assert result['status'] == 'validated', result
    assert [entry['stage'] for entry in result['stage_records']] == ['actor', 'actor', 'roleplay', 'director']
    assert result['stage_records'][0]['result']['dialogue'] == []
    assert len(result['semantic_repairs']) == 1
    repaired = result['stage_records'][1]
    assert 'Correct this specific issue' in repaired['system']
    assert 'Earlier rejected response' in repaired['content'][1]['text']
    assert '8642' not in json.dumps(repaired['content'])
    assert result['plan']['dialogue'][-1]['text'] == 'The code is 4729.'


def test_coordinator_repair_reuses_good_actor_without_another_actor_call():
    result = evaluate_case(case('long_history'), RepairClient('roleplay'), 'mock-only', semantic_repair=True)
    assert result['status'] == 'validated', result
    assert [entry['stage'] for entry in result['stage_records']] == ['actor', 'roleplay', 'roleplay', 'director']
    assert result['cached_stage_reuses'] == [{'stage': 'actor', 'actor_id': 'mira', 'stage_record_index': 0}]
    assert result['stage_records'][1]['result']['effects'][0]['character_id'] == 'alex'
    assert result['plan']['effects'] == []


def test_second_bad_semantic_output_is_a_recorded_failure_without_another_retry():
    result = evaluate_case(case('npc_direct_factual_answer'), RepairClient(persist=True), 'mock-only', semantic_repair=True)
    assert result['status'] == 'failed'
    assert len(result['semantic_repairs']) == 1 and len(result['stage_records']) == 2
    assert 'compilation' not in result


@pytest.mark.parametrize('failure', [LMStudioError('Still running', code='request_timeout'),
                                    LMStudioError('Disconnected', code='connection_error'),
                                    ValueError('Invalid transport result')])
def test_transport_failure_is_never_semantically_retried(failure):
    result = evaluate_case(case('long_history'), Client(failure=failure), 'mock-only', semantic_repair=True)
    assert result['status'] == 'failed' and len(result['stage_records']) == 1
    assert result['semantic_repairs'] == []


def test_uncertain_repair_preserves_original_failure_and_stops():
    result = evaluate_case(case('npc_direct_factual_answer'),
        RepairClient(failure=LMStudioError('Still running', code='request_timeout')), 'mock-only', semantic_repair=True)
    assert result['status'] == 'failed' and result['error_code'] == 'request_timeout'
    assert len(result['stage_records']) == 2 and len(result['semantic_repairs']) == 1
