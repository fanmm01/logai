"""Small Pratt expression evaluator. No Python/JavaScript execution."""
import re
import json
import operator
from story_models import StoryError

TOKEN = re.compile(r'\s*(?:(\d+(?:\.\d+)?)|("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|([^\W\d]\w*)|(==|!=|<=|>=|&&|\|\||[()+*/%!?<>=,:.-]))', re.UNICODE)
PRECEDENCE = {'||': 1, '&&': 2, '==': 3, '!=': 3, '<': 4, '>': 4, '<=': 4, '>=': 4, '+': 5, '-': 5, '*': 6, '/': 6, '%': 6}
OPS = {'+': operator.add, '-': operator.sub, '*': operator.mul, '/': operator.truediv, '%': operator.mod,
       '==': operator.eq, '!=': operator.ne, '<': operator.lt, '>': operator.gt, '<=': operator.le, '>=': operator.ge}


def parse_expression(source):
    tokens, pos = [], 0
    source = source.strip()
    while pos < len(source):
        match = TOKEN.match(source, pos)
        if not match:
            raise StoryError('表达式中存在无效字符: ' + source[pos:pos + 20])
        number, string, name, symbol = match.groups()
        tokens.append(('number', float(number) if '.' in number else int(number)) if number else
                      ('value', json.loads(string) if string.startswith('"') else bytes_string(string)) if string else
                      ('name', name) if name else (symbol, symbol))
        pos = match.end()
    tokens.append(('eof', ''))
    index = 0

    def take(kind=None):
        nonlocal index
        item = tokens[index]
        if kind and item[0] != kind:
            raise StoryError('表达式需要 ' + kind)
        index += 1
        return item

    def expression(minimum=0):
        kind, value = take()
        if kind in ('number', 'value'):
            left = ('value', value)
        elif kind == 'name':
            if tokens[index][0] == '(':
                take('(')
                args = []
                if tokens[index][0] != ')':
                    args.append(expression())
                    while tokens[index][0] == ',':
                        take(','); args.append(expression())
                take(')')
                left = ('call', value, args)
            else:
                left = ('value', {'true': True, 'false': False}.get(value)) if value in ('true', 'false') else ('name', value)
        elif kind == '(':
            left = expression(); take(')')
        elif kind in ('!', '-', '+'):
            left = ('unary', kind, expression(7))
        else:
            raise StoryError('无效表达式')
        while tokens[index][0] in PRECEDENCE and PRECEDENCE[tokens[index][0]] >= minimum:
            op = take()[0]
            left = ('binary', op, left, expression(PRECEDENCE[op] + 1))
        if minimum == 0 and tokens[index][0] == '?':
            take('?'); yes = expression(); take(':'); left = ('ternary', left, yes, expression())
        return left
    result = expression()
    take('eof')
    return result


def bytes_string(value):
    return re.sub(r'\\(.)', r'\1', value[1:-1])


def evaluate(node, variables=None, builtins=None):
    variables, builtins = variables or {}, builtins or {}
    kind = node[0]
    if kind == 'value':
        return node[1]
    if kind == 'name':
        return variables.get(node[1], 0)
    if kind == 'unary':
        value = evaluate(node[2], variables, builtins)
        return not value if node[1] == '!' else -value if node[1] == '-' else +value
    if kind == 'ternary':
        return evaluate(node[2] if evaluate(node[1], variables, builtins) else node[3], variables, builtins)
    if kind == 'call':
        name = node[1]
        if name not in builtins:
            raise StoryError('未知内置方法: ' + name)
        args = [a[1] if name in ('time', 'isAlive') and a[0] == 'name' else evaluate(a, variables, builtins) for a in node[2]]
        return builtins[name](*args)
    left = evaluate(node[2], variables, builtins)
    op = node[1]
    if op == '&&':
        return bool(left) and bool(evaluate(node[3], variables, builtins))
    if op == '||':
        return bool(left) or bool(evaluate(node[3], variables, builtins))
    try:
        return OPS[op](left, evaluate(node[3], variables, builtins))
    except (TypeError, ZeroDivisionError, OverflowError) as error:
        raise StoryError('表达式计算失败: ' + str(error)) from error


def value(source, variables=None, builtins=None):
    return evaluate(parse_expression(source), variables, builtins)
