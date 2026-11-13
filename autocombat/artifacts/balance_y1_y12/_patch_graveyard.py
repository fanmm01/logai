#!/usr/bin/env python3
"""One-off patch: graveyard + revive primitives (no runtime behavior until wired)."""
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "battle_engine.py"
s = p.read_text(encoding="utf-8")

needle = """            # Death
            self._remove_summons_of_owner(uid)
            return False"""
new = """            # Death: preserve a graveyard entry before cleanup removes the unit.
            self._record_graveyard_entry(uid)
            self._remove_summons_of_owner(uid)
            return False"""
assert needle in s
s = s.replace(needle, new, 1)

needle2 = '''    def _remove_summons_of_owner(self, owner_uid):
        """Remove an owner's physical summons, preserving the next/current actor."""
        owner_uid = self._resolve_uid(owner_uid)'''
new2 = '''    def _record_graveyard_entry(self, uid):
        """Store enough state to let a data-configured revive effect restore a unit."""
        import copy
        base = self._resolve_uid(uid)
        state = self._get_state() or {}
        entry = self._unit_il_entry(base, self._get_initiative())
        char = self.get_char(base)
        if char is None:
            return
        grave = getattr(self, '_graveyard', None)
        if grave is None:
            grave = {}
            self._graveyard = grave
        if any(g.get('uid') == base and g.get('revived') is False for g in grave.values()):
            return
        member = self._summon_members.get(base) if (entry or {}).get('isSummon') else None
        grave[base] = {
            'uid': base, 'team': (entry or {}).get('team', self._battle_team_of(base)),
            'round': int(state.get('round', 1)),
            'entry': copy.deepcopy(entry) if entry else None,
            'member': copy.deepcopy(member) if member else None,
            'ownerId': (entry or {}).get('ownerId') or (member or {}).get('ownerId'),
            'originalOwnerId': (entry or {}).get('originalOwnerId'),
            'isSummon': bool((entry or {}).get('isSummon')),
            'attrs': copy.deepcopy(char.attrs), 'str_attrs': copy.deepcopy(char.str_attrs),
            'spells': copy.deepcopy(char.spells), 'inventory': copy.deepcopy(char.inventory),
            'name': char.name, 'serial': char.serial, 'phase': getattr(char, 'phase', 1),
            'hs_transformed': getattr(char, 'hs_transformed', False),
            'once_consumed': list(getattr(char, '_once_consumed', set()) or []),
            'revived': False,
        }

    def _revive_candidates(self, caster_id, eff):
        """Return graveyard entries selectable by a revive effect."""
        grave = getattr(self, '_graveyard', {}) or {}
        cur_round = int((self._get_state() or {}).get('round', 1))
        window = int(eff.get('死亡轮窗口', 0) or 0)
        caster_base = self._resolve_uid(caster_id)
        caster_team = self._battle_team_of(caster_id)
        allow_summon = int(eff.get('复活主体', 1) or 0) == 1
        out = []
        for g in grave.values():
            if g.get('revived'):
                continue
            if g['uid'] == caster_base or g.get('team') != caster_team:
                continue
            if g.get('isSummon') and not allow_summon:
                continue
            if window and cur_round - int(g.get('round', 0)) > window:
                continue
            out.append(g)
        return out

    def _find_free_coord_near(self, caster_id):
        il = self._get_initiative()
        caster_entry = self._unit_il_entry(caster_id, il)
        base = parse_coord((caster_entry or {}).get('coord', '')) or (1, 1)
        md = self._get_map() or {}
        occupied = md.get('occupants', {})
        for radius in range(0, 10):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    c = format_coord(base[0] + dx, base[1] + dy)
                    if c not in occupied:
                        return c
        return format_coord(base)

    def _restore_graveyard_unit(self, caster_id, grave_entry, eff):
        """Restore a dead unit at the configured HP ratio without refreshing resources."""
        uid = grave_entry['uid']
        char = self.get_char(uid)
        char.attrs = dict(grave_entry.get('attrs') or {})
        char.str_attrs = dict(grave_entry.get('str_attrs') or {})
        char.spells = grave_entry.get('spells') or []
        char.inventory = grave_entry.get('inventory') or []
        char.name = grave_entry.get('name') or char.name
        char.serial = grave_entry.get('serial') or char.serial
        char.phase = grave_entry.get('phase', 1)
        char.hs_transformed = grave_entry.get('hs_transformed', False)
        char._once_consumed = set(grave_entry.get('once_consumed') or [])
        max_hp = char.get_attr('体力上限', 1) or 1
        revive_hp = max(1, int(max_hp * float(eff.get('复活回复比例', 0.4) or 0.4)))
        old_entry = grave_entry.get('entry') or {}
        coord = old_entry.get('coord') or self._find_free_coord_near(caster_id)
        is_summon = grave_entry.get('isSummon')
        owner = self._resolve_uid(caster_id) if is_summon else old_entry.get('ownerId')
        if is_summon:
            old = dict(self._summon_members.get(uid, {}))
            tmpl = _get_summon_template(old_entry.get('name', '')) or {}
            self._summon_members[uid] = old
            self._register_summon_member_state(uid, tmpl, owner)
            self._summon_members[uid]['ownerId'] = self._resolve_uid(owner)
            self._summon_members[uid]['team'] = self._battle_team_of(caster_id)
            self._summon_members[uid]['coord'] = coord
        entry = dict(old_entry)
        entry.update({'userId': uid, 'baseUserId': uid, 'team': self._battle_team_of(caster_id),
                      'coord': coord, 'ownerId': owner})
        entry.setdefault('name', char.name)
        entry['isSummon'] = bool(is_summon)
        il = self._get_initiative()
        if is_summon:
            grp_key = None
            for key, grp in self._summon_groups.items():
                if (grp.get('template_name') == entry.get('name')
                        and self._resolve_uid(grp.get('ownerId', '')) == self._resolve_uid(owner)):
                    grp_key = key
                    grp['members'].append(uid)
                    break
            if grp_key is None:
                self._summon_groups[uid] = {'members': [uid], 'template_name': entry.get('name'),
                                            'ownerId': self._resolve_uid(owner), 'representative_uid': uid}
                self._summon_members.setdefault(uid, {})
                il.append(entry)
        else:
            il.append(entry)
        self._set_initiative(il)
        self._init_combat_hp(uid, revive_hp)
        acts = self._get_actions()
        acts.setdefault(self._summon_group_key(uid), {'主动': 0, '附加': 0})
        self._set_actions(acts)
        md = self._get_map()
        if md:
            md.setdefault('occupants', {})[coord] = uid
            self._set_map(md)
        grave_entry['revived'] = True
        return revive_hp

    def _remove_summons_of_owner(self, owner_uid):
        """Remove an owner's physical summons, preserving the next/current actor."""
        owner_uid = self._resolve_uid(owner_uid)'''
assert needle2 in s
s = s.replace(needle2, new2, 1)

# Graveyard entries for summons destroyed as collateral (owner death).
needle3 = """    def _remove_summons_of_owner(self, owner_uid):
        \"\"\"Remove an owner's physical summons, preserving the next/current actor.\"\"\"
        owner_uid = self._resolve_uid(owner_uid)
        il = self._get_initiative()"""
new3 = """    def _remove_summons_of_owner(self, owner_uid, record_collateral=False):
        \"\"\"Remove an owner's physical summons, preserving the next/current actor.\"\"\"
        owner_uid = self._resolve_uid(owner_uid)
        il = self._get_initiative()"""
assert needle3 in s
s = s.replace(needle3, new3, 1)
p.write_text(s, encoding="utf-8")
print("graveyard patch applied")
