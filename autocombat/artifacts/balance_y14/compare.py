"""Reproducible in-process battle probes, without importing server/logging setup."""
import argparse
import collections
import hashlib
import itertools
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, default=ROOT)
p.add_argument('--mode', choices=['y14', 'control'], default='y14')
p.add_argument('--seeds', type=int, default=10)
p.add_argument('--seed', type=int, default=2026111400)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--mp', type=int)
p.add_argument('--cheap-attacks', action='store_true')
p.add_argument('--ablation', choices=['ai', 'cooldown'])
p.add_argument('--pair', nargs=2)
p.add_argument('--trace', action='store_true')
p.add_argument('--disable-hooks', action='store_true')
p.add_argument('--rng-log', action='store_true')
a = p.parse_args()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(a.source.resolve()))
import battle_engine as be
import characters_data as cd
import ai_trainer as tr
import tests.test_summon_groups as fixture
# Engine target-strategy loading is relative to __file__; both arms must read
# the same existing weights even when the code comes from the saved snapshot.
WEIGHTS = ROOT / 'artifacts/balance_y14/weights/ai_weights.json'
be.__file__ = str(WEIGHTS.parent / 'battle_engine.py')
inputs = json.loads((ROOT / 'artifacts/balance_y14/comparison_inputs.json').read_text(encoding='utf-8'))
cd.ALL_CHARACTERS = inputs['characters']
cd.SUMMON_TEMPLATES = inputs['summons']
be._SUMMON_TEMPLATES = cd.SUMMON_TEMPLATES
tr.SUMMON_TEMPLATES = cd.SUMMON_TEMPLATES
from combat_settings import settings_scope
scope = settings_scope(inputs['settings'])
scope.__enter__()
fixture.ROOT = a.source.resolve()
classes, logs = fixture.battle_classes()
if a.disable_hooks:
    be.CombatEngine._is_y14 = lambda self, uid: False

weights = tr.load_q_table(WEIGHTS)
for name in ['solo', 'team', 'summon']:
    setattr(classes.AIController, 'Q_' + name.upper(), weights[name])
data = {c['serial']: c for c in cd.ALL_CHARACTERS}
if a.mp:
    data['Y14']['attrs']['魔力'] = data['Y14']['attrs']['魔力上限'] = a.mp
if a.cheap_attacks:
    for s in data['Y14']['spells']:
        if s['name'] in ['S弹幕·散射', 'S弹幕·集中', 'L型贯穿激光', '三连斩·散', '三连斩·聚', '极限技·凶斩']:
            s['消耗mp'] -= 1
if a.ablation:
    # Policy-only view: execution keeps the original Y14 behavior, while the
    # policy can apply the corrected action space. Other character work stays
    # present identically in all arms of the comparison.
    identify = be.CombatEngine._is_y14
    be.CombatEngine._is_y14 = lambda self, uid: False
    class PolicyView:
        def __init__(self, engine):
            self.engine = engine
        def __getattr__(self, name):
            return getattr(self.engine, name)
        def _is_y14(self, uid):
            return identify(self.engine, uid)
        def _y14_preflight(self, uid, spell):
            return be.CombatEngine._y14_preflight(self, uid, spell)
        def _y14_attack_profile(self, uid):
            return be.CombatEngine._y14_attack_profile(self, uid)
    original_decide = classes.AIController.decide_action
    classes.AIController.decide_action = lambda self, engine, uid: original_decide(self, PolicyView(engine), uid)
    if a.ablation == 'cooldown':
        execute = be.CombatEngine._execute_spell
        def cooldown_cast(self, uid, target, spell):
            reason = PolicyView(self)._y14_preflight(uid, spell)
            if reason:
                return be.ActionExecutionResult(reason, False, 'spell_rejected')
            result = execute(self, uid, target, spell)
            if be.action_committed(result) and identify(self, uid) and spell['index'] in (1, 2, 3):
                self._y14_start_cooldown(uid)
            return result
        be.CombatEngine._execute_spell = cooldown_cast

serials = sorted(data, key=lambda s: int(s[1:]))
pairs = [('Y14', s) for s in serials if s != 'Y14'] if a.mode == 'y14' else list(itertools.combinations([s for s in serials if s != 'Y14'], 2))
if a.pair:
    pairs = [tuple(a.pair)]
rows = []
rng_rows = []
if a.rng_log:
    for name in ['randint', 'random', 'choice', 'choices']:
        original = getattr(random, name)
        def traced(*args, _name=name, _original=original, **kwargs):
            result = _original(*args, **kwargs)
            rng_rows.append([_name, sys._getframe(1).f_code.co_name, repr(args), repr(kwargs), repr(result)])
            return result
        setattr(random, name, traced)
for left, right in pairs:
    total = collections.Counter()
    rejections = collections.Counter()
    fingerprints = []
    for seed in range(a.seed, a.seed + a.seeds):
        for side in (0, 1):
            rng_rows.clear()
            random.seed(seed)
            en = classes.BattleEngine(delay=0, quiet=True, max_rounds=20)
            for uid, serial in [('a', left), ('b', right)]:
                cd.load_character_to_engine(en, data[serial], uid)
                en.process_command(uid, '.hs')
            en.setup_battle(['b'] if side else ['a'], ['a'] if side else ['b'], '10x10', season_status=50)
            if a.ablation == 'ai':
                # The AI-only arm suppresses invalid choices without activating cooldown.
                en._get_cooldown = lambda uid, idx: 0
            trace = hashlib.sha256()
            trace_rows = []
            original_command = en._execute_ai_command
            original_cast = en._execute_spell
            original_hp = en._set_combat_hp
            def hp_change(uid, hp, source_dmg=0, **kwargs):
                old_hp = en._get_combat_hp(uid) or 0
                result = original_hp(uid, hp, source_dmg=source_dmg, **kwargs)
                if left == 'Y14' and source_dmg > 0:
                    amount = max(0, old_hp - (en._get_combat_hp(uid) or 0))
                    if en._resolve_uid(uid) == 'a':
                        total['damage_taken'] += amount
                    else:
                        total['damage_dealt'] += amount
                return result
            def command(uid, cmd):
                row = json.dumps([uid, cmd, en._get_state(), en._get_actions(),
                    en._get_initiative(), en._get_effects(),
                    [(u, en._get_combat_hp(u), en.get_char(u).get_attr('魔力')) for u in ('a', 'b')]],
                    ensure_ascii=False, sort_keys=True, default=str)
                trace.update(row.encode())
                if a.trace:
                    trace_rows.append(json.loads(row))
                return original_command(uid, cmd)
            def cast(uid, target, spell):
                mine = left == 'Y14' and en._resolve_uid(uid) == 'a'
                old_mp = en.get_char(uid).get_attr('魔力') if mine else 0
                if mine:
                    total['casts'] += 1
                    if spell['index'] in (1, 2, 3):
                        total['switches'] += 1
                        if spell['index'] == en.get_char(uid).phase:
                            total['empty_switches'] += 1
                result = original_cast(uid, target, spell)
                if mine:
                    total['cast_mp_spent'] += max(0, old_mp - en.get_char(uid).get_attr('魔力'))
                if mine and not be.action_committed(result):
                    total['rejects'] += 1
                    reason = next((r for r in ['没有可攻击的目标', '无法接近目标', '不可选中',
                        '资源不足', '魔力不足', '冷却', '已经处于', '另一重剑模式'] if r in str(result)), str(result))
                    rejections[reason] += 1
                return result
            en._execute_ai_command = command
            en._execute_spell = cast
            en._set_combat_hp = hp_change
            result = en.run_battle({u: classes.AIController(u, data[s], team) for u, s, team in
                [('a', left, 'X' if side else 'Y'), ('b', right, 'Y' if side else 'X')]})
            total['games'] += 1
            total['wins'] += result['winner'] == ('X' if side else 'Y')
            total['rounds'] += result.get('rounds', 0)
            fingerprints.append([seed, side, result, trace.hexdigest()])
            if a.trace:
                a.output.with_suffix(f'.{seed}.{side}.trace.json').write_text(json.dumps(trace_rows, ensure_ascii=False, indent=2), encoding='utf-8')
            if a.rng_log:
                a.output.with_suffix(f'.{seed}.{side}.rng.json').write_text(json.dumps(rng_rows, ensure_ascii=False, indent=2), encoding='utf-8')
            logs.clear()
    row = dict(pair=[left, right], stats=dict(total), rejections=dict(rejections), fingerprints=fingerprints)
    rows.append(row)
    print(left, right, dict(total), flush=True)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
