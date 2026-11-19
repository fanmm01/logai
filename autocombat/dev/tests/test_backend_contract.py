"""Read-only generation gate, deploy import contract and scoped data checks."""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import characters_data as cd
import merge_backend as merge


ROOT = (Path(__file__).resolve().parents[2] / 'backend')


class BackendContractTests(unittest.TestCase):
    def test_check_detects_drift_and_never_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'autocombat_backend.py'
            output.write_text('stale', encoding='utf-8')
            before = (output.read_bytes(), output.stat().st_mtime_ns)
            with patch.object(merge, 'BASE', folder), \
                    patch.object(merge, 'render_backend', return_value='current'), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(merge.main(['--check']), 1)
                self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
                output.write_text('current', encoding='utf-8')
                stamp = output.stat().st_mtime_ns
                self.assertEqual(merge.main(['--check']), 0)
                self.assertEqual(output.stat().st_mtime_ns, stamp)

    def test_generated_sections_are_unique_and_ordered(self):
        source = merge.render_backend()
        last = -1
        for filename in merge.FILES_IN_ORDER:
            begin = f'# BEGIN INLINED: {filename}\n'
            end = f'# END INLINED: {filename}\n'
            self.assertEqual(source.count(begin), 1)
            self.assertEqual(source.count(end), 1)
            pos = source.index(begin)
            self.assertGreater(pos, last)
            last = source.index(end)
        compile(source, 'autocombat_backend.py', 'exec')

    def test_integrated_import_api_and_policy_adapter(self):
        # Import in a child process: the generated deployment installs module aliases.
        result = subprocess.run([sys.executable, '-c', '''
import autocombat_backend as backend
import ai_trainer_pvp as adapter
from unittest.mock import patch
assert callable(adapter.encode_state)
assert callable(adapter.execute_summon_action)
assert backend.app.test_client().get('/api/characters').status_code == 200
assert backend.FullBattleEngine()._get_effects() == []
engine = backend.FullBattleEngine()
for uid, serial in [('wood', 'Y9'), ('enemy', 'Y1')]:
    card = next(c for c in backend.ALL_CHARACTERS if c['serial'] == serial)
    backend.load_character_to_engine(engine, card, uid)
    engine.process_command(uid, '.hs')
engine.setup_battle(['wood'], ['enemy'], '12x12', season_status=50)
actor = engine.get_char('wood')
mp = actor.get_attr('魔力')
actor.set_attr('理智', 1)
engine._set_combat_hp('wood', 1)
with patch.object(backend.random, 'randint', side_effect=[99, 99, 99, 100]), patch.object(backend, 'roll_dice', return_value=12):
    engine._check_hp_triggers()
assert actor.phase == 2
assert actor.get_attr('魔力') == mp - 1
assert actor.get_attr('理智') == 0
assert len(next(e for e in engine._get_effects() if e.get('type') == 'hpTrigger')['attemptedThresholds']) == 4
'''], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_y1_y10_stable_identity_phase_and_summon_references(self):
        seen = set()
        for card in cd.ALL_CHARACTERS:
            if card['serial'] not in {f'Y{i}' for i in range(1, 11)}:
                continue
            self.assertNotIn(card['serial'], seen)
            seen.add(card['serial'])
            skills = set()
            for spell in card['spells']:
                identity = (spell['name'], spell.get('phase', 0))
                self.assertNotIn(identity, skills)
                skills.add(identity)
                self.assertIsInstance(spell.get('phase', 0), int)
                for effect in spell.get('effects', []):
                    template = effect.get('召唤物模板')
                    if template:
                        self.assertIn(template, cd.SUMMON_TEMPLATES)
                    if effect.get('type') == 9:
                        ratios = [t['ratio'] for t in effect['hp_thresholds']]
                        self.assertEqual(len(ratios), len(set(ratios)))
                        self.assertTrue(all(0 < r < 1 for r in ratios))
                        self.assertTrue(all(0 <= t['rate'] <= 100 for t in effect['hp_thresholds']))
        self.assertEqual(seen, {f'Y{i}' for i in range(1, 11)})

    def test_http_ai_missing_adapter_reports_explicit_fallback(self):
        import builtins
        import battle_http_server as srv
        from tests.test_y9_rules import setup
        engine = setup()
        original = builtins.__import__

        def without_adapter(name, *args, **kwargs):
            if name == 'ai_trainer_pvp':
                raise ModuleNotFoundError(name)
            return original(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=without_adapter), \
                self.assertLogs(srv.__name__, level='ERROR') as logs:
            command, action = srv._ai_decide_action(engine, 'wood', {'solo': {}, 'team': {}})
        self.assertIn('explicitly falling back', '\n'.join(logs.output))
        self.assertTrue(command.startswith('.'))
        self.assertIsNone(action)


if __name__ == '__main__':
    unittest.main()
