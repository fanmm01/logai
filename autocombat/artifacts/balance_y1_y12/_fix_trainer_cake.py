#!/usr/bin/env python3
from pathlib import Path
p=Path(__file__).resolve().parents[2]/'ai_trainer.py'
s=p.read_text(encoding='utf-8')
start=s.index("    elif base == 'EAT_CAKE':",s.index('def execute_action'))
end=s.index("def _fast_move(engine, uid):",start)
# Preserve the function's final return while replacing the malformed action branches.
new='''    elif base == 'EAT_CAKE':
        if hasattr(engine, '_eat_cake') and engine._has_ready_cake(uid):
            engine._eat_cake(uid)
        return ''
    elif base == 'GIVE_CAKE':
        if hasattr(engine, '_eat_cake') and engine._has_ready_cake(uid):
            teammates = [e for e in engine._battle_unit_entries()
                         if e.get('team') == my_team and not e.get('isSummon')
                         and engine._resolve_uid(e['userId']) != engine._resolve_uid(uid)
                         and (engine._get_combat_hp(e['userId']) or 0) > 0]
            if teammates:
                tm_id = select_teammate_by_strategy(engine, uid, target_strat, teammates)
                if tm_id:
                    engine._eat_cake(uid, tm_id)
        return ''
    return ''

'''
s=s[:start]+new+s[end:]
p.write_text(s,encoding='utf-8')
