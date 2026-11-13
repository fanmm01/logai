#!/usr/bin/env python3
from pathlib import Path
import re
p=Path(__file__).resolve().parents[2]/'battle_engine.py'
s=p.read_text(encoding='utf-8')
start=s.index("        elif ct == 19:  # Data-configured revive")
end=s.index("        elif ct == 7:  # Trigger",start)
block=s[start:end]
block=re.sub(r"(out \+= f'[^\n']*)\n'\n", lambda m:m.group(1)+"\\n'\n", block)
block=re.sub(r"(return '[^\n']*)\n'\n", lambda m:m.group(1)+"\\n'\n", block)
s=s[:start]+block+s[end:]
s=s.replace("self._set_effects(effects); out += f'  开始制造 {count} 个（{cr}回合）\n'", "self._set_effects(effects); out += f'  开始制造 {count} 个（{cr}回合）\\n'")
p.write_text(s,encoding='utf-8')
