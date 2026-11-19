"""Flask story authoring/playback endpoints; development authoring is session-open."""
import copy
import json
import time
from pathlib import Path
from flask import jsonify,request,Response,send_from_directory
from werkzeug.exceptions import BadRequest,Conflict,Forbidden,NotFound
from project_paths import PROJECT_ROOT
from story_models import StoryError
from story_workspace import Workspaces
from story_runtime import StoryRuntime
from story_battle import StoryBattles
from story_flow import build_graph,apply_graph_edit
from story_text import compile_scene


class Stories:
    def __init__(self,bp,context,store,runtime):
        self.c,self.store=context,store
        serials={x['serial'] for x in context['catalog']()}
        self.workspaces=Workspaces(runtime,store,serials);self.runtime=StoryRuntime(self.workspaces,store)
        self.battles=StoryBattles(context,self.runtime);self.runtime.battles=self.battles
        for run in store.list('story_run'):
            if run['status']=='external_running':
                run.update(status='external_error',message='服务已重启，外部执行中断；可明确选择重新执行');self.runtime.commit(run)
        for route,name,method in [('/projects','projects','GET'),('/workspaces','workspace_list','GET'),('/workspaces/open','workspace_open','POST'),('/workspaces/create','workspace_create','POST'),('/workspaces/<wid>/tree','tree','GET'),('/workspaces/<wid>/files','file_read','GET'),('/workspaces/<wid>/files','file_write','POST'),('/workspaces/<wid>/compile','compile','POST'),('/workspaces/<wid>/graph/patch','graph_patch','POST'),('/workspaces/<wid>/publish','publish','POST'),('/runs','run_create','POST'),('/runs/<rid>','run_get','GET'),('/runs/<rid>/commands','run_command','POST'),('/runs/<rid>/events','run_events','GET'),('/runs/<rid>/game/<kind>/<path:name>','game_file','GET')]:
            bp.add_url_rule('/api/ui/story'+route,'story_'+name,getattr(self,name),methods=[method])
        bp.add_url_rule('/ui/story-engine/','story_engine_root',self.engine_root)
        bp.add_url_rule('/api/ui/story/workspaces/<wid>/preview','story_preview',self.preview,methods=['POST'])
        bp.add_url_rule('/api/ui/story/runs/<rid>/slots','story_slots',self.run_slots)
        bp.add_url_rule('/ui/story-engine/<path:name>','story_engine',self.engine_file)
        bp.register_error_handler(StoryError,self.error)

    def error(self,error):
        status=409 if '已被其他' in error.message else 400
        return jsonify(error=True,message=error.message,diagnostics=[error.diagnostic()]),status

    def owner(self):
        sid=self.c['session_id']()
        if not sid: raise Forbidden('请先建立玩家会话')
        return sid

    def body(self): self.owner();return self.c['body']()

    def ensure_demo(self):
        demo=PROJECT_ROOT/'story-projects/demo'
        if demo.is_dir() and not self.store.get('story_project','astral-demo'):
            self.workspaces.publish_version(self.workspaces.open_workspace(str(demo)))

    def projects(self):
        owner=self.owner();self.ensure_demo();result=[]
        for project in self.store.list('story_project'):
            if project.get('preview'): continue
            result.append(dict(**project,chapters=self.runtime.chapters(project['id'],owner)))
        active=[dict(id=r['id'],project=r['project'],flow=r['flow'],status=r['status']) for r in self.store.list('story_run') if r['owner']==owner]
        return jsonify(projects=result,runs=active,default_workspace=str(PROJECT_ROOT/'story-projects/demo'))

    def workspace_list(self):
        self.owner()
        folder=PROJECT_ROOT/'story-projects'
        for root in sorted(folder.iterdir()) if folder.is_dir() else []:
            if root.is_dir() and (root/'story.project.json').is_file(): self.workspaces.open_workspace(root)
        result=[]
        for workspace in self.store.list('story_workspace'):
            if not Path(workspace['path']).is_dir(): continue
            item=dict(workspace)
            try:item['name']=json.loads((Path(item['path'])/'story.project.json').read_text(encoding='utf-8-sig')).get('name',item['name'])
            except (OSError,ValueError):pass
            result.append(item)
        return jsonify(workspaces=result)
    def workspace_create(self):
        data=self.body()
        return jsonify(self.workspaces.create_workspace(PROJECT_ROOT/'story-projects',data.get('name')))
    def workspace_open(self): return jsonify(self.workspaces.open_workspace(self.body()['path']))
    def tree(self,wid): self.owner();return jsonify(files=self.workspaces.scan_files(self.workspaces.lookup(wid)))
    def file_read(self,wid): self.owner();return jsonify(self.workspaces.read_file(self.workspaces.lookup(wid),request.args.get('path','')))
    def file_write(self,wid):
        data=self.body();return jsonify(files=self.workspaces.apply_file_transaction(self.workspaces.lookup(wid),data['changes']))
    def compile(self,wid):
        data=self.body();project=self.workspaces.read_project(self.workspaces.lookup(wid),data.get('drafts'))
        return jsonify(diagnostics=[],graphs={p:build_graph(f) for p,f in project['flows'].items()},config=project['config'])
    def graph_patch(self,wid):
        data=self.body();workspace=self.workspaces.lookup(wid)
        source=data.get('source')
        if source is None: source=self.workspaces.read_file(workspace,data['path'])['content']
        result=apply_graph_edit(source,data['operation'],data['path'])
        from story_flow import parse_flow
        return jsonify(content=result,graph=build_graph(parse_flow(result,data['path'])))
    def publish(self,wid): self.body();return jsonify(self.workspaces.publish_version(self.workspaces.lookup(wid)))

    def preview(self,wid):
        data=self.body();workspace=self.workspaces.lookup(wid)
        record=self.workspaces.publish_version(workspace,data.get('drafts'),preview=True)
        project,_=self.workspaces.load_version(record['id'])
        flow=data.get('flow')
        if flow and not any(e['kind']=='start' for e in project['flows'][flow]['entries']): raise StoryError('预览请从一个含 START 的入口开始；接续章节需先在预览档案中完成前章')
        run=self.runtime.create_run(record['id'],self.owner(),flow,'START' if flow else None)
        return jsonify(self.runtime.public(run))

    def run_create(self):
        data=self.body()
        with self.c['guard']:
            run=self.runtime.create_run(data['project'],self.owner(),data.get('flow'),data.get('entry'),data.get('version'))
            return jsonify(self.runtime.public(run))

    def run_get(self,rid):
        with self.c['guard']:
            run=self.runtime.lookup(rid,self.owner())
            if run['status']=='battle' and run.get('room') not in self.c['rooms']:
                self.battles.restore_room(run);self.runtime.commit(run)
            return jsonify(self.runtime.public(run))

    def run_command(self,rid):
        data=self.body()
        with self.c['guard']:
            run=self.runtime.lookup(rid,self.owner());key=data.get('commandId')
            if not isinstance(key,str) or not 1<=len(key)<=120: raise BadRequest('缺少有效 commandId')
            if key in run['requests']: return jsonify(self.runtime.public(run))
            if data.get('expectedRevision')!=run['revision']: raise Conflict('剧情状态已更新，请重试当前操作')
            profile=self.runtime.profile(run['project'],run['owner'])
            self.runtime.apply_command(run,data,profile)
            run['requests'][key]=True
            while len(run['requests'])>300: run['requests'].pop(next(iter(run['requests'])))
            self.runtime.commit(run,profile)
            return jsonify(self.runtime.public(run))

    def run_slots(self,rid):
        run=self.runtime.lookup(rid,self.owner())
        slots=[]
        for slot in self.store.list('story_slot'):
            if slot['project']==run['project'] and slot['owner']==run['owner']:
                slots.append(dict(slot=slot['slot'],snapshot=slot['state'].get('player_snapshot'),time=slot['time']))
        return jsonify(slots=slots)

    def run_events(self,rid):
        self.runtime.lookup(rid,self.owner())
        try: after=int(request.args.get('after','-1'))
        except ValueError: raise BadRequest('无效事件序号')
        def stream():
            nonlocal after
            last_profile=-1
            until=time.monotonic()+25
            while time.monotonic()<until:
                with self.c['guard']:
                    run=self.runtime.lookup(rid)
                    profile_revision=self.runtime.profile(run['project'],run['owner'])['revision']
                    if run['revision']!=after or profile_revision!=last_profile:
                        last_profile=profile_revision
                        after=run['revision'];payload=json.dumps(self.runtime.public(run),ensure_ascii=False)
                    else: payload=None
                yield ('id: '+str(after)+'\ndata: '+payload+'\n\n') if payload else ': heartbeat\n\n'
                time.sleep(1)
        return Response(stream(),mimetype='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    def game_file(self,rid,kind,name):
        run=self.runtime.lookup(rid,self.owner());project,folder=self.workspaces.load_version(run['project'],run['version'])
        if kind=='scene':
            if run.get('scene') and name==run['scene']['token']+'.txt': text=run['scene']['script']
            elif name.removesuffix('.txt') in run.get('scene_archive',{}): text=run['scene_archive'][name.removesuffix('.txt')]['script']
            elif name in project['prose'] or 'scene/'+name in project['prose']:
                ops=project['prose'][name if name in project['prose'] else 'scene/'+name]
                text=ops[0]['script'] if len(ops)==1 and ops[0].get('whole') else compile_scene(ops,project['config'].get('figures',{}))['script'].removesuffix('storyHost:advance;')
            else: raise NotFound('场景不存在')
            return Response(text,mimetype='text/plain',headers={'Cache-Control':'no-store'})
        if kind=='config':
            if name=='config.txt': return Response('Game_name:'+project['config'].get('name','星序剧情')+';\nGame_key:story-'+run['project']+';',mimetype='text/plain')
        if kind=='video' and (folder/name).is_file(): return send_from_directory(folder,name,conditional=True)
        path=folder/kind/name
        if not path.resolve().is_relative_to(folder.resolve()): raise NotFound()
        if kind=='animation' and name=='animationTable.json' and not path.exists(): return jsonify([])
        if kind=='style' and name=='userStyleSheet.css' and not path.exists(): return Response('',mimetype='text/css')
        return send_from_directory(folder,kind+'/'+name,conditional=True)

    def engine_root(self): return self.engine_file('index.html')
    def engine_file(self,name):
        folder=PROJECT_ROOT/'frontend/dist/story-engine'
        if not folder.is_dir(): folder=PROJECT_ROOT/'webgal-adv/engine/packages/webgal/dist'
        return send_from_directory(folder,name)
