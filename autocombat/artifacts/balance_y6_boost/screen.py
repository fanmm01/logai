"""Run isolated candidate screens against a fixed source/settings snapshot."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--stage', choices=('cost', 'hp', 'regen', 'mp'), required=True)
    parser.add_argument('--source', type=Path, default=OUT / 'baseline_source')
    parser.add_argument('--cost', action='append', default=[])
    parser.add_argument('--base-rule', action='append', default=[])
    parser.add_argument('--hp', type=int)
    parser.add_argument('--prefix', default='')
    args = parser.parse_args()
    candidates = {
        'cost': [('zone', ['--spell-cost', '制造雪带=3']),
                 ('summon', ['--spell-cost', '堆雪人=4']),
                 ('both', ['--spell-cost', '制造雪带=3', '--spell-cost', '堆雪人=4'])],
        'hp': [('hp10', ['--hp', '10'])],
        'regen': [('regen_hp9', ['--hp', '9', '--rule', '冰冻即时回复层数=3']),
                  ('regen_hp10', ['--hp', '10', '--rule', '冰冻即时回复层数=3'])],
        'mp': [('mp15', ['--rule', '魔力=15', '--rule', '魔力上限=15']),
               ('mp17', ['--rule', '魔力=17', '--rule', '魔力上限=17'])],
    }[args.stage]

    def run(item):
        name, options = item
        name = args.prefix + args.stage + '_' + name
        command = [sys.executable, str(ROOT / 'artifacts/balance_y6/run_acceptance.py'),
                   '--source', str(args.source), '--settings', str(OUT / 'baseline_source/settings.json'),
                   '--rule', '冰冻命中层数=2', '--screen', '--runs', '3', '--seed', '2026111500',
                   '--output', str(OUT / (name + '.json'))] + options
        for cost in args.cost:
            command.extend(['--spell-cost', cost])
        for rule in args.base_rule:
            command.extend(['--rule', rule])
        if args.hp is not None:
            command.extend(['--hp', str(args.hp)])
        result = subprocess.run(command, capture_output=True, encoding='utf-8',
                                env=dict(os.environ, PYTHONHASHSEED='0', PYTHONIOENCODING='utf-8'))
        if result.returncode:
            raise RuntimeError(result.stderr)
        data = json.loads((OUT / (name + '.json')).read_text(encoding='utf-8'))
        return name, data['mean_wins']

    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(run, item) for item in candidates]):
            print(future.result(), flush=True)


if __name__ == '__main__':
    main()
