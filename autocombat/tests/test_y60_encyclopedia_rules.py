"""Documented legacy snow-field timing through real round advancement."""
import logging.handlers
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd


class LegacySnowEncyclopediaTests(unittest.TestCase):
    def test_field_damages_on_two_round_starts_and_expires_before_the_third(self):
        for engine_type in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=engine_type.__name__):
                engine = engine_type()
                for uid, serial in [('snow', 'Y60'), ('target', 'Y17')]:
                    card = next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial)
                    cd.load_character_to_engine(engine, card, uid)
                    engine.process_command(uid, '.hs')
                engine.setup_battle(['snow'], ['target'], '14x14', season_status=50)
                engine._set_effects([])
                entries = engine._get_initiative()
                for entry in entries:
                    entry['coord'] = 'B7' if entry['userId'] == 'snow' else 'D7'
                engine._set_initiative(entries)
                board = engine._get_map()
                board['occupants'] = {'B7': 'snow', 'D7': 'target'}
                engine._set_map(board)
                engine._set_combat_hp('target', 1000)
                spell = next(s for s in engine.load_spells('snow') if s['name'] == '制造雪带')
                with patch.object(engine, '_roll_spell_success_bp', return_value=(60, '')):
                    result = engine._execute_spell('snow', 'target', spell)
                self.assertTrue(be.action_committed(result), str(result))
                initial_hp = engine._get_combat_hp('target')
                initial_mp = engine.get_char('snow').get_attr('魔力')
                rejected = engine._execute_spell('snow', 'target', spell)
                self.assertFalse(be.action_committed(rejected))
                self.assertEqual(engine.get_char('snow').get_attr('魔力'), initial_mp)

                # Use the actual turn wrapper, which decrements lifetime before
                # settling ordinary zone damage at each new round.
                losses = []
                remaining = []
                with patch.object(be, 'roll_dice', return_value=3):
                    for _ in range(3):
                        entries = engine._get_initiative()
                        state = engine._get_state()
                        state['activeIndex'] = len(entries) - 1
                        engine._set_state(state)
                        before = engine._get_combat_hp('target')
                        engine._advance_turn(entries[-1]['userId'])
                        losses.append(before - engine._get_combat_hp('target'))
                        zones = [e for e in engine._get_effects()
                                 if e.get('spellName') == '制造雪带' and e.get('type') == 'zone']
                        remaining.append(zones[0]['remainingRounds'] if zones else None)
                self.assertEqual(losses, [3, 3, 0])
                self.assertEqual(remaining, [2, 1, None])
                self.assertEqual(engine._get_combat_hp('target'), initial_hp - 6)


if __name__ == '__main__':
    unittest.main()
