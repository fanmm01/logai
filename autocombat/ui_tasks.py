"""Isolated background tasks with pinned source/weights and private exports."""
import csv
import io
import json
import os
import random
import secrets
import subprocess
import sys
import threading
import time

from flask import jsonify, request, Response
from werkzeug.exceptions import BadRequest, Conflict, NotFound
from ui_management import atomic_write, digest
from combat_settings import value as setting, snapshot as settings_snapshot, settings_scope, valid_season


class Tasks:
    def __init__(self, bp, context, management, store):
        self.c, self.management, self.store = context, management, store
        self.lock, self.processes = threading.RLock(), {}
        for job in store.list('task'):
            if job['status'] == 'running':
                job.update(status='failed', message='服务重启，任务已中断；原权重未替换')
                store.put('task', job)
        for path, name, method in [('/simulations', 'start', 'POST'), ('/simulations/<jid>', 'get', 'GET'),
                                   ('/simulations/<jid>/cancel', 'cancel', 'POST'),
                                   ('/simulations/<jid>/export', 'export', 'GET'),
                                   ('/training', 'train', 'POST'), ('/training', 'training_status', 'GET')]:
            bp.add_url_rule('/api/ui' + path, 'tasks_' + name, getattr(self, name), methods=[method])

    def params(self, data):
        mode = data.get('mode', 'single')
        if mode not in ('single', 'verbose', 'table', 'prelim'):
            raise BadRequest('不支持的模拟模式')
        defaults = {'single': setting('SIM_SINGLE_COUNT'), 'verbose': 1, 'table': setting('SIM_TABLE_COUNT'), 'prelim': setting('SIM_PRELIM_COUNT')}
        n = data.get('n', defaults[mode])
        if type(n) is not int or not 1 <= n <= 1000:
            raise BadRequest('模拟次数必须为 1–1000')
        seed, season = data.get('seed', random.randrange(2**31)), data.get('season', setting('SEASON_AVG_STATUS'))
        if type(seed) is not int or not 0 <= seed < 2**31 or not valid_season(season):
            raise BadRequest('种子应为非负整数，赛季状态为 0–100 或随机')
        params = dict(mode=mode, n=1 if mode == 'verbose' else n, seed=seed, season=season, map_size=data.get('map_size', setting('DEFAULT_MAP_SIZE')))
        valid = {c['serial'] for c in self.management.module().ALL_CHARACTERS}
        if mode in ('single', 'verbose'):
            self.c['valid_teams'](data)
            params.update(team_a=data['team_a'], team_b=data['team_b'])
        else:
            fmt = data.get('format', setting('SIM_DEFAULT_FORMAT'))
            if fmt not in ('1v1', '2v2', '3v3'):
                raise BadRequest('请选择有效赛制')
            params['format'] = fmt
            if mode == 'table':
                from team_tables import TEAM_TABLES_2V2, TEAM_TABLES_3V3
                teams = data.get('teams') or ([[s] for s in sorted(valid)] if fmt == '1v1' else
                                             TEAM_TABLES_2V2 if fmt == '2v2' else TEAM_TABLES_3V3)
                if not data.get('teams'):
                    teams = [team for team in teams if all(s in valid for s in team)]
                if not isinstance(teams, list) or len(teams) < 2:
                    raise BadRequest('至少需要两个有效小队')
                for team in teams:
                    if not isinstance(team, list) or not team or len(set(team)) != len(team) or any(s not in valid for s in team):
                        raise BadRequest('小队含重复、已禁用或已删除角色，请检查预设')
                params['teams'] = teams
            else:
                formats = data.get('formats', [fmt])
                if not isinstance(formats, list) or not formats or any(f not in ('1v1', '2v2', '3v3') for f in formats):
                    raise BadRequest('请选择预赛赛制')
                from team_tables import TEAM_TABLES_2V2, TEAM_TABLES_3V3
                pools = {'1v1': [[s] for s in valid], '2v2': TEAM_TABLES_2V2, '3v3': TEAM_TABLES_3V3}
                for requested in formats:
                    if sum(all(s in valid for s in team) for team in pools[requested]) < 2:
                        raise BadRequest(f'{requested} 预赛至少需要两支由启用角色组成的有效小队')
                workers = data.get('workers', setting('PRELIM_WORKERS'))
                if type(workers) is not int or not 1 <= workers <= 32:
                    raise BadRequest('并发数应为 1–32')
                params.update(formats=formats, workers=workers)
        # Ignore the retired UI option sent by older browser pages.
        params['fast'] = False
        if mode == 'single':
            workers = data.get('workers', min(8, os.cpu_count() or 1))
            if type(workers) is not int or not 1 <= workers <= 32:
                raise BadRequest('并发数应为 1–32')
            params['workers'] = workers
        return params

    def start(self):
        if not self.c['session_id']():
            raise BadRequest('请先建立会话')
        data = self.c['body']()
        return jsonify(self.launch(self.params(data)))

    def launch(self, params):
        with self.lock, self.management.lock:
            if params['mode'] == 'train':
                from ai_trainer import normalize_training_targets
                enabled = [ch['serial'] for ch in self.management.module().ALL_CHARACTERS]
                try:
                    targets = normalize_training_targets(params.get('target_serials'), enabled)
                except ValueError as exc:
                    raise BadRequest(str(exc)) from exc
                if len(self.management.module().ALL_CHARACTERS) < 6:
                    raise BadRequest('默认训练包含 3 对 3 抽样，需要至少启用 6 名角色')
                params = {**params, 'enabled_serials': enabled, 'target_serials': targets}
            else:
                params = self.params(params)
            params = {**params, 'settings': settings_snapshot()}
            if any(j['status'] == 'running' for j in self.store.list('task')):
                raise Conflict('已有任务正在运行，请等待或取消模拟')
            jid = secrets.token_hex(8)
            folder = self.management.snapshot_task(jid)
            job = dict(id=jid, owner=self.c['session_id'](), status='running', completed=0, params=params,
                       result=None, logs=[], created=time.time(), data_version=digest((folder/'characters_data.py').read_bytes()))
            self.store.put('task', job)
            threading.Thread(target=self.run, args=(jid,), daemon=True).start()
            return job

    def run(self, jid):
        folder = self.management.runtime / 'jobs' / jid
        job = self.store.get('task', jid)
        error_path = folder / 'stderr.log'
        try:
            with error_path.open('w', encoding='utf-8') as err:
                proc = subprocess.Popen([sys.executable, '-u', str(folder/'ui_task_worker.py')], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=err, text=True, encoding='utf-8', cwd=folder,
                    start_new_session=os.name != 'nt', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
                with self.lock:
                    self.processes[jid] = proc
                    if self.store.get('task', jid)['status'] == 'cancelled':
                        proc.terminate()
                proc.stdin.write(json.dumps(job['params']) + '\n')
                proc.stdin.close()
                last_save = time.monotonic()
                for line in proc.stdout:
                    row = json.loads(line)
                    with self.lock:
                        if self.store.get('task', jid)['status'] == 'cancelled':
                            continue
                        if 'log' in row or 'logs' in row:
                            job['logs'].extend(row.get('logs', [row['log']] if 'log' in row else []))
                            if job['params']['mode'] != 'verbose':
                                job['logs'] = job['logs'][-3000:]
                        else:
                            job.update(row)
                        if time.monotonic() - last_save >= 1:
                            self.store.put('task', job)
                            last_save = time.monotonic()
                with self.lock:
                    if self.store.get('task', jid)['status'] != 'cancelled':
                        self.store.put('task', job)
                proc.stdout.close()
                code = proc.wait()
            with self.lock:
                job = self.store.get('task', jid)
                if job['status'] == 'cancelled':
                    return
                if code or job['result'] is None:
                    raise RuntimeError(error_path.read_text(encoding='utf-8')[-1500:] or '任务未返回结果')
                if job['params']['mode'] == 'train':
                    self.install_weights(folder, job)
                job['status'] = 'completed'
                self.store.put('task', job)
        except Exception as exc:
            with self.lock:
                job = self.store.get('task', jid)
                if job['status'] != 'cancelled':
                    job.update(status='failed', message=str(exc))
                    self.store.put('task', job)
        finally:
            with self.lock:
                self.processes.pop(jid, None)

    def install_weights(self, folder, job):
        weights = (folder/'ai_weights.json').read_bytes()
        json.loads(weights)
        with self.management.lock, self.c['guard'], self.c['ns']['_lock']:
            self.management.make_backup('before training weights')
            atomic_write(self.management.root/'ai_weights.json', weights)
            if job['params'].get('target_serials') is None and job['data_version'] == digest((self.management.root/'characters_data.py').read_bytes()):
                self.management.runtime.joinpath('needs-training').unlink(missing_ok=True)
            # Engines already hold their pinned Q-table; new rooms reload it.
            for name in ('_Q_TABLE_PVP_CACHE',):
                if name in self.c['ns']:
                    self.c['ns'][name] = None

    def lookup(self, jid):
        job = self.store.get('task', jid)
        if not job or job['owner'] != self.c['session_id']():
            raise NotFound('任务不存在')
        return job

    def get(self, jid):
        return jsonify(self.lookup(jid))

    def cancel(self, jid):
        self.c['body']()
        with self.lock:
            job = self.lookup(jid)
            if job['params']['mode'] == 'train':
                raise Conflict('训练正在独立执行，请等待完成')
            if job['status'] == 'running':
                job['status'] = 'cancelled'
                self.store.put('task', job)
                if jid in self.processes:
                    proc = self.processes[jid]
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    else:
                        import signal
                        os.killpg(proc.pid, signal.SIGTERM)
            return jsonify(job)

    def train(self):
        self.management.require_local()
        data = self.c['body']()
        if 'target_serials' in data and data['target_serials'] is None:
            raise BadRequest('训练名单必须为非空角色编号列表')
        return jsonify(self.launch({'mode': 'train', 'seed': 42, **({'target_serials': data['target_serials']} if 'target_serials' in data else {})}))

    def training_status(self):
        self.management.require_local()
        return jsonify(jobs=[j for j in self.store.list('task') if j['params']['mode'] == 'train' and j['owner'] == self.c['session_id']()][:10])

    def export(self, jid):
        job = self.lookup(jid)
        if job['status'] != 'completed':
            raise Conflict('请等待任务完成')
        if request.args.get('format') == 'csv':
            buf = io.StringIO()
            writer = csv.writer(buf)
            result = job['result']
            if 'matrix' in result:
                writer.writerow([''] + result['teams'])
                for name, row in zip(result['teams'], result['matrix']):
                    writer.writerow([name] + row)
            else:
                writer.writerow(['field', 'value'])
                for k, v in result.items():
                    writer.writerow([k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v])
            return Response('\ufeff' + buf.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=simulation-{jid}.csv'})
        return Response(json.dumps(job, ensure_ascii=False, indent=2), mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename=simulation-{jid}.json'})
