from collections import Counter
from itertools import combinations
import json
from pathlib import Path

OUT=Path(__file__).resolve().parent
def read(name): return json.loads((OUT/name).read_text(encoding='utf-8'))
proposal=read('proposal.json')
names=read('roster.json')
baseline=read('validation.json')
patches=[read('validation2_patch.json'),read('validation3_patch.json')]
assert all(p['fingerprints']==baseline['fingerprints'] for p in patches)
assert read('training.json')['fingerprints']==baseline['fingerprints']==read('fixed.json')['fingerprints']
stats={}
rows=[]
for fmt,item in proposal.items():
    teams=item['teams']; valid={frozenset(t) for t in teams}
    selected=[r for r in baseline['rows'] if r['format']==fmt and frozenset(r['a']) in valid and frozenset(r['b']) in valid]
    selected += [r for p in patches for r in p['rows'] if r['format']==fmt]
    expected=Counter()
    errors=[r for r in baseline['errors'] if r['format']==fmt and frozenset(r['a']) in valid and frozenset(r['b']) in valid]
    errors += [r for p in patches for r in p['errors'] if r['format']==fmt]
    for r in selected+errors:
        expected[frozenset((frozenset(r['a']),frozenset(r['b'])))]+=1
    assert len(expected)==153 and set(expected.values())=={8}
    result=[]
    for t in teams:
        games=[r for r in selected if frozenset(t) in (frozenset(r['a']),frozenset(r['b']))]
        wins=sum(r['win'] if frozenset(r['a'])==frozenset(t) else not r['win'] for r in games)
        result.append(dict(team=t,games=len(games),wins=wins,rate=wins/len(games)))
    stats[fmt]=result
    rows+=selected
    counts=Counter(s for t in teams for s in t)
    assert len(teams)==18 and len(counts)==18
    assert sorted(counts.values())==([2]*18 if fmt=='2v2' else [2]+[3]*17)
    triples=[set(t) for t in teams if len(t)==3]
    assert all(len(a&b)<=1 for a,b in combinations(triples,2))
report=['# 2v2 / 3v3 分组调整方案（仅提案，未应用）',
        '', '范围：Y1–Y17 与旧版雪人 Y60，共 18 位。Y6 为雪人 v2。保留现有三支非三人队的成员。',
        '', '## 建议与边界', '',
        '- 两种模式均设 18 队。2v2 每角色出现 2 次；3v3 除 Y16 出现 2 次外，其余角色均出现 3 次。',
        '- 3v3 固定队共占 8 个席位，其他队每队增加 3 个席位，总席位恒为 3t+8，不可能等于 18k。因此完全等次数无解，本方案将最大次数差压到 1。',
        '- 15 个三人组的 105 对比较均通过：最多重合 1 人，内部共 45 个角色对无重复。',
        '- 固定四人队与两支固定二人队分别做了 100 场直接对照，四人队均为 98 胜，200 场无异常。仅修改其余队伍，无法改变这些固定阵容间的直接实力差距。',
        '- 建议将可修改队伍作为近似平衡的主要验收对象；固定特殊队明确标注为特殊阵容。若要求所有队伍的原始胜率均接近 50%，则需要额外允许战斗条件补偿，不能承诺仅换表即可实现。',
        '', '## 完整候选表与试跑结果', '',
        '每对阵容计划 8 场，4 对随机种子分别交换左右方；10×10 地图，所有角色状态 50，2v2 上限 30 回合、3v3 上限 45 回合。表中为有效对局胜率，失败场不计胜负。相同对阵可直接复用首轮记录；换队后的全部相关对阵另行取样。',
        '']
for fmt,result in stats.items():
    rates=[r['rate'] for r in result]
    normal=[r['rate'] for r in result if len(r['team'])==3]
    report += [f'### {fmt}', '',f'全体队伍有效对局胜率范围：{min(rates):.1%}–{max(rates):.1%}。']
    if normal: report += [f'其中可修改的三人组：{min(normal):.1%}–{max(normal):.1%}。']
    report += ['', '| 序号 | 阵容 | 有效场数 | 胜率 |','|---|---|---:|---:|']
    for i,r in enumerate(result,1):
        label=' + '.join(f'{s} {names[s]}' for s in r['team'])
        if fmt=='3v3' and len(r['team'])!=3: label+='（固定）'
        report += [f'| {i} | {label} | {r["games"]} | {r["rate"]:.1%} |']
    report+=['']
report += ['## 出现次数', '', '| 角色 | 2v2 | 3v3 |','|---|---:|---:|']
for s in [f'Y{i}' for i in range(1,18)]+['Y60']:
    report += [f'| {s} {names[s]} | {proposal["2v2"]["appearances"][s]} | {proposal["3v3"]["appearances"][s]} |']
report += ['', '## 赛程配套建议（均未应用）', '',
           '1. 将 2v2 加赛 56 调整为 54：18 队各增加 6 场；将 3v3 加赛 65 调整为 63：各增加 7 场。当前算法按队伍平衡加赛，选择这些总数可避免额外的队伍场数差。',
           '2. 按现有循环轮数，2v2 每队 85+6=91 场，每角色合计 182 个上场席位；3v3 每队 102+7=109 场，Y16 为 218 个席位，其余为 327 个席位。此处统计两边独立出场实体；同角色在对阵双方出现时分别计数。',
           '3. 每赛季轮换 3v3 少一次的角色，并重新满足固定队、角色次数和角色对不重复约束。若 18 赛季中每位各承担一次，则每位累计在表中出现 53 次；队伍赛程量也须一致，长期出场才能相等。',
           '4. 同一对阵轮换左右方，减少队表顺序形成的位置偏差。',
           '', '## 强度证据的限制与编辑顺序', '',
           '当前引擎部分涉及罗杰及其召唤物的对局触发 KeyError：ultimate_threshold / bow_extra_mp。对这些场次均保留异常记录，未记成失败或胜利。因异常并非随机缺失，涉及罗杰的胜率可能有偏差。表中数据为候选筛选依据，不能视为已完成平衡验收。',
           '', '本轮只新增本目录的提案、脚本与取样结果，没有修改默认分组表、运行设置、角色、AI 或战斗逻辑。下一阶段建议先修复上述既有异常并复测候选；再以常规组整体胜率尽量落在 40%–60% 为优化方向，至少每队 300 场有效样本，并对不同状态档位复验。固定特殊组的差距需另行约定可接受范围或补偿规则。',
           '', '实施时实际配置入口为 combat_defaults.py 的默认 TEAM_TABLES，以及 .ui-runtime/settings.json / 对应实例设置；team_tables.py 仅作兼容转发，单改该文件不会修改实际配置。应在后续编辑阶段同步校验实际使用实例。',
           '', '## 可复核材料', '',
           '- proposal.json：最终候选表及次数；proposal_first.json：首轮候选。',
           '- training.json：1,600 场初筛尝试；validation.json：首轮全循环；validation2.json：第二轮 2v2（未选用）；validation2_patch.json / validation3_patch.json：最终局部换搭后的完整相关对阵。',
           '- fixed.json：200 场固定队直接对照。所有阶段所记录的五份源码/权重指纹一致。',
           '- final_summary.json / final_validation.json：最终候选的有效场次和摘要，合并时已确认每种格式 153 个对阵各计划 8 场（包含异常）。',
           '- analyze.py：取样、组合搜索及局部换搭脚本；build_report.py：最终统计与结构核验。',
           '']
(OUT/'方案.md').write_text('\n'.join(report),encoding='utf-8')
(OUT/'final_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'final_validation.json').write_text(json.dumps(dict(rows=rows,fingerprints=baseline['fingerprints']),ensure_ascii=False,indent=2),encoding='utf-8')
for fmt,result in stats.items():
    rates=[r['rate'] for r in result];normal=[r['rate'] for r in result if len(r['team'])==3]
    print(fmt, 'range',min(rates),max(rates),'normal', (min(normal),max(normal)) if normal else None)
print('All structural checks passed; report written.')
