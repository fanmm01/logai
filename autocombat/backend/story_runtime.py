"""Server-owned resumable story VM. Player gate state never rolls back with saves."""
import copy
import hashlib
import secrets
import time
from pathlib import Path
from story_expression import value
from story_models import StoryError
from story_flow import resolve_target
from story_text import compile_scene,parse_text
from story_gates import apply_gate_command,available_entries,flow_state,option_enabled


class StoryRuntime:
    def __init__(self,workspaces,store):
        self.workspaces,self.store=workspaces,store
        self.battles=None

    def project(self,run): return self.workspaces.load_version(run['project'],run['version'])[0]

    def profile(self,project,owner):
        identifier=hashlib.sha256((project+'\0'+owner).encode()).hexdigest()
        return self.store.get('story_profile',identifier) or dict(id=identifier,project=project,owner=owner,gates={},continuations=[],completed=[],revision=0,user_data={})

    def save_profile(self,profile):
        profile['revision']+=1;self.store.put('story_profile',profile)

    def create_run(self,project_id,owner,flow=None,entry=None,version=None):
        project,folder=self.workspaces.load_version(project_id,version)
        record=self.store.get('story_project',project_id)
        if not flow:
            flow=project['texts'].get('MAIN','').strip()
            if not flow:
                starts=[p for p,f in project['flows'].items() if any(e['kind']=='start' for e in f['entries'])]
                if len(starts)!=1: raise StoryError('请选择起始章节')
                flow=starts[0]
        flow=resolve_target(flow,project['texts'],('.flow',));profile=self.profile(project_id,owner)
        if not flow_state(project,profile,flow)['unlocked']: raise StoryError(flow_state(project,profile,flow)['reason'])
        entries=available_entries(project,profile,flow)
        selected=next((x for x in entries if x['id']==entry),None) if entry else entries[0] if len(entries)==1 else None
        if not selected: raise StoryError('请选择 START 或具体接续来源')
        incoming=selected.get('ticket',{}).get('state',{})
        run=dict(id=secrets.token_hex(12),schema=1,project=project_id,version=version or record['version'],owner=owner,flow=flow,
                 pc=0,variables=copy.deepcopy(incoming.get('variables',{})),counts=copy.deepcopy(incoming.get('counts',{})),
                 player_snapshot=copy.deepcopy(incoming.get('player_snapshot')),scene=None,queue=[],text_file='',choice=None,
                 status='running',revision=0,requests={},retry_sources=[],pending_branch=None,game=None,room=None,
                 entry=selected['id'],created=time.time(),message='')
        self.advance_until_wait(run,profile);self.commit(run,profile)
        return run

    def lookup(self,identifier,owner=None):
        run=self.store.get('story_run',identifier)
        if not run or (owner is not None and run['owner']!=owner): raise StoryError('剧情运行不存在')
        return run

    def commit(self,run,profile=None,extra=None):
        run['revision']+=1
        records=[('story_run',run)]
        if profile is not None:
            profile['revision']+=1;records.append(('story_profile',profile))
        records.extend(extra or [])
        self.store.put_many(records)

    def public(self,run):
        result={k:copy.deepcopy(v) for k,v in run.items() if k not in ('owner','requests','retry_sources','queue','pending_branch','game','battle_checkpoint','weights','scene_archive')}
        project=self.project(run);profile=self.profile(run['project'],run['owner'])
        result['profile_revision']=profile['revision'];result['user_data']=profile.get('user_data',{})
        if run.get('choice'):
            result['choice']['options']=[dict(id=o['id'],text=o['text'],enabled=option_enabled(project,profile,run['text_file'],o)) for o in run['choice']['options']]
        if run.get('game'):
            game=project['games'][run['game']['path']]
            result['encounter']=dict(teams=game.get('teams',{}),battles=game.get('battles',[]),bind=game.get('bind',''),wave=run['game'].get('wave',0),control=game.get('control','NONE'))
        if run['status']=='continue':
            result['successors']=self.chapters(run['project'],run['owner'],run['version'])
        result['slots']=[dict(id=x['id'],slot=x['slot'],flow=x['state']['flow'],time=x['time']) for x in self.store.list('story_slot') if x['owner']==run['owner'] and x['project']==run['project']]
        return result

    def chapters(self,project_id,owner,version=None):
        project,_=self.workspaces.load_version(project_id,version);profile=self.profile(project_id,owner)
        settings=project['config'].get('chapters',{})
        result=[]
        for path,flow in project['flows'].items():
            meta=settings.get(path,settings.get(Path(path).stem,{}))
            result.append(dict(path=path,title=meta.get('title',Path(path).stem),description=meta.get('description',''),order=meta.get('order',len(result)),completed=path in profile['completed'],**flow_state(project,profile,path)))
        return sorted(result,key=lambda x:(x['order'],x['path']))

    def _snapshot_source(self,run,pc):
        return dict(pc=pc,flow=run['flow'],player_snapshot=copy.deepcopy(run['player_snapshot']))

    def _scene(self,run,ops):
        result=compile_scene(ops,self.project(run)['config'].get('figures',{}))
        run['scene']=dict(token=secrets.token_hex(8),path=run.get('text_file') or run['flow'],**result)
        run.setdefault('scene_archive',{})[run['scene']['token']]=copy.deepcopy(run['scene'])
        run['status']='text'

    def _text_step(self,run,profile):
        pending=[]
        while run['queue']:
            op=run['queue'][0]
            if op['kind']=='gate':
                if pending: self._scene(run,pending);return True
                run['queue'].pop(0);apply_gate_command(self.project(run),profile,op['action'],op['target']);continue
            if op['kind']=='choice':
                if pending: self._scene(run,pending);return True
                run['queue'].pop(0);run['choice']=op;run['status']='choice';return True
            run['queue'].pop(0);pending.append(op)
            if op['kind']=='say' or op.get('whole'):
                self._scene(run,pending);return True
        if pending: self._scene(run,pending);return True
        run['text_file']=''
        self._finish_branch(run,run.pop('choice_result',''))
        return False

    def _finish_branch(self,run,result):
        branch=run.get('pending_branch');run['pending_branch']=None
        if branch:
            if str(result) not in branch: raise StoryError('没有匹配返回结果的分支: '+str(result),run['flow'])
            run['pc']=branch[str(result)]

    def _condition(self,run,expression):
        return value(expression,run['variables'],{'time':lambda label:run['counts'].get(run['flow']+':'+str(label),0)})

    def advance_until_wait(self,run,profile):
        project=self.project(run);run['status']='running';run['scene']=None
        for _ in range(2000):
            if run['text_file']:
                if self._text_step(run,profile): return
            ops=project['flows'][run['flow']]['instructions']
            if run['pc']>=len(ops): self._finish(run,profile,'ended');return
            current=run['pc'];op=ops[current];run['pc']=op.get('next',current+1)
            alias=op.get('alias')
            if alias:
                key=run['flow']+':'+alias;run['counts'][key]=run['counts'].get(key,0)+1
            kind=op['kind']
            if kind=='assign': run['variables'][op['name']]=self._condition(run,op['expression']);continue
            if kind=='if': run['pc']=op['yes'] if self._condition(run,op['expression']) else op['no'];continue
            if kind=='goto': run['pc']=op['target'];continue
            if kind=='gate': apply_gate_command(project,profile,op['action'],op['target']);continue
            if kind=='media':
                run['queue']=parse_text(op['command'],run['flow'],project['config'].get('figures',{}));run['text_file']=run['flow'];continue
            target=op['target']
            if target.startswith(('UNLOCK@','LOCK@')):
                action,ref=target.split('@',1);apply_gate_command(project,profile,action,ref);continue
            if target.startswith('(') and '?' in target:
                # Target names are not expression variables.
                m=__import__('re').fullmatch(r'\((.*)\)\s*\?\s*([^:]+)\s*:\s*(.+)',target)
                if not m: raise StoryError('无效三元跳转',run['flow'],op['line'])
                target=(m[2] if self._condition(run,m[1]) else m[3]).strip()
            labels=project['flows'][run['flow']]['labels']
            if target in labels: run['pc']=labels[target];continue
            if target=='START': run['pc']=0;continue
            if target=='NONE': continue
            if target=='END': self._finish(run,profile,'ended');return
            if target.startswith('CONTINUE-'):
                state=dict(variables=copy.deepcopy(run['variables']),counts=copy.deepcopy(run['counts']),player_snapshot=copy.deepcopy(run['player_snapshot']))
                ticket=dict(id=secrets.token_hex(8),source=str(Path(run['flow']).with_suffix('')).replace('\\','/'),tag=target,state=state,time=time.time())
                profile['continuations']=[previous for previous in profile['continuations'] if (previous['source'],previous['tag'])!=(ticket['source'],ticket['tag'])]
                profile['continuations'].append(ticket)
                self._finish(run,profile,'continue');run['message']='本章完成，选择继续下一章或返回章节列表';return
            if target=='RETRY':
                run['status']='retry';run['message']='本次尝试未完成。重试将保留变量与到达计数。';return
            if Path(target).suffix.lower() in ('.mp4','.webm','.ogg'):
                self._scene(run,[dict(kind='media',media='video',asset=target,path=run['flow'],line=op['line'])]);return
            path=resolve_target(target,project['texts'])
            run['pending_branch']=op.get('branches')
            if path.endswith(('.txt','.chos')):
                if op.get('branches') or path.endswith('.chos'):
                    run['retry_sources']=(run['retry_sources']+[self._snapshot_source(run,current)])[-64:]
                run['queue']=copy.deepcopy(project['prose'][path]);run['text_file']=path;run['choice_result']=''
            elif path.endswith('.gmf'):
                run['retry_sources']=(run['retry_sources']+[self._snapshot_source(run,current)])[-64:]
                run['game']=dict(path=path,wave=0,attempt=secrets.token_hex(8));run['status']='battle_ready' if project['games'][path]['rule']=='AUTOCOMBAT' else 'external_ready';return
        run['status']='paused';run['message']='推进指令超过 2000 条，可能存在非交互死循环'

    def _finish(self,run,profile,status):
        if run['flow'] not in profile['completed']: profile['completed'].append(run['flow'])
        run['status']=status;run['scene']=None

    def complete_game(self,run,result,profile):
        run['last_result']=result;run['game']=None;run['room']=None
        self._finish_branch(run,result);self.advance_until_wait(run,profile)

    def apply_command(self,run,data,profile):
        operation=data.get('op');payload=data.get('payload') or {}
        if operation=='preferences':
            if not isinstance(payload.get('user_data'),dict): raise StoryError('阅读设置格式无效')
            profile['user_data']=copy.deepcopy(payload['user_data'])
        elif operation=='checkpoint':
            if run['status']!='text' or payload.get('token')!=run['scene']['token']: raise StoryError('剧情画面已变化')
            run['player_snapshot']=payload.get('snapshot')
            if payload.get('user_data') is not None: profile['user_data']=payload['user_data']
        elif operation=='advance':
            if run['status']!='text' or payload.get('token')!=run['scene']['token']: raise StoryError('剧情画面已变化')
            snapshot=payload.get('snapshot') or run.get('player_snapshot')
            if snapshot is not None and snapshot.get('hostToken')==run['scene']['token']:
                run['player_snapshot']=snapshot
                run['variables'].update(snapshot.get('nowStageState',{}).get('GameVar',{}))
            if payload.get('user_data') is not None: profile['user_data']=payload['user_data']
            self.advance_until_wait(run,profile)
        elif operation=='choose':
            if run['status']!='choice': raise StoryError('当前没有选择')
            option=next((x for x in run['choice']['options'] if x['id']==str(payload.get('id'))),None)
            if not option or not option_enabled(self.project(run),profile,run['text_file'],option): raise StoryError('该选项已锁定或不存在')
            if not run.get('choice_result'): run['choice_result']=option['id']
            run['queue']=copy.deepcopy(option['body'])+run['queue'];run['choice']=None;self.advance_until_wait(run,profile)
        elif operation=='retry':
            if run['status'] not in ('retry','paused','external_error'): raise StoryError('当前不能重试')
            source=run['retry_sources'].pop() if run['retry_sources'] else dict(flow=run['flow'],pc=0,player_snapshot=None)
            if run.get('room') and self.battles: self.battles.detach(run)
            run.update(flow=source['flow'],pc=source['pc'],player_snapshot=copy.deepcopy(source['player_snapshot']),queue=[],text_file='',choice=None,scene=None,game=None,room=None,pending_branch=None)
            self.advance_until_wait(run,profile)
        elif operation=='startBattle':
            if run['status']!='battle_ready': raise StoryError('当前没有待开始战斗')
            self.battles.start_game(run)
        elif operation=='resumeBattle':
            if run['status']!='battle': raise StoryError('当前不在战斗中')
            self.battles.restore_room(run)
        elif operation=='save':
            if payload.get('snapshot') is not None and run['status']=='text' and payload['snapshot'].get('hostToken')==run['scene']['token']: run['player_snapshot']=payload['snapshot']
            slot=str(payload.get('slot','quick'))
            if not slot or len(slot)>40: raise StoryError('无效存档槽')
            state=copy.deepcopy(run);state['requests']={}
            key=hashlib.sha256((run['project']+'\0'+run['owner']+'\0'+slot).encode()).hexdigest()
            self.store.put('story_slot',dict(id=key,project=run['project'],owner=run['owner'],slot=slot,state=state,time=time.time()))
        elif operation=='load':
            slot=str(payload.get('slot','quick'));key=hashlib.sha256((run['project']+'\0'+run['owner']+'\0'+slot).encode()).hexdigest()
            saved=self.store.get('story_slot',key)
            if not saved: raise StoryError('该槽位没有存档')
            if run.get('room') and self.battles: self.battles.detach(run)
            revision,identifier,receipts=run['revision'],run['id'],run['requests']
            run.clear();run.update(copy.deepcopy(saved['state']));run.update(id=identifier,revision=revision,requests=receipts)
            if run.get('scene'):
                old_token=run['scene']['token'];new_token=secrets.token_hex(8);run['scene']['token']=new_token
                run.setdefault('scene_archive',{})[new_token]=copy.deepcopy(run['scene'])
                if run.get('player_snapshot') and run['player_snapshot'].get('hostToken')==old_token:
                    run['player_snapshot']['hostToken']=new_token
                    scene_data=run['player_snapshot'].get('sceneData',{})
                    if scene_data.get('sceneUrl'): scene_data['sceneUrl']=scene_data['sceneUrl'].replace(old_token,new_token)
            if run['status']=='battle': self.battles.restore_room(run,renew=True)
        elif operation=='executeExternal':
            if run['status'] not in ('external_ready','external_error'): raise StoryError('当前没有外部规则')
            if run['game'].pop('cancelled',False):run['game']['attempt']=secrets.token_hex(8)
            self.battles.execute_external(run)
        elif operation=='cancelExternal':
            if run['status']!='external_running':raise StoryError('当前没有运行中的外部规则')
            from story_rules import cancel_execution
            cancel_execution(run['game']['attempt']);run['game']['cancelled']=True;run.update(status='external_error',message='执行已取消。HTTP 请求的远端结果将被忽略。')
        else: raise StoryError('不支持的剧情操作: '+str(operation))
