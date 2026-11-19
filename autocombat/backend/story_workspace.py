"""Disk workspaces, lossless saves and immutable published story versions."""
import copy
import hashlib
import json
import os
import secrets
import shutil
import threading
import re
from pathlib import Path
from story_models import StoryError
from story_flow import parse_flow, resolve_target
from story_text import parse_text, walk_ops
from story_game import parse_game
from story_rules import validate_rule
from story_gates import resolve_gate_target

TEXT_SUFFIXES={'.txt','.chos','.gmf','.rule','.flow','.json','.css'}
EXCLUDED={'.git','node_modules','.ui-runtime','.story-transactions','__pycache__'}


def digest(data): return hashlib.sha256(data).hexdigest()


def compile_project(texts, config=None, characters=None):
    try:config=config or json.loads(texts.get('story.project.json','{}'))
    except ValueError as error:raise StoryError('项目配置 JSON 无效: '+str(error),'story.project.json') from error
    if not isinstance(config,dict) or not isinstance(config.get('figures',{}),dict) or any(not isinstance(v,str) for v in config.get('figures',{}).values()) or not isinstance(config.get('chapters',{}),dict):
        raise StoryError('项目配置需要对象；figures 应为名称到文件名的映射','story.project.json')
    project=dict(config=config,texts=texts,flows={},prose={},games={},rules={})
    for path,text in texts.items():
        try:
            if path.endswith('.flow'): project['flows'][path]=parse_flow(text,path)
            elif path.endswith(('.txt','.chos')): project['prose'][path]=parse_text(text,path,config.get('figures',{}))
            elif path.endswith('.rule'): project['rules'][Path(path).stem]=validate_rule(json.loads(text))
        except StoryError as error:
            if not error.path: error.path=path
            raise
        except ValueError as error: raise StoryError(str(error),path) from error
    for path,text in texts.items():
        if path.endswith('.gmf'): project['games'][path]=parse_game(text,path,project['rules'],characters)
    for path,flow in project['flows'].items():
        for entry in flow['entries']:
            if entry['kind']=='continue':
                source=resolve_target(entry['source'],texts,('.flow',))
                if source not in project['flows']: raise StoryError('接续源不是 flow',path)
                if not any(op.get('target')==entry['tag'] for op in project['flows'][source]['instructions']): raise StoryError('接续源缺少标签 '+entry['tag'],path)
        for op in flow['instructions']:
            if op['kind']=='gate': resolve_gate_target(project,op['target'])
            if op['kind']=='jump':
                target=op['target']
                if target.startswith(('UNLOCK@','LOCK@')): resolve_gate_target(project,target.split('@',1)[1]); continue
                if target in flow['labels'] or target in ('END','RETRY','START','NONE','') or target.startswith(('CONTINUE-','(')): continue
                resolved=resolve_target(target,texts) if Path(target).suffix.lower() not in ('.mp4','.webm','.ogg') else target
                if op.get('branches') and resolved in project['prose']:
                    choices=[x for x in walk_ops(project['prose'][resolved]) if x['kind']=='choice']
                    if not choices: raise StoryError('分支源没有选择块',path,op['line'])
                    missing={o['id'] for o in choices[0]['options']}-set(op['branches'])
                    if missing: raise StoryError('流程缺少选项分支: '+','.join(sorted(missing)),path,op['line'])
                if op.get('branches') and resolved in project['games']:
                    game=project['games'][resolved]
                    returns={r['result'] for r in game['returns']} if 'returns' in game else set(project['rules'][game['rule']]['returns'])
                    missing=returns-set(op['branches'])
                    if missing:raise StoryError('流程缺少游戏结果分支: '+','.join(sorted(missing)),path,op['line'])
    for path,ops in project['prose'].items():
        for op in walk_ops(ops):
            if op['kind']=='gate': resolve_gate_target(project,op['target'])
    if not project['flows']: raise StoryError('项目没有 .flow 文件')
    return project


class Workspaces:
    def __init__(self,runtime,store,characters=None):
        self.runtime,self.store,self.characters=Path(runtime),store,characters
        self.base=self.runtime/'story';self.base.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.cache={}

    def open_workspace(self,path):
        root=Path(path).expanduser().resolve()
        if not root.is_dir(): raise StoryError('工作区目录不存在: '+str(root))
        identifier=digest(str(root).encode())[:20]
        workspace=dict(id=identifier,path=str(root),name=root.name)
        self.store.put('story_workspace',workspace)
        self.recover_transactions(workspace)
        return workspace

    def create_workspace(self,parent,name):
        if not isinstance(name,str) or not name.strip() or len(name.strip())>100:
            raise StoryError('请输入 1–100 字的故事名称')
        name=name.strip()
        root=Path(parent)/('story-'+secrets.token_hex(6))
        config=dict(id=root.name,name=name,figures={},chapters={'XU':dict(title='序章',description='故事待创作。',order=1)})
        texts={'story.project.json':json.dumps(config,ensure_ascii=False,indent=2)+'\n','MAIN':'XU\n','XU.flow':'@START:\n->opening\n->END\n','opening.txt':'：'+name+'\n：故事待创作。\n'}
        compile_project(texts)
        root.mkdir(parents=True,exist_ok=False)
        for path,text in texts.items(): (root/path).write_text(text,encoding='utf-8')
        return self.open_workspace(root)

    def lookup(self,identifier):
        workspace=self.store.get('story_workspace',identifier)
        if not workspace: raise StoryError('工作区不存在')
        return workspace

    def resolve_path(self,workspace,path):
        root=Path(workspace['path']); candidate=(root/path).resolve()
        if not candidate.is_relative_to(root) or any(x in EXCLUDED for x in candidate.relative_to(root).parts): raise StoryError('路径必须位于所选工作区')
        return candidate

    def scan_files(self,workspace):
        root=Path(workspace['path']); result=[]
        for folder,dirs,names in os.walk(root):
            dirs[:]=[d for d in dirs if d not in EXCLUDED and not d.startswith('.')]
            for name in sorted(names):
                if name.startswith('.'): continue
                p=Path(folder)/name
                if p.is_symlink(): continue
                relative=p.relative_to(root).as_posix()
                result.append(dict(path=relative,size=p.stat().st_size,text=p.suffix.lower() in TEXT_SUFFIXES or name=='MAIN'))
        return sorted(result,key=lambda x:x['path'])

    def read_file(self,workspace,path):
        p=self.resolve_path(workspace,path)
        if not p.is_file(): raise StoryError('文件不存在: '+path)
        data=p.read_bytes()
        try: content=data.decode('utf-8-sig').replace('\r\n','\n')
        except UnicodeDecodeError as error: raise StoryError('该文件不是 UTF-8 文本',path) from error
        return dict(path=path,content=content,hash=digest(data))

    def read_project(self,workspace,overlays=None):
        texts={x['path']:self.read_file(workspace,x['path'])['content'] for x in self.scan_files(workspace) if x['text']}
        texts.update(overlays or {})
        return compile_project(texts,characters=self.characters)

    def recover_transactions(self,workspace):
        folder=Path(workspace['path'])/'.story-transactions'
        if not folder.exists(): return
        for txn in folder.iterdir():
            journal=txn/'journal.json'
            if not journal.exists(): continue
            data=json.loads(journal.read_text(encoding='utf-8'))
            if data['status']=='pending':
                for i,entry in enumerate(data['files']):
                    p=self.resolve_path(workspace,entry['path'])
                    if entry['existed']: p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((txn/str(i)).read_bytes())
                    elif p.exists(): p.unlink()
            shutil.rmtree(txn)

    def apply_file_transaction(self,workspace,changes):
        with self.lock:
            if not isinstance(changes,list) or not changes: raise StoryError('缺少文件修改')
            prepared={}
            for change in changes:
                path=change['path'];p=self.resolve_path(workspace,path);current=p.read_bytes() if p.exists() else None
                if change.get('baseHash') != (digest(current) if current is not None else None): raise StoryError('文件已被其他编辑修改: '+path,path)
                if change.get('op','write')=='rename':
                    dest=change['to'];target=self.resolve_path(workspace,dest)
                    if target.exists(): raise StoryError('目标文件已存在',dest)
                    if current is None: raise StoryError('源文件不存在',path)
                    prepared[path]=None;prepared[dest]=current
                    config_path=self.resolve_path(workspace,'story.project.json')
                    if config_path.exists() and path!='story.project.json':
                        config=json.loads(config_path.read_text(encoding='utf-8-sig'))
                        for section in ('chapters','figures'):
                            entries=config.get(section,{})
                            replacements={path:dest,str(Path(path).with_suffix('')):str(Path(dest).with_suffix(''))}
                            if path.startswith('figure/') and dest.startswith('figure/'):replacements[path[7:]]=dest[7:]
                            if section=='chapters':config[section]={replacements.get(k,k):v for k,v in entries.items()}
                            else:config[section]={k:replacements.get(v,v) for k,v in entries.items()}
                        ids=config.setdefault('content_ids',{})
                        ids[dest]=ids.pop(path,path)
                        prepared['story.project.json']=json.dumps(config,ensure_ascii=False,indent=2).encode('utf-8')
                    oldstem=str(Path(path).with_suffix(''));newstem=str(Path(dest).with_suffix(''))
                    for entry in self.scan_files(workspace):
                        if entry['text'] and entry['path'] not in prepared:
                            original=self.read_file(workspace,entry['path'])['content']
                            replaced=self.rename_references(workspace,entry['path'],original,path,dest)
                            if replaced!=original: prepared[entry['path']]=replaced.encode('utf-8')
                else:
                    prepared[path]=None if change.get('op')=='delete' else change.get('content','').encode('utf-8')
            txn=Path(workspace['path'])/'.story-transactions'/secrets.token_hex(8);txn.mkdir(parents=True)
            journal=dict(status='pending',files=[])
            for i,(path,data) in enumerate(prepared.items()):
                p=self.resolve_path(workspace,path);journal['files'].append(dict(path=path,existed=p.exists()))
                if p.exists(): (txn/str(i)).write_bytes(p.read_bytes())
            (txn/'journal.json').write_text(json.dumps(journal),encoding='utf-8')
            try:
                for path,data in prepared.items():
                    p=self.resolve_path(workspace,path)
                    if data is None:
                        if p.exists(): p.unlink()
                    else:
                        p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_name(p.name+'.story-tmp');temp.write_bytes(data);os.replace(temp,p)
                journal['status']='committed';(txn/'journal.json').write_text(json.dumps(journal),encoding='utf-8')
            finally:
                self.recover_transactions(workspace)
            return [self.read_file(workspace,p) for p in prepared if prepared[p] is not None]

    def rename_references(self,workspace,source_path,source,old,new):
        """Rewrite recognized references only; never replace ordinary story words."""
        oldstem=str(Path(old).with_suffix('')).replace('\\','/');newstem=str(Path(new).with_suffix('')).replace('\\','/')
        if source_path=='MAIN':return source.replace(source.strip(),newstem if source.strip()==oldstem else new) if source.strip() in (old,oldstem) else source
        pairs=[(old,new)]
        if Path(old).suffix in ('.txt','.chos','.gmf'):
            names={x['path']:True for x in self.scan_files(workspace)}
            try:default=resolve_target(oldstem,names)
            except StoryError:default=None
            labels={}
            if source_path.endswith('.flow'):
                try:labels=parse_flow(source,source_path)['labels']
                except StoryError:pass
            if default==old and oldstem not in labels:pairs.append((oldstem,newstem))
        if Path(old).suffix=='.flow':pairs.append((oldstem,newstem))
        for before,after in pairs:
            source=re.sub(r'(->\s*|(?:UNLOCK|LOCK)@)'+re.escape(before)+r'(?=$|[\s;<{\[}])',lambda m:m[1]+after,source,flags=re.M)
            if old.endswith('.flow'):source=re.sub(r'@'+re.escape(before)+r'(?=\.CONTINUE-)',lambda m:'@'+after,source)
        if old.endswith('.rule'):
            source=re.sub(r'(?m)^(\s*rule\s+)'+re.escape(oldstem)+r'(?=\.?\s*$)',lambda m:m[1]+newstem,source)
        if source_path.endswith(('.txt','.chos','.flow')):
            aliases=[(old,new)]
            if '/' in old and old.split('/',1)[0]==new.split('/',1)[0]:aliases.append((old.split('/',1)[1],new.split('/',1)[1]))
            for before,after in aliases:
                source=re.sub(r'((?:changeBg|changeFigure|bgm|playVideo|callScene|changeScene|playEffect):\s*|\\(?:m|bg|video|se|vocal)\s+)'+re.escape(before)+r'(?=$|[\s;])',lambda m:m[1]+after,source,flags=re.M)
        return source

    def publish_version(self,workspace,overlays=None,preview=False):
        with self.lock:
            project=self.read_project(workspace,overlays); identifier=('preview-'+workspace['id']) if preview else project['config'].get('id',workspace['id'])
            if not isinstance(identifier,str) or not identifier or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in identifier): raise StoryError('项目 id 只允许字母数字下划线和连字符')
            files=self.scan_files(workspace); hashes={x['path']:digest(self.resolve_path(workspace,x['path']).read_bytes()) for x in files}
            hashes.update({p:digest(text.encode('utf-8')) for p,text in (overlays or {}).items()})
            version=digest(json.dumps(hashes,sort_keys=True).encode())[:24];folder=self.base/identifier/version
            if not folder.exists():
                temp=folder.with_name(version+'.tmp-'+secrets.token_hex(4));temp.mkdir(parents=True)
                for entry in files:
                    target=temp/entry['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(self.resolve_path(workspace,entry['path']),target)
                for name,text in (overlays or {}).items():
                    target=temp/name
                    if not target.resolve().is_relative_to(temp.resolve()): raise StoryError('草稿路径越界')
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8')
                (temp/'.compiled.json').write_text(json.dumps(project,ensure_ascii=False),encoding='utf-8');temp.rename(folder)
            record=dict(id=identifier,version=version,name=project['config'].get('name',workspace['name']),workspace=workspace['id'],preview=preview)
            self.store.put('story_project',record);self.cache[(identifier,version)]=project
            return record

    def load_version(self,identifier,version=None):
        record=self.store.get('story_project',identifier)
        if not record: raise StoryError('项目不存在')
        version=version or record['version'];key=(identifier,version)
        if not isinstance(version,str) or not re_version(version): raise StoryError('无效发布版本')
        if key not in self.cache:
            p=self.base/identifier/version/'.compiled.json'
            if not p.is_file(): raise StoryError('发布版本不存在')
            self.cache[key]=json.loads(p.read_text(encoding='utf-8'))
        return self.cache[key],self.base/identifier/version


def re_version(value):
    return len(value)==24 and all(c in '0123456789abcdef' for c in value)
