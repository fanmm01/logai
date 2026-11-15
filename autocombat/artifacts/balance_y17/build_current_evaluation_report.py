"""Summarize the frozen current-version win-rate test without changing it."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def main():
    result = json.loads((HERE / 'current_evaluation_tournaments10.json').read_text(encoding='utf-8'))
    manifest = json.loads((HERE / 'current_evaluation_manifest.json').read_text(encoding='utf-8'))
    runs = result['runs']
    assert len(runs) == 10 and all(r['battles'] == 82 for r in runs)
    assert result['inputs_unchanged'] and not result['errors']
    opponents = defaultdict(Counter)
    for run in runs:
        for game in run['games']:
            a, b, winner, rounds, *_ = game
            if 'Y17' not in (a, b):
                continue
            opponent = b if a == 'Y17' else a
            opponents[opponent]['games'] += 1
            opponents[opponent]['wins'] += winner == 'Y17'
            opponents[opponent]['rounds'] += rounds
    games = sum(s['games'] for s in opponents.values())
    wins = sum(s['wins'] for s in opponents.values())
    assert games == 820 and wins == sum(r['wins'] for r in runs)
    non_support = {s: v for s, v in opponents.items() if s not in ('Y6', 'Y7', 'Y8')}
    combat_wins = sum(s['wins'] for s in non_support.values())
    combat_games = sum(s['games'] for s in non_support.values())
    mean = wins / 10
    comparison = '处于目标区间' if 43 <= mean <= 45 else '高于目标上沿' if mean > 45 else '低于目标下沿'
    per_run = '\n'.join(f"| {i} | {r['seed']} | {r['wins']}/82 | {r['wins']/82:.1%} | {r['rank']} |"
                        for i, r in enumerate(runs, 1))
    per_opponent = '\n'.join(f"| {serial} | {opponents[serial]['wins']}/{opponents[serial]['games']} | {opponents[serial]['wins']/opponents[serial]['games']:.1%} |"
                             for serial in sorted(opponents, key=lambda s: int(s[1:])))
    hp = manifest['y17']['attrs']['体力上限']
    level = manifest['y17']['attrs']['等级']
    rejection_count = sum(r['counts'].get('command_rejects', 0) for r in runs)
    report = f'''# Y17 当前状态胜率测试

日期：2026-11-15（Asia/Singapore）。本轮仅冻结并测试当前版本，没有继续调参。

## 结果

**十次完整单挑锦标赛平均 {mean:.1f} 胜/82 场，总计 {wins}/820，胜率 {wins/820:.2%}。**

相对用户当前目标“6 级、基础 HP≤10、平均 43–45 胜”：**{comparison}**。

- 冻结时等级 {level}、角色卡基础 HP {hp}、标准变身后 HP {hp * (5 + level)}。
- 十次胜场范围 {min(r['wins'] for r in runs)}–{max(r['wins'] for r in runs)}；平均积分名次 {statistics.mean(r['rank'] for r in runs):.1f}/17。
- 排除 Y6/Y7/Y8 三名辅助后：{combat_wins}/{combat_games}，胜率 {combat_wins/combat_games:.2%}。
- Y17 命令拒绝 {rejection_count} 次；对局异常 {len(result['errors'])} 次。

## 每次锦标赛

| 次数 | 种子 | 胜场 | 胜率 | 积分名次 |
|---|---|---:|---:|---:|
{per_run}

## 实际对位

| 对手 | 胜/场 | 胜率 |
|---|---:|---:|
{per_opponent}

## 测试条件

复用正式 `Tournament.run_phases`、`BattleEngine` 与 `AIController`。每次完整运行 17 名角色单挑预赛，每次 Y17 为 82 场；十次合计执行 6,970 场预赛，未计淘汰赛。胜场采用正式积分榜字段，并由逐场记录复核。

保留当前地图、射程覆盖、回蓝、回合限制、AI 权重和随机赛季状态。每次锦标赛内部串行运行以固定随机序列，四个独立进程并发处理四次完整锦标赛。十次使用不同种子；不是固定状态 50 的对位筛查。

当前包含持续血甲、血武器与血球修复、受控月相选择、三行动阿瑞卡纳的完成奖励骰，以及预检/执行统一射程设置等机制。当前具体数据及指纹以 `current_evaluation_manifest.json` 为准。

输入前后未变化：`{result['inputs_unchanged']}`。结果只对应这份冻结版本；本轮没有根据结果继续改动角色。

原始结果：`current_evaluation_tournaments10.json`；逐次原始记录：`current_evaluation_tournaments10_parts/`；冻结输入清单：`current_evaluation_manifest.json`。

```powershell
$env:PYTHONHASHSEED='17'
python artifacts/balance_y17/run_acceptance.py --source .ui-runtime/y17-current-evaluation --settings .ui-runtime/y17-current-evaluation/settings.json --runs 10 --seed 2026117500 --workers 4 --output artifacts/balance_y17/current_recheck.json
```
'''
    (HERE / '当前状态胜率测试.md').write_text(report, encoding='utf-8')
    summary = dict(wins=wins, games=games, win_rate=wins/games, mean_wins=mean,
                   comparison=comparison, wins_per_tournament=[r['wins'] for r in runs],
                   non_support_wins=combat_wins, non_support_games=combat_games,
                   mean_rank=statistics.mean(r['rank'] for r in runs),
                   opponents=dict(opponents), command_rejections=rejection_count)
    (HERE / 'current_evaluation_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
