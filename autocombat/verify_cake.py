import sys
sys.path.insert(0, '.')
from battle_engine import FullBattleEngine

engine = FullBattleEngine()
engine.setup_battle(['Y8'], ['Y5'], '10x10')
print('skills', [(s['index'], s['name']) for s in (engine.get_char('Y8').spells or engine.load_spells('Y8')) if s['index'] in (1, 2)])
print('cast1', engine._use_skill('Y8', 1, ''))
print('effects_after_cast', engine._get_effects())
engine._tick_down()
print('ready_after_tick', engine._has_ready_cake(), engine._get_effects())
print('self_eat', engine._eat_cake('Y8'))
print('after_self_eat_hp', engine._get_combat_hp('Y8'))
print('after_self_eat_mp', engine.get_char('Y8').get_attr('魔力', 0))

engine2 = FullBattleEngine()
engine2.setup_battle(['Y8', 'Y5'], ['Y9'], '10x10')
print('cast2', engine2._use_skill('Y8', 1, ''))
engine2._tick_down()
print('ready2', engine2._has_ready_cake())
print('before_give_hp', engine2._get_combat_hp('Y5'))
print('give_to_teammate', engine2._eat_cake('Y8', 'Y5'))
print('after_give_hp', engine2._get_combat_hp('Y5'))
