import hashlib
from backend.runtime_identity import runtime_fingerprint


def test_fingerprint_has_stable_order_and_changes_when_backend_changes(tmp_path):
    folder = tmp_path / 'backend'
    folder.mkdir()
    (folder / 'z.py').write_bytes(b'last\r\n')
    (folder / 'a.py').write_bytes(b'first\n')
    manifest = '\n'.join(f'backend/{name}={hashlib.sha256((folder / name).read_bytes()).hexdigest()}'
                         for name in ('a.py', 'z.py'))
    original = runtime_fingerprint(tmp_path)
    assert original == hashlib.sha256(manifest.encode()).hexdigest()
    (folder / 'a.py').write_bytes(b'changed\n')
    assert runtime_fingerprint(tmp_path) != original


def test_frontend_and_private_data_changes_do_not_affect_python_identity(tmp_path):
    (tmp_path / 'backend').mkdir()
    (tmp_path / 'backend' / 'app.py').write_bytes(b'app')
    original = runtime_fingerprint(tmp_path)
    (tmp_path / 'dist').mkdir()
    (tmp_path / 'dist' / 'index.html').write_text('updated frontend')
    (tmp_path / 'backend' / 'note.txt').write_text('unrelated')
    assert runtime_fingerprint(tmp_path) == original
