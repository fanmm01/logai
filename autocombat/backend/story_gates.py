"""Persistent per-player gate overrides, independent of save slots."""
from pathlib import PurePosixPath
from story_models import StoryError
from story_flow import parse_gate_target, resolve_target
from story_text import walk_ops


def content_key(project, path):
    return project.get('config',{}).get('content_ids',{}).get(path,path)


def resolve_gate_target(project,target):
    ref=parse_gate_target(target); files=project['texts']
    if ref['option'] is None:
        path=resolve_target(ref['file'],files,('.flow',))
        if path not in project['flows']: raise StoryError('权限目标不是 flow: '+ref['file'])
        return 'flow:'+content_key(project,path)
    path=resolve_target(ref['file'],files,('.txt','.chos'))
    choices=[(i,o) for i,op in enumerate(walk_ops(project['prose'][path])) if op['kind']=='choice' for o in op['options'] if o['id']==ref['option']]
    if len(choices)!=1: raise StoryError('选项不存在或编号不唯一: '+target)
    return 'option:'+content_key(project,path)+':'+ref['option']


def option_key(project,path,option):
    return 'option:'+content_key(project,path)+':'+option['id']


def apply_gate_command(project,profile,action,target):
    if action not in ('UNLOCK','LOCK'): raise StoryError('未知权限指令')
    profile.setdefault('gates',{})[resolve_gate_target(project,target)]=action=='UNLOCK'


def available_entries(project,profile,path):
    entries=[]
    # Older profiles may contain one ticket per playthrough. Each declared source
    # and continuation tag represents one entry, backed by its newest completion.
    latest={}
    for ticket in profile.get('continuations',[]):
        key=(ticket['source'],ticket['tag'])
        previous=latest.get(key)
        if previous is None or ticket.get('time',0)>=previous.get('time',0):
            latest[key]=ticket
    for i,entry in enumerate(project['flows'][path]['entries']):
        if entry['kind']=='start': entries.append(dict(id='START',kind='start',label='重新开始'))
        else:
            ticket=latest.get((entry['source'],entry['tag']))
            if ticket is not None:
                entries.append(dict(id=ticket['id'],kind='continue',label=entry['source']+' · '+entry['tag'],ticket=ticket))
    return entries


def flow_state(project,profile,path):
    entries=available_entries(project,profile,path)
    override=profile.get('gates',{}).get('flow:'+content_key(project,path))
    unlocked=override is not False and bool(entries)
    return dict(unlocked=unlocked,reason='' if unlocked else '该章节已锁定' if override is False else '需要前章接续状态',entries=[{k:v for k,v in x.items() if k!='ticket'} for x in entries])


def option_enabled(project,profile,path,option):
    return profile.get('gates',{}).get(option_key(project,path,option),not option.get('locked',False))
