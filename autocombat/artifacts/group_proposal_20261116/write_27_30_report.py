"""Render the selected group tables and their validation summary."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
proposal=json.loads((ROOT/'proposal_final.json').read_text(encoding='utf-8'))
summary=json.loads((ROOT/'summary_final.json').read_text(encoding='utf-8'))
names=json.loads((ROOT/'roster.json').read_text(encoding='utf-8'))
fixed={frozenset(t) for t in proposal['fixed_3v3']}
lines=[
    '# 2v2 / 3v3 分组表与 82 场赛程', '',
    '已应用到默认配置及本地 8889、8890 实例。1v1 规则保持不变。所有分组包含 Y1–Y17 与旧版雪人 Y60。', '',
    '## 规则', '',
    '- 2v2 共 27 队；每位角色进入 3 队。',
    '- 3v3 共 30 队：固定队为 Y5＋Y60＋Y7＋Y8、Y9＋Y12、Y9＋Y3；其余为 27 支三人队。Y9 出现 4 次，其余角色各 5 次。',
    '- 所有三人组两两最多重合 1 位角色。',
    '- 2v2、3v3 每支队伍预赛各 82 场。目标配置为 82，算法按全循环实际场数计算每队欠场并成对补齐。',
    '- 当前队数和默认单地图下，2v2 为 3 轮循环（每队 78 场）加调配 4 场；3v3 为 2 轮循环（每队 58 场）加调配 24 场。加赛总量分别为 54、360 场。旧固定额外场数设置停用。',
    '- 多地图时，全循环基数按地图数计入；系统自动减少循环轮数后再按目标补齐。若目标总差额不能由成对对局精确分配，系统报错，不静默多赛或少赛。', '',
    '## 2v2', '',
    f"每队间对照 4 场、每队累计 {summary['2v2'][0]['games']} 场有效样本；队伍胜率范围 {min(r['rate'] for r in summary['2v2']):.1%}–{max(r['rate'] for r in summary['2v2']):.1%}。该范围仍较宽，表明有队伍强弱差异；此结果用于相对筛选，不代表已严格平衡。", '',
    '| 队伍 | 角色 | 对照场数 | 胜率 |','|---:|---|---:|---:|']
for i,(team,row) in enumerate(zip(proposal['teams']['2v2'],summary['2v2']),1):
    label=' + '.join(f'{s} {names[s]}' for s in team)
    lines.append(f'| {i} | {label} | {row["games"]} | {row["rate"]:.1%} |')
lines += ['', '## 3v3', '',
          f"每队间对照 4 场、每队累计 {summary['3v3'][0]['games']} 场有效样本。27 支可调整三人队胜率范围 {min(r['rate'] for r in summary['3v3'] if len(r['team'])==3):.1%}–{max(r['rate'] for r in summary['3v3'] if len(r['team'])==3):.1%}。固定四人组、二人组胜率分别为 {next(r['rate'] for r in summary['3v3'] if frozenset(r['team'])==frozenset(['Y5','Y60','Y7','Y8'])):.1%}、{next(r['rate'] for r in summary['3v3'] if frozenset(r['team'])==frozenset(['Y9','Y12'])):.1%}、{next(r['rate'] for r in summary['3v3'] if frozenset(r['team'])==frozenset(['Y9','Y3'])):.1%}。固定组人数不同且强度差异明显，胜率单独列示。", '',
          '| 队伍 | 角色 | 对照场数 | 胜率 |','|---:|---|---:|---:|']
for i,(team,row) in enumerate(zip(proposal['teams']['3v3'],summary['3v3']),1):
    label=' + '.join(f'{s} {names[s]}' for s in team)
    if frozenset(team) in fixed:label+='（固定）'
    lines.append(f'| {i} | {label} | {row["games"]} | {row["rate"]:.1%} |')
lines += ['', '## 验证', '',
          '最终采用队表对应 3,144 场有效对照记录：10×10 地图、状态 50、每个对阵 4 场并交换左右位置，零异常。每个角色次数、队伍数、固定阵容、配对数和三人组重合限制均已自动校验。目标场数调配另以多组随机种子验证：27 队每队 82 场、30 队每队 82 场。', '',
          '验证文件：`proposal_final.json`、`summary_final.json`、`validation_refined.json`、`validation_27_30.json`。', '']
(ROOT/'方案_27组82场.md').write_text('\n'.join(lines),encoding='utf-8')
(ROOT/'方案.md').write_text('\n'.join(lines),encoding='utf-8')
print(ROOT/'方案_27组82场.md')
