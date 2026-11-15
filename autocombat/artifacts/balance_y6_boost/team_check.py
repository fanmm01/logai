"""Paired complete team prelims using frozen inputs and production scheduling."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def unchanged(data, version):
    """Revalidate a completed round saved before an interrupted multi-run job."""
    source = OUT / (version + '_source')
    return bool(data and data.get('runs') and not data['errors'] and all(
        hashlib.sha256((source / ('ai_weights.json' if name == 'weights_used' else name)).read_bytes()).hexdigest() == digest
        for name, digest in data['fingerprints'].items()))


def run(job):
    version, fmt, seed = job
    path = OUT / f'team_{version}_{fmt}_{seed}.json'
    command = [sys.executable, str(ROOT / 'artifacts/balance_y6/run_acceptance.py'),
               '--source', str(OUT / (version + '_source')),
               '--settings', str(OUT / 'baseline_source/settings.json'),
               '--format', fmt, '--runs', '1', '--seed', str(seed), '--output', str(path)]
    result = subprocess.run(command, capture_output=True, encoding='utf-8',
                            env=dict(os.environ, PYTHONHASHSEED='0', PYTHONIOENCODING='utf-8'))
    if result.returncode:
        raise RuntimeError(result.stderr)
    data = json.loads(path.read_text(encoding='utf-8'))
    return version, fmt, data


def main():
    results = {}
    jobs = []
    # Resume only complete, unchanged tournament records from this fixed snapshot.
    for version in ('baseline', 'final'):
        for fmt in ('2v2', '3v3'):
            path = OUT / f'team_{version}_{fmt}.json'
            cached = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
            if unchanged(cached, version):
                cached['inputs_unchanged'] = True
                results[version, fmt] = cached
            else:
                results[version, fmt] = None
            done = {r['seed'] for r in cached['runs']} if results[version, fmt] else set()
            jobs.extend((version, fmt, seed) for seed in range(2026111500, 2026111503) if seed not in done)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in as_completed([pool.submit(run, job) for job in jobs]):
            version, fmt, data = future.result()
            current = results[version, fmt]
            if current:
                if current['fingerprints'] != data['fingerprints'] or current['settings'] != data['settings']:
                    raise RuntimeError('Cached team inputs differ from new tournament inputs')
                current['runs'].extend(data['runs'])
                current['runs'].sort(key=lambda r: r['seed'])
                current['errors'].extend(data['errors'])
                current['inputs_unchanged'] &= data['inputs_unchanged']
                current['mean_wins'] = sum(r['wins'] for r in current['runs']) / len(current['runs'])
                current['mean_win_rate'] = sum(r['wins'] for r in current['runs']) / sum(r['battles'] for r in current['runs'])
            else:
                current = results[version, fmt] = data
            (OUT / f'team_{version}_{fmt}.json').write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
            print(version, fmt, data['mean_win_rate'], flush=True)
    summary = {}
    for fmt in ('2v2', '3v3'):
        before, after = [results[v, fmt] for v in ('baseline', 'final')]
        delta = after['mean_win_rate'] - before['mean_win_rate']
        summary[fmt] = dict(baseline=before['mean_win_rate'], final=after['mean_win_rate'],
                            delta_percentage_points=100 * delta,
                            guard_passed=delta <= .15 and not before['errors'] and not after['errors']
                            and before['inputs_unchanged'] and after['inputs_unchanged'],
                            snow_team_battles=sum(r['battles'] for r in after['runs']))
    (OUT / 'team_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
