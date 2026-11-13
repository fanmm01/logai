#!/usr/bin/env python3
"""Trace a small, fixed matchup panel without changing battle rules or AI weights.

HP gains/losses are effective HP movement, not raw damage/healing, and include
all passives/zones. Action counts are attempts: failed casts are counted too.
"""
import collections
import concurrent.futures
import json
import random
from pathlib import Path
import time

import run_balance_prelims as base


def probe_match(job):
    ab = base._AB

    class ProbeEngine(ab.BattleEngine):
        def __init__(self, *args, **kwargs):
            self.probe_active = False
            self.hp_gain = collections.Counter()
            self.hp_loss = collections.Counter()
            self.hp_gain_source = collections.Counter()
            self.attempts = collections.Counter()
            self.action_lines = []
            self.action_stack = []
            self.first_zero_round = {}
            super().__init__(*args, **kwargs)

        def _set_combat_hp(self, uid, hp, source_dmg=0):
            before = self._get_combat_hp(uid)
            result = super()._set_combat_hp(uid, hp, source_dmg)
            after = self._get_combat_hp(uid)
            if self.probe_active and before is not None and after is not None:
                key = self._resolve_uid(uid)
                if after > before:
                    self.hp_gain[key] += after - before
                    source = self.action_stack[-1] if self.action_stack else "passive/zone/tick"
                    self.hp_gain_source[f"{key}|{source}"] += after - before
                elif after < before:
                    self.hp_loss[key] += before - after
                if before > 0 and after <= 0 and key not in self.first_zero_round:
                    self.first_zero_round[key] = (self._get_state() or {}).get("round", 0)
            return result

        def probe_action(self, uid, label, fun, *args):
            key = f"{self._resolve_uid(uid)}|{label}"
            outer = not self.action_stack
            self.action_stack.append(key)
            try:
                result = fun(uid, *args)
            finally:
                self.action_stack.pop()
            if self.probe_active and outer:
                self.attempts[key] += 1
                if len(self.action_lines) < 70:
                    self.action_lines.append(dict(round=(self._get_state() or {}).get("round", 0),
                                                  action=key, output=str(result)[:650]))
            return result

        def _use_skill(self, uid, num, args):
            return self.probe_action(uid, f"skill{num}", super()._use_skill, num, args)

        def _basic_attack(self, uid):
            return self.probe_action(uid, "basic", super()._basic_attack)

        def _additional_action(self, uid, args):
            label = "bonus_skill" + args[1:].strip() if args.startswith("s") else "move" if args.startswith("m") else "bonus_other"
            return self.probe_action(uid, label, super()._additional_action, args)

        def _eat_cake(self, uid, target_id=None):
            return self.probe_action(uid, "cake", super()._eat_cake, target_id)

    random.seed(job["seed"])
    t = base.new_tournament(50)
    engine = ProbeEngine(delay=0, quiet=True, max_rounds=job["limit"])
    for c in ab.ALL_CHARACTERS:
        if c["serial"] in job["a"] + job["b"]:
            uid = t.char_map[c["serial"]]
            base._DATA.load_character_to_engine(engine, c, uid)
            if not c.get("pre_transformed"):
                engine.process_command(uid, ".hs")
    a_uids, b_uids = t._resolve_clones(job["a"], job["b"], engine)
    for uid in a_uids:
        t.ai_map[uid].team = "Y"
    for uid in b_uids:
        t.ai_map[uid].team = "X"
    engine._ai_react_dodge_w = {uid: t.ai_map[uid].react_dodge_w for uid in a_uids+b_uids}
    engine._ai_react_counter_w = {uid: t.ai_map[uid].react_counter_w for uid in a_uids+b_uids}
    engine._ai_react_block_w = {uid: getattr(t.ai_map[uid], "react_block_w", 0) for uid in a_uids+b_uids}
    engine.setup_battle(a_uids, b_uids, job["map"], season_status=50)
    initial = {uid: dict(hp=engine._get_combat_hp(uid),
                         mp=engine.get_char(uid).get_attr("魔力"),
                         dodge=engine.get_char(uid).get_attr("闪避"),
                         melee=engine.get_char(uid).get_attr("斗殴"),
                         dex=engine.get_char(uid).get_attr("敏捷")) for uid in a_uids+b_uids}
    engine.probe_active = True
    result = engine.run_battle(t.ai_map)
    final = {uid: dict(hp=engine._get_combat_hp(uid), mp=engine.get_char(uid).get_attr("魔力"))
             for uid in a_uids+b_uids}
    return dict(job, result=result, initial=initial, final=final,
                hp_gain=dict(engine.hp_gain), hp_loss=dict(engine.hp_loss),
                hp_gain_source=dict(engine.hp_gain_source), attempts=dict(engine.attempts),
                first_zero_round=engine.first_zero_round, action_lines=engine.action_lines)


def main():
    panels = [(["Y1"], ["Y4"]), (["Y3"], ["Y4"]), (["Y3"], ["Y5"]),
              (["Y11"], ["Y4"]), (["Y2"], ["Y4"]), (["Y9"], ["Y12"]),
              (["Y4", "Y6"], ["Y2", "Y11"]), (["Y4", "Y7"], ["Y2", "Y11"]),
              (["Y4", "Y8"], ["Y2", "Y11"]), (["Y1", "Y6"], ["Y4", "Y10"]),
              (["Y1", "Y7"], ["Y4", "Y10"]), (["Y1", "Y8"], ["Y4", "Y10"]),
              (["Y9", "Y12"], ["Y5", "Y6", "Y7", "Y8"]),
              (["Y5", "Y6", "Y7", "Y8"], ["Y1", "Y10", "Y11"])]
    jobs = [base.make_job("mechanism_probe", 2026111240, a, b, repeat=r,
                          limit=20 if len(a)+len(b) == 2 else 45)
            for a, b in panels for r in range(12)]
    base.init_worker()
    base.OUT.mkdir(parents=True, exist_ok=True)
    hashes = base.fingerprint()
    started = time.time()
    with (base.OUT / "mechanism_probe.jsonl").open("w", encoding="utf-8") as out:
        with concurrent.futures.ProcessPoolExecutor(max_workers=3, initializer=base.init_worker) as pool:
            for i, record in enumerate(pool.map(probe_match, jobs, chunksize=2), 1):
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                if i % 30 == 0 or i == len(jobs):
                    out.flush()
                    print(f"probes {i}/{len(jobs)} elapsed={time.time()-started:.1f}s", flush=True)
    print(f"done; {len(jobs)} traces; source hashes unchanged={base.fingerprint()==hashes}", flush=True)


if __name__ == "__main__":
    main()
