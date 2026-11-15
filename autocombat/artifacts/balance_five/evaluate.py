"""Five production 1v1 tournaments, fixed seeds and unchanged user settings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[2]
TARGETS = {'Y11': (49, 52), 'Y10': (44, 46), 'Y9': (52, 55),
           'Y6': (28, 30), 'Y7': (13, 17)}


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=2026118500)
    p.add_argument('--runs', type=int, default=5)
    p.add_argument('--child', action='store_true')
    p.add_argument('--source', type=Path, default=ROOT)
    args = p.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.child:
        parts = args.output.parent / (args.output.stem + '_parts')
        parts.mkdir(exist_ok=True)
        def one(seed):
            path = parts / f'{seed}.json'
            env = dict(os.environ, PYTHONHASHSEED='0', PYTHONUTF8='1')
            r = subprocess.run([sys.executable, '-B', __file__, '--child', '--source',
                str(args.source.resolve()), '--seed', str(seed), '--output', str(path.resolve())],
                capture_output=True, text=True, encoding='utf-8', env=env)
            if r.returncode:
                raise RuntimeError(r.stderr[-4000:])
            result = json.loads(path.read_text(encoding='utf-8'))
            print(seed, {s: result['records'][s]['wins'] for s in TARGETS}, flush=True)
            return result
        with ThreadPoolExecutor(max_workers=5) as pool:
            runs = list(pool.map(one, range(args.seed, args.seed + args.runs)))
        assert all(r['fingerprints'] == runs[0]['fingerprints'] and r['settings'] == runs[0]['settings']
                   and r['inputs_unchanged'] for r in runs), 'Tournament inputs changed'
        means = {s: sum(r['records'][s]['wins'] for r in runs)/len(runs) for s in TARGETS}
        result = dict(runs=runs, mean_wins=means, targets=TARGETS,
            target_met=all(lo <= means[s] <= hi for s, (lo, hi) in TARGETS.items()))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(mean_wins=means, target_met=result['target_met'])), flush=True)
        return
    sys.path.insert(0, str(args.source.resolve()))
    import ai_battle as ab
    from combat_settings import settings_scope, snapshot
    config_path = ROOT / '.ui-runtime/settings.json'
    config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else snapshot()
    config['PRELIM_WORKERS'] = 1
    for k in config:
        if k.startswith('DELAY_') or k == 'TOURNAMENT_EVENT_DELAY':
            config[k] = 0
    ab.logger.handlers.clear()
    ab.logger.addHandler(__import__('logging').NullHandler())
    ab.logger.setLevel(50)
    random.seed(args.seed)
    files = ['characters_data.py', 'battle_engine.py', 'ai_battle.py', 'ai_trainer.py', 'ai_weights.json']
    hashes = {f: hashlib.sha256((args.source/f).read_bytes()).hexdigest() for f in files}
    started = time.monotonic()
    with settings_scope(config):
        t = ab.Tournament(tournament_type=0, run_times=1, enabled_modes=['1v1'], settings=config)
        t.init_characters()
        t.run_phases(write_results_flag=False)
    records = {r[1]: dict(rank=r[0], points=r[2], wins=r[3], battles=r[4]) for r in t._rankings_info['1v1']}
    assert all(records[s]['battles'] == 82 for s in TARGETS), records
    unchanged = all(hashlib.sha256((args.source/f).read_bytes()).hexdigest() == hashes[f] for f in files)
    result = dict(seed=args.seed, records=records, games=t.results, settings=config, fingerprints=hashes,
                  inputs_unchanged=unchanged, seconds=round(time.monotonic()-started, 2))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
