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

ROOT = Path(__file__).resolve().parents[1]


class EncyclopediaTests(unittest.TestCase):
    def test_every_skill_has_correctly_matched_article_and_unique_asset(self):
        assets = set()
        self.assertEqual(len(PROFILES), len(cd.ALL_CHARACTERS))
        self.assertEqual(len({p[0] for p in PROFILES.values()}), len(PROFILES))
        for c in cd.ALL_CHARACTERS:
            with self.subTest(serial=c['serial']):
                self.assertEqual(ARTICLE_ORDER[c['serial']], [(s['name'], s.get('phase', 0)) for s in c['spells']])
                self.assertEqual(len(ARTICLES[c['serial']]), len(c['spells']))
                article = encyclopedia(c, cd.SUMMON_TEMPLATES, cd.ITEM_TEMPLATES)
                self.assertEqual(len(article['traits']), 2)
                for skill in article['skills']:
                    self.assertTrue(skill['description'])
                    self.assertTrue(skill['facts'])
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

    def test_catalog_exposes_articles_and_svg_mime_type(self):
        from tests.test_battle_execution import http_module
        client = http_module().app.test_client()
        response = client.get('/api/ui/catalog')
        self.assertEqual(response.status_code, 200)
        chars = response.get_json()['characters']
        self.assertEqual(sum(len(c['skills']) for c in chars), 106)
        for c in chars:
            self.assertEqual(len(c['traits']), 2)
        icon = client.get(chars[0]['skills'][0]['icon'])
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.mimetype, 'image/svg+xml')
        icon.close()


if __name__ == '__main__':
    unittest.main()
