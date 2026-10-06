"""Explicit live local AI smoke; does not submit ComfyUI rendering jobs."""
import json
import time
import sys
from pathlib import Path
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.projects import new_project

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'test-results' / 'local-review-live.json'
REPORT.parent.mkdir(exist_ok=True)
evidence = {'started': time.time(), 'render_jobs_submitted': 0}
with httpx.Client(base_url='http://127.0.0.1:8766', timeout=300) as connection:
    boot = connection.get('/api/bootstrap').raise_for_status().json()
    connection.headers['X-H3-Token'] = boot['token']
    settings = boot['settings']
    assert settings['assistant_model_policy'] == 'qwen3.8-27b'
    assert all(profile['model'] == 'qwen3.8-27b@q4_k_s' and profile['ai_memory_mode'] == 'exclusive'
               for profile in settings['assistant_profiles'].values())
    denied = connection.post('/api/settings', json={'assistant_profiles': {'game': {'model': 'qwen3.5-4b-uncensored-hauhaucs-aggressive'}}})
    assert denied.status_code == 400, denied.text
    evidence['smaller_model_rejected'] = True
    project = new_project()
    project.update(mode='t2va', title='Local prompt smoke')
    project['story']['text'] = 'A paper lantern glows in a quiet garden. A breeze moves it gently. End with the lantern hanging still.'
    project['shots'][0].update(action=project['story']['text'], final_state='The lantern hangs still.')
    started = time.monotonic()
    result = connection.post('/api/ai/plan', json={'project': project, 'vision': False,
        'instructions': 'Keep one coherent gentle action, a static camera, no people and no dialogue.'}).raise_for_status().json()
    assert result['compiled']['valid'] and result['compiled']['prompt'].strip()
    evidence['prompt'] = {'valid': True, 'seconds': round(time.monotonic()-started, 2),
                          'characters': len(result['compiled']['prompt'])}
    print('Live Qwen prompt writing passed', flush=True)
    video = ROOT / 'examples' / 'web-gui-five-seconds' / '01-first-5s.mp4'
    if not video.exists():
        video = ROOT / 'demo' / 'scene-continuity' / 'continuity-30s.mp4'
    previous = json.loads(REPORT.read_text('utf-8')) if REPORT.exists() else {}
    saved_id = previous.get('video_id')
    library = connection.get('/api/reviews/library').raise_for_status().json()['videos']
    imported = next((item for item in library if item['id'] == saved_id), None)
    if imported is None:
        with video.open('rb') as file:
            imported = connection.post('/api/reviews/import', files={'file': ('Local review demo.mp4', file, 'video/mp4')}).raise_for_status().json()
    ident = imported['id']
    evidence['video_id'] = ident
    endpoint = '/api/reviews/asset/' + ident
    connection.put(endpoint, json={'verdict': 'unreviewed', 'notes': 'Workflow demonstration. Watch the complete clip before deciding.', 'checklist': {}}).raise_for_status()
    started = time.monotonic()
    reviewed = connection.post(endpoint + '/analyze', json={'sample_count': 4,
        'intent': 'Check only visible identity, anatomy, objects and framing. Do not infer audio or continuous movement.'}).raise_for_status().json()
    ai = reviewed['ai']
    assert reviewed['verdict'] == 'unreviewed'
    assert ai['improved_prompt'].strip() and len(ai['samples']) == 4 and ai['limitations']
    assert all(0 <= issue['timestamp'] <= imported['duration'] for issue in ai['issues'])
    for sample in ai['samples']:
        response = connection.get(sample['url']).raise_for_status()
        assert response.headers['content-type'].startswith('image/')
    assert connection.get(endpoint + '/prompt').raise_for_status().content
    evidence['review'] = {'ai_verdict': ai['verdict'], 'issues': len(ai['issues']), 'samples': len(ai['samples']),
        'human_verdict_preserved': True, 'model_key': ai.get('model_key'), 'seconds': round(time.monotonic()-started, 2)}
    connection.get('/api/reviews/library').raise_for_status()
    evidence['connections'] = connection.get('/api/connections').raise_for_status().json().get('active_profile')
evidence['completed'] = time.time()
REPORT.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
print(json.dumps(evidence, indent=2), flush=True)
