"""Incorrect inferred labels cannot turn quoted speech into H3 language metadata."""
import copy
import json

import pytest

from backend.compiler import compile_project
from backend.game_director import _complete_dialogue_languages, _inferred_language, _player_language, direct_plan, plan_turn, validate_narrative
from test_game_director import coordinator, direction, narrative, setup_scene


@pytest.mark.parametrize('label', ['Who are you?', '“Who are you?”', 'who ARE you?',
                                   'I am speaking English', 'The language is English',
                                   'What language is this', 'Please answer!', '誰ですか？', ''])
def test_invalid_inferred_player_language_does_not_fail_or_change_dialogue(label):
    project, world = setup_scene()
    project['mode'] = 't2va'
    world['characters'][0]['speaking_style'] = ''
    calls = []
    def predict(stage, actor, system, content, schema):
        calls.append((stage, json.loads(content)))
        if stage == 'actor':
            return {'character_id': actor, 'action': 'Mira points to herself.',
                    'dialogue': [{'text': 'Mira.', 'language': 'en', 'delivery': 'quiet'}],
                    'intent': 'Answer with her name.'}
        if stage == 'roleplay':
            result = coordinator()
            result['player_language'] = label
            return result
        if stage == 'language':
            assert json.loads(content)['missing_language_indices'] == [0]
            return {'languages': {'0': 'en'}}
        return direction()
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I ask "Who are you?"', duration=5, predict=predict)
    assert [entry[0] for entry in calls] == ['actor', 'roleplay', 'language', 'director']
    assert result['dialogue'][0]['text'] == 'Who are you?'
    assert result['dialogue'][0]['language'] == 'English'
    assert result['dialogue'][1]['text'] == 'Mira.'
    assert result['dialogue'][1]['language'] == 'English'
    assert calls[-1][1]['dialogue'][0]['language'] == 'English'
    compiled = compile_project(direct_plan(result, project, duration=5))
    assert compiled['valid'], compiled['issues']
    assert '<d>[English] Who are you?</d>' in compiled['prompt']


@pytest.mark.parametrize(('label', 'expected'), [('en', 'English'), ('Albanian', 'Albanian'),
    ('pt-BR', 'pt-BR'), ('zh-Hant-TW', 'zh-Hant-TW'), ('Swiss German', 'Swiss German'),
    ('Bahasa Indonesia', 'Bahasa Indonesia'), ('中文', '中文'), ('English (U.S.)', 'English (U.S.)')])
def test_inferred_languages_preserve_names_dialects_and_codes(label, expected):
    assert _inferred_language(label, ['Who are you?']) == expected


def test_quote_copy_without_question_punctuation_is_not_a_language():
    assert _inferred_language('  Good morning  ', ['Good morning']) == ''


@pytest.mark.parametrize('key', ['game_language', 'dialogue_language'])
def test_explicit_user_language_is_preserved_and_overrides_bad_history(key):
    project, world = setup_scene()
    project[key] = 'Who are you?'
    original = copy.deepcopy(project)
    assert _player_language(project, world['characters'][0]) == 'Who are you?'
    assert project == original


def test_previous_language_is_never_a_preference_for_current_auto_dialogue():
    project, world = setup_scene()
    player = world['characters'][0]
    player['speaking_style'] = ''
    project['shots'][0]['dialogue'] = [{'speaker_id': player['id'], 'text': 'Who are you?',
                                      'language': 'Who are you?'}]
    assert _player_language(project, player) == ''
    project['shots'][0]['dialogue'][0]['language'] = 'sq'
    assert _player_language(project, player) == ''


def test_previous_english_does_not_override_current_albanian_or_need_an_extra_call():
    project, world = setup_scene()
    world['characters'][0]['speaking_style'] = ''
    project['mode'] = 't2va'
    project['shots'][0]['dialogue'] = [{'id': 'previous-line', 'speaker_id': 'player', 'text': 'Where am I?', 'language': 'English'}]
    calls = []
    def predict(stage, actor, system, content, schema):
        calls.append(stage)
        if stage == 'actor':
            return {'character_id': actor, 'action': 'Mira points at the workshop.',
                    'dialogue': [{'text': 'Këtu.', 'language': 'sq', 'delivery': 'quiet'}], 'intent': 'Answer.'}
        if stage == 'roleplay':
            assert 'player_language' in schema['required']
            return dict(coordinator(), player_language='sq')
        assert stage == 'director'
        return direction()
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I ask "Ku jemi?"', duration=5, predict=predict)
    assert calls == ['actor', 'roleplay', 'director']
    compiled = compile_project(direct_plan(result, project, duration=5))
    assert compiled['valid'], compiled['issues']
    assert '<d>[Albanian] Ku jemi?</d>' in compiled['prompt']
    assert '<d>[English]' not in compiled['prompt']


@pytest.mark.parametrize('response', [{'languages': {}}, {'languages': {'0': 'en', '2': 'fr'}},
    {'languages': {'0': 'Unknown'}}, {'languages': {'0': 'Who are you?'}},
    {'languages': {'0': 'I am speaking English'}}])
def test_language_completion_is_bounded_requires_exact_coverage_and_preserves_text_on_failure(response):
    plan = {'dialogue': [{'speaker': 'Alex', 'text': 'Who are you?', 'language': ''}]}
    original = copy.deepcopy(plan)
    calls = []
    def predict(*args):
        calls.append(args)
        return response
    with pytest.raises(ValueError):
        _complete_dialogue_languages(plan, predict)
    assert len(calls) == 1
    assert plan == original


def test_edited_blank_language_resolves_without_redirection_or_changing_explicit_other_line():
    project, world = setup_scene()
    project['mode'] = 't2va'
    plan = narrative()
    plan['dialogue'][0]['language'] = ''
    plan['dialogue'][1]['language'] = 'Swiss German'
    plan = validate_narrative(plan, world=world, player_character_id='player', message='"Ku jemi?"', duration=5)
    plan['direction'] = direction()
    original = copy.deepcopy(plan)
    calls = []
    def predict(stage, actor, system, content, schema):
        calls.append(stage)
        assert actor == 'dialogue' and json.loads(content)['missing_language_indices'] == [0]
        return {'languages': {'0': 'sq'}}
    directed = direct_plan(plan, project, duration=5, predict=predict)
    assert calls == ['language'] and plan == original
    assert [line['text'] for line in directed['shots'][0]['dialogue']] == [line['text'] for line in original['dialogue']]
    assert [line['language'] for line in directed['shots'][0]['dialogue']] == ['Albanian', 'Swiss German']
    compiled = compile_project(directed)
    assert compiled['valid'], compiled['issues']


def test_missing_language_in_external_draft_has_specific_compile_recovery():
    project, world = setup_scene()
    project['mode'] = 't2va'
    plan = narrative()
    plan['dialogue'][0]['language'] = ''
    plan = validate_narrative(plan, world=world, player_character_id='player', message='"Ku jemi?"', duration=5)
    plan['direction'] = direction()
    result = compile_project(direct_plan(plan, project, duration=5))
    assert not result['valid']
    issue = next(issue for issue in result['issues'] if issue['code'] == 'missing_dialogue_language')
    assert 'Choose a spoken language' in issue['message']
    assert not result['prompt']


def test_text_only_newcomer_with_invalid_language_metadata_reaches_compiler_without_assets():
    from test_game_director_robustness import augmented_predictor, scene
    project, world = scene(())
    project['mode'] = 't2va'
    project['comfy_render'] = {'experimental_preview': True, 'resolution': '0.2'}
    world['characters'][0]['speaking_style'] = ''
    calls = []
    raw = {}
    def edit(plan):
        plan['player_language'] = 'Who are you?'
        plan['new_characters'] = [{'name': 'Elena', 'description': 'The same woman already visible by the shop.',
            'voice': 'warm', 'dialogue': [{'text': 'I am Elena.', 'language': 'I am Elena', 'delivery': 'quiet'}]}]
        plan['beats'][0].update(action='The woman turns to Alex and introduces herself.', final_state='Elena remains beside the shop.')
        raw.update(copy.deepcopy(plan))
    base = augmented_predictor(calls, edit)
    metadata_calls = []
    def predict(stage, actor, system, content, schema):
        if stage == 'language':
            metadata_calls.append(json.loads(content))
            return {'languages': {'0': 'en', '1': 'en'}}
        return base(stage, actor, system, content, schema)
    result = plan_turn(project=project, world=world, player_character_id='player',
                       message='I ask "Who are you?"', duration=3, predict=predict)
    assert len(metadata_calls) == 1 and metadata_calls[0]['missing_language_indices'] == [0, 1]
    assert raw['player_language'] == 'Who are you?'  # Recorded model output is intact.
    assert result['asset_requests'] == result['effects'] == []
    compiled = compile_project(direct_plan(result, project, duration=3))
    assert compiled['valid'], compiled['issues']
    assert '<d>[English] Who are you?</d>' in compiled['prompt']
    assert '<d>[English] I am Elena.</d>' in compiled['prompt']
