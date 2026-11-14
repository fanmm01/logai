"""Summarize the frozen, paired comparisons and verify the control traces."""
import collections
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def totals(rows):
    result = collections.Counter()
    for row in rows:
        result.update(row['stats'])
    result['win_rate'] = result['wins'] / result['games']
    result['median_matchup_win_rate'] = statistics.median(r['stats']['wins'] / r['stats']['games'] for r in rows)
    return dict(result)


before, after = read('verified_before.json'), read('verified_after.json')
assert len(before) == len(after) == 16
assert all(row['stats']['games'] == 100 for row in before + after)
controls_before, controls_after = read('verified_control_before.json'), read('verified_control_after.json')
assert len(controls_before) == len(controls_after) == 120
assert controls_before == controls_after, 'An unrelated matchup changed'
core_before = [r for r in before if r['pair'][1] != 'Y17']
core_after = [r for r in after if r['pair'][1] != 'Y17']
rejections = collections.Counter()
for row in after:
    rejections.update(row['rejections'])
summary = dict(
    primary_cohort='15 original opponents; Y17, enabled concurrently, is reported separately',
    before=totals(core_before), after=totals(core_after),
    all_before=totals(before), all_after=totals(after),
    rejections_after=dict(rejections), control_games=240, control_traces_identical=True,
    frozen_weight_sha256=hashlib.sha256((ROOT / 'weights/ai_weights.json').read_bytes()).hexdigest(),
    source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'final_source').glob('*.py')},
    inputs_sha256=hashlib.sha256((ROOT / 'comparison_inputs.json').read_bytes()).hexdigest(),
)
(ROOT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
