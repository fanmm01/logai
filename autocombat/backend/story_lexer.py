"""Lossless offsets for flow and game scripts."""
import re
from dataclasses import dataclass
from story_models import StoryError


@dataclass
class Token:
    value: str
    start: int
    end: int
    line: int


PATTERN = re.compile(r'(?P<space>[^\S\n]+)|(?P<comment>//[^\n]*)|(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|(?P<symbol>->|\|\||&&|==|!=|<=|>=|[{}();?:,<\[\]=+*/%!])|(?P<newline>\n)|(?P<word>[^\s{}();?:,<\[\]=+*/%!]+)')


def tokenize(source, path=''):
    result, pos, line = [], 0, 1
    while pos < len(source):
        match = PATTERN.match(source, pos)
        if not match:
            raise StoryError('无法识别的字符', path, line)
        if match.lastgroup not in ('space', 'comment'):
            result.append(Token(match.group(), pos, match.end(), line))
        line += match.group().count('\n')
        pos = match.end()
    result.append(Token('<EOF>', len(source), len(source), line))
    return result


def strip_comment(line):
    quoted, escaped = '', False
    for i, c in enumerate(line):
        if escaped:
            escaped = False; continue
        if c == '\\':
            escaped = True; continue
        if quoted:
            if c == quoted: quoted = ''
        elif c in ('"', "'"):
            quoted = c
        elif line[i:i+2] == '//' and (i == 0 or line[i-1].isspace() or line[i-1] in ';}'):
            return line[:i]
    return line
