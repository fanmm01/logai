"""Balanced-roster team validation against the real battle/AI classes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--games', type=int, default=400, help='per format, including swapped sides')
    p.add_argument('--size', type=int, choices=(2, 3))
    p.add_argument('--pair-start', type=int, default=0)
    p.add_argument('--workers', type=int, default=1)
    a = p.parse_args()
    if a.workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        parts = a.output.parent / (a.output.stem + '_parts')
        parts.mkdir(parents=True, exist_ok=True)
        def run_chunk(size, start, count):
            path = parts / f'{size}v{size}_{start}.json'
            cmd = [sys.executable, str(Path(__file__).resolve()), '--source', str(a.source),
                   '--output', str(path), '--games', str(count * 2), '--size', str(size), '--pair-start', str(start)]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if result.returncode:
                raise RuntimeError(result.stderr[-2000:])
            return json.loads(path.read_text(encoding='utf-8'))
        results = []
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            jobs = [pool.submit(run_chunk, size, a.pair_start + start, min(50, a.games // 2 - start))
                    for size in ((a.size,) if a.size else (2, 3)) for start in range(0, a.games // 2, 50)]
            for job in as_completed(jobs):
                results.append(job.result())
                output = dict(results[0], rows=[r for item in results for r in item['rows']], summary={})
                for item in results:
                    if item['fingerprints'] != output['fingerprints']:
                        raise RuntimeError('Team validation inputs changed')
                    for fmt, summary in item['summary'].items():
                        merged = Counter(output['summary'].get(fmt, {}))
                        merged.update(summary)
                        output['summary'][fmt] = dict(merged)
                output['workers'] = a.workers
                a.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
                print('completed chunks', len(results), flush=True)
        return
    source = a.source.resolve()
    sys.path[:0] = [str(source), str(ROOT)]
    import battle_engine as be
    import characters_data as cd
    import ai_trainer as tr
    import tests.test_summon_groups as fixture
    from combat_settings import settings_scope
    fixture.ROOT = source
    classes, logs = fixture.battle_classes()
    weights = tr.load_q_table(source / 'ai_weights.json')
    for mode in ('solo', 'team', 'summon'):
        setattr(classes.AIController, 'Q_' + mode.upper(), weights[mode])
    data = {c['serial']: c for c in cd.ALL_CHARACTERS}
    others = sorted(set(data) - {'Y17'}, key=lambda s: int(s[1:]))
    config = json.loads((source / 'settings.json').read_text(encoding='utf-8'))
    output = dict(source=str(source), weights_sha256=hashlib.sha256((source/'ai_weights.json').read_bytes()).hexdigest(),
                  fingerprints={n: hashlib.sha256((source/n).read_bytes()).hexdigest()
                                for n in ('characters_data.py', 'battle_engine.py', 'ai_trainer.py', 'ai_battle.py', 'settings.json')},
                  settings=config, rows=[], summary={})
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with settings_scope(config):
        for size in ((a.size,) if a.size else (2, 3)):
            summary = Counter()
            for i in range(a.pair_start, a.pair_start + a.games // 2):
                seed = 2026120000 + size * 10000 + i
                allies = [others[(i + k * 5) % len(others)] for k in range(size - 1)]
                opponent_pool = [s for s in others if s not in allies]
                rng = random.Random(seed)
                opponents = rng.sample(opponent_pool, size)
                statuses = {s: rng.randrange(101) for s in ['Y17'] + allies + opponents}
                for side in (0, 1):
                    random.seed(seed * 2 + side)
                    tr._ACTION_CACHE.clear()
                    engine = classes.BattleEngine(delay=0, quiet=True, max_rounds=30 if size == 2 else 45)
                    left, right = ['Y17'] + allies, opponents
                    controllers = {}
                    for serial in left + right:
                        uid = 'char_' + serial
                        cd.load_character_to_engine(engine, data[serial], uid)
                        if not data[serial].get('pre_transformed'):
                            engine.process_command(uid, '.hs')
                        controllers[uid] = classes.AIController(uid, data[serial],
                            ('X' if side else 'Y') if serial in left else ('Y' if side else 'X'))
                    la, ra = ['char_' + s for s in left], ['char_' + s for s in right]
                    engine.setup_battle(ra if side else la, la if side else ra, config['DEFAULT_MAP_SIZE'],
                                        season_status={'char_' + s: v for s, v in statuses.items()})
                    engine._ai_react_dodge_w = {uid: ai.react_dodge_w for uid, ai in controllers.items()}
                    engine._ai_react_counter_w = {uid: ai.react_counter_w for uid, ai in controllers.items()}
                    result = engine.run_battle(controllers)
                    audit = json.loads(engine.get_char('char_Y17').get_str('_blood_audit') or '{}')
                    win = result['winner'] == ('X' if side else 'Y')
                    summary['games'] += 1
                    summary['wins'] += win
                    summary['timeouts'] += bool(result.get('timeout'))
                    for key, value in audit.items():
                        summary[key] += value
                    output['rows'].append(dict(format=f'{size}v{size}', seed=seed, side=side,
                        left=left, right=right, win=win, result=result, audit=audit))
                    logs.clear()
                if (i + 1) % 25 == 0:
                    print(f'{size}v{size} {summary["games"]}/{a.games} wins={summary["wins"]}', flush=True)
                    output['summary'][f'{size}v{size}'] = dict(summary)
                    a.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
            output['summary'][f'{size}v{size}'] = dict(summary)
            a.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
