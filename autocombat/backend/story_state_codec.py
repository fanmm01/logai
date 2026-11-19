"""Explicit JSON object-graph codec for resumable combat. Never pickle."""
from collections import defaultdict, OrderedDict
import math
from story_models import StoryError, validate_checkpoint_version


def encode_value(value):
    nodes, memo = [], {}
    def encode(item):
        if item is None or isinstance(item,(str,bool,int)): return item
        if isinstance(item,float):
            if not math.isfinite(item): return {'$float':str(item)}
            return item
        identity=id(item)
        if identity in memo: return {'$ref':memo[identity]}
        index=len(nodes);memo[identity]=index;node={};nodes.append(node)
        if isinstance(item,dict):
            tag='defaultdict' if isinstance(item,defaultdict) else 'ordered' if isinstance(item,OrderedDict) else 'dict'
            node.update(type=tag,data=[[encode(k),encode(v)] for k,v in item.items()])
            if tag=='defaultdict':
                factory=item.default_factory
                if factory in (None,int,float,list,dict,set):
                    node['factory']=factory.__name__ if factory else None
                else:
                    # Existing AI weights use lambda: defaultdict(float). Persist only
                    # the resulting data template, never the callable or source code.
                    template=factory()
                    if not isinstance(template,(dict,list,set,int,float,str,bool,type(None))): raise StoryError('不支持的 defaultdict 工厂')
                    node['factory']='template';node['template']=encode(template)
        elif isinstance(item,(list,tuple,set,frozenset)):
            node.update(type=type(item).__name__,data=[encode(x) for x in item])
        elif type(item).__name__=='Character':
            node.update(type='Character',data=encode(vars(item)))
        else: raise StoryError('存档不支持对象: '+type(item).__name__)
        return {'$ref':index}
    root=encode(value)
    return dict(schema=1,root=root,nodes=nodes)


def decode_value(document):
    validate_checkpoint_version(document)
    from battle_engine import Character
    nodes=document['nodes'];memo={}
    def decode(value):
        if not isinstance(value,dict): return value
        if '$float' in value: return float(value['$float'])
        if '$ref' not in value: raise StoryError('无效对象图引用')
        index=value['$ref']
        if not isinstance(index,int) or not 0<=index<len(nodes): raise StoryError('损坏的对象图引用')
        if index in memo: return memo[index]
        node=nodes[index];tag=node['type'];data=node['data']
        if tag in ('dict','ordered','defaultdict'):
            result=OrderedDict() if tag=='ordered' else defaultdict({'int':int,'float':float,'list':list,'dict':dict,'set':set,None:None}.get(node.get('factory'))) if tag=='defaultdict' else {}
            memo[index]=result
            for k,v in data: result[decode(k)]=decode(v)
            if tag=='defaultdict' and node.get('factory')=='template':
                import copy
                template=decode(node['template']);result.default_factory=lambda:copy.deepcopy(template)
        elif tag=='Character':
            result=Character.__new__(Character);memo[index]=result;result.__dict__.update(decode(data))
        elif tag in ('list','tuple','set','frozenset'):
            result=[];memo[index]=result;result.extend(decode(x) for x in data)
            result={'list':lambda x:x,'tuple':tuple,'set':set,'frozenset':frozenset}[tag](result);memo[index]=result
        else: raise StoryError('未知存档对象类型: '+tag)
        return result
    return decode(document['root'])


def capture_room(room,engine,pending):
    # Replay snapshots are append-only records, not copied into every checkpoint.
    room_state={k:v for k,v in room.items() if k not in ('updated','touched','_story_last_checkpoint','history')}
    engine_state={k:v for k,v in vars(engine).items() if k!='_ui_q_table'}
    return encode_value(dict(room=room_state,engine=engine_state,pending=pending))


def restore_room_state(document,engine_factory,q_table=None):
    import threading,time
    data=decode_value(document);engine=engine_factory();engine.__dict__.clear();engine.__dict__.update(data['engine'])
    engine._ui_q_table=q_table or {}
    room=data['room'];room.update(updated=threading.Event(),touched=time.time(),running=False)
    room.setdefault('history',[])
    return room,engine,data['pending']
