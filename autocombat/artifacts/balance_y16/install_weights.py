"""Atomically install only the validated Y16 entries, with an external-write guard."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
path = ROOT / 'ai_weights.json'
raw = path.read_bytes()
expected = (OUT / 'latest_weights.json').read_bytes()
if raw != expected:
    raise SystemExit('Weights changed after final validation; preserve them and revalidate before installation.')
current = json.loads(raw)
candidate = json.loads((OUT / 'final_weights.json').read_text(encoding='utf-8'))
original = json.loads(raw)
for table in ('Q_solo', 'Q_team'):
    current[table]['Y16'] = candidate[table]['Y16']
current.setdefault('targeted_training', {})['Y16'] = candidate['targeted_training']['Y16']
for table in ('Q_solo', 'Q_team', 'Q_summon'):
    assert {k: v for k, v in current[table].items() if k != 'Y16'} == {
        k: v for k, v in original[table].items() if k != 'Y16'}
assert current == candidate
pending = path.with_name('ai_weights.y16.pending.json')
pending.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
if path.read_bytes() != raw:
    pending.unlink()
    raise SystemExit('Concurrent weight update detected; installation cancelled.')
os.replace(pending, path)
result = {'installed': True, 'other_character_and_summon_tables_preserved': True,
          'before_sha256': hashlib.sha256(raw).hexdigest(),
          'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
          'Y16_solo_entries': len(current['Q_solo']['Y16']),
          'Y16_team_entries': len(current['Q_team']['Y16'])}
(OUT / 'weight_installation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
