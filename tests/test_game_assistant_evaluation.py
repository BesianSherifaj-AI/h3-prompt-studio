"""Evaluation reports must retain failures and never mislabel mocks as live quality."""
import copy
import json

import pytest

from backend.lmstudio import LMStudioError
from backend.game_director import validate_narrative
from backend.projects import check_project
from backend.world import validate_world
from scripts.evaluate_game_assistant import DEFAULT_SETTINGS, evaluate_case, fixtures, main, run_suite, summarize


class Client:
    def __init__(self, *, failure=None, loaded=False):
        self.calls = []
        self.failure, self.loaded = failure, loaded
    def native_models(self):
        return [{'key': key, 'type': 'llm', 'capabilities': {},
                 'loaded_instances': [{'id': 'user-owned'}] if self.loaded else []} for key in ('model-a', 'model-b')]
    def complete_json_result(self, model, system, content, schema, **options):
        self.calls.append((model, options))
        if self.failure:
            raise self.failure
        request = json.loads(content)
        if 'languages' in schema['properties']:
            value = {'languages': {str(index): 'English' for index in request['missing_language_indices']}}
        elif 'character_id' in schema['properties']:
            value = {'character_id': request['acting_as']['id'],
                     'action': request['acting_as']['name'] + ' steadies a hand on the workbench.', 'dialogue': [], 'intent': 'Keep balance.'}
        elif 'shots' in schema['properties']:
            value = {'shots': [{'beat_id': request['beats'][0]['id'], 'duration': request['new_seconds'],
                'camera': {'framing': 'medium two-shot', 'movement': 'static', 'height': 'eye level', 'speed': 'still', 'focus': 'hands'},
                'performance': 'The characters keep their hands clearly visible.', 'sound': 'Quiet workshop ambience.',
                'visible_subject_ids': [subject['id'] for subject in request['subjects']], 'offscreen_subject_ids': [],
                'dialogue_indices': list(range(len(request['dialogue']))), 'transition': 'continuous'}]}
        else:
            value = {'transition': 'continue', 'new_characters': [], 'effects': (request.get('resolved_intent') or {}).get('effects', []),
                'asset_requests': [], 'beats': [{'id': 'beat-1', 'action': 'Alex examines the workbench while Mira steadies her hand.',
                    'setting': 'Inside the workshop.', 'final_state': 'Alex and Mira remain beside the workbench.'}],
                'choices': [{'title': 'Look', 'message': 'I examine the door.'}, {'title': 'Ask', 'message': 'I ask Mira about the key.'},
                            {'title': 'Wait', 'message': 'I wait beside the workbench.'}]}
            if 'player_language' in schema['properties']:
                value['player_language'] = 'Albanian'
            if request.get('mode') == 'studio':
                value.pop('new_characters')
                value.update(action=value['beats'][0]['action'], setting=value['beats'][0]['setting'],
                    final_state=value['beats'][0]['final_state'], dialogue=[],
                    characters=[{'name': person['name'], 'description': person['description'], 'voice': ''}
                                for person in request['existing_characters']])
        return {'result': value, 'diagnostics': {'attempts': 1, 'locally_validated': True,
                'attempt_records': [{'response_text': json.dumps(value)}]}}


def test_every_fixture_is_valid_deterministic_and_keeps_user_data_out():
    first, second = fixtures(), fixtures()
    assert first == second
    assert len({case['id'] for case in first}) == 14
    assert [case['id'] for case in first[:12]] == [
        'combat_attempt', 'item_pickup', 'item_drop', 'item_give', 'door_open', 'locked_door_guard',
        'locked_door_attempt', 'addressed_npc', 'multilingual_quote', 'text_only_start', 'long_history', 'studio_author_mode']
    for case in first:
        check_project(case['project'])
        validate_world(case['world'])
        assert case['project']['assets'] == []


@pytest.mark.parametrize('case_id', ['text_only_start', 'item_pickup', 'item_drop', 'item_give', 'door_open',
                                    'addressed_npc', 'multilingual_quote', 'long_history', 'studio_author_mode'])
def test_evaluation_runs_real_planning_validators_and_preserves_evidence(case_id):
    case = next(case for case in fixtures() if case['id'] == case_id)
    original = copy.deepcopy(case)
    client = Client()
    result = evaluate_case(case, client, 'owned-test')
    assert result['status'] == 'validated', result.get('error') or result['automatic_checks']
    assert result['manual_review'] == 'required' and all(result['automatic_checks'].values())
    assert result['evaluation_version'] == 2
    assert result['compilation']['valid'] and result['compilation']['prompt']
    assert not [issue for issue in result['compilation']['issues'] if issue['severity'] == 'error']
    assert case == original
    assert all(entry['schema_validated'] and entry['diagnostics']['attempt_records'][0]['response_text']
               for entry in result['stage_records'])
    assert all(call[0] == 'owned-test' for call in client.calls)


def test_disabled_interaction_is_reported_separately_from_model_success():
    case = next(case for case in fixtures() if case['id'] == 'locked_door_guard')
    client = Client()
    result = evaluate_case(case, client, 'unused')
    assert result['status'] == 'expected_guard_rejection' and client.calls == []
    totals = summarize([result])
    assert totals['model_cases'] == 0 and totals['validated_model_cases'] == 0
    assert totals['expected_guard_rejections'] == 1 and totals['quality_rating'] is None


def test_text_only_opening_supplies_premise_without_existing_location_state():
    case = next(case for case in fixtures() if case['id'] == 'text_only_start')
    assert case['world']['locations'] == [] and case['world']['current_location_id'] is None
    result = evaluate_case(case, Client(), 'owned-test')
    roleplay = next(entry for entry in result['stage_records'] if entry['stage'] == 'roleplay')
    assert json.loads(roleplay['content'])['story_premise'] == case['premise']
    assert 'workshop' in case['premise']


def test_model_failure_retains_raw_diagnostics_and_is_never_counted_as_valid():
    failure = LMStudioError('Invalid model JSON', code='invalid_model_output', diagnostics={'attempt_records': [{'response_text': 'bad JSON'}]})
    result = evaluate_case(fixtures()[0], Client(failure=failure), 'model-a')
    assert result['status'] == 'failed' and result['error_code'] == 'invalid_model_output'
    assert result['stage_records'][0]['diagnostics'] == failure.diagnostics
    assert result['stage_records'][0]['schema_validated'] is False
    assert summarize([result])['validated_model_cases'] == 0


def test_director_request_budget_matches_three_shots_with_three_actors(monkeypatch):
    from backend.scene_contract import director_output_budget, scene_contract_schema
    schema = {'type': 'object', 'properties': {'shots': {'type': 'array', 'minItems': 3,
        'items': {'type': 'object', 'properties': {'scene_contract': scene_contract_schema(['alex', 'mira', 'ivo'])}}}}}
    def request_director(**kwargs):
        kwargs['predict']('director', 'director', 'Stage three shots.', '{}', schema)
        raise ValueError('Budget probe ends before narrative validation.')
    monkeypatch.setattr('scripts.evaluate_game_assistant.plan_turn', request_director)
    client = Client(failure=LMStudioError('probe', code='probe'))
    result = evaluate_case(fixtures()[0], client, 'owned-test')
    assert director_output_budget(schema) == 2900
    assert client.calls[0][1]['max_tokens'] == 2900
    assert result['stage_records'][0]['max_tokens'] == 2900


def test_inspection_that_invents_a_pickup_is_not_reported_as_semantically_valid(monkeypatch):
    case = next(case for case in fixtures() if case['id'] == 'long_history')
    invalid = evaluate_case(case, Client(), 'mock-only')['plan']
    invalid['effects'] = [{'kind': 'holder', 'entity_id': 'key', 'character_id': 'alex'}]
    monkeypatch.setattr('scripts.evaluate_game_assistant.plan_turn', lambda **kwargs: copy.deepcopy(invalid))
    result = evaluate_case(case, Client(), 'model-a')
    assert result['status'] == 'invariant_failed'
    assert result['automatic_checks']['production_pipeline_validated'] is True
    assert result['automatic_checks']['inspection_preserves_possession'] is False


class Resources:
    def __init__(self, settings, client, *, state_path):
        self.settings, self.client, self.state_path = settings, client, state_path
        self.calls = []
        self.pending_load = None
    def run_ai(self, model, operation=None):
        self.calls.append(('prediction' if operation else 'prepare', model))
        return operation('owned:' + model) if operation else {'instance_id': 'owned:' + model}
    def prepare_h3(self):
        self.calls.append(('release_owned', self.settings()['model']))
        return {'ready': True}


def factories(client, created):
    def resources(*args, **kwargs):
        manager = Resources(*args, **kwargs)
        created.append(manager)
        return manager
    return {'client_factory': lambda *args, **kwargs: client, 'resource_factory': resources, 'emit': lambda *_: None}


def test_suite_compares_models_sequentially_under_owned_leases_and_saves_reports(tmp_path):
    created = []
    case = next(case for case in fixtures() if case['id'] == 'text_only_start')
    report = run_suite(['model-a', 'model-b'], [case], DEFAULT_SETTINGS, tmp_path / 'eval', **factories(Client(), created))
    assert report['complete'] and report['render_jobs'] == 0
    assert report['version'] == 2 and 'Version 1' in report['validation_scope']
    assert created[0].calls == [('prepare', 'model-a'), ('prediction', 'model-a'), ('release_owned', 'model-a'),
                                ('prepare', 'model-b'), ('prediction', 'model-b'), ('release_owned', 'model-b')]
    assert created[0].state_path == (tmp_path / 'eval' / 'resource_state.json').resolve()
    saved = json.loads((tmp_path / 'eval' / 'report.json').read_text(encoding='utf-8'))
    assert saved['complete'] and len(saved['models']) == 2
    assert len(list((tmp_path / 'eval').glob('model-*.json'))) == 2


def test_timeout_stops_all_further_models_and_preserves_instance_for_inspection(tmp_path):
    created = []
    client = Client(failure=LMStudioError('Still running', code='request_timeout'))
    report = run_suite(['model-a', 'model-b'], [fixtures()[0]], DEFAULT_SETTINGS, tmp_path / 'eval', **factories(client, created))
    assert not report['complete'] and len(report['models']) == 1
    assert len(client.calls) == 1
    assert created[0].calls == [('prepare', 'model-a'), ('prediction', 'model-a')]
    assert report['models'][0]['release']['released'] is False


def test_keyboard_interrupt_never_unloads_a_potentially_running_prediction(tmp_path):
    created = []
    client = Client(failure=KeyboardInterrupt())
    report = run_suite(['model-a', 'model-b'], [fixtures()[0]], DEFAULT_SETTINGS, tmp_path / 'eval', **factories(client, created))
    assert report['error_code'] == 'KeyboardInterrupt'
    assert report['models'][0]['release']['released'] is False
    assert created[0].calls == [('prepare', 'model-a'), ('prediction', 'model-a')]


def test_existing_user_model_and_unknown_model_are_rejected_before_loading(tmp_path):
    for index, (client, models) in enumerate([(Client(loaded=True), ['model-a']), (Client(), ['not-installed'])]):
        created = []
        report = run_suite(models, [fixtures()[0]], DEFAULT_SETTINGS, tmp_path / str(index), **factories(client, created))
        assert not report['complete'] and created == [] and client.calls == []


def test_existing_output_directory_is_never_overwritten(tmp_path):
    with pytest.raises(FileExistsError):
        run_suite(['model-a'], [fixtures()[0]], DEFAULT_SETTINGS, tmp_path)


def test_listing_cases_requires_no_model_or_network(capsys):
    assert main(['--list-cases']) == 0
    assert 'combat_attempt' in capsys.readouterr().out


def test_remote_endpoint_is_rejected_before_creating_output(tmp_path):
    target = tmp_path / 'untouched'
    with pytest.raises(SystemExit) as error:
        main(['--model', 'model-a', '--output', str(target), '--lm-url', 'https://example.com/v1'])
    assert error.value.code == 2 and not target.exists()


def test_compiler_gate_rejects_schema_valid_dialogue_with_blank_language(monkeypatch):
    case = fixtures()[0]
    baseline = evaluate_case(case, Client(), 'mock-only')
    narrative = copy.deepcopy(baseline['plan'])
    narrative['dialogue'] = [{'speaker': 'Mira', 'speaker_id': 'mira', 'text': 'I am Mira.',
                              'language': '', 'delivery': 'calm'}]
    narrative['direction']['shots'][0]['dialogue_indices'] = [0]
    validate_narrative(narrative, world=case['world'], player_character_id='alex',
                       message=case['message'], duration=case['duration'])
    prepared = copy.deepcopy(baseline['compilation']['project'])
    prepared['shots'][0]['dialogue'] = [{'id': 'blank-language', 'speaker_id': 'mira',
        'text': 'I am Mira.', 'language': '', 'delivery': 'calm', 'locked': True}]
    check_project(prepared)
    # Inject the old metadata gap at the prepared-project boundary. Even when
    # upstream language repair prevents it, the independent compiler gate must
    # reject a schema-valid plan which cannot produce a usable H3 prompt.
    monkeypatch.setattr('scripts.evaluate_game_assistant.plan_turn', lambda **kwargs: copy.deepcopy(narrative))
    monkeypatch.setattr('scripts.evaluate_game_assistant.direct_plan', lambda *args, **kwargs: copy.deepcopy(prepared))
    result = evaluate_case(case, Client(), 'mock-only')
    assert result['status'] == 'invariant_failed'
    assert result['automatic_checks']['production_pipeline_validated'] is True
    assert result['automatic_checks']['compiled_prompt_ready'] is False
    assert result['compilation']['valid'] is False and result['compilation']['prompt'] == ''
    assert any(issue['severity'] == 'error' and issue['path'].endswith('.language') for issue in result['compilation']['issues'])
    assert summarize([result])['compiler_failed_cases'] == 1
    assert summarize([result])['validated_model_cases'] == 0


def test_language_metadata_stage_has_bounded_budget_and_retained_evidence(monkeypatch):
    case = fixtures()[0]
    narrative = evaluate_case(case, Client(), 'mock-only')['plan']
    def planned(**kwargs):
        result = kwargs['predict']('language', 'dialogue', 'Name the language only.',
            json.dumps({'dialogue': [{'index': 0, 'speaker': 'Mira', 'text': 'Hello.', 'language': ''}],
                        'missing_language_indices': [0]}),
            {'type': 'object', 'properties': {'languages': {'type': 'object',
                'properties': {'0': {'type': 'string'}}, 'required': ['0'], 'additionalProperties': False}},
             'required': ['languages'], 'additionalProperties': False})
        assert result == {'languages': {'0': 'English'}}
        return copy.deepcopy(narrative)
    monkeypatch.setattr('scripts.evaluate_game_assistant.plan_turn', planned)
    client = Client()
    result = evaluate_case(case, client, 'mock-only', temperature=.8, budgets={'actor': 123})
    assert result['status'] == 'validated'
    assert client.calls[0][1]['max_tokens'] == 350
    assert client.calls[0][1]['temperature'] == .2
    assert result['stage_records'][0]['stage'] == 'language'
    assert result['stage_records'][0]['schema_validated'] is True
