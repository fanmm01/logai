from pathlib import Path
p=Path(__file__).resolve().parents[2]/'battle_engine.py'
s=p.read_text(encoding='utf-8')
start=s.index('    def _restore_graveyard_unit(self, caster_id, grave_entry, eff):')
end=s.index('    def _remove_summons_of_owner(', start)
new='''    def _restore_graveyard_unit(self, caster_id, grave_entry, eff):
        """Preflight, then restore one unit without resetting resources/actions."""
        import copy
        uid = self._resolve_uid(grave_entry['uid'])
        caster = self.get_char(caster_id)
        ratio = float(eff.get('复活回复比例', 0.4) or 0.4)
        if not 0 < ratio <= 1 or grave_entry.get('revived'):
            return None
        state = self._get_state() or {}
        now_round = int(state.get('round', 1))
        window = int(eff.get('死亡轮窗口', 0) or 0)
        if window and now_round - int(grave_entry.get('round', 0)) > window:
            return None
        caster_team = self._battle_team_of(caster_id)
        if grave_entry.get('team') != caster_team or uid == self._resolve_uid(caster_id):
            return None
        if self._get_combat_hp(uid) not in (None, 0):
            return None

        is_summon = bool(grave_entry.get('isSummon'))
        old_entry = copy.deepcopy(grave_entry.get('entry') or {})
        old_member = copy.deepcopy(grave_entry.get('member') or {})
        name = old_entry.get('name') or old_member.get('name') or ''
        source_owner = self._resolve_uid(grave_entry.get('ownerId') or '') if grave_entry.get('ownerId') else ''
        new_owner = self._resolve_uid(caster_id) if is_summon else old_entry.get('ownerId')
        template = _get_summon_template(name) or {} if is_summon else {}
        attrs = grave_entry.get('attrs') or {}
        max_hp = int(attrs.get('体力上限', 1) or 1)
        revive_hp = max(1, min(max_hp, int(max_hp * ratio)))

        md = self._get_map() or {}
        coord = old_entry.get('coord') or old_member.get('coord') or ''
        size = 2 if template.get('size_2x2') else 1
        def footprint_at(cell):
            point = parse_coord(cell) if cell else None
            if not point:
                return None
            cells = {format_coord(point[0] + dx, point[1] + dy)
                     for dx in range(size) for dy in range(size)}
            if any(c not in md.get('occupants', {}) and c not in md.get('obstacles', {})
                   for c in cells):
                occupied_by = {self._resolve_uid(md['occupants'][c]) for c in cells
                               if c in md.get('occupants', {})}
                if not occupied_by or occupied_by == {uid}:
                    return cells
            return None
        footprint = footprint_at(coord)
        if footprint is None:
            coord = self._find_free_coord_near(caster_id)
            footprint = footprint_at(coord)
        if footprint is None:
            return None

        group_key = None
        if is_summon:
            owner_base = self._resolve_uid(new_owner)
            # Test Y7's live quota against a hypothetical state where this UID
            # has left the former owner; no mutations are committed on failure.
            saved_groups, saved_members = self._summon_groups, self._summon_members
            saved_counts = self._summon_total_spawned
            try:
                groups = {k: dict(g, members=list(g.get('members', []))) for k, g in saved_groups.items()}
                for key, group in list(groups.items()):
                    if key == uid or uid in group.get('members', []):
                        group['members'] = [m for m in group.get('members', []) if m != uid]
                        if not group['members']:
                            groups.pop(key, None)
                        elif group.get('representative_uid') == uid:
                            group['representative_uid'] = group['members'][0]
                members = {k: dict(v) for k, v in saved_members.items() if k != uid}
                counts = dict(saved_counts)
                prior = grave_entry.get('previousTransfers', [])
                if owner_base not in prior:
                    key = (owner_base, name)
                    counts[key] = counts.get(key, 0) + 1
                self._summon_groups, self._summon_members, self._summon_total_spawned = groups, members, counts
                quota, _reason = self._get_summon_quota(owner_base, name, template)
                group_key = next((k for k, g in groups.items()
                                  if g.get('template_name') == name
                                  and self._resolve_uid(g.get('ownerId', '')) == owner_base
                                  and any((self._get_combat_hp(m) or 0) > 0 for m in g.get('members', []))), None)
            finally:
                self._summon_groups, self._summon_members, self._summon_total_spawned = saved_groups, saved_members, saved_counts
            if quota == 0:
                return None
            if group_key is None:
                group_key = uid

        # All validation passed; now restore the saved unit and transfer ownership.
        char.attrs = copy.deepcopy(attrs)
        char.str_attrs = copy.deepcopy(grave_entry.get('str_attrs') or {})
        char.spells = copy.deepcopy(grave_entry.get('spells') or [])
        char.inventory = copy.deepcopy(grave_entry.get('inventory') or [])
        char.name = grave_entry.get('name') or char.name
        char.serial = grave_entry.get('serial') or char.serial
        char.phase = grave_entry.get('phase', 1)
        char.hs_transformed = grave_entry.get('hs_transformed', False)
        char._once_consumed = set(grave_entry.get('once_consumed') or [])

        if is_summon:
            for key, group in list(self._summon_groups.items()):
                if key == uid or uid in group.get('members', []):
                    group['members'] = [m for m in group.get('members', []) if m != uid]
                    if not group['members']:
                        self._summon_groups.pop(key, None)
                    elif group.get('representative_uid') == uid:
                        group['representative_uid'] = group['members'][0]
            old_member.update({'userId': uid, 'name': name, 'ownerId': new_owner,
                               'team': caster_team, 'coord': coord,
                               'summonGroupKey': group_key})
            self._summon_members[uid] = old_member
            if group_key not in self._summon_groups:
                self._summon_groups[group_key] = {
                    'members': [uid], 'template_name': name, 'ownerId': new_owner,
                    'representative_uid': uid, 'initiative_entry': dict(old_entry)}
            elif uid not in self._summon_groups[group_key].setdefault('members', []):
                self._summon_groups[group_key]['members'].append(uid)
            group = self._summon_groups[group_key]
            if group.get('representative_uid') == uid:
                group['initiative_entry'] = dict(old_entry)
                group['initiative_entry'].update({'ownerId': new_owner, 'team': caster_team, 'coord': coord})
            prior = list(grave_entry.get('previousTransfers', []))
            if new_owner not in prior:
                prior.append(new_owner)
                spawn_key = (new_owner, name)
                self._summon_total_spawned[spawn_key] = self._summon_total_spawned.get(spawn_key, 0) + 1
            grave_entry['previousTransfers'] = prior
            old_member.setdefault('name', name)
            char.name = old_member.get('display_name') or char.name

        entry = dict(old_entry)
        entry.update({'userId': uid, 'baseUserId': uid, 'team': caster_team,
                      'coord': coord, 'ownerId': new_owner,
                      'initRoll': old_entry.get('initRoll', 100), 'initRank': old_entry.get('initRank', 0),
                      'dex': old_entry.get('dex', char.get_attr('敏捷', 50)),
                      'actionIdx': old_entry.get('actionIdx', 0), 'isSummon': is_summon})
        entry.setdefault('name', name or char.name)
        if is_summon:
            entry['summonGroup'] = group_key
        il = self._get_initiative()
        if not any(e.get('userId') == uid for e in il):
            il.append(entry)
        self._set_initiative(il)
        self._init_combat_hp(uid, revive_hp)
        actions = self._get_actions()
        action_key = self._summon_group_key(uid) if is_summon else uid
        actions.setdefault(action_key, {'主动': 0, '附加': 0})
        self._set_actions(actions)
        md.setdefault('occupants', {})
        for cell in footprint:
            md['occupants'][cell] = uid
        self._set_map(md)
        grave_entry['revived'] = True
        return revive_hp

'''
s=s[:start]+new+s[end:]
p.write_text(s,encoding='utf-8')
