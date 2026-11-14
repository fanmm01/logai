"""Paired Y17 screening with fixed seeds, sides, settings and existing weights."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, default=ROOT)
p.add_argument('--seeds', type=int, default=8)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--team', action='store_true')
a = p.parse_args()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(a.source.resolve()))
import battle_engine as be
import characters_data as cd
import ai_trainer as tr
import tests.test_summon_groups as fixture
from combat_settings import settings_scope, snapshot
be.__file__ = str(ROOT / 'battle_engine.py')
fixture.ROOT = a.source.resolve()
classes, logs = fixture.battle_classes()
weights = tr.load_q_table(ROOT / 'ai_weights.json')
for mode in ('solo', 'team', 'summon'):
    setattr(classes.AIController, 'Q_' + mode.upper(), weights[mode])
data = {c['serial']: c for c in cd.ALL_CHARACTERS}
rows = []
with settings_scope(snapshot()):
    for serial in sorted(data, key=lambda s: int(s[1:])):
        if serial == 'Y17':
            continue
        count = collections.Counter()
        games = []
        for seed in range(2026111700, 2026111700 + a.seeds):
            for side in (0, 1):
                random.seed(seed)
                en = classes.BattleEngine(delay=0, quiet=True, max_rounds=20)
                roster = [('a', 'Y17'), ('b', serial)]
                if a.team:
                    roster += [('c', 'Y6'), ('d', 'Y7' if serial != 'Y7' else 'Y8')]
                for uid, s in roster:
                    cd.load_character_to_engine(en, data[s], uid)
                    en.process_command(uid, '.hs')
                left = ['a', 'c'] if a.team else ['a']
                right = ['b', 'd'] if a.team else ['b']
                en.setup_battle(right if side else left, left if side else right,
                                '10x10', season_status=50)
                original = en._execute_spell
                def cast(uid, target, spell):
                    if uid == 'a':
                        count['casts'] += 1
                        count['spell_' + str(spell['index'])] += 1
                    result = original(uid, target, spell)
                    if uid == 'a' and not be.action_committed(result):
                        count['rejects'] += 1
                    return result
                en._execute_spell = cast
                result = en.run_battle({uid: classes.AIController(uid, data[s],
                    ('X' if side else 'Y') if uid in left else ('Y' if side else 'X'))
                    for uid, s in roster})
                count['games'] += 1
                count['wins'] += result['winner'] == ('X' if side else 'Y')
                count['draws'] += result['winner'] not in ('X', 'Y')
                count['rounds'] += result.get('rounds', 0)
                games.append([seed, side, result])
                logs.clear()
        rows.append(dict(opponent=serial, stats=dict(count), games=games))
        print(serial, dict(count), flush=True)
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(dict(rows=rows,
            weights_sha256=hashlib.sha256((ROOT / 'ai_weights.json').read_bytes()).hexdigest()),
            ensure_ascii=False, indent=2), encoding='utf-8')
