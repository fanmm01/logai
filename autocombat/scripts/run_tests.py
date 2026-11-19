"""Run the archived development suite with an isolated runtime directory."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='autocombat-tests-') as runtime:
        env = dict(os.environ, AUTOCOMBAT_RUNTIME_DIR=runtime,
                   AUTOCOMBAT_NO_FILE_LOG='1', PYTHONIOENCODING='utf-8')
        paths = [ROOT / 'backend', ROOT / 'dev', ROOT / 'dev/tests', ROOT / 'scripts']
        env['PYTHONPATH'] = os.pathsep.join(map(str, paths)) + (
            os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        args = sys.argv[1:]
        if not args or all(arg in ('-v', '--verbose', '-q', '--quiet', '-f', '--failfast', '-b', '--buffer') for arg in args):
            args = ['discover', '-s', str(ROOT / 'dev/tests'), '-t', str(ROOT / 'dev'), *args]
        return subprocess.call([sys.executable, '-m', 'unittest', *args], cwd=ROOT, env=env)


if __name__ == '__main__':
    raise SystemExit(main())
