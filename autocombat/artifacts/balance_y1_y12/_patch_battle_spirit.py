from pathlib import Path
p = Path(__file__).resolve().parents[2] / 'battle_engine.py'
s = p.read_text(encoding='utf-8')
start = s.index('    def _process_battle_spirit(self):')
end = s.index('    # ---- HP Threshold Trigger & Phase System ----', start)
new = '''    def _process_battle_spirit(self):
        """Process 战意值 for enemy summon groups. Called each round from _tick_down.

        State lives on the stable summon group, not on whichever member currently
        holds the initiative slot: representative changes keep their stacks, and
        a group is processed once per round even with several active sources.
        All numbers come from the source's CharacterData type-11 effect.
        """
        effects = self._get_effects()
        sources = []
        il = self._get_initiative()
        for eff in effects:
            if eff.get('type') != 'battleSpirit':
                continue
            src = self._resolve_uid(eff.get('sourceUserId', ''))
            if not src:
                continue
            src_entry = next((e for e in il if self._resolve_uid(e['userId']) == src), None)
            if src_entry is None or (self._get_combat_hp(src) or 0) <= 0:
                continue  # 来源阵亡/离场：光环停止
            if self._has_status(src, '隔离'):
                continue
            sources.append((src, _eff_get(eff, 'sourceTeam') or src_entry.get('team', 'Y'), eff))
        if not sources:
            return []

        msgs = []
        processed = set()
        for src, src_team, eff in sources:
            cfg = self._battle_spirit_config(eff)
            for key in list(self._summon_groups):
                grp = self._summon_groups.get(key)
                if not grp or key in processed:
                    continue
                if self._summon_group_team(key) == src_team:
                    continue  # 只影响敌方召唤组
                members = [m for m in grp.get('members', [])
                           if (self._get_combat_hp(m) or 0) > 0]
                if not members:
                    continue
                processed.add(key)
                label = grp.get('template_name', key)
                stacks = grp.get('battle_spirit')
                if stacks is None:
                    grp['battle_spirit'] = cfg['initial']
                    msgs.append(f"战意值: {label} 获得 {cfg['initial']} 层战意值")
                    continue
                if stacks <= 0:
                    continue
                rate = cfg['rate'] if stacks > cfg['bonus_threshold'] else cfg['rate_bonus']
                success = random.randint(1, 100) <= rate
                if success:
                    grp['battle_spirit'] = stacks - 1
                    stacks = grp['battle_spirit']
                    msgs.append(f"战意值: {label} 削弱至 {stacks} 层")
                    if stacks <= 0:
                        msgs.append(f"战意值: {label} 战意归零，整组撤离战场！")
                        grp['battle_spirit_penalty_dice'] = 0
                        self._withdraw_summon_group(key, reason='battle_spirit')
                        continue
                old_pens = grp.get('battle_spirit_penalty_dice', 0)
                new_pens = 0
                if stacks <= cfg['penalty2_threshold']:
                    new_pens = 2
                elif stacks <= cfg['penalty1_threshold']:
                    new_pens = 1
                new_pens = min(new_pens, cfg['penalty_max'])
                if new_pens != old_pens:
                    grp['battle_spirit_penalty_dice'] = new_pens
                    if new_pens > 0:
                        pen_label = 'p' if new_pens == 1 else f'p{new_pens}'
                        msgs.append(f"战意值: {label} 获得 {new_pens} 惩罚骰 ({pen_label})")
        return msgs

    def _battle_spirit_config(self, eff):
        """Per-source battle-spirit parameters, all declared in CharacterData."""
        def num(key, default):
            try:
                return int(float(_eff_get(eff, key)))
            except (TypeError, ValueError):
                return default
        return {
            'initial': num('战意初始层数', 5),
            'rate': num('战意削弱概率', 50),
            'rate_bonus': num('战意削弱概率奖励层', 50),
            'bonus_threshold': num('战意奖励骰阈值', 3),
            'penalty1_threshold': num('战意惩罚1阈值', 4),
            'penalty2_threshold': num('战意惩罚2阈值', 2),
            'penalty_max': num('战意惩罚骰上限', 2),
        }

    def _summon_group_team(self, group_key):
        grp = self._summon_groups.get(group_key) or {}
        for m in grp.get('members', []):
            mdata = self._summon_members.get(m) or {}
            if mdata.get('team'):
                return mdata['team']
        entry = self._unit_il_entry(group_key, self._get_initiative())
        return (entry or {}).get('team', 'Y')

    def _withdraw_summon_group(self, group_key, reason='battle_spirit'):
        """Remove a whole summon group from battle without recording deaths."""
        grp = self._summon_groups.pop(group_key, None)
        if not grp:
            return
        members = set(grp.get('members', [])) | {group_key, grp.get('representative_uid', group_key)}
        for uid in members:
            self._summon_members.pop(uid, None)
        self._replace_initiative_preserving_actor(
            [e for e in self._get_initiative()
             if self._resolve_uid(e.get('userId', '')) not in members])
        md = self._get_map()
        if md:
            md['occupants'] = {c: uid for c, uid in md.get('occupants', {}).items()
                               if self._resolve_uid(uid) not in members}
            self._set_map(md)
        acts = self._get_actions()
        for uid in members:
            acts.pop(uid, None)
        self._set_actions(acts)

'''
s = s[:start] + new + s[end:]
p.write_text(s, encoding='utf-8')
print('battle spirit replaced')
