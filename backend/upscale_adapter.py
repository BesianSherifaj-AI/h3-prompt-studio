"""Open the user's existing UPSCALE desktop GUI without starting processing.

No engine is copied, imported, downloaded or run by Studio. Only trusted paths
resolved by the app's library/video code may be passed as video_path.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


class UpscaleError(ValueError):
    pass


VIDEO_EXTENSIONS = frozenset({'.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.wmv', '.ts', '.mts', '.m2ts', '.mpg', '.mpeg', '.flv', '.mxf'})


def _directory(directory=None):
    configured = directory or os.environ.get('H3_STUDIO_UPSCALE')
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = [Path.home() / 'OneDrive' / 'Desktop' / 'UPSCALE', Path.home() / 'Desktop' / 'UPSCALE']
    return next((path.resolve() for path in candidates if (path / 'main.py').is_file()), candidates[0].resolve())


def capabilities(directory=None):
    folder = _directory(directory)
    python = folder / '.venv' / 'Scripts' / 'pythonw.exe'
    available = all(path.is_file() for path in (python, folder / 'main.py', folder / 'backend.py')) and (folder / 'engine').is_dir()
    return {'available': available, 'name': 'UPSCALE desktop', 'directory': str(folder),
            'supports_video': True, 'supports_images': False, 'accepts_video_paths': True,
            'video_extensions': sorted(VIDEO_EXTENSIONS), 'autostarts_processing': False,
            'uses_existing_settings': True,
            'note': 'Opens the existing UPSCALE window with the video added. Choose its scale, frame rate and other options there, then press Start pending. Original clips stay unchanged.',
            'image_note': 'This UPSCALE GUI accepts videos, not still-image files.',
            'reason': None if available else 'UPSCALE was not found. Set H3_STUDIO_UPSCALE to its existing folder, or install it separately. Studio does not download or install its engine.'}


def open_gui(video_path=None, *, directory=None, launcher=None):
    """Launch pythonw/main.py with a video argument; the GUI handles one instance.

    Launching an existing instance forwards the path and activates its window.
    This does not click Start pending, change preferences or acquire a GPU lease.
    """
    info = capabilities(directory)
    if not info['available']:
        raise UpscaleError(info['reason'])
    source = None
    if video_path is not None:
        try:
            source = Path(video_path).resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise UpscaleError('The selected video is unavailable. Save or download the completed clip first.') from exc
        if not source.is_file() or not source.stat().st_size:
            raise UpscaleError('Choose a completed, nonempty video file.')
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            raise UpscaleError('This UPSCALE GUI accepts videos, not still-image files.')
    folder = Path(info['directory'])
    args = [str(folder / '.venv' / 'Scripts' / 'pythonw.exe'), str(folder / 'main.py')]
    if source:
        args.append(str(source))
    try:
        # The user explicitly opens an interactive desktop GUI. pythonw avoids
        # a terminal window while Qt shows the requested application normally.
        process = (launcher or subprocess.Popen)(args, cwd=str(folder), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                 close_fds=True, shell=False)
    except OSError as exc:
        raise UpscaleError('UPSCALE could not open. Run its existing desktop shortcut and check its logs folder.') from exc
    return {'status': 'launch_requested', 'process_id': process.pid, 'video_path': str(source) if source else None,
            'directory': str(folder), 'processing_started': False,
            'message': 'Opening UPSCALE with this video added. Choose your options there and press Start pending.' if source else
                       'Opening UPSCALE. Add a video and choose your options there.'}
