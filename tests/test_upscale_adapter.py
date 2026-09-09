from types import SimpleNamespace

import pytest

from backend.upscale_adapter import UpscaleError, capabilities, open_gui


@pytest.fixture
def installed(tmp_path):
    folder = tmp_path / 'UPSCALE with spaces'
    (folder / '.venv' / 'Scripts').mkdir(parents=True)
    (folder / 'engine').mkdir()
    for name in ('.venv/Scripts/pythonw.exe', 'main.py', 'backend.py'):
        (folder / name).write_bytes(b'installed')
    return folder


def test_optional_capability_does_not_claim_still_image_or_automatic_processing(installed):
    info = capabilities(installed)
    assert info['available'] and info['supports_video'] and not info['supports_images']
    assert not info['autostarts_processing'] and info['uses_existing_settings']
    assert capabilities(installed / 'missing')['available'] is False


def test_open_hands_off_exact_video_as_argument_to_existing_gui_without_shell_or_processing(installed, tmp_path):
    source = tmp_path / 'scene & $(keep-original).mp4'
    source.write_bytes(b'original video')
    calls = []
    def launch(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=123)
    result = open_gui(source, directory=installed, launcher=launch)
    assert calls[0][0] == [str(installed / '.venv' / 'Scripts' / 'pythonw.exe'), str(installed / 'main.py'), str(source)]
    assert calls[0][1]['shell'] is False and calls[0][1]['cwd'] == str(installed)
    assert result['processing_started'] is False and result['status'] == 'launch_requested'
    assert source.read_bytes() == b'original video'


def test_still_images_missing_install_and_missing_video_do_not_launch(installed, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported handoff must not launch a process')
    image = tmp_path / 'reference.png'
    image.write_bytes(b'image')
    with pytest.raises(UpscaleError, match='not still-image'):
        open_gui(image, directory=installed, launcher=forbidden)
    with pytest.raises(UpscaleError, match='unavailable'):
        open_gui(tmp_path / 'missing.mp4', directory=installed, launcher=forbidden)
    with pytest.raises(UpscaleError, match='not found'):
        open_gui(directory=installed / 'missing', launcher=forbidden)


def test_open_options_without_video_is_an_explicit_gui_only_handoff(installed):
    calls = []
    def launch(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(pid=124)
    result = open_gui(directory=installed, launcher=launch)
    assert len(calls[0]) == 2 and result['video_path'] is None
    assert result['processing_started'] is False
