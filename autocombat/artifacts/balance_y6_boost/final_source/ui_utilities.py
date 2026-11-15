"""Authenticated browser utilities for mode2 combat and battle administration."""
import copy
from flask import jsonify
from werkzeug.exceptions import BadRequest, Conflict, Forbidden


def register_utilities(bp, c):
    @bp.post('/api/ui/rooms/<code>/utility')
    def utility(code):
        data = c['body']()
        with c['guard'], c['ns']['_lock']:
            room = c['room_for'](code)
            eng = c['engine_for'](room)
            op, uid = data.get('op'), data.get('uid')
            host_ops = ('save', 'restore', 'attribute', 'add', 'remove', 'end', 'reload', 'ai')
            if op in host_ops:
                c['room_for'](code, True)
                if room.get('tournament_id') and op not in ('ai',):
                    raise Forbidden('赛事观战只能接管正常战斗操作，不能修改赛程或战斗属性')
            elif uid not in eng.characters or c['controller'](room, eng, uid) != c['session_id']():
                raise Forbidden('只能操作自己控制的单位')
            if c['pending_for'](room):
                raise Conflict('请先处理当前反应；停止自动反应请使用独立控件')
            output = ''
            if op in ('transform', 'untransform'):
                eng.process_command(uid, '.hs' if op == 'transform' else '.unh')
                output = f'{eng.get_char(uid).name} 已' + ('变身' if op == 'transform' else '解除变身')
            elif op == 'end_effect':
                output = eng.end_skill_effects(uid, int(data.get('skill', 0)))
            elif op in ('use_item', 'transfer_item'):
                entry = c['active'](eng)
                if not entry or eng._resolve_uid(entry['userId']) != uid:
                    raise Conflict('仅当前行动单位可以使用或转交物品')
                acts = eng._get_my_actions(uid)
                if acts.get('主动', 0) <= 0 or acts.get('附加', 0) <= 0:
                    raise BadRequest('行动次数不足')
                index = data.get('index')
                inventory = eng.get_char(uid).inventory
                if type(index) is not int or not 0 <= index < len(inventory):
                    raise BadRequest('请选择有效物品')
                target = data.get('target') or uid
                if target not in eng._ally_targets(uid, include_self=True):
                    raise BadRequest('请选择同队目标')
                item = inventory[index]
                if op == 'transfer_item':
                    if target == uid:
                        raise BadRequest('请选择其他友方单位')
                    transferred = copy.deepcopy(item)
                    transferred['count'] = 1
                    eng.get_char(target).inventory.append(transferred)
                    item['count'] -= 1
                    if item['count'] <= 0:
                        inventory.pop(index)
                    output = f'{eng.get_char(uid).name} 将 {transferred["item"]} 交给 {eng.get_char(target).name}'
                else:
                    output, success = eng._consume_inventory_item(uid, index, item['item'], target)
                    if not success:
                        raise BadRequest(output)
                eng._consume_action(uid, '附加')
            elif op == 'save':
                room['checkpoint'] = c['clone_engine'](eng)
                output = '已保存本场战斗检查点'
            elif op == 'restore':
                if 'checkpoint' not in room:
                    raise BadRequest('尚未保存检查点')
                c['ns']['_battles'][room['battle_id']] = c['clone_engine'](room['checkpoint'])
                room['running'] = False
                for key in list(room.get('auto_reactions', {})):
                    c['reactions'].stop(room, key)
                output = '已恢复检查点，自动推进已暂停'
            elif op == 'attribute':
                name, value = data.get('name'), data.get('value')
                if uid not in eng.characters or name not in ('HP', 'MP', 'SAN') or type(value) is not int:
                    raise BadRequest('请选择角色与 HP／MP／SAN 整数值')
                if name == 'HP':
                    eng._set_combat_hp(uid, value)
                else:
                    eng.get_char(uid).set_attr('魔力' if name == 'MP' else '理智', value)
                output = f'{eng.get_char(uid).name} 的 {name} 已调整为 {value}'
            elif op in ('add', 'remove'):
                if op == 'remove':
                    matches = [e for e in eng._get_initiative() if getattr(eng.get_char(e['userId']), 'serial', '') == data.get('serial')]
                    if len(matches) != 1:
                        raise BadRequest('镜像角色请通过 HP 调整指定单位，不支持按编号模糊移除')
                result = c['invoke']('add_character' if op == 'add' else 'remove_character', room['battle_id'],
                                     dict(serial=data.get('serial'), team=data.get('team', 'Y')))
                output = result.get('output', result.get('message', '战斗名单已更新'))
            elif op == 'ai':
                if uid not in eng.characters:
                    raise BadRequest('请选择单位')
                overrides = data.get('overrides')
                if not isinstance(overrides, dict):
                    raise BadRequest('策略格式不正确')
                for stage in ('main', 'addi', 'react'):
                    values = overrides.get(stage, [])
                    if not isinstance(values, list):
                        raise BadRequest('策略应为分组列表')
                eng._ai_overrides[uid] = overrides
                eng._ai_override_state[uid] = {}
                output = f'{eng.get_char(uid).name} 的 AI 策略已更新'
            elif op == 'end':
                st = eng._get_state()
                st['phase'] = 'ended'
                eng._set_state(st)
                room['running'] = False
                output = '房主已结束本场战斗'
            elif op == 'reload':
                c['management'].reload()
                output = '角色数据已重载，新的战斗使用最新数据'
            else:
                raise BadRequest('不支持的操作')
            c['emit'](room, 'utility', str(output))
            return jsonify(c['public'](room))
