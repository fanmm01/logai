"""Round-trip disabled spell effects as readable, non-executable Python comments."""
import ast
import copy
import io
import pprint
import tokenize

START = '# autocombat:disabled-effect'
END = '# autocombat:end-disabled-effect'


def field(node, key):
    if isinstance(node, ast.Dict):
        return next((value for name, value in zip(node.keys, node.values)
                     if isinstance(name, ast.Constant) and name.value == key), None)
    return None


def runtime_character(character):
    result = copy.deepcopy(character)
    for spell in result.get('spells', []):
        spell['effects'] = [{key: value for key, value in effect.items() if key != '_disabled'}
                            for effect in spell.get('effects', []) if not effect.get('_disabled', False)]
    return result


class EffectComments:
    def __init__(self, source):
        self.lines = source.splitlines(keepends=True)
        self.bases = [0]
        for line in self.lines:
            self.bases.append(self.bases[-1] + len(line.encode('utf-8')))
        self.comments = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                line, column = token.start
                position = self.bases[line-1] + len(self.lines[line-1][:column].encode('utf-8'))
                self.comments.append((position, token.string))

    def position(self, node, end=False):
        line, column = (node.end_lineno, node.end_col_offset) if end else (node.lineno, node.col_offset)
        return self.bases[line-1] + column

    def restore(self, character, assignment):
        result = copy.deepcopy(character)
        spells = field(assignment.value, 'spells')
        if not isinstance(spells, ast.List):
            return result
        for spell, spell_node in zip(result.get('spells', []), spells.elts):
            effects = field(spell_node, 'effects')
            if not isinstance(effects, ast.List):
                continue
            first, last = self.position(effects), self.position(effects, True)
            entries = [(self.position(node), {**effect, '_disabled': False})
                       for node, effect in zip(effects.elts, spell.get('effects', []))]
            pending, start = None, None
            for position, comment in self.comments:
                if not first < position < last:
                    continue
                if comment == START:
                    if pending is not None:
                        raise ValueError('禁用效果注释不能嵌套')
                    pending, start = [], position
                elif comment == END:
                    if pending is None:
                        raise ValueError('禁用效果注释缺少开始标记')
                    value = ast.literal_eval('\n'.join(pending))
                    if not isinstance(value, dict):
                        raise ValueError('禁用效果注释必须保留完整效果字典')
                    entries.append((start, {**value, '_disabled': True}))
                    pending = None
                elif pending is not None:
                    pending.append(comment[2:] if comment.startswith('# ') else comment[1:])
            if pending is not None:
                raise ValueError('禁用效果注释缺少结束标记')
            spell['effects'] = [value for _, value in sorted(entries, key=lambda item: item[0])]
        return result


def format_character(name, character):
    source = name + ' = ' + pprint.pformat(runtime_character(character), width=110, sort_dicts=False) + '\n'
    spells = field(ast.parse(source).body[0].value, 'spells')
    if not isinstance(spells, ast.List):
        return source
    positions = EffectComments(source)
    edits = []
    for spell, spell_node in zip(character.get('spells', []), spells.elts):
        if not any(effect.get('_disabled', False) for effect in spell.get('effects', [])):
            continue
        node = field(spell_node, 'effects')
        line = positions.lines[node.lineno-1]
        indent = len(line) - len(line.lstrip())
        pad = ' ' * (indent + 4)
        rows = ['[']
        for effect in spell.get('effects', []):
            value = {key: val for key, val in effect.items() if key != '_disabled'}
            lines = pprint.pformat(value, width=max(30, 110-len(pad)), sort_dicts=False).splitlines()
            if effect.get('_disabled', False):
                rows.append(pad + START)
                rows.extend(pad + '# ' + item for item in lines)
                rows.append(pad + END)
            else:
                rows.extend(pad + item for item in lines[:-1])
                rows.append(pad + lines[-1] + ',')
        rows.append(' ' * indent + ']')
        edits.append((positions.position(node), positions.position(node, True), '\n'.join(rows).encode('utf-8')))
    output = source.encode('utf-8')
    for first, last, replacement in sorted(edits, reverse=True):
        output = output[:first] + replacement + output[last:]
    return output.decode('utf-8')
