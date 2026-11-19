"""Every character retains skill identity through editing and engine reloads."""
import ast
import copy
from pathlib import Path
import unittest

import ai_trainer as trainer
import battle_engine as be
import characters_data as cd
from skill_identity import identify_skills, validate_global_skill_ids
from ui_effects import EffectComments, runtime_character, format_character


def definitions():
    source = Path(cd.__file__).read_text(encoding='utf-8-sig')
    comments = EffectComments(source)
    result = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        if {'name', 'serial', 'attrs'} <= keys:
            data = comments.restore(ast.literal_eval(node.value), node)
            for spell in data.get('spells', []):
                spell['_disabled'] = False
            result.append(data)
    return result


class SkillIdentityTests(unittest.TestCase):
    def test_all_source_skills_have_unique_persisted_identity(self):
        chars = definitions()
        validate_global_skill_ids(chars)
        for char in chars:
            for spell in char['spells']:
                self.assertTrue(spell.get('skill_id'))
                self.assertIn(spell['skill_index'], range(1, 31))

    def test_disable_reorder_restore_and_reload_preserve_every_character(self):
        for data in definitions():
            if not data['spells']:
                continue
            expected = {s['skill_id']: s['skill_index'] for s in data['spells']}
            for cls in (be.FullBattleEngine, be.FastBattleEngine):
                for disabled in range(len(data['spells'])):
                    with self.subTest(serial=data['serial'], engine=cls.__name__, disabled=disabled + 1):
                        changed = copy.deepcopy(data)
                        removed = changed['spells'][disabled]
                        removed['_disabled'] = True
                        changed['spells'].reverse()
                        engine = cls()
                        cd.load_character_to_engine(engine, runtime_character(changed), 'actor')
                        # Check all phases, including unavailable weapon forms.
                        char = engine.get_char('actor')
                        observed = {}
                        for phase in {1, *(s.get('phase', 0) for s in data['spells'])}:
                            char.phase = phase
                            for weapon in ('', 'staff', 'sword', 'bow'):
                                char.set_str('_weapon_name', weapon)
                                observed.update({s['skill_id']: s['index'] for s in engine.load_spells('actor')})
                        self.assertNotIn(removed['skill_id'], observed)
                        self.assertEqual(observed, {k: v for k, v in expected.items() if k != removed['skill_id']})
                        # Re-enable on the same engine: no stale names or slots.
                        removed['_disabled'] = False
                        cd.load_character_to_engine(engine, runtime_character(changed), 'actor')
                        char.phase = removed.get('phase', 0) or 1
                        char.set_str('_weapon_name', removed.get('weapon_required', 'staff'))
                        spell = next(s for s in engine.load_spells('actor') if s['skill_id'] == removed['skill_id'])
                        self.assertEqual(spell['index'], expected[removed['skill_id']])

    def test_disabling_each_skill_still_allows_battle_setup_and_ai(self):
        trainer._ACTION_CACHE.clear()
        enemy = next(c for c in definitions() if c['serial'] == 'Y3')
        for data in definitions():
            variants = [i for i in range(len(data['spells']))] + [None]
            for disabled in variants:
                for cls in (be.FullBattleEngine, be.FastBattleEngine):
                    with self.subTest(serial=data['serial'], disabled=disabled, engine=cls.__name__):
                        changed = copy.deepcopy(data)
                        for i, spell in enumerate(changed['spells']):
                            spell['_disabled'] = disabled is None or i == disabled
                        engine = cls()
                        for uid, definition in (('actor', changed), ('enemy', enemy)):
                            cd.load_character_to_engine(engine, runtime_character(definition), uid)
                            engine.process_command(uid, '.hs')
                        engine.setup_battle(['actor'], ['enemy'], '14x14', season_status=50)
                        initiative = engine._get_initiative()
                        for entry in initiative:
                            entry['coord'] = 'B7' if entry['userId'] == 'actor' else 'D7'
                        engine._set_initiative(initiative)
                        battle_map = engine._get_map()
                        battle_map['occupants'] = {'B7': 'actor', 'D7': 'enemy'}
                        engine._set_map(battle_map)
                        for phase in {getattr(engine.get_char('actor'), 'phase', 1), *(s.get('phase', 0) for s in data['spells'] if s.get('phase'))}:
                            engine.get_char('actor').phase = phase
                            engine.load_spells('actor')
                            actions = trainer.get_available_actions(engine, 'actor')
                            self.assertTrue(trainer.affordable_actions(engine, 'actor', actions))

    def test_ai_cache_adds_reenabled_skills_and_updates_labels(self):
        trainer._ACTION_CACHE.clear()
        data = next(c for c in definitions() if c['serial'] == 'Y1')
        data['spells'][1]['_disabled'] = True
        engine = be.FullBattleEngine()
        cd.load_character_to_engine(engine, runtime_character(data), 'actor')
        engine.get_char('actor').set_attr('魔力', 100)
        before = trainer.get_available_actions(engine, 'actor')
        slot = data['spells'][1]['skill_index']
        self.assertFalse(any(key.startswith(f'SKILL_{slot}__') for key, _ in before))
        data['spells'][1]['_disabled'] = False
        data['spells'][1]['name'] = 'Renamed skill'
        cd.load_character_to_engine(engine, runtime_character(data), 'actor')
        engine.get_char('actor').set_attr('魔力', 100)
        after = trainer.get_available_actions(engine, 'actor')
        self.assertTrue(any(key.startswith(f'SKILL_{slot}__') and label.startswith('Renamed skill') for key, label in after))

    def test_round_trip_comments_keep_ids_and_slots(self):
        for data in definitions():
            for spell in data['spells'][::2]:
                spell['_disabled'] = True
            source = format_character('CHARACTER', data)
            node = ast.parse(source).body[0]
            restored = EffectComments(source).restore(ast.literal_eval(node.value), node)
            self.assertEqual([(s['skill_id'], s['skill_index'], s.get('_disabled', False)) for s in restored['spells']],
                             [(s['skill_id'], s['skill_index'], s.get('_disabled', False)) for s in data['spells']])

    def test_new_skills_never_reuse_deleted_or_disabled_slots(self):
        original = identify_skills(dict(serial='CUSTOM', spells=[{'name': 'one'}, {'name': 'two'}]))
        changed = copy.deepcopy(original)
        changed['spells'].pop()
        changed = identify_skills(changed, original)
        changed['spells'].append({'name': 'new', '_disabled': True})
        added = identify_skills(changed, original)
        self.assertEqual(added['spells'][-1]['skill_index'], 3)
        self.assertNotEqual(added['spells'][-1]['skill_id'], original['spells'][-1]['skill_id'])

    def test_duplicate_or_changed_identity_is_rejected(self):
        original = identify_skills(dict(serial='CUSTOM', spells=[{'name': 'one'}, {'name': 'two'}]))
        for field, value in (('skill_id', original['spells'][0]['skill_id']), ('skill_index', 1),
                             ('skill_id', 'different'), ('skill_index', 3), ('skill_index', True)):
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(original)
                changed['spells'][1][field] = value
                changed['spells'][1]['_disabled'] = True
                with self.assertRaises(ValueError):
                    identify_skills(changed, original)


if __name__ == '__main__':
    unittest.main()
