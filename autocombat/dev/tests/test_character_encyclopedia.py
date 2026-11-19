"""Library coverage and observable behavior of character-owned passive tuning."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import battle_engine as be
import characters_data as cd
from character_encyclopedia import ARTICLES, ARTICLE_ORDER, PROFILES, encyclopedia
from tests.encyclopedia_helpers import resolved_encyclopedia

ROOT = Path(__file__).resolve().parents[2]


# Approved first-plan copy. This fixture is independent of the shipped JSON.
Y5Y6_APPROVED = {
    ('Y5', '飞行'): {
        'description': '战斗开始时消耗 2 MP，获得行动力 +5 和 1 颗攻击奖励骰，持续生效。',
        'requirements': [('时机', '被动'), ('基础费用', '2 MP'), ('目标', '自身')],
        'paragraphs': [
            '攻击奖励骰适用于普通攻击、法术攻击及反击。魔力不足时无法获得本技能的增益。',
            '血天使自身具有飞行属性，飞行提供行动力 +8；本技能的行动力加成与之叠加。',
        ],
    },
    ('Y5', '吸血'): {
        'description': '对敌方单体发动吸血攻击，以自身最佳近战技能检定，造成 2d4+1d6+2 伤害，并按伤害的 100% 回复自身生命。',
        'requirements': [('时机', '主动'), ('基础费用', '0 MP'), ('目标', '敌方单体'),
                         ('射程', '基础 2 格；当前全局射程覆盖启用时为 99 格'), ('防御反应', '反击、格挡')],
        'paragraphs': [
            '攻击命中、护盾吸收后仍有伤害，且自身生命大于零时，触发生命回复。回复量以护盾吸收前的修正伤害乘以吸血比例，向下取整，受自身生命上限约束。',
        ],
    },
    ('Y6', '制造雪带'): {
        'description': '消耗 4 MP，在自身所在位置创建半径 5 格的固定雪带，持续 6 轮。自下一轮起，每轮为区域内敌方单位增加 2 层冰冻。',
        'requirements': [('时机', '主动'), ('基础费用', '4 MP'), ('作用对象', '区域内敌方单位'),
                         ('作用半径', '5 格'), ('持续时间', '6 轮'), ('同时存在上限', '每名施法者 1 处')],
        'paragraphs': [
            '雪带以创建位置为中心。创建后的六次轮首各施加一次冰冻；每轮先衰减已有冰冻，再增加雪带提供的层数。',
            '同一施法者已有雪带时无法再次创建。雪人的雪带与小雪人的雪带分别计算名额；覆盖同一敌人时，两者提供的冰冻层数相加。',
        ],
    },
    ('Y6', '雪幕'): {
        'description': '战斗开始时消耗 2 MP，生成随自身移动、半径 5 格的雪幕，使范围内己方单位受到的伤害降低 10%。',
        'requirements': [('时机', '被动'), ('基础费用', '2 MP'),
                         ('作用对象', '区域内己方单位，含自身'), ('作用半径', '5 格')],
        'paragraphs': [
            '减伤随单位进出范围即时生效或解除。雪幕随来源存活并留在战场而持续；来源生命降至零或离场后消散。',
            '来源受到隔离时，雪幕暂停生效；隔离解除后恢复。开战时魔力不足则无法生成雪幕。',
        ],
    },
    ('Y6', '发送雪球'): {
        'description': '消耗 2 MP，向同一敌人连续投出两枚雪球。每枚分别以 80 的技能值检定，造成 1d8+2 伤害，命中后增加 1 层冰冻。',
        'requirements': [('时机', '主动'), ('基础费用', '2 MP'), ('目标', '敌方单体'),
                         ('射程', '99 格'), ('防御反应', '闪避、格挡')],
        'paragraphs': [
            '两枚雪球分别进行攻击检定与防御反应，一次施放支付一次费用。某枚雪球未命中时，继续处理另一枚；目标倒下后停止后续攻击。',
            '命中后的冰冻独立于实际生命损失，伤害被护盾完全吸收或被一次伤害免疫抵消时，仍可增加冰冻层数。',
        ],
    },
    ('Y6', '雪之法杖'): {
        'description': '战斗开始时，为在场己方单位附加 1d6 攻击伤害骰，持续 5 轮，并分别授予 1d8+4 点护盾。',
        'requirements': [('时机', '被动'), ('基础费用', '0 MP'),
                         ('目标', '开战时在场的己方单位，含自身与召唤物')],
        'paragraphs': [
            '伤害骰加值适用于普通攻击、法术攻击及反击，持续时间结束后解除。',
            '每名目标的护盾容量分别掷骰。护盾持续至耗尽，保留超过 5 轮后的剩余容量。',
            '两项效果均在开战时授予；后续加入战场的单位须通过其他方式获得增益。',
        ],
    },
    ('Y6', '堆雪人'): {
        'description': '消耗 5 MP，召唤 1 只小雪人，持续 999 轮，技能冷却 4 轮。同一施法者最多同时拥有 1 只小雪人。',
        'requirements': [('时机', '主动'), ('基础费用', '5 MP'), ('技能冷却', '4 轮'),
                         ('召唤数量', '1 只'), ('同时存活上限', '1 只'), ('持续时间', '999 轮')],
        'summon_rows': [
            ('生命', '35'), ('初始魔力', '12'), ('基础行动力', '8'), ('敏捷', '50'),
            ('闪避', '35'), ('防御反应', '闪避'),
            ('小雪带', '消耗 3 MP；半径 3 格；持续 4 轮；每轮增加 2 层冰冻'),
            ('雪球', '消耗 1 MP；技能值 65；伤害 1d8+2；射程 8 格；可闪避、格挡；命中增加 2 层冰冻'),
            ('近战攻击', '斗殴 45；伤害 1d4；命中增加 2 层冰冻'),
        ],
        'paragraphs': [
            '小雪人自动行动。魔力足够、半径 3 格内存在敌人且自身尚无雪带时，优先创建小雪带；其他情况下接近最近的敌人并投出一枚雪球。魔力耗尽后改用近战攻击。',
            '小雪带固定在创建位置，自下一轮起每轮施加冰冻。同一小雪人同时维持 1 处小雪带。',
            '小雪人与主人向同一敌人施加的冰冻合并计算，冰冻触发的生命与魔力回复归主人所有。',
            '小雪人死亡或持续时间结束后释放存活名额，冷却结束后可再次召唤；主人死亡时随之退场。',
        ],
    },
}

Y6_APPROVED_PASSIVES = [
    '冰冻来源',
    '雪人普通攻击及反击命中增加 2 层冰冻，自身每枚雪球命中增加 1 层。小雪人的雪球与近战攻击命中增加 2 层。雪带每轮增加的层数分别计算。',
    '冰冻按主人和敌方目标分别记录。每轮先衰减 1 层，再增加雪带提供的层数，随后处理持续伤害、到期爆发和持续回复。',
    '八层循环',
    '第 1 层：来自雪人所属阵营的伤害提高 18%。',
    '第 2 层：技能值修正 −10，并增加一份每轮 1d4+2 的持续伤害。',
    '第 3 层：造成的伤害降低 20%，行动力 −5；主人立即回复 2d10 生命。',
    '第 4 层：登记一次冰冻爆发：下一轮行动力为零，并受到 1d10+1d6+2 伤害。',
    '第 5 层：来自雪人所属阵营的伤害再提高 18%。',
    '第 6 层：主人自下一轮起，每轮回复 1d6+2 生命与 1d10 魔力。',
    '第 7 层：再增加一份技能值修正 −10 和每轮 1d4+2 的持续伤害。',
    '第 8 层：再登记一次冰冻爆发，并移除本循环第 1、5 层的易伤及第 7 层追加的技能削减与持续伤害。',
    '叠加与衰减',
    '每增加八层进入下一循环。各循环第 2 层的技能削减与持续伤害、第 3 层的伤害降低与行动力削减，以及第 6 层产生的持续回复可以叠加。',
    '技能削减、伤害降低与易伤分别相加；行动力削减按份数累加。每份持续伤害和持续回复分别掷骰。',
    '自然衰减会按剩余层数解除相应减益。冰冻归零前，同一循环已经触发的即时回复、爆发和持续回复保留触发记录；重新达到该层时沿用原记录。第 8 层已经移除的减益也保持移除，直至冰冻归零后重新累计。',
    '已登记的爆发在下一轮生效，每次造成一段伤害；冰冻提前归零后仍会触发。爆发当轮行动力为零。',
    '持续回复在冰冻归零后继续生效，可由不同敌人、不同循环分别增加。生命与魔力回复受各自上限约束，主人生命大于零时生效。',
]


class EncyclopediaTests(unittest.TestCase):
    def test_every_skill_has_correctly_matched_article_and_unique_asset(self):
        assets = set()
        self.assertEqual(len(PROFILES), len(cd.ALL_CHARACTERS))
        self.assertEqual(len({p[0] for p in PROFILES.values()}), len(PROFILES))
        for c in cd.ALL_CHARACTERS:
            with self.subTest(serial=c['serial']):
                self.assertEqual(ARTICLE_ORDER[c['serial']], [(s['name'], s.get('phase', 0)) for s in c['spells']])
                self.assertEqual(sum(key[0] == c['serial'] for key in ARTICLES), len(c['spells']))
                article = encyclopedia(c, cd.SUMMON_TEMPLATES, cd.ITEM_TEMPLATES)
                self.assertEqual(len(article['traits']), 2)
                for skill in article['skills']:
                    self.assertTrue(skill['description'])
                    self.assertTrue(skill['facts'] or skill['versions'][0]['sections'])
                    path = ROOT / 'frontend/public' / skill['icon'].removeprefix('/ui/')
                    ET.parse(path)
                    raw = path.read_bytes()
                    self.assertNotIn(raw, assets)
                    assets.add(raw)

    def test_reordered_skills_keep_their_own_prose(self):
        c = copy.deepcopy(cd.YANYAN)
        before = encyclopedia(c, cd.SUMMON_TEMPLATES, cd.ITEM_TEMPLATES)['skills']
        c['spells'].reverse()
        after = encyclopedia(c, cd.SUMMON_TEMPLATES, cd.ITEM_TEMPLATES)['skills']
        self.assertEqual(before[0]['description'], after[-1]['description'])

    def test_cloak_balance_data_controls_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            card = copy.deepcopy(next(c for c in cd.ALL_CHARACTERS if c['serial'] == 'Y15'))
            card['passive_rules']['cloak'].update(high_multiplier=.25, mp_per_hit=2)
            en = cls(); cd.load_character_to_engine(en, card, 'actor')
            char = en.get_char('actor'); old = char.get_attr('魔力')
            self.assertEqual(en._cloak_damage('actor', 20), 5)
            self.assertEqual(char.get_attr('魔力'), old - 2)
            char.set_attr('魔力', 1)
            self.assertEqual(en._cloak_damage('actor', 20), 20)

    def test_writing_and_moon_tables_are_character_owned(self):
        card = copy.deepcopy(next(c for c in cd.ALL_CHARACTERS if c['serial'] == 'Y16'))
        card['passive_rules']['phantom']['1'] = '11'
        en = be.FullBattleEngine(); cd.load_character_to_engine(en, card, 'actor')
        self.assertEqual(en._phantom_rank_bonus('actor', 1), 11)
        self.assertEqual(en._phantom_rank_bonus('actor', 0), 0)
        # A failed writing roll must retain its signed penalty after migration.
        with patch.object(be.random, 'randint', return_value=2):
            self.assertEqual(en._phantom_rank_bonus('actor', -2), -2)
        card = next(c for c in cd.ALL_CHARACTERS if c['serial'] == 'Y17')
        cd.load_character_to_engine(en, card, 'blood')
        self.assertEqual(en._y17_rules('blood')['moon_phases'][4][1], '1d16+12')

    def test_catalog_exposes_only_combat_data_and_serves_frontend_icons(self):
        from tests.test_battle_execution import http_module
        client = http_module().app.test_client()
        response = client.get('/api/ui/catalog')
        self.assertEqual(response.status_code, 200)
        chars = response.get_json()['characters']
        self.assertEqual(sum(len(c['spells']) for c in chars), 106)
        for c in chars:
            self.assertNotIn('skills', c)
            self.assertNotIn('traits', c)
        articles = json.loads((ROOT / 'frontend/src/data/encyclopedia.json').read_text(encoding='utf-8'))
        icon = client.get(articles[chars[0]['serial']]['skills'][0]['icon'])
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.mimetype, 'image/svg+xml')
        icon.close()


class Y1Y2EncyclopediaTests(unittest.TestCase):
    def setUp(self):
        self.registry = patch.object(be, '_SUMMON_TEMPLATES', copy.deepcopy(cd.SUMMON_TEMPLATES))
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def articles(self, card, summons=None):
        return encyclopedia(card, cd.SUMMON_TEMPLATES if summons is None else summons,
                            cd.ITEM_TEMPLATES)['skills']

    def engine(self, cls=be.FullBattleEngine, mp=None):
        eng = cls()
        for uid, card in [('flame', cd.YANYAN), ('mage', cd.DANIUSI)]:
            cd.load_character_to_engine(eng, card, uid)
        if mp is not None:
            eng.get_char('mage').set_attr('魔力', mp)
        eng.setup_battle(['flame'], ['mage'], '10x10', season_status=50)
        return eng

    def spell(self, eng, uid, name):
        return next(s for s in eng.get_char(uid).spells if s['name'] == name)

    def test_copy_matches_approved_plan_without_isolation_text(self):
        expected = {
            ('Y1', '斗殴精通'): (
                '战斗开始时获得 1 颗攻击奖励骰，适用于普通攻击、法术攻击及反击，持续生效。', []),
            ('Y1', '火焰附魔'): (
                '发动一次火焰攻击，以自身最佳近战技能检定，造成 2d6+1d8 伤害。目标可以闪避、反击或格挡。',
                ['命中后附加灼烧，每轮造成 2d4+2 伤害，持续 4 轮。多次命中产生的灼烧分别生效。']),
            ('Y2', '火球术'): (
                '向同一目标连续发射三枚火球，每枚分别以 90 的技能值检定，依次造成 1d8+2、1d8+2、1d8+2 伤害。',
                ['目标仅可格挡。某枚火球未命中时，继续结算其余火球；目标倒下后停止后续攻击。']),
            ('Y2', '召唤使魔'): (
                '召唤 1 只使魔加入战斗，同一施法者最多同时拥有 12 只。名额已满时无法施放，使魔死亡后可以补召。',
                ['生命：24', '基础行动力：9', '敏捷：70', '斗殴：55', '攻击伤害：1d6+1d4',
                 '闪避：35', '飞行：是', '防御反应：闪避、反击',
                 '同一施法者的使魔组成召唤组，共享先攻位置，各自保留生命与状态。使魔持续 99 回合，到期退场；主人死亡时随之退场。']),
            ('Y2', '速攻'): (
                '战斗开始时支付费用，获得 行动力 +5，持续生效。魔力不足时无法发动。', []),
        }
        seen = set()
        for card in (cd.YANYAN, cd.DANIUSI):
            for skill in self.articles(card):
                key = card['serial'], skill['name']
                with self.subTest(key=key):
                    seen.add(key)
                    self.assertEqual((skill['description'], skill['facts']), expected[key])
                    self.assertTrue(skill['reviewed'])
                    self.assertEqual(json.loads(skill['id']), [*key, 0])
                    self.assertNotIn('隔离', json.dumps(skill, ensure_ascii=False))
                    for version in skill['versions']:
                        self.assertEqual(version['overview'], expected[key][0])
                        self.assertTrue(version['sections'])
                        self.assertNotIn('steps', [s['id'] for s in version['sections']])
        self.assertEqual(seen, set(expected))

    def test_stable_keys_and_input_data_are_preserved(self):
        for original in (cd.YANYAN, cd.DANIUSI):
            card = copy.deepcopy(original)
            before = {s['id']: s for s in self.articles(card)}
            self.assertEqual(card, original)
            card['spells'].reverse()
            self.assertEqual(before, {s['id']: s for s in self.articles(card)})
            other_phase = copy.deepcopy(card['spells'][0])
            other_phase['phase'] = 99
            card['spells'].append(other_phase)
            self.assertFalse(self.articles(card)[-1]['reviewed'])

    def test_live_skill_values_and_effect_order(self):
        card = copy.deepcopy(cd.YANYAN)
        card['spells'][0]['effects'][0]['辅助效果值'] = 'b2'
        card['spells'][1]['消耗mp'] = 7
        card['spells'][1]['effects'][0].update(伤害骰='3d9', 每回合伤害骰='1d3', 持续回合=6, 射程=8)
        mastery, fire = self.articles(card)
        self.assertIn('2 颗攻击奖励骰', mastery['description'])
        self.assertEqual(fire['cost'], '7 MP')
        self.assertIn('3d9', fire['description'])
        self.assertIn('1d3', fire['facts'][0])
        self.assertIn('6 轮', fire['facts'][0])
        self.assertIn('8 格', fire['limits'])
        card = copy.deepcopy(cd.DANIUSI)
        card['spells'][0]['effects'][1].update(伤害骰='4d7', 成功率=81)
        card['spells'][0]['effects'].reverse()
        card['spells'][2]['消耗mp'] = 5
        card['spells'][2]['effects'][0]['技能加减值'] = '行动力+9'
        fireball, _, speed = self.articles(card)
        self.assertIn('1d8+2、4d7、1d8', fireball['description'])
        self.assertIn('90、81', fireball['description'])
        self.assertEqual(speed['cost'], '5 MP')
        self.assertIn('行动力 +9', speed['description'])

    def test_live_summon_template(self):
        summons = copy.deepcopy(cd.SUMMON_TEMPLATES)
        summons['使魔'].update(HP=31, MOV=7, max_simultaneous=5, skills=['斗殴:61 2d9'])
        snapshot = copy.deepcopy(summons)
        skill = self.articles(cd.DANIUSI, summons)[1]
        self.assertIn('5 只', skill['description'])
        for fact in ('生命：31', '基础行动力：7', '斗殴：61', '攻击伤害：2d9'):
            self.assertIn(fact, skill['facts'])
        self.assertEqual(summons, snapshot)

    def test_passive_cost_bonus_and_persistence_match_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                self.assertEqual(eng.get_char('mage').get_attr('魔力'), cd.DANIUSI['attrs']['魔力'] - 2)
                self.assertEqual(eng._get_effective_mov('mage'), cd.DANIUSI['attrs']['行动力'] + 5)
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('flame')), 'b')
                effects = eng._get_effects()
                for effect in effects:
                    if effect.get('sourcePassive'):
                        effect['remainingRounds'] = 1
                eng._set_effects(effects)
                eng._tick_down()
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('flame')), 'b')
                self.assertEqual(eng._get_effective_mov('mage'), cd.DANIUSI['attrs']['行动力'] + 5)
                poor = self.engine(cls, mp=1)
                self.assertEqual(poor.get_char('mage').get_attr('魔力'), 1)
                self.assertEqual(poor._get_effective_mov('mage'), cd.DANIUSI['attrs']['行动力'])

    def test_fire_dot_is_separate_and_stacks_on_repeated_hits(self):
        eng = self.engine()
        eng.get_char('mage').set_attr('体力上限', 1000)
        eng._set_combat_hp('mage', 1000)
        spell = self.spell(eng, 'flame', '火焰附魔')
        with patch.object(eng, '_roll_spell_success_bp', return_value=(50, '')), \
             patch.object(eng, '_trigger_spell_reaction', return_value=(False, False, [])):
            eng._execute_spell('flame', 'mage', spell)
            eng._execute_spell('flame', 'mage', spell)
        dots = [e for e in eng._get_effects() if e['type'] == 'dot']
        self.assertEqual(len(dots), 2)
        self.assertTrue(all(e['dotDice'] == '2d4+2' and e['remainingRounds'] == 4 for e in dots))
        self.assertTrue(all(not e.get('persistent') for e in dots))
        before = eng._hp_safe('mage')
        with patch.object(be, 'roll_dice', return_value=6):
            eng._tick_down()
        self.assertEqual(eng._hp_safe('mage'), before - 12)

    def test_fire_failed_or_defended_hit_has_no_dot(self):
        for roll, defended in ((99, False), (50, True)):
            with self.subTest(roll=roll, defended=defended):
                eng = self.engine()
                spell = self.spell(eng, 'flame', '火焰附魔')
                old_mp = eng.get_char('flame').get_attr('魔力')
                with patch.object(eng, '_roll_spell_success_bp', return_value=(roll, '')), \
                     patch.object(eng, '_trigger_spell_reaction', return_value=(defended, False, [])):
                    eng._execute_spell('flame', 'mage', spell)
                self.assertFalse(any(e['type'] == 'dot' for e in eng._get_effects()))
                self.assertEqual(eng.get_char('flame').get_attr('魔力'), old_mp - 2)

    def test_fireball_independent_checks_one_payment_and_block_permission(self):
        eng = self.engine()
        eng.get_char('flame').set_attr('体力上限', 1000)
        eng._set_combat_hp('flame', 1000)
        spell = self.spell(eng, 'mage', '火球术')
        old_mp = eng.get_char('mage').get_attr('魔力')
        with patch.object(eng, '_roll_spell_success_bp', side_effect=[(99, ''), (50, ''), (50, '')]) as checks, \
             patch.object(eng, '_trigger_spell_reaction', return_value=(False, False, [])) as reactions, \
             patch.object(eng, '_apply_spell_damage', wraps=eng._apply_spell_damage) as damage:
            eng._execute_spell('mage', 'flame', spell)
        self.assertEqual(checks.call_count, 3)
        self.assertEqual([call.args[2] for call in damage.call_args_list], ['1d8+2', '1d8+2'])
        self.assertEqual(eng.get_char('mage').get_attr('魔力'), old_mp - 3)
        self.assertTrue(reactions.call_args_list)
        for call in reactions.call_args_list:
            self.assertEqual(call.args[5:8], (0, 0, 1))

    def test_fireball_stops_after_target_falls(self):
        eng = self.engine()
        eng._set_combat_hp('flame', 1)
        with patch.object(eng, '_roll_spell_success_bp', return_value=(1, '')) as checks, \
             patch.object(eng, '_trigger_spell_reaction', return_value=(False, False, [])):
            eng._execute_spell('mage', 'flame', self.spell(eng, 'mage', '火球术'))
        self.assertLessEqual(eng._hp_safe('flame'), 0)
        self.assertEqual(checks.call_count, 1)

    def test_summon_quota_group_and_lifetime(self):
        be._SUMMON_TEMPLATES['使魔']['max_simultaneous'] = 2
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                spell = copy.deepcopy(self.spell(eng, 'mage', '召唤使魔'))
                spell['effects'][0]['持续回合'] = 1
                for _ in range(2):
                    self.assertTrue(be.action_committed(eng._execute_spell('mage', 'mage', spell)))
                self.assertEqual(eng._get_summon_live_count('mage', '使魔'), 2)
                old_mp = eng.get_char('mage').get_attr('魔力')
                self.assertFalse(be.action_committed(eng._execute_spell('mage', 'mage', spell)))
                self.assertEqual(eng.get_char('mage').get_attr('魔力'), old_mp)
                group = next(g for g in eng._summon_groups.values() if g['ownerId'] == 'mage')
                self.assertEqual(len(group['members']), 2)
                timers = [e for e in eng._get_effects() if e['type'] == 'summon']
                self.assertEqual(len(timers), 2)
                self.assertTrue(all(e['remainingRounds'] == 1 for e in timers))
                eng._tick_down()
                eng._tick_down()
                self.assertEqual(eng._get_summon_live_count('mage', '使魔'), 0)
                self.assertFalse(any(e['type'] == 'summon' for e in eng._get_effects()))
                self.assertTrue(be.action_committed(eng._execute_spell('mage', 'mage', spell)))
                self.assertEqual(eng._get_summon_live_count('mage', '使魔'), 1)

    def test_catalog_preserves_raw_y1_y2_spells(self):
        from tests.test_battle_execution import http_module
        client = http_module().app.test_client()
        response = client.get('/api/ui/catalog')
        self.assertEqual(response.status_code, 200)
        characters = response.get_json()['characters']
        for card in (cd.YANYAN, cd.DANIUSI):
            actual = next(c for c in characters if c['serial'] == card['serial'])
            self.assertEqual(actual['spells'], card['spells'])
            self.assertNotIn('skills', actual)


class Y3Y4EncyclopediaTests(unittest.TestCase):
    def articles(self, card):
        return encyclopedia(card, cd.SUMMON_TEMPLATES, cd.ITEM_TEMPLATES)['skills']

    def engine(self, cls, mp=None):
        eng = cls()
        for uid, original in [('bull', cd.LINGNIU), ('star', cd.XINGSHAN), ('enemy', cd.YANYAN)]:
            card = copy.deepcopy(original)
            if uid == 'bull' and mp is not None:
                card['attrs']['魔力'] = mp
            cd.load_character_to_engine(eng, card, uid)
        eng.setup_battle(['bull'], ['star', 'enemy'], '10x10', season_status=50)
        for uid in ('bull', 'star', 'enemy'):
            eng.get_char(uid).set_attr('体力上限', 1000)
            eng._set_combat_hp(uid, 1000)
        return eng

    def spell(self, eng, uid, name):
        return next(s for s in eng.get_char(uid).spells if s['name'] == name)

    def test_approved_copy_and_y1_y2_sections(self):
        expected = {
            '斗殴强化': ('战斗开始时获得 1 颗攻击奖励骰，适用于普通攻击、法术攻击及反击，持续生效。',
                         [('时机', '被动'), ('费用', '1 MP'), ('目标', '自身')], []),
            '附魔术': ('战斗开始时支付费用，为自身的普通攻击及法术攻击附加 1d6+1d4 伤害，持续生效。魔力不足时无法发动。',
                       [('时机', '被动'), ('费用', '1 MP'), ('目标', '自身')], []),
            '星闪攻击': ('向同一目标连续发动两段星闪攻击，每段分别以 75 的技能值检定，各获得 1 颗攻击奖励骰，依次造成 1d12+4+1d3、1d12+4+1d3 伤害。',
                         [('时机', '主动'), ('费用', '1 MP'), ('射程', '99 格'), ('目标', '敌方单体')],
                         ['目标仅可格挡。每段攻击分别处理格挡；某段攻击未命中或被格挡时，继续结算其余攻击；目标倒下后停止后续攻击。', '贯穿：是。']),
            '演唱': ('使敌方全体获得 1 颗攻击惩罚骰，适用于普通攻击、法术攻击及反击，持续 3 轮。',
                     [('时机', '附加动作'), ('费用', '2 MP'), ('目标', '敌方全体')],
                     ['施放后直接对全场存活敌人生效，包含召唤物。重复施放时，各目标已有的演唱效果刷新至至少剩余 3 轮，同名效果保持 1 颗攻击惩罚骰。持续时间结束后解除。']),
        }
        for card in (cd.LINGNIU, cd.XINGSHAN):
            for skill in self.articles(card):
                with self.subTest(skill=skill['name']):
                    prose, requirements, facts = expected[skill['name']]
                    self.assertEqual(skill['description'], prose)
                    self.assertEqual(skill['facts'], facts)
                    self.assertTrue(skill['reviewed'])
                    self.assertNotIn('附加伤害与原攻击合并', json.dumps(skill, ensure_ascii=False))
                    for version in skill['versions']:
                        self.assertEqual(version['overview'], prose)
                        self.assertEqual([s['id'] for s in version['sections']],
                                         ['inputs', 'effects'] if facts else ['inputs'])
                        self.assertEqual(version['sections'][0]['title'], '施放要求')
                        self.assertEqual([(r['label'], r['value']) for r in version['sections'][0]['rows']], requirements)
                        if facts:
                            self.assertEqual(version['sections'][1]['title'], '技能效果')
                            self.assertEqual(version['sections'][1]['paragraphs'], facts)

    def test_live_values_order_identity_and_no_mutation(self):
        for original in (cd.LINGNIU, cd.XINGSHAN):
            card = copy.deepcopy(original)
            before = {s['id']: s for s in self.articles(card)}
            self.assertEqual(card, original)
            card['spells'].reverse()
            self.assertEqual(before, {s['id']: s for s in self.articles(card)})
            extra = copy.deepcopy(card['spells'][0])
            extra['phase'] = 99
            card['spells'].append(extra)
            self.assertFalse(self.articles(card)[-1]['reviewed'])
        bull = copy.deepcopy(cd.LINGNIU)
        bull['spells'][0]['effects'][0]['辅助效果值'] = 'b2'
        bull['spells'][1]['消耗mp'] = 4
        bull['spells'][1]['effects'][0]['辅助效果值'] = '3d7'
        mastery, enchant = self.articles(bull)
        self.assertIn('2 颗攻击奖励骰', mastery['description'])
        self.assertIn('3d7', enchant['description'])
        self.assertEqual(enchant['cost'], '4 MP')
        star = copy.deepcopy(cd.XINGSHAN)
        attacks = star['spells'][0]['effects']
        attacks[0].update(伤害骰='2d9', 成功率=81, 成功率奖惩骰=2, 射程=8)
        attacks.reverse()
        star['spells'][1]['消耗mp'] = 5
        star['spells'][1]['effects'][0].update(持续回合=6, 辅助效果值='p2')
        attack, song = self.articles(star)
        self.assertIn('1d12+4+1d3、2d9', attack['description'])
        self.assertIn('75、81', attack['description'])
        self.assertIn('1 颗攻击奖励骰、2 颗攻击奖励骰', attack['description'])
        self.assertIn('99、8 格', attack['limits'])
        attacks[0]['可贯穿性'] = 0
        self.assertEqual(self.articles(star)[0]['facts'][1], '贯穿：否、是。')
        self.assertEqual(song['cost'], '5 MP')
        self.assertIn('2 颗攻击惩罚骰', song['description'])
        self.assertIn('持续 6 轮', song['description'])
        self.assertIn('剩余 6 轮', song['facts'][0])

    def test_passive_payment_bonus_and_persistence_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                self.assertEqual(eng.get_char('bull').get_attr('魔力'), cd.LINGNIU['attrs']['魔力'] - 2)
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('bull')), 'b')
                self.assertEqual(eng._get_buff_dmg_dice_bonus('bull'), '1d6+1d4')
                effects = eng._get_effects()
                for effect in effects:
                    if effect.get('sourceUserId') == 'bull':
                        effect['remainingRounds'] = 1
                eng._set_effects(effects)
                eng._tick_down()
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('bull')), 'b')
                self.assertEqual(eng._get_buff_dmg_dice_bonus('bull'), '1d6+1d4')
                with patch.object(be, 'roll_dice_detailed', return_value=(8, '8')) as roll:
                    eng._apply_spell_damage('bull', 'star', '2d6', 0, 0)
                self.assertEqual(roll.call_args.args[0], '2d6+1d6+1d4')
                poor = self.engine(cls, mp=0)
                self.assertEqual(poor.get_char('bull').get_attr('魔力'), 0)
                self.assertEqual(poor._get_buff_dmg_dice_bonus('bull'), '')
                self.assertEqual(be._calc_net_bp(poor._get_active_buffs('bull')), '')

    def test_enchantment_basic_attack_and_counter_scope_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                with patch.object(eng, '_move_to_attack_range', return_value=(True, '')), \
                     patch.object(be.random, 'random', return_value=0), \
                     patch.object(be, 'roll_d100', side_effect=[(50, ''), (99, '')]), \
                     patch.object(be, 'max_damage', wraps=be.max_damage) as maximum:
                    if cls is be.FastBattleEngine:
                        eng._fast_coc7_attack('bull', 'star', '斗殴', 85, '2d6', 0, 0)
                    else:
                        eng._coc7_attack('bull', 'star', '斗殴', 85, '2d6', 0, 0)
                self.assertIn('2d6+1d6+1d4', [call.args[0] for call in maximum.call_args_list])
                with patch.object(be, 'roll_d100', return_value=(50, '')) as check, \
                     patch.object(be, 'roll_dice_detailed', return_value=(7, '7')) as damage:
                    _, countered, _ = eng._resolve_spell_counter(
                        'star', 'bull', {}, {'name': 'test'}, '1d4', '斗殴', 85, atk_rank=0)
                self.assertTrue(countered)
                self.assertEqual(check.call_args.args[0], 'b')
                self.assertEqual(damage.call_args.args[0], '2d6')

    def test_star_separate_checks_block_and_one_mp_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for first_roll, blocked in ((99, False), (50, True)):
                with self.subTest(engine=cls.__name__, roll=first_roll, blocked=blocked):
                    eng = self.engine(cls)
                    old_mp = eng.get_char('star').get_attr('魔力')
                    spell = self.spell(eng, 'star', '星闪攻击')
                    with patch.object(eng, '_roll_spell_success_bp', side_effect=[(first_roll, ''), (50, '')]) as checks, \
                         patch.object(eng, '_trigger_spell_reaction', side_effect=[(blocked, False, []), (False, False, [])]) as reactions, \
                         patch.object(eng, '_apply_spell_damage', wraps=eng._apply_spell_damage) as damage:
                        eng._execute_spell('star', 'bull', spell)
                    self.assertEqual(checks.call_count, 2)
                    self.assertEqual(damage.call_count, 1)
                    self.assertEqual(damage.call_args.args[:3], ('star', 'bull', '1d12+4+1d3'))
                    self.assertEqual(damage.call_args.args[3], 1)
                    self.assertEqual(eng.get_char('star').get_attr('魔力'), old_mp - 1)
                    for call in reactions.call_args_list:
                        self.assertEqual(call.args[5:8], (0, 0, 1))
                    with patch.object(be, 'roll_d100', return_value=(50, '')) as d100:
                        eng._roll_spell_success_bp('star', spell['effects'][0], spell)
                    self.assertEqual(d100.call_args.args[0], 'b')

    def test_star_stops_after_target_falls_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                eng._set_combat_hp('bull', 1)
                with patch.object(eng, '_roll_spell_success_bp', return_value=(1, '')) as checks, \
                     patch.object(eng, '_trigger_spell_reaction', return_value=(False, False, [])):
                    eng._execute_spell('star', 'bull', self.spell(eng, 'star', '星闪攻击'))
                self.assertLessEqual(eng._hp_safe('bull'), 0)
                self.assertEqual(checks.call_count, 1)

    def test_song_targets_refresh_payment_and_expiry_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                summon = eng._create_summon('bull', '使魔')
                spell = self.spell(eng, 'star', '演唱')
                old_mp = eng.get_char('star').get_attr('魔力')
                with patch.object(eng, '_roll_spell_success_bp') as checks:
                    eng._execute_spell('star', 'bull', spell)
                checks.assert_not_called()
                self.assertEqual(eng.get_char('star').get_attr('魔力'), old_mp - 2)
                def song_effects():
                    return [e for e in eng._get_effects() if e.get('spellName') == '演唱']
                self.assertEqual({e['targetUserId'] for e in song_effects()}, {'bull', summon})
                self.assertTrue(all(e['auxVal'] == 'p' and e['remainingRounds'] == 3 for e in song_effects()))
                eng._tick_down()
                mp_before_refresh = eng.get_char('star').get_attr('魔力')
                eng._execute_spell('star', 'bull', spell)
                self.assertEqual(len(song_effects()), 2)
                self.assertTrue(all(e['remainingRounds'] == 3 for e in song_effects()))
                self.assertEqual(eng.get_char('star').get_attr('魔力'), mp_before_refresh - 2)
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('bull')), '')
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs(summon)), 'p')
                for _ in range(3):
                    eng._tick_down()
                self.assertEqual(song_effects(), [])
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('bull')), 'b')

    def test_catalog_preserves_raw_y3_y4_spells(self):
        from tests.test_battle_execution import http_module
        response = http_module().app.test_client().get('/api/ui/catalog')
        self.assertEqual(response.status_code, 200)
        characters = response.get_json()['characters']
        for card in (cd.LINGNIU, cd.XINGSHAN):
            self.assertEqual(next(c for c in characters if c['serial'] == card['serial'])['spells'], card['spells'])


class Y7Y8EncyclopediaTests(unittest.TestCase):
    """Approved copy, live tuning and stable identities for the support pair."""

    CAKE_USE = [
        '蛋糕可供自身或同队存活、濒死单位食用。使用时优先消耗使用者个人库存中的首个可用蛋糕，再取用己方共享制造物池中的首个成品。',
        '同种蛋糕的增益重复食用时，剩余持续时间取原值与该蛋糕完整持续时间中的较大值。同种增益保留一份。',
        '携带个人生命蛋糕的单位进入濒死时，自动食用生命蛋糕，直至脱离濒死或可用生命蛋糕耗尽。',
    ]
    FEAST = [
        '按己方存活或濒死主体人数分配，人数包含比哈米。两类蛋糕分别分配至个人库存。',
        '仅有 1 人时，比哈米每类获得 3 个；共 2 人时，比哈米每类获得 1 个，队友每类获得 2 个；共 3 人时，比哈米每类获得 1 个，两名队友每类各获得 2 个。',
        '4 人及以上时，每类总量为 min(6，2×人数−1)。先按比哈米 1 个、每名队友 2 个分配；超额部分先削减比哈米份额，再逐个随机削减尚有份额的队友。两类蛋糕分别随机削减。',
    ]
    SPIRIT = [
        '敌方主体分别记录战意，初始为 4 层。首次轮首记录初始层数，此后每轮有 65% 概率减少 1 层，最低为 0 层。战意至多为 4 层时，攻击检定承受 1 颗惩罚骰；至多为 2 层时，改为 2 颗惩罚骰；至多为 1 层时，造成伤害降低 30%；降至 0 层时，另受 4 级沉默，继续留在战场。',
        '敌方召唤物按召唤组共享战意，初始为 5 层。首次轮首记录初始层数，此后每轮有 50% 概率减少 1 层。战意至多为 4 层时，攻击检定承受 1 颗惩罚骰；至多为 2 层时，改为 2 颗惩罚骰；降至 0 层时，整组撤离。更换组内代表保留已有战意。',
        '环花暖生命大于 0、留在战场且处于隔离状态之外时，推进战意削弱并施加主体压制。条件中断时，已有战意层数保留，召唤组已获得的攻击惩罚保留。多个来源共同作用时，每个主体或召唤组每轮处理一次，主体同类压制取较强效果。',
    ]

    def cards(self):
        return [c for c in cd.ALL_CHARACTERS if c['serial'] in ('Y7', 'Y8')]

    def articles(self, card, items=None):
        return encyclopedia(card, cd.SUMMON_TEMPLATES,
                            cd.ITEM_TEMPLATES if items is None else items)['skills']

    def expected(self):
        # Complete default-data text is independent of the rendering helpers.
        return {
            ('Y7', '战意值削弱'): (
                '战斗开始时获得战意光环，逐轮削弱敌方主体与召唤物的战意。主体承受攻击惩罚、伤害降低及沉默；召唤物战意归零时整组撤离。',
                [('时机', '被动'), ('费用', '1 MP'), ('目标', '敌方主体与召唤物')], self.SPIRIT),
            ('Y7', '治愈领域'): (
                '创建随自身移动的治愈领域，半径 10 格，持续 3 回合。范围内的自身、己方主体及召唤物分别回复 2d8 HP 与 1d6 MP。',
                [('时机', '主动'), ('费用', '5 MP'), ('目标', '自身及范围内己方单位')], [
                    '施放时立即回复一次，随后于轮首回复 2 次。各目标、各项回复分别掷骰。轮首先为范围内的濒死单位急救，同一单位在该次轮首获得的同类回复计入本轮回复。',
                    '隔离中的单位及已点燃的召唤物排除在回复对象之外。',
                    '每名施法者同时至多维持 1 个治愈领域。已有领域时，重复施放被拒绝，保留本次费用。',
                ]),
            ('Y7', '返生之花'): (
                '复活 1 个己方阵亡主体或召唤物，生命恢复至上限的 40%，向下取整，最低为 1 HP。每场战斗限用 1 次，施放后冷却 99 回合。',
                [('时机', '主动'), ('费用', '12 MP'), ('目标', '符合死亡轮窗口的己方阵亡单位')], [
                    '目标须有阵亡记录，且施放轮次与阵亡轮次之差至多为 3；阵亡当轮可以复活。每条阵亡记录可复活一次。',
                    '可以指定符合条件的阵亡单位。省略选择时，优先复活候选召唤物的主人；其余情况优先主体，再按阵亡轮次由早到晚选择。',
                    '复活主体时，仅恢复所选主体。复活召唤物时，召唤物归环花暖所有，并受其对应召唤名额限制；首次转归环花暖计入其累计召唤数量。',
                    '优先返回原位置；原位置无法容纳时，尝试施法者附近的空位。复活保留阵亡时记录的魔力、物品、形态及技能次数消耗，行动额度沿用已有记录。',
                    '缺少符合条件的阵亡目标时，施放被拒绝，保留魔力与本场使用次数。召唤物名额及复活位置须满足要求。',
                ]),
            ('Y8', '美味的蛋糕'): (
                '立即制造 1d2+1 个生命蛋糕，放入己方共享制造物池。每个食用后回复 2d8+4 HP，并获得闪避 +10，持续 1 回合。',
                [('时机', '主动 / 附加动作'), ('费用', '3 MP'), ('目标', '己方共享制造物池')], self.CAKE_USE),
            ('Y8', '甜甜的蛋糕'): (
                '立即制造 1d2+1 个魔力蛋糕，放入己方共享制造物池。每个食用后回复 2d8+4 MP，并获得斗殴 +10，持续 1 回合。',
                [('时机', '主动 / 附加动作'), ('费用', '3 MP'), ('目标', '己方共享制造物池')], self.CAKE_USE),
            ('Y8', '品尝蛋糕'): (
                '战斗开始时回复自身 2d8+4 HP。附带品尝回复的生命蛋糕在食用时，额外为食用者回复 2d8+4 HP。',
                [('时机', '被动'), ('费用', '1 MP'), ('目标', '自身；品尝回复作用于食用者')], [
                    '比哈米初始携带 4 个附带品尝回复的生命蛋糕。“最后的款待-恢复”分发的生命蛋糕同样附带此效果。蛋糕基础回复与品尝回复分别掷骰，每个蛋糕触发一次额外回复。',
                ]),
            ('Y8', '最后的款待-恢复'): (
                '立即制造并分发生命蛋糕与魔力蛋糕，两类蛋糕分别放入己方主体的个人库存。',
                [('时机', '主动 / 附加动作'), ('费用', '5 MP'), ('目标', '己方存活或濒死主体')], [
                    '生命蛋糕：食用后回复 2d8+4 HP，另触发一次 2d8+4 HP 的品尝回复，并获得闪避 +10，持续 1 回合。',
                    '魔力蛋糕：食用后回复 2d8+4 MP，并获得斗殴 +10，持续 1 回合。',
                ] + self.FEAST + self.CAKE_USE),
            ('Y8', '轻盈蛋糕'): (
                '立即制造 1 个轻盈蛋糕，放入己方共享制造物池。食用后获得行动力 +5，持续 5 回合。',
                [('时机', '主动 / 附加动作'), ('费用', '4 MP'), ('目标', '己方共享制造物池')], self.CAKE_USE),
            ('Y8', '幸运蛋糕'): (
                '立即制造 1 个幸运蛋糕，放入己方共享制造物池。食用后，所有 D100 检定获得 1 颗奖励骰，持续 5 回合。',
                [('时机', '主动 / 附加动作'), ('费用', '5 MP'), ('目标', '己方共享制造物池')], self.CAKE_USE),
            ('Y8', '最后的款待-进攻'): (
                '立即制造并分发轻盈蛋糕与幸运蛋糕，两类蛋糕分别放入己方主体的个人库存。',
                [('时机', '主动 / 附加动作'), ('费用', '8 MP'), ('目标', '己方存活或濒死主体')], [
                    '轻盈蛋糕：食用后获得行动力 +5，持续 5 回合。',
                    '幸运蛋糕：食用后，所有 D100 检定获得 1 颗奖励骰，持续 5 回合。',
                ] + self.FEAST + self.CAKE_USE),
        }

    def test_copy_and_sections_match_approved_plan_exactly(self):
        expected = self.expected()
        seen = set()
        for card in self.cards():
            for skill in self.articles(card):
                key = card['serial'], skill['name']
                with self.subTest(key=key):
                    seen.add(key)
                    description, inputs, paragraphs = expected[key]
                    self.assertEqual(skill['description'], description)
                    self.assertEqual(skill['facts'], paragraphs)
                    self.assertTrue(skill['reviewed'])
                    self.assertEqual(json.loads(skill['id']), [*key, 0])
                    sections = [dict(id='inputs', title='施放要求', paragraphs=[],
                                     rows=[dict(label=k, value=v) for k, v in inputs],
                                     effects=[], templates=[], ordered=False),
                                dict(id='effects', title='技能效果', paragraphs=paragraphs,
                                     rows=[], effects=[], templates=[], ordered=False)]
                    for version in skill['versions']:
                        self.assertEqual(version['overview'], description)
                        self.assertEqual(version['sections'], sections)
        self.assertEqual(seen, set(expected))
        healer = next(c for c in self.cards() if c['serial'] == 'Y7')
        self.assertEqual(encyclopedia(healer, cd.SUMMON_TEMPLATES, cd.ITEM_TEMPLATES)['passives'], self.SPIRIT)

    def test_stable_identity_reordering_and_no_input_mutation(self):
        for original in self.cards():
            card = copy.deepcopy(original)
            items = copy.deepcopy(cd.ITEM_TEMPLATES)
            before = {s['id']: s for s in self.articles(card, items)}
            self.assertEqual(card, original)
            self.assertEqual(items, cd.ITEM_TEMPLATES)
            card['spells'].reverse()
            self.assertEqual(before, {s['id']: s for s in self.articles(card, items)})
            changed_phase = copy.deepcopy(card['spells'][0])
            changed_phase['phase'] = 42
            card['spells'].append(changed_phase)
            self.assertFalse(self.articles(card)[-1]['reviewed'])
            self.assertNotIn(self.articles(card)[-1]['id'], before)

    def test_live_healer_values_and_shared_passive(self):
        card = copy.deepcopy(next(c for c in self.cards() if c['serial'] == 'Y7'))
        spells = {s['name']: s for s in card['spells']}
        spells['战意值削弱']['effects'][0].update({
            '战意主体初始层数': 7, '战意主体削弱概率': 81, '战意初始层数': 9,
            '战意削弱概率': 33, '战意削弱概率奖励层': 67, '战意奖励骰阈值': 6,
            '战意惩罚1阈值': 5, '战意惩罚2阈值': 3, '战意惩罚骰上限': 1,
            '战意主体减伤阈值': 2, '战意主体减伤百分比': 42, '战意主体沉默阈值': 1,
        })
        spells['治愈领域']['消耗mp'] = 7
        spells['治愈领域']['effects'][0].update({
            '作用半径': 12, '持续回合': 6, '回复hp': '3d9', '回复mp': '2d3',
            '领域轮首结算次数': 4, '领域同时上限': 2,
        })
        spells['返生之花']['消耗mp'] = 9
        spells['返生之花']['cooldown_rounds'] = 31
        spells['返生之花']['effects'][0].update({'复活回复比例': .55, '死亡轮窗口': 5})
        articles = {s['name']: s for s in self.articles(card)}
        spirit = ' '.join(articles['战意值削弱']['facts'])
        for fact in ('初始为 7 层', '81%', '初始为 9 层', '33%', '6 层时为 67%',
                     '至多为 5 层', '至多为 3 层', '改为 1 颗', '伤害降低 42%', '降至 1 层'):
            self.assertIn(fact, spirit)
        self.assertEqual(encyclopedia(card, cd.SUMMON_TEMPLATES, cd.ITEM_TEMPLATES)['passives'],
                         articles['战意值削弱']['facts'])
        zone = articles['治愈领域']
        self.assertEqual(zone['cost'], '7 MP')
        for fact in ('12 格', '6 回合', '3d9 HP', '2d3 MP'):
            self.assertIn(fact, zone['description'])
        self.assertIn('轮首回复 4 次', zone['facts'][0])
        self.assertIn('维持 2 个', zone['facts'][2])
        revive = articles['返生之花']
        self.assertEqual(revive['cost'], '9 MP')
        self.assertIn('55%', revive['description'])
        self.assertIn('冷却 31 回合', revive['description'])
        self.assertIn('至多为 5', revive['facts'][0])

    def test_live_cake_templates_counts_tasting_and_effect_order(self):
        card = copy.deepcopy(next(c for c in self.cards() if c['serial'] == 'Y8'))
        items = copy.deepcopy(cd.ITEM_TEMPLATES)
        spells = {s['name']: s for s in card['spells']}
        items['蛋糕HP'].update({'回复hp': '3d7', '技能加减值': '闪避+14', 'buff持续回合': 3})
        items['蛋糕MP']['回复mp'] = '4d3'
        items['蛋糕轻盈'].update({'技能加减值': 'MOV+8', 'buff持续回合': 7})
        items['蛋糕幸运'].update({'辅助效果值': 'b2', 'buff持续回合': 8})
        spells['美味的蛋糕']['消耗mp'] = 6
        spells['美味的蛋糕']['effects'][0]['制造个数'] = '2d4'
        spells['品尝蛋糕']['effects'][0]['回复hp'] = '1d9'
        card['inventory'][0].update({'count': 7, '消费追加回复hp': '2d5'})
        spells['最后的款待-恢复']['effects'][0]['消费追加回复hp'] = '4d6'
        spells['最后的款待-恢复']['effects'].reverse()
        articles = {s['name']: s for s in self.articles(card, items)}
        hp = articles['美味的蛋糕']
        self.assertEqual(hp['cost'], '6 MP')
        for fact in ('2d4 个', '3d7 HP', '闪避 +14', '3 回合'):
            self.assertIn(fact, hp['description'])
        self.assertIn('4d3 MP', articles['甜甜的蛋糕']['description'])
        self.assertIn('行动力 +8，持续 7 回合', articles['轻盈蛋糕']['description'])
        self.assertIn('2 颗奖励骰，持续 8 回合', articles['幸运蛋糕']['description'])
        taste = articles['品尝蛋糕']
        self.assertIn('自身 1d9 HP', taste['description'])
        self.assertIn('食用者回复 2d5 HP', taste['description'])
        self.assertIn('携带 7 个', taste['facts'][0])
        feast = articles['最后的款待-恢复']
        self.assertIn('魔力蛋糕与生命蛋糕', feast['description'])
        self.assertTrue(feast['facts'][0].startswith('魔力蛋糕：'))
        self.assertIn('另触发一次 4d6 HP', feast['facts'][1])
        self.assertNotIn('品尝回复', hp['description'])

    def test_live_feast_formula_and_overrides(self):
        card = copy.deepcopy(next(c for c in self.cards() if c['serial'] == 'Y8'))
        spell = next(s for s in card['spells'] if s['name'] == '最后的款待-进攻')
        for effect in spell['effects']:
            effect.update({'分发自留': 2, '分发队友每人': 3, '分发给总数上限': 11,
                           '分发给总数公式': {'每人': 3, '加': -2},
                           '分发人数覆盖': {'1': {'分发自留': 4},
                                      '2': {'分发自留': 2, '分发队友每人': 4}}})
        article = next(s for s in self.articles(card) if s['name'] == spell['name'])
        self.assertIn('仅有 1 人时，比哈米每类获得 4 个', article['facts'][3])
        self.assertIn('队友每类获得 4 个', article['facts'][3])
        self.assertIn('min(11，3×人数−2)', article['facts'][4])
        self.assertIn('先按比哈米 2 个、每名队友 3 个', article['facts'][4])

    def test_optional_and_unknown_fields_do_not_break_reviewed_identity(self):
        for original in self.cards():
            card = copy.deepcopy(original)
            for spell in card['spells']:
                spell.pop('消耗mp', None)
                spell['custom_unknown'] = 12345
                spell['effects'][0]['custom_effect'] = 98765
            for article in self.articles(card):
                self.assertEqual(article['cost'], '')
                self.assertNotIn('费用', [r['label'] for r in article['versions'][0]['sections'][0]['rows']])
                self.assertIn('custom_unknown', article['audit']['unverified_fields'])
                self.assertIn('custom_effect', article['audit']['unverified_fields'])
                self.assertNotIn('98765', article['description'])
                self.assertNotIn('12345', article['description'])

    def test_catalog_preserves_raw_y7_y8_and_all_spells(self):
        from tests.test_battle_execution import http_module
        response = http_module().app.test_client().get('/api/ui/catalog')
        self.assertEqual(response.status_code, 200)
        characters = response.get_json()['characters']
        self.assertEqual(sum(len(c['spells']) for c in characters), 106)
        for card in self.cards():
            actual = next(c for c in characters if c['serial'] == card['serial'])
            self.assertEqual(actual['spells'], card['spells'])
            self.assertNotIn('skills', actual)


class Y5Y6StaticEncyclopediaTests(unittest.TestCase):
    def setUp(self):
        self.profiles = resolved_encyclopedia()
        self.registry = patch.object(be, '_SUMMON_TEMPLATES', copy.deepcopy(cd.SUMMON_TEMPLATES))
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def article(self, serial, name):
        return next(s for s in self.profiles[serial]['skills'] if s['name'] == name and s['phase'] == 0)

    def spell(self, card, name):
        return next(s for s in card['spells'] if s['name'] == name)

    def test_compact_copy_preserves_identity_and_separates_costs(self):
        for (serial, name), expected in Y5Y6_APPROVED.items():
            actual = self.article(serial, name)
            self.assertEqual(json.loads(actual['id']), [serial, name, 0])
            self.assertTrue(actual['reviewed'])
            self.assertTrue(actual['description'])
            self.assertEqual(actual['timing'], dict(expected['requirements'])['时机'])
            self.assertNotIn('施放要求', actual['facts'])
            self.assertNotIn('技能效果', actual['facts'])
            self.assertFalse(any(line.startswith('基础费用：') for line in actual['facts']))
        self.assertEqual(sum(len(self.profiles[s]['skills']) for s in ('Y5', 'Y6')), 7)

    def test_static_costs_targets_and_attack_values_match_live_rules(self):
        for card in (cd.XUETIANSHI, cd.XUEREN):
            for spell in card['spells']:
                article = self.article(card['serial'], spell['name'])
                self.assertEqual(article['cost'], f"{spell.get('消耗mp', 0)} MP")
                self.assertEqual(article['timing'], '被动' if spell['timing'] == '1' else '主动')
        flight = self.spell(cd.XUETIANSHI, '飞行')
        self.assertEqual(cd.XUETIANSHI['attrs']['飞行'], 1)
        self.assertEqual(flight['effects'][0]['技能加减值'], '行动力+5')
        self.assertEqual(flight['effects'][1]['辅助效果值'], 'b')
        self.assertEqual(flight['default_persist'], 1)
        blood = self.spell(cd.XUETIANSHI, '吸血')['effects'][0]
        article = self.article('Y5', '吸血')
        self.assertIn(f"造成 {blood['伤害骰']} 伤害", article['description'])
        self.assertIn(f"伤害的 {float(blood['吸血比例'])*100:g}%", article['description'])
        self.assertIn(f"射程：基础 {blood['射程']} 格；当前全局射程覆盖启用时为 99 格", article['facts'])
        self.assertEqual((blood['客体'], blood['可闪避性'], blood['可反击性'], blood.get('可格挡性', 1)), (4, 0, 1, 1))
        balls = self.spell(cd.XUEREN, '发送雪球')['effects']
        self.assertEqual(len(balls), 2)
        for effect in balls:
            article = self.article('Y6', '发送雪球')
            self.assertIn(f"以 {effect['成功率']} 的技能值", article['description'])
            self.assertIn(effect['伤害骰'], article['description'])
            self.assertIn('依次造成', article['description'])
            self.assertIn(f"射程：{effect['射程']} 格", article['facts'])
            self.assertEqual((effect['客体'], effect['可闪避性'], effect['可反击性'], effect.get('可格挡性', 1)), (4, 1, 0, 1))

    def test_static_zones_and_staff_match_live_rules(self):
        field = self.spell(cd.XUEREN, '制造雪带')['effects'][0]
        article = self.article('Y6', '制造雪带')
        for label, value in [('作用半径', f"{field['作用半径']} 格"),
                             ('持续时间', f"{field['持续回合']} 轮"),
                             ('同时存在上限', f"每名施法者 {field['领域同时上限']} 处")]:
            self.assertIn(f'{label}：{value}', article['facts'])
        self.assertIn(f"增加 {field['冰冻每轮层数']} 层冰冻", article['description'])
        self.assertEqual((field['领域中心跟随'], field['客体']), (0, 45))
        curtain = self.spell(cd.XUEREN, '雪幕')['effects'][0]
        article = self.article('Y6', '雪幕')
        self.assertIn(f"半径 {curtain['作用半径']} 格", article['description'])
        self.assertIn(f"降低 {abs(int(curtain['辅助效果值']))}%", article['description'])
        self.assertEqual((curtain['来源存活绑定'], curtain['领域中心跟随'], curtain['客体']), (1, 1, 13))
        staff = self.spell(cd.XUEREN, '雪之法杖')
        buff, shield = staff['effects']
        article = self.article('Y6', '雪之法杖')
        self.assertIn(f"附加 {buff['辅助效果值'].lstrip('+')} 攻击伤害骰", article['description'])
        self.assertIn(f"持续 {buff['持续回合']} 轮", article['description'])
        self.assertIn(f"授予 {shield['护盾值']} 点护盾", article['description'])
        self.assertEqual((buff['客体'], shield['客体']), (13, 13))
        self.assertEqual(buff['默认延续性'], 0)
        self.assertEqual(shield.get('默认延续性', staff['default_persist']), 1)

    def test_static_summon_values_match_live_template(self):
        spell = self.spell(cd.XUEREN, '堆雪人')
        effect = spell['effects'][0]
        template = cd.SUMMON_TEMPLATES[effect['召唤物模板']]
        article = self.article('Y6', '堆雪人')
        for label, value in [('技能冷却', f"{spell['cooldown_rounds']} 轮"),
                             ('召唤数量', f"{effect['召唤个数']} 只"),
                             ('持续时间', f"{effect['持续回合']} 轮"),
                             ('同时存活上限', f"{template['max_simultaneous']} 只")]:
            self.assertIn(f'{label}：{value}', article['facts'])
        for key, label in [('HP', '生命'), ('MP', '初始魔力'), ('MOV', '基础行动力'), ('DEX', '敏捷'), ('闪避', '闪避')]:
            self.assertIn(f'{label}：{template[key]}', article['facts'])
        self.assertEqual((template['可反应'], template['可反击']), (1, 0))
        zone, ball = template['frost_zone'], template['frost_ball']
        self.assertIn(f"小雪带：消耗 {zone['mp_cost']} MP；半径 {zone['radius']} 格；持续 {zone['duration']} 轮；每轮增加 {zone['stacks']} 层冰冻", article['facts'])
        self.assertIn(f"雪球：消耗 {ball['mp_cost']} MP；技能值 {ball['success']}；伤害 {ball['dice']}；射程 {ball['range']} 格；可闪避、格挡；命中增加 {cd.XUEREN['attrs']['冰冻命中层数']} 层冰冻", article['facts'])

    def test_static_frost_numbers_match_live_passive_rules(self):
        a, r = cd.XUEREN['attrs'], cd.XUEREN['passive_rules']['frost']
        copy = '\n'.join(self.profiles['Y6']['passives'])
        self.assertIn(f"命中增加 {a['冰冻命中层数']} 层", copy)
        self.assertIn(f"自身每枚雪球命中增加 {a['冰冻雪球命中层数']} 层", copy)
        self.assertIn(f"每轮先衰减 {r['decay']} 层", copy)
        for value in (f"提高 {r['vulnerability_pct']}%", f"技能值 −{r['skill_penalty']}",
                      f"伤害降低 {r['dodge_penalty']}%", f"行动力 −{r['move_penalty']}",
                      f"每轮受到 {r['dot']}", f"受到 {r['burst']} 爆发伤害",
                      f"回复 {r['instant_heal']} HP", f"回复 {r['regen_hp']} HP、{r['regen_mp']} MP"):
            self.assertIn(value, copy)
        self.assertIn(f"第 {a['冰冻即时回复层数']} 层：", copy)
        self.assertEqual(a['冰冻跨循环叠加'], 1)
        self.assertNotIn('闪避−20', copy)
        self.assertNotIn('追加2层', copy)

    def engine(self, cls, vampire_mp=None):
        eng = cls()
        for uid, card in [('vampire', cd.XUETIANSHI), ('snow', cd.XUEREN), ('enemy', cd.YANYAN)]:
            data = copy.deepcopy(card)
            data['inventory'] = []  # Prevent automatic cake rescue in zero-HP cases.
            cd.load_character_to_engine(eng, data, uid)
            eng.process_command(uid, '.hs')
            eng.get_char(uid).set_attr('行动力', card['attrs']['行动力'])
        eng.get_char('vampire').set_attr('魔力', cd.XUETIANSHI['attrs']['魔力'] if vampire_mp is None else vampire_mp)
        eng.setup_battle(['vampire', 'snow'], ['enemy'], '12x12', season_status=50)
        return eng

    def test_flight_cost_bonus_persistence_and_innate_flight(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                self.assertEqual(eng.get_char('vampire').get_attr('魔力'), cd.XUETIANSHI['attrs']['魔力'] - 2)
                self.assertEqual(eng._get_effective_mov('vampire'), cd.XUETIANSHI['attrs']['行动力'] + 8 + 5)
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('vampire')), 'b')
                effects = eng._get_effects()
                for effect in effects:
                    if effect.get('spellName') == '飞行':
                        effect['remainingRounds'] = 1
                eng._set_effects(effects)
                eng._tick_down_effects()
                self.assertEqual(eng._get_effective_mov('vampire'), 21)
                self.assertEqual(be._calc_net_bp(eng._get_active_buffs('vampire')), 'b')
                poor = self.engine(cls, vampire_mp=1)
                self.assertEqual(poor.get_char('vampire').get_attr('魔力'), 1)
                self.assertTrue(poor._is_flying('vampire'))
                self.assertEqual(poor._get_effective_mov('vampire'), cd.XUETIANSHI['attrs']['行动力'] + 8)
                self.assertEqual(be._calc_net_bp(poor._get_active_buffs('vampire')), '')

    def test_lifesteal_uses_pre_shield_damage_only_when_damage_remains(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for capacity, hp, expected_hp in ((0, 1, 9), (3, 1, 9), (8, 1, 1), (3, 8, 10), (0, 0, 0)):
                with self.subTest(engine=cls.__name__, shield=capacity, hp=hp):
                    eng = self.engine(cls)
                    eng._set_effects([])
                    eng.get_char('vampire').set_attr('体力上限', 10)
                    eng.get_char('enemy').set_attr('体力上限', 100)
                    eng._set_combat_hp('vampire', hp)
                    eng._set_combat_hp('enemy', 100)
                    if capacity:
                        eng._set_effects([dict(type='shield', value=capacity, remainingRounds=5,
                                              targetUserId='enemy', spellName='test')])
                    eng._apply_spell_damage('vampire', 'enemy', '8', 0, 0,
                                            lifesteal_ratio=1.0, spell_name='吸血')
                    self.assertEqual(eng._hp_safe('enemy'), 100 - max(0, 8 - capacity))
                    self.assertEqual(eng._hp_safe('vampire'), expected_hp)

    def test_frost_third_seventh_and_eighth_layers_match_explained_effects(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                eng._set_effects([])
                base_mov = eng._get_effective_mov('enemy')
                eng._add_frost('snow', 'enemy', 3)
                frost = next(e for e in eng._get_effects() if e['type'] == 'frost')
                self.assertEqual(eng._frost_components(frost), (1, 1, 1))
                self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'vampire'), .8)
                self.assertEqual(eng._apply_buff_skill_mod('enemy', 60, '闪避'), 50)
                self.assertEqual(eng._get_effective_mov('enemy'), max(0, base_mov - 5))
                eng._add_frost('snow', 'enemy', 4)
                self.assertEqual(eng._frost_components(frost), (2, 2, 1))
                self.assertEqual(eng._apply_buff_skill_mod('enemy', 60, '闪避'), 40)
                eng._add_frost('snow', 'enemy', 1)
                self.assertEqual(eng._frost_components(frost), (0, 1, 1))
                eng._add_frost('snow', 'enemy', 8)
                self.assertEqual(eng._frost_components(frost), (0, 2, 2))
                self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'vampire'), .6)

    def test_catalog_preserves_raw_y5_y6_and_static_skill_identities(self):
        from tests.test_battle_execution import http_module
        response = http_module().app.test_client().get('/api/ui/catalog')
        self.assertEqual(response.status_code, 200)
        characters = response.get_json()['characters']
        self.assertEqual(sum(len(c['spells']) for c in characters), 106)
        for card in (cd.XUETIANSHI, cd.XUEREN):
            actual = next(c for c in characters if c['serial'] == card['serial'])
            self.assertEqual(actual['spells'], card['spells'])
            self.assertNotIn('skills', actual)
            expected = {(s['name'], s.get('phase', 0)) for s in actual['spells']}
            articles = self.profiles[card['serial']]['skills']
            self.assertEqual(expected, {(s['name'], s['phase']) for s in articles})
            self.assertEqual(len(expected), len(articles))

    def test_frost_regen_stacks_across_targets_and_cycles_after_frost_clears(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                eng._set_effects([])
                enemy_summon = eng._create_summon('enemy', '使魔')
                self.assertIsNotNone(enemy_summon)
                eng.get_char('snow').set_attr('体力上限', 1000)
                eng._set_combat_hp('snow', 100)
                eng.get_char('snow').set_attr('魔力', 0)
                with patch.object(be, 'roll_dice', return_value=2):
                    eng._add_frost('snow', 'enemy', 14)
                    eng._add_frost('snow', enemy_summon, 6)
                    self.assertEqual(eng._hp_safe('snow'), 106)
                    regen = next(e for e in eng._get_effects() if e['type'] == 'frostRegen')
                    self.assertEqual(len(regen['activations']), 3)
                    for expected_hp, expected_mp in ((112, 6), (118, 12)):
                        effects = eng._get_effects()
                        for effect in effects:
                            if effect['type'] == 'frost':
                                effect['stacks'] = 1
                        eng._set_effects(effects)
                        state = eng._get_state()
                        state['round'] += 1
                        eng._set_state(state)
                        eng._frost_round_start()
                        self.assertFalse(any(e['type'] == 'frost' for e in eng._get_effects()))
                        self.assertEqual(eng._hp_safe('snow'), expected_hp)
                        self.assertEqual(eng.get_char('snow').get_attr('魔力'), expected_mp)

    def test_snow_curtain_follows_source_pauses_for_isolation_and_ends_at_zero_hp(self):
        from tests.test_y6_frost import place
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = self.engine(cls)
                zone = next(e for e in eng._get_effects() if e.get('spellName') == '雪幕')
                eng._set_effects([zone])
                place(eng, {'snow': 'D4', 'vampire': 'E4', 'enemy': 'L12'})
                self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'vampire'), .9)
                place(eng, {'snow': 'L1', 'vampire': 'E4', 'enemy': 'L12'})
                self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'vampire'), 1)
                place(eng, {'snow': 'D4', 'vampire': 'E4', 'enemy': 'L12'})
                isolation = dict(type='buff', auxType='隔离', remainingRounds=2, targetUserId='snow')
                eng._set_effects([zone, isolation])
                self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'vampire'), 1)
                eng._set_effects([zone])
                self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'vampire'), .9)
                eng._set_combat_hp('snow', 0)
                self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'vampire'), 1)


if __name__ == '__main__':
    unittest.main()
