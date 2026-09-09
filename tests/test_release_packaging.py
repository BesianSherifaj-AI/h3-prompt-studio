"""A packaged checkout must retain its tools and licensing without local state."""
import shutil

import pytest

from tools.package_release import ROOT, REQUIRED, release_paths


def release_fixture(tmp_path):
    for name in REQUIRED:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    (tmp_path / 'dist/index.html').write_text('<script src="/assets/app.js"></script>', encoding='utf-8')
    (tmp_path / 'dist/assets').mkdir()
    (tmp_path / 'dist/assets/app.js').write_text('export {};', encoding='utf-8')
    return tmp_path


def test_current_release_includes_tools_required_by_its_tests_and_optional_feature_docs(tmp_path):
    root = release_fixture(tmp_path)
    expected = {
        'LICENSE', 'UPSCALE_SETUP.md', 'AUDIO_SETUP.md',
        'scripts/evaluate_game_assistant.py', 'scripts/render_story_examples.py',
        'scripts/render_scene_control_demo.py', 'tools/browser_game_smoke.mjs',
        'tools/browser_scene_continuity_smoke.mjs',
    }
    for name in expected:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    assert expected <= {name for _, name in release_paths(root)}


def test_release_refuses_an_unlicensed_snapshot(tmp_path):
    root = release_fixture(tmp_path)
    (root / 'LICENSE').unlink()
    with pytest.raises(ValueError, match='LICENSE'):
        release_paths(root)


def test_new_scene_demo_scripts_are_included_but_runtime_data_and_secrets_are_excluded(tmp_path):
    root = release_fixture(tmp_path)
    for name in ('scripts/scene_control_demo.py', 'scripts/nested/evaluate.py',
                 'scripts/private/token.py', 'scripts/data/saved_project.py',
                 'scripts/secrets/key.py', 'scripts/__pycache__/old.py',
                 'scripts/.env', 'scripts/render.log', 'scripts/model.gguf'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    selected = {name for _, name in release_paths(root)}
    assert {name for name in selected if name.startswith('scripts/')} == {
        'scripts/scene_control_demo.py', 'scripts/nested/evaluate.py',
    }


def test_demo_images_and_videos_are_selected_without_runtime_metadata(tmp_path):
    root = release_fixture(tmp_path)
    for name in ('demo/v1.2/poster.jpg', 'demo/v1.2/scene.mp4', 'demo/v1.2/manifest.json',
                 'demo/v1.2/private/record.json', 'demo/v1.2/logs/run.json'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    selected = {name for _, name in release_paths(root)}
    assert {name for name in selected if name.startswith('demo/')} == {
        'demo/v1.2/poster.jpg', 'demo/v1.2/scene.mp4', 'demo/v1.2/manifest.json',
    }
