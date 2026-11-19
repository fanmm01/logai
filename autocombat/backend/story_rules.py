"""JSON rule contracts and executable/HTTP adapters. No shell interpolation."""
import json
import subprocess
import urllib.request
import threading
from story_models import StoryError

_processes={}
_cancelled=set()
_process_lock=threading.RLock()


def validate_input(payload,schema,path='input'):
    if not schema:return
    types={'object':dict,'array':list,'string':str,'number':(int,float),'integer':int,'boolean':bool,'null':type(None)}
    kind=schema.get('type')
    if kind and (kind not in types or not isinstance(payload,types[kind]) or (kind in ('number','integer') and isinstance(payload,bool))):raise StoryError('规则输入类型不匹配: '+path)
    if 'enum' in schema and payload not in schema['enum']:raise StoryError('规则输入不在允许值中: '+path)
    if isinstance(payload,dict):
        for key in schema.get('required',[]):
            if key not in payload:raise StoryError('缺少规则输入: '+path+'.'+key)
        for key,child in schema.get('properties',{}).items():
            if key in payload:validate_input(payload[key],child,path+'.'+key)
    if isinstance(payload,list):
        for i,child in enumerate(payload):validate_input(child,schema.get('items',{}),path+'['+str(i)+']')


def cancel_execution(execution_id):
    with _process_lock:
        _cancelled.add(execution_id)
        process=_processes.get(execution_id)
        if process and process.poll() is None:process.kill()


def validate_rule(rule):
    if not isinstance(rule,dict):raise StoryError('规则必须为 JSON 对象')
    if rule.get('schema',1)!=1 or rule.get('syntax') not in ('autocombat-v1','json-v1'):
        raise StoryError('无效规则语法或版本')
    executor=rule.get('executor',{})
    if not isinstance(executor,dict):raise StoryError('规则 executor 必须为对象')
    if executor.get('kind') not in ('builtin','http','exec'): raise StoryError('未知规则执行器')
    if executor['kind']=='builtin' and (executor.get('target')!='AUTOCOMBAT' or rule['syntax']!='autocombat-v1'):raise StoryError('内置执行器仅支持 AUTOCOMBAT')
    if executor['kind']!='builtin' and rule['syntax']!='json-v1':raise StoryError('外部执行器使用 json-v1 语法')
    if executor['kind']=='http' and not str(executor.get('url','')).startswith(('http://','https://')): raise StoryError('HTTP 规则需要 URL')
    if executor['kind']=='exec' and (not isinstance(executor.get('argv'),list) or not executor['argv'] or any(not isinstance(x,str) for x in executor['argv'])): raise StoryError('程序规则需要 argv 字符串数组')
    if not isinstance(rule.get('returns'),list) or not rule['returns'] or any(not isinstance(x,str) or not x for x in rule['returns']): raise StoryError('规则需要声明字符串返回码')
    return rule


def execute_rule(rule, payload, execution_id, cwd=None):
    validate_rule(rule); executor=rule['executor']; kind=executor['kind']
    validate_input(payload,rule.get('inputSchema',{}))
    with _process_lock:
        if execution_id in _cancelled:raise StoryError('执行已取消')
    request=dict(schema=1,execution_id=execution_id,input=payload)
    raw=json.dumps(request,ensure_ascii=False).encode('utf-8'); timeout=max(1,min(float(executor.get('timeout',30)),3600))
    try:
        if kind=='http':
            req=urllib.request.Request(executor['url'],data=raw,headers={'Content-Type':'application/json','Idempotency-Key':execution_id},method='POST')
            with urllib.request.urlopen(req,timeout=timeout) as response: output=response.read(4*1024*1024)
        elif kind=='exec':
            with _process_lock:
                if execution_id in _cancelled:raise StoryError('执行已取消')
                process=subprocess.Popen(executor['argv'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=cwd,shell=False)
                _processes[execution_id]=process
            try:
                try:output,stderr=process.communicate(raw,timeout=timeout)
                except subprocess.TimeoutExpired:process.kill();process.communicate();raise
                if process.returncode: raise StoryError('规则程序失败: '+stderr.decode('utf-8',errors='replace')[:1000])
            finally:
                with _process_lock:_processes.pop(execution_id,None)
        else: raise StoryError('内置规则应通过战斗适配器执行')
        result=json.loads(output)
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:
        raise StoryError('规则执行失败: '+str(error)) from error
    if not isinstance(result,dict) or result.get('result') not in rule['returns']: raise StoryError('规则返回了未声明的结果')
    return result
