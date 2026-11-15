"""Browser reaction policies. Each tick settles one reaction, allowing cancellation."""
from flask import jsonify
from werkzeug.exceptions import BadRequest, Conflict, Forbidden


class Reactions:
    def __init__(self, bp, context):
        self.c = context
        bp.add_url_rule('/api/ui/rooms/<code>/auto-reaction', 'auto_reaction', self.configure, methods=['POST'])

    def key(self, eng, uid):
        return eng._summon_group_key(eng._resolve_uid(uid))

    def state(self, room, eng, uid):
        key = self.key(eng, uid)
        p = room.get('auto_reactions', {}).get(key, {})
        return dict(enabled=p.get('remaining', 0) > 0, choice=p.get('choice', 'dodge'),
                    remaining=p.get('remaining', 0), revision=room.get('auto_versions', {}).get(key, 0))

    def stop(self, room, key):
        room.setdefault('auto_reactions', {}).pop(key, None)
        versions = room.setdefault('auto_versions', {})
        versions[key] = versions.get(key, 0) + 1

    def clean(self, room, eng):
        for key, p in list(room.get('auto_reactions', {}).items()):
            group = eng._get_summon_group(key)
            alive = bool(eng._get_alive_summon_group_members(key)) if group else (eng._get_combat_hp(key) or 0) > 0
            if (eng._get_state().get('phase') == 'ended'
                    or self.c['controller'](room, eng, key) != p['owner'] or not alive):
                self.stop(room, key)

    def configure(self, code):
        c = self.c
        data = c['body']()
        with c['guard'], c['ns']['_lock']:
            room = c['room_for'](code)
            eng = c['engine_for'](room)
            uid = data.get('uid')
            ids = {eng._resolve_uid(e['userId']) for e in eng._battle_unit_entries() + eng._get_initiative()}
            if not isinstance(uid, str) or uid not in ids:
                raise BadRequest('请选择有效角色')
            if c['controller'](room, eng, uid) != c['session_id']():
                raise Forbidden('只能设置自己控制单位的自动反应')
            rid = data.get('request_id')
            if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
                raise BadRequest('缺少请求 ID')
            token = c['session_id']() + ':auto:' + rid
            if token in room['requests']:
                return jsonify(c['public'](room))
            key = self.key(eng, uid)
            if data.get('op') == 'start':
                if eng._get_state().get('phase') not in ('active', 'preparation'):
                    raise Conflict('战斗已结束')
                if (eng._get_combat_hp(uid) or 0) <= 0:
                    raise BadRequest('该单位当前无法反应')
                if data.get('revision') != self.state(room, eng, uid)['revision']:
                    raise Conflict('设置已更新，请重新开启自动反应')
                choice, count = data.get('choice'), data.get('count')
                if choice not in ('dodge', 'counter', 'block') or type(count) is not int or not 1 <= count <= 999:
                    raise BadRequest('请选择反应方式与 1–999 次反应')
                if choice == 'block' and not eng.get_char(uid).get_attr('可格挡', 0):
                    raise BadRequest('该单位不能格挡')
                self.stop(room, key)
                room['auto_reactions'][key] = dict(owner=c['session_id'](), choice=choice, remaining=count)
                output = f'{eng.get_char(uid).name} 已开启自动反应 ×{count}'
            elif data.get('op') == 'stop':
                self.stop(room, key)
                output = f'{eng.get_char(uid).name} 已停止自动反应，后续反应请手动选择'
            else:
                raise BadRequest('请选择开启或停止')
            room['requests'][token] = True
            while len(room['requests']) > 200:
                room['requests'].popitem(last=False)
            c['emit'](room, 'auto_reaction', output)
            return jsonify(c['public'](room))

    def resolve(self, room, choice, bp=''):
        c = self.c
        eng, bid = c['engine_for'](room), room['battle_id']
        original = c['ns']['_pending_reactions'].get(bid)
        queue = list(original) if isinstance(original, list) else [original]
        p, rest = queue[0], queue[1:]
        if not p:
            raise Conflict('没有待处理反应')
        defeated = (eng._get_combat_hp(p.get('def_uid', '')) or 0) <= 0
        if not defeated and (choice not in ('dodge', 'counter', 'block') or not p.get('can_' + choice, choice != 'block')):
            raise BadRequest('该反应不可用')
        actor = c['active'](eng)
        slot = actor['userId'] if actor else None
        lines = list(p.get('prefix_lines', [])) if p.get('restim_mode') == 1 and p.get('reaction_type') == 'spell' else []
        if bp:
            if bp not in ('b', 'b2', 'b3', 'p', 'p2', 'p3'):
                raise BadRequest('无效的反应奖惩骰')
            for key in ('def_bp_dodge', 'def_bp_counter', 'def_bp_block'):
                p[key] = c['ns']['_calc_net_bp']([], '', None, [p.get(key, p.get('def_bp', '')), bp])
        c['ns']['_pending_reactions'].pop(bid, None)
        eng._manual_reaction_bp = bp
        try:
            try:
                resolver = eng.resolve_spell_reaction if p.get('reaction_type') == 'spell' else eng.resolve_reaction
                if (eng._get_combat_hp(p.get('def_uid', '')) or 0) <= 0:
                    rows = ['目标已阵亡，剩余攻击不再重复结算']
                else:
                    _, _, rows = resolver(p, choice)
                lines.extend(rows)
            except c['ns']['ReactionNeeded'] as nxt:
                nxt.data.update(battle_id=bid, restim_mode=p.get('restim_mode', 0))
                rest.insert(0, nxt.data)
                lines.extend(nxt.data.get('prefix_lines', []))
        except Exception:
            c['ns']['_pending_reactions'][bid] = original
            raise
        finally:
            eng._manual_reaction_bp = ''
        if rest:
            c['ns']['_pending_reactions'][bid] = rest
        else:
            current = c['active'](eng)
            if current and current['userId'] == slot and eng._get_my_actions(slot).get('主动', 0) <= 0:
                out = eng._end_turn(slot)
                if isinstance(out, str):
                    lines.append(out)
        eng._check_and_mark_end(eng._get_state())
        return '\n'.join(lines)

    def step(self, room):
        c = self.c
        eng = c['engine_for'](room)
        p = c['pending_for'](room)
        if not p or eng._get_state().get('phase') != 'active':
            return False
        uid = eng._resolve_uid(p.get('def_uid', ''))
        if (eng._get_combat_hp(uid) or 0) <= 0:
            with c['ns']['_lock'], c['room_random'](room):
                c['emit'](room, 'reaction', self.resolve(room, 'dodge'))
            return True
        key = self.key(eng, uid)
        policy = room.get('auto_reactions', {}).get(key)
        if not policy or c['controller'](room, eng, uid) != policy['owner']:
            return False
        if not p.get('can_' + policy['choice'], policy['choice'] != 'block'):
            self.stop(room, key)
            c['emit'](room, 'auto_reaction', '预设反应不可用，已停止自动反应，请手动选择')
            return True
        with c['ns']['_lock'], c['room_random'](room):
            output = self.resolve(room, policy['choice'])
            policy['remaining'] -= 1
            if policy['remaining'] <= 0:
                self.stop(room, key)
            c['emit'](room, 'auto_reaction', output + f'\n自动反应剩余 {policy["remaining"]} 次')
        return True
