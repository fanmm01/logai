"""Local data editor, atomic source updates, recoverable backups and task snapshots."""
import ast
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import pprint
import secrets
import shutil
import threading
import time
import zipfile

from flask import jsonify, request, Response
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound

ROOT = Path(__file__).resolve().parent
FILES = ('characters_data.py', 'characters_data_pvp.py', 'team_tables.py', 'ai_weights.json', 'ai_weights_pvp.json')


def atomic_write(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.' + secrets.token_hex(5) + '.tmp')
    temp.write_bytes(data)
    os.replace(temp, path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Management:
    def __init__(self, bp, context, root=ROOT):
        self.c, self.root = context, Path(root)
        self.runtime = self.root / '.ui-runtime'
        self.runtime.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.loaded_version = digest((self.root / 'characters_data.py').read_bytes())
        self.training = None
        for path, name, method in [('/data', 'data', 'GET'), ('/data/save', 'save', 'POST'),
                                   ('/backups', 'backups', 'GET'), ('/backups', 'backup', 'POST'),
                                   ('/backups/<bid>/download', 'download', 'GET'),
                                   ('/backups/<bid>/restore', 'restore', 'POST')]:
            bp.add_url_rule('/api/ui' + path, 'manage_' + name, getattr(self, name), methods=[method])

    def require_local(self):
        if request.remote_addr not in (None, '127.0.0.1', '::1'):
            raise Forbidden('数据管理仅允许本机访问')
        if not self.c['session_id']():
            raise Forbidden('请先建立会话')

    def module(self):
        return self.c['ns']['characters_data_pvp']

    def data(self):
        self.require_local()
        with self.lock:
            return self.describe()

    def describe(self):
        current = digest((self.root / 'characters_data.py').read_bytes())
        if current != self.loaded_version:
            with self.c['guard'], self.c['ns']['_lock']:
                self.reload()
        import battle_engine as be
        module = self.module()
        import team_tables
        nodes = ast.parse((self.root / 'characters_data.py').read_text(encoding='utf-8-sig'))
        reserved = []
        for n in nodes.body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict):
                for k, v in zip(n.value.keys, n.value.values):
                    if isinstance(k, ast.Constant) and k.value == 'serial' and isinstance(v, ast.Constant):
                        reserved.append(v.value)
        enums = {'type': {str(i): label for i, label in enumerate(
            ['', '伤害', '护盾', '恢复', '增益', '召唤', '制造', '触发法术', '领域', '生命触发',
             '延迟恢复', '战意', '特殊防护', '复生', '计数器', '特殊15', '特殊16', '切换阶段', '位移', '复活']) if i and i not in (15, 16)},
            '客体': {'1': '自身', '2': '其他友方单体', '3': '其他友方全体', '4': '敌方单体', '5': '敌方全体',
                   '12': '任意友方', '13': '自身与友方', '14': '自身与敌方单体', '15': '自身与敌方全体',
                   '24': '友方或敌方单体', '25': '友方单体与敌方群体', '35': '领域全体', '45': '敌方群体',
                   '124': '任意单体', '125': '任意友方与敌方群体', '135': '全场', 'R': '随机单位', 'RP': '随机角色'},
            'timing': {'1': '被动', '2': '主动作', '3': '附加动作', '12': '被动／主动', '23': '主动／附加', '123': '全部'},
            'category': {str(i): x for i, x in enumerate(['', '伤害', '护盾', '恢复', '增益', '召唤', '制造', '触发', '领域', '特殊']) if i},
            '辅助效果': {k: k for k in getattr(be, 'AUX_NAME_TO_CODE', {})},
            '召唤物模板': {k: k for k in module.SUMMON_TEMPLATES},
            '制造物模板': {k: k for k in module.ITEM_TEMPLATES}}
        enums['_mp_formula'] = {'': '固定费用', 'summon_count_ceil_half': '召唤数除二向上取整', 'ceil_2d6_div_2': '2d6 除二向上取整'}
        enums['可叠加'] = {'0': '不叠加／同名刷新', '1': '允许叠加', '2': '允许叠加'}
        enums['成功率奖惩骰'] = {str(i): ('无奖惩' if i == 0 else ('奖励骰' if i > 0 else '惩罚骰') + f' ×{abs(i)}') for i in range(-3, 4)}
        enums['phase'] = {'0': '所有阶段', '1': '阶段一', '2': '阶段二', '3': '阶段三', '4': '阶段四', '99': '保留／不启用'}
        enums['weapon_state'] = {'staff': '杖', 'bow': '弓', 'sword': '剑'}
        enums['variant'] = {'fire_rat_cloak': '火鼠裘防护'}
        enums['友方行为'] = {'': '默认', 'damage_and_heal': '伤害与延迟恢复'}
        for key in ('可闪避性', '可反击性', '可格挡性', '可贯穿性', '领域中心跟随', 'default_persist',
                    'GLOBAL_RANGE_OVERRIDE', '可反击', '可格挡', '可反应', '忽略全局奖惩骰', 'ally_safe'):
            enums[key] = {'0': '否', '1': '是'}
        fields = {key: {} for key in ('character', 'spell', 'effect', 'attrs', 'str_attrs', 'entry')}
        for char in module.__dict__.values():
            if not isinstance(char, dict) or 'serial' not in char or 'attrs' not in char:
                continue
            for key, value in char.items():
                fields['character'].setdefault(key, value)
            for group in ('attrs', 'str_attrs'):
                for key, value in char.get(group, {}).items():
                    fields[group].setdefault(key, value)
            for spell in char.get('spells', []):
                for key, value in spell.items():
                    fields['spell'].setdefault(key, value)
                for effect in spell.get('effects', []):
                    for key, value in effect.items():
                        fields['effect'].setdefault(key, value)
            for item in char.get('inventory', []):
                for key, value in item.items():
                    fields['entry'].setdefault(key, value)
        return jsonify(characters=module.ALL_CHARACTERS, summons=module.SUMMON_TEMPLATES,
                       items=module.ITEM_TEMPLATES, version=digest((self.root / 'characters_data.py').read_bytes()),
                       reserved=reserved, enums=enums, fields=fields,
                       presets={'2v2': team_tables.TEAM_TABLES_2V2, '3v3': team_tables.TEAM_TABLES_3V3},
                       settings={'GLOBAL_RANGE_OVERRIDE': getattr(module, 'GLOBAL_RANGE_OVERRIDE', 0)},
                       needs_training=self.runtime.joinpath('needs-training').exists())

    def make_backup(self, reason='manual'):
        folder = self.runtime / 'backups'
        folder.mkdir(exist_ok=True)
        bid = str(time.time_ns()) + '-' + secrets.token_hex(3)
        hashes = {}
        with zipfile.ZipFile(folder / (bid + '.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
            for name in FILES:
                path = self.root / name
                if path.exists():
                    raw = path.read_bytes()
                    hashes[name] = digest(raw)
                    z.writestr(name, raw)
            z.writestr('manifest.json', json.dumps(dict(id=bid, time=time.time(), reason=reason, files=hashes)))
        return bid

    def backups(self):
        self.require_local()
        rows = []
        for path in sorted((self.runtime / 'backups').glob('*.zip'), reverse=True):
            with zipfile.ZipFile(path) as z:
                rows.append(json.loads(z.read('manifest.json')))
        return jsonify(backups=rows)

    def backup(self):
        self.require_local()
        self.c['body']()
        with self.lock:
            return jsonify(id=self.make_backup())

    def backup_path(self, bid):
        if not all(ch in '0123456789abcdef-' for ch in bid):
            raise NotFound('备份不存在')
        path = self.runtime / 'backups' / (bid + '.zip')
        if not path.exists():
            raise NotFound('备份不存在')
        return path

    def download(self, bid):
        self.require_local()
        return Response(self.backup_path(bid).read_bytes(), mimetype='application/zip',
                        headers={'Content-Disposition': f'attachment; filename=backup-{bid}.zip'})

    def reload(self):
        module = self.module()
        cache = Path(importlib.util.cache_from_source(str(self.root / 'characters_data.py')))
        if self.root.resolve() in cache.resolve().parents:
            cache.unlink(missing_ok=True)
        raw = (self.root / 'characters_data.py').read_bytes()
        exec(compile(raw, str(self.root / 'characters_data.py'), 'exec'), module.__dict__)
        self.loaded_version = digest(raw)
        import battle_engine
        battle_engine._SUMMON_TEMPLATES = module.SUMMON_TEMPLATES
        # New rooms obtain new data; existing engines keep their own templates.
        self.c['ns']['_SUMMON_TEMPLATES'] = module.SUMMON_TEMPLATES
        self.c['ns']['_Q_TABLE_PVP_CACHE'] = None

    def restore(self, bid):
        self.require_local()
        self.c['body']()
        with self.lock, self.c['guard'], self.c['ns']['_lock']:
            with zipfile.ZipFile(self.backup_path(bid)) as z:
                manifest = json.loads(z.read('manifest.json'))
                payload = {k: z.read(k) for k in manifest['files'] if k in FILES}
                if any(digest(v) != manifest['files'][k] for k, v in payload.items()):
                    raise BadRequest('备份校验失败')
                for name, value in payload.items():
                    if name.endswith('.py'):
                        compile(value, name, 'exec')
                before = self.make_backup('before restore')
                for name, value in payload.items():
                    atomic_write(self.root / name, value)
            self.reload()
            return jsonify(ok=True, backup=before)

    def validate(self, characters, summons):
        if not isinstance(characters, list) or not characters:
            raise BadRequest('至少保留一个角色')
        serials = set()
        from battle_engine import CombatEngine, CN_NUMS, CAT_LETTERS
        for ch in characters:
            if not isinstance(ch, dict) or not ch.get('name') or not isinstance(ch.get('serial'), str):
                raise BadRequest('角色必须有名称和唯一编号')
            if not __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{0,31}', ch['serial']) or ch['serial'] in serials:
                raise BadRequest('角色编号重复或格式不正确')
            serials.add(ch['serial'])
            if not isinstance(ch.get('attrs'), dict) or any(type(v) not in (int, float, bool) for v in ch['attrs'].values()):
                raise BadRequest('数值属性必须为数字')
            if len(ch.get('spells', [])) >= len(CN_NUMS):
                raise BadRequest(f'当前引擎最多支持 {len(CN_NUMS)-1} 个技能')
            for s in ch.get('spells', []):
                if not s.get('name') or len(s.get('effects', [])) > len(CAT_LETTERS):
                    raise BadRequest('技能需有名称，效果数量不能超过引擎支持数量')
                for e in s.get('effects', []):
                    if type(e.get('type')) is not int or e['type'] not in (*range(1, 15), 17, 18, 19):
                        raise BadRequest('请选择有效效果类型')
                    if e.get('召唤物模板') and e['召唤物模板'] not in summons:
                        raise BadRequest('召唤物模板不存在')
            try:
                eng = CombatEngine()
                self.module().load_character_to_engine(eng, ch, 'validation')
            except Exception as exc:
                raise BadRequest('角色无法加载：' + str(exc)) from exc

    def save(self):
        self.require_local()
        data = self.c['body']()
        with self.lock, self.c['guard'], self.c['ns']['_lock']:
            path = self.root / 'characters_data.py'
            raw = path.read_bytes()
            if data.get('version') != digest(raw):
                raise Conflict('数据已被修改，请重新加载后编辑')
            characters = data.get('characters')
            try:
                json.dumps(data, allow_nan=False)
            except (ValueError, TypeError) as exc:
                raise BadRequest('数值必须是有限数字') from exc
            summons = data.get('summons', self.module().SUMMON_TEMPLATES)
            self.validate(characters, summons)
            source = raw.decode('utf-8-sig')
            tree = ast.parse(source)
            nodes = {n.targets[0].id: n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
            active = [n.id for n in nodes['ALL_CHARACTERS'].value.elts]
            by_serial = {getattr(self.module(), name)['serial']: name for name in active}
            lines = source.splitlines(keepends=True)
            edits, names, additions = [], [], []
            for ch in characters:
                name = by_serial.get(ch['serial'], 'CUSTOM_' + ch['serial'])
                names.append(name)
                value = name + ' = ' + pprint.pformat(ch, width=110, sort_dicts=False) + '\n'
                if name in nodes:
                    n = nodes[name]
                    edits.append((n.lineno-1, n.end_lineno, value))
                else:
                    additions.append(value)
            for name in set(active) - set(names):
                n = nodes[name]
                edits.append((n.lineno-1, n.end_lineno, ''))
            for name, val in [('SUMMON_TEMPLATES', summons), ('ITEM_TEMPLATES', data.get('items', self.module().ITEM_TEMPLATES))]:
                n = nodes[name]
                later = []
                for assignment in tree.body:
                    if not isinstance(assignment, ast.Assign) or len(assignment.targets) != 1:
                        continue
                    target = assignment.targets[0]
                    if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                            and target.value.id == name and isinstance(target.slice, ast.Constant)):
                        later.append((assignment, target.slice.value))
                late_keys = {key for _, key in later}
                base = {key: value for key, value in val.items() if key not in late_keys}
                edits.append((n.lineno-1, n.end_lineno, name + ' = ' + pprint.pformat(base, width=110, sort_dicts=False) + '\n'))
                for assignment, key in later:
                    replacement = (f'{name}[{key!r}] = ' + pprint.pformat(val[key], width=110, sort_dicts=False) + '\n') if key in val else ''
                    edits.append((assignment.lineno-1, assignment.end_lineno, replacement))
            n = nodes['ALL_CHARACTERS']
            edits.append((n.lineno-1, n.end_lineno, '\n'.join(additions) + '\nALL_CHARACTERS = [' + ', '.join(names) + ']\n'))
            if 'settings' in data:
                setting = data['settings'].get('GLOBAL_RANGE_OVERRIDE')
                if type(setting) is not int or setting not in (0, 1):
                    raise BadRequest('全局射程覆盖应为 0 或 1')
                n = nodes['GLOBAL_RANGE_OVERRIDE']
                edits.append((n.lineno-1, n.end_lineno, f'GLOBAL_RANGE_OVERRIDE = {setting}\n'))
            for start, end, val in sorted(edits, reverse=True):
                lines[start:end] = [val]
            updated = ''.join(lines).encode('utf-8')
            compile(updated, str(path), 'exec')
            bid = self.make_backup('before save')
            atomic_write(path, updated)
            self.reload()
            self.runtime.joinpath('needs-training').touch()
            return jsonify(ok=True, version=digest(updated), backup=bid, needs_training=True)

    def snapshot_task(self, jid):
        dest = self.runtime / 'jobs' / jid
        dest.mkdir(parents=True, exist_ok=True)
        for source in self.root.glob('*.py'):
            if source.name != 'autocombat_backend.py':
                shutil.copy2(source, dest / source.name)
        for name in ('ai_weights.json',):
            if (self.root / name).exists():
                shutil.copy2(self.root / name, dest / name)
        return dest
