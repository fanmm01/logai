"""Exercise generated engine classes without starting/importing the HTTP service."""
import ast
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import battle_engine as be
import characters_data as cd
from tests.test_y16_balance import setup, spell, moon

tree = ast.parse((ROOT / 'autocombat_backend.py').read_text(encoding='utf-8'))
names = {'Character', 'ActionExecutionResult', 'CombatEngine', 'FullBattleEngine', 'FastBattleEngine'}
classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names]
assert {n.name for n in classes} == names
namespace = dict(vars(be), _SUMMON_TEMPLATES=cd.SUMMON_TEMPLATES)
exec(compile(ast.Module(body=classes, type_ignores=[]), 'generated_engine_classes', 'exec'), namespace)
verified = []
for name in ('FullBattleEngine', 'FastBattleEngine'):
    en = setup(namespace[name])
    uid, ch = moon(en)
    en._set_effects([e for e in en._get_effects() if not e.get('linked_summon')])
    en._tick_down()
    assert ch.get_attr('魔力') == 0 and en._get_combat_hp(uid) > 0
    en._execute_spell('actor', 'actor', spell(en, 8))
    assert any(s['index'] == 10 for s in en.get_char('actor').spells)
    before = en.get_char('actor').get_attr('魔力')
    with patch.object(en, '_roll_phantom_bonus', return_value=(2, 'writing\n')) as writing:
        en._execute_spell('actor', 'enemy', spell(en, 6))
        assert writing.call_count == 1
    assert en.get_char('actor').get_attr('魔力') == before - 2
    assert ch.get_attr('魔力') == 4
    verified.append(name)
print(json.dumps({'integrated_engines_verified': verified}))
