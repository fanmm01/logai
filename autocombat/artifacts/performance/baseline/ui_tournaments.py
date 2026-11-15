"""Persistent, independent per-format schedules with bounded advancement commands."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import random
import secrets
import shutil
import subprocess
import sys
import threading
import time

from flask import jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, NotFound
from combat_settings import value as setting, snapshot as settings_snapshot, settings_scope, season_statuses, valid_season


def standings(t):
    return sorted(t['standings'], key=lambda x: (-x['points'], -x['wins'], x['id']))


def bracket(t):
    """Same weighted combined play-in/QF draw and SF pairings as Tournament."""
    import ai_battle as ab
    cfg = t.get('settings') or settings_snapshot()
    ranked = [row['id'] for row in standings(t)]
    rng = random.Random(t['seed'] + 701)
    series = []

    def add(identifier, stage, a, b, n):
        series.append(dict(id=identifier, stage=stage, a=a, b=b, best_of=n, score=[0, 0], games=[], winner=None, loser=None))

    if len(ranked) >= 12:
        seeds, challengers = ranked[:4], ranked[4:12]
        remaining = list(range(8))
        for i, seed in enumerate(seeds):
            pair = []
            for _ in range(2):
                chosen = rng.choices(remaining, weights=[cfg['KNOCKOUT_DRAW_WEIGHTS'][c][i] for c in remaining])[0]
                remaining.remove(chosen)
                pair.append(challengers[chosen])
            add(f'PI{i+1}', '附加赛', *pair, cfg['BO_N_PLAYIN'])
            add(f'QF{i+1}', '八进四', seed, f'W:PI{i+1}', cfg['BO_N_QF'])
    elif len(ranked) >= 8:
        remaining = list(range(4))
        for i, challenger in enumerate(ranked[4:8]):
            chosen = rng.choices(remaining, weights=[cfg['KNOCKOUT_DRAW_WEIGHTS'][i][c] for c in remaining])[0]
            remaining.remove(chosen)
            add(f'QF{i+1}', '八进四', ranked[chosen], challenger, cfg['BO_N_QF'])
    if len(ranked) >= 8:
        sf = [('W:QF1', 'W:QF4'), ('W:QF2', 'W:QF3')]
    else:
        sf = [(ranked[0], ranked[3]), (ranked[1], ranked[2])]
    for i, pair in enumerate(sf):
        add(f'SF{i+1}', '半决赛', *pair, cfg['BO_N_SF'])
    add('BRONZE', '季军赛', 'L:SF1', 'L:SF2', cfg['BO_N_BRONZE'])
    add('FINAL', '决赛', 'W:SF1', 'W:SF2', cfg['BO_N_FINAL'])
    order = {'附加赛': 0, '八进四': 1, '半决赛': 2, '季军赛': 3, '决赛': 4}
    return sorted(series, key=lambda s: order[s['stage']])


def participant(t, source):
    if isinstance(source, int):
        return source
    kind, identifier = source.split(':', 1)
    s = next(x for x in t['series'] if x['id'] == identifier)
    return s['winner' if kind == 'W' else 'loser']


def ready(t):
    return [s for s in t['series'] if s['winner'] is None
            and participant(t, s['a']) is not None and participant(t, s['b']) is not None]


class Tournaments:
    def __init__(self, bp, context, management, store):
        self.c, self.management, self.store = context, management, store
        self.lock, self.threads, self.workers = threading.RLock(), set(), {}
        for t in store.list('tournament'):
            if 'settings' not in t:
                t['settings'] = settings_snapshot()
                self.save(t)
            if t['status'] in ('running', 'watching'):
                t.update(status='paused', current=None, message='服务重启，已完成赛果已保留；未完成场次将从场初重新开始')
                self.save(t)
        for path, name, method in [('/tournaments', 'create', 'POST'), ('/tournaments', 'list', 'GET'),
                                   ('/tournaments/<tid>', 'get', 'GET'), ('/tournaments/<tid>/advance', 'advance', 'POST'),
                                   ('/tournaments/<tid>/watch', 'watch', 'POST'),
                                   ('/tournaments/<tid>/next', 'next_game', 'POST'),
                                   ('/tournaments/<tid>/replays/<replay_id>', 'replay', 'GET')]:
            bp.add_url_rule('/api/ui' + path, 'tournaments_' + name, getattr(self, name), methods=[method])

    def save(self, t):
        t['revision'] = t.get('revision', 0) + 1
        self.store.put('tournament', t)

    def lookup(self, tid):
        t = self.store.get('tournament', tid)
        if not t or not self.c['session_id']():
            raise NotFound('赛事不存在')
        return t

    def public(self, t):
        result = copy.deepcopy(t)
        result.pop('owner', None)
        result.setdefault('speed', 1)
        result['standings'] = standings(t)
        for s in result['series']:
            s['team_a'] = participant(t, s['a'])
            s['team_b'] = participant(t, s['b'])
        return result

    def list(self):
        if not self.c['session_id']():
            raise NotFound('赛事不存在')
        return jsonify(tournaments=[self.public(t) for t in self.store.list('tournament')])

    def get(self, tid):
        return jsonify(self.public(self.lookup(tid)))

    def replay(self, tid, replay_id):
        self.lookup(tid)
        replay = self.store.get('tournament_replay', replay_id)
        if not replay or replay['tournament_id'] != tid:
            raise NotFound('回放不存在')
        return jsonify(replay)

    def room_context(self, room):
        tid = room.get('tournament_id')
        if not tid:
            return None
        t = self.store.get('tournament', tid)
        return dict(id=tid, series=room['series_id'], next_available=bool(t and ready(t)))

    def create(self):
        data = self.c['body']()
        if not self.c['session_id']():
            raise BadRequest('请先建立会话')
        import ai_battle as ab
        TEAM_TABLES_2V2, TEAM_TABLES_3V3 = setting('TEAM_TABLES_2V2'), setting('TEAM_TABLES_3V3')
        cfg = settings_snapshot()
        fmt = data.get('format', cfg['SIM_DEFAULT_FORMAT'])
        if fmt not in ('1v1', '2v2', '3v3'):
            raise BadRequest('无效赛制')
        valid = {c['serial'] for c in self.management.module().ALL_CHARACTERS}
        teams = data.get('teams') or ([[c['serial']] for c in self.management.module().ALL_CHARACTERS] if fmt == '1v1'
                                   else TEAM_TABLES_2V2 if fmt == '2v2' else TEAM_TABLES_3V3)
        if not data.get('teams'):
            teams = [team for team in teams if all(s in valid for s in team)]
        if len(teams) < 4 or any(not isinstance(team, list) or not team or len(team) != len(set(team)) or any(s not in valid for s in team) for team in teams):
            raise BadRequest('赛事至少需要四支有效队伍，请检查预设引用')
        seed, season = data.get('seed', random.randrange(2**31)), data.get('season', cfg['SEASON_AVG_STATUS'])
        if type(seed) is not int or not 0 <= seed < 2**31 or not valid_season(season):
            raise BadRequest('种子或赛季状态无效')
        # Pool builders use module RNG; preserve live battle randomness.
        with self.lock, self.management.lock, self.c['ns']['_lock']:
            state = random.getstate()
            random.seed(seed)
            try:
                tournament = ab.Tournament(enabled_modes=[fmt], settings=cfg)
                if fmt == '1v1':
                    rr = min(cfg['RR_ROUNDS_1V1'], cfg['TARGET_1V1_MATCHES'] // (len(teams)-1))
                    pool = tournament._build_rr_pool(fmt, teams, [cfg['DEFAULT_MAP_SIZE']], fmt, rr)
                    pool += tournament._allocate_individual_bonus_matches([x[0] for x in teams], rr, cfg['TARGET_1V1_MATCHES'])
                else:
                    rr = cfg['RR_ROUNDS_2V2'] if fmt == '2v2' else cfg['RR_ROUNDS_3V3']
                    maps = cfg['MAP_SIZES_2V2'] if fmt == '2v2' else cfg['MAP_SIZES_3V3']
                    pool = tournament._build_rr_pool(fmt, teams, maps, fmt, rr)
                    pool += tournament._allocate_team_bonus_matches(teams, rr, cfg['RANDOM_BONUS_2V2'] if fmt == '2v2' else cfg['RANDOM_BONUS_3V3'])
                random.shuffle(pool)
                schedule = [dict(a=p[4], b=p[5], map_size=p[1], seed=random.randrange(2**31)) for p in pool]
                with settings_scope(cfg):
                    statuses = season_statuses(valid, season)
            finally:
                random.setstate(state)
            tid = secrets.token_hex(8)
            self.management.snapshot_task(tid)
            t = dict(id=tid, owner=self.c['session_id'](), format=fmt, seed=seed, char_season=statuses,
                     teams=teams, schedule=schedule, completed=0, series=[], phase='regular', status='paused',
                     current=None, revision=0, requests=[], logs=[], results=[], speed=cfg['ROOM_DEFAULT_SPEED'], settings=cfg,
                     standings=[dict(id=i, team=team, points=0, wins=0, battles=0) for i, team in enumerate(teams)])
            self.save(t)
            return jsonify(self.public(t))

    def advance(self, tid):
        data = self.c['body']()
        with self.lock:
            t = self.lookup(tid)
            rid, op = data.get('request_id'), data.get('op')
            if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
                raise BadRequest('缺少请求 ID')
            sid = self.c['session_id']()
            request_key = sid + ':' + rid
            if request_key in t['requests'] or (rid in t['requests'] and t.get('owner') == sid):
                return jsonify(self.public(t))
            if op == 'speed':
                speed = data.get('speed')
                if type(speed) is not int or speed not in (1, 2, 4):
                    raise BadRequest('速度必须为 1、2 或 4')
                t['speed'] = speed
                t['requests'] = (t['requests'] + [request_key])[-200:]
                self.save(t)
                return jsonify(self.public(t))
            watch_room = (t.get('current') or {}).get('room')
            resuming = op == 'resume' and (tid in self.threads or watch_room) and t['status'] == 'paused' and t.get('current')
            if resuming:
                t['status'] = 'watching' if watch_room else 'running'
                if watch_room:
                    with self.c['guard']:
                        room = self.c['rooms'].get(watch_room)
                        if not room:
                            raise Conflict('观战房间已失效，请重新加载赛事')
                        room['running'] = True
                        self.c['emit'](room, 'control', '继续比赛')
            elif op == 'pause':
                t['status'] = 'paused'
                if watch_room:
                    with self.c['guard']:
                        room = self.c['rooms'].get(watch_room)
                        if room:
                            room['running'] = False
                            self.c['emit'](room, 'control', '比赛已暂停')
            else:
                if watch_room:
                    raise Conflict('当前比赛尚未结束，请进场观战或继续当前比赛')
                if t['status'] in ('running', 'watching') or tid in self.threads:
                    raise Conflict('请先暂停当前推进')
                if data.get('revision') != t['revision']:
                    raise Conflict('赛况已更新，请重试')
                if t['phase'] == 'done':
                    raise Conflict('赛事已完成')
                if op not in ('regular', 'game', 'series', 'round', 'playoffs', 'turn_game', 'turn_playoffs'):
                    raise BadRequest('无效的推进方式')
                if t['phase'] == 'regular' and op != 'regular':
                    raise Conflict('请先完成常规赛')
                if t['phase'] != 'regular' and op == 'regular':
                    raise Conflict('常规赛已结束')
                selected = data.get('series')
                available = ready(t) if t['phase'] == 'playoffs' else []
                if op in ('game', 'series', 'round', 'turn_game') and not any(s['id'] == selected for s in available):
                    raise BadRequest('请选择已确定双方且尚未结束的系列赛')
                t.update(status='running', operation=op, selected=selected)
            t['requests'] = (t['requests'] + [request_key])[-200:]
            self.save(t)
            if op != 'pause' and not resuming:
                self.threads.add(tid)
                threading.Thread(target=self.run, args=(tid,), daemon=True).start()
            return jsonify(self.public(t))

    def run(self, tid):
        try:
            t = self.store.get('tournament', tid)
            selected = next((s for s in t['series'] if s['id'] == t.get('selected')), None)
            stage = selected['stage'] if selected else None
            while True:
                with self.lock:
                    t = self.store.get('tournament', tid)
                    if t['status'] != 'running':
                        return
                    if t['phase'] == 'regular':
                        match = t['schedule'][t['completed']]
                        identifier = None
                    else:
                        available = ready(t)
                        if t['operation'] in ('game', 'series', 'turn_game'):
                            available = [s for s in available if s['id'] == t['selected']]
                        elif t['operation'] == 'round':
                            available = [s for s in available if s['stage'] == stage]
                        if not available:
                            t['status'] = 'paused'
                            self.save(t)
                            return
                        s = available[0]
                        identifier = s['id']
                        match = dict(a=participant(t, s['a']), b=participant(t, s['b']), map_size=t['settings']['KNOCKOUT_MAP_SIZE'],
                                     seed=t['seed'] + 10000 + t['series'].index(s)*100 + len(s['games']))
                    t['current'] = dict(series=identifier, match=match, replay=[])
                    t['logs'] = []
                    self.save(t)
                result = self.simulate(tid, t, match, t.get('operation', '').startswith('turn_'))
                with self.lock:
                    t = self.store.get('tournament', tid)
                    # A finished atomic game is always committed, even if Pause
                    # arrived during computation. It is never counted twice.
                    self.commit(t, match, identifier, result, False)
                    if t.get('operation') in ('game', 'turn_game') and t['status'] == 'running':
                        t['status'] = 'paused'
                    self.save(t)
                    if t['status'] != 'running':
                        return
        except Exception as exc:
            with self.lock:
                t = self.store.get('tournament', tid)
                t.update(status='paused', current=None, message='推进失败：' + str(exc))
                self.save(t)
        finally:
            with self.lock:
                self.threads.discard(tid)
                worker = self.workers.pop(tid, None)
            if worker:
                proc, err = worker
                proc.stdin.close()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait()
                proc.stdout.close()
                err.close()

    def simulate(self, tid, t, match, verbose=False):
        cfg = t['settings']
        import ai_battle as ab
        folder = self.management.runtime/'jobs'/tid
        params = dict(settings=cfg, mode='game', team_a=t['teams'][match['a']], team_b=t['teams'][match['b']],
                      map_size=match['map_size'], seed=match['seed'], char_season=t['char_season'], verbose=verbose,
                      max_rounds=cfg['MAX_ROUNDS_PRELIM_BY_FORMAT'].get(t['format'], cfg['MAX_ROUNDS_PRELIM']) if t['phase'] == 'regular' else cfg['MAX_ROUNDS_KNOCKOUT'])
        result = None
        replay = []
        logs = []
        if tid not in self.workers:
            # Upgrade the display adapter for existing pinned tournaments too.
            # Their engine, character data and weights remain untouched.
            for name in ('ui_task_worker.py', 'combat_settings.py', 'combat_defaults.py'):
                shutil.copyfile(Path(__file__).with_name(name), folder/name)
            err = (folder/'game-error.log').open('w', encoding='utf-8')
            proc = subprocess.Popen([sys.executable, '-u', str(folder/'ui_task_worker.py'), '--server'], cwd=folder,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, encoding='utf-8', text=True,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.workers[tid] = proc, err
        proc, err = self.workers[tid]
        proc.stdin.write(json.dumps(params)+'\n')
        proc.stdin.flush()
        for line in proc.stdout:
            if verbose:
                while self.store.get('tournament', tid)['status'] == 'paused':
                    time.sleep(setting('TOURNAMENT_PAUSE_POLL', live=True))
            row = json.loads(line)
            if 'result' in row:
                result = dict(row['result'], _round_replay=row.get('replay', replay), _battle_logs=logs)
                break
            if 'round' in row:
                replay.append(row['round'])
            if verbose and 'round' in row:
                with self.lock:
                    current = self.store.get('tournament', tid)
                    current['current']['replay'] = list(replay)
                    self.save(current)
                speed = self.store.get('tournament', tid).get('speed', 1)
                time.sleep(setting('TOURNAMENT_EVENT_DELAY', live=True) / speed)
            if verbose and 'log' in row:
                logs.append(row['log'])
                with self.lock:
                    current = self.store.get('tournament', tid)
                    current['logs'] = list(logs)
                    self.save(current)
        if result is None:
            raise RuntimeError((folder/'game-error.log').read_text(encoding='utf-8')[-1000:] or '比赛未完成')
        return result

    def commit(self, t, match, identifier, result, manual):
        import ai_battle as ab
        result = dict(result)
        replay = result.pop('_round_replay', [])
        logs = result.pop('_battle_logs', [])
        winner = match['a'] if result['winner'] == 'Y' else match['b']
        loser = match['b'] if result['winner'] == 'Y' else match['a']
        record = dict(match=match, result=result, manual=manual)
        if replay:
            replay_id = secrets.token_hex(12)
            game = t['completed'] + 1 if identifier is None else len(next(s for s in t['series'] if s['id'] == identifier)['games']) + 1
            self.store.put('tournament_replay', dict(id=replay_id, tournament_id=t['id'],
                series=identifier, game=game, match=match, lines=replay, logs=logs))
            record.update(replay_id=replay_id, replay_rounds=len(replay))
            t['last_replay'] = dict(id=replay_id, series=identifier, game=game)
        if identifier is None:
            wp, lp = ab.Tournament._calc_points(None, result)
            for idx, points, won in [(winner, wp, 1), (loser, lp, 0)]:
                row = t['standings'][idx]
                row['points'] = round(row['points'] + points, 2)
                row['wins'] += won
                row['battles'] += 1
            t['results'].append(record)
            t['completed'] += 1
            if t['completed'] == len(t['schedule']):
                t.update(phase='playoffs', status='paused', message='常规赛完成，季后赛已生成并自动暂停')
                t['series'] = bracket(t)
        else:
            s = next(s for s in t['series'] if s['id'] == identifier)
            side = 0 if winner == participant(t, s['a']) else 1
            s['score'][side] += 1
            s['games'].append(record)
            if s['score'][side] > s['best_of']//2:
                s.update(winner=winner, loser=loser)
            if all(s['winner'] is not None for s in t['series']):
                t.update(phase='done', status='paused', message='锦标赛已完成')
        t['current'] = None

    def watch(self, tid):
        data = self.c['body']()
        with self.lock, self.c['guard'], self.c['ns']['_lock']:
            t = self.lookup(tid)
            if (t.get('current') or {}).get('room'):
                room = self.c['rooms'].get(t['current']['room'])
                if room:
                    return jsonify(room=self.c['public'](room, True), tournament=self.public(t))
            if t['status'] != 'paused' or tid in self.threads or data.get('revision') != t['revision']:
                raise Conflict('赛况已更新或正在推进，请刷新后重试')
            s = next((s for s in ready(t) if s['id'] == data.get('series')), None)
            if not s:
                raise BadRequest('请选择可进行的系列赛')
            return self.open_watch(t, s)

    def next_game(self, tid):
        data = self.c['body']()
        with self.lock, self.c['guard'], self.c['ns']['_lock']:
            t = self.lookup(tid)
            room = self.c['room_for'](str(data.get('room', '')))
            if room.get('tournament_id') != tid:
                raise BadRequest('房间不属于本赛事')
            if self.c['engine_for'](room)._get_state().get('phase') != 'ended':
                raise Conflict('当前比赛尚未结束')
            # Repeated clicks from the same result screen return its one successor.
            successor = self.c['rooms'].get(room.get('next_room', ''))
            if successor:
                return jsonify(room=self.c['public'](successor, True), tournament=self.public(t))
            current_room = self.c['rooms'].get((t.get('current') or {}).get('room', ''))
            if current_room:
                room['next_room'] = current_room['code']
                return jsonify(room=self.c['public'](current_room, True), tournament=self.public(t))
            if t['status'] != 'paused' or tid in self.threads:
                raise Conflict('赛事正在推进，请先暂停')
            available = ready(t)
            if not available:
                raise Conflict('锦标赛已结束，没有下一场')
            selected = next((s for s in available if s['id'] == room['series_id']), available[0])
            response = self.open_watch(t, selected)
            room['next_room'] = response.get_json()['room']['code']
            return response

    def open_watch(self, t, s):
        cfg = t['settings']
        tid = t['id']
        folder = self.management.runtime/'jobs'/tid
        spec = importlib.util.spec_from_file_location('watch_data', folder/'characters_data.py')
        pinned = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pinned)
        module = self.management.module()
        keys = ('ALL_CHARACTERS', 'SUMMON_TEMPLATES', 'ITEM_TEMPLATES', 'GLOBAL_RANGE_OVERRIDE')
        before = {k: getattr(module, k) for k in keys}
        a, b = participant(t, s['a']), participant(t, s['b'])
        match = dict(a=a, b=b, map_size=t['settings']['KNOCKOUT_MAP_SIZE'], seed=t['seed']+10000+t['series'].index(s)*100+len(s['games']))
        params = dict(map_size=match['map_size'], seed=match['seed'], team_a=t['teams'][a], team_b=t['teams'][b], local=True,
                      ai_slots=['Y:'+x for x in t['teams'][a]] + ['X:'+x for x in t['teams'][b]], name='锦标赛 · '+s['id'])
        cookie = request.headers.get('Cookie', '')
        room = None
        try:
            for k in keys:
                setattr(module, k, getattr(pinned, k))
            with settings_scope(cfg), self.c['app'].test_request_context('/api/ui/rooms', method='POST', json=params, headers={'Cookie': cookie}):
                response = self.c['create_room']()
                info = response.get_json()
            room = self.c['rooms'][info['code']]
            eng = self.c['engine_for'](room)
            eng._ui_q_table = self.c['ns']['_load_q_table_pvp'](str(folder/'ai_weights.json'))
            with self.c['room_random'](room):
                self.c['invoke']('prepare_ready', room['battle_id'])
                for uid in dict.fromkeys(eng._resolve_uid(e['userId']) for e in eng._get_initiative()):
                    serial = eng.get_char(uid).serial
                    from battle_engine import season_status_roll
                    eng.get_char(uid).set_attr('状态', season_status_roll(t['char_season'].get(serial, cfg['SEASON_FALLBACK_STATUS'])))
            import ai_battle as ab
            eng.max_rounds = cfg['MAX_ROUNDS_KNOCKOUT']
            eng._team_roster = list(dict.fromkeys((eng._resolve_uid(e['userId']), e.get('team', 'Y')) for e in eng._get_initiative() if not e.get('isSummon')))
            room.update(running=True, tournament_id=tid, series_id=s['id'], manual=False)
            from ai_battle import round_hp_line
            room['replay_round'] = eng._get_state()['round']
            room['round_replay'] = [round_hp_line(eng, room['replay_round'])]
            self.c['emit'](room, 'start', '锦标赛观战已开始，可在房间面板接管角色')
        except Exception:
            if room:
                self.c['rooms'].pop(room['code'], None)
                self.c['ns']['_battles'].pop(room['battle_id'], None)
                self.c['ns']['_pending_reactions'].pop(room['battle_id'], None)
            raise
        finally:
            for k, v in before.items():
                setattr(module, k, v)
        match = dict(a=a, b=b, map_size=t['settings']['KNOCKOUT_MAP_SIZE'], seed=t['seed']+10000+t['series'].index(s)*100+len(s['games']))
        t.update(status='watching', current=dict(room=room['code'], series=s['id'], match=match))
        self.save(t)
        return jsonify(room=self.c['public'](room, True), tournament=self.public(t))

    def room_event(self, room, eng):
        tid = room.get('tournament_id')
        if not tid:
            return
        # Called with room guard held; avoid reversing the tournament/room lock order.
        t = self.store.get('tournament', tid)
        current = t.get('current') if t else None
        if not current or current.get('room') != room['code']:
            return
        state = eng._get_state()
        if state.get('phase') == 'active' and state.get('round') != room.get('replay_round'):
            from ai_battle import round_hp_line
            room['replay_round'] = state['round']
            room.setdefault('round_replay', []).append(round_hp_line(eng, state['round']))
        if eng._get_state().get('phase') != 'ended':
            status = 'watching' if room['running'] else 'paused'
            if t['status'] != status:
                t['status'] = status
                self.save(t)
            return
        result = dict(eng._battle_result)
        result['_round_replay'] = room.get('round_replay', [])
        result.setdefault('rounds', eng._get_state().get('round', 0))
        self.commit(t, current['match'], current['series'], result, room.get('manual', False))
        t['status'] = 'paused'
        self.save(t)
