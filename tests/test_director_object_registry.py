"""Directors receive object identities before assigning scene prop instances."""
import copy
import json

import pytest

from backend.game_director import _known_scene_objects, direct_plan, plan_turn
from backend.world import actor_context, validate_world
from test_game_director import coordinator, direction, narrative, setup_scene
from test_stories import observed_for_schema, rig, uid


def scene():
    project, world = setup_scene()
    project['mode'] = 't2va'
    world['entities'] = [
        {'id': 'token-star', 'name': 'Brass token', 'description': 'One round token marked with a star.', 'holder_id': 'player', 'affordances': ['examine', 'give'], 'state': {'count': 1}},
        {'id': 'token-circle', 'name': 'Brass token', 'description': 'One round token marked with a circle.', 'holder_id': 'npc', 'state': {'count': 1}},
        {'id': 'secret', 'name': 'Hidden token', 'holder_id': 'player', 'state': {'hidden': True}},
    ]
    return project, validate_world(world)


def test_known_registry_preserves_distinct_same_named_records_and_filters_hidden_objects():
    _, world = scene()
    before = copy.deepcopy(world)
    context = actor_context(world, 'player')
    context['visible_objects'] = [item for item in context['visible_objects'] if item['id'] == 'token-star']
    assert [row['entity_id'] for row in _known_scene_objects(world, context)] == ['token-star']
    rows = _known_scene_objects(world, None)
    assert [row['entity_id'] for row in rows] == ['token-star', 'token-circle']
    assert rows[0]['name'] == rows[1]['name'] and rows[0]['holder_id'] != rows[1]['holder_id']
    assert rows[0]['description'] != rows[1]['description'] and all(row['count'] == 1 for row in rows)
    assert world == before


def test_registry_is_bounded_and_prioritizes_the_actual_action_target():
    _, world = scene()
    world['entities'] += [{'id': 'item-' + str(index), 'name': 'Item ' + str(index), 'description': 'x' * 1200}
                          for index in range(100)]
    rows = _known_scene_objects(world, None, important_ids=['item-99'])
    assert rows[0]['entity_id'] == 'item-99'
    assert len(rows) <= 24 and len(json.dumps(rows)) < 12200
    assert all(len(row['description']) <= 500 for row in rows)


@pytest.mark.parametrize('mode', ['game', 'studio'])
def test_creative_director_receives_registry_in_game_and_playerless_studio(mode):
    project, world = scene()
    requests = []
    def predict(stage, actor, system, content, schema):
        request = json.loads(content)
        requests.append((stage, request))
        if stage == 'actor':
            return {'character_id': 'npc', 'action': 'Mira points toward the window.', 'intent': 'Answer Alex.',
                'dialogue': [{'text': 'Në punishten time.', 'language': 'Albanian', 'delivery': 'soft'}]}
        if stage == 'roleplay':
            return coordinator() if mode == 'game' else narrative()
        return direction()
    plan_turn(project=project, world=world, player_character_id='player' if mode == 'game' else None,
        message='I ask Mira "Ku jemi?"', mode=mode, duration=5, predict=predict)
    request = next(request for stage, request in requests if stage == 'director')
    assert {row['entity_id'] for row in request['known_objects']} == {'token-star', 'token-circle'}
    assert request['known_objects'][0]['holder_id'] == 'player'
    assert request['known_objects'][1]['holder_id'] == 'npc'


def prop(key, name='Brass token'):
    return {'entity_id': key, 'name': name, 'count': 1, 'description': 'Round brass token.', 'start': 'held', 'end': 'held'}


@pytest.mark.parametrize('ambiguous', [False, True])
def test_unknown_exact_name_alias_is_rejected_without_guessing_known_identity(ambiguous):
    project, world = scene()
    registry = _known_scene_objects(world)
    if not ambiguous:
        registry = registry[:1]
    def predict(*args):
        value = direction()
        value['shots'][0]['scene_contract'] = {'actors': [], 'objects': [prop('invented-alias')], 'environment': '', 'background_activity': ''}
        return value
    with pytest.raises(ValueError, match='Reuse the supplied ID') as exc:
        direct_plan(narrative(), project, duration=5, predict=predict, game_mode=False, known_objects=registry)
    assert 'token-star' in str(exc.value)
    assert ('token-circle' in str(exc.value)) is ambiguous


def test_two_known_tokens_and_approved_new_same_named_discovery_keep_separate_ids():
    project, world = scene()
    value = narrative()
    value['discoveries'] = {'entities': [{'id': 'third-token', 'name': 'Brass token', 'description': 'A newly introduced triangle-marked token.', 'kind': 'object'}]}
    def predict(*args):
        result = direction()
        result['shots'][0]['scene_contract'] = {'actors': [], 'objects': [prop(key) for key in ('token-star', 'token-circle', 'third-token')],
                                               'environment': '', 'background_activity': ''}
        return result
    prepared = direct_plan(value, project, duration=5, predict=predict, game_mode=False, known_objects=_known_scene_objects(world))
    assert [row['entity_id'] for row in prepared['shots'][0]['scene_contract']['objects']] == ['token-star', 'token-circle', 'third-token']


def test_new_studio_prop_with_a_different_identity_remains_possible():
    project, world = scene()
    def predict(*args):
        result = direction()
        result['shots'][0]['scene_contract'] = {'actors': [], 'objects': [prop('parcel', 'Violet parcel')], 'environment': '', 'background_activity': ''}
        return result
    prepared = direct_plan(narrative(), project, duration=5, predict=predict, game_mode=False, known_objects=_known_scene_objects(world))
    assert prepared['shots'][0]['scene_contract']['objects'][0]['entity_id'] == 'parcel'


def test_production_fast_director_receives_current_object_registry(rig):
    project, world = scene()
    world['entities'][0]['state'] = {}  # No custom mechanics condition on this fast target.
    session = rig.manager.create({'request_id': uid(), 'project': project, 'world': world, 'mode': 'game',
        'player_name': 'Alex', 'player_character_id': 'player', 'settings': {'duration': 3, 'fast_actions': True}})
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'I examine my token.', 'intent': {'kind': 'examine', 'target_id': 'token-star'}})
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    requests = []
    def complete(model, system, content, schema, **kwargs):
        request = json.loads(next(item['text'] for item in content if item['type'] == 'text'))
        requests.append(request)
        result = direction(3)
        result['shots'][0].update(dialogue_indices=[], beat_id=request['beats'][0]['id'])
        return {'result': result, 'diagnostics': {}}
    rig.client.complete_json_result = complete
    value = rig.manager.plan(rig.manager._execution(record, turn), turn, turn['snapshot']['project'])
    assert [stage['stage'] for stage in value['assistant_stages']] == ['mechanics', 'director']
    assert len(requests) == 1 and requests[0]['known_objects'][0]['entity_id'] == 'token-star'
    assert {row['entity_id'] for row in requests[0]['known_objects']} == {'token-star', 'token-circle'}
    assert not rig.videos.queues and not rig.assets.requests


def test_production_alias_rejection_repairs_only_director_before_any_render(rig):
    project, world = scene()
    session = rig.manager.create({'request_id': uid(), 'project': project, 'world': world, 'mode': 'studio',
        'settings': {'duration': 5, 'review_before_render': False}})
    public = rig.manager.submit(session['id'], {'request_id': uid(), 'message': 'Show Mira pointing toward the window.'})
    stages = []
    def complete(model, system, content, schema, **kwargs):
        properties = schema['properties']
        if 'shots' in properties:
            stages.append('director')
            assert not rig.videos.queues, 'Reject and repair an alias before starting a render.'
            request = json.loads(next(item['text'] for item in content if item['type'] == 'text'))
            assert {row['entity_id'] for row in request['known_objects']} == {'token-star', 'token-circle'}
            repairing = stages.count('director') == 2
            result = direction()
            result['shots'][0]['scene_contract'] = {'actors': [], 'objects': [prop('token-star' if repairing else 'invented-alias')],
                                                   'environment': '', 'background_activity': ''}
            if repairing:
                assert 'Reuse the supplied ID' in system
                assert any('invented-alias' in item.get('text', '') for item in content)
        elif 'observed_state' in properties:
            stages.append('inspection')
            result = observed_for_schema(rig.client.observation, schema)
        else:
            stages.append('writer')
            result = narrative()
        return {'result': copy.deepcopy(result), 'diagnostics': {}}
    rig.client.complete_json_result = complete
    rig.manager.process(session['id'], public['id'])
    record = rig.manager._story(session['id'])
    turn = rig.manager._turn(record, public['id'])
    assert turn['status'] == 'succeeded', turn.get('error')
    assert stages == ['writer', 'director', 'director', 'inspection']
    assert turn['automatic_repair_used'] and turn['assistant_repair']['stage'] == 'director'
    assert len(rig.videos.queues) == 1 and not rig.assets.requests
    assert [row['entity_id'] for row in turn['project']['shots'][0]['scene_contract']['objects']] == ['token-star', 'token-circle']
