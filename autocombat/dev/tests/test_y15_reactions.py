"""Y15-01/02/03/02a/03a: HP loss is the single reaction commit point."""
import copy
import unittest
from unittest.mock import patch
import battle_engine as be
from tests.test_y12_summons import setup


class BlastReactionTests(unittest.TestCase):
    def test_attempt_and_full_shield_do_not_commit(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y15')
            before = en.get_char('actor').get_attr('魔力')
            en._received_attack_reactions('enemy', 'actor')
            self.assertEqual(en.get_char('actor').get_attr('魔力'), before)
            en._set_effects(en._get_effects() + [dict(type='shield', targetUserId='actor', sourceUserId='actor', value=100, remainingRounds=3)])
            dmg, _, _ = en._absorb_damage_with_shield('actor', 5)
            en._set_combat_hp('actor', en._get_combat_hp('actor') - dmg, source_dmg=dmg, damage_source='enemy')
            self.assertEqual(en.get_char('actor').get_attr('魔力'), before)
            self.assertNotIn('爆破圈', getattr(en.get_char('actor'), '_once_consumed', set()))

    def test_any_hp_loss_commits_once_even_without_damage_source(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for source in ('enemy', None):
                en = setup(cls, 'Y15')
                before = en.get_char('actor').get_attr('魔力')
                with patch.object(en, '_roll_spell_success_bp', return_value=(50, '')):
                    en._set_combat_hp('actor', en._get_combat_hp('actor') - 1, damage_source=source)
                    self.assertEqual(en.get_char('actor').get_attr('魔力'), before - 1)
                    self.assertIn('爆破圈', en.get_char('actor')._once_consumed)
                    en._set_combat_hp('actor', en._get_combat_hp('actor') - 1, damage_source=source)
                    self.assertEqual(en.get_char('actor').get_attr('魔力'), before - 1)

    def test_no_mana_preserves_once_and_retry_after_resource_recovery(self):
        en = setup(serial='Y15')
        en.get_char('actor').set_attr('魔力', 0)
        en._set_combat_hp('actor', en._get_combat_hp('actor') - 1, damage_source='enemy')
        self.assertNotIn('爆破圈', getattr(en.get_char('actor'), '_once_consumed', set()))
        en.get_char('actor').set_attr('魔力', 3)
        en._set_combat_hp('actor', en._get_combat_hp('actor') - 1, damage_source='enemy')
        self.assertIn('爆破圈', en.get_char('actor')._once_consumed)


if __name__ == '__main__':
    unittest.main()
