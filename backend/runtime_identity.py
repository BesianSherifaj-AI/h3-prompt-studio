"""Identify the Python source a server loaded, independently of its version label."""
from __future__ import annotations

import hashlib
from pathlib import Path


def runtime_fingerprint(root):
    files = sorted((Path(root) / 'backend').glob('*.py'), key=lambda path: path.name)
    if not files:
        raise ValueError('The local Studio backend source folder is unavailable.')
    manifest = '\n'.join(f'backend/{path.name}={hashlib.sha256(path.read_bytes()).hexdigest()}' for path in files)
    return hashlib.sha256(manifest.encode('utf-8')).hexdigest()
