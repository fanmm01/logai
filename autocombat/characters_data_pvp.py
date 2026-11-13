"""Compatibility entry point. All character data lives in characters_data.py."""
import characters_data as _data

def __getattr__(name):
    if name == '__all__':
        return [key for key in dir(_data) if not key.startswith('_')]
    return getattr(_data, name)

def __dir__():
    return dir(_data)
