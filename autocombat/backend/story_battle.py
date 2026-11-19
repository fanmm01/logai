"""Story encounters use the existing UI engine, AI loop and event stream."""
import copy
import hashlib
import json
import random
import secrets
import threading
from collections import OrderedDict
from story_models import StoryError
from story_expression import value
from story_state_codec import capture_room,restore_room_state,encode_value,decode_value
from story_rules import execute_rule


class StoryBattles:
    def __init__(self,context,runtime):
        self.c,self.runtime=context,runtime;self.active={}

    def program(self,run): return self.runtime.project(run)['games'][run['game']['path']]

    def start_game(self,run):
        c=self.c;program=self.program(run);wave=program['battles'][0]
        ai=[('Y' if team==wave['a'] else 'X')+':'+s for team in (wave['a'],wave['b']) for s in program['teams'][team] if program['control']=='NONE' or team not in program['control'].split(',')]
        room=c['create_room_state'](dict(team_a=program['teams'][wave['a']],team_b=program['teams'][wave['b']],ai_slots=ai,local=True,name='剧情战斗 · '+run['game']['path'],player_name='剧情玩家'),run['owner'])
        eng=c['engine_for'](room)
        room['story']=dict(run=run['id'],game=run['game']['path'],wave=0,waves=len(program['battles']),control=program['control'],finished=False,round_start=1)
        room['story_catalog']=copy.deepcopy(c['ns']['characters_data_pvp'].ALL_CHARACTERS)
        room['story_baselines']={};room['story_rosters']={}
        for team,side in ((wave['a'],'Y'),(wave['b'],'X')):
            uids=[eng._resolve_uid(e['userId']) for e in eng._get_initiative() if e['team']==side and not e.get('isSummon')]
            room['story_rosters'][team]=uids
            for uid in uids: room['story_baselines'][uid]=copy.deepcopy(vars(eng.get_char(uid)))
        blob=encode_value(eng._ui_q_table);blob_id=hashlib.sha256(json.dumps(blob,sort_keys=True).encode()).hexdigest()
        self.runtime.store.put('story_blob',dict(id=blob_id,value=blob));run['weights']=blob_id
        run.update(status='battle',room=room['code']);self.active[run['id']]=run
        try:
            with c['ns']['_lock'],c['room_random'](room):
                c['invoke']('prepare_ready',room['battle_id']);room['running']=True
                c['emit'](room,'story_start','剧情战斗开始 · 第 1 波')
        finally: self.active.pop(run['id'],None)

    def detach(self,run):
        room=self.c['rooms'].pop(run.get('room'),None)
        if room:
            room['running']=False;room['updated'].set()
            self.c['ns']['_battles'].pop(room['battle_id'],None);self.c['ns']['_pending_reactions'].pop(room['battle_id'],None)

    def restore_room(self,run,renew=False):
        c=self.c
        if not renew and run.get('room') in c['rooms']: return c['rooms'][run['room']]
        checkpoint=run.get('battle_checkpoint')
        if not checkpoint: raise StoryError('缺少战斗检查点')
        blob=self.runtime.store.get('story_blob',run['weights'])
        if not blob: raise StoryError('缺少战斗权重快照')
        room,eng,pending=restore_room_state(checkpoint,c['ns']['PvPFullBattleEngine'],decode_value(blob['value']))
        room['history']=[record['event'] for identifier in room.get('story_event_ids',[]) if (record:=self.runtime.store.get('story_battle_event',identifier))]
        if renew: room['code']=secrets.token_hex(3).upper()
        room['story']['run']=run['id'];run['room']=room['code']
        c['rooms'][room['code']]=room;c['ns']['_battles'][room['battle_id']]=eng
        if pending: c['ns']['_pending_reactions'][room['battle_id']]=pending
        threading.Thread(target=c['runner'],args=(room['code'],),daemon=True).start()
        return room

    def room_event(self,room,engine):
        try:
            self._commit_room_event(room,engine)
        except Exception:
            # A failed durable commit must never be broadcast as an accepted action.
            # Restore the last committed combat boundary and pause the room.
            room['running']=False
            previous=self.runtime.store.get('story_run',room['story']['run'])
            if previous and previous.get('battle_checkpoint'):
                restored,restored_engine,pending=restore_room_state(previous['battle_checkpoint'],self.c['ns']['PvPFullBattleEngine'],engine._ui_q_table)
                signal=room['updated'];restored['updated']=signal
                restored['history']=[record['event'] for identifier in restored.get('story_event_ids',[]) if (record:=self.runtime.store.get('story_battle_event',identifier))]
                room.clear();room.update(restored);engine.__dict__.clear();engine.__dict__.update(restored_engine.__dict__)
                if pending:self.c['ns']['_pending_reactions'][room['battle_id']]=pending
                else:self.c['ns']['_pending_reactions'].pop(room['battle_id'],None)
            raise

    def _commit_room_event(self,room,engine):
        sid=room['story']['run'];run=self.active.get(sid) or self.runtime.lookup(sid)
        if run['status']!='battle' or room['story'].get('finished'): return
        profile=self.runtime.profile(run['project'],run['owner'])
        event=room['history'][-1]
        event_id=run['id']+':'+room['code']+':'+str(event['id'])
        room['story_event_ids']=(room.get('story_event_ids',[])+[event_id])[-room['settings']['ROOM_HISTORY_LIMIT']:]
        # Snapshot engine, pending spell plans and RNG at an atomic action boundary.
        run['battle_checkpoint']=capture_room(room,engine,self.c['ns']['_pending_reactions'].get(room['battle_id']))
        if (engine._get_state() or {}).get('phase')=='ended':
            program=self.program(run);wave=program['battles'][run['game']['wave']]
            alive=self.team_survives(room,engine,program['bind'])
            if alive and run['game']['wave']+1<len(program['battles']):
                run['game']['pending']=True
            else:
                room['running']=False;room['story']['finished']=True
                result='l'
                for branch in program['returns']:
                    if branch['condition'] is None or value(branch['condition'],run['variables'],{'isAlive':lambda team:self.team_survives(room,engine,str(team))}): result=branch['result'];break
                self.runtime.complete_game(run,result,profile)
        self.runtime.commit(run,profile,[('story_battle_event',dict(id=event_id,event=event))])

    def team_survives(self,room,engine,team):
        uids=room.get('story_rosters',{}).get(team,[])
        return any((engine._get_combat_hp(uid) or 0)>0 or (engine._is_dying(uid) and engine._has_healing_item(uid)) for uid in uids)

    def apply_carry_policy(self,room,engine,uids,policy):
        for uid in uids:
            ch=engine.get_char(uid);base=room['story_baselines'][uid]
            resources={k:ch.attrs.get(k) for k in ('体力','魔力','理智')};hp=engine._get_combat_hp(uid)
            inventory=copy.deepcopy(ch.inventory);once=copy.deepcopy(getattr(ch,'_once_consumed',set()))
            if not policy['statuses']:
                ch.__dict__.clear();ch.__dict__.update(copy.deepcopy(base))
            if policy['resources']:
                for k,v in resources.items():
                    if v is not None: ch.attrs[k]=v
                engine._set_combat_hp(uid,hp)
            else:
                for k in ('体力','魔力','理智'):
                    if k in base['attrs']: ch.attrs[k]=base['attrs'][k]
                engine._set_combat_hp(uid,base['attrs'].get('体力',1))
                getattr(engine,'_graveyard',{}).pop(uid,None)
            ch.inventory=inventory if policy['inventory'] else copy.deepcopy(base.get('inventory',[]))
            ch._once_consumed=once if policy['usage'] else set()
        effects=[]
        for effect in engine._get_effects():
            involved=any(str(v) in uids for k,v in effect.items() if isinstance(v,str) and ('uid' in k.lower() or 'userid' in k.lower() or k in ('owner','source','caster')))
            group='cooldowns' if effect.get('type')=='cooldown' else 'resources' if effect.get('type')=='dying' else 'statuses'
            if not involved or policy[group]: effects.append(effect)
        engine._set_effects(effects)
        if not policy['summons']:
            remove={sid for sid,entry in engine._summon_members.items() if entry.get('ownerId') in uids}
            engine._summon_members={k:v for k,v in engine._summon_members.items() if k not in remove}
            engine._summon_groups={k:v for k,v in engine._summon_groups.items() if v.get('ownerId') not in uids}
            engine._set_initiative([e for e in engine._get_initiative() if engine._resolve_uid(e['userId']) not in remove])
        if not policy['usage']:
            for attr in ('_summoned_once','_summon_counters','_summon_total_spawned'):
                data=getattr(engine,attr,{})
                for k in list(data):
                    if (k[0] if isinstance(k,tuple) else k) in uids: data.pop(k,None)
            engine._revive_use_ledger={x for x in getattr(engine,'_revive_use_ledger',set()) if not any(uid in str(x) for uid in uids)}

    def advance_pending(self,room):
        if room['story'].get('finished') or not room['running']: return
        run=self.runtime.lookup(room['story']['run'])
        if run['status']!='battle' or not run['game'].get('pending'): return
        c=self.c;program=self.program(run);previous=program['battles'][run['game']['wave']]
        run['game'].pop('pending');run['game']['wave']+=1;wave=program['battles'][run['game']['wave']]
        engine=c['engine_for'](room)
        self.active[run['id']]=run
        try:
            with c['ns']['_lock'],c['room_random'](room):
                policy=previous['carry'] if previous['keep'] else {k:False for k in previous['carry']}
                for team in (previous['a'],previous['b']): self.apply_carry_policy(room,engine,room['story_rosters'][team],policy)
                self.start_wave(run,room,engine,wave,program)
                room['story']['wave']=run['game']['wave']
                c['emit'](room,'story_wave',f"第 {run['game']['wave']+1} 波开始；已应用状态继承策略")
        finally: self.active.pop(run['id'],None)

    def start_wave(self,run,room,engine,wave,program):
        c=self.c;temporary=room['battle_id']+'_wave'
        module=c['ns']['characters_data_pvp'];original=module.ALL_CHARACTERS
        try:
            module.ALL_CHARACTERS=room.get('story_catalog',original)
            c['invoke']('create_battle',data=dict(group_id=temporary,mode='multi_pvp',team_a=program['teams'][wave['a']],team_b=program['teams'][wave['b']],map_size=f"{engine._get_map()['width']}x{engine._get_map()['height']}"))
        finally: module.ALL_CHARACTERS=original
        fresh=c['ns']['_battles'].pop(temporary);c['ns']['_pending_reactions'].pop(temporary,None)
        old_il=copy.deepcopy(engine._get_initiative());new_il=[];retained=set();new_uids=set()
        for team,side in ((wave['a'],'Y'),(wave['b'],'X')):
            if team in room['story_rosters']:
                roster=room['story_rosters'][team];retained.update(roster)
                entries=[e for e in old_il if engine._resolve_uid(e['userId']) in roster or e.get('ownerId') in roster]
                # Previously dead members explicitly reset by NONE can re-enter.
                for uid in roster:
                    if not any(engine._resolve_uid(e['userId'])==uid for e in entries) and (engine._get_combat_hp(uid) or 0)>0:
                        entries.append(dict(userId=uid,baseUserId=uid,name=engine.get_char(uid).name,serial=engine.get_char(uid).serial,actionIdx=0))
            else:
                entries=[copy.deepcopy(e) for e in fresh._get_initiative() if e['team']==side]
                roster=[fresh._resolve_uid(e['userId']) for e in entries if not e.get('isSummon')]
                room['story_rosters'][team]=roster
                for e in entries:
                    uid=fresh._resolve_uid(e['userId']);engine.characters[uid]=fresh.characters[uid];new_uids.add(uid)
                    engine._set_combat_hp(uid,fresh._get_combat_hp(uid))
                    room['story_baselines'][uid]=copy.deepcopy(vars(fresh.get_char(uid)))
            for i,e in enumerate(entries):
                uid=engine._resolve_uid(e['userId'])
                if (engine._get_combat_hp(uid) or 0)<=0 and not engine._is_dying(uid): continue
                e.update(team=side,coord=c['ns']['format_coord'](1 if side=='Y' else engine._get_map()['width']-2,min(engine._get_map()['height']-1,1+i)))
                e.pop('_move_used',None);e.pop('actions',None)
                ch=engine.get_char(uid);dex=ch.get_attr('敏捷',50);roll=random.randint(1,100)
                e.update(dex=dex,initRoll=roll,initRank=c['ns']['success_rank'](roll,dex));new_il.append(e)
        active_ids={engine._resolve_uid(e['userId']) for e in new_il}
        # Keep source tombstones in characters so carried DOTs can still resolve their caster.
        effects=[e for e in engine._get_effects() if any(isinstance(v,str) and v in active_ids for v in e.values())]
        effects.extend(copy.deepcopy(e) for e in fresh._get_effects() if any(isinstance(v,str) and v in new_uids for v in e.values()))
        engine._set_effects(effects)
        for attr in ('_summon_members','_summon_groups'):
            current=getattr(engine,attr,{})
            setattr(engine,attr,{k:v for k,v in current.items() if v.get('ownerId') in retained})
            getattr(engine,attr).update({k:copy.deepcopy(v) for k,v in getattr(fresh,attr,{}).items() if v.get('ownerId') in new_uids})
        new_il.sort(key=lambda e:(-e.get('initRank',0),-e.get('dex',0),e.get('initRoll',0)))
        engine._set_initiative(new_il);map_data=engine._get_map();map_data['occupants']={e['coord']:e['userId'] for e in new_il};engine._set_map(map_data)
        st=engine._get_state();st.update(phase='active',activeIndex=0);engine._set_state(st);engine._battle_result=None
        room['story']['round_start']=st.get('round',1)
        engine._last_death_overflow={};engine._last_death_max_hp={}
        engine._ai_failed_commands=set();engine._sync_all_initiative_slots()
        engine._reset_round_actions()
        for e in new_il:
            uid=engine._resolve_uid(e['userId']);side=e['team'];team=wave['a'] if side=='Y' else wave['b']
            room['controls'][uid]='ai' if program['control']=='NONE' or team not in program['control'].split(',') else run['owner']
        c['sync_controls'](room,engine)
        engine._check_and_mark_end(engine._get_state())

    def execute_external(self,run):
        program=self.program(run);project,folder=self.runtime.workspaces.load_version(run['project'],run['version'])
        run.update(status='external_running',message='正在执行外部规则');identifier=run['id'];attempt=run['game']['attempt']
        self.runtime.commit(run)
        def worker():
            try: result=execute_rule(project['rules'][program['rule']],program['payload'],attempt,str(folder));error=None
            except Exception as failure: result=None;error=str(failure)
            with self.c['guard']:
                current=self.runtime.lookup(identifier)
                if current['status']!='external_running' or current['game']['attempt']!=attempt: return
                profile=self.runtime.profile(current['project'],current['owner'])
                if error: current.update(status='external_error',message=error)
                else: current['external_data']=result.get('data');self.runtime.complete_game(current,result['result'],profile)
                self.runtime.commit(current,profile)
        threading.Thread(target=worker,daemon=True).start()
