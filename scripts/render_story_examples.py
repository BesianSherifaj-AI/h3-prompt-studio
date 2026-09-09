"""Prepare or render authored Studio examples against the isolated local QA app.

Preparation performs no network or GPU work:
  .venv/Scripts/python scripts/render_story_examples.py --example both --name qa-demo --output staging/qa-demo

Render only when the local GPU is available and the QA app is running on 8768:
  .venv/Scripts/python scripts/render_story_examples.py --example both --name qa-demo --output staging/qa-demo --resume --execute

This measures authored scene rendering and real saved-motion continuation, not
autonomous LLM writing. No existing story, project, or global settings are edited.
Every POST gets a durable request receipt first. An uncertain POST is never
repeated: resume searches the app's matching saved receipt using GET only.
Review/failure states stop the run. The runner never approves or retries them.
Original generated videos and overlap-trimmed source scenes are retained.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.game_director import direct_plan, validate_narrative
from backend.projects import atomic_json, new_project, shot
from backend.stories import validate_plan
from backend.world import world_from_project


LABEL = 'Authored Studio render/continuation test; not autonomous LLM narrative success.'
PIXEL_STYLE = '2D pixel art, flat clean pixel sprites, limited bright palette, crisp silhouettes, playful retro game animation; no voxel or 3D look.'
ACTIVE = {'planning', 'assets', 'rendering', 'observing'}


class NeedsAttention(RuntimeError):
    """The saved operation needs observation or user review, never resubmission."""


def endpoint_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.port != 8768 or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Use the isolated loopback QA app on port 8768, for example http://127.0.0.1:8768.')
    return value.rstrip('/')


def _uuid(key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _scene(action, ending, camera, performance, sound):
    return {'action': action, 'ending': ending, 'camera': camera, 'performance': performance, 'sound': sound}


def example_specs():
    """Original, silent physical comedy with two continuous visual identities."""
    return {
        'chase': {
            'title': 'A Very Polite Pursuit', 'steps': 4, 'seconds': 30,
            'premise': 'In a small pixel art courtyard, a friendly courier and a round delivery robot play a harmless chase over a blue rubber ball.',
            'characters': [('Pip', 'An adult pixel courier with a red scarf, blue jacket, dark trousers and white shoes.'),
                           ('Bolt', 'A small friendly round yellow delivery robot on two wheels, with a single blue eye and two short grippers.')],
            'setting': 'The same sunny pixel art courtyard: pale cobblestones, teal wall, one orange cone and a low bench.',
            'scenes': [
                _scene('Pip holds a blue rubber ball. Bolt rolls toward Pip and reaches a gripper toward the ball. Pip grins and jogs right; Bolt follows at a playful pace.',
                       'Pip jogs right with the ball; Bolt follows two paces behind.', 'Smooth lateral tracking right.',
                       'Keep Pip and Bolt fully visible. Pip has a light skipping stride; Bolt bobs cheerfully, never threatening.',
                       'Light footsteps, soft wheel whirr and one cheerful robot beep. No dialogue or music.'),
                _scene('Continuing their rightward movement, Pip sidesteps the orange cone while holding the ball. Bolt makes an exaggerated wide circle around the cone and catches up.',
                       'Both are past the cone, moving toward the low bench; Pip still holds the ball.', 'Continue the lateral tracking, easing wider at the cone.',
                       'Pip makes one readable sidestep. Bolt tilts into a smooth circular turn; no collision or impact.',
                       'Quick footsteps, a little wheel squeak and playful electronic chirps. No dialogue or music.'),
                _scene('Pip slows beside the bench, turns and offers the ball. Bolt stops and receives it gently in both grippers. Pip gives a friendly bow; Bolt answers with a tiny bow.',
                       'Pip and Bolt stand calmly beside the bench; Bolt holds the blue ball.', 'Ease to a static medium-wide two-shot.',
                       'Make the ball handoff clear and gentle, followed by two distinct small bows. End with a brief still pose.',
                       'Footsteps settle, wheels stop and a soft happy beep ends the exchange. No dialogue or music.'),
            ],
        },
        'parody': {
            'title': 'The Extremely Important Parcel', 'steps': 8, 'seconds': 60,
            'premise': 'A courier and a pompous little robot treat delivery of one tiny cardboard parcel like an impossibly serious secret mission. The parcel contains a small rubber duck.',
            'characters': [('Pip', 'An adult pixel courier in a yellow cap, navy coat, tan trousers and white shoes, carrying one small plain cardboard parcel.'),
                           ('Bolt', 'A short round silver service robot on two wheels, one blue eye, two small grippers and a red bow tie.')],
            'setting': 'The same pastel pixel art delivery courtyard: cobbled path, teal doorway, a shallow puddle, a low rail and a small bench. No readable signs.',
            'scenes': [
                _scene('Pip stands holding the tiny parcel with both hands. Bolt rolls beside Pip and gives an absurdly formal salute. Pip solemnly returns the salute while carefully keeping the parcel supported.',
                       'Pip holds the parcel at chest height; Bolt waits beside Pip facing right.', 'Slow gentle push toward a medium-wide two-shot.',
                       'One oversized salute each, serious expressions and an awkward pause. The parcel remains small and plain.',
                       'Soft courtyard ambience, paper rustle, a wheel whirr and one officious beep. No dialogue or music.'),
                _scene('Pip tiptoes right with the parcel, approaching the tiny puddle as if it were a dangerous river. Bolt rolls slowly beside Pip, matching the exaggerated caution.',
                       'Pip and Bolt pause just before the shallow puddle; Pip still holds the parcel.', 'Gentle lateral track right, keeping the puddle visible.',
                       'Pip raises each knee comically high. Bolt leans forward to inspect the harmless puddle.',
                       'Tiny careful footsteps, quiet motor whirr and a faint water drip. No dialogue or music.'),
                _scene('Pip takes one needlessly grand step over the tiny puddle while holding the parcel steady. Bolt simply rolls around the puddle and stops next to Pip.',
                       'Both are safely beyond the puddle and face the low rail; the parcel stays with Pip.', 'Continue the same lateral view with a small widening movement.',
                       'The heroic step is clear, slow enough to read and followed by an embarrassed sideways glance at the robot.',
                       'One heavy comic footstep, a brief coat rustle and ordinary wheel sounds. No dialogue or music.'),
                _scene('At the knee-high rail, Pip crouches and shuffles underneath while carefully keeping the parcel level. Bolt waits, then casually rolls around the open end of the rail.',
                       'Pip and Bolt stand on the far side of the rail, near the bench; Pip holds the parcel.', 'Low medium-wide lateral view, gently tracking the crouch.',
                       'One continuous awkward duck-under by Pip; one simple detour by Bolt. Keep both routes visible.',
                       'Coat rustle, two squeaky steps and an effortless motor whirr. No dialogue or music.'),
                _scene('Beside the bench, Bolt extends a gripper ceremonially to inspect the parcel. Pip holds it still. Bolt circles the little parcel with its eye, then issues a grand approving salute.',
                       'Pip holds the unopened parcel above the bench while Bolt maintains an officious salute.', 'Slow settle into an eye-level medium two-shot.',
                       'The robot inspection is visibly disproportionate to the tiny box. Pip waits with patient seriousness.',
                       'Two short scanning chirps, paper rustle and one triumphant little beep. No dialogue or music.'),
                _scene('Pip sets the parcel on the bench and opens its lid, revealing a tiny yellow rubber duck. Pip and Bolt stare at the duck, then turn their heads toward each other in a long embarrassed pause.',
                       'The open parcel and yellow rubber duck rest on the bench; Pip and Bolt share a sheepish look.', 'A small push toward the open parcel while both faces remain visible.',
                       'Reveal exactly one tiny duck, then hold the awkward shared look. Keep the ending readable and unhurried.',
                       'Cardboard rustle, a single soft rubber-duck squeak and quiet courtyard ambience. No dialogue or music.'),
            ],
        },
    }


def authored_example(kind, name, run_key):
    spec = copy.deepcopy(example_specs()[kind])
    project = new_project()
    project.update(id=_uuid(run_key + ':' + kind), title=name + ' · ' + spec['title'], mode='t2va', duration=10,
                   authoring_mode='full', profile='custom', assets=[], music='', soundscape='Quiet playful courtyard ambience.')
    project['style'] = {'genre': 'silent physical comedy', 'vibe': 'playful', 'lighting': 'soft daylight',
                        'color': 'limited pastel pixel palette', 'notes': PIXEL_STYLE}
    project['story'] = {'text': spec['premise'], 'locked': True}
    project['custom_instructions'] = 'Only the two established identities appear. Original silent physical comedy. No subtitles, text overlays or spoken dialogue. ' + PIXEL_STYLE
    project['subjects'] = [{'id': _uuid(run_key + ':' + kind + ':' + person), 'name': person, 'description': description, 'asset_ids': []}
                           for person, description in spec['characters']]
    project['shots'] = [shot(10)]
    project['shots'][0]['id'] = _uuid(run_key + ':' + kind + ':base-shot')
    world = world_from_project(project)
    plans = []
    for index, scene in enumerate(spec['scenes']):
        beat_id = f'beat-{index + 1}'
        plan = {'action': scene['action'], 'setting': spec['setting'], 'final_state': scene['ending'],
                'transition': 'cut' if index == 0 else 'continue', 'dialogue': [],
                'characters': [{'id': subject['id'], 'name': subject['name'], 'description': subject['description'], 'voice': 'silent'}
                               for subject in project['subjects']], 'asset_requests': [], 'effects': [],
                'choices': [{'title': 'Continue', 'message': 'Continue the authored scene.'},
                            {'title': 'Hold', 'message': 'Hold the final pose.'}, {'title': 'Finish', 'message': 'End the authored sequence.'}],
                'beats': [{'id': beat_id, 'action': scene['action'], 'setting': spec['setting'], 'final_state': scene['ending']}],
                'direction': {'shots': [{'beat_id': beat_id, 'duration': 10,
                    'camera': {'framing': 'medium-wide two-shot', 'movement': scene['camera'], 'height': 'eye level', 'speed': 'gentle', 'focus': 'Both established characters and the key prop stay clear.'},
                    'performance': scene['performance'], 'sound': scene['sound'],
                    'visible_subject_ids': [subject['id'] for subject in project['subjects']], 'offscreen_subject_ids': [],
                    'dialogue_indices': [], 'transition': 'continuous'}]}}
        validate_plan(plan, mode='studio', duration=10)
        validate_narrative(plan, world=world, player_character_id=None, message=scene['action'], duration=10, mode='studio')
        direct_plan(plan, project, duration=10)
        plans.append(plan)
    return {'kind': kind, 'title': spec['title'], 'classification': LABEL, 'target_seconds': spec['seconds'], 'plans': plans,
            'create_body': {'request_id': _uuid(run_key + ':' + kind + ':create'), 'mode': 'studio', 'title': project['title'],
                'premise': spec['premise'], 'project': project, 'world': world,
                'settings': {'duration': 10, 'steps': spec['steps'], 'resolution': '0.2', 'experimental_preview': True,
                    'aspect_ratio': '16:9', 'style': PIXEL_STYLE, 'review_before_render': False,
                    'transition': 'auto', 'seed': int(uuid.UUID(run_key)) % (2**32)}}}


class LocalAPI:
    def __init__(self, endpoint):
        self.endpoint = endpoint_url(endpoint)
        self.client = httpx.Client(base_url=self.endpoint, timeout=30, trust_env=False, follow_redirects=False)
        bootstrap = self.get('/api/bootstrap')
        self.settings_hash = _digest(bootstrap.get('settings', {}))
        self.client.headers['X-H3-Token'] = bootstrap['token']

    def get(self, path):
        response = self.client.get(path)
        response.raise_for_status()
        return response.json()

    def post(self, path, body):
        response = self.client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    def download(self, path, destination):
        destination = Path(destination)
        temporary = destination.with_suffix('.partial')
        with self.client.stream('GET', path, timeout=120) as response:
            response.raise_for_status()
            with temporary.open('wb') as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        if not temporary.stat().st_size:
            raise NeedsAttention('The generated clip download was empty.')
        temporary.replace(destination)

    def close(self):
        self.client.close()


class Runner:
    def __init__(self, directory, state, api, *, poll_seconds=2, timeout_seconds=1800, clock=time.monotonic, sleep=time.sleep):
        self.directory, self.state, self.api = Path(directory), state, api
        self.poll_seconds, self.timeout_seconds, self.clock, self.sleep = poll_seconds, timeout_seconds, clock, sleep

    def save(self):
        atomic_json(self.directory / 'run.json', self.state)

    def post_once(self, key, path, body, recover):
        operations = self.state.setdefault('operations', {})
        previous = operations.get(key)
        if previous:
            if previous['path'] != path or previous['body_digest'] != _digest(body):
                raise NeedsAttention('The saved request differs from this invocation; keep its original authored plan.')
            if previous['status'] == 'completed':
                return copy.deepcopy(previous['response'])
            recovered = recover(body['request_id'])
            if recovered is None:
                raise NeedsAttention('The earlier POST has no confirmed receipt yet. No request was repeated: ' + key)
            previous.update(status='completed', response=recovered, recovered_by_get=True)
            self.save()
            return recovered
        record = {'path': path, 'body': copy.deepcopy(body), 'request_id': body['request_id'],
                  'body_digest': _digest(body), 'status': 'prepared', 'created_at': time.time()}
        operations[key] = record
        self.save()  # Durable request ID and exact authored body before POST.
        record['status'] = 'dispatched'
        self.save()
        try:
            response = self.api.post(path, body)
        except Exception as exc:
            record.update(status='uncertain', error=str(exc)[:1200])
            self.save()
            raise NeedsAttention('Submission needs read-only reconciliation; no automatic resubmit: ' + key) from exc
        record.update(status='completed', response=response)
        self.save()
        return response

    def recover_story(self, request_id):
        match = next((story for story in self.api.get('/api/stories')['stories'] if story.get('create_request_id') == request_id), None)
        return self.api.get('/api/stories/' + match['id']) if match else None

    def recover_turn(self, story_id, request_id):
        return next((turn for turn in self.api.get('/api/stories/' + story_id)['turns'] if turn.get('request_id') == request_id), None)

    def wait_turn(self, story_id, turn_id):
        deadline = self.clock() + self.timeout_seconds
        reported = None
        while True:
            story = self.api.get('/api/stories/' + story_id)
            turn = next((turn for turn in story['turns'] if turn['id'] == turn_id), None)
            if turn is None:
                raise NeedsAttention('The saved turn is missing; it was not submitted again.')
            if turn['status'] == 'succeeded':
                return story, turn
            progress = (turn['status'], turn.get('stage', ''))
            if progress != reported:
                print(json.dumps({'turn_id': turn_id, 'status': progress[0], 'stage': progress[1]}), flush=True)
                reported = progress
            if turn['status'] not in ACTIVE:
                raise NeedsAttention(f"Turn {turn_id} needs attention: {turn['status']} · {turn.get('error') or turn.get('stage', '')}")
            if self.clock() >= deadline:
                raise NeedsAttention('Polling timed out; the saved turn may still be running. Resume to inspect the same turn.')
            self.sleep(min(self.poll_seconds, max(0, deadline - self.clock())))

    def render(self, example):
        kind = example['kind']
        saved = self.state.setdefault('results', {}).setdefault(kind, {'classification': LABEL, 'clips': []})
        story = self.post_once(kind + ':create', '/api/stories', example['create_body'], self.recover_story)
        saved['story_id'] = story['id']
        self.save()
        parent_run = None
        for index, plan in enumerate(example['plans']):
            key = f'{kind}:turn:{index + 1}'
            request_id = _uuid(self.state['run_key'] + ':' + key)
            body = {'request_id': request_id, 'duration': 10, 'message': plan['action'], 'planned': plan}
            if key not in self.state.get('operations', {}):
                current = self.api.get('/api/stories/' + story['id'])
                if current.get('active_run_id') != parent_run:
                    raise NeedsAttention('The QA story ending changed before submission; no new work was queued.')
                settings = current.get('settings', {})
                if settings.get('resolution') != '0.2' or settings.get('steps') != example['create_body']['settings']['steps']:
                    raise NeedsAttention('The QA story render settings changed before submission; no new work was queued.')
            turn = self.post_once(key, '/api/stories/' + story['id'] + '/turns', body,
                                  lambda rid: self.recover_turn(story['id'], rid))
            if turn.get('parent_run_id') != parent_run:
                raise NeedsAttention('The authored sequence no longer follows its expected saved ending. No replacement was queued.')
            story, turn = self.wait_turn(story['id'], turn['id'])
            run_id = turn.get('run_id') or (turn.get('video') or {}).get('id')
            run = self.api.get('/api/video/runs/' + run_id)
            if run.get('status') != 'succeeded' or not run.get('continuation_source'):
                raise NeedsAttention('The completed scene has no verified saved motion state for continuation.')
            if index and run.get('parent_run_id') != parent_run:
                raise NeedsAttention('This clip did not continue the preceding actual generated scene.')
            if run.get('width', 0) <= 0 or run.get('height', 0) <= 0 or run['width'] * run['height'] > 210000:
                raise NeedsAttention('The generated scene does not match the required 0.2 MP pixel preview budget.')
            clip = {'index': index + 1, 'turn_id': turn['id'], 'run_id': run_id, 'parent_run_id': parent_run,
                    'run': run, 'authored_plan': plan, 'classification': LABEL}
            previous = next((item for item in saved['clips'] if item['index'] == index + 1), None)
            if previous and previous['run_id'] != run_id:
                raise NeedsAttention('A saved scene now points to a different run; original sources are preserved.')
            if not previous:
                saved['clips'].append(clip)
            self.save()
            parent_run = run_id
            print(json.dumps({'example': kind, 'scene': index + 1, 'status': 'rendered', 'run_id': run_id}), flush=True)
        saved['status'] = 'rendered'
        self.save()
        return saved


def _command(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise NeedsAttention('Media verification failed: ' + result.stderr[-1800:])
    return result.stdout


def probe(path):
    data = json.loads(_command(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    video = next((stream for stream in data['streams'] if stream['codec_type'] == 'video'), None)
    if not video:
        raise NeedsAttention('A downloaded scene contains no video stream.')
    duration = float(video.get('duration') or data['format']['duration'])
    if not math.isfinite(duration) or video['width'] * video['height'] > 210000:
        raise NeedsAttention('The clip duration or 0.2 MP dimensions are invalid.')
    return {'duration': duration, 'width': video['width'], 'height': video['height'],
            'frames': int(video['nb_frames']) if str(video.get('nb_frames', '')).isdigit() else None,
            'audio': any(stream['codec_type'] == 'audio' for stream in data['streams'])}


def decode(path):
    _command(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'])


def verify_and_assemble(runner, example, result):
    folder = runner.directory / example['kind']
    folder.mkdir(exist_ok=True)
    segments, geometry, audio = [], None, None
    for clip in result['clips']:
        stem = f"scene-{clip['index']:02d}"
        for label, route in (('generated', 'video'), ('new-footage', 'scene')):
            path = folder / f'{stem}-{label}.mp4'
            if not path.exists():
                runner.api.download(f"/api/video/runs/{clip['run_id']}/{route}", path)
            info = probe(path)
            decode(path)
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if clip.get(label, {}).get('sha256', checksum) != checksum:
                raise NeedsAttention('A saved source clip changed since its earlier receipt; no source was overwritten.')
            clip[label] = {'path': str(path.resolve()), 'sha256': checksum, 'probe': info, 'decoded': True}
        source = folder / f'{stem}-new-footage.mp4'
        info = clip['new-footage']['probe']
        if info['duration'] < 10 - 1 / 24:
            raise NeedsAttention('The new-footage segment is shorter than ten seconds; no frames were fabricated.')
        this_geometry = (info['width'], info['height'])
        if geometry is not None and (geometry != this_geometry or audio != info['audio']):
            raise NeedsAttention('Segment dimensions or audio streams differ; original clips are preserved for review.')
        geometry, audio = this_geometry, info['audio']
        segment = folder / f'{stem}-exact-10s.mp4'
        if not segment.exists():
            temporary = segment.with_suffix('.partial.mp4')
            _command(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-t', '10', '-map', '0:v:0', '-map', '0:a?',
                      '-vf', 'fps=24', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
                      '-c:a', 'aac', '-ar', '48000', '-movflags', '+faststart', str(temporary)])
            temporary.replace(segment)
        exact = probe(segment)
        if abs(exact['duration'] - 10) > .001 or exact['frames'] != 240:
            raise NeedsAttention('A trimmed scene does not contain exactly 240 frames at 24 fps.')
        segments.append(segment)
        runner.save()
    listing = folder / 'concat.txt'
    # Basenames are generated internally and contain no user-supplied quoting.
    listing.write_text(''.join("file '" + path.name + "'\n" for path in segments), encoding='utf-8')
    final = folder / (example['kind'] + '-' + str(example['target_seconds']) + 's.mp4')
    if not final.exists():
        temporary = final.with_suffix('.partial.mp4')
        _command(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(listing),
                  '-t', str(example['target_seconds']), '-c', 'copy', '-movflags', '+faststart', str(temporary)])
        temporary.replace(final)
    final_info = probe(final)
    decode(final)
    if abs(final_info['duration'] - example['target_seconds']) > .05 or final_info['frames'] != example['target_seconds'] * 24:
        raise NeedsAttention('The assembled film does not have the exact requested number of video frames.')
    result.update(status='media_verified', output=str(final.resolve()), probe=final_info,
                  visual_quality='Manual viewing required; technical decoding does not prove visual or narrative quality.',
                  sha256=hashlib.sha256(final.read_bytes()).hexdigest())
    runner.save()


def prepare(directory, *, example, name, endpoint, resume=False):
    endpoint = endpoint_url(endpoint)
    directory = Path(directory).resolve()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}', name):
        raise ValueError('Use a short QA run name containing letters, numbers, spaces, dots or hyphens.')
    if resume:
        state = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
        if any(state[key] != value for key, value in (('name', name), ('example', example), ('endpoint', endpoint))):
            raise ValueError('Resume with the exact original endpoint, name and example selection.')
        return directory, state
    directory.mkdir(parents=True, exist_ok=False)
    run_key = str(uuid.uuid4())
    selected = ('chase', 'parody') if example == 'both' else (example,)
    state = {'schema_version': 1, 'run_key': run_key, 'name': name, 'example': example, 'endpoint': endpoint,
             'classification': LABEL, 'status': 'prepared', 'operations': {}, 'results': {},
             'examples': [authored_example(kind, name, run_key) for kind in selected]}
    atomic_json(directory / 'run.json', state)
    return directory, state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example', choices=('chase', 'parody', 'both'), default='both')
    parser.add_argument('--name', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8768')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--execute', action='store_true', help='Submit the prepared authored examples to the isolated QA app.')
    parser.add_argument('--poll-seconds', type=float, default=2)
    parser.add_argument('--timeout-seconds', type=float, default=1800, help='Bounded polling time for each saved turn.')
    args = parser.parse_args(argv)
    if not 0.1 <= args.poll_seconds <= 30 or not 1 <= args.timeout_seconds <= 7200:
        parser.error('Use a poll interval of 0.1–30 seconds and a per-turn timeout of 1–7200 seconds.')
    directory, state = prepare(args.output, example=args.example, name=args.name, endpoint=args.endpoint, resume=args.resume)
    if not args.execute:
        print(json.dumps({'status': 'prepared_no_network_or_gpu_work', 'receipt': str(directory / 'run.json'), 'classification': LABEL}))
        return 0
    api = LocalAPI(args.endpoint)
    runner = Runner(directory, state, api, poll_seconds=args.poll_seconds, timeout_seconds=args.timeout_seconds)
    state.setdefault('initial_settings_hash', api.settings_hash)
    if state.get('error'):
        state.setdefault('recovery_history', []).append({'previous_status': state.get('status'),
                                                        'error': state['error'], 'resumed_at': time.time()})
    state.update(status='running')
    state.pop('error', None)
    runner.save()
    try:
        for example in state['examples']:
            result = runner.render(example)
            verify_and_assemble(runner, example, result)
        state.update(status='completed', final_settings_hash=_digest(api.get('/api/bootstrap').get('settings', {})))
        state['global_settings_unchanged'] = state['initial_settings_hash'] == state['final_settings_hash']
        runner.save()
        print(json.dumps({'status': state['status'], 'outputs': [r['output'] for r in state['results'].values()], 'classification': LABEL}))
        return 0
    except (NeedsAttention, httpx.HTTPError, OSError, subprocess.SubprocessError) as exc:
        state.update(status='needs_attention', error=str(exc)[:2000])
        runner.save()
        print(json.dumps({'status': 'needs_attention', 'error': str(exc), 'receipt': str(directory / 'run.json')}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        state.update(status='interrupted', error='The saved turn may still be running. Resume reads its existing receipt; no replacement is submitted.')
        runner.save()
        return 130
    finally:
        api.close()


if __name__ == '__main__':
    raise SystemExit(main())
