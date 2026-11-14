"""Frozen-input, swapped-side Y16 evaluation using the actual tournament classes."""
import argparse
import collections
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent / 'baseline_source'


def initialize(source, weights):
    global cd, be, tr, classes, logs, cards, settings
    source = Path(source)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(source))
    import battle_engine as be
    import characters_data as cd
    import ai_trainer as tr
    import tests.test_summon_groups as fixture
    from combat_settings import settings_scope
    settings = settings_scope(json.loads((BASE / 'settings.json').read_text(encoding='utf-8')))
    settings.__enter__()
    fixture.ROOT = source
    classes, logs = fixture.battle_classes()
    be.__file__ = str(ROOT / 'battle_engine.py')
    be._SUMMON_TEMPLATES = cd.SUMMON_TEMPLATES
    tables = tr.load_q_table(weights)
    for name in ('solo', 'team', 'summon'):
        setattr(classes.AIController, 'Q_' + name.upper(), tables[name])
    cards = {c['serial']: c for c in cd.ALL_CHARACTERS}


def run(job):
    import random
    left, right, count, seed_start = job
    stats = collections.Counter()
    casts = collections.Counter()
    trace = hashlib.sha256()
    for seed in range(seed_start, seed_start + count):
        for side in (False, True):
            random.seed(seed)
            tr._ACTION_CACHE.clear()
            en = classes.BattleEngine(delay=0, quiet=True, max_rounds={1: 20, 2: 30, 3: 45}[len(left)])
            roster = [(f'l{i}', serial, 'X' if side else 'Y') for i, serial in enumerate(left)]
            roster += [(f'r{i}', serial, 'Y' if side else 'X') for i, serial in enumerate(right)]
            for uid, serial, team in roster:
                cd.load_character_to_engine(en, cards[serial], uid)
                en.process_command(uid, '.hs')
            y = [u for u, _, t in roster if t == 'Y']
            x = [u for u, _, t in roster if t == 'X']
            en.setup_battle(y, x, '10x10', season_status=50)
            original = en._execute_spell
            def cast(uid, target, spell):
                mine = uid == 'l0' and left[0] == 'Y16'
                if mine:
                    casts[spell['name']] += 1
                result = original(uid, target, spell)
                if mine and not be.action_committed(result):
                    stats['rejections'] += 1
                return result
            en._execute_spell = cast
            result = en.run_battle({u: classes.AIController(u, cards[s], t) for u, s, t in roster})
            trace.update(json.dumps(result, sort_keys=True).encode())
            stats['games'] += 1
            stats['wins'] += result['winner'] == ('X' if side else 'Y')
            stats['rounds'] += result.get('rounds', 0)
            stats['timeouts'] += bool(result.get('timeout'))
            logs.clear()
    return {'left': left, 'right': right, 'stats': dict(stats), 'casts': dict(casts), 'result_hash': trace.hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--weights', type=Path, default=BASE / 'ai_weights.json')
    parser.add_argument('--seeds', type=int, default=10)
    parser.add_argument('--seed', type=int, default=2026111600)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--format', choices=('solo', 'team', 'control'), default='solo')
    parser.add_argument('--roles', nargs='+', default=['Y16', 'Y1', 'Y4', 'Y11'])
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    manifest = {f: hashlib.sha256((a.source / f).read_bytes()).hexdigest()
                for f in ('battle_engine.py', 'characters_data.py', 'ai_trainer.py', 'ai_battle.py')}
    serials = [f'Y{i}' for i in range(1, 18)]
    if a.format == 'solo':
        pairs = [(['Y16'], [s]) for s in serials if s != 'Y16']
    elif a.format == 'control':
        pairs = [(['Y2'], ['Y3']), (['Y1'], ['Y11']), (['Y4'], ['Y12'])]
    else:
        configs = [(['Y2'], ['Y3', 'Y4']), (['Y7'], ['Y1', 'Y10']),
                   (['Y10'], ['Y2', 'Y11']), (['Y9'], ['Y3', 'Y12']),
                   (['Y2', 'Y7'], ['Y1', 'Y3', 'Y10']),
                   (['Y3', 'Y8'], ['Y2', 'Y11', 'Y7']),
                   (['Y9', 'Y6'], ['Y1', 'Y12', 'Y10']),
                   (['Y1', 'Y10'], ['Y2', 'Y4', 'Y7'])]
        pairs = [([role] + allies, enemies) for role in a.roles
                 for allies, enemies in configs]
    jobs = [(left, right, min(10, a.seeds - start), a.seed + start)
            for left, right in pairs for start in range(0, a.seeds, 10)]
    rows = []
    with ProcessPoolExecutor(max_workers=a.workers, initializer=initialize,
            initargs=(str(a.source.resolve()), str(a.weights.resolve()))) as pool:
        for row in pool.map(run, jobs):
            rows.append(row)
            print('+'.join(row['left']), 'vs', '+'.join(row['right']), row['stats'], flush=True)
    totals = collections.Counter()
    nonsupport = collections.Counter()
    for row in rows:
        if row['left'][0] != 'Y16':
            continue
        totals.update(row['stats'])
        if row['right'][0] not in ('Y6', 'Y7', 'Y8'):
            nonsupport.update(row['stats'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'source': manifest,
        'weights': hashlib.sha256(a.weights.read_bytes()).hexdigest(),
        'seed': a.seed, 'seeds': a.seeds, 'format': a.format,
        'total': dict(totals), 'nonsupport': dict(nonsupport), 'rows': rows},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('TOTAL', dict(totals), 'NONSUPPORT', dict(nonsupport), flush=True)


if __name__ == '__main__':
    main()
