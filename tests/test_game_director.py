import copy
import json
import pytest

from backend.game_director import GAME_ENGINE_SYSTEM, direct_plan, plan_turn, validate_narrative
from backend.projects import new_project
from backend.world import validate_world


def setup_scene():
    p = new_project()
    p['subjects'] = [{'id': 'player', 'name': 'Alex', 'description': 'Blue coat', 'asset_ids': []},
                     {'id': 'npc', 'name': 'Mira', 'description': 'Green jacket', 'asset_ids': []}]
    w = validate_world({'schema_version': 1, 'current_location_id': 'room',
        'locations': [{'id': 'room', 'name': 'Workshop'}],
        'characters': [{'id': 'player', 'name': 'Alex', 'control': 'player', 'speaking_style': 'Albanian, calm'},
                       {'id': 'npc', 'name': 'Mira', 'private_knowledge': ['The door hides a safe.'], 'speaking_style': 'Albanian, soft'}]})
    return p, w


def narrative():
    return {'action': 'Mira turns toward Alex and points at the window.', 'setting': 'Inside the workshop.',
        'final_state': 'Mira faces the window beside Alex.', 'transition': 'continue',
        'dialogue': [{'speaker': 'Alex', 'text': 'Ku jemi?', 'language': 'Albanian'},
                     {'speaker': 'Mira', 'text': 'Në punishten time.', 'language': 'Albanian'}],
        'characters': [{'name': 'Alex', 'description': 'Blue coat', 'voice': 'calm'},
                       {'name': 'Mira', 'description': 'Green jacket', 'voice': 'soft'}],
        'asset_requests': [], 'effects': [],
        'choices': [{'title': 'Window', 'message': 'I examine the window.'},
                    {'title': 'Question', 'message': 'I ask about the workshop.'},
                    {'title': 'Wait', 'message': 'I wait by the workbench.'}],
        'beats': [{'id': 'beat-1', 'action': 'Mira turns toward Alex and points at the window.',
                   'setting': 'Inside the workshop.', 'final_state': 'Mira faces the window beside Alex.'}]}



def coordinator():
    original = narrative()
    return {**{k: copy.deepcopy(original[k]) for k in ('transition', 'beats', 'effects', 'choices', 'asset_requests')},
            'new_characters': []}


def direction(duration=5):
    return {'shots': [{'beat_id': 'beat-1', 'duration': duration,
        'camera': {'framing': 'medium two-shot', 'movement': 'slow pan toward the window',
                   'height': 'eye level', 'speed': 'slow', 'focus': 'Mira and Alex remain sharp'},
        'performance': 'Mira keeps a calm expression; her pointing hand remains clearly visible.',
        'sound': 'Quiet workshop ambience.', 'visible_subject_ids': ['player', 'npc'],
        'offscreen_subject_ids': [], 'dialogue_indices': [0, 1], 'transition': 'continuous'}]}


def test_legacy_plan_validates_without_rewriting_or_forcing_language():
    p, w = setup_scene()
    plan = narrative()
    plan.pop('beats'); plan.pop('effects')
    for line in plan['dialogue']:
        line.pop('language')
    result = validate_narrative(plan, world=w, player_character_id='player', message='I ask "Ku jemi?"', duration=5)
    assert result['dialogue'][0]['text'] == 'Ku jemi?' and result['dialogue'][0]['language'] == ''
    assert result['dialogue'][0]['speaker_id'] == 'player'
    assert result['effects'] == [] and len(result['beats']) == 1
    assert 'beats' not in plan


@pytest.mark.parametrize('action', ['continue', 'Continue the story.', 'choose whatever happens next', 'Surprise me'])
def test_bare_continuation_is_rejected(action):
    _, w = setup_scene()
    value = narrative(); value['action'] = action
    with pytest.raises(ValueError, match='vague continuation'):
        validate_narrative(value, world=w, player_character_id='player', message='"Ku jemi?"', duration=5)


def test_exact_speech_unknown_speaker_and_too_many_words_are_rejected():
    _, w = setup_scene()
    for edit, match in [(lambda x: x['dialogue'][0].update(text='Where are we?'), 'changed or invented'),
                        (lambda x: x['dialogue'][1].update(speaker='Someone else'), 'known speaker'),
                        (lambda x: x['dialogue'][1].update(text='word ' * 30), 'does not fit')]:
        value = narrative(); edit(value)
        with pytest.raises(ValueError, match=match):
            validate_narrative(value, world=w, player_character_id='player', message='"Ku jemi?"', duration=5)


def test_existing_face_cannot_be_regenerated():
    _, w = setup_scene(); w['characters'][1]['asset_ids'] = ['face']
    value = narrative()
    value['asset_requests'] = [{'name': 'New face', 'prompt': 'Mira portrait', 'semantic_role': 'character', 'person_name': 'Mira', 'prompt_tag': 'mira'}]
    with pytest.raises(ValueError, match='silently regenerated'):
        validate_narrative(value, world=w, player_character_id='player', message='"Ku jemi?"', duration=5)


def test_actor_roleplay_director_are_separate_requests_and_no_secret_leaks():
    p, w = setup_scene()
    p['subjects'][1]['private_knowledge'] = ['This project-only secret must never reach the director.']
    calls = []
    def predict(stage, actor, system, content, schema):
        assert system.startswith(GAME_ENGINE_SYSTEM)
        calls.append((stage, actor, json.loads(content)))
        if stage == 'actor':
            return {'character_id': 'npc', 'action': 'Mira points toward the workshop window.',
                    'dialogue': [{'text': 'Në punishten time.', 'language': 'Albanian', 'delivery': 'soft'}], 'intent': 'Answer the question.'}
        return coordinator() if stage == 'roleplay' else direction()
    value = plan_turn(project=p, world=w, player_character_id='player', message='I ask "Ku jemi?"', duration=5, predict=predict)
    assert [(s, a) for s, a, _ in calls] == [('actor', 'npc'), ('roleplay', 'world'), ('director', 'director')]
    assert 'safe' in str(calls[0][2]['context'])
    assert 'safe' not in str(calls[1][2]) and 'safe' not in str(calls[2][2])
    assert 'project-only secret' not in str(calls[2][2])
    expected_direction = direction()
    expected_direction['shots'][0]['performance'] = value['beats'][0]['action'] + ' End with: ' + value['beats'][0]['final_state']
    actual_direction = copy.deepcopy(value['direction'])
    contract = actual_direction['shots'][0].pop('scene_contract')
    assert actual_direction['shots'][0].pop('scene_contract_source') == 'generated'
    assert actual_direction == expected_direction
    assert {row['subject_id'] for row in contract['actors']} == {'player', 'npc'}
    assert all(row['activity'] == 'act' for row in contract['actors'])
    assert 'private' not in json.dumps(contract).lower()
    rendered = direct_plan(value, p, duration=5)
    assert rendered['shots'][0]['action'] == narrative()['action']
    assert rendered['shots'][0]['camera']['movement'] == 'slow pan toward the window'
    assert [x['text'] for x in rendered['shots'][0]['dialogue']] == ['Ku jemi?', 'Në punishten time.']


def test_second_actor_receives_first_public_response_not_private_knowledge():
    p, w = setup_scene()
    w['characters'].append({'id': 'npc2', 'name': 'Ben', 'private_knowledge': ['Private Ben fact.']})
    w = validate_world(w)
    seen = []
    def predict(stage, actor, system, content, schema):
        request = json.loads(content)
        if stage == 'actor':
            seen.append(request)
            return {'character_id': actor, 'action': 'The character turns toward the window.', 'dialogue': [], 'intent': 'Observe.'}
        if stage == 'roleplay':
            result = coordinator()
            return result
        result = direction(); result['shots'][0]['dialogue_indices'] = [0]
        return result
    plan_turn(project=p, world=w, player_character_id='player', message='"Ku jemi?"', duration=5, predict=predict)
    assert seen[0]['heard_responses'] == []
    assert seen[1]['heard_responses'][0]['character_id'] == 'npc'
    assert 'safe' not in str(seen[1]) and 'Private Ben fact' not in str(seen[0])


def test_roleplay_cannot_rewrite_actor_reply_or_language():
    p, w = setup_scene()
    def predict(stage, actor, system, content, schema):
        if stage == 'actor':
            return {'character_id': actor, 'action': 'Mira looks at the window.', 'dialogue': [{'text': 'Hello.', 'language': 'English', 'delivery': 'quiet'}], 'intent': 'Answer.'}
        return coordinator() if stage == 'roleplay' else direction()
    result = plan_turn(project=p, world=w, player_character_id='player', message='"Ku jemi?"', duration=5, predict=predict)
    assert [(d['speaker_id'], d['text'], d['language']) for d in result['dialogue']] == [
        ('player', 'Ku jemi?', 'Albanian'), ('npc', 'Hello.', 'English')]


def test_actor_identity_and_compact_coordinator_use_authored_beats():
    p, w = setup_scene()
    def predict(stage, actor, system, content, schema):
        if stage == 'actor':
            assert schema['properties']['character_id']['enum'] == ['npc']
            assert 'pattern' in schema['properties']['dialogue']['items']['properties']['text']
            assert json.loads(content)['acting_as'] == {'id': 'npc', 'name': 'Mira'}
            return {'character_id': actor, 'action': 'Mira looks at the window.', 'dialogue': [], 'intent': 'Observe.'}
        if stage == 'roleplay':
            assert 'dialogue' not in schema['properties'] and 'characters' not in schema['properties']
            result = coordinator()
            return result
        assert schema['properties']['shots']['minItems'] == schema['properties']['shots']['maxItems'] == 1
        assert schema['properties']['shots']['items']['properties']['duration']['enum'] == [5]
        assert schema['properties']['shots']['items']['properties']['dialogue_indices']['const'] == [0]
        result = direction(); result['shots'][0]['dialogue_indices'] = [0]
        return result
    result = plan_turn(project=p, world=w, player_character_id='player', message='"Ku jemi?"', duration=5, predict=predict)
    assert result['action'] == narrative()['beats'][0]['action']
    assert result['dialogue'][0]['text'] == 'Ku jemi?'


def test_director_cannot_invent_beat_drop_line_or_conflict_visibility():
    p, w = setup_scene()
    value = validate_narrative(narrative(), world=w, player_character_id='player', message='"Ku jemi?"', duration=5)
    cases = [(lambda d: d['shots'][0].update(beat_id='invented'), 'narrative beat'),
             (lambda d: d['shots'][0].update(dialogue_indices=[0]), 'dialogue'),
             (lambda d: d['shots'][0].update(offscreen_subject_ids=['player']), 'conflicting'),
             (lambda d: d['shots'][0].update(duration=8), 'add up')]
    for edit, match in cases:
        value['direction'] = direction(); edit(value['direction'])
        with pytest.raises(ValueError, match=match):
            direct_plan(value, p, duration=5)


def test_director_preserves_user_camera_locks_loras_and_aspect_ratio():
    p, w = setup_scene()
    p['aspect_ratio'] = '9:16'; p['comfy_render'] = {'seed': 71, 'loras': [{'name': 'style', 'strength': .5}]}
    p['shots'][0]['camera']['framing'] = 'wide'
    p['shots'][0]['director_locks'] = ['camera.framing']
    value = validate_narrative(narrative(), world=w, player_character_id='player', message='"Ku jemi?"', duration=5)
    value['direction'] = direction()
    result = direct_plan(value, p, duration=5)
    assert result['shots'][0]['camera']['framing'] == 'wide'
    assert result['comfy_render'] == p['comfy_render'] and result['aspect_ratio'] == '9:16'


def test_experimental_three_second_direction_keeps_timing_exact():
    p, w = setup_scene()
    value = validate_narrative(narrative(), world=w, player_character_id='player', message='"Ku jemi?"', duration=3)
    value['direction'] = direction(3)
    result = direct_plan(value, p, duration=3)
    assert result['duration'] == 3 and sum(s['duration'] for s in result['shots']) == 3


def test_no_unapproved_generic_direction_fallback():
    p, _ = setup_scene()
    with pytest.raises(ValueError, match='needs scene direction'):
        direct_plan(narrative(), p, duration=5)


def test_replayed_turn_has_identical_stage_request_content_for_durable_receipts():
    p, w = setup_scene()
    batches = []
    for _ in range(2):
        requests = []
        def predict(stage, actor, system, content, schema):
            requests.append((stage, actor, system, content, schema))
            if stage == 'actor':
                return {'character_id': 'npc', 'action': 'Mira points toward the workshop window.',
                        'dialogue': [{'text': 'Në punishten time.', 'language': 'Albanian', 'delivery': 'soft'}], 'intent': 'Answer the question.'}
            return coordinator() if stage == 'roleplay' else direction()
        plan_turn(project=p, world=w, player_character_id='player', message='I ask "Ku jemi?"', duration=5, predict=predict)
        batches.append(requests)
    assert batches[0] == batches[1]


def test_exported_game_engine_matches_the_runtime_system_layer():
    from pathlib import Path
    assert Path("system-prompts/game-engine.txt").read_text(encoding="utf-8").strip() == GAME_ENGINE_SYSTEM


def test_new_game_identifies_only_language_without_rewriting_player_words():
    p, w = setup_scene()
    w['characters'][0]['speaking_style'] = ''
    def predict(stage, actor, system, content, schema):
        if stage == 'actor':
            return {'character_id': actor, 'action': 'Mira gestures toward the workshop.',
                    'dialogue': [{'text': 'Here.', 'language': 'en', 'delivery': 'quiet'}], 'intent': 'Answer.'}
        if stage == 'roleplay':
            assert 'player_language' in schema['required']
            result = coordinator(); result['player_language'] = 'Albanian'
            return result
        return direction()
    result = plan_turn(project=p, world=w, player_character_id='player', message='"Ku jemi?"', duration=5, predict=predict)
    assert result['dialogue'][0]['text'] == 'Ku jemi?'
    assert result['dialogue'][0]['language'] == 'Albanian'
    assert result['dialogue'][1]['language'] == 'English'
    assert 'player_language' not in result


def test_actor_reference_metadata_excludes_an_unseen_character_photo():
    from backend.game_director import _actor_references
    from backend.world import actor_context
    p, w = setup_scene()
    w['locations'].append({'id': 'elsewhere', 'name': 'Elsewhere'})
    w['characters'][0].update(location_id='room', asset_ids=['player-photo'])
    w['characters'][1].update(location_id='room', asset_ids=['npc-photo'])
    w['characters'].append({'id': 'unseen', 'name': 'Unseen', 'location_id': 'elsewhere', 'asset_ids': ['hidden-photo']})
    w = validate_world(w)
    p['assets'] = [
        {'id': 'player-photo', 'name': 'Player face', 'semantic_role': 'face', 'prompt_tag': 'player', 'description': 'Blue coat.'},
        {'id': 'npc-photo', 'name': 'Mira face', 'semantic_role': 'face', 'prompt_tag': 'mira', 'description': 'Green jacket.'},
        {'id': 'hidden-photo', 'name': 'Secret person', 'semantic_role': 'face', 'prompt_tag': 'hidden', 'description': 'An unseen spy.'}]
    refs = _actor_references(p, actor_context(w, 'npc'))
    assert {r['id'] for r in refs} == {'player-photo', 'npc-photo'}
    assert next(r for r in refs if r['id'] == 'npc-photo')['assigned_to'] == 'Mira'
    assert 'unseen spy' not in str(refs).lower()


def test_user_failure_choices_accept_contractions_and_preserve_asked_question():
    from backend.game_director import _player_choice
    assert _player_choice("I'll look at this key more closely.") == "I'll look at this key more closely."
    assert _player_choice("Alright, I've checked the key.") == "I've checked the key."
    question = 'Does this door have a special mechanism?'
    assert _player_choice(question) == 'I ask, "' + question + '"'


def test_holder_effect_schema_cannot_emit_objective_fields_or_unknown_identity():
    from backend.game_director import _effect_schema
    from jsonschema import Draft202012Validator
    _, w = setup_scene()
    w['entities'] = [{'id': 'key', 'name': 'Gold key', 'kind': 'object', 'location_id': 'room'}]
    w = validate_world(w)
    schema = _effect_schema(w)
    validator = Draft202012Validator(schema)
    assert validator.is_valid([{'kind': 'holder', 'entity_id': 'key', 'character_id': 'player'}])
    assert not validator.is_valid([{'kind': 'holder', 'objective_id': 'key', 'status': 'active'}])
    assert not validator.is_valid([{'kind': 'holder', 'entity_id': 'key', 'character_id': 'invented'}])
    assert not validator.is_valid([{'kind': 'holder', 'entity_id': 'key'}])


def test_named_player_suggestions_are_valid_but_npc_decisions_are_not():
    _, w = setup_scene()
    value = narrative()
    value['choices'][0]['message'] = 'Alex turns the key over in his hands.'
    value['choices'][1]['message'] = '"Does this key fit?" Alex asks, holding it up.'
    checked = validate_narrative(value, world=w, player_character_id='player', message='"Ku jemi?"', duration=5)
    assert checked['choices'][0]['message'] == value['choices'][0]['message']
    value['choices'][0]['message'] = 'Mira takes the key and opens the door.'
    with pytest.raises(ValueError, match="another character"):
        validate_narrative(value, world=w, player_character_id='player', message='"Ku jemi?"', duration=5)
    value['choices'][0]['message'] = 'I wait, then Mira opens the door.'
    with pytest.raises(ValueError, match="another character"):
        validate_narrative(value, world=w, player_character_id='player', message='"Ku jemi?"', duration=5)


def test_single_quoted_actual_game_choice_preserves_internal_contraction():
    from backend.game_director import quoted_speech
    choice = "With the object in hand, I look at you and say, 'Now that I have this, what's the next move?' ."
    assert quoted_speech(choice) == ["Now that I have this, what's the next move?"]
    assert quoted_speech("I'm holding it and wondering what's next.") == []
    assert quoted_speech("I say 'I'm ready.' Then I say \"Go.\"") == ["I'm ready.", "Go."]
    assert quoted_speech("I say \u2018I\u2019m ready.\u2019") == ["I\u2019m ready."]


def test_player_suggestion_can_begin_with_context_or_an_imperative():
    _, world = setup_scene()
    for text in ("With the key in my hand, I try the lock.", "Carefully turn the key.", '"Will it fit?"'):
        value = narrative()
        value['choices'][0]['message'] = text
        assert validate_narrative(value, world=world, player_character_id='player', message='"Ku jemi?"', duration=5)['choices'][0]['message'] == text


def test_current_holder_fact_overrides_the_original_table_caption():
    from backend.game_director import _current_scene_facts
    _, world = setup_scene()
    world['entities'] = [{'id': 'key', 'name': 'Gold key', 'description': 'A key on a table', 'holder_id': 'player', 'state': {}}]
    assert _current_scene_facts(world) == ['Gold key is ALREADY HELD by Alex. Do not pick it up again from a table or floor.']


def test_completed_context_keeps_effects_and_exact_speech_without_replaying_prose():
    from backend.game_director import _planning_context, _check_action_replay
    text = 'Alex picks up the key from the table and holds it under the light, carefully examining its shape before moving it toward the wooden door.'
    original = {'recent_events': [{'id': 'past', 'summary': text, 'effects': [{'kind': 'holder'}], 'dialogue': [{'text': 'Ready.'}]}]}
    cleaned = _planning_context(original)
    assert 'summary' not in cleaned['recent_events'][0]
    assert cleaned['recent_events'][0]['dialogue'] == [{'text': 'Ready.'}]
    assert original['recent_events'][0]['summary'] == text
    with pytest.raises(ValueError, match='copies a completed scene'):
        _check_action_replay({'action': text}, original, 'Unlock the door', None)
    _check_action_replay({'action': text}, original, 'Repeat that action', None)
    _check_action_replay({'action': text}, original, 'Move forward', {'kind': 'move'})
