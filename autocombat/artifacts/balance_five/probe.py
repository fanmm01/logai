"""Audit actual AI action choice, without changing its policy or weights."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import json
import logging
import random
from collections import Counter
import ai_battle as ab
from combat_settings import settings_scope, snapshot

ab.logger.handlers[:] = [logging.NullHandler()]
ab.logger.setLevel(50)
counts = Counter()
old = ab.BattleEngine._execute_ai_command

def command(engine, uid, cmd):
    c = engine.get_char(uid)
    if getattr(c, 'serial', '') in ('Y6', 'Y9', 'Y11'):
        counts[c.serial + ':' + cmd.split('@')[0].strip()] += 1
    return old(engine, uid, cmd)

ab.BattleEngine._execute_ai_command = command
config = snapshot()
for k in config:
    if k.startswith('DELAY_'):
        config[k] = 0
with settings_scope(config):
    t = ab.Tournament(tournament_type=0, enabled_modes=['1v1'], settings=config)
    t.init_characters()
    for s in ('Y6', 'Y9', 'Y11'):
        for i in range(1, 18):
            if i in (2, int(s[1:])):
                continue
            random.seed(2026119000+i)
            t.battle([s], [f'Y{i}'], '10x10', max_rounds=20, delay=0)
print(json.dumps(dict(counts), ensure_ascii=True, indent=2))
