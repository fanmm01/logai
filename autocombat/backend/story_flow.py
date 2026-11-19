"""Flow parser, linked instructions and lossless graph edits."""
import re
from pathlib import PurePosixPath
from story_lexer import tokenize,strip_comment
from story_models import StoryError
from story_expression import parse_expression


def parse_entry_header(header, path=''):
    entries = []
    for part in header.strip().rstrip(':').split('||'):
        part = part.strip().lstrip('@')
        if part == 'START': entry = dict(kind='start')
        else:
            m = re.fullmatch(r'(.+)\.(CONTINUE-[\w+\-]+)', part, re.UNICODE)
            if not m: raise StoryError('无效入口: ' + part, path)
            entry = dict(kind='continue', source=m[1].removesuffix('.flow'), tag=m[2])
        if entry in entries: raise StoryError('重复入口: ' + part, path)
        entries.append(entry)
    if not entries: raise StoryError('文件缺少入口', path)
    return entries


def parse_gate_target(target):
    target = target.strip().rstrip(';')
    match = re.fullmatch(r'(.+?)\[(\d+)\]', target)
    return dict(file=match[1], option=match[2]) if match else dict(file=target, option=None)


def parse_flow(source, path=''):
    source = source.lstrip('\ufeff').replace('\r\n', '\n')
    match = re.search(r'(?m)^\s*(@[^\n]+|START\s*:?)\s*$', source)
    if not match: raise StoryError('flow 第一行需要入口文件头', path)
    if any(strip_comment(line).strip() for line in source[:match.start()].splitlines()):raise StoryError('入口文件头必须位于首个有效行',path)
    header = match.group(1).split('//', 1)[0].strip()
    entries = parse_entry_header(header, path)
    prefix = source[:match.end()]
    rest = source[match.end():]
    tokens = tokenize(rest, path); index = 0; line_offset = prefix.count('\n')

    def peek(n=0): return tokens[min(index+n, len(tokens)-1)].value
    def take(expected=None):
        nonlocal index
        token = tokens[index]
        if expected and token.value != expected: raise StoryError('需要 ' + expected, path, token.line + line_offset)
        index += 1
        return token
    def skip():
        while peek() in ('\n', ';'): take()
    def branch_key():
        return peek(1) == ':' and (peek().isdigit() or peek().startswith(('"', "'")))
    def raw_until(stops):
        start = tokens[index].start; depth = 0
        while peek() != '<EOF>':
            if not depth and peek() in stops: break
            token = take().value
            if token in ('(', '[', '{'): depth += 1
            elif token in (')', ']', '}'): depth -= 1
        end = tokens[index-1].end if tokens[index].start != start else start
        return rest[start:end].strip()
    def sequence(stop_branch=False):
        nodes = []; skip()
        while peek() not in ('}', '<EOF>') and not (stop_branch and branch_key()):
            start = take(); token = start.value
            node = dict(kind='', line=start.line + line_offset, start=start.start+len(prefix))
            if token == 'if':
                take('('); begin = tokens[index].start; depth = 1
                while depth:
                    t = take()
                    if t.value == '(': depth += 1
                    if t.value == ')': depth -= 1
                    if t.value == '<EOF>': raise StoryError('if 缺少右括号', path, node['line'])
                expr = rest[begin:t.start].strip(); parse_expression(expr)
                skip()
                if peek() == '{':
                    take('{'); yes = sequence(); take('}')
                else:
                    # A single assignment/control statement following if.
                    yes = one_statement()
                skip(); no = []
                if peek() == 'else':
                    take(); skip()
                    if peek() == '{': take('{'); no = sequence(); take('}')
                    else: no = one_statement()
                node.update(kind='if', expression=expr, yes=yes, no=no)
            elif token == '->':
                target = raw_until({'\n', ';', '{', '}'})
                alias = ''
                # < is an alias delimiter only outside an expression.
                m = re.match(r'^(.*?)\s*<([\w+\-]+)\s*$', target)
                if m and not target.startswith('('): target, alias = m[1].strip(), m[2]
                node.update(kind='jump', target=re.sub(r'\((\.(?:txt|chos|gmf))\)$', r'\1', target), alias=alias)
                if peek() == '{':
                    take('{'); branches = {}; skip()
                    while peek() not in ('}', '<EOF>'):
                        key = take().value.strip('"\''); take(':')
                        if key in branches: raise StoryError('重复分支: ' + key, path, node['line'])
                        branches[key] = sequence(True); skip()
                    take('}'); node['branches'] = branches
            else:
                tail = raw_until({'\n', ';', '}'})
                statement = rest[start.start:tokens[index-1].end].strip() if tail else token
                # raw_until begins after the first token; the original slice preserves spaces.
                gate = re.fullmatch(r'(UNLOCK|LOCK)@(.+)', statement)
                if gate: node.update(kind='gate', action=gate[1], target=gate[2].strip())
                elif token.startswith('\\'):
                    node.update(kind='media', command=statement)
                elif re.match(r'^(changeBg|changeFigure|changeFigureDiff|bgm|playVideo|setTransform|setAnimation|setTempAnimation|setComplexAnimation|setFilter|playEffect|pixiPerform|pixiInit|filmMode|setTextbox|miniAvatar):',statement):
                    node.update(kind='media',command=statement)
                else:
                    assignment = re.fullmatch(r'([^\W\d]\w*)\s*=\s*(.+)', statement, re.UNICODE)
                    if not assignment: raise StoryError('未知流程语句: ' + statement, path, node['line'])
                    parse_expression(assignment[2]); node.update(kind='assign', name=assignment[1], expression=assignment[2])
            node['end'] = tokens[index-1].end + len(prefix)
            node['id'] = f"n{node['start']}"
            nodes.append(node); skip()
        return nodes
    def one_statement():
        # Reuse the sequence parser with a temporary end token after the first semicolon/newline.
        nonlocal tokens
        end = index
        while tokens[end].value not in (';', '\n', '<EOF>', '}'): end += 1
        saved = tokens[end].value; tokens[end].value = '}'
        try: return sequence()
        finally: tokens[end].value = saved

    tree = sequence()
    if peek() != '<EOF>': raise StoryError('多余的右大括号', path, tokens[index].line + line_offset)
    instructions, labels = [], {}
    def compile_sequence(nodes, exit_target=None):
        beginning = len(instructions); exits = []
        for item in nodes:
            op = {k:v for k,v in item.items() if k not in ('yes', 'no', 'branches')}
            i = len(instructions); instructions.append(op)
            if item.get('alias'):
                if item['alias'] in labels: raise StoryError('重复别名: '+item['alias'], path, item['line'])
                labels[item['alias']] = i
            if item['kind'] == 'if':
                op['yes'] = len(instructions); _, yes_exits = compile_sequence(item['yes'])
                instructions.append(dict(kind='goto', target=None)); yes_exits.append(len(instructions)-1)
                op['no'] = len(instructions); _, no_exits = compile_sequence(item['no'])
                finish = len(instructions)
                for e in yes_exits + no_exits: instructions[e]['target'] = finish
            elif 'branches' in item:
                op['branches'] = {}; branch_exits=[]
                for key, body in item['branches'].items():
                    op['branches'][key] = len(instructions); _, inner = compile_sequence(body)
                    instructions.append(dict(kind='goto', target=None)); inner.append(len(instructions)-1); branch_exits.extend(inner)
                for e in branch_exits: instructions[e]['target'] = len(instructions)
            if item['kind'] == 'jump' and not item['target']:
                op.update(kind='goto', target=None); exits.append(i)
            op['next'] = len(instructions)
        return beginning, exits
    _, exits = compile_sequence(tree)
    for e in exits: instructions[e]['target'] = len(instructions)
    return dict(path=path, entries=entries, instructions=instructions, labels=labels, tree=tree, source=source,header_start=match.start(1),header_end=match.end(1))


def resolve_target(target, files, suffixes=('.txt', '.chos', '.gmf')):
    target = target.strip().replace('\\', '/')
    if target in files: return target
    for suffix in suffixes:
        if target + suffix in files: return target + suffix
    raise StoryError('找不到内容文件: ' + target)


def build_graph(flow):
    nodes, edges = [], []
    ops = flow['instructions']
    def identifier(index): return ops[index].get('id', f'join{index}') if index < len(ops) else 'end'
    for i,entry in enumerate(flow['entries']):
        label='START' if entry['kind']=='start' else entry['source']+'.'+entry['tag']
        nodes.append(dict(id='entry'+str(i),kind='entry',label=label,line=1,index=-1,start=flow.get('header_start',0),end=flow.get('header_end',len(flow['source'].splitlines()[0]))))
        edges.append(dict(id='entry-edge'+str(i),source='entry'+str(i),target=identifier(0),label='或'))
    for i, op in enumerate(ops):
        if op['kind'] == 'goto' and 'id' not in op: continue
        label=(op['action']+'@'+op['target']) if op['kind']=='gate' else (op['name']+'='+op['expression']) if op['kind']=='assign' else op.get('target') or op.get('command') or op.get('expression') or op.get('name') or op['kind']
        nodes.append(dict(id=identifier(i), kind=op['kind'], label=label, line=op.get('line', 1), index=i, start=op.get('start'), end=op.get('end')))
        conditional=re.fullmatch(r'\((.*)\)\s*\?\s*([^:]+)\s*:\s*(.+)',str(op.get('target','')))
        if op['kind']=='jump' and conditional:
            destinations=[('是',flow['labels'].get(conditional[2].strip(),len(ops))),('否',flow['labels'].get(conditional[3].strip(),len(ops)))]
        elif op['kind']=='jump' and op.get('target') in flow['labels']:
            destinations=[('跳转',flow['labels'][op['target']])]
        elif op['kind']=='jump' and op.get('target')=='RETRY':
            previous=next((j for j in range(i-1,-1,-1) if ops[j].get('branches')),0)
            destinations=[('RETRY',previous)]
        elif op['kind']=='jump' and (op.get('target')=='END' or op.get('target','').startswith('CONTINUE-')):
            destinations=[(op['target'],len(ops))]
        else:
            destinations = list(op.get('branches', {}).items()) if op.get('branches') else [('是', op['yes']), ('否', op['no'])] if op['kind'] == 'if' else [('', op.get('next', i+1))]
        for label, dest in destinations:
            if not isinstance(dest, int): continue
            visited=set()
            while dest < len(ops) and ops[dest]['kind'] == 'goto' and 'id' not in ops[dest] and dest not in visited:
                visited.add(dest); dest=ops[dest]['target']
            edges.append(dict(id=f'{i}-{label}-{dest}', source=identifier(i), target=identifier(dest), label=label))
    nodes.append(dict(id='end', kind='end', label='结束', line=1, index=len(ops)))
    return dict(nodes=nodes, edges=edges, entries=flow['entries'])


def apply_graph_edit(source, operation, path=''):
    source=source.lstrip('\ufeff').replace('\r\n','\n')
    flow=parse_flow(source, path); kind=operation['op']
    if kind == 'header':
        line=next(i for i,x in enumerate(source.splitlines()) if x.strip().startswith(('@','START')))
        rows=source.splitlines(keepends=True); rows[line]=operation['text'].rstrip('\n')+'\n'; result=''.join(rows)
    elif kind == 'append':
        result=source.rstrip()+'\n'+operation['text'].rstrip()+'\n'
    else:
        node=next((x for x in flow['instructions'] if x.get('id')==operation.get('id')),None)
        if not node: raise StoryError('节点已变化，请刷新流程图',path)
        replacement='' if kind=='delete' else operation.get('text','')
        result=source[:node['start']]+replacement+source[node['end']:]
    parse_flow(result,path)
    return result
