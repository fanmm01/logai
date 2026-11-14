"""Reproducible Y17 screening and actual ten-tournament acceptance.

Uses the production Tournament/BattleEngine classes, with display delays removed.
Full mode runs every scheduled prelim match, preserving reaction learning and
the production schedule/standings; no knockout wins enter the 82-game statistic.
"""
import argparse
import ast
from collections import Counter
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import threading
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
FILES = ('characters_data.py', 'characters_data_pvp.py', 'battle_engine.py',
         'ai_trainer.py', 'ai_battle.py', 'battle_http_server.py',
         'combat_settings.py', 'combat_defaults.py', 'team_tables.py', 'ai_weights.json')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--weights', type=Path)
    parser.add_argument('--settings', type=Path, default=ROOT / '.ui-runtime/settings.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--seed', type=int, default=2026111500)
    parser.add_argument('--screen', action='store_true', help='Y17 schedule only; not final acceptance')
    parser.add_argument('--rule', action='append', default=[], help='Candidate blood rule KEY=JSON_VALUE')
    parser.add_argument('--hp', type=int, help='Candidate Y17 base HP')
    parser.add_argument('--level', type=int, help='Candidate Y17 level')
    parser.add_argument('--workers', type=int, default=1, help='Independent tournament processes')
    args = parser.parse_args()
    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        parts = args.output.parent / (args.output.stem + '_parts')
        parts.mkdir(parents=True, exist_ok=True)
        def run_one(seed):
            path = parts / f'{seed}.json'
            command = [sys.executable, str(Path(__file__).resolve()), '--source', str(args.source),
                       '--settings', str(args.settings), '--runs', '1', '--seed', str(seed), '--output', str(path)]
            for flag, value in (('--weights', args.weights), ('--hp', args.hp), ('--level', args.level)):
                if value is not None:
                    command.extend([flag, str(value)])
            if args.screen:
                command.append('--screen')
            for rule in args.rule:
                command.extend(['--rule', rule])
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if result.returncode:
                raise RuntimeError(result.stderr[-2000:])
            return json.loads(path.read_text(encoding='utf-8'))
        completed = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_one, args.seed + i) for i in range(args.runs)]
            for future in as_completed(futures):
                result = future.result()
                completed.append(result)
                first = completed[0]
                if any(r['fingerprints'] != first['fingerprints'] or r['settings'] != first['settings']
                       or r['effective_character'] != first['effective_character'] for r in completed):
                    raise RuntimeError('Tournament inputs differ between processes')
                output = dict(first)
                output['seed'] = args.seed
                output['runs'] = sorted((r for result in completed for r in result['runs']), key=lambda r: r['seed'])
                output['errors'] = [e for result in completed for e in result['errors']]
                output['inputs_unchanged'] = all(r['inputs_unchanged'] for r in completed)
                output['mean_wins'] = sum(r['wins'] for r in output['runs']) / len(output['runs'])
                output['target_met'] = (not args.screen and len(output['runs']) == 10 and not output['errors']
                    and output['inputs_unchanged'] and all(r['battles'] == 82 for r in output['runs'])
                    and output['effective_character']['level'] == 6 and 45 <= output['mean_wins'] <= 47)
                output['workers'] = args.workers
                args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
                print('completed', len(output['runs']), 'mean_wins', output['mean_wins'], flush=True)
        return
    source = args.source.resolve()
    sys.path[:0] = [str(source), str(ROOT)]
    import battle_engine as be
    import characters_data as cd
    import ai_trainer as tr
    from combat_settings import settings_scope, pinned, value, snapshot
    import tests.test_summon_groups as fixture
    overrides = {key: json.loads(value) for key, value in (pair.split('=', 1) for pair in args.rule)}
    cd.YEZHAOMING['blood_rules'].update(overrides)
    if args.hp is not None:
        cd.YEZHAOMING['attrs'].update(体力=args.hp, 体力上限=args.hp)
    if args.level is not None:
        cd.YEZHAOMING['attrs']['等级'] = args.level
    fixture.ROOT = source
    classes, logs = fixture.battle_classes()
    # Retain the production scheduler too, without module-level logger setup.
    ns = vars(classes)
    errors = []
    def log(level, msg):
        if level == 'error':
            errors.append(msg)
    ns.update(itertools=itertools, threading=threading, datetime=datetime,
              settings_scope=settings_scope, ALL_CHARACTERS=cd.ALL_CHARACTERS,
              CombatEngine=be.CombatEngine, load_character_to_engine=cd.load_character_to_engine,
              battle_log=log)
    tree = ast.parse((source / 'ai_battle.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Tournament']
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source / 'ai_battle.py'), 'exec'), ns)
    config = json.loads(args.settings.read_text(encoding='utf-8')) if args.settings.exists() else snapshot()
    # Threaded production prelims share global RNG. Serial execution makes the
    # same rules reproducible without a scheduler-dependent random interleaving.
    config['PRELIM_WORKERS'] = 1
    for key in config:
        if key.startswith('DELAY_') or key == 'TOURNAMENT_EVENT_DELAY':
            config[key] = 0
    weights_path = (args.weights or source / 'ai_weights.json').resolve()
    weights = tr.load_q_table(weights_path)
    for mode in ('solo', 'team', 'summon'):
        setattr(classes.AIController, 'Q_' + mode.upper(), weights[mode])
    classes.AIController.load_q_table = classmethod(lambda cls: None)
    fingerprints = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                    for name in FILES if (source / name).exists()}
    fingerprints['weights_used'] = hashlib.sha256(weights_path.read_bytes()).hexdigest()
    output = dict(mode='screen' if args.screen else 'full_prelim', seed=args.seed,
                  source_snapshot=str(source),
                  candidate=dict(rules=overrides, hp=args.hp, level=args.level),
                  effective_character=dict(level=cd.YEZHAOMING['attrs']['等级'], hp=cd.YEZHAOMING['attrs']['体力上限'],
                      mp=cd.YEZHAOMING['attrs']['魔力上限'], rules=cd.YEZHAOMING['blood_rules']),
                  fingerprints=fingerprints, settings=config, runs=[], errors=errors)
    counts = Counter()
    original_cast = classes.BattleEngine._execute_spell
    original_command = classes.BattleEngine._execute_ai_command
    original_run = classes.BattleEngine.run_battle
    def cast(engine, uid, target, spell):
        if engine._is_y17(uid):
            counts['casts'] += 1
            counts['spell_' + str(spell['index'])] += 1
        result = original_cast(engine, uid, target, spell)
        if engine._is_y17(uid) and not be.action_committed(result):
            counts['spell_rejects'] += 1
        return result
    def command(engine, uid, cmd):
        result = original_command(engine, uid, cmd)
        if engine._is_y17(uid):
            counts['commands'] += 1
            if cmd.startswith(('.s', '.a s')) and not be.action_committed(result):
                counts['command_rejects'] += 1
                counts['reject:' + cmd + ':' + str(result)[:90]] += 1
        return result
    classes.BattleEngine._execute_spell = cast
    classes.BattleEngine._execute_ai_command = command
    def battle_run(engine, controllers):
        result = original_run(engine, controllers)
        char = engine.characters.get('char_Y17')
        if char:
            for key, amount in json.loads(char.get_str('_blood_audit') or '{}').items():
                counts['audit:' + key] += amount
            counts['blood_remaining'] += engine._get_blood('char_Y17')
        return result
    classes.BattleEngine.run_battle = battle_run
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with settings_scope(config):
        for run in range(args.runs):
            seed = args.seed + run
            random.seed(seed)
            tr._ACTION_CACHE.clear()
            counts.clear()
            tournament = ns['Tournament'](tournament_type=0, run_times=1,
                                           enabled_modes=['1v1'], settings=copy.deepcopy(config))
            tournament.init_characters()
            if args.screen:
                build = tournament._build_rr_pool
                allocate = tournament._allocate_individual_bonus_matches
                tournament._build_rr_pool = lambda *a, **k: [m for m in build(*a, **k)
                    if 'Y17' in m[2] + m[3]]
                tournament._allocate_individual_bonus_matches = lambda *a, **k: [m for m in allocate(*a, **k)
                    if 'Y17' in m[2] + m[3]]
            started = time.monotonic()
            tournament.run_phases(write_results_flag=False)
            standings = tournament._rankings_info['1v1']
            row = next(r for r in standings if r[1] == 'Y17')
            record = dict(seed=seed, wins=row[3], battles=row[4], rank=None if args.screen else row[0], points=row[2],
                          seconds=round(time.monotonic() - started, 2), counts=dict(counts),
                          season_status=tournament.char_season_status, standings=standings,
                          games=tournament.results)
            output['runs'].append(record)
            output['mean_wins'] = sum(r['wins'] for r in output['runs']) / len(output['runs'])
            output['target_met'] = (not args.screen and len(output['runs']) == 10
                and not errors and all(r['battles'] == 82 for r in output['runs'])
                and cd.YEZHAOMING['attrs']['等级'] == 6 and 45 <= output['mean_wins'] <= 47)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: record[k] for k in ('seed','wins','battles','rank','seconds','counts')},
                             ensure_ascii=False), flush=True)
            logs.clear()
    after = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
             for name in FILES if (source / name).exists()}
    output['inputs_unchanged'] = all(fingerprints[k] == v for k, v in after.items())
    output['target_met'] = output['target_met'] and output['inputs_unchanged']
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print('mean_wins', output['mean_wins'], 'target_met', output['target_met'], 'errors', len(errors))


if __name__ == '__main__':
    main()
