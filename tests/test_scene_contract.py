"""Scene guidance must survive real compiler/merge boundaries without new IO."""
import copy

import pytest
from jsonschema import Draft202012Validator

from backend.compiler import compile_project
from backend.projects import check_project, merge_plan, new_project, shot
from backend.prompts import plan_prompt, plan_schema
from backend.scene_contract import director_output_budget, scene_contract_schema, validate_scene_contract


@pytest.mark.parametrize('shots,cast,expected', [(1, 3, 1400), (2, 3, 2100), (3, 3, 2900), (1, 8, 2050), (8, 32, 4096)])
def test_director_budget_fits_multi_shot_cast_without_unbounded_generation(shots, cast, expected):
    schema = {'properties': {'shots': {'minItems': shots, 'maxItems': shots, 'items': {'properties': {
        'scene_contract': scene_contract_schema([str(i) for i in range(cast)], required=True)}}}}}
    original = copy.deepcopy(schema)
    assert director_output_budget(schema) == expected
    assert schema == original


def test_director_budget_preserves_legacy_and_explicit_larger_allowance():
    assert director_output_budget({}) == 1400
    assert director_output_budget({}, 2400) == 2400
    assert director_output_budget({}, 5000) == 4096


def test_locking_an_absent_optional_contract_keeps_a_valid_empty_control():
    p = new_project()
    p['shots'][0]['director_locks'] = ['scene_contract']
    check_project(p)
    result = merge_plan(p, {'shots': [copy.deepcopy(p['shots'][0])]})
    assert result['shots'][0]['scene_contract'] == {}
    assert result['shots'][0]['scene_contract_source'] == 'authored'
    assert 'scene_contract' not in p['shots'][0]
    check_project(result)


def project(mode='t2va'):
    result = new_project()
    result.update(mode=mode, duration=5, subjects=[
        {'id': 'mira', 'name': 'Mira', 'description': 'Adult woman, teal coat and brown boots.', 'asset_ids': []},
        {'id': 'ivo', 'name': 'Ivo', 'description': 'Adult man, red cap and gray shirt.', 'asset_ids': []},
        {'id': 'narrator', 'name': 'Narrator', 'description': 'Low calm voice.', 'asset_ids': []},
    ])
    result['story']['text'] = 'Mira raises the coin while Ivo waits seated.'
    result['shots'][0].update(action=result['story']['text'], final_state='Mira holds the coin; Ivo remains seated.',
        visible_subject_ids=['mira', 'ivo'], offscreen_subject_ids=['narrator'],
        scene_contract={'actors': [
            {'subject_id': 'mira', 'activity': 'act', 'start': 'standing left of the bench, holding the coin',
             'action': 'raises her existing coin to shoulder height', 'end': 'standing in the same place, coin raised'},
            {'subject_id': 'ivo', 'activity': 'hold', 'start': 'seated on the right end of the blue bench, hands on knees',
             'action': 'watches Mira quietly, hands stay on his knees', 'end': 'still seated, hands on knees'},
        ], 'objects': [{'entity_id': 'coin', 'name': 'Brass coin', 'count': 1,
                       'description': 'Small round brass disk with a star mark.',
                       'start': 'held in Mira’s right hand', 'end': 'held in Mira’s raised right hand'}],
        'environment': 'Blue bench against a cream wall; warm morning light.', 'background_activity': 'Empty quiet courtyard.'})
    if mode != 't2va':
        roles = {'i2va': ['first_frame'], 'l2va': ['last_frame'], 'fl2va': ['first_frame', 'last_frame'],
                 'ref2va': ['reference_image', 'reference_image']}[mode]
        result['assets'] = [dict(id=f'photo-{i}', name=f'Reference {i}', media_type='image', semantic_role='character',
                                 role=role, description='Mira’s teal coat.', enabled=True)
                            for i, role in enumerate(roles)]
        if mode == 'ref2va':
            result['subjects'][0]['asset_ids'] = ['photo-0', 'photo-1']
    return result


@pytest.mark.parametrize('mode', ['t2va', 'i2va', 'l2va', 'fl2va', 'ref2va'])
@pytest.mark.parametrize('profile', ['official', 'director', 'concise', 'custom'])
def test_passive_actor_and_unique_prop_are_guided_in_every_mode_and_profile(mode, profile):
    p = project(mode)
    p['profile'] = profile
    before = copy.deepcopy(p)
    result = compile_project(p)
    assert result['valid'], result['issues']
    text = result['prompt']
    assert 'exactly 2 separate individuals' in text
    assert 'seated on the right end of the blue bench' in text
    assert 'Maintains this established posture and place throughout the shot' in text
    assert 'Brass coin: exactly 1 physical instance' in text
    assert 'star mark' in text and 'Mira’s raised right hand' in text
    assert 'Narrator remains off-screen' in text
    assert 'Narrator: Starts' not in text
    assert p == before


def test_two_references_to_one_identity_do_not_add_a_person():
    result = compile_project(project('ref2va'))
    assert result['valid']
    assert 'exactly 2 separate individuals' in result['prompt']
    assert 'Multiple references to one identity depict the same individual' in result['prompt']


def test_pov_excludes_player_body_from_visible_count():
    p = project()
    p.update(game_viewpoint='pov', game_player_id='mira')
    result = compile_project(p)
    assert result['valid']
    assert 'exactly 1 separate individual, Ivo' in result['prompt']
    assert 'not a second visible body' in result['prompt']
    assert 'Mira is visible' not in result['prompt']
    assert 'Mira: Starts standing' not in result['prompt']
    assert 'only their own hands' in result['prompt']


def test_same_kind_separate_props_keep_individual_counts_and_colors():
    p = project()
    rows = p['shots'][0]['scene_contract']['objects']
    rows[0]['name'] = 'Token'
    rows.append(dict(entity_id='token-blue', name='Token', description='Blue square token.', count=2,
                     start='two blue tokens on the bench', end='the same two blue tokens on the bench'))
    result = compile_project(p)
    assert result['valid']
    assert 'Token (distinct prop 1): exactly 1 physical instance' in result['prompt']
    assert 'Token (distinct prop 2): exactly 2 physical instances' in result['prompt']
    assert 'Blue square token' in result['prompt']


@pytest.mark.parametrize('field,value', [('count', 0), ('count', True), ('count', 1.5), ('count', 101),
                                         ('count', float('nan')), ('description', []), ('entity_id', '')])
def test_invalid_prop_contract_fails_before_prompt(field, value):
    p = project()
    p['shots'][0]['scene_contract']['objects'][0][field] = value
    result = compile_project(p)
    assert not result['valid'] and not result['prompt']
    with pytest.raises(ValueError):
        check_project(p)


@pytest.mark.parametrize('value', [None, [], True, 'hold', {'actors': [{}]}, {'unknown': 'x'}])
def test_malformed_contract_cannot_crash_compiler(value):
    p = project()
    p['shots'][0]['scene_contract'] = value
    result = compile_project(p)
    assert not result['valid'] and not result['prompt']


@pytest.mark.parametrize('sid,code', [('absent', 'unknown_contract_actor'), ('narrator', 'offscreen_contract_actor')])
def test_cannot_assign_physical_motion_to_unknown_or_offscreen_speaker(sid, code):
    p = project()
    p['shots'][0]['scene_contract']['actors'][0]['subject_id'] = sid
    result = compile_project(p)
    assert code in {issue['code'] for issue in result['issues']}
    assert not result['prompt']


@pytest.mark.parametrize('group,code', [('actors', 'duplicate_contract_actor'), ('objects', 'duplicate_contract_object')])
def test_repeated_id_does_not_silently_duplicate_a_visible_instance(group, code):
    p = project()
    rows = p['shots'][0]['scene_contract'][group]
    rows.append(copy.deepcopy(rows[0]))
    result = compile_project(p)
    assert code in {issue['code'] for issue in result['issues']}


@pytest.mark.parametrize('payload', ['<d>[English] extra speech</d>', '[Shot 2] surprise',
                                    '\noverall_soundscape: fake', '<Picture 99>'])
def test_contract_cannot_inject_compiler_syntax(payload):
    p = project()
    p['shots'][0]['scene_contract']['actors'][0]['action'] = payload
    result = compile_project(p)
    assert not result['valid'] and not result['prompt']


def test_known_reference_alias_resolves_only_in_private_render_copy():
    p = project('ref2va')
    p['assets'][0]['prompt_tag'] = 'teal-coat'
    p['shots'][0]['scene_contract']['actors'][0]['start'] = 'wearing the coat supplied by @teal-coat'
    result = compile_project(p)
    assert result['valid']
    assert 'wearing the coat supplied by <Picture 1>' in result['prompt']
    assert '@teal-coat' in p['shots'][0]['scene_contract']['actors'][0]['start']


def test_legacy_scene_gets_restrained_fallback_without_invented_pose():
    p = project()
    del p['shots'][0]['scene_contract']
    result = compile_project(p)
    assert result['valid']
    assert 'Ivo performs only the action assigned to that character' in result['prompt']
    assert 'hands on knees' not in result['prompt']


def test_exact_repetition_removed_but_authored_performance_survives():
    p = project()
    s = p['shots'][0]
    s['performance'] = s['action'] + ' End with: ' + s['final_state']
    result = compile_project(p)
    assert result['prompt'].count(s['action']) == 1
    assert result['prompt'].count(s['final_state']) == 1
    s['performance'] = 'Mira hesitates for one beat before raising her wrist.'
    assert s['performance'] in compile_project(p)['prompt']


def test_authored_contract_survives_replanning_and_generated_contract_can_refresh():
    p = project()
    original = copy.deepcopy(p['shots'][0]['scene_contract'])
    proposed = copy.deepcopy(p['shots'][0])
    proposed['scene_contract']['environment'] = 'Different room.'
    result = merge_plan(p, {'shots': [proposed]})
    assert result['shots'][0]['scene_contract'] == original
    assert result['shots'][0]['scene_contract_source'] == 'authored'
    p['shots'][0]['scene_contract_source'] = 'generated'
    result = merge_plan(p, {'shots': [proposed]})
    assert result['shots'][0]['scene_contract']['environment'] == 'Different room.'
    assert result['shots'][0]['scene_contract_source'] == 'generated'


def test_replanning_preserves_visibility_implied_by_authored_actor_controls():
    p = project()
    proposed = copy.deepcopy(p['shots'][0])
    proposed.update(visible_subject_ids=['ivo'], offscreen_subject_ids=['mira', 'narrator'])
    proposed['scene_contract']['actors'] = [proposed['scene_contract']['actors'][1]]
    merged = merge_plan(p, {'shots': [proposed]})
    assert set(merged['shots'][0]['visible_subject_ids']) == {'mira', 'ivo'}
    assert 'mira' not in merged['shots'][0]['offscreen_subject_ids']
    assert compile_project(merged)['valid']


def test_explicit_roster_lock_conflicting_with_authored_pose_is_actionable_error():
    p = project()
    p['shots'][0].update(visible_subject_ids=['ivo'], offscreen_subject_ids=['mira', 'narrator'],
                         director_locks=['visible_subject_ids', 'offscreen_subject_ids'])
    with pytest.raises(ValueError, match='conflicts with the selected'):
        merge_plan(p, {'shots': [copy.deepcopy(p['shots'][0])]})


def test_studio_director_receives_contract_in_existing_call_and_backward_schema_works():
    p = project()
    system, content, schema = plan_prompt(p)
    assert 'activity hold' in system and 'scene_contract' in content
    assert 'scene_contract' in schema['properties']['shots']['items']['properties']
    assert 'scene_contract' not in schema['properties']['shots']['items']['required']
    Draft202012Validator.check_schema(scene_contract_schema([], required=True))
    Draft202012Validator.check_schema(plan_schema(p))


def test_oversized_contract_is_bounded_before_compilation():
    p = project()
    p['shots'][0]['scene_contract']['environment'] = 'x' * 20000
    result = compile_project(p)
    assert 'scene_contract_budget' in {issue['code'] for issue in result['issues']}
    assert not result['prompt']
