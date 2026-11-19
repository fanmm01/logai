"""Natural prose -> resumable story operations -> real WebGAL scripts."""
import re
import json
from story_models import StoryError
from story_lexer import strip_comment

NATIVE = set('say changeBg changeFigure changeFigureDiff character bgm playVideo pixiPerform pixiInit intro miniAvatar changeScene choose end setComplexAnimation setFilter label jumpLabel setVar callScene return showVars unlockCg unlockBgm filmMode setTextbox setAnimation playEffect setTempAnimation setTransform setTransition getUserInput applyStyle wait'.split())
GATE = re.compile(r'^(UNLOCK|LOCK)@(.+?);?$')


def parse_mem_arguments(source, path='', line=1):
    source = source.strip()
    if not source or source == 'NONE':
        return []
    names = [x.strip() for x in re.split('[,，]', source)]
    if len(names) > 5:
        raise StoryError('\\mem 最多显示五个角色', path, line)
    if any(not x for x in names) or len(set(names)) != len(names):
        raise StoryError('\\mem 名称不能为空或重复', path, line)
    return names


def parse_text(source, path='', figures=None):
    lines = source.lstrip('\ufeff').replace('\r\n', '\n').split('\n')
    if lines and lines[0].strip() == '#!webgal':
        return [dict(kind='native', script='\n'.join(lines[1:]), line=2, path=path, whole=True)]

    def section(rows, speaker='', forced=False, black=False):
        ops, index = [], 0
        while index < len(rows):
            number, raw = rows[index]; index += 1
            text = strip_comment(raw).strip()
            if not text:
                continue
            # Closing markers may be adjacent and may precede ordinary prose.
            closed = False
            while True:
                closing = re.match(r'^(mem|bg|m|b)\\', text)
                if not closing:
                    break
                closed = True
                kind = closing[1]
                if kind == 'b':
                    black = False
                elif kind == 'mem':
                    ops.append(dict(kind='mem', names=[], line=number, path=path))
                else:
                    ops.append(dict(kind='media', media=kind, asset='NONE', line=number, path=path))
                text = text[closing.end():].lstrip()
            if closed and text == ']':
                text = ''
            if not text:
                continue
            if re.match(r'^\\c\s*\{', text):
                options = []
                while index < len(rows) and rows[index][1].strip() != '}':
                    n, option = rows[index]; index += 1
                    option = strip_comment(option).strip().rstrip(';').strip()
                    if not option: continue
                    m = re.fullmatch(r'(__LOCK\s+)?(\d+)\s*[“"](.*?)[”"]', option)
                    if not m:
                        raise StoryError('选项应为 [__LOCK ]数字“文字”', path, n)
                    key = m[2]
                    if any(x['id'] == key for x in options):
                        raise StoryError('重复选项编号 ' + key, path, n)
                    options.append(dict(id=key, text=m[3], locked=bool(m[1]), body=[]))
                if index >= len(rows) or not options:
                    raise StoryError('选择列表缺少 } 或没有选项', path, number)
                index += 1
                bodies, current, depth = {}, None, 0
                # Branch markers belong to this choice only; nested choices are retained.
                while index < len(rows):
                    n, body = rows[index]; stripped = strip_comment(body).strip()
                    marker = re.match(r'^\\c(\d+)(?:\s+(.*)|$)', stripped) if depth == 0 else None
                    if depth == 0 and stripped == '\\c':
                        index += 1; break
                    if marker:
                        current = marker[1]
                        if current in bodies or not any(o['id'] == current for o in options):
                            raise StoryError('未知或重复选项正文 ' + current, path, n)
                        bodies[current] = []
                        if marker[2]: bodies[current].append((n, marker[2]))
                        index += 1; continue
                    if current is None:
                        break
                    if re.match(r'^\\c\s*\{', stripped): depth += 1
                    elif stripped == '\\c' and depth: depth -= 1
                    bodies[current].append((n, body)); index += 1
                for option in options:
                    option['body'] = section(bodies.get(option['id'], []), speaker, forced, black)
                ops.append(dict(kind='choice', options=options, line=number, path=path))
                continue
            gate = GATE.fullmatch(text)
            if gate:
                ops.append(dict(kind='gate', action=gate[1], target=gate[2].rstrip(';').strip(), line=number, path=path)); continue
            command = re.match(r'^\\(mem|bg|m|video|se|vocal)(?:\s+(.*)|$)', text)
            if command:
                kind, arg = command[1], (command[2] or '').strip()
                if kind == 'mem':
                    names = parse_mem_arguments(arg, path, number)
                    if figures is not None:
                        for name in names:
                            if name not in figures: raise StoryError('未知立绘角色: ' + name, path, number)
                    ops.append(dict(kind='mem', names=names, line=number, path=path))
                else:
                    ops.append(dict(kind='media', media=kind, asset=arg or 'NONE', line=number, path=path))
                continue
            native = re.match(r'^([A-Za-z]+):', text)
            if native and native[1] in NATIVE:
                ops.append(dict(kind='native', script=text, line=number, path=path)); continue
            header = re.match(r'^([^：:"“”\\]{0,60})[：:](.*)$', text)
            if header:
                speaker, text = header[1].strip(), header[2].strip()
                forced = not bool(speaker)
                if not text: continue
            if text.startswith('\\b') and not text.startswith('\\bg'):
                black = True
                text = text[2:].lstrip()
                if not text:
                    continue
            chunks = [text]
            while len(re.search(r'(\\*)$', chunks[-1])[1]) % 2:
                chunks[-1] = chunks[-1][:-1]
                if index >= len(rows): raise StoryError('续行符后缺少内容', path, number)
                chunks.append(rows[index][1].strip()); index += 1
            dialogue = not forced and bool(speaker) and any(c.startswith(('“', '"')) for c in chunks)
            content = '\n'.join(re.sub(r'\\([“"\\])', r'\1', c) for c in chunks)
            ops.append(dict(kind='say', text=content, speaker=speaker if dialogue else '', black=black, line=number, path=path))
        return ops
    return section(list(enumerate(lines, 1)))


def walk_ops(ops):
    for op in ops:
        yield op
        if op['kind'] == 'choice':
            for option in op['options']:
                yield from walk_ops(option['body'])


def escape_webgal_text(text):
    return text.replace('\\', '\\\\').replace(';', '\\;').replace(':', '\\:').replace('|', '\\|').replace('\n', '|').replace(' -', ' \\-')


def compile_scene(ops, figures=None):
    scripts, mapping = [], []
    for op in ops:
        kind = op['kind']
        if kind == 'say':
            text = escape_webgal_text(op['text'])
            script = ('intro:' + text + ';') if op.get('black') else (escape_webgal_text(op.get('speaker', '')) + ':' + text + ';')
        elif kind == 'native':
            script = op['script']
        elif kind == 'media':
            command = {'bg': 'changeBg', 'm': 'bgm', 'video': 'playVideo', 'se': 'playEffect', 'vocal': 'playEffect'}[op['media']]
            asset='none' if op['asset']=='NONE' else op['asset']
            script = f"{command}:{asset} -next;"
            if op['media'] == 'video': script = f"{command}:{op['asset']};"
        elif kind == 'mem':
            names = op['names']; rows = []
            for i in range(5):
                if i < len(names):
                    position = 0 if len(names) == 1 else (-640 + i * 1280 / (len(names) - 1))
                    transform = json.dumps({'position': {'x': position, 'y': 0}, 'scale': {'x': min(1, 3 / len(names)), 'y': min(1, 3 / len(names))}}, separators=(',', ':'))
                    asset = (figures or {}).get(names[i], names[i])
                    rows.append(f'changeFigure:{asset} -id=story-{i} -transform={transform} -next;')
                else:
                    rows.append(f'changeFigure:none -id=story-{i} -next;')
            script = '\n'.join(rows)
        else:
            continue
        scripts.append(script)
        mapping.extend([dict(path=op.get('path', ''), line=op.get('line', 1))] * len(script.splitlines()))
    scripts.append('storyHost:advance;')
    return dict(script='\n'.join(scripts), source_map=mapping)
