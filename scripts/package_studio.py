"""Create a portable release from public source and explicitly included examples.

User data, live logs, models, Python/Node environments, and hidden files are never
included. Run from a reviewed public checkout after building the frontend.
"""
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ('backend', 'dist', 'system-prompts', 'examples', 'demo', 'docs', 'licenses',
           'comfy_extension', 'frontend/src', 'tests', 'scripts')
FILES = ('README.md', 'VERIFICATION.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE',
         'COMFY_BRIDGE_SETUP.md', 'COMFY_FLOW.md', 'CREATIVE_TOOLS.md', 'CONTRACT.md',
         'Launch.ps1', 'Setup.ps1', 'requirements.txt', 'requirements.lock.txt',
         'pytest.ini', 'frontend/package.json', 'frontend/package-lock.json',
         'frontend/index.html', 'frontend/tsconfig.json', 'frontend/vite.config.ts',
         'frontend/vitest.config.ts')
EXCLUDED = {'__pycache__', 'node_modules', '.venv', '.pytest_cache', 'data', 'logs',
            '.git', '.env', 'test-results', 'playwright-report'}


def release_paths():
    paths = {ROOT / name for name in FILES if (ROOT / name).is_file()}
    for folder in FOLDERS:
        paths.update(path for path in (ROOT / folder).rglob('*') if path.is_file())
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED or part.startswith('.') for part in relative.parts):
            continue
        if path.suffix in {'.pyc', '.pyo', '.gguf', '.safetensors', '.mmh3', '.tsbuildinfo'}:
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f'Release input must be an ordinary file in this checkout: {relative}')
        yield path, relative


def main():
    if not (ROOT / 'dist/index.html').is_file():
        raise SystemExit('Build the frontend before packaging: npm run build in frontend.')
    destination = ROOT / 'release/H3-Prompt-Studio-v1.zip'
    destination.parent.mkdir(exist_ok=True)
    count = 0
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, relative in release_paths():
            archive.write(path, 'H3-Prompt-Studio/' + relative.as_posix())
            count += 1
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Archive integrity check failed.')
    print(f'{destination.name}: {count} files, {destination.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
