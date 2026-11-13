"""Build and start the local browser UI: python start_ui.py [--lan] [--port 8889]."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description='星序浏览器战术室')
    parser.add_argument('--lan', action='store_true', help='允许局域网设备访问')
    parser.add_argument('--port', type=int, default=8889)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise SystemExit('需要 Node.js 20.19+ 或 22.12+（含 npm）来构建界面。')
    frontend = root / 'frontend'
    if not (frontend / 'node_modules' / 'vite').is_dir():
        subprocess.run([npm, 'ci', '--no-audit', '--no-fund'], cwd=frontend, check=True)
    subprocess.run([npm, 'run', 'build'], cwd=frontend, check=True)
    subprocess.run([sys.executable, str(root / 'merge_backend.py')], cwd=root, check=True)
    print(f'\n星序战术室：http://127.0.0.1:{args.port}/ui/', flush=True)
    if args.lan:
        print('好友可使用 http://本机局域网IP:' + str(args.port) + '/ui/ 加入房间。', flush=True)
    subprocess.run([sys.executable, str(root / 'autocombat_backend.py'), '--host',
                    '0.0.0.0' if args.lan else '127.0.0.1', '--port', str(args.port)], cwd=root, check=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
