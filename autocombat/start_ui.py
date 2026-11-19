"""Build and start the browser UI: python start_ui.py [--port 8889] [--tcp-port 8892]."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description='星序浏览器战术室')
    parser.add_argument('--lan', action='store_true', help='Deprecated; server now listens on all interfaces')
    parser.add_argument('--port', type=int, default=8889)
    parser.add_argument('--skip-build', action='store_true', help='Use the existing frontend and generated backend')
    parser.add_argument('--tcp-port', type=int, default=8892,
                        help='TCP tunnel forwarding port (default: 8892)')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.tcp_port <= 65535:
        parser.error('端口必须在 1–65535 之间')
    if args.port == args.tcp_port:
        parser.error('--port 和 --tcp-port 必须不同')
    root = Path(__file__).resolve().parent
    frontend = root / 'frontend'
    # Release bundles already contain the built UI and generated backend.
    prebuilt = args.skip_build or not (frontend / 'package.json').exists()
    if not prebuilt:
        npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
        if not npm:
            raise SystemExit('需要 Node.js 20.19+ 或 22.12+（含 npm）来构建界面。')
        if not (frontend / 'node_modules' / 'vite').is_dir():
            subprocess.run([npm, 'ci', '--no-audit', '--no-fund'], cwd=frontend, check=True)
        subprocess.run([npm, 'run', 'build'], cwd=frontend, check=True)
        subprocess.run([sys.executable, str(root / 'scripts' / 'merge_backend.py')], cwd=root, check=True)
    if not (frontend / 'dist' / 'index.html').is_file() or not (frontend / 'dist' / 'story-engine' / 'index.html').is_file() or not (root / 'backend' / 'autocombat_backend.py').is_file():
        raise SystemExit('缺少构建产物，请在源码目录运行 python start_ui.py，或重新下载完整发布包。')
    print(f'本机访问：http://127.0.0.1:{args.port}/ui/', flush=True)
    print(f'服务监听：0.0.0.0:{args.port}（局域网或隧道访问时使用本机 IP／映射地址）', flush=True)
    print(f'TCP 隧道入口：0.0.0.0:{args.tcp_port} -> 127.0.0.1:{args.port}', flush=True)
    subprocess.run([sys.executable, str(root / 'backend' / 'autocombat_backend.py'), '--host',
                    '0.0.0.0', '--port', str(args.port), '--tcp-port', str(args.tcp_port)],
                   cwd=root, check=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
