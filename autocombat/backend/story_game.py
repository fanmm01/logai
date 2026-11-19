"""Declarative game-block parser and carry policies."""
import re
import json
from story_models import StoryError
from story_expression import parse_expression
from story_lexer import strip_comment

GROUPS = ('resources', 'statuses', 'cooldowns', 'inventory', 'summons', 'usage')


def normalize_carry_policy(preset='FULL', overrides=None):
    if preset not in ('FULL', 'RESOURCES', 'NONE'): raise StoryError('未知继承策略: '+preset)
    policy={k:preset=='FULL' or (preset=='RESOURCES' and k in ('resources','inventory')) for k in GROUPS}
    for key,val in (overrides or {}).items():
        if key not in GROUPS or not isinstance(val,bool): raise StoryError('无效继承分组: '+key)
        policy[key]=val
    return policy


def parse_game(source, path='', rules=None, characters=None):
    source='\n'.join(strip_comment(x) for x in source.lstrip('\ufeff').splitlines())
    m=re.match(r'\s*rule\s+([\w-]+)\.?\s*(?:\n|$)',source)
    if not m: raise StoryError('游戏块首行需要 rule 规则名',path)
    name=m[1]; text=source[m.end():].strip()
    rule=(rules or {}).get(name)
    if rules is not None and not rule: raise StoryError('找不到规则: '+name,path)
    if name!='AUTOCOMBAT' or (rule and rule.get('syntax')=='json-v1'):
        try: payload=json.loads(text)
        except ValueError as error: raise StoryError('通用游戏块正文必须为 JSON: '+str(error),path) from error
        return dict(rule=name,payload=payload,display=(rule or {}).get('display','NONE'))
    teams, battles, returns = {}, [], []
    control, bind, preset, overrides, display = 'NONE', 'Y', 'FULL', {}, 'AUTOCOMBAT_DEFAULT'
    # Each statement is separated by semicolon or line break; if/else returns may share a line.
    text=re.sub(r'\belse\s+return\b','\nelse return',text)
    statements=[x.strip() for x in re.split(r'[;\n]',text) if x.strip()]
    previous_condition=None
    for statement in statements:
        setting=re.fullmatch(r'set\s+(\w+)\s*:\s*(.*)',statement,re.I)
        battle=re.fullmatch(r'battle\s+(\w+)\s+vs\s+(\w+)(?:\s+(KEEP_STATUS))?',statement,re.I)
        ret=re.fullmatch(r'(?:if\s*\((.*)\)\s*)?return\s+["\']([^"\']+)["\']',statement)
        otherwise=re.fullmatch(r'else\s+return\s+["\']([^"\']+)["\']',statement)
        if setting:
            key,arg=setting[1].upper(),setting[2]
            if key=='TEAMS':
                matched=list(re.finditer(r'(\w+)\s*\{([^}]+)\}',arg)); remainder=re.sub(r'(\w+)\s*\{([^}]+)\}','',arg).replace(',','').strip()
                if not matched or remainder: raise StoryError('无效队伍定义',path)
                for team in matched:
                    members=[x.strip() for x in re.split('[,，]',team[2])]
                    if team[1] in teams or not 1<=len(members)<=6 or len(set(members))!=len(members): raise StoryError('重复队伍或无效成员数',path)
                    if characters is not None and any(x not in characters for x in members): raise StoryError('队伍含未知角色',path)
                    teams[team[1]]=members
            elif key=='CONTROL': control=arg
            elif key=='BIND': bind=arg
            elif key=='CARRY': preset=arg.upper()
            elif key=='CARRY_FIELDS':
                for pair in arg.split(','):
                    parts=pair.strip().split('=')
                    if len(parts)!=2 or parts[1].lower() not in ('true','false'): raise StoryError('继承覆盖应为 分组=true/false',path)
                    overrides[parts[0]]=parts[1].lower()=='true'
            else: raise StoryError('未知游戏设置: '+key,path)
        elif battle:
            battles.append(dict(a=battle[1],b=battle[2],keep=bool(battle[3]),carry=normalize_carry_policy(preset,overrides)))
        elif ret:
            if ret[1]: parse_expression(ret[1])
            previous_condition=ret[1]; returns.append(dict(condition=ret[1],result=ret[2]))
        elif otherwise:
            if previous_condition is None: raise StoryError('else 缺少 if return',path)
            returns.append(dict(condition=None,result=otherwise[1]))
        elif statement.startswith('DISPLAY:'): display=statement.split(':',1)[1].strip()
        else: raise StoryError('未知游戏语句: '+statement,path)
    if not battles or bind not in teams: raise StoryError('游戏块需要战斗和有效 BIND 队伍',path)
    for battle in battles:
        if battle['a'] not in teams or battle['b'] not in teams or battle['a']==battle['b']: raise StoryError('战斗引用无效队伍',path)
    if control!='NONE' and any(t.strip() not in teams for t in control.split(',')): raise StoryError('CONTROL 应为 NONE 或队伍名列表',path)
    if display!='AUTOCOMBAT_DEFAULT': raise StoryError('AUTOCOMBAT 需要 DISPLAY:AUTOCOMBAT_DEFAULT',path)
    if rule and any(x['result'] not in rule['returns'] for x in returns):raise StoryError('游戏块返回值未在规则中声明',path)
    return dict(rule=name,teams=teams,battles=battles,returns=returns or [dict(condition=None,result='w')],control=control,bind=bind,display=display)
