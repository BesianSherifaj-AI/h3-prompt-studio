"""Prepare/render an original 30-second scene-continuity demonstration.

Uses the existing isolated render runner and durable POST receipts. Preparation
is offline; --execute submits three authored 10-second clips at 0.2 MP. This is
a directing/continuation demonstration, not an autonomous writing benchmark.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.compiler import compile_project
from backend.game_director import direct_plan, validate_narrative
from backend.projects import atomic_json, new_project, shot
from backend.stories import validate_plan
from backend.world import world_from_project
from scripts.render_story_examples import LocalAPI, Runner, NeedsAttention, verify_and_assemble, endpoint_url, _digest, _uuid


STYLE = 'Flat 2D pixel art, crisp square pixels, simple readable sprite silhouettes, teal, cream and warm red palette; side-on retro adventure game view.'
LABEL = 'Original authored scene-control demonstration; not autonomous writing or guaranteed video compliance.'


def demonstration(run_key):
    ids = {key: _uuid(run_key + ':' + key) for key in ('mira', 'ivo', 'coin', 'bench', 'project')}
    p = new_project()
    p.update(id=ids['project'], title='The Coin Safety Inspector', mode='t2va', duration=10, profile='custom', authoring_mode='full')
    p['subjects'] = [
        {'id': ids['mira'], 'name': 'Mira', 'description': 'Adult woman with short dark hair, teal knee-length coat, cream trousers and brown boots.', 'asset_ids': []},
        {'id': ids['ivo'], 'name': 'Ivo', 'description': 'Adult man with a red cap, gray shirt, navy trousers and dark shoes.', 'asset_ids': []},
    ]
    p['style'] = {'genre': 'silent pixel comedy', 'notes': STYLE, 'lighting': 'steady warm daylight', 'color': 'teal, cream and warm red'}
    setting = 'Side-on view of a quiet courtyard, cream plaster wall behind one blue wooden bench. Mira stands left of the bench. Ivo sits on its right end. Clear empty ground and steady warm daylight.'
    p['story']['text'] = 'Mira and the very serious seated inspector Ivo handle exactly one brass coin with exaggerated care. Ivo remains seated throughout.'
    p['custom_instructions'] = 'Original silent comedy. One continuous side-on view. No spoken words, subtitles or overlays. All movements are small and easy to follow. ' + STYLE
    p['shots'] = [shot(10)]
    world = world_from_project(p)
    scenarios = [
        ('Mira slowly raises the single brass coin already in her right hand to shoulder height, inspecting it with mock seriousness. Ivo remains seated at the right end of the blue bench, hands resting on his knees, watching without reaching.',
         'Mira stands left of the bench, right hand raised with the one brass coin. Ivo remains seated, both hands on his knees.',
         ('standing left of the bench, the single coin in her right hand at waist height', 'slowly raises only her right hand with the existing coin to shoulder height', 'standing in the same place, the coin visible in her raised right hand'),
         ('seated on the right end of the bench, both hands on his knees', 'quietly watches Mira; only a blink and subtle breathing, hands remain on knees', 'still seated at the right end, hands on knees'),
         'in Mira’s right hand at waist height', 'in Mira’s raised right hand', 'hold'),
        ('Mira lowers her right hand and offers the existing brass coin toward Ivo. Ivo remains seated, extends his left palm and receives that same coin. Mira releases the coin and withdraws her now-empty right hand. Both pause.',
         'Ivo remains seated with the single brass coin in his left palm. Mira stands left of the bench with empty hands lowered.',
         ('standing left of the bench, holding the one coin in her right hand', 'offers that existing coin to Ivo’s extended left palm, releases it, then withdraws her empty hand', 'standing left, both hands empty and lowered'),
         ('seated on the right end of the bench, hands on knees', 'stays seated; extends only his left hand, receives the existing coin and holds it in that palm', 'still seated, the single brass coin in his left palm'),
         'in Mira’s right hand; Ivo has no coin', 'in Ivo’s left palm; Mira’s hands are empty', 'hold'),
        ('Ivo remains seated and carefully places the one brass coin from his left palm onto the empty blue bench seat immediately to his left, then returns that empty hand to his knee. Mira remains standing with empty hands and makes a tiny satisfied nod. Both hold their places.',
         'Exactly one brass coin rests on the blue bench immediately left of seated Ivo. Ivo’s hands rest empty on his knees. Mira stands left of the bench with empty hands.',
         ('standing left of the bench with empty hands lowered', 'stays in place with empty hands; gives one tiny satisfied nod after the coin is placed', 'standing in the same place with empty hands'),
         ('seated on the right end of the bench, the single coin in his left palm', 'stays seated; places the existing coin on the seat immediately to his left, then returns his empty left hand to his knee', 'still seated at the right end, both hands empty on knees'),
         'in Ivo’s left palm', 'resting on the bench immediately to Ivo’s left; neither character holds a coin', 'hold'),
    ]
    plans = []
    for i, (action, end, mira, ivo, prop_start, prop_end, ivo_activity) in enumerate(scenarios):
        contract = {'actors': [dict(subject_id=ids['mira'], activity='act' if i < 2 else 'hold', start=mira[0], action=mira[1], end=mira[2]),
                               dict(subject_id=ids['ivo'], activity=ivo_activity, start=ivo[0], action=ivo[1], end=ivo[2])],
            'objects': [dict(entity_id=ids['coin'], name='Brass coin', count=1,
                             description='One small round flat golden brass disk, unchanged size and color.', start=prop_start, end=prop_end),
                        dict(entity_id=ids['bench'], name='Blue bench', count=1,
                             description='One low blue wooden bench with a flat seat and two legs.',
                             start='stationary against the cream wall; Ivo sits on its right end', end='unchanged in the same place')],
            'environment': setting, 'background_activity': 'Empty courtyard, still wall and ground; no other people, animals or moving background objects.'}
        beat = dict(id=f'beat-{i+1}', action=action, setting=setting, final_state=end)
        plan = {'action': action, 'setting': setting, 'final_state': end, 'transition': 'cut' if i == 0 else 'continue',
            'dialogue': [], 'characters': [dict(id=s['id'], name=s['name'], description=s['description'], voice='silent') for s in p['subjects']],
            'asset_requests': [], 'effects': [], 'choices': [dict(title=t, message=m) for t, m in
                [('Continue', 'Continue the authored performance.'), ('Hold', 'Hold this final pose.'), ('Finish', 'Finish the sequence.')]],
            'beats': [beat], 'direction': {'shots': [dict(beat_id=beat['id'], duration=10,
                camera=dict(framing='medium-wide full-body two-shot', movement='static', height='eye level', speed='still', focus='Both characters, their hands, the single coin and the bench.'),
                performance='Use a slow readable beginning, one clear action, then a held ending. Keep both full bodies and hands visible.',
                sound='Quiet courtyard ambience and soft cloth movement; one small metallic tap only when the coin contacts the bench. No speech or music.',
                visible_subject_ids=[ids['mira'], ids['ivo']], offscreen_subject_ids=[], dialogue_indices=[], transition='continuous',
                scene_contract=contract, scene_contract_source='authored')]}}
        validate_plan(plan, mode='studio', duration=10)
        validate_narrative(plan, world=world, player_character_id=None, message=action, duration=10, mode='studio')
        directed = direct_plan(plan, p, duration=10)
        assert compile_project(directed)['valid']
        plans.append(plan)
    return {'kind': 'continuity', 'title': p['title'], 'classification': LABEL, 'target_seconds': 30, 'plans': plans,
        'create_body': {'request_id': _uuid(run_key + ':create'), 'mode': 'studio', 'title': p['title'], 'premise': p['story']['text'],
                        'project': p, 'world': world, 'settings': {'duration': 10, 'steps': 8, 'resolution': '0.2',
                            'experimental_preview': True, 'aspect_ratio': '16:9', 'style': STYLE,
                            'review_before_render': False, 'transition': 'auto', 'seed': 909202630}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8768')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    endpoint = endpoint_url(args.endpoint)
    directory = args.output.resolve()
    if args.resume:
        state = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
        if state['endpoint'] != endpoint:
            raise ValueError('Resume with the original endpoint.')
    else:
        directory.mkdir(parents=True, exist_ok=False)
        key = str(uuid.uuid4())
        state = dict(schema_version=1, run_key=key, endpoint=endpoint, status='prepared', classification=LABEL,
                     operations={}, results={}, examples=[demonstration(key)])
        atomic_json(directory / 'run.json', state)
    if not args.execute:
        print(json.dumps({'status': 'prepared_no_network_or_gpu_work', 'receipt': str(directory / 'run.json')}))
        return 0
    api = LocalAPI(endpoint)
    runner = Runner(directory, state, api)
    state.setdefault('initial_settings_hash', api.settings_hash)
    state['status'] = 'running'
    runner.save()
    try:
        for example in state['examples']:
            result = runner.render(example)
            verify_and_assemble(runner, example, result)
        state.update(status='completed', global_settings_unchanged=state['initial_settings_hash'] == _digest(api.get('/api/bootstrap').get('settings', {})))
        runner.save()
        print(json.dumps({'status': state['status'], 'outputs': [r['output'] for r in state['results'].values()]}))
        return 0
    except Exception as exc:
        state.update(status='needs_attention', error=str(exc)[:2000])
        runner.save()
        raise
    finally:
        api.close()


if __name__ == '__main__':
    raise SystemExit(main())
