#!/usr/bin/env python3
"""One-off repair for the heal block's string literals (no engine behavior)."""
from pathlib import Path

path = Path(__file__).resolve().parents[2] / "battle_engine.py"
text = path.read_text(encoding="utf-8")
start = text.index("        if ct == 3:  # Heal")
end = text.index("        if ct == 4:  # Buff", start)
block = text[start:end]

import re
# Lines split like:  out += f'...text<newline>'  were written with a literal
# newline instead of the two-character escape. Rejoin them.
fixed = re.sub(r"(out \+= f'[^\n']*)\n'\n", lambda m: m.group(1) + chr(92) + "n'" + "\n", block)
path.write_text(text[:start] + fixed + text[end:], encoding="utf-8")
print("fixed newline literals:", block.count("\n'\n") - fixed.count("\n'\n"))
