"""Authored camera choices and reusable photo tags survive assistant rewrites."""
import copy
import json

import pytest

from backend.compiler import compile_project
from backend.projects import check_project, merge_plan, new_project, shot, uid
from backend.prompts import plan_prompt


def project(mode='ref2va'):
    p = new_project()
    p.update(mode=mode, profile='concise')
    face = dict(id=uid(), name='Mira face', prompt_tag='mira-face', media_type='image',
                role='reference_image', semantic_role='face', enabled=True,
                description='Short dark hair and an expressive face.')
    dress = dict(id=uid(), name='Green dress', prompt_tag='green-dress', media_type='image',
                 role='reference_image', semantic_role='wardrobe', enabled=True,
                 description='An emerald green dress with long sleeves.')
    person = dict(id=uid(), name='Mira', description='An adult visitor', asset_ids=[face['id'], dress['id']])
    p['assets'] = [face, dress]
    p['subjects'] = [person]
    p['story']['text'] = 'Mira walks into the atelier.'
    p['shots'][0].update(action='Mira walks into the atelier.', visible_subject_ids=[person['id']])
    if mode == 'i2va':
        face['role'], dress['role'] = 'first_frame', 'context'
    if mode == 'fl2va':
        face['role'], dress['role'] = 'first_frame', 'last_frame'
    return p


def errors(result):
    return {issue['code'] for issue in result['issues'] if issue['severity'] == 'error'}


def test_continuation_planner_receives_completed_story_and_actual_ending_separately():
    p = project()
    ending = dict(id=uid(), name='Actual ending', role='context', semantic_role='pose', enabled=True,
                  media_type='image', approved_observation='Nora holds the closed gift box beside the window.')
    p['assets'].append(ending)
    p['simple'] = {'continuation': {'request': 'Nora opens the box.', 'ending_image_asset_id': ending['id'],
        'previous_story': {'brief': 'Mira gives Nora a gift.', 'shots': [{'action': 'Mira hands over the box.',
                          'dialogue': [{'speaker_id': p['subjects'][0]['id'], 'text': 'This is for you.'}]}]},
        'previous_ending': 'The planned ending is by the door.'}}
    p['comfy_render'] = {'continuation_source': 'mmh3/example.mmh3', 'continuation_overlap_frames': 39}
    system, content, _ = plan_prompt(p)
    data = json.loads(content)['project']
    assert data['continuation']['previous_story']['shots'][0]['dialogue'][0]['text'] == 'This is for you.'
    assert data['continuation']['ending_image_asset_id'] == ending['id']
    assert data['assets'][-1]['approved_observation'] == ending['approved_observation']
    assert data['continuation_render']['preserved_context_seconds'] == 39 / 24
    assert 'never replay them' in system and 'do not claim you watched or heard' in system


def test_photo_tags_follow_real_conditioning_order_without_changing_the_saved_draft():
    p = project()
    p['story']['text'] = 'The person in @mira-face wears @green-dress.'
    p['shots'][0]['action'] = 'The garment from @green-dress moves as the person in @mira-face turns.'
    original = copy.deepcopy(p)
    first = compile_project(p)
    assert first['valid'] and p == original
    assert 'The person in <Picture 1> wears <Picture 2>.' in first['prompt']
    p['assets'].reverse()
    second = compile_project(p)
    assert second['valid']
    assert 'The person in <Picture 2> wears <Picture 1>.' in second['prompt']
    assert 'The garment from <Picture 1>' in second['prompt']
    assert second['reference_tags'] == [
        dict(id=a['id'], tag=a['prompt_tag'], label=a['name'], role=a['role'], enabled=True, token=f'<Picture {i}>')
        for i, a in enumerate(p['assets'], 1)
    ]
    assert all(a['id'] not in second['prompt'] for a in p['assets'])


def test_context_tag_uses_plain_label_without_inventing_a_conditioning_input():
    p = project('i2va')
    p['story']['text'] = 'Begin from @mira-face and use @green-dress for the outfit.'
    result = compile_project(p)
    assert result['valid']
    assert 'Begin from <Picture 1> and use Green dress for the outfit.' in result['prompt']
    assert len(result['references']) == 1 and '<Picture 2>' not in result['prompt']
    assert result['reference_tags'][1]['token'] is None
    assert result['reference_tags'][1]['role'] == 'context'


def test_context_name_cannot_manufacture_picture_tokens():
    p = project('i2va')
    p['assets'][1]['name'] = 'Green dress <Picture 9>'
    p['shots'][0]['action'] = 'Follow @green-dress for clothing only.'
    result = compile_project(p)
    assert result['valid'] and '<Picture 9>' not in result['prompt']
    assert 'Green dress Picture 9' in result['prompt']


def test_first_and_last_tags_follow_keyframe_roles_instead_of_library_order():
    p = project('fl2va')
    p['story']['text'] = 'Move from @mira-face to @green-dress.'
    p['assets'].reverse()
    result = compile_project(p)
    assert result['valid']
    assert 'Move from <Picture 1> to <Picture 2>.' in result['prompt']


@pytest.mark.parametrize('missing, expected', [(True, 'unknown_reference_tag'), (False, 'disabled_reference_tag')])
def test_unavailable_photo_tag_is_an_actionable_error(missing, expected):
    p = project()
    p['shots'][0]['action'] = 'Wear @green-dress.'
    if missing:
        p['assets'][1]['prompt_tag'] = 'another-dress'
    else:
        p['assets'][1]['enabled'] = False
    result = compile_project(p)
    assert not result['valid'] and result['prompt'] == ''
    assert expected in errors(result)
    issue = next(i for i in result['issues'] if i['code'] == expected)
    assert issue['path'] == 'shots[0].action' and '@green-dress' in issue['message']


def test_duplicate_photo_tags_reject_ambiguous_ownership_even_if_one_is_disabled():
    p = project()
    p['assets'][1].update(prompt_tag='mira-face', enabled=False)
    result = compile_project(p)
    assert errors(result) == {'duplicate_reference_tag'}


@pytest.mark.parametrize('tag', ['Mira', 'mira face', '-mira', 'mira--face', 'mira_face', 'a' * 65, 42])
def test_photo_tags_have_a_small_predictable_format(tag):
    p = project()
    p['assets'][0]['prompt_tag'] = tag
    assert 'invalid_reference_tag' in errors(compile_project(p))


def test_dialogue_tags_and_emails_remain_exact_while_delivery_and_scene_tags_resolve():
    p = project()
    p['story']['text'] = 'Show the email mira@example.com beside https://example.com/@handle.'
    words = '  Follow @unknown-person and @mira-face. Email me@example.com!  '
    line = dict(id=uid(), speaker_id=p['subjects'][0]['id'], language='English', text=words,
                delivery='Looking like @mira-face', locked=True)
    p['shots'][0]['dialogue'] = [line]
    p['shots'][0].update(setting='Use the look of @green-dress.', final_state='Hold the expression from @mira-face.')
    p['shots'][0]['camera']['focus'] = '@mira-face'
    p['style']['notes'] = 'The colors in @green-dress dominate.'
    p['subjects'][0]['description'] = 'Face from @mira-face'
    original = copy.deepcopy(p)
    result = compile_project(p)
    assert result['valid'] and p == original
    assert '<d>[English] ' + words + '</d>' in result['prompt']
    assert 'Looking like <Picture 1>' in result['prompt']
    assert 'mira@example.com' in result['prompt'] and 'https://example.com/@handle' in result['prompt']
    assert 'The colors in <Picture 2> dominate.' in result['prompt']
    assert 'Focus on <Picture 1>' in result['prompt']


def test_director_controls_and_exact_scene_timing_survive_opposite_ai_choices():
    p = project()
    p['duration'] = 15
    p['shots'].extend([shot(5), shot(7)])
    p['shots'][0]['duration'] = 3
    p['shots'][0]['camera'].update(framing='wide', focus='')
    p['shots'][0]['director_locks'] = ['camera.framing', 'camera.focus']
    p['shots'][1].update(transition='cut', setting='At the shop counter', director_locks=['transition', 'setting'])
    p['shots'][2].update(final_state='Mira holds the dress still', director_locks=['final_state'])
    exact_line = dict(id=uid(), speaker_id=p['subjects'][0]['id'], language='English', text='My @green-dress stays!', locked=True)
    p['shots'][2]['dialogue'] = [exact_line]
    proposal = {'shots': [dict(duration=100, camera={'framing': 'extreme close-up', 'focus': 'the window'}, action='Improved action'),
                          dict(duration=1, transition='continuous', setting='On the street'),
                          dict(duration=1, final_state='Mira drops the dress')]}
    source, suggested = copy.deepcopy(p), copy.deepcopy(proposal)
    candidate = merge_plan(p, proposal)
    assert p == source and proposal == suggested
    assert [s['duration'] for s in candidate['shots']] == [3, 5, 7]
    assert [s['id'] for s in candidate['shots']] == [s['id'] for s in p['shots']]
    assert candidate['shots'][0]['camera']['framing'] == 'wide'
    assert candidate['shots'][0]['camera']['focus'] == ''
    assert candidate['shots'][0]['action'] == 'Improved action'
    assert candidate['shots'][1]['transition'] == 'cut' and candidate['shots'][1]['setting'] == 'At the shop counter'
    assert candidate['shots'][2]['final_state'] == 'Mira holds the dress still'
    assert candidate['shots'][2]['dialogue'] == [exact_line]
    assert candidate['shots'][0]['director_locks'] == ['camera.framing', 'camera.focus']


def test_simple_directed_timeline_stays_exact_even_without_locked_camera_controls():
    p = project()
    p['simple'] = {'directed': True}
    p['shots'][0]['duration'] = 2
    p['shots'].append(shot(3))
    candidate = merge_plan(p, {'shots': [{'duration': 99}, {'duration': 1}]})
    assert [s['duration'] for s in candidate['shots']] == [2, 3]
    assert [s['id'] for s in candidate['shots']] == [s['id'] for s in p['shots']]
    with pytest.raises(ValueError, match='number of scenes'):
        merge_plan(p, {'shots': [{'duration': 5}]})


@pytest.mark.parametrize('locked, opposite', [('visible_subject_ids', 'offscreen_subject_ids'), ('offscreen_subject_ids', 'visible_subject_ids')])
def test_selected_character_roster_wins_over_contradictory_model_roster(locked, opposite):
    p = project()
    sid = p['subjects'][0]['id']
    p['shots'][0].update({locked: [sid], opposite: [], 'director_locks': [locked]})
    candidate = merge_plan(p, {'shots': [{'duration': 5, locked: [], opposite: [sid]}]})
    assert candidate['shots'][0][locked] == [sid]
    assert candidate['shots'][0][opposite] == []


def test_contradictory_user_rosters_are_not_silently_rewritten():
    p = project()
    p['shots'][0].update(offscreen_subject_ids=[p['subjects'][0]['id']], director_locks=['visible_subject_ids', 'offscreen_subject_ids'])
    with pytest.raises(ValueError, match='both in the scene and off-screen'):
        merge_plan(p, {'shots': [{'duration': 5}]})


@pytest.mark.parametrize('locks', ['camera.framing', ['story'], ['camera.execute'], [42]])
def test_invalid_director_lock_metadata_is_rejected_at_the_project_boundary(locks):
    p = project()
    p['shots'][0]['director_locks'] = locks
    with pytest.raises(ValueError, match='director_locks'):
        check_project(p)


def test_model_receives_exact_selected_controls_aliases_and_scene_count_schema():
    p = project()
    p['shots'][0]['duration'] = 2
    p['shots'][0].update(director_locks=['camera.framing', 'visible_subject_ids'])
    p['shots'][0]['camera']['framing'] = 'wide'
    p['shots'].append(shot(3))
    p['shots'][1]['camera']['framing'] = 'close-up'
    p['shots'][1]['director_locks'] = ['camera.framing']
    system, content, schema = plan_prompt(p, 'Improve my prompt')
    payload = json.loads(content)
    assert [c['duration'] for c in payload['scene_constraints']] == [2, 3]
    assert [c['selected_controls']['camera.framing'] for c in payload['scene_constraints']] == ['wide', 'close-up']
    assert payload['project']['assets'][0]['prompt_tag'] == 'mira-face'
    assert payload['project']['shots'][0]['director_locks'] == ['camera.framing', 'visible_subject_ids']
    assert schema['properties']['shots']['minItems'] == schema['properties']['shots']['maxItems'] == 2
    assert 'Preserve those values literally' in system and 'prompt inspiration' in system
