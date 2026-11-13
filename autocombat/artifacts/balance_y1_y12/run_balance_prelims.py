#!/usr/bin/env python3
"""Read-only character balance experiments using the current full battle engine.

Run from any directory. No training or game data edits. Each match owns its RNG
and AI controllers; processes, rather than threads, isolate global random state.
The official schedule builder and point formula are reused. Reaction weights stay
at their initial values so outcomes do not depend on match scheduling order.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import itertools
import json
import logging
import os
from pathlib import Path
import random
import sys
import time
import traceback

ROOT = Path(os.environ.get("BALANCE_SOURCE_ROOT", str(Path(__file__).resolve().parents[2])))
OUT = Path(os.environ.get("BALANCE_OUT_DIR", str(Path(__file__).resolve().parent)))
SERIALS = [f"Y{i}" for i in range(1, 13)]
_AB = None
_DATA = None


def init_worker():
    global _AB, _DATA
    os.environ["BATTLE_DEBUG_LOG"] = "0"
    sys.path.insert(0, str(ROOT))
    # ai_battle creates an import-time mode='w' log. Suppress that side effect.
    original_handler = logging.FileHandler
    logging.FileHandler = lambda *args, **kwargs: logging.NullHandler()
    try:
        import ai_battle
        import characters_data
    finally:
        logging.FileHandler = original_handler
    logging.disable(logging.CRITICAL)
    ai_battle.battle_log = lambda *args, **kwargs: None
    _AB, _DATA = ai_battle, characters_data
    _AB.ALL_CHARACTERS = [c for c in _DATA.ALL_CHARACTERS if c["serial"] in SERIALS]
    assert len(_AB.ALL_CHARACTERS) == 12, "Expected all and only Y1-Y12"
    _AB.AIController.load_q_table()


def new_tournament(season):
    t = _AB.Tournament(0)
    # No need to load all characters before every match: battle() loads entrants.
    t.char_map = {c["serial"]: f'char_{c["serial"]}' for c in _AB.ALL_CHARACTERS}
    t.ai_map = {t.char_map[c["serial"]]: _AB.AIController(t.char_map[c["serial"]], c, "Y")
                for c in _AB.ALL_CHARACTERS}
    t._parallel_prelim = True  # disable result writes and between-match learning
    t.char_season_status = {s: season for s in SERIALS}
    return t


def run_match(job):
    random.seed(job["seed"])
    t = new_tournament(job["season"])
    try:
        w, _, rounds, result = t.battle(job["a"], job["b"], job["map"],
                                        job["suite"], delay=0,
                                        max_rounds=job["limit"])
        wp, lp = t._calc_points(result)
        return dict(job, winner=w, winner_side=result["winner"], rounds=rounds, timeout=bool(result.get("timeout")),
                    winner_points=wp, loser_points=lp,
                    y_ratio=result.get("y_ratio"), x_ratio=result.get("x_ratio"))
    except Exception:
        return dict(job, error=traceback.format_exc())


def make_job(suite, batch, a, b, map_size="10x10", season=50, limit=None, **extra):
    raw = f'{suite}|{batch}|{a}|{b}|{map_size}|{extra}'
    seed = int.from_bytes(hashlib.sha256(raw.encode()).digest()[:8], "big")
    return dict(suite=suite, batch=batch, a=list(a), b=list(b), map=map_size,
                season=season, limit=limit or {1: 20, 2: 30, 3: 45}.get(max(len(a), len(b)), 45),
                seed=seed, **extra)


def official_jobs(batch):
    random.seed(batch)
    t = new_tournament(50)
    singles = [[s] for s in SERIALS]
    rr = min(_AB.RR_ROUNDS_1V1, _AB.TARGET_1V1_MATCHES // 11)
    pool1 = t._build_rr_pool("1v1", singles, ["10x10"], "1v1", rr)
    pool1 += t._allocate_individual_bonus_matches(SERIALS, rr, _AB.TARGET_1V1_MATCHES)
    pool2 = t._build_rr_pool("2v2", _AB.TEAM_TABLES_2V2, _AB.MAP_SIZES_2V2,
                             "2v2", _AB.RR_ROUNDS_2V2)
    pool2 += t._allocate_team_bonus_matches(_AB.TEAM_TABLES_2V2, _AB.RR_ROUNDS_2V2,
                                           _AB.RANDOM_BONUS_2V2)
    pool3 = t._build_rr_pool("3v3", _AB.TEAM_TABLES_3V3, _AB.MAP_SIZES_3V3,
                             "3v3", _AB.RR_ROUNDS_3V3)
    pool3 += t._allocate_team_bonus_matches(_AB.TEAM_TABLES_3V3, _AB.RR_ROUNDS_3V3,
                                           _AB.RANDOM_BONUS_3V3)
    jobs = []
    for fmt, pool in [("1v1", pool1), ("2v2", pool2), ("3v3", pool3)]:
        for idx, (_, ms, a, b, _, _) in enumerate(pool):
            # Reverse half of repeats in alternating batches to check side bias.
            if (idx + batch) % 2:
                a, b = b, a
            jobs.append(make_job("official_" + fmt, batch, a, b, ms,
                                 limit=_AB.MAX_ROUNDS_PRELIM_BY_FORMAT[fmt], index=idx))
    return jobs


def balanced_jobs(batch, partitions=120):
    rng = random.Random(batch)
    jobs = []
    for size in [2, 3]:
        for r in range(partitions):
            chars = SERIALS[:]
            rng.shuffle(chars)
            for start in range(0, 12, size * 2):
                a = chars[start:start + size]
                b = chars[start + size:start + size * 2]
                jobs.append(make_job(f"balanced_{size}v{size}", batch, a, b,
                                     partition=r, index=start))
    return jobs


def duel_map_jobs(batch, repeats=6):
    jobs = []
    for ms in ["6x6", "20x20"]:
        for a, b in itertools.combinations(SERIALS, 2):
            for r in range(repeats):
                a1, b1 = ([a], [b]) if r % 2 == 0 else ([b], [a])
                jobs.append(make_job("map_duel", batch, a1, b1, ms, repeat=r))
    return jobs


def support_jobs(batch, repeats=8):
    # Same carry, same enemies; compare the three designated supports to a DPS
    # and a separate carry-alone control. Controls are not labelled equal-size.
    jobs = []
    carries = ["Y1", "Y3", "Y4", "Y5", "Y9", "Y11"]
    enemies = [("Y1", "Y2"), ("Y4", "Y10"), ("Y9", "Y12"), ("Y2", "Y11")]
    for carry, enemy in itertools.product(carries, enemies):
        for r in range(repeats):
            for support in ["none", "Y2", "Y6", "Y7", "Y8"]:
                a = [carry] + ([] if support == "none" else [support])
                b = list(enemy)
                swapped = r % 2 == 1
                if swapped:
                    a, b = b, a
                jobs.append(make_job("support_control", batch, a, b,
                                     limit=30, repeat=r, carry=carry, support=support,
                                     enemy=list(enemy), carry_side="b" if swapped else "a"))
    return jobs


def aggregate(records):
    stats = {}
    for row in records:
        if "error" in row:
            continue
        for side in ["a", "b"]:
            won = (row["winner_side"] == ("Y" if side == "a" else "X")
                   if "winner_side" in row else row["winner"] == "+".join(row[side]))
            for s in set(row[side]):
                key = (row["suite"], row["map"], s)
                d = stats.setdefault(key, dict(n=0, wins=0, timeouts=0, timeout_wins=0,
                                                rounds=0, points=0))
                d["n"] += 1
                d["wins"] += int(won)
                d["timeouts"] += int(row["timeout"])
                d["timeout_wins"] += int(won and row["timeout"])
                d["rounds"] += row["rounds"]
                d["points"] += row["winner_points"] if won else row["loser_points"]
    result = []
    for (suite, ms, serial), d in sorted(stats.items()):
        n, w = d["n"], d["wins"]
        p = w / n
        # Wilson interval is descriptive only: repeated lineups are dependent.
        z = 1.96
        den = 1 + z * z / n
        center = (p + z * z / (2 * n)) / den
        half = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / den
        result.append(dict(suite=suite, map=ms, serial=serial, **d,
                           rate=round(p, 6), wilson_lo=round(center-half, 6),
                           wilson_hi=round(center+half, 6),
                           timeout_rate=round(d["timeouts"] / n, 6),
                           avg_rounds=round(d["rounds"] / n, 3),
                           avg_points=round(d["points"] / n, 4)))
    return result


def fingerprint():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ["characters_data.py", "characters_data_pvp.py", "battle_engine.py",
                         "ai_battle.py", "ai_trainer.py", "ai_weights.json", "team_tables.py"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["pilot", "official", "balanced", "maps", "supports"], required=True)
    parser.add_argument("--batch", type=int, default=2026111201)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--partitions", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=8)
    args = parser.parse_args()
    init_worker()
    if args.suite == "official":
        jobs = official_jobs(args.batch)
    elif args.suite == "balanced":
        jobs = balanced_jobs(args.batch, args.partitions)
    elif args.suite == "maps":
        jobs = duel_map_jobs(args.batch, args.repeats)
    elif args.suite == "supports":
        jobs = support_jobs(args.batch, args.repeats)
    else:
        jobs = official_jobs(args.batch)[:16]
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{args.suite}_{args.batch}"
    started = time.time()
    hashes = fingerprint()
    print(f"Starting {tag}: {len(jobs)} matches, workers={args.workers}", flush=True)
    records = []
    with (OUT / f"{tag}.jsonl").open("w", encoding="utf-8") as out:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker) as pool:
            for row in pool.map(run_match, jobs, chunksize=4):
                records.append(row)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                if len(records) % 100 == 0 or len(records) == len(jobs):
                    out.flush()
                    print(f"{tag}: {len(records)}/{len(jobs)}, elapsed={time.time()-started:.1f}s, "
                          f"errors={sum('error' in r for r in records)}", flush=True)
    summary = dict(suite=args.suite, batch=args.batch, matches=len(records), workers=args.workers,
                   source_root=str(ROOT), error_count=sum("error" in r for r in records),
                   errors=[r for r in records if "error" in r], elapsed=time.time()-started,
                   season=50, policy="current ai_weights.json; fresh AI per match; no reaction training",
                   hashes=hashes, hashes_unchanged=(hashes == fingerprint()), stats=aggregate(records))
    (OUT / f"{tag}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for fmt in sorted(set(r["suite"] for r in summary["stats"])):
        print(fmt)
        for d in sorted([r for r in summary["stats"] if r["suite"] == fmt], key=lambda x: -x["rate"]):
            print(f'  {d["serial"]:4} {d["map"]:6} {d["wins"]:4}/{d["n"]:<4} '
                  f'{d["rate"]*100:5.1f}% timeout={d["timeout_rate"]*100:5.1f}%')
    print(f"Saved {tag}; hashes unchanged={summary['hashes_unchanged']}", flush=True)


if __name__ == "__main__":
    main()
