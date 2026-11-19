"""Versioned, JSON-native story contracts and source diagnostics."""
from dataclasses import dataclass, asdict

SCHEMA_VERSION = 1


class StoryError(ValueError):
    def __init__(self, message, path='', line=1, column=1):
        super().__init__(message)
        self.message, self.path, self.line, self.column = message, path, line, column

    def diagnostic(self):
        return dict(message=self.message, path=self.path, line=self.line, column=self.column, severity='error')


@dataclass
class SourceSpan:
    path: str
    line: int
    column: int = 1


def validate_checkpoint_version(value):
    if value.get('schema', SCHEMA_VERSION) != SCHEMA_VERSION:
        raise StoryError('不支持的存档版本')


def to_public(value):
    return asdict(value) if hasattr(value, '__dataclass_fields__') else value
