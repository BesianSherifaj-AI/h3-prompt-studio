"""Edited response authority and stop/apply draining; no real models or queue."""
import copy
import json
import threading

import pytest

from test_stories import observed_for_schema, rig, story, plan, uid, reference


def directed(rig, action='Nora points to the locked drawer.'):
    value = plan(action=action)
    value['beats'] = [{'id': 'beat-1', **{k: value[k] for k in ('action', 'setting', 'final_state')}}]
    value['effects'] = [{'kind': 'knowledge', 'character_id': rig.project['subjects'][1]['id'], 'fact': 'An obsolete secret.'}]
    value['direction'] = {'shots': [{'beat_id': 'beat-1', 'duration': 5,
        'camera': {'framing': 'wide shot', 'movement': 'static', 'height': 'eye level', 'speed': 'slow', 'focus': 'Both visitors'},
        'performance': 'Nora points at the old drawer.', 'sound': 'Quiet room ambience.',
        'visible_subject_ids': [p['id'] for p in rig.project['subjects']], 'offscreen_subject_ids': [],
        'dialogue_indices': [0], 'transition': 'continuous'}]}
    return value


def test_edited_events_are_redirected_before_render_and_stale_effects_are_removed(rig):
    session = story(rig, settings={'assistant_provider': 'supervised', 'duration': 5})
    old = directed(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look toward Nora.', 'planned': old})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    rig.manager._change(record, turn, status='awaiting_review')
    edited = copy.deepcopy(old)
    edited.update(action='Nora walks to the window and opens its curtain.', final_state='Nora stands at the open curtain.')
    edited['dialogue'] = [{'speaker': 'Nora', 'text': 'Look outside.', 'language': 'English', 'delivery': 'calm'},
                          {'speaker': 'Nora', 'text': 'The rain stopped.', 'language': 'English', 'delivery': 'calm'}]
    rig.manager.action(session['id'], turn['id'], 'approve', {'request_id': uid(), 'plan': edited})
    assert 'direction' not in turn['plan']
    assert turn['plan']['beats'][0]['action'] == edited['action']
    assert turn['plan']['effects'] == []
    stages = []
    def predict(execution, current, stage, actor, system, content, schema, images=()):
        stages.append(stage)
        if stage == 'director':
            assert not rig.assets.requests and not rig.videos.queues
            payload = json.loads(content)
            assert payload['beats'][0]['action'] == edited['action']
            result = copy.deepcopy(old['direction'])
            result['shots'][0].update(performance='Nora walks naturally and opens the curtain.', dialogue_indices=[0, 1])
            return result
        assert stage == 'ending-inspection'
        return observed_for_schema(rig.client.observation, schema)
    rig.manager._predict = predict
    rig.manager.process(session['id'], turn['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    queued = rig.videos.submissions[-1][1]
    assert queued['shots'][0]['action'] == edited['action']
    assert [d['text'] for d in queued['shots'][0]['dialogue']] == ['Look outside.', 'The rain stopped.']
    assert 'old drawer' not in queued['shots'][0]['performance']
    assert stages == ['director', 'ending-inspection']


def test_actor_sees_approved_identity_and_ending_but_not_offscene_references(rig):
    p = copy.deepcopy(rig.project)
    mira, nora = p['subjects']
    own_face, visible_face = p['assets']
    own_face['approved_observation'] = 'Approved identity description must not suppress the actual photo.'
    wardrobe = reference('Mira coat', 'wardrobe', 'mira-coat')
    elsewhere = reference('Hidden visitor', 'face', 'hidden-face')
    hidden_place = reference('Offscene room', 'background', 'elsewhere')
    obj = reference('Carried key', 'object', 'key')
    own_face['simple_owner_id'] = None
    obj['simple_owner_id'] = 'offscene'
    p['assets'] = [elsewhere, hidden_place, obj, visible_face, wardrobe, own_face]
    mira['asset_ids'].append(wardrobe['id'])
    world = {'current_location_id': 'room', 'characters': [
        {**mira, 'location_id': 'room'}, {**nora, 'location_id': 'room'},
        {'id': 'offscene', 'asset_ids': [elsewhere['id']], 'location_id': 'away'}],
        'locations': [{'id': 'away', 'asset_ids': [hidden_place['id']]}],
        'entities': [{'asset_ids': [obj['id']], 'owner_id': 'offscene', 'holder_id': mira['id'], 'state': {}}]}
    execution = {'world': world, 'player_character_id': mira['id']}
    ending = reference('Actual ending', 'pose', 'ending')
    selected = rig.manager._plan_images(execution, {}, p, ending, 'actor', mira['id'])
    assert [a['id'] for a in selected] == [ending['id'], own_face['id'], wardrobe['id'], visible_face['id']]
    # Ownership does not hide a prop held by a visible character.
    p['assets'] = [elsewhere, hidden_place, obj]
    assert [a['id'] for a in rig.manager._plan_images(execution, {}, p, None, 'actor', mira['id'])] == [obj['id']]


def test_new_game_pixel_defaults_do_not_change_studio_or_explicit_settings(rig):
    game = story(rig)
    assert (game['settings']['resolution'], game['settings']['duration']) == ('0.2', 3)
    assert game['settings']['experimental_preview'] and '2D pixel' in game['settings']['style']
    studio = rig.manager.create({'request_id': uid(), 'project': rig.project, 'mode': 'studio'})
    assert (studio['settings']['resolution'], studio['settings']['duration']) == ('0.3', 5)
    explicit = story(rig, settings={'resolution': '0.7', 'duration': 10, 'style': ''})
    assert explicit['settings']['resolution'] == '0.7' and explicit['settings']['style'] == ''


def test_narrative_receives_saved_initiative(rig, monkeypatch):
    captured = {}
    def planner(**kwargs):
        captured.update(kwargs)
        return plan()
    monkeypatch.setattr('backend.game_director.plan_turn', planner)
    session = story(rig, settings={'assistant_provider': 'supervised', 'initiative': 'proactive'})
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I wait.'})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    execution = rig.manager._execution(record, turn)
    rig.manager.plan(execution, turn, execution['base_project'])
    assert captured['initiative'] == 'proactive'
    assert captured['world'] == turn['snapshot']['world']


def test_first_person_project_keeps_player_offscreen_and_current_holder_context(rig):
    rig.project['game_viewpoint'] = 'pov'
    session = story(rig, settings={'assistant_provider': 'supervised', 'duration': 5})
    supplied = directed(rig)
    supplied['dialogue'][0]['language'] = 'English'
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look at Nora.', 'planned': supplied})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    execution = rig.manager._execution(record, turn)
    mira, nora = execution['world']['characters']
    execution['world']['entities'].append({'id': uid(), 'name': 'Silver key', 'kind': 'prop', 'description': '',
        'location_id': None, 'owner_id': mira['id'], 'holder_id': nora['id'], 'worn_by_id': None,
        'asset_ids': [], 'affordances': [], 'state': {}})
    project = rig.manager._project(execution, turn, execution['base_project'], None)
    assert mira['id'] not in project['shots'][0]['visible_subject_ids']
    assert mira['id'] in project['shots'][0]['offscreen_subject_ids']
    from backend.compiler import compile_project
    compiled = compile_project(project)
    assert compiled['valid'], compiled['issues']
    assert 'Principal cast in this shot: exactly 1 separate individual, <Subject 2> Nora' in compiled['prompt']
    contract = project['shots'][0]['scene_contract']
    assert [row['subject_id'] for row in contract['actors']] == [nora['id']]
    key = next(row for row in contract['objects'] if row['name'] == 'Silver key')
    assert key['start'] == key['end'] and 'held by Nora' in key['end']


@pytest.mark.parametrize('change,expected_mode', [('same', 'i2va'), ('place', 'ref2va'), ('pov', 'ref2va'),
                                               ('user_first', 'i2va'), ('user_last', 'fl2va'), ('identity', 'ref2va')])
def test_cut_releases_inherited_opening_but_preserves_new_keyframes(rig, change, expected_mode):
    from backend.compiler import compile_project
    original = reference('Original opening', 'pose', 'original-opening')
    original['role'] = 'first_frame'
    for asset in rig.project['assets']:
        asset['role'] = 'context'
    rig.project.update(mode='i2va', assets=rig.project['assets'] + [original])
    parent = rig.videos.add(rig.project)
    session = story(rig, source=parent['id'], settings={'duration': 5})
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look at Nora.', 'planned': plan(transition='cut')})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    execution = rig.manager._execution(record, turn)
    source = copy.deepcopy(execution['base_project'])
    ending = rig.manager.ending_asset(parent['id'])
    added = None
    if change == 'place':
        added = reference('A new forest', 'background', 'forest')
        turn['created_assets'] = [added]
    elif change == 'pov':
        source['game_viewpoint'] = 'pov'
    elif change in ('user_first', 'user_last'):
        added = reference('User selected frame', 'pose', 'chosen-frame')
        added['role'] = 'first_frame' if change == 'user_first' else 'last_frame'
        source['assets'] = [a for a in source['assets'] if a['id'] != original['id'] or change == 'user_last'] + [added]
    elif change == 'identity':
        source['assets'][0]['role'] = 'reference_image'
    project = rig.manager._project(execution, turn, source, ending)
    assert project['mode'] == expected_mode
    active = [a for a in project['assets'] if a.get('enabled', True) and a['role'] != 'context']
    assert original['id'] not in {a['id'] for a in active}
    assert {a['id'] for a in source['assets'] if a['semantic_role'] == 'face'} <= {a['id'] for a in project['assets']}
    if change in ('same', 'identity', 'pov', 'user_last'):
        assert ending['id'] in {a['id'] for a in active}
    if change in ('user_first', 'user_last'):
        assert next(a for a in active if a['id'] == added['id'])['role'] == added['role']
    if change == 'place':
        assert [a['id'] for a in active] == [added['id']]
    if change == 'pov':
        assert project['game_viewpoint'] == 'pov'
        assert project['simple']['cut_source']['viewpoint'] == 'pov'
    compiled = compile_project(project)
    assert not [i for i in compiled['issues'] if i['severity'] == 'error'], compiled['issues']


def test_stop_apply_saves_changes_and_waits_for_original_image_before_resnapshot(rig):
    session = story(rig)
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look at Nora.'})
    record = rig.manager._story(session['id']); turn = rig.manager._turn(record, public['id'])
    child = uid(); rig.assets.records[child] = {'id': child, 'status': 'running'}
    turn.update(asset_jobs=[child], status='assets')
    refreshed = threading.Event()
    def cancel(aid):
        rig.assets.cancelled.append(aid); rig.assets.records[aid]['status'] = 'cancelling'
    original_refresh = rig.assets.refresh
    def refresh(aid):
        refreshed.set(); return original_refresh(aid)
    rig.assets.cancel = cancel; rig.assets.refresh = refresh
    old_snapshot = copy.deepcopy(turn['snapshot']); old_request = turn['render_request_id']
    body = {'request_id': uid(), 'expected_configuration_revision': session['configuration_revision'],
            'settings': {'seed': 919, 'aspect_ratio': '9:16', 'duration': 7}, 'premise': 'The user changed the scene direction.'}
    rig.manager.action(session['id'], turn['id'], 'stop-and-apply', body)
    assert refreshed.wait(1)
    assert turn['status'] == 'stopping' and turn['snapshot'] == old_snapshot
    assert turn['render_request_id'] == old_request
    assert rig.manager.get(session['id'])['settings']['seed'] == 919
    with pytest.raises(ValueError, match='current story turn'):
        rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'Another move.'})
    # A dying original worker cannot present the session as idle while its child drains.
    rig.manager._change(record, turn, status='cancelled', stage='Old worker stopped')
    assert turn['status'] == 'stopping'
    rig.assets.records[child]['status'] = 'cancelled'
    rig.manager.replacement_workers[turn['id']].join(2)
    assert not rig.manager.replacement_workers[turn['id']].is_alive()
    assert turn['status'] == 'planning' and not turn.get('pending_replacement')
    assert turn['snapshot']['settings']['seed'] == 919 and turn['snapshot']['settings']['aspect_ratio'] == '9:16'
    assert turn['snapshot']['premise'] == body['premise'] and turn['duration'] == 7
    assert turn['render_request_id'] != old_request and len(turn['attempt_history']) == 1
    saved_request = turn['render_request_id']
    rig.manager.action(session['id'], turn['id'], 'stop-and-apply', body)
    assert turn['render_request_id'] == saved_request and not rig.videos.queues


def test_stop_apply_stale_revision_does_not_cancel_or_change_configuration(rig):
    session = story(rig)
    turn = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I look around.'})
    with pytest.raises(ValueError, match='another window'):
        rig.manager.action(session['id'], turn['id'], 'stop-and-apply', {'request_id': uid(),
            'expected_configuration_revision': 999, 'settings': {'seed': 919}})
    stored = rig.manager.get(session['id'])
    assert stored['configuration_revision'] == session['configuration_revision']
    assert stored['turns'][0]['status'] == 'planning'
    assert not rig.assets.cancelled and not rig.videos.cancelled
