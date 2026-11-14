"""Train only Y16's character policy; preserve all other character/summon tables."""
import argparse
import collections
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import ai_trainer as tr
import characters_data as cd


def battle(job):
    seed, left, right = job
    random.seed(seed)
    tr._ACTION_CACHE.clear()
    roster = set(left + right)
    data = [c for c in cd.ALL_CHARACTERS if c['serial'] in roster]
    mapping = {s: 'train_' + s for s in roster}
    return [u for u in tr._mp_run_battle((left, right, '10x10', data, mapping,
                                       cd.SUMMON_TEMPLATES)) if u[1] == 'Y16']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--battles', type=int, default=1200)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--seed', type=int, default=2026111400)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    base = ROOT / 'artifacts/balance_y16/baseline_source/ai_weights.json'
    previous = json.loads(base.read_text(encoding='utf-8'))
    rng = random.Random(a.seed)
    others = [c['serial'] for c in cd.ALL_CHARACTERS if c['serial'] != 'Y16']
    jobs = []
    for i in range(a.battles):
        if i % 10 < 7:
            left, right = ['Y16'], [others[i % len(others)]]
        else:
            chosen = rng.sample(others, 5)
            left, right = ['Y16'] + chosen[:2], chosen[2:]
        if rng.randrange(2):
            left, right = right, left
        jobs.append((a.seed + i, left, right))
    tables = {'solo': {}, 'team': {}}
    by_state = {'solo': {}, 'team': {}}
    counts = collections.Counter()
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for n, updates in enumerate(pool.map(battle, jobs), 1):
            for kind, _, state, action, reward, next_state in updates:
                table = tables[kind]
                old = table.get((state, action), 0.0)
                states = by_state[kind]
                future = max(states.get(next_state, {}).values(), default=0.0) if next_state else 0
                table[state, action] = old + tr.ALPHA * (reward + tr.GAMMA * future - old)
                states.setdefault(state, {})[action] = table[state, action]
                counts[kind] += 1
            if n % 100 == 0:
                print(n, dict(counts), flush=True)
    for kind, table in tables.items():
        previous.setdefault('Q_' + kind, {})['Y16'] = {
            '|'.join(map(str, state)) + '__' + action: value
            for (state, action), value in table.items()}
    previous.setdefault('targeted_training', {})['Y16'] = {
        'battles': a.battles, 'seed': a.seed, 'state_dimensions': 25,
        'updates': dict(counts), 'baseline_weights': hashlib.sha256(base.read_bytes()).hexdigest()}
    a.output.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding='utf-8')
    print('saved', a.output, {k: len(t) for k, t in tables.items()}, flush=True)


if __name__ == '__main__':
    main()
