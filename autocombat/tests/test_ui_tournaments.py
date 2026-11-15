"""Exercise tournament creation before any standalone AI modules are loaded."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TournamentStartupTests(unittest.TestCase):
    def test_cold_backend_creates_all_formats_and_retires_fast_option(self):
        # Importing test_ui first loads ai_trainer and conceals missing exports
        # in the integrated backend's synthetic battle_engine module.
        for backend in ('battle_http_server', 'autocombat_backend'):
            with self.subTest(backend=backend):
                script = r'''
import importlib
import logging
import logging.handlers
from pathlib import Path
import tempfile
from unittest.mock import patch

with patch.object(logging.handlers, 'RotatingFileHandler', return_value=logging.NullHandler()):
    srv = importlib.import_module(BACKEND)
from ui_store import Store
from combat_settings import snapshot

ext = srv.app.extensions['combat_ui']
client = srv.app.test_client()
assert client.post('/api/ui/session', json={'name': 'regression'}).status_code == 200
service = ext['tournaments']
with tempfile.TemporaryDirectory() as tmp:
    service.store = Store(Path(tmp) / 'test.sqlite3')
    with patch.object(service.management, 'snapshot_task'):
        for fmt in ('1v1', '2v2', '3v3'):
            response = client.post('/api/ui/tournaments', json={'format': fmt, 'seed': 77})
            assert response.status_code == 200, response.get_data(as_text=True)
            t = response.get_json()
            assert t['format'] == fmt and t['status'] == 'paused'
            assert len(t['teams']) >= 4 and t['schedule']
            assert all(0 <= m['a'] < len(t['teams']) and 0 <= m['b'] < len(t['teams'])
                       and m['a'] != m['b'] for m in t['schedule'])
            cfg = snapshot()
            if fmt != '1v1':
                count = len(t['teams'])
                expected = (count * (count - 1) // 2 * cfg['RR_ROUNDS_' + fmt.upper()]
                            * len(cfg['MAP_SIZES_' + fmt.upper()]) + cfg['RANDOM_BONUS_' + fmt.upper()])
                assert len(t['schedule']) == expected
            repeat = client.post('/api/ui/tournaments', json={'format': fmt, 'seed': 77}).get_json()
            assert repeat['schedule'] == t['schedule']
            response = client.post('/api/ui/tournaments/' + t['id'] + '/advance',
                json={'op': 'fast', 'fast': True, 'revision': t['revision'], 'request_id': 'old-page'})
            assert response.status_code == 400
            assert not service.store.get('tournament', t['id']).get('fast')
            t['fast'] = True
            service.store.put('tournament', t)
        # Previously saved tournaments must not retain the retired setting.
        from flask import Blueprint
        from ui_tournaments import Tournaments
        Tournaments(Blueprint('restart_test', __name__), service.c, service.management, service.store)
        assert all('fast' not in t for t in service.store.list('tournament'))

params = ext['tasks'].params({'mode': 'verbose', 'team_a': ['Y1'], 'team_b': ['Y2'], 'fast': True})
assert params['fast'] is False
'''
                result = subprocess.run(
                    [sys.executable, '-c', 'BACKEND = ' + repr(backend) + '\n' + script],
                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60,
                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
