"""Package a reviewed checkout without local state or machine-specific metadata.

Build and validate the app first. This command neither builds nor publishes it.
The same version, input bytes and Python/zlib toolchain produce the same ZIP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAMP = (1980, 1, 1, 0, 0, 0)
ROOT_FILES = (
    'README.md', 'CHANGELOG.md', 'VERIFICATION.md', 'VERIFICATION_V1.1.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE',
    'COMFY_BRIDGE_SETUP.md', 'COMFY_FLOW.md', 'CREATIVE_TOOLS.md', 'CONTRACT.md',
    'Launch.ps1', 'Setup.ps1', 'requirements.txt', 'requirements.lock.txt', 'pytest.ini',
    'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html',
    'frontend/tsconfig.json', 'frontend/vite.config.ts', 'frontend/vitest.config.ts',
    'comfy_extension/__init__.py', 'comfy_extension/README.md',
    'tools/package_release.py', 'scripts/package_studio.py',
)
# A folder is included only for these source or reviewed demo file types.
# Runtime directories and ad hoc workflow/export directories are never roots.
TREES = {
    'backend': {'.py'},
    'frontend/src': {'.ts', '.tsx', '.css', '.svg'},
    'tests': {'.py'},
    'comfy_extension/web': {'.js', '.mjs', '.css'},
    'comfy_extension/tests': {'.mjs'},
    'system-prompts': {'.txt', '.md'},
    'licenses': {'.txt', '.md'},
    'demo': {'.md', '.json', '.txt', '.srt', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4'},
    'dist': {'.html', '.js', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2'},
}
EXCLUDED_PARTS = {
    'data', 'logs', 'models', 'node_modules', 'venv', '.venv', '.git', '__pycache__',
    '.pytest_cache', 'test-results', 'playwright-report', 'coverage', 'release',
    'research', 'attachments', 'private', 'credentials', 'secrets', 'backups',
}
REQUIRED = {
    'README.md', 'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md', 'Setup.ps1', 'Launch.ps1',
    'requirements.txt', 'requirements.lock.txt', 'backend/app.py',
    'frontend/package.json', 'frontend/package-lock.json', 'dist/index.html',
    'licenses/react-LICENSE.txt', 'licenses/react-dom-LICENSE.txt',
    'licenses/scheduler-LICENSE.txt', 'licenses/lucide-react-LICENSE.txt',
}


def version_value(value: str) -> str:
    value = value.removeprefix('v')
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?', value):
        raise argparse.ArgumentTypeError('Use a release version such as 1.1.0 or 1.1.0-rc.1.')
    return value


def allowed(relative: Path) -> bool:
    return not any(part.startswith('.') or part.casefold() in EXCLUDED_PARTS for part in relative.parts)


def checked_file(root: Path, path: Path) -> Path:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Symbolic links are not release inputs: {relative.as_posix()}')
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f'Input must be an ordinary file inside the checkout: {relative.as_posix()}')
    return relative


def release_paths(root: Path = ROOT) -> list[tuple[Path, str]]:
    root = root.resolve()
    selected = {root / name for name in ROOT_FILES if (root / name).is_file()}
    for directory, extensions in TREES.items():
        folder = root / directory
        if folder.is_symlink():
            raise ValueError(f'Symbolic links are not release inputs: {directory}')
        if not folder.is_dir():
            continue
        for path in folder.rglob('*'):
            relative = path.relative_to(root)
            if not allowed(relative):
                continue
            # Reject links even if their suffix would otherwise be excluded.
            if path.is_symlink():
                raise ValueError(f'Symbolic links are not release inputs: {relative.as_posix()}')
            if path.is_file() and path.suffix.lower() in extensions:
                selected.add(path)
    files, names = [], set()
    for path in sorted(selected, key=lambda item: item.relative_to(root).as_posix()):
        relative = checked_file(root, path)
        if not allowed(relative):
            continue
        name = relative.as_posix()
        if name.casefold() in names:
            raise ValueError(f'Case-colliding paths are not portable: {name}')
        names.add(name.casefold())
        files.append((path, name))
    missing = REQUIRED - {name for _, name in files}
    if missing:
        raise ValueError('Required release files are missing: ' + ', '.join(sorted(missing)))
    if not any(name.startswith('dist/') and name.endswith('.js') for _, name in files):
        raise ValueError('The built frontend is missing. Run npm run build in frontend first.')
    # A stale or partial bundle must not be packaged with a broken entry page.
    index = (root / 'dist/index.html').read_text(encoding='utf-8')
    selected_names = {name for _, name in files}
    for asset in re.findall(r'(?:src|href)=["\']/?(assets/[^"\'?#]+)', index):
        if 'dist/' + asset not in selected_names:
            raise ValueError(f'The entry page refers to a missing built asset: {asset}')
    return files


def entry(name: str) -> zipfile.ZipInfo:
    result = zipfile.ZipInfo(name, date_time=STAMP)
    result.create_system = 3
    result.external_attr = (stat.S_IFREG | 0o644) << 16
    result.compress_type = zipfile.ZIP_DEFLATED
    return result


def package(version: str, output_dir: Path, root: Path = ROOT) -> tuple[Path, Path, int]:
    version = version_value(version)
    paths = release_paths(root)
    # Read each reviewed file once; its manifest hash describes the exact archived bytes.
    payloads = [(name, path.read_bytes()) for path, name in paths]
    manifest = {'version': version, 'format': 1, 'files': [
        {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in payloads]}
    payloads.append(('RELEASE-MANIFEST.json', (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')))
    payloads.sort(key=lambda item: item[0])
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f'H3-Prompt-Studio-v{version}.zip'
    checksum = destination.with_suffix('.zip.sha256')
    if destination.exists() or checksum.exists():
        raise FileExistsError('Release output already exists. Choose a new output directory; nothing was overwritten.')
    prefix = f'H3-Prompt-Studio-v{version}/'
    # Exclusive creation prevents replacing an existing release, including concurrent runs.
    with destination.open('xb') as output:
        try:
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, data in payloads:
                    archive.writestr(entry(prefix + name), data, compresslevel=9)
            output.flush()
        except BaseException:
            output.close()
            destination.unlink(missing_ok=True)
            raise
    try:
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None or archive.namelist() != [prefix + name for name, _ in payloads]:
                raise ValueError('Archive verification failed.')
        with destination.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        with checksum.open('x', encoding='ascii', newline='\n') as output:
            output.write(f'{digest}  {destination.name}\n')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination, checksum, len(payloads)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, type=version_value, help='Release version, for example 1.1.0')
    parser.add_argument('--output-dir', required=True, type=Path, help='Directory for the ZIP and SHA-256 sidecar')
    options = parser.parse_args()
    try:
        archive, checksum, count = package(options.version, options.output_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'Packaging stopped: {exc}\n')
    print(f'{archive.name}: {count} files, {archive.stat().st_size:,} bytes')
    print(f'SHA-256: {checksum.read_text(encoding="ascii").split()[0]}')


if __name__ == '__main__':
    main()
