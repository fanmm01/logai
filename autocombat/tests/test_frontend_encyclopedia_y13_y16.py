"""Check static frontend prose against the scoped live combat data.

The approved text fixture belongs to the frontend. No legacy encyclopedia
generator is used here, and changing combat data does not regenerate prose.
"""
import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERIALS = ('Y13', 'Y14', 'Y15', 'Y16')


class FrontendEncyclopediaY13Y16Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = json.loads((ROOT / 'frontend/src/data/encyclopedia.json').read_text(encoding='utf-8'))
        cls.cards = {}
        # Inspect literal cards without loading engines or touching their logs.
        tree = ast.parse((ROOT / 'characters_data.py').read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
                continue
            try:
                card = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if card.get('serial') in SERIALS:
                cls.cards[card['serial']] = card

    def article(self, serial, spell):
        return next(a for a in self.profiles[serial]['skills']
                    if (a['name'], a['phase']) == (spell['name'], spell.get('phase', 0)))

    def text(self, serial, name):
        a = next(a for a in self.profiles[serial]['skills'] if a['name'] == name)
        return '\n'.join([a['cost'], a['limits'], a['description'], *a['facts']])

    def test_all_live_skill_keys_have_one_frontend_article(self):
        self.assertEqual(set(self.cards), set(SERIALS))
        count = 0
        for serial, card in self.cards.items():
            expected = [(s['name'], s.get('phase', 0)) for s in card['spells']]
            actual = [(s['name'], s['phase']) for s in self.profiles[serial]['skills']]
            self.assertCountEqual(actual, expected)
            self.assertEqual(len(actual), len(set(actual)))
            count += len(expected)
        self.assertEqual(count, 47)

    def test_fixed_costs_damage_checks_and_ranges_remain_in_static_text(self):
        for serial, card in self.cards.items():
            for spell in card['spells']:
                a = self.article(serial, spell)
                text = '\n'.join([a['cost'], a['limits'], a['description'], *a['facts']])
                cost = spell.get('消耗mp')
                if cost:
                    self.assertIn(f'{cost} MP', text, (serial, spell['name'], 'cost'))
                for effect in spell['effects']:
                    if effect['type'] != 1:
                        continue
                    if not effect.get('consume_moon'):
                        self.assertIn(effect['伤害骰'], text, (serial, spell['name'], 'damage'))
                    self.assertIn(f"检定值 {effect['成功率']}", text, (serial, spell['name'], 'check'))
                    self.assertIn(f"原始射程 {effect.get('射程', 2)} 格", text)
                    self.assertIn('99 格', text)

    def test_cartridge_resource_rules_and_noncombat_phase_are_explicit(self):
        rules = self.cards['Y14']['passive_rules']['cartridge']
        self.assertIn(f"共享 {rules['cooldown']} 轮冷却", self.text('Y14', '卡带切换·魂斗罗'))
        self.assertIn(f"上限 {rules['limit_cap']} 点", self.text('Y14', '极限槽'))
        rings = self.text('Y14', '金环')
        self.assertIn(f"上限 {rules['ring_cap']} 枚", rings)
        self.assertIn(f"至少 {rules['dash_distance']} 格", rings)
        for name in ('燃梦孤灯·自身', '燃梦孤灯·他人'):
            self.assertIn('阶段 99 专属｜常规战斗无法使用', self.text('Y15', name))
        for name in ('爆破圈', '极限技·凶斩'):
            serial = 'Y15' if name == '爆破圈' else 'Y14'
            spell = next(s for s in self.cards[serial]['spells'] if s['name'] == name)
            self.assertTrue(spell['_once_per_battle'])
            self.assertIn('每场战斗限一次', self.text(serial, name))

    def test_cloak_writing_and_moon_values_have_not_drifted(self):
        cloak = self.cards['Y15']['passive_rules']['cloak']
        text = self.text('Y15', '火鼠裘')
        self.assertIn(f"每次减伤消耗 {cloak['mp_per_hit']} MP", text)
        self.assertIn(f"不超过 {cloak['immune_max']} 点", text)
        self.assertIn(f"{cloak['middle_below'] - 1} 点时降为 {cloak['middle_damage']} 点", text)
        self.assertIn(f"保留 {cloak['high_multiplier'] * 100:g}%", text)
        card = self.cards['Y16']
        writing = self.text('Y16', '幻造兵武')
        self.assertIn(f"基础写作值为 {card['attrs']['写作']}", writing)
        for rank, dice in card['passive_rules']['phantom'].items():
            self.assertIn(dice.lstrip('-'), writing)
        threshold = card['passive_rules']['moon']['ultimate_threshold']
        self.assertIn(f'月能超过 {threshold} 点', self.text('Y16', '月光王座全额率输出'))
        shield = next(s for s in card['spells'] if s['name'] == '已然遥远的理想城')['effects'][0]
        self.assertEqual(shield['shield_absorb_moon_ratio'], 1)
        self.assertIn('每实际吸收 1 点伤害', self.text('Y16', '已然遥远的理想城'))


if __name__ == '__main__':
    unittest.main()
