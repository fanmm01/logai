#!/usr/bin/env python3
"""Aggregate completed balance batches into compact feedback for role designers."""
import collections
import json
import os
from pathlib import Path

OUT = Path(os.environ.get("BALANCE_OUT_DIR", str(Path(__file__).resolve().parent)))
SERIALS = [f"Y{i}" for i in range(1, 13)]


def records_from(pattern):
    for path in sorted(OUT.glob(pattern)):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def summarize(rows):
    stats = {}
    for row in rows:
        if "error" in row:
            raise RuntimeError(row["error"])
        fmt = row["suite"].replace("official_", "").replace("balanced_", "balanced ")
        for side in ["a", "b"]:
            won = row["winner"] == "+".join(row[side])
            for serial in set(row[side]):
                key = (fmt, serial)
                d = stats.setdefault(key, collections.Counter())
                d["n"] += 1
                d["wins"] += won
                d["timeouts"] += row["timeout"]
                d["timeout_wins"] += bool(row["timeout"] and won)
                d["points"] += row["winner_points"] if won else row["loser_points"]
                d["rounds"] += row["rounds"]
    return stats


def main():
    official = list(records_from("official_*.jsonl"))
    balanced = list(records_from("balanced_*.jsonl"))
    maps = list(records_from("maps_*.jsonl"))
    supports = list(records_from("supports_*.jsonl"))
    probes = list(records_from("mechanism_probe.jsonl"))
    stats = summarize(official + balanced)
    result = {"match_counts": dict(official=len(official), balanced=len(balanced),
                                   maps=len(maps), supports=len(supports), probes=len(probes)),
              "formats": {}, "official_batches": {}, "duel_matrix": {}, "maps": {},
              "support_controls": {}, "probes": {}}
    for fmt in ["1v1", "2v2", "3v3", "balanced 2v2", "balanced 3v3"]:
        result["formats"][fmt] = {}
        print(fmt)
        for s in SERIALS:
            d = dict(stats.get((fmt, s), {}))
            if not d:
                continue
            d["rate"] = d["wins"] / d["n"]
            d["timeout_rate"] = d["timeouts"] / d["n"]
            d["avg_points"] = d["points"] / d["n"]
            d["avg_rounds"] = d["rounds"] / d["n"]
            result["formats"][fmt][s] = d
            print(f'{s:4} {d["wins"]}/{d["n"]}={d["rate"]:.1%} timeout={d["timeout_rate"]:.1%} avgpts={d["avg_points"]:.3f}')
    for batch in sorted(set(row["batch"] for row in official)):
        bs = summarize([row for row in official if row["batch"] == batch])
        result["official_batches"][str(batch)] = {
            fmt: {s: dict(bs[(fmt, s)]) for s in SERIALS}
            for fmt in ["1v1", "2v2", "3v3"]}
    for s in SERIALS:
        result["duel_matrix"][s] = {}
        for enemy in SERIALS:
            rr = [r for r in official if r["suite"] == "official_1v1" and
                  s in r["a"] + r["b"] and enemy in r["a"] + r["b"] and s != enemy]
            n = len(rr)
            if n:
                w = sum(s == r["winner"] for r in rr)
                result["duel_matrix"][s][enemy] = dict(wins=w, n=n, rate=w/n)
    for ms in ["6x6", "20x20"]:
        result["maps"][ms] = {}
        for s in SERIALS:
            rr = [r for r in maps if r["map"] == ms and s in r["a"] + r["b"]]
            n = len(rr)
            if n:
                w = sum(s == r["winner"] for r in rr)
                result["maps"][ms][s] = dict(n=n, wins=w, rate=w/n,
                                              timeouts=sum(r["timeout"] for r in rr))
    for support in ["none", "Y2", "Y6", "Y7", "Y8"]:
        rr = [r for r in supports if r["support"] == support]
        if not rr:
            continue
        won = lambda r: (r["winner_side"] == ("Y" if r["carry_side"] == "a" else "X")
                         if "winner_side" in r else r["winner"] == "+".join(r[r["carry_side"]]))
        n = len(rr)
        d = dict(n=n, wins=sum(won(r) for r in rr), timeouts=sum(r["timeout"] for r in rr),
                 avg_rounds=sum(r["rounds"] for r in rr) / n)
        d["rate"] = d["wins"] / n
        d["by_carry"] = {}
        d["by_enemy"] = {}
        for carry in ["Y1", "Y3", "Y4", "Y5", "Y9", "Y11"]:
            cr = [r for r in rr if r["carry"] == carry]
            d["by_carry"][carry] = dict(n=len(cr), wins=sum(won(r) for r in cr))
        for enemy in sorted(set("+".join(r["enemy"]) for r in rr)):
            er = [r for r in rr if "+".join(r["enemy"]) == enemy]
            d["by_enemy"][enemy] = dict(n=len(er), wins=sum(won(r) for r in er))
        result["support_controls"][support] = d
        print(f"support {support}: {d['wins']}/{n}={d['rate']:.1%}")
    for panel in sorted(set("+".join(p["a"])+" vs "+"+".join(p["b"]) for p in probes)):
        rr = [p for p in probes if "+".join(p["a"])+" vs "+"+".join(p["b"]) == panel]
        d = dict(n=len(rr), a_wins=sum(p["result"]["winner"] == "Y" for p in rr),
                 avg_rounds=sum(p["result"]["rounds"] for p in rr) / len(rr),
                 timeouts=sum(bool(p["result"].get("timeout")) for p in rr), actors={})
        for uid in rr[0]["initial"]:
            attempts = collections.Counter()
            for p in rr:
                attempts.update({k.split("|", 1)[1]: v for k, v in p["attempts"].items() if k.startswith(uid+"|")})
            n = len(rr)
            death_rounds = [p["first_zero_round"][uid] for p in rr if uid in p["first_zero_round"]]
            d["actors"][uid] = dict(initial=rr[0]["initial"][uid],
                                     hp_gain=sum(p["hp_gain"].get(uid, 0) for p in rr)/n,
                                     hp_loss=sum(p["hp_loss"].get(uid, 0) for p in rr)/n,
                                     final_hp=sum(p["final"][uid]["hp"] or 0 for p in rr)/n,
                                     final_mp=sum(p["final"][uid]["mp"] or 0 for p in rr)/n,
                                     actions_per_battle={k:v/n for k,v in attempts.items()},
                                     deaths=len(death_rounds),
                                     avg_first_zero_round=sum(death_rounds)/len(death_rounds) if death_rounds else None)
        result["probes"][panel] = d
    # Team standings and side split expose format and initiative biases.
    for fmt in ["official_2v2", "official_3v3"]:
        teams = {}
        for r in official:
            if r["suite"] != fmt:
                continue
            for side in ["a", "b"]:
                team = "+".join(r[side])
                dd = teams.setdefault(team, dict(n=0, wins=0, timeouts=0, points=0))
                w = r["winner"] == team
                dd["n"] += 1
                dd["wins"] += w
                dd["timeouts"] += r["timeout"]
                dd["points"] += r["winner_points"] if w else r["loser_points"]
        result[fmt+"_teams"] = teams
    result["side_a_rates"] = {}
    for suite in sorted(set(r["suite"] for r in official+balanced+maps+supports)):
        rr = [r for r in official+balanced+maps+supports if r["suite"] == suite]
        result["side_a_rates"][suite] = sum(r["winner"] == "+".join(r["a"]) for r in rr)/len(rr)
    path = OUT / "combined_evidence.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Counts", result["match_counts"])
    print("Saved", path)


if __name__ == "__main__":
    main()
