"""Compatibility access to shared, configurable team presets."""
from combat_settings import value


def __getattr__(name):
    if name in ('TEAM_TABLES_2V2', 'TEAM_TABLES_3V3'):
        return value(name)
    raise AttributeError(name)
