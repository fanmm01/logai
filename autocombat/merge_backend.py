#!/usr/bin/env python3
"""Temporary merge script to build autocombat_backend.py from source files."""

import re, os

BASE = os.path.dirname(os.path.abspath(__file__))  # = autocombat/

FILES_IN_ORDER = [
    'characters_data_pvp.py',
    'battle_engine.py',
    'ai_trainer.py',
    'team_tables.py',
    'ai_battle.py',
    'battle_http_server.py',
]

INTERNAL_MODULES = {
    'battle_engine', 'characters_data', 'characters_data_pvp',
    'ai_trainer', 'ai_trainer_pvp', 'ai_battle', 'team_tables',
}

def is_internal_import(line):
    if line[0:1] in (' ', '\t'):
        return False
    s = line.strip()
    for mod in INTERNAL_MODULES:
        if re.match(rf'^from\s+{mod}\s+import', s):
            return True
    return False

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    result = []
    in_multiline = False
    for line in lines:
        if in_multiline:
            result.append(f'# [INLINED] {line.rstrip()}\n')
            if ')' in line:
                in_multiline = False
            continue
        if is_internal_import(line):
            result.append(f'# [INLINED] {line.rstrip()}\n')
            s = line.strip()
            if '(' in s and ')' not in s:
                in_multiline = True
            continue
        # Strip if __name__ == '__main__' blocks from individual files
        # to prevent ai_trainer/ai_battle main() from running before the Flask server.
        if line.strip() == "if __name__ == '__main__':":
            result.append(f'# [STRIPPED __main__] {line.rstrip()}\n')
            # Skip all remaining lines in this file (__main__ is always last)
            break
        result.append(line)
    return result

def main():
    output_lines = []
    output_lines.append('#!/usr/bin/env python3\n')
    output_lines.append('"""autocombat_backend.py — 集成后端 v1.4.0\n')
    output_lines.append('All-in-one self-contained backend for autocombat JS plugin.\n')
    output_lines.append('Generated: 2026-07-08  Version: 1.4.0\n"""\n\n')

    output_lines.append('import sys, os, json, uuid, threading, random, math, time, importlib, re, queue\n')
    output_lines.append('from datetime import datetime\n')
    output_lines.append('from collections import defaultdict\n')
    output_lines.append('from concurrent.futures import ProcessPoolExecutor, as_completed\n')
    output_lines.append('import multiprocessing\n')
    output_lines.append('import itertools\n')
    output_lines.append('import logging, logging.handlers\n')
    output_lines.append('from http.server import HTTPServer, BaseHTTPRequestHandler\n')
    output_lines.append('from socketserver import ThreadingMixIn\n')
    output_lines.append('import io as _io\n\n')
    output_lines.append('from flask import Flask, request, jsonify\n\n')

    # ── Symbols needed by characters_data_pvp.py before battle_engine.py is inlined ──
    output_lines.append("CN_NUMS = ['零','一','二','三','四','五','六','七','八','九','十',\n")
    output_lines.append("           '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十',\n")
    output_lines.append("           '二十一','二十二','二十三','二十四','二十五','二十六','二十七','二十八','二十九','三十']\n")
    output_lines.append("CAT_LETTERS = ['a','b','c','d','e','f']\n\n")

    for fname in FILES_IN_ORDER:
        fpath = os.path.join(BASE, fname)
        output_lines.append(f'\n# {"="*60}\n')
        output_lines.append(f'# BEGIN INLINED: {fname}\n')
        output_lines.append(f'# {"="*60}\n')
        output_lines.extend(process_file(fpath))
        output_lines.append(f'\n# END INLINED: {fname}\n')

    output_lines.append('\n# Post-inline fixups\n')
    output_lines.append('import types as _types\n')
    # 将 sys.modules['characters_data'] 直接指向 characters_data_pvp 模块
    # （而非假模块拷贝）。这样 importlib.reload(characters_data_pvp) 后，
    # battle_engine 的 lazy import "from characters_data import ..." 会自动获得重载后的数据。
    output_lines.append('sys.modules[\'characters_data\'] = characters_data_pvp\n')
    output_lines.append('_SUMMON_TEMPLATES = characters_data_pvp.SUMMON_TEMPLATES  # backward compat\n')
    output_lines.append('_bem = _types.ModuleType(\'battle_engine\')\n')
    output_lines.append('_bem._SUMMON_TEMPLATES = characters_data_pvp.SUMMON_TEMPLATES\n')
    output_lines.append('_bem.CN_NUMS = CN_NUMS\n')
    output_lines.append('_bem.CAT_LETTERS = CAT_LETTERS\n')
    output_lines.append('_bem.CombatEngine = CombatEngine\n')
    output_lines.append('_bem.FullBattleEngine = FullBattleEngine\n')
    output_lines.append('_bem.FastBattleEngine = FastBattleEngine\n')
    output_lines.append('sys.modules[\'battle_engine\'] = _bem\n')

    output_lines.append('\n# Main entry\n')
    output_lines.append("""if __name__ == '__main__':
    import argparse, logging
    parser = argparse.ArgumentParser(description='autocombat integrated backend v1.4.0')
    parser.add_argument('--port', type=int, default=8889, help='Server port')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    # Flask error logging
    app_logger = logging.getLogger('flask.app')
    app_logger.setLevel(logging.ERROR)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    app_logger.addHandler(handler)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # Reload character data at startup
    try:
        importlib.invalidate_caches()
        importlib.reload(characters_data_pvp)
        sys.modules['characters_data'] = characters_data_pvp
        if hasattr(characters_data_pvp, 'SUMMON_TEMPLATES'):
            _bem._SUMMON_TEMPLATES = characters_data_pvp.SUMMON_TEMPLATES
        char_count = len(characters_data_pvp.ALL_CHARACTERS) if hasattr(characters_data_pvp, 'ALL_CHARACTERS') else 0
        sum_tmpl = len(characters_data_pvp.SUMMON_TEMPLATES) if hasattr(characters_data_pvp, 'SUMMON_TEMPLATES') else 0
        print(f'[reload] {char_count} characters, {sum_tmpl} summon templates loaded')
    except Exception as e:
        print(f'[reload] character data load failed: {e}')

    print(f'[autocombat_backend v1.4.0] http://{args.host}:{args.port}')
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
""")

    output_path = os.path.join(BASE, 'autocombat_backend.py')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(output_lines)
    print(f'Written {len(output_lines)} lines to {output_path}')

if __name__ == '__main__':
    main()
