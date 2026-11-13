#!/usr/bin/env python3
"""Repair literal-newline string breaks in inserted f-strings (one-off)."""
from pathlib import Path
import re

p = Path(__file__).resolve().parents[2] / "battle_engine.py"
s = p.read_text(encoding="utf-8")
# An inserted f-string such as:  ...。\n'
# was written with a real newline inside the quotes. Join the next line "'".
s = re.sub(r"(return f'[^\n']*)\n'\n", lambda m: m.group(1) + "\\n'" + "\n", s)
p.write_text(s, encoding="utf-8")
print("ok")
