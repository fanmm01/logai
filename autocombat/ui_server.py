"""Local/LAN browser adapter. All combat execution stays in the existing engine.

UI rooms use a separate battle-id namespace and cookie-based membership. State
and event history are intentionally process-local, just like the legacy API.
"""
import copy
import csv
import io
import json
import os
import random
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from flask import Blueprint, Response, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound
from combat_settings import value as setting, snapshot as settings_snapshot, settings_scope, instance_name

ROOT = Path(__file__).resolve().parent


def register_ui(app, ns):
    bp = Blueprint('combat_ui', __name__)
    rooms, jobs, identities = {}, {}, {}
    guard = threading.RLock()
    instance = instance_name()
    cookie_name = 'combat_ui_session' + ('' if instance == '8889' else '_' + instance)
    try:
        lan_ips = sorted({r[4][0] for r in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                          if not r[4][0].startswith(('127.', '169.254.'))})
    except OSError:
        lan_ips = []
    # Exposed to regression tests, not HTTP clients.
    app.extensions['combat_ui'] = {'rooms': rooms, 'jobs': jobs, 'lock': guard}

    def clean(value):
        return json.loads(json.dumps(value, ensure_ascii=False, default=lambda v: list(v) if isinstance(v, set) else str(v)))

    def session_id():
        token = request.cookies.get(cookie_name, '')
        saved = store.get('identity', token) if token else None
        return identities.get(token, saved['player'] if saved else '')

    @app.before_request
    def isolate_ui_battles():
        # UI combat is private to its cookie-authenticated adapter. Legacy callers
        # must not bypass room permissions by guessing its predictable battle id.
        if request.path.startswith('/api/pvp/ui_'):
            return jsonify(error=True, message='浏览器房间请使用 /api/ui/ 接口'), 403

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise BadRequest('请求必须为 JSON 对象')
        # Same-origin UI writes; LAN users receive the UI from this same host.
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
            raise Forbidden('请从战术室页面发起操作')
        return data

    def catalog():
        return [dict(serial=c['serial'], name=c['name'], attrs=c.get('attrs', {}), str_attrs=c.get('str_attrs', {}),
                     spells=c.get('spells', []),
                     passive_rules=c.get('passive_rules', {}), blood_rules=c.get('blood_rules', {}),
                     inventory=c.get('inventory', []), initial_phase=c.get('initial_phase', 0),
                     initial_weapon=c.get('initial_weapon', ''), 初始血液=c.get('初始血液', 0),
                     portrait=f"/ui/portraits/{c['serial']}.webp",
                     avatar=f"/ui/portraits/{c['serial']}-avatar.webp")
                for c in ns['characters_data_pvp'].ALL_CHARACTERS]

    def room_for(code, host=False):
        room = rooms.get(code.upper())
        if not room:
            raise NotFound('房间不存在或服务已重启，请重新创建房间')
        if session_id() not in room['members']:
            raise Forbidden('请先加入房间')
        if host and session_id() != room['host']:
            raise Forbidden('此操作需要房主执行')
        return room

    def engine_for(room):
        eng = ns['_battles'].get(room['battle_id'])
        if not eng:
            raise NotFound('对局已失效，请创建新房间')
        return eng

    def clone_engine(eng):
        # Immutable pinned weights are large. Share them in dry-run clones;
        # clone mutable combat state only (especially for every movement cell).
        q = getattr(eng, '_ui_q_table', None)
        return copy.deepcopy(eng, {id(q): q} if q is not None else {})

    @contextmanager
    def room_random(room):
        original = random.getstate()
        random.setstate(room.get('rng', original))
        try:
            yield
        finally:
            room['rng'] = random.getstate()
            random.setstate(original)

    def active(eng):
        il, st = eng._get_initiative(), eng._get_state() or {}
        idx = st.get('activeIndex', 0)
        return il[idx] if 0 <= idx < len(il) else None

    def pending_for(room):
        p = ns['_pending_reactions'].get(room['battle_id'])
        return (p[0] if p else None) if isinstance(p, list) else p

    def controller(room, eng, uid):
        base = eng._resolve_uid(uid)
        if base in room['controls']:
            return room['controls'][base]
        return room['controls'].get(eng._summon_group_key(base), 'ai')

    def sync_controls(room, eng):
        eng._player_controllers = {}
        for uid in eng.characters:
            who = controller(room, eng, uid)
            if who != 'ai':
                eng._player_controllers[uid] = [who]

    def invoke(name, battle_id=None, data=None):
        # Reuse the existing HTTP handlers without issuing a network request.
        with app.test_request_context('/internal-ui', method='POST', json=data or {}):
            rv = ns[name](battle_id) if battle_id else ns[name]()
            response = app.make_response(rv)
            result = response.get_json() or {}
            if response.status_code >= 400:
                raise BadRequest(result.get('message', '战斗操作失败'))
            return result

    def spell_info(eng, uid, s):
        timing = str(s.get('时机', s.get('timing', '2')))
        acts = eng._get_my_actions(uid)
        extra = '3' in timing and acts.get('附加', 0) > 0
        command = f".a s{s['index']}" if extra else f".s{s['index']}"
        reason = ''
        if '2' not in timing and '3' not in timing:
            reason = '被动技能'
        elif eng._get_cooldown(uid, s['index']):
            reason = f"冷却 {eng._get_cooldown(uid, s['index'])} 回合"
        elif not eng._can_cast_active_skill(uid):
            reason = '当前状态无法施法'
        elif acts.get('主动', 0) <= 0 or (not extra and '2' not in timing):
            reason = '行动次数不足'
        elif s.get('_once_per_battle') and s['name'] in getattr(eng.get_char(uid), '_once_consumed', set()):
            reason = '本场已使用'
        else:
            reason = eng._revive_preflight(uid, s)
        cost = s.get('消耗mp', 1) or 1
        if not reason and isinstance(cost, (int, float)) and eng.get_char(uid).get_attr('魔力', 0) < cost:
            reason = '魔力不足'
        cands = []
        for eff in s.get('effects', []):
            if eff.get('type') == 19:
                cands.extend(g['uid'] for g in eng._revive_candidates(uid, eff))
        return dict(index=s['index'], skill_id=s.get('skill_id', ''), name=s['name'], command=command, timing=timing,
                    mp_cost=cost, san_cost=s.get('消耗san', 0), formula=s.get('_mp_formula', ''),
                    effects=s.get('effects', []), available=not reason, reason=reason,
                    cooldown=eng._get_cooldown(uid, s['index']), revive_candidates=cands,
                    once=bool(s.get('_once_per_battle')))

    def snapshot(room):
        eng = engine_for(room)
        st = eng._get_state() or {}
        il = eng._get_initiative()
        current = active(eng)
        units, seen = [], set()
        # The targeting view excludes defeated units. Presentation must retain
        # zero-HP initiative entries until lifecycle cleanup moves them to grave.
        entries = eng._battle_unit_entries() + il
        for entry in entries:
            uid = eng._resolve_uid(entry['userId'])
            if uid in seen:
                continue
            seen.add(uid)
            ch = eng.get_char(uid)
            hp = eng._get_combat_hp(uid)
            group = eng._get_summon_group(uid) or {}
            rep = group.get('representative_uid', uid)
            units.append(dict(
                uid=uid, serial=getattr(ch, 'serial', ''), name=ch.name,
                team=entry.get('team', 'Y'), coord=entry.get('coord', ''),
                hp=hp if hp is not None else ch.get_attr('体力', 0), hp_max=ch.get_attr('体力上限', 1),
                mp=ch.get_attr('魔力', 0), mp_max=ch.get_attr('魔力上限', 1), san=ch.get_attr('理智', 0),
                summon=bool(entry.get('isSummon')), owner=entry.get('ownerId'), representative=rep,
                group_count=len(eng._get_alive_summon_group_members(uid)) if group else 1,
                spirit=group.get('battle_spirit', group.get('battleSpirit')),
                statuses=eng._get_active_statuses(uid), buffs=eng._get_active_buffs(uid),
                inventory=getattr(ch, 'inventory', []), controller=controller(room, eng, uid),
                auto_reaction=reactions.state(room, eng, uid),
                reaction_choices=['dodge', 'counter'] + (['block'] if ch.get_attr('可格挡', 0) else []),
                move_remaining=max(0, eng._get_move_power(uid) - entry.get('_move_used', 0)),
                actions=eng._get_my_actions(uid), phase=getattr(ch, 'phase', 1),
            ))
        turn = None
        if current:
            uid = current['userId']
            ch = eng.get_char(uid)
            skills = [dict(index=0, name='基本攻击', command='.s0', timing='2', mp_cost=0,
                           effects=[], available=eng._get_my_actions(uid).get('主动', 0) > 0 and eng._can_basic_attack(uid),
                           reason='', cooldown=0, revive_candidates=[], once=False)]
            skills += [spell_info(eng, uid, s) for s in (ch.spells or eng.load_spells(uid) or [])]
            turn = dict(uid=eng._resolve_uid(uid), slot_uid=uid, skills=skills,
                        controller=controller(room, eng, uid), actions=eng._get_my_actions(uid))
        p = pending_for(room)
        reaction = None
        if p:
            defender = p.get('def_uid', p.get('target_uid', ''))
            reaction = dict(defender=eng._resolve_uid(defender), attacker=p.get('atk_name', ''),
                            defender_name=p.get('def_name', ''), skill=p.get('skill_name', ''),
                            damage=p.get('dmg_dice', ''), roll=None if p.get('restim_mode') == 1 else p.get('atk_result', p.get('atk_roll')),
                            controller=controller(room, eng, defender),
                            choices=[k for k in ('dodge', 'counter', 'block') if p.get('can_' + k, k != 'block')])
        grave = list(getattr(eng, '_graveyard', {}).values())
        effects = copy.deepcopy(eng._get_effects())
        for effect in effects:
            if effect.get('type') == 'zone':
                effect['center'] = eng._zone_caster_coord(effect, il)
                effect['affected'] = [u['uid'] for u in units if eng._zone_covers_uid(effect, u['uid'], il)]
        return clean(dict(state=st, map=eng._get_map(), initiative=il, units=units, turn=turn,
                          effects=effects, graveyard=grave, reaction=reaction,
                          result=getattr(eng, '_battle_result', None), revision=room['revision']))

    def emit(room, kind, output=''):
        room['revision'] += 1
        eng = engine_for(room)
        sync_controls(room, eng)
        reactions.clean(room, eng)
        if 'tournaments' in app.extensions['combat_ui']:
            app.extensions['combat_ui']['tournaments'].room_event(room, eng)
        extra = eng._drain_battle_events()
        if extra:
            output += '\n' + '\n'.join(extra)
        event = dict(id=room['revision'], kind=kind, output=output.strip(), time=time.time(), snapshot=snapshot(room))
        room['history'].append(event)
        room['history'] = room['history'][-room['settings']['ROOM_HISTORY_LIMIT']:]
        room['updated'].set()
        return event

    def public(room, history=False):
        port = request.host.rsplit(':', 1)[1] if ':' in request.host else '80'
        invite_urls = [f'http://{ip}:{port}/ui/?room={room["code"]}' for ip in lan_ips]
        return dict(code=room['code'], name=room['name'], local=room['local'], host=room['host'],
                    me=session_id(), members=list(room['members'].values()), revision=room['revision'],
                    running=room['running'], speed=room['speed'], controls=room['controls'],
                    snapshot=snapshot(room), history=room['history'] if history else [], invite_urls=invite_urls,
                    tournament=tournaments.room_context(room))

    def valid_teams(data):
        valid = {c['serial'] for c in catalog()}
        teams = []
        for key in ('team_a', 'team_b'):
            team = data.get(key, [])
            if not isinstance(team, list) or not 1 <= len(team) <= 6 or any(not isinstance(s, str) or s not in valid for s in team):
                raise BadRequest('每队请选择 1–6 名有效角色')
            if len(set(team)) != len(team):
                raise BadRequest('同队角色不能重复')
            teams.append(team)
        size = data.get('map_size', setting('DEFAULT_MAP_SIZE'))
        if not isinstance(size, str) or not re.fullmatch(r'(?:[8-9]|1[0-6])x(?:[8-9]|1[0-6])', size):
            raise BadRequest('地图边长应为 8–16')
        return teams

    def ai_step(room):
        eng = engine_for(room)
        if (eng._get_state() or {}).get('phase') != 'active' or pending_for(room):
            return False
        entry = active(eng)
        if not entry or controller(room, eng, entry['userId']) != 'ai':
            return False
        with ns['_lock'], room_random(room):
            lines = ns['_run_ai_turns'](eng, Q=eng._ui_q_table, max_steps=1, ui_step=True)
            eng._check_and_mark_end(eng._get_state())
            if room.get('tournament_id') and eng._get_state().get('phase') == 'active' and eng._get_state().get('round', 0) > eng.max_rounds:
                from ai_battle import BattleEngine
                eng._battle_result = BattleEngine._timeout_verdict(eng)
                st = eng._get_state()
                st['phase'] = 'ended'
                eng._set_state(st)
                lines.append('达到季后赛回合上限，已按原规则完成超时判定')
            emit(room, 'ai', '\n'.join(lines))
        return True

    def runner(code):
        while True:
            with guard, app.app_context():
                room = rooms.get(code)
                if not room:
                    return
                if time.time() - room['touched'] > room['settings']['ROOM_IDLE_HOURS'] * 3600:
                    ns['_battles'].pop(room['battle_id'], None)
                    ns['_pending_reactions'].pop(room['battle_id'], None)
                    rooms.pop(code, None)
                    return
                delay = setting('ROOM_AI_DELAY', live=True) / room['speed']
                if room['running'] or pending_for(room):
                    try:
                        if not reactions.step(room) and room['running']:
                            ai_step(room)
                    except Exception:
                        app.logger.exception('UI AI step failed')
                        room['running'] = False
                        for key in list(room.get('auto_reactions', {})):
                            reactions.stop(room, key)
                        emit(room, 'error', 'AI 推进失败，已暂停。可以重试单步或由房主接管角色。')
            time.sleep(delay)

    @bp.errorhandler(BadRequest)
    @bp.errorhandler(Forbidden)
    @bp.errorhandler(NotFound)
    @bp.errorhandler(Conflict)
    def ui_error(e):
        return jsonify(error=True, message=e.description), e.code

    @bp.get('/')
    @bp.get('/index.html')
    def root():
        # Tunnel users commonly open the bare host. Keep invite links working.
        query = '?' + request.query_string.decode('latin-1') if request.query_string else ''
        return redirect('/ui/' + query, code=302)

    @bp.get('/ui/')
    @bp.get('/ui/<path:path>')
    def serve(path='index.html'):
        if path.startswith(('portraits/', 'icons/')):
            return send_from_directory(ROOT / 'frontend' / 'public', path)
        dist = ROOT / 'frontend' / 'dist'
        if not (dist / 'index.html').exists():
            return Response('请先在 frontend 目录运行 npm install 和 npm run build。', status=503, content_type='text/plain; charset=utf-8')
        return send_from_directory(dist, path if (dist / path).is_file() else 'index.html')

    @bp.get('/api/ui/catalog')
    def get_catalog():
        data = ns['characters_data_pvp']
        return jsonify(characters=clean(catalog()),
                       summon_templates=clean(data.SUMMON_TEMPLATES),
                       item_templates=clean(data.ITEM_TEMPLATES))

    @bp.get('/api/ui/presets')
    def get_presets():
        from team_tables import TEAM_TABLES_2V2, TEAM_TABLES_3V3
        valid = {c['serial'] for c in catalog()}
        return jsonify(presets=[dict(format=f, index=i, team=t, valid=all(s in valid for s in t))
            for f, teams in [('2v2', TEAM_TABLES_2V2), ('3v3', TEAM_TABLES_3V3)] for i, t in enumerate(teams)])

    @bp.post('/api/ui/session')
    def get_session():
        data = body()
        token = request.cookies.get(cookie_name, '')
        sid = session_id()
        if not sid:
            token = secrets.token_urlsafe(32)
            sid = 'player_' + secrets.token_hex(8)
            identities[token] = sid
            store.put('identity', dict(id=token, player=sid))
        name = str(data.get('name', '指挥官')).strip()[:20] or '指挥官'
        response = jsonify(id=sid, name=name)
        response.set_cookie(cookie_name, token, httponly=True, samesite='Strict', max_age=86400 * 7)
        return response

    @bp.post('/api/ui/rooms')
    def create_room():
        data = body()
        sid = session_id()
        if not sid:
            raise Forbidden('请先建立玩家会话')
        a, b = valid_teams(data)
        with guard, settings_scope():
            code = secrets.token_hex(3).upper()
            while code in rooms:
                code = secrets.token_hex(3).upper()
            battle_id = 'ui_' + code
            invoke('create_battle', data=dict(group_id=battle_id, mode='multi_pvp', team_a=a,
                                              team_b=b, map_size=data.get('map_size', setting('DEFAULT_MAP_SIZE'))))
            eng = ns['_battles'][battle_id]
            eng._settings = settings_snapshot()
            eng._ui_deferred_ai = True
            eng._range_override_snapshot = getattr(ns['characters_data_pvp'], 'GLOBAL_RANGE_OVERRIDE', 0)
            eng._summon_templates_snapshot = copy.deepcopy(ns['characters_data_pvp'].SUMMON_TEMPLATES)
            eng._item_templates_snapshot = copy.deepcopy(ns['characters_data_pvp'].ITEM_TEMPLATES)
            eng._ui_q_table = ns['_load_q_table_pvp']()
            local = data.get('local', True) is True
            ai_slots = data.get('ai_slots', [])
            if not isinstance(ai_slots, list) or any(not isinstance(s, str) for s in ai_slots):
                raise BadRequest('AI 角色设置无效')
            ai = set(ai_slots)
            controls = {}
            for entry in eng._get_initiative():
                uid = eng._resolve_uid(entry['userId'])
                slot = entry['team'] + ':' + eng.get_char(uid).serial
                controls[uid] = 'ai' if slot in ai else sid
            room = dict(code=code, battle_id=battle_id, name=str(data.get('name', '星序演习室'))[:30],
                        host=sid, local=local, controls=controls, revision=0,
                        members={sid: dict(id=sid, name=str(data.get('player_name', '指挥官'))[:20], ready=True, spectator=False)},
                        history=[], requests=OrderedDict(), running=False, speed=setting('ROOM_DEFAULT_SPEED'), settings=settings_snapshot(), updated=threading.Event(), touched=time.time())
            room['rng'] = random.Random(data.get('seed', secrets.randbits(31))).getstate()
            rooms[code] = room
            emit(room, 'created', '战术室已就绪。调整角色控制权与站位后，开始本次演习。')
            threading.Thread(target=runner, args=(code,), daemon=True).start()
            return jsonify(public(room, True))

    @bp.post('/api/ui/rooms/<code>/join')
    def join_room(code):
        data = body()
        sid = session_id()
        if not sid:
            raise Forbidden('请先建立玩家会话')
        with guard:
            room = rooms.get(code.upper())
            if not room:
                raise NotFound('房间不存在或服务已重启')
            if room['local'] and sid != room['host']:
                raise Forbidden('本地演习不接受其他玩家，请创建局域网房间')
            existing = room['members'].get(sid)
            room['members'][sid] = existing or dict(id=sid, name=str(data.get('name', '指挥官'))[:20],
                                                     ready=False, spectator=data.get('spectator', False) is True)
            room['touched'] = time.time()
            emit(room, 'member', f"{room['members'][sid]['name']} 加入了房间")
            return jsonify(public(room, True))

    @bp.get('/api/ui/rooms/<code>')
    def get_room(code):
        with guard:
            room = room_for(code)
            room['touched'] = time.time()
            return jsonify(public(room, True))

    @bp.get('/api/ui/rooms/<code>/events')
    def room_events(code):
        with guard:
            room = room_for(code)
            room['touched'] = time.time()
            last = request.headers.get('Last-Event-ID', request.args.get('after', '0'))
            try:
                after = int(last)
            except ValueError:
                raise BadRequest('无效的事件序号')

        def stream():
            nonlocal after
            # Short, renewable connections avoid retaining idle server threads forever.
            until = time.monotonic() + 25
            while time.monotonic() < until:
                with guard:
                    if code.upper() not in rooms:
                        return
                    history = room['history']
                    if history and after < history[0]['id'] - 1:
                        yield 'event: reset\ndata: {}\n\n'
                        return
                    events = [e for e in history if e['id'] > after]
                    meta = dict(running=room['running'], speed=room['speed'], members=list(room['members'].values()), controls=room['controls'], tournament=tournaments.room_context(room))
                    room['updated'].clear()
                for event in events:
                    after = event['id']
                    yield f"id: {after}\ndata: {json.dumps(dict(event=event, **meta), ensure_ascii=False)}\n\n"
                if not events:
                    yield ': heartbeat\n\n'
                room['updated'].wait(2)
        return Response(stream(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @bp.post('/api/ui/rooms/<code>/control')
    def control_room(code):
        data = body()
        with guard:
            room = room_for(code)
            eng = engine_for(room)
            op = data.get('op')
            if op == 'ready':
                room['members'][session_id()]['ready'] = bool(data.get('ready', True))
            elif op == 'leave':
                if session_id() == room['host']:
                    room['running'] = False
                else:
                    room['members'].pop(session_id(), None)
                    for uid, who in list(room['controls'].items()):
                        if who == session_id():
                            room['controls'][uid] = room['host']
            else:
                room_for(code, True)
                if op == 'assign':
                    uid, who = data.get('uid'), data.get('controller')
                    valid_units = {eng._resolve_uid(e['userId']) for e in eng._battle_unit_entries()}
                    if uid not in valid_units or (who != 'ai' and (who not in room['members'] or room['members'][who]['spectator'])):
                        raise BadRequest('请选择有效角色和控制者')
                    if pending_for(room):
                        raise Conflict('请先处理当前防御反应')
                    room['controls'][eng._summon_group_key(uid)] = who
                    if room.get('tournament_id') and who != 'ai':
                        room['manual'] = True
                    sync_controls(room, eng)
                elif op == 'start':
                    if not all(m['ready'] for m in room['members'].values() if not m['spectator']):
                        raise Conflict('请等待所有玩家准备')
                    result = invoke('prepare_ready', room['battle_id'])
                    room['running'] = True
                    emit(room, 'start', result.get('output', '演习开始'))
                    return jsonify(public(room))
                elif op == 'play':
                    room['running'] = True
                elif op == 'pause':
                    room['running'] = False
                elif op == 'speed':
                    if data.get('speed') not in (1, 2, 4):
                        raise BadRequest('播放速度请选择 1、2 或 4')
                    room['speed'] = data['speed']
                elif op == 'step':
                    room['running'] = False
                    if not ai_step(room):
                        raise Conflict('当前需要玩家行动或防御反应，无法推进 AI')
                    return jsonify(public(room))
                else:
                    raise BadRequest('不支持的房间操作')
            emit(room, 'control', '')
            return jsonify(public(room))

    def action_payload(room, eng, data):
        entry = active(eng)
        if not entry:
            raise Conflict('当前没有行动角色')
        uid = data.get('actor') if (eng._get_state() or {}).get('phase') == 'preparation' else entry['userId']
        if uid not in eng.characters and not eng._unit_il_entry(uid, eng._get_initiative()):
            raise BadRequest('行动角色不存在')
        who = controller(room, eng, uid)
        if who != session_id():
            raise Forbidden('该角色由其他玩家或 AI 控制')
        cmd, target = data.get('command', ''), data.get('target', '')
        if not isinstance(cmd, str) or not isinstance(target, str):
            raise BadRequest('行动和目标必须为字符串')
        physical_ids = {eng._resolve_uid(e['userId']) for e in eng._battle_unit_entries()}
        grave_ids = set(getattr(eng, '_graveyard', {}))
        target_mode = target if target in ('R', 'RP') else ''
        if target_mode:
            target = ''
        if target and target not in physical_ids | grave_ids:
            raise BadRequest('目标单位不存在')
        if not re.fullmatch(r'\.s\d+|\.a s\d+|\.a m [A-P](?:[1-9]|1[0-6])|\.a eat|\.a give|\.i end', cmd):
            raise BadRequest('无效的行动')
        if pending_for(room):
            raise Conflict('请先处理当前防御反应')
        slot_uid = entry['userId']
        payload = dict(player_id=who, action=cmd, target=target, args=target_mode, _ui_target=target or None,
                       restim_mode=room['settings']['DEFAULT_RESTIM_MODE'],
                       allow_failed_reaction=int(room['settings']['ALLOW_FAILED_REACTION']))
        for key in ('skill_name', 'skill_value', 'damage_dice', 'penetration', 'lethality',
                    'count', 'bp_suffix', 'restim_mode', 'allow_failed_reaction'):
            if key in data:
                payload[key] = data[key]
        if cmd == '.a give':
            if not target or eng._battle_team_of(target) != eng._battle_team_of(uid) or eng._resolve_uid(target) == eng._resolve_uid(uid):
                raise BadRequest('请选择其他同队角色')
            payload['action'] += ' ' + (eng._unit_il_entry(target, eng._get_initiative()) or {}).get('name', eng.get_char(target).name)
        if cmd.startswith('.s') or cmd.startswith('.a s'):
            sn = int(cmd.split('s')[-1])
            if sn:
                spell = next((s for s in eng.load_spells(uid) if s['index'] == sn), None)
                if not spell:
                    raise BadRequest('技能不存在')
                info = spell_info(eng, uid, spell)
                if not info['available']:
                    raise BadRequest(info['reason'])
                valid = info['revive_candidates'] or list(dict.fromkeys(
                    t for e in spell.get('effects', []) for t in eng._resolve_effect_targets(uid, target or uid, e)
                    if e.get('type') != 19))
                # A selected unit must actually participate in at least one effect.
                if target and target not in valid:
                    raise BadRequest('该技能不能作用于所选目标')
            elif target and target not in eng._enemy_targets(uid):
                raise BadRequest('请选择可攻击的敌方单位')
        return uid, slot_uid, payload

    def execute(room, eng, data, bid):
        uid, slot_uid, payload = action_payload(room, eng, data)
        if (eng._get_state() or {}).get('phase') == 'preparation':
            if not payload['action'].startswith('.a m '):
                raise BadRequest('准备阶段仅能调整站位')
            coord = payload['action'][5:]
            md = eng._get_map()
            pt = ns['parse_coord'](coord)
            if not pt or not (0 <= pt[0] < md['width'] and 0 <= pt[1] < md['height']):
                raise BadRequest('位置超出地图')
            if coord in md.get('occupants', {}) or md.get('obstacles', {}).get(coord):
                raise BadRequest('该位置已被占用')
            md['occupants'] = {k: v for k, v in md['occupants'].items() if eng._resolve_uid(v) != eng._resolve_uid(uid)}
            md['occupants'][coord] = eng._resolve_uid(uid)
            eng._set_map(md)
            il = eng._get_initiative()
            for e in il:
                if eng._resolve_uid(e['userId']) == eng._resolve_uid(uid):
                    e.update(coord=coord, _anchor_coord=coord)
            eng._set_initiative(il)
            return {'output': f'{eng.get_char(uid).name} 调整站位至 {coord}'}
        eng._manual_attack_bp = data.get('bp_suffix', '')
        try:
            return invoke('submit_action', bid, payload)
        finally:
            eng._manual_attack_bp = ''

    @bp.post('/api/ui/rooms/<code>/preview')
    def preview_action(code):
        data = body()
        with guard, ns['_lock']:
            room = room_for(code)
            eng = engine_for(room)
            if data.get('revision') != room['revision']:
                raise Conflict('战况已更新，请重新选择行动')
            # Run the actual handler on an isolated engine and restore random state.
            # Thus previews cannot charge resources, reveal future dice, or drift rules.
            clone = clone_engine(eng)
            temp = 'preview_' + secrets.token_hex(8)
            ns['_battles'][temp] = clone
            rng = random.getstate()
            random.setstate(room.get('rng', rng))
            before = clean((clone._get_actions(), clone.storage, clone.characters))
            try:
                result = execute(room, clone, data, temp)
                changed = before != clean((clone._get_actions(), clone.storage, clone.characters)) or bool(ns['_pending_reactions'].get(temp))
                if not changed:
                    raise BadRequest(result.get('output', '该行动不可执行'))
                # Dice and outcomes intentionally never leave this dry run.
                return jsonify(valid=True, message='行动可执行；检定与随机费用将在确认后结算',
                               revision=room['revision'])
            finally:
                random.setstate(rng)
                ns['_battles'].pop(temp, None)
                ns['_pending_reactions'].pop(temp, None)

    @bp.post('/api/ui/rooms/<code>/options')
    def action_options(code):
        data = body()
        with guard, ns['_lock']:
            room = room_for(code)
            eng = engine_for(room)
            if data.get('revision') != room['revision']:
                raise Conflict('战况已更新，请重新选择行动')
            entry = active(eng)
            if not entry:
                return jsonify(targets=[], cells=[])
            prep = (eng._get_state() or {}).get('phase') == 'preparation'
            uid = data.get('actor') if prep else entry['userId']
            if not uid or controller(room, eng, uid) != session_id():
                raise Forbidden('不能操作此角色')
            command = data.get('command', '')
            if command == 'move':
                md, cells = eng._get_map(), []
                # Use exactly the engine's validator on throwaway copies. No path
                # or movement-distance rules are reimplemented in the browser.
                for y in range(md['height']):
                    for x in range(md['width']):
                        coord = ns['format_coord'](x, y)
                        if prep:
                            valid = coord not in md['occupants'] and not md.get('obstacles', {}).get(coord)
                        else:
                            clone = clone_engine(eng)
                            rng = random.getstate()
                            try:
                                valid = clone._get_my_actions(uid).get('附加', 0) > 0 and clone._get_my_actions(uid).get('主动', 0) > 0 and ns['action_committed'](clone._move_explicit(uid, coord))
                            finally:
                                random.setstate(rng)
                        if valid:
                            cells.append(coord)
                return jsonify(cells=cells, targets=[], revision=room['revision'])
            if command == '.a give':
                targets = eng._ally_targets(uid, include_self=False)
            elif command == '.s0':
                targets = eng._enemy_targets(uid)
            else:
                match = re.fullmatch(r'\.(?:a )?s(\d+)', command)
                spell = next((s for s in eng.load_spells(uid) if match and s['index'] == int(match.group(1))), None)
                if not spell:
                    raise BadRequest('技能不存在')
                effects = spell.get('effects', [])
                targets = [g['uid'] for e in effects if e.get('type') == 19 for g in eng._revive_candidates(uid, e)]
                if not targets:
                    candidates = [e['userId'] for e in eng._battle_unit_entries()]
                    rng = random.getstate()
                    try:
                        targets = [t for t in candidates if any(t in eng._resolve_effect_targets(uid, t, e) for e in effects)]
                    finally:
                        random.setstate(rng)
            return jsonify(cells=[], targets=targets, revision=room['revision'])

    @bp.post('/api/ui/rooms/<code>/action')
    def submit_ui_action(code):
        data = body()
        with guard, ns['_lock']:
            room = room_for(code)
            rid = str(data.get('request_id', ''))
            if not rid or len(rid) > 100:
                raise BadRequest('缺少有效请求 ID')
            key = session_id() + ':' + rid
            if key in room['requests']:
                return jsonify(public(room))
            if data.get('revision') != room['revision']:
                raise Conflict('战况已更新，请重新选择行动')
            eng = engine_for(room)
            with room_random(room):
                if data.get('choice'):
                    p = pending_for(room)
                    if not p:
                        raise Conflict('当前没有待处理的反应')
                    if controller(room, eng, p.get('def_uid', '')) != session_id():
                        raise Forbidden('此防御反应需要由对应角色的玩家处理')
                    choice = data['choice']
                    if choice not in ('dodge', 'counter', 'block') or not p.get('can_' + choice, choice != 'block'):
                        raise BadRequest('该反应方式不可用')
                    result = {'output': reactions.resolve(room, choice, data.get('bp_suffix', ''))}
                    kind = 'reaction'
                else:
                    result = execute(room, eng, data, room['battle_id'])
                    kind = 'action'
                eng._check_and_mark_end(eng._get_state())
            emit(room, kind, result.get('output', ''))
            response = public(room)
            room['requests'][key] = True
            while len(room['requests']) > 200:
                room['requests'].popitem(last=False)
            room['touched'] = time.time()
            return jsonify(response)

    from ui_reactions import Reactions
    reactions = Reactions(bp, locals())
    app.extensions['combat_ui']['reactions'] = reactions
    from ui_management import Management
    from ui_store import Store
    from ui_tasks import Tasks
    management = Management(bp, locals())
    from ui_settings import Settings
    settings = Settings(bp, locals(), management, instance)
    app.extensions['combat_ui']['settings'] = settings
    store = Store(management.runtime / ('browser.sqlite3' if instance == '8889' else f'browser-{instance}.sqlite3'))
    tasks = Tasks(bp, locals(), management, store)
    app.extensions['combat_ui'].update(management=management, store=store, tasks=tasks)
    from ui_tournaments import Tournaments
    tournaments = Tournaments(bp, locals(), management, store)
    app.extensions['combat_ui']['tournaments'] = tournaments
    from ui_utilities import register_utilities
    register_utilities(bp, locals())
    app.register_blueprint(bp)
