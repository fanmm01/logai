"""Exercise the actual frontend resolver rather than duplicating it in Python."""
import json
from pathlib import Path
import subprocess
from functools import lru_cache
import characters_data as cd


@lru_cache(maxsize=1)
def resolved_encyclopedia():
    root = Path(__file__).resolve().parents[1]
    source = json.dumps(dict(cards=cd.ALL_CHARACTERS, summons=cd.SUMMON_TEMPLATES,
                             items=cd.ITEM_TEMPLATES), ensure_ascii=False)
    result = subprocess.run(['node', 'frontend/tests/resolve-encyclopedia.cjs'], cwd=root,
                            input=source, encoding='utf-8', capture_output=True, check=True)
    return json.loads(result.stdout)
