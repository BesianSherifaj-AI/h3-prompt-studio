"""Repeatable, local-only Game/Studio planning evaluation; never queues a render.

Examples (run from the project directory):
  .venv/Scripts/python scripts/evaluate_game_assistant.py --list-cases
  .venv/Scripts/python scripts/evaluate_game_assistant.py --model EXACT_LOCAL_KEY \
      --settings data/settings.json --output staging/eval-run-001

The output directory must be new. Settings are read, never changed. Close other
assistant work before starting: loaded instances from another session are never
adopted or unloaded. ResourceManager checks Comfy queues and may release idle
Comfy weights to prepare the explicitly selected model. No images, assets, video
jobs, model downloads or remote inference are requested. Set LM_STUDIO_API_KEY
in the environment if the loopback server requires authentication.

Automatic checks measure structural validity, final prompt compilation and explicit invariants. They do
not establish whether an NPC's writing is convincing. Every model result keeps
its raw stage output and a manual-review requirement.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsonschema import Draft202012Validator

from backend.compiler import compile_project
from backend.game_director import direct_plan, plan_turn, quoted_speech
from backend.lmstudio import LMStudioClient
from backend.projects import atomic_json, check_project, new_project
from backend.resources import ResourceManager, local_url
from backend.scene_contract import director_output_budget
from backend.world import apply_effects, character_can_act, resolve_intent, validate_world


DEFAULT_SETTINGS = {'lm_url': 'http://127.0.0.1:1234/v1', 'context_length': 8192,
                    'comfy_urls': ['http://127.0.0.1:8188', 'http://127.0.0.1:8000', 'http://127.0.0.1:8010'],
                    'ai_memory_mode': 'exclusive'}
DEFAULT_BUDGETS = {'actor': 450, 'roleplay': 1800, 'director': 1400, 'language': 350}
EVALUATION_VERSION = 2
VALIDATION_SCOPE = ('Version 2 additionally compiles the prepared directed project and requires compiled.valid. '
                    'Version 1 checked narrative/direction and explicit invariants without final prompt compilation. '
                    'Version 2 fixtures use the valid H3 t2va mode instead of the old t2v fixture spelling. '
                    'No assets or renders are generated; writing and visual quality still require manual review.')
UNCERTAIN_ERRORS = {'request_timeout', 'connection_error', 'redirect_rejected',
                    'response_too_large', 'model_load_uncertain', 'model_unverified'}


def fixtures():
    """Synthetic, deterministic cases without user documents or reference images."""
    def scene(case_id, message, *, intent=None, mode='game'):
        project = new_project()
        project['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'h3-eval:' + case_id))
        project['shots'][0]['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'h3-eval-shot:' + case_id))
        project['title'] = 'Assistant evaluation: ' + case_id
        project['mode'] = 't2va'
        project['game_language'] = 'English'
        project['custom_instructions'] = 'Ground each action in the current workshop. Mira is cautious, practical, and speaks briefly.'
        world = validate_world({'schema_version': 1, 'current_location_id': 'workshop',
            'rules': ['Ordinary physical causality applies. An attack is an attempt; opponents can resist.',
                      'A locked door stays locked until an established unlocking action succeeds.'],
            'locations': [{'id': 'workshop', 'name': 'Workshop', 'description': 'A workbench, a brass key and a closed oak door.'}],
            'characters': [
                {'id': 'alex', 'name': 'Alex', 'control': 'player', 'description': 'Adult wearing a blue coat.', 'speaking_style': 'English, calm'},
                {'id': 'mira', 'name': 'Mira', 'control': 'npc', 'description': 'Adult mechanic wearing a green jacket.',
                 'personality': 'Cautious and practical; defends herself when threatened.', 'speaking_style': 'English, terse',
                 'goals': ['Finish repairing the workbench.'], 'private_knowledge': ['A spare tool is hidden behind the bench.']}],
            'entities': [
                {'id': 'key', 'name': 'Brass key', 'affordances': ['take', 'drop', 'give', 'examine']},
                {'id': 'door', 'name': 'Oak door', 'affordances': ['open', 'close', 'examine'], 'state': {'open': False, 'locked': False}}]})
        project['subjects'] = [{k: copy.deepcopy(c[k]) for k in ('id', 'name', 'description', 'asset_ids')} for c in world['characters']]
        return {'id': case_id, 'project': project, 'world': world, 'player_character_id': 'alex',
                'message': message, 'duration': 5, 'mode': mode, 'intent': intent,
                'premise': 'Alex visits the cautious mechanic Mira in her workshop. A loose workbench leg, a brass key, and a closed oak door are in the room.',
                'review_focus': 'Does the response follow the attempted action, portray independent NPC behavior, and preserve the current scene?'}

    result = [
        scene('combat_attempt', 'I lunge toward Mira and try to punch her shoulder.', intent={'kind': 'attack', 'target_id': 'mira'}),
        scene('item_pickup', 'I take the brass key from the workbench.', intent={'kind': 'take', 'target_id': 'key'}),
        scene('item_drop', 'I drop the brass key beside the workbench.', intent={'kind': 'drop', 'target_id': 'key'}),
        scene('item_give', 'I offer Mira the brass key.', intent={'kind': 'give', 'target_id': 'key', 'recipient_id': 'mira'}),
        scene('door_open', 'I open the oak door.', intent={'kind': 'open', 'target_id': 'door'}),
        scene('locked_door_guard', 'I open the locked oak door.', intent={'kind': 'open', 'target_id': 'door'}),
        scene('locked_door_attempt', 'I press the handle of the locked oak door without a key; show what actually happens.'),
        scene('addressed_npc', 'Mira, show me the broken hinge.'),
        scene('multilingual_quote', 'I tell Mira: “Ku është çelësi?”'),
        scene('text_only_start', 'I kneel beside the workbench and inspect its loose leg.'),
        scene('long_history', 'I examine the brass key currently on the workbench.'),
        scene('studio_author_mode', 'Direct Alex to wave while Mira silently checks the oak door.', mode='studio'),
        scene('npc_direct_factual_answer', 'I ask Mira, "What is the door code?"', intent={'kind': 'talk', 'target_id': 'mira'}),
        scene('weakened_enemy_finish', 'I strike the Clockwork Bandit\'s exposed core with my already-held sword, trying to kill it.',
              intent={'kind': 'attack', 'target_id': 'bandit'}),
    ]
    by_id = {case['id']: case for case in result}
    for name in ('item_drop', 'item_give'):
        by_id[name]['world']['entities'][0]['holder_id'] = 'alex'
    for name in ('locked_door_guard', 'locked_door_attempt'):
        by_id[name]['world']['entities'][1]['state']['locked'] = True
    by_id['locked_door_guard']['expected_error'] = 'locked'
    by_id['locked_door_guard']['review_focus'] = 'Deterministic interaction guard: zero model calls expected. Excluded from model quality metrics.'
    direct = by_id['addressed_npc']
    # Two earlier bystanders must not displace the NPC explicitly named by the player.
    direct['world']['characters'][1:1] = [
        {'id': 'ben', 'name': 'Ben', 'control': 'npc', 'description': 'Adult in gray overalls.'},
        {'id': 'cara', 'name': 'Cara', 'control': 'npc', 'description': 'Adult in a brown apron.'}]
    direct['world'] = validate_world(direct['world'])
    direct['project']['subjects'] = [{k: copy.deepcopy(c[k]) for k in ('id', 'name', 'description', 'asset_ids')} for c in direct['world']['characters']]
    direct['expected_first_actor'] = 'mira'
    multilingual = by_id['multilingual_quote']
    multilingual['project']['game_language'] = 'Albanian'
    multilingual['world']['characters'][0]['speaking_style'] = 'Albanian, calm'
    multilingual['expected_player_language'] = 'Albanian'
    # An opening scene can have only its premise, without accepted location
    # state or images yet. The writer must still receive those scenario facts.
    opening = by_id['text_only_start']['world']
    opening.update(current_location_id=None, locations=[], entities=[])
    for character in opening['characters']:
        character['location_id'] = None
    long = by_id['long_history']['world']
    by_id['long_history']['preserve_possession'] = ['key']
    long['events'] = [{'id': f'past-{index}', 'summary': f'Past inspection {index}: Alex and Mira checked a workbench screw and returned to their places.',
                       'witness_ids': ['alex', 'mira']} for index in range(300)]
    for character in long['characters']:
        character['witnessed_events'] = [event['id'] for event in long['events']]

    factual = by_id['npc_direct_factual_answer']
    factual.update(evaluation_contract='npc_direct_factual_answer_v1', expected_first_actor='mira',
                   expected_spoken_fact={'speaker_id': 'mira', 'code': '4729', 'forbidden_codes': ['8642']},
                   review_focus='Mira must answer the actual question in spoken dialogue using her known code. A nod, generic assistance or private intent is insufficient. Check natural in-character phrasing manually.')
    factual['project']['custom_instructions'] = 'Mira cooperates with direct workshop questions and answers briefly in character.'
    factual['world']['characters'][1].update(personality='Helpful, direct and practical.',
        private_knowledge=['The workshop door code is 4729. Mira willingly tells Alex this code when asked.'],
        goals=['Help Alex enter the workshop.'])
    factual['world']['characters'].append({'id': 'ben', 'name': 'Ben', 'description': 'A quiet visitor in gray overalls.',
                                          'private_knowledge': ['Ben alone knows his unrelated private locker code is 8642.']})
    factual['world'] = validate_world(factual['world'])
    factual['project']['subjects'] = [{key: copy.deepcopy(character[key]) for key in ('id', 'name', 'description', 'asset_ids')}
                                     for character in factual['world']['characters']]

    finish = by_id['weakened_enemy_finish']
    finish.update(evaluation_contract='weakened_enemy_finish_v1', expected_first_actor='bandit',
                  expected_combat_outcome={'target_id': 'bandit', 'health': 0, 'protected_ids': ['alex', 'mira']},
                  premise='In a fictional pixel game workshop, Alex already holds a sword within striking reach of a weakened Clockwork Bandit. Mira stands safely away from the exchange.',
                  review_focus='Execute the grounded finishing strike and show its consequence, rather than another preparation. Effects must record zero health and defeated/dead status only for the weakened target. Manually inspect agreement between the visible ending and recorded state; no graphic detail is needed.')
    finish['project']['custom_instructions'] = 'A brief non-graphic fictional pixel game combat exchange. Resolve the established game rules without inventing extra attacks, weapons or victims.'
    finish['world']['rules'] = [
        'The Clockwork Bandit is at arm\'s reach with its core exposed, one health remaining and no ability to evade or counterattack this turn.',
        'One sword strike to that exposed core deterministically deals one damage. At zero health record health=0 and defeated=true (or dead=true); the target then stops acting.',
        'Alex already holds the sword. Only the named target is struck; the player and the distant bystander Mira take no damage. Do not add a pickup or another attack.']
    finish['world']['characters'][0]['state'] = {'health': 3}
    finish['world']['characters'][1].update(id='bandit', name='Clockwork Bandit',
        description='A weakened fictional clockwork enemy at arm\'s reach, with its chest core exposed.',
        personality='A damaged mechanical opponent.', goals=['Remain upright.'], private_knowledge=[],
        state={'health': 1, 'core_exposed': True, 'can_dodge': False, 'can_counterattack': False})
    finish['world']['characters'].append({'id': 'mira', 'name': 'Mira',
        'description': 'The mechanic stands safely beside the distant workbench, away from the strike.', 'state': {'health': 5}})
    finish['world']['entities'].append({'id': 'sword', 'name': 'Sword', 'kind': 'weapon',
        'description': 'A short sword already held by Alex.', 'holder_id': 'alex', 'affordances': ['examine', 'drop', 'give']})
    finish['world'] = validate_world(finish['world'])
    finish['project']['subjects'] = [{key: copy.deepcopy(character[key]) for key in ('id', 'name', 'description', 'asset_ids')}
                                    for character in finish['world']['characters']]
    return result


def _spoken_codes(text):
    """Match a known four-digit fact, allowing ordinary spoken digit spelling."""
    digits = {'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
              'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9'}
    normalized = re.sub(r'\b(?:' + '|'.join(digits) + r')\b', lambda match: digits[match[0].lower()], text, flags=re.I)
    return {re.sub(r'\D', '', match[0]) for match in re.finditer(r'(?<!\d)\d(?:[\s,-]*\d){3}(?!\d)', normalized)}


def role_outcome_checks(case, plan):
    """Explicit fact/state checks, not a general-purpose prose-quality judge."""
    checks = {}
    if case.get('expected_spoken_fact'):
        expected = case['expected_spoken_fact']
        answers = [line['text'] for line in plan['dialogue'] if line.get('speaker_id') == expected['speaker_id'] and line['text'].strip()]
        codes = set().union(*(_spoken_codes(answer) for answer in answers)) if answers else set()
        all_spoken_codes = set().union(*(_spoken_codes(line['text']) for line in plan['dialogue'])) if plan['dialogue'] else set()
        checks['addressed_npc_supplied_spoken_answer'] = bool(answers)
        checks['spoken_answer_contains_established_fact'] = expected['code'] in codes
        checks['unrelated_private_fact_not_spoken'] = not all_spoken_codes.intersection(expected['forbidden_codes'])
    if case.get('expected_combat_outcome'):
        expected = case['expected_combat_outcome']
        before = {character['id']: character for character in case['world']['characters']}
        projected = apply_effects(case['world'], plan['effects'], event_id='evaluation-only:' + case['id'], actor_id=case['player_character_id'])
        after = {character['id']: character for character in projected['characters']}
        target = after[expected['target_id']]
        health = target['state'].get('health')
        checks['target_health_resolved_to_zero'] = type(health) in (int, float) and health == expected['health']
        terminal = target['state'].get('dead') is True or target['state'].get('defeated') is True or target['state'].get('alive') is False or str(target['state'].get('status', '')).casefold() in ('dead', 'defeated')
        checks['target_defeated_or_dead'] = terminal and not character_can_act(target)
        def unharmed(character_id):
            old, new = before[character_id], after[character_id]
            old_health, new_health = old['state'].get('health'), new['state'].get('health')
            health_preserved = (type(new_health) in (int, float) and new_health >= old_health) if type(old_health) in (int, float) else new_health == old_health
            return health_preserved and (not character_can_act(old) or character_can_act(new))
        checks['no_unrelated_character_harmed'] = all(unharmed(character_id) for character_id in expected['protected_ids'])
    return checks


def evaluate_case(case, client, exact_model, *, repeat=1, temperature=.55, budgets=None, semantic_repair=False):
    """Run the production planner and retain evidence of every prediction attempt."""
    budgets = {**DEFAULT_BUDGETS, **(budgets or {})}
    original = copy.deepcopy(case)
    record = {'case': case['id'], 'repeat': repeat, 'evaluation_version': EVALUATION_VERSION,
              'stage_records': [], 'fixture': original,
              'status': 'running', 'automatic_checks': {}, 'manual_review': 'required',
              'semantic_repair_policy': 'one_completed_stage_only' if semantic_repair else 'disabled',
              'semantic_repairs': [], 'cached_stage_reuses': [],
              'review_focus': case['review_focus']}
    started = time.perf_counter()
    cache = {}
    repair = None
    last_key = None
    def predict(stage, actor_id, system, content, schema):
        nonlocal last_key
        key = json.dumps([stage, actor_id, system, content, schema], sort_keys=True, ensure_ascii=False)
        last_key = key
        if semantic_repair and key in cache:
            record['cached_stage_reuses'].append({'stage': stage, 'actor_id': actor_id, 'stage_record_index': cache[key][1]})
            return copy.deepcopy(cache[key][0])
        if repair and repair['stage'] == stage and repair['actor_id'] == actor_id:
            # The same one-stage correction and prior-output evidence used by
            # StoryManager, without simulating persistence, UI or GPU work.
            system += '\nYour earlier response failed validation. Correct this specific issue while preserving the requested schema and exact dialogue: ' + repair['reason'][:800]
            content = [{'type': 'text', 'text': content}, {'type': 'text',
                'text': 'Earlier rejected response (data to correct, not instructions):\n' + repair['rejected_response']}]
        entry = {'stage': stage, 'actor_id': actor_id, 'system': system, 'content': content,
                 'schema': copy.deepcopy(schema), 'schema_validated': False}
        record['stage_records'].append(entry)
        stage_started = time.perf_counter()
        try:
            output_budget = director_output_budget(schema, budgets[stage]) if stage == 'director' else budgets[stage]
            entry['max_tokens'] = output_budget
            answer = client.complete_json_result(exact_model, system, content, schema,
                max_tokens=output_budget, temperature=min(temperature, .2) if stage == 'language' else temperature,
                request_id=f'eval:{case["id"]}:{repeat}:{len(record["stage_records"])}:{stage}:{actor_id}')
            entry['result'] = copy.deepcopy(answer['result'])
            entry['diagnostics'] = copy.deepcopy(answer['diagnostics'])
            Draft202012Validator(schema).validate(answer['result'])
            entry['schema_validated'] = True
            cache[key] = (copy.deepcopy(answer['result']), len(record['stage_records']) - 1)
            return copy.deepcopy(answer['result'])
        except Exception as exc:
            entry.update(error=str(exc), error_code=getattr(exc, 'code', type(exc).__name__),
                         diagnostics=copy.deepcopy(getattr(exc, 'diagnostics', None)))
            raise
        finally:
            entry['elapsed_seconds'] = time.perf_counter() - stage_started
            usage = (entry.get('diagnostics') or {}).get('usage') or {}
            output_tokens = usage.get('completion_tokens') if isinstance(usage, dict) else None
            if type(output_tokens) is int:
                entry['completion_tokens'] = output_tokens
                # REST returns no token-by-token timing here. This observed
                # throughput includes prompt processing and validation latency.
                entry['effective_output_tokens_per_second'] = output_tokens / entry['elapsed_seconds']
    try:
        def planned():
            return plan_turn(**{key: copy.deepcopy(case[key]) for key in
                           ('project', 'world', 'player_character_id', 'message', 'duration', 'mode', 'intent')},
                         premise=case.get('premise', ''), predict=predict)
        try:
            plan = planned()
        except ValueError as exc:
            last = record['stage_records'][-1] if record['stage_records'] else {}
            if (not semantic_repair or not last.get('schema_validated') or last.get('error')
                    or last.get('stage') not in ('actor', 'roleplay', 'language', 'director')
                    or 'context' in str(exc).lower() or 'does not fit' in str(exc).lower()):
                raise
            repair = {'stage': last['stage'], 'actor_id': last['actor_id'], 'reason': str(exc),
                      'rejected_response': json.dumps(last['result'], ensure_ascii=False)[:6000],
                      'rejected_stage_record_index': len(record['stage_records']) - 1}
            record['semantic_repairs'].append(copy.deepcopy(repair))
            cache.pop(last_key, None)
            plan = planned()  # At most one app-level repair; failure remains a failure.
        record['plan'] = plan
        checks = record['automatic_checks']
        checks['production_pipeline_validated'] = True
        checks['fixture_unchanged'] = case == original
        if case['mode'] == 'game':
            player_lines = [line for line in plan['dialogue'] if line['speaker_id'] == case['player_character_id']]
            checks['exact_player_quotes'] = [line['text'] for line in player_lines] == quoted_speech(case['message'])
            if case.get('expected_player_language'):
                checks['player_language'] = all(line['language'] == case['expected_player_language'] for line in player_lines)
        if case['intent']:
            expected = resolve_intent(case['world'], case['player_character_id'], case['intent'])['effects']
            checks['selected_interaction_effects'] = all(effect in plan['effects'] for effect in expected)
        if case.get('expected_first_actor'):
            actors = [entry['actor_id'] for entry in record['stage_records'] if entry['stage'] == 'actor']
            checks['addressed_npc_acts_first'] = bool(actors) and actors[0] == case['expected_first_actor']
        if case.get('preserve_possession'):
            protected = {entity['id']: entity for entity in case['world']['entities']
                         if entity['id'] in case['preserve_possession']}
            checks['inspection_preserves_possession'] = not any(
                effect.get('entity_id') in protected
                and ((effect.get('kind') == 'holder'
                      and effect.get('character_id') != protected[effect['entity_id']]['holder_id'])
                     or (effect.get('kind') == 'entity_location'
                         and effect.get('location_id') != protected[effect['entity_id']]['location_id']))
                for effect in plan['effects'])
        if case['id'] == 'locked_door_attempt':
            checks['locked_door_not_opened_by_effect'] = not any(
                effect.get('entity_id') == 'door' and effect.get('kind') == 'entity_state'
                and ((effect.get('key') == 'open' and effect.get('value') is True)
                     or (effect.get('key') == 'locked' and effect.get('value') is False)) for effect in plan['effects'])
        if case['mode'] == 'studio':
            checks['no_game_actor_requests'] = not any(entry['stage'] == 'actor' for entry in record['stage_records'])
        checks.update(role_outcome_checks(case, plan))
        if case.get('expected_error'):
            checks['expected_guard_rejected'] = False
        compile_started = time.perf_counter()
        compilation = record['compilation'] = {'valid': False, 'issues': []}
        try:
            # Reuse the already generated direction without another model call.
            # This is the final prompt boundary, not a render/asset submission.
            prepared = direct_plan(plan, check_project(copy.deepcopy(case['project'])),
                                   duration=case['duration'], game_mode=case['mode'] == 'game')
            compilation['project'] = prepared
            compiled = compile_project(prepared)
            compilation.update(valid=compiled['valid'], issues=copy.deepcopy(compiled.get('issues', [])),
                               prompt=compiled['prompt'])
            checks['compiled_prompt_ready'] = compiled['valid'] is True
        except Exception as exc:
            compilation.update(error=str(exc), error_code=type(exc).__name__)
            checks['compiled_prompt_ready'] = False
            raise
        finally:
            compilation['elapsed_seconds'] = time.perf_counter() - compile_started
        record['status'] = 'validated' if all(checks.values()) else 'invariant_failed'
    except Exception as exc:
        record.update(status='failed', error=str(exc), error_code=getattr(exc, 'code', type(exc).__name__))
        if (case.get('expected_error') and case['expected_error'].lower() in str(exc).lower()
                and not record['stage_records'] and isinstance(exc, ValueError)):
            record.update(status='expected_guard_rejection', manual_review='not_applicable_no_model_call')
            record['automatic_checks']['expected_guard_rejected_before_inference'] = True
    record['elapsed_seconds'] = time.perf_counter() - started
    return record


def summarize(records):
    """Report measured validity separately from guard behavior and writing quality."""
    model_records = [record for record in records if record['status'] != 'expected_guard_rejection']
    stages = [entry for record in records for entry in record['stage_records']]
    measured = [entry for entry in stages if type(entry.get('completion_tokens')) is int]
    measured_seconds = sum(entry['elapsed_seconds'] for entry in measured)
    return {'cases': len(records), 'model_cases': len(model_records),
            'validated_model_cases': sum(record['status'] == 'validated' for record in model_records),
            'failed_model_cases': sum(record['status'] != 'validated' for record in model_records),
            'expected_guard_rejections': len(records) - len(model_records),
            'median_case_seconds': statistics.median(record['elapsed_seconds'] for record in model_records) if model_records else None,
            'stage_calls': sum(len(record['stage_records']) for record in records),
            'semantic_repair_attempts': sum(len(record.get('semantic_repairs', [])) for record in records),
            'schema_validated_stage_calls': sum(entry['schema_validated'] for record in records for entry in record['stage_records']),
            'compiled_model_cases': sum(record.get('compilation', {}).get('valid') is True for record in model_records),
            'compiler_failed_cases': sum(record.get('compilation', {}).get('valid') is False for record in model_records),
            'validation_scope': VALIDATION_SCOPE,
            'effective_output_tokens_per_second': sum(entry['completion_tokens'] for entry in measured) / measured_seconds if measured_seconds else None,
            'throughput_scope': 'Completed output tokens per measured stage second; includes prompt processing, transport and validation, not pure decode speed.',
            'quality_rating': None, 'quality_rating_reason': 'NPC behavior and scene fidelity require manual review of saved stage outputs.'}


def run_suite(models, cases, settings, output_dir, *, repeats=1, timeout=180, temperature=.55,
              budgets=None, semantic_repair=False, client_factory=None, resource_factory=None, emit=print,
              case_evaluator=None):
    """Sequential models, isolated durable ownership, and no rendering callbacks."""
    client_factory = client_factory or LMStudioClient
    resource_factory = resource_factory or ResourceManager
    case_evaluator = case_evaluator or evaluate_case
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = {**DEFAULT_SETTINGS, **copy.deepcopy(settings), 'ai_memory_mode': 'exclusive'}
    client = client_factory(config['lm_url'], api_key=os.environ.get('LM_STUDIO_API_KEY', ''), timeout=timeout)
    report = {'version': EVALUATION_VERSION, 'validation_scope': VALIDATION_SCOPE,
              'started_at': time.time(), 'settings': {key: config[key] for key in DEFAULT_SETTINGS},
              'models': [], 'temperature': temperature, 'output_budgets': {**DEFAULT_BUDGETS, **(budgets or {})},
              'selected_case_ids': [case['id'] for case in cases],
              'semantic_repair_policy': 'one_completed_stage_only' if semantic_repair else 'disabled',
              'render_jobs': 0, 'complete': False}
    atomic_json(output / 'report.json', report)
    try:
        inventory = client.native_models()
        report['inventory'] = inventory
        installed = {entry['key']: entry for entry in inventory if entry.get('type') == 'llm'}
        if any(model not in installed for model in models):
            raise ValueError('Every --model must match an exact installed LLM key. No models were loaded.')
        if any(entry.get('loaded_instances') for entry in inventory):
            raise ValueError('Another LM Studio instance is loaded. Finish its work and unload it before evaluation; it was left unchanged.')
        manager = resource_factory(lambda: copy.deepcopy(config), lambda: client, state_path=output / 'resource_state.json')
        for model_index, model in enumerate(models, 1):
            config['model'] = model
            model_report = {'model': model, 'records': [], 'status': 'running'}
            report['models'].append(model_report)
            uncertain = False
            prepared = False
            try:
                load_started = time.perf_counter()
                model_report['preparation'] = manager.run_ai(model)
                model_report['prepare_seconds'] = time.perf_counter() - load_started
                prepared = True
                if hasattr(client, 'loaded_instances'):
                    model_report['loaded_instances'] = client.loaded_instances()
                for repeat in range(1, repeats + 1):
                    for case in cases:
                        emit(f'{model_index}/{len(models)} {model} · {case["id"]} · repeat {repeat}')
                        # Keep one lease through dependent actor/coordinator/director calls.
                        record = manager.run_ai(model, lambda exact, c=case: case_evaluator(c, client, exact,
                            repeat=repeat, temperature=temperature, budgets=budgets, semantic_repair=semantic_repair))
                        model_report['records'].append(record)
                        atomic_json(output / f'model-{model_index:02d}-{case["id"]}-{repeat:02d}.json', record)
                        model_report['summary'] = summarize(model_report['records'])
                        atomic_json(output / 'report.json', report)
                        if record.get('error_code') in UNCERTAIN_ERRORS:
                            uncertain = True
                            raise RuntimeError('Prediction or connection state is uncertain. Evaluation stopped; the owned model was kept for inspection.')
                model_report['status'] = 'completed'
            except BaseException as exc:
                uncertain = (uncertain or isinstance(exc, (KeyboardInterrupt, SystemExit))
                             or getattr(exc, 'code', '') in UNCERTAIN_ERRORS or getattr(manager, 'pending_load', None) is not None)
                model_report.update(status='failed', error=str(exc) or 'Evaluation interrupted; server state needs inspection.',
                                    error_code=getattr(exc, 'code', type(exc).__name__))
                raise
            finally:
                model_report['summary'] = summarize(model_report['records'])
                model_report['total_seconds_including_prepare'] = time.perf_counter() - load_started
                if prepared and not uncertain:
                    # This coordinator created the instance from empty inventory.
                    # Its production handoff verifies exact ownership and unload;
                    # operation=None cannot submit an asset or video job.
                    model_report['release'] = manager.prepare_h3()
                elif uncertain:
                    model_report['release'] = {'released': False, 'reason': 'Uncertain server state; inspect the saved ownership marker before retrying.'}
                atomic_json(output / 'report.json', report)
        report['complete'] = True
    except BaseException as exc:
        report.update(error=str(exc) or 'Evaluation interrupted; server state needs inspection.',
                      error_code=getattr(exc, 'code', type(exc).__name__))
    finally:
        report['finished_at'] = time.time()
        atomic_json(output / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list-cases', action='store_true')
    parser.add_argument('--model', action='append', help='Exact installed model key; repeat to compare models sequentially.')
    parser.add_argument('--case', action='append', dest='case_ids', help='Case ID; repeat to select a subset.')
    parser.add_argument('--settings', type=Path, help='Read only the LM URL, Comfy URLs and context length from this JSON.')
    parser.add_argument('--lm-url')
    parser.add_argument('--comfy-url', action='append', dest='comfy_urls')
    parser.add_argument('--context-length', type=int)
    parser.add_argument('--output', type=Path, help='A new directory for isolated reports and ownership state.')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--temperature', type=float, default=.55)
    parser.add_argument('--semantic-repair', action='store_true', help='Allow one correction after a completed, schema-valid stage fails production semantic validation. Keep good prior stages; never retry uncertain transport.')
    args = parser.parse_args(argv)
    cases = fixtures()
    if args.list_cases:
        for case in cases:
            print(case['id'] + ': ' + case['message'])
        return 0
    if not args.model or args.output is None:
        parser.error('--model and --output are required for live evaluation.')
    if not 1 <= args.repeat <= 10 or not 0 <= args.temperature <= 1 or not 1 <= args.timeout <= 600:
        parser.error('Use repeat 1–10, temperature 0–1, and timeout 1–600 seconds.')
    if args.case_ids:
        missing = set(args.case_ids) - {case['id'] for case in cases}
        if missing:
            parser.error('Unknown case: ' + ', '.join(sorted(missing)))
        cases = [case for case in cases if case['id'] in args.case_ids]
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if args.settings:
        loaded = json.loads(args.settings.read_text(encoding='utf-8-sig'))
        settings.update({key: loaded[key] for key in ('lm_url', 'comfy_urls', 'context_length') if key in loaded})
    for arg, key in ((args.lm_url, 'lm_url'), (args.comfy_urls, 'comfy_urls'), (args.context_length, 'context_length')):
        if arg is not None:
            settings[key] = arg
    if type(settings['context_length']) is not int or not 1024 <= settings['context_length'] <= 32768:
        parser.error('Context length must be an integer from 1024 to 32768.')
    if not isinstance(settings['comfy_urls'], list) or not settings['comfy_urls']:
        parser.error('Supply the local Comfy server URLs to check before loading models.')
    try:
        LMStudioClient(settings['lm_url'], timeout=args.timeout)
        settings['comfy_urls'] = [local_url(url) for url in settings['comfy_urls']]
    except ValueError as exc:
        parser.error(str(exc))
    report = run_suite(list(dict.fromkeys(args.model)), cases, settings, args.output,
                       repeats=args.repeat, timeout=args.timeout, temperature=args.temperature, semantic_repair=args.semantic_repair)
    print('Report: ' + str((args.output / 'report.json').resolve()))
    if report.get('error'):
        print(report['error'], file=sys.stderr)
        return 2
    return 1 if any(model['summary']['failed_model_cases'] for model in report['models']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
