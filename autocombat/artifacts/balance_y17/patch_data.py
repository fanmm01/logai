"""Apply the reviewed blood spell definitions without rewriting other characters."""
import ast
from pathlib import Path
import pprint

root = Path(__file__).resolve().parents[2]
path = root / 'characters_data.py'
source = path.read_text(encoding='utf-8')
node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'YEZHAOMING' for t in n.targets))
data = ast.literal_eval(node.value)
spells = data['spells']
data['blood_ml_per_hp'] = 500
spells[2] = dict(name='凝血式-守', category=4, timing='3', 消耗mp=0,
                effects=[dict(type=4, 客体=1, 持续回合=10,
                              辅助效果='受到伤害加值', 辅助效果值='-1')])
spells[3]['effects'][0].pop('damage_pct_caster_max_hp', None)
spells[3]['effects'][0]['damage_pct_target_max_hp'] = 90
spells[4] = dict(name='凝血式-幻', category=4, timing='3', 消耗mp=0,
                cost_blood_ml=100,
                effects=[dict(type=4, 客体=1, 持续回合=10)])
spells[5].pop('on_kill_blood_ml', None)
spells[6]['cost_blood_ml'] = 500
spells[6]['timing'] = '23'
spells[6]['effects'][0]['客体'] = 12
spells[9]['timing'] = '3'
spells[9]['effects'][0]['回复mp'] = '4'
spells[11] = dict(name='血色突刺', category=1, timing='23', 消耗mp=0,
                 cost_blood_ml=1000,
                 effects=[dict(type=1, 客体=4, 射程=8, 成功率=61,
                               可闪避性=1, 可反击性=0, 可贯穿性=1,
                               伤害骰='1d10+6')])
lines = source.splitlines(keepends=True)
lines[node.lineno - 1:node.end_lineno] = [
    'YEZHAOMING = ' + pprint.pformat(data, width=110, sort_dicts=False) + '\n']
source = ''.join(lines)
source = source.replace("if char_data.get('serial') == 'Y16':\n        char.set_str('_weapon_name', 'staff')",
    "if char_data.get('serial') == 'Y16':\n        char.set_str('_weapon_name', 'staff')\n"
    "    elif char_data.get('serial') == 'Y17':\n        char.set_str('_weapon_name', 'bow')")
path.write_text(source, encoding='utf-8', newline='\n')
