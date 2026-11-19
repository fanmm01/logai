"""Build a portable release ZIP from an explicit runtime file allowlist."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-frontend-build', action='store_true', help='Package an already verified frontend/dist')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}(?:-[A-Za-z0-9.-]+)?', version):
        parser.error('VERSION must contain a three- or four-part numeric version')
    if not args.skip_frontend_build:
        npm = shutil.which('npm.cmd' if sys.platform == 'win32' else 'npm')
        if not npm:
            parser.error('Node.js and npm are required to build the frontend')
        if not (ROOT / 'frontend/node_modules/vite').is_dir():
            subprocess.run([npm, 'ci', '--no-audit', '--no-fund'], cwd=ROOT / 'frontend', check=True)
        subprocess.run([npm, 'run', 'build'], cwd=ROOT / 'frontend', check=True)
    if not (ROOT / 'frontend/dist/index.html').is_file():
        parser.error('frontend/dist/index.html is missing')
    if not (ROOT / 'frontend/dist/story-engine/index.html').is_file():
        parser.error('frontend/dist/story-engine/index.html is missing')
    # Generate into the bundle; packaging never relies on a stale deployment file.
    spec = importlib.util.spec_from_file_location('release_merge', ROOT / 'scripts/merge_backend.py')
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    generated = merge.render_backend()
    compile(generated, 'autocombat_backend.py', 'exec')
    name = f'autocombat-{version}'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='autocombat-release-') as folder:
        bundle = Path(folder) / name
        bundle.mkdir()
        files = [ROOT / p for p in ('start_ui.py', 'requirements.txt', 'VERSION',
                 'plugins/autocombat.js', 'docs/游戏总机制.md', 'docs/MOV规则参考.md',
                 'docs/REORGANIZATION_VALIDATION.md')]
        files += sorted((ROOT / 'backend').glob('*.py'))
        files += [ROOT / 'backend/ai_weights.json', ROOT / 'backend/ai_weights_pvp.json']
        files += sorted(p for base in ('backend/fonts', 'frontend/dist') for p in (ROOT / base).rglob('*') if p.is_file())
        files += sorted(p for p in (ROOT / 'story-projects/demo').rglob('*') if p.is_file() and not any(x.startswith('.') for x in p.relative_to(ROOT / 'story-projects/demo').parts))
        files += sorted((ROOT / 'docs').glob('STORY_*.md'))
        files += [ROOT / 'docs/THIRD_PARTY_NOTICES.md']
        for source in files:
            if source.name == 'autocombat_backend.py':
                continue
            target = bundle / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (bundle / 'backend/autocombat_backend.py').write_text(generated, encoding='utf-8')
        # Include the exact modified MPL source and build metadata, not dependency caches.
        source_zip = bundle / 'webgal-adv/engine-source.zip'
        source_zip.parent.mkdir(parents=True, exist_ok=True)
        engine_root = ROOT / 'webgal-adv/engine'
        with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in sorted(engine_root.rglob('*')):
                if p.is_file() and not p.relative_to(engine_root).as_posix().startswith('packages/webgal/public/game/') and not any(x in ('node_modules', 'dist', 'build', '.cache') for x in p.relative_to(engine_root).parts):
                    z.write(p, p.relative_to(engine_root).as_posix())
        shutil.copy2(ROOT / 'docs/RELEASE_README.md', bundle / 'README.md')
        manifest = {p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(bundle.rglob('*')) if p.is_file()}
        (bundle / 'MANIFEST.sha256.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Validate the exact staged files before creating the archive.
        env = os.environ.copy()
        env.update(AUTOCOMBAT_RUNTIME_DIR=str(Path(folder) / 'runtime'), AUTOCOMBAT_NO_FILE_LOG='1', PYTHONIOENCODING='utf-8')
        smoke = """import autocombat_backend as b
c = b.app.test_client()
assert c.get('/api/pvp/ping').status_code == 200
assert c.get('/api/ui/catalog').status_code == 200
r = c.get('/ui/')
assert r.status_code == 200 and b'<!doctype html>' in r.data
r.close()
assert b._PIL_AVAILABLE and b._FONT_PATH
c.post('/api/ui/session', json={'name':'release-smoke'})
p = c.get('/api/ui/story/projects').get_json()
assert len(p['projects'][0]['chapters']) == 3
r = c.post('/api/ui/story/runs', json={'project':'astral-demo','flow':'XU'})
assert r.status_code == 200 and r.get_json()['status'] == 'text'
r = c.get('/ui/story-engine/')
assert r.status_code == 200
r.close()
from story_state_codec import encode_value, decode_value
from battle_engine import Character
assert isinstance(decode_value(encode_value(Character('smoke','smoke'))), Character)
"""
        subprocess.run([sys.executable, '-c', smoke], cwd=bundle / 'backend', env=env, check=True)
        archive = output / (name + '.zip')
        # Temporary runtime/log files created by the smoke check stay out of the ZIP.
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for relative in sorted([*manifest, 'MANIFEST.sha256.json']):
                z.write(bundle / relative, name + '/' + relative)
        with zipfile.ZipFile(archive) as z:
            if z.testzip():
                raise RuntimeError('Release ZIP validation failed')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'Release: {archive}\nSHA256: {digest}\nFiles: {len(manifest) + 1}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
