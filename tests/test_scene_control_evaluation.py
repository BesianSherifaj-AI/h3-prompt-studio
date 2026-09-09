"""Offline fixture/check regressions. These are not evidence of live model quality."""
import copy
import json

import pytest

from backend.projects import check_project
from backend.stories import _stage_scene_contracts
from backend.world import validate_world
from scripts import evaluate_scene_control as evaluation


def fixture(case_id):
    return next(case for case in evaluation.fixtures() if case['id'] == case_id)


def assembled(case):
    """Synthetic evidence solely for exercising checks, with no model score."""
    project = copy.deepcopy(case['project'])
    expected = case['scene_expectations']
    project['shots'][0].update(visible_subject_ids=list(expected['visible_ids']), offscreen_subject_ids=list(expected['offscreen_ids']))
    project['shots'][0]['camera']['movement'] = 'pan right'
    plan = {'action': case['message'], 'effects': [], 'dialogue': [], 'actor_actions': []}
    if case['id'] == 'single_prop_handoff':
        plan['effects'] = [{'kind': 'holder', 'entity_id': 'token-star', 'character_id': 'mira'}]
    _stage_scene_contracts(project, case['world'], plan, 'alex', game_mode=case['mode'] == 'game')
    return plan, project


def test_fixtures_are_stable_public_safe_and_unplanned():
    cases = evaluation.fixtures()
    assert cases == evaluation.fixtures() and len(cases) == 6
    assert len({case['id'] for case in cases}) == 6
    for case in cases:
        check_project(case['project'])
        validate_world(case['world'])
        assert case['project']['assets'] == []
        assert not case.get('plan') and not case['project']['shots'][0].get('scene_contract')
        assert case['world']['characters'][2]['state']['posture'] == 'seated'


def test_default_and_prepare_are_offline_even_when_model_was_supplied(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation.game, 'LMStudioClient', lambda *_args, **_kwargs: pytest.fail('Unexpected network client'))
    monkeypatch.setattr(evaluation.game, 'run_suite', lambda *_args, **_kwargs: pytest.fail('Unexpected execution'))
    assert evaluation.main([]) == 0
    output = tmp_path / 'prepared'
    assert evaluation.main(['--model', 'unused-local-key', '--output', str(output)]) == 0
    value = json.loads((output / 'prepared.json').read_text())
    assert value['model_calls'] == value['render_jobs'] == 0
    assert value['status'] == 'prepared_offline'
    assert len(value['fixtures']) == 6 and value['source_hashes']
    with pytest.raises(FileExistsError):
        evaluation.main(['--output', str(output)])


def test_execute_needs_explicit_model_and_output():
    with pytest.raises(SystemExit) as exc:
        evaluation.main(['--execute'])
    assert exc.value.code == 2


@pytest.mark.parametrize('case_id', [case['id'] for case in evaluation.fixtures()])
def test_checks_accept_correct_synthetic_canonical_assembly(case_id):
    case = fixture(case_id)
    plan, project = assembled(case)
    assert all(evaluation.scene_checks(case, plan, project).values())


@pytest.mark.parametrize('defect,failed_check', [
    ('missing_bystander', 'expected_visible_cast_preserved'),
    ('standing_bystander', 'passive_actor_activity_and_posture'),
    ('wrong_holder', 'canonical_object_holders_match'),
    ('wrong_count', 'required_prop_ids_counts_and_marks'),
    ('wrong_appearance', 'known_actor_appearance_retained'),
    ('wrong_endpoint', 'prop_endpoint_instructions_match_holders'),
])
def test_checks_retain_concrete_scene_failures(defect, failed_check):
    case = fixture('passive_seated_bystander')
    plan, project = assembled(case)
    shot = project['shots'][0]
    contract = shot['scene_contract']
    if defect == 'missing_bystander':
        shot['visible_subject_ids'].remove('ivo')
    elif defect == 'standing_bystander':
        row = next(row for row in contract['actors'] if row['subject_id'] == 'ivo')
        row.update(activity='act', end='standing')
    elif defect == 'wrong_holder':
        plan['effects'] = [{'kind': 'holder', 'entity_id': 'token-star', 'character_id': 'alex'}]
    elif defect == 'wrong_count':
        contract['objects'][0]['count'] = 2
    elif defect == 'wrong_appearance':
        project['subjects'][0]['description'] = 'A different outfit.'
    elif defect == 'wrong_endpoint':
        contract['objects'][0]['end'] = 'Held by Alex.'
    assert evaluation.scene_checks(case, plan, project)[failed_check] is False


def test_same_kind_items_cannot_collapse_to_one_identity():
    case = fixture('two_distinct_tokens')
    plan, project = assembled(case)
    objects = project['shots'][0]['scene_contract']['objects']
    assert len(objects) == 2 and objects[0]['name'] == objects[1]['name']
    assert objects[0]['entity_id'] != objects[1]['entity_id']
    objects.pop()
    assert not evaluation.scene_checks(case, plan, project)['required_prop_ids_counts_and_marks']


def test_offscreen_voice_and_camera_checks_do_not_use_cast_presence_as_success():
    case = fixture('offscreen_voice')
    plan, project = assembled(case)
    project['shots'][0]['visible_subject_ids'].append('mira')
    assert not evaluation.scene_checks(case, plan, project)['offscreen_voices_remain_offscreen']
    case = fixture('camera_only')
    plan, project = assembled(case)
    plan['effects'] = [{'kind': 'character_state', 'character_id': 'alex', 'key': 'posture', 'value': 'walking'}]
    assert not evaluation.scene_checks(case, plan, project)['camera_only_no_dialogue_or_world_effects']


def decor(name, eid, count=1):
    return {'entity_id': eid, 'name': name, 'count': count, 'description': 'Existing fixture furnishing.',
            'start': 'Established place.', 'end': 'Established place.'}


def test_camera_only_accepts_exact_authored_furniture_without_requiring_it():
    case = fixture('camera_only')
    plan, project = assembled(case)
    rows = project['shots'][0]['scene_contract']['objects']
    rows.extend([decor('wooden workbench', 'bench'), decor('stool', 'seat'), decor('Empty Doorway', 'doorway')])
    assert evaluation.SCENE_EVALUATION_VERSION == 3
    assert all(evaluation.scene_checks(case, plan, project).values())


@pytest.mark.parametrize('extras', [
    [decor('workshop', 'room')],
    [decor('workbench', 'bench', 2)],
    [decor('workbench', 'bench'), decor('wooden workbench', 'bench-alias')],
    [decor('empty doorway', 'arch', 2)],
    [decor('empty doorway', 'arch'), decor('doorway', 'doorway-alias')],
    [decor('locked door', 'door')],
    [decor('Brass token', 'token-alias')],
    [decor('Brass token', 'token-star')],
    [decor('new cabinet', 'cabinet')],
])
def test_camera_only_whitelist_cannot_admit_rooms_duplicates_or_new_props(extras):
    case = fixture('camera_only')
    plan, project = assembled(case)
    project['shots'][0]['scene_contract']['objects'].extend(extras)
    assert not evaluation.scene_checks(case, plan, project)['no_unrequested_or_duplicate_prop_instances']
