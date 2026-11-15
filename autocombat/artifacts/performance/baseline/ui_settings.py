"""Browser settings API; runtime settings have a separate lifecycle from character data."""
import sys

from flask import jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from combat_settings import (SettingsStore, configure, metadata, READONLY, DEFAULTS,
                             file_path, instance_name)


class Settings:
    def __init__(self, bp, context, management, instance=None):
        self.c, self.management = context, management
        self.instance = instance or instance_name()
        self.store = SettingsStore(management.root, self.instance)
        configure(self.store)
        management.settings = self
        bp.add_url_rule('/api/ui/settings', 'settings_get', self.get, methods=['GET'])
        bp.add_url_rule('/api/ui/settings', 'settings_save', self.save, methods=['POST'])

    def can_manage(self):
        try:
            self.management.require_local()
            return True
        except Forbidden:
            return False

    def response(self):
        values = self.store.read()
        managed = self.can_manage()
        fields = metadata()
        if not managed:
            # Shared defaults are public; local paths and access policy are private.
            fields = {k: v for k, v in fields.items() if k not in ('LOG_FILE', 'RESULT_FILE', 'BRACKET_FILE')}
        current = {key: values[key] for key in fields}
        runtime = dict(instance=self.instance, web_address=request.host_url.rstrip('/'))
        if managed:
            runtime['settings_file'] = str(self.store.path)
            runtime['files'] = {key: file_path(key) for key in ('LOG_FILE', 'RESULT_FILE', 'BRACKET_FILE')}
            runtime['web_port_argument'] = self._port_override()
        return dict(values=current, fields=fields, version=self.store.version(values), can_manage=managed, runtime=runtime)

    @staticmethod
    def _port_override():
        if '--port' in sys.argv:
            index = sys.argv.index('--port')
            return sys.argv[index + 1] if index + 1 < len(sys.argv) else None
        return None

    def get(self):
        return jsonify(self.response())

    def save(self):
        self.management.require_local()
        data = self.c['body']()
        incoming = data.get('values')
        if not isinstance(incoming, dict) or any(key not in DEFAULTS for key in incoming):
            raise BadRequest('配置必须是已知字段组成的对象')
        if not isinstance(data.get('version'), str): raise BadRequest('缺少配置版本，请重新加载')
        with self.management.lock, self.store.lock:
            old = self.store.read()
            if data['version'] != self.store.version(old): raise Conflict('配置已被其他页面修改，请重新加载后保存')
            if any(key in incoming and incoming[key] != old[key] for key in READONLY):
                raise BadRequest('只读配置不能通过页面修改')
            values = {**old, **incoming}
            try:
                from combat_settings import validate
                # Include disabled draft definitions; they remain valid preset references.
                import ast
                tree = ast.parse((self.management.root / 'characters_data.py').read_text(encoding='utf-8'))
                definitions = self.management.character_definitions(tree)
                serials = {ast.literal_eval(v) for node in definitions.values()
                           for k, v in zip(node.value.keys, node.value.values)
                           if isinstance(k, ast.Constant) and k.value == 'serial'}
                validate(values, serials)
            except (ValueError, TypeError, SyntaxError) as exc:
                raise BadRequest(str(exc)) from exc
            self.management.make_backup('before settings save')
            self.store.write(values, data['version'], serials)
            # Apply log severity to subsequent records, including already-imported adapters.
            for name in ('ai_battle', 'autocombat_backend'):
                module = sys.modules.get(name)
                if module:
                    for handler_name, key in [('fh', 'FILE_LOG_LEVEL'), ('ch', 'CONSOLE_LOG_LEVEL'),
                                              ('buf_handler', 'WEB_LOG_LEVEL'), ('simple_handler', 'CONSOLE_LOG_LEVEL')]:
                        handler = getattr(module, handler_name, None)
                        if handler: handler.setLevel(values[key])
            return jsonify(self.response())
