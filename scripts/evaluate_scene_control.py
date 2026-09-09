"""Synthetic scene-control planning evaluation; never generates assets or video.

With no arguments, list the cases offline. --output NEW_DIR prepares fixtures
offline. Live planning requires --execute --model EXACT_INSTALLED_KEY --output
NEW_DIR, optionally --settings data/settings.json and --semantic-repair.

The live path reuses the game evaluation's production planner, bounded repair,
local model ownership and raw response records. It additionally calls the same
canonical scene staging helper as StoryManager before compiling. It does not
simulate saved video/reference continuity, commit world state or score pixels.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.compiler import compile_project
from backend.projects import atomic_json, check_project
from backend.stories import _stage_scene_contracts
from backend.world import apply_effects, validate_world
from scripts import evaluate_game_assistant as game


SCENE_EVALUATION_VERSION = 3
CHECK_CHANGE = ('Version 3 also permits the exact already-authored empty doorway in the camera-only Studio fixture. '
                'Version 2 permitted exact workbench/stool names, with at most one instance per identity. '
                'These fixture-only exceptions still reject room-as-prop rows, '
                'unknown prop names, duplicated furniture aliases and extra token identities. '
                'Version 1 rejected all non-token prop IDs; its original results remain unchanged.')
SCOPE = ('Real local planning, generated direction, production canonical scene staging and final H3 compilation. '
         'Checks measure explicit fixture invariants and assembled instructions, not visual obedience or writing quality. '
         'No references/assets/video jobs or saved-turn commits. Raw director contracts remain available before canonical staging.')


def fixtures():
    """Six deterministic, public-safe cases; no authored narrative or direction."""
    template = game.fixtures()[0]
    specs = [
        ('passive_seated_bystander', 'I ask Mira, "What is the workshop door code?"'),
        ('single_prop_mention', 'I ask Mira, "What is the token made of?" I only look at the token already in her hand.'),
        ('single_prop_handoff', 'I give Mira the brass token already in my hand. She accepts it and keeps holding it.'),
        ('two_distinct_tokens', 'I ask Mira, "Which mark is on your token?" We keep our own tokens in our hands.'),
        ('offscreen_voice', 'I ask Mira, "What is the workshop door code?" Keep Mira speaking from outside the camera view.'),
        ('camera_only', 'Make only the camera pan slowly right across this workshop. Every character keeps their established posture and place; nobody speaks or moves an object.'),
    ]
    cases = []
    for case_id, message in specs:
        case = copy.deepcopy(template)
        case.update(id=case_id, message=message, duration=8, intent=None,
                    mode='studio' if case_id == 'camera_only' else 'game',
                    review_focus='Inspect physical scene contract, passive seated actor, exact prop identities/holders and camera-versus-character motion. Text checks are not visual success.',
                    premise='Three fictional adults share a quiet workshop. Alex stands on the left; Mira stands at the workbench; Ivo sits on a stool behind them. Their established clothing and positions stay consistent.')
        case['world'] = validate_world({'schema_version': 1, 'current_location_id': 'workshop',
            'rules': ['Ordinary physical causality applies.',
                      'Ivo stays seated on his stool throughout this turn with quiet breathing and blinking. He does not speak, handle props or join another person\'s action.',
                      'A mentioned object stays with its current holder unless the player explicitly transfers or drops that object. Do not create additional tokens.'],
            'locations': [{'id': 'workshop', 'name': 'Workshop', 'description': 'A quiet room with one wooden workbench and a stool behind it; empty doorway.'}],
            'characters': [
                {'id': 'alex', 'name': 'Alex', 'control': 'player', 'location_id': 'workshop',
                 'description': 'Adult wearing a navy jacket.', 'speaking_style': 'English, calm',
                 'state': {'posture': 'standing', 'position': 'left of the workbench'}},
                {'id': 'mira', 'name': 'Mira', 'control': 'npc', 'location_id': 'workshop',
                 'description': 'Adult mechanic wearing a green jacket.', 'speaking_style': 'English, brief',
                 'personality': 'Helpful and practical. Answers direct workshop questions aloud in one short sentence.',
                 'private_knowledge': ['The workshop door code is 4729.', 'The round token is made of brass. Her token has a star marking.'],
                 'goals': ['Answer Alex\'s workshop questions and accept an explicitly offered token without setting it down.'],
                 'state': {'posture': 'standing', 'position': 'right of the workbench'}},
                {'id': 'ivo', 'name': 'Ivo', 'control': 'npc', 'location_id': 'workshop',
                 'description': 'Adult wearing an ochre shirt, seated on a wooden stool.',
                 'personality': 'A quiet observer who remains seated without speaking or handling objects.',
                 'goals': ['Keep resting on the stool.'],
                 'state': {'posture': 'seated', 'position': 'on the stool behind the workbench'}}],
            'entities': [{'id': 'token-star', 'name': 'Brass token', 'kind': 'object',
                          'description': 'One round brass token with a star marking.', 'holder_id': 'mira',
                          'affordances': ['examine', 'give', 'drop'],
                          'state': {'count': 1, 'material': 'brass', 'markings': 'star'}}]})
        project = case['project']
        project.update(id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'h3-scene-eval:' + case_id)),
                       title='Scene control evaluation: ' + case_id, duration=8,
                       game_player_id='alex', game_viewpoint='third-person',
                       custom_instructions='Keep the three established adults and their clothing consistent. Frame all three principal people throughout this shot. Ivo remains seated and silent. No new foreground people enter the quiet workshop.')
        project['shots'][0].update(id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'h3-scene-eval-shot:' + case_id)), duration=8)
        project['subjects'] = [{k: copy.deepcopy(c[k]) for k in ('id', 'name', 'description', 'asset_ids')}
                               for c in case['world']['characters']]
        case['scene_expectations'] = {'visible_ids': ['alex', 'mira', 'ivo'], 'offscreen_ids': [],
            'passive_postures': {'ivo': 'seated'}, 'final_holders': {'token-star': 'mira'},
            'object_counts': {'token-star': 1}, 'object_marks': {'token-star': 'star'},
            'character_locations': {'alex': 'workshop', 'mira': 'workshop', 'ivo': 'workshop'}}
        if case_id in ('passive_seated_bystander', 'offscreen_voice'):
            case['expected_spoken_fact'] = {'speaker_id': 'mira', 'code': '4729', 'forbidden_codes': []}
        if case_id == 'single_prop_handoff':
            case['world']['entities'][0]['holder_id'] = 'alex'
            case['intent'] = {'kind': 'give', 'target_id': 'token-star', 'recipient_id': 'mira'}
        if case_id == 'two_distinct_tokens':
            case['world']['entities'].append({'id': 'token-circle', 'name': 'Brass token', 'kind': 'object',
                'description': 'One round brass token with a circle marking.', 'holder_id': 'alex',
                'affordances': ['examine', 'give', 'drop'], 'state': {'count': 1, 'material': 'brass', 'markings': 'circle'}})
            case['scene_expectations']['final_holders']['token-circle'] = 'alex'
            case['scene_expectations']['object_counts']['token-circle'] = 1
            case['scene_expectations']['object_marks']['token-circle'] = 'circle'
        if case_id == 'offscreen_voice':
            case['scene_expectations'].update(visible_ids=['alex', 'ivo'], offscreen_ids=['mira'], object_counts={}, object_marks={})
            project['custom_instructions'] = project['custom_instructions'].replace('Frame all three principal people throughout this shot.', 'Frame only Alex and Ivo throughout this shot.')
            project['custom_instructions'] += ' Mira remains in the same room but outside this shot, speaking offscreen; do not cut to her.'
        if case_id == 'camera_only':
            case['scene_expectations']['passive_postures'].update(alex='standing', mira='standing')
            case['scene_expectations']['camera_only'] = True
            case['scene_expectations']['allowed_decor_names'] = {
                'workbench': ['workbench', 'wooden workbench'], 'stool': ['stool', 'wooden stool'],
                'doorway': ['doorway', 'empty doorway']}
        case['world'] = validate_world(case['world'])
        check_project(project)
        cases.append(case)
    return cases


def scene_checks(case, plan, project):
    """Check explicit synthetic facts; retain prose for separate manual review."""
    expected = case['scene_expectations']
    shots = project['shots']
    contracts = [shot.get('scene_contract') or {} for shot in shots]
    all_actors = [row for contract in contracts for row in contract.get('actors', [])]
    all_objects = [row for contract in contracts for row in contract.get('objects', [])]
    projected = apply_effects(case['world'], plan.get('effects', []), event_id='scene-evaluation-only')
    characters = {row['id']: row for row in projected['characters']}
    entities = {row['id']: row for row in projected['entities']}
    def allowed_inventory(contract):
        decor_counts = {name: 0 for name in expected.get('allowed_decor_names', {})}
        token_ids = set(expected['object_counts'])
        seen_ids = set()
        for row in contract.get('objects', []):
            if row['entity_id'] in seen_ids:
                return False
            seen_ids.add(row['entity_id'])
            if row['entity_id'] in token_ids:
                if row['count'] != expected['object_counts'][row['entity_id']]:
                    return False
                continue
            group = next((name for name, aliases in expected.get('allowed_decor_names', {}).items()
                          if row['name'].casefold() in aliases), None)
            if group is None:
                return False
            decor_counts[group] += row['count']
        return all(count <= 1 for count in decor_counts.values())
    checks = {
        'actor_contract_covers_visible_roster': all(
            sorted(row['subject_id'] for row in contract.get('actors', [])) == sorted(shot['visible_subject_ids'])
            for shot, contract in zip(shots, contracts)),
        'expected_visible_cast_preserved': all(set(expected['visible_ids']) <= set(shot['visible_subject_ids']) for shot in shots),
        'offscreen_voices_remain_offscreen': all(set(expected['offscreen_ids']) <= set(shot['offscreen_subject_ids'])
            and not set(expected['offscreen_ids']).intersection(shot['visible_subject_ids']) for shot in shots),
        'passive_actor_activity_and_posture': all(
            any(row['subject_id'] == sid for row in all_actors) and all(
                row['activity'] == 'hold' and posture in row['start'].casefold() and posture in row['end'].casefold()
                for row in all_actors if row['subject_id'] == sid)
            for sid, posture in expected['passive_postures'].items()),
        'canonical_character_locations_preserved': all(characters[sid]['location_id'] == location
            for sid, location in expected['character_locations'].items()),
        'canonical_object_holders_match': all(entities[eid]['holder_id'] == holder for eid, holder in expected['final_holders'].items()),
        'required_prop_ids_counts_and_marks': all(any(row['entity_id'] == eid and row['count'] == count
            and expected['object_marks'][eid] in row['description'].casefold() for row in all_objects)
            for eid, count in expected['object_counts'].items()),
        'no_unrequested_or_duplicate_prop_instances': all(allowed_inventory(contract) for contract in contracts),
        'known_actor_appearance_retained': all(any(subject['id'] == actor['id'] and actor['description'] in subject['description']
            for subject in project['subjects']) for actor in case['world']['characters']),
    }
    names = {row['id']: row['name'] for row in case['world']['characters']}
    checks['prop_endpoint_instructions_match_holders'] = all(any(
        row['entity_id'] == eid and ('held by ' + names[holder]).casefold() in row['end'].casefold()
        for row in contracts[-1].get('objects', [])) for eid, holder in expected['final_holders'].items()
        if eid in expected['object_counts'])
    if expected.get('camera_only'):
        checks['camera_only_no_dialogue_or_world_effects'] = not plan['dialogue'] and not plan.get('effects')
        checks['camera_pan_was_directed'] = any('pan' in shot['camera']['movement'].casefold() for shot in shots)
    return checks


def evaluate_case(case, client, exact_model, **options):
    record = game.evaluate_case(case, client, exact_model, **options)
    record.update(scene_evaluation_version=SCENE_EVALUATION_VERSION, scene_validation_scope=SCOPE,
                  scene_check_change=CHECK_CHANGE, scene_source_hashes=source_hashes())
    started = time.perf_counter()
    if record.get('compilation', {}).get('project') and record.get('plan'):
        before = copy.deepcopy(record['compilation'])
        record['before_canonical_staging'] = before
        try:
            project = copy.deepcopy(before['project'])
            _stage_scene_contracts(project, case['world'], record['plan'], case['player_character_id'], game_mode=case['mode'] == 'game')
            project = check_project(project)
            compiled = compile_project(project)
            record['compilation'] = {'project': project, 'valid': compiled['valid'], 'issues': compiled['issues'], 'prompt': compiled['prompt']}
            record['scene_control_checks'] = scene_checks(case, record['plan'], project)
            record['automatic_checks'].update(record['scene_control_checks'])
            record['automatic_checks']['compiled_prompt_ready'] = compiled['valid'] is True
            if record['status'] != 'failed':
                record['status'] = 'validated' if all(record['automatic_checks'].values()) else 'invariant_failed'
        except Exception as exc:
            record.update(status='failed', scene_control_error=str(exc), scene_control_error_code=type(exc).__name__)
    prompt = record.get('compilation', {}).get('prompt', '')
    stages = record['stage_records']
    input_tokens = [((row.get('diagnostics') or {}).get('usage') or {}).get('prompt_tokens') for row in stages]
    input_tokens = [value for value in input_tokens if type(value) is int]
    record['scene_metrics'] = {'compiled_prompt_chars': len(prompt), 'compiled_prompt_words': len(prompt.split()),
        'stage_prompt_tokens': sum(input_tokens) if input_tokens else None,
        'stage_completion_tokens': sum(row.get('completion_tokens', 0) for row in stages),
        'stages_with_completion_usage': sum(type(row.get('completion_tokens')) is int for row in stages),
        'maximum_stage_input_chars': max((len(row['system']) + len(json.dumps(row['content'], ensure_ascii=False)) for row in stages), default=0),
        'canonical_staging_and_checks_seconds': time.perf_counter() - started,
        'action_exact_occurrences': prompt.count(record.get('plan', {}).get('action', '')) if record.get('plan', {}).get('action') else None}
    # Missing usage stays distinguishable from a measured zero-token answer.
    if not record['scene_metrics']['stages_with_completion_usage']:
        record['scene_metrics']['stage_completion_tokens'] = None
    record['elapsed_seconds'] += record['scene_metrics']['canonical_staging_and_checks_seconds']
    return record


def source_hashes():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
        ('backend/game_director.py', 'backend/stories.py', 'backend/scene_contract.py', 'backend/compiler.py',
         'scripts/evaluate_game_assistant.py', 'scripts/evaluate_scene_control.py')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--list-cases', action='store_true')
    parser.add_argument('--model', action='append')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--case', action='append', dest='case_ids')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--semantic-repair', action='store_true')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args(argv)
    cases = fixtures()
    if args.case_ids:
        unknown = set(args.case_ids) - {case['id'] for case in cases}
        if unknown:
            parser.error('Unknown case: ' + ', '.join(sorted(unknown)))
        cases = [case for case in cases if case['id'] in args.case_ids]
    if not 1 <= args.repeat <= 10 or not 1 <= args.timeout <= 600:
        parser.error('Use repeat 1–10 and timeout 1–600 seconds.')
    if args.list_cases or not args.execute:
        for case in cases:
            print(case['id'] + ': ' + case['message'])
        if args.output and not args.list_cases:
            args.output.mkdir(parents=True, exist_ok=False)
            atomic_json(args.output / 'prepared.json', {'status': 'prepared_offline', 'version': SCENE_EVALUATION_VERSION,
                'scope': SCOPE, 'check_change': CHECK_CHANGE, 'fixtures': cases, 'source_hashes': source_hashes(), 'model_calls': 0, 'render_jobs': 0})
        return 0
    if not args.model or args.output is None:
        parser.error('--execute requires --model and a new --output directory.')
    settings = copy.deepcopy(game.DEFAULT_SETTINGS)
    if args.settings:
        supplied = json.loads(args.settings.read_text(encoding='utf-8-sig'))
        settings.update({key: supplied[key] for key in ('lm_url', 'comfy_urls', 'context_length') if key in supplied})
    if type(settings['context_length']) is not int or not 1024 <= settings['context_length'] <= 32768:
        parser.error('Context length must be an integer from 1024 to 32768.')
    if not isinstance(settings['comfy_urls'], list) or not settings['comfy_urls']:
        parser.error('Supply local Comfy server URLs in settings before loading a model.')
    try:
        game.LMStudioClient(settings['lm_url'], timeout=args.timeout)
        settings['comfy_urls'] = [game.local_url(url) for url in settings['comfy_urls']]
    except ValueError as exc:
        parser.error(str(exc))
    hashes = source_hashes()
    report = game.run_suite(list(dict.fromkeys(args.model)), cases, settings, args.output,
        repeats=args.repeat, timeout=args.timeout, semantic_repair=args.semantic_repair, case_evaluator=evaluate_case)
    report.update(scene_evaluation_version=SCENE_EVALUATION_VERSION, scene_validation_scope=SCOPE, scene_check_change=CHECK_CHANGE, source_hashes=hashes,
                  source_hashes_after=source_hashes())
    atomic_json(args.output / 'report.json', report)
    print('Report: ' + str((args.output / 'report.json').resolve()))
    return 2 if report.get('error') else int(any(model['summary']['failed_model_cases'] for model in report['models']))


if __name__ == '__main__':
    raise SystemExit(main())
