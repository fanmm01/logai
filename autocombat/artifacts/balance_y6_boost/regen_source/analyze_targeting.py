#!/usr/bin/env python3
"""Analyze summon vs player targeting tendencies across all characters."""
import json, re
from collections import defaultdict

with open('ai_weights.json','r',encoding='utf-8') as f:
    data = json.load(f)

char_names = {
    'Y1':'炎焱','Y2':'达尼厄斯','Y3':'灵牛','Y4':'星闪',
    'Y5':'血天使','Y6':'雪人','Y7':'环花暖','Y8':'比哈米',
    'Y9':'木落','Y10':'春晌','Y11':'蓝','Y12':'斯瑞提卡'
}

# ===== 1. T3 vs Player Targeting Summary =====
print('='*70)
print('全角色 T3(召唤物) 攻击倾向排名 (Q_team)')
print('='*70)
rankings = []
for ck in ['Y1','Y2','Y3','Y4','Y5','Y6','Y7','Y8','Y9','Y10','Y11','Y12']:
    entries = data['Q_team'].get(ck, {})
    state_pairs = defaultdict(dict)
    for key, val in entries.items():
        m = re.match(r'^([\d|]+)__(.+)$', key)
        if not m: continue
        st_str, ak_full = m.group(1), m.group(2)
        parts = ak_full.split('__')
        if len(parts) != 2: continue
        base_ak, target = parts[0], parts[1]
        if target in ('T0','T1','T2','T3') and base_ak.startswith(('SKILL_','BASIC_ATTACK')):
            state_pairs[(st_str, base_ak)][target] = val

    diffs = []
    for (st_str, base_ak), targets in state_pairs.items():
        if 'T3' in targets:
            t3v = targets['T3']
            pv = max([v for t,v in targets.items() if t in ('T0','T1','T2')], default=None)
            if pv is not None:
                diffs.append(t3v - pv)

    if diffs:
        avg = sum(diffs)/len(diffs)
        t3pct = 100*sum(1 for d in diffs if d > 0.001)/len(diffs)
        playerpct = 100*sum(1 for d in diffs if d < -0.001)/len(diffs)
        rankings.append((char_names[ck], ck, t3pct, playerpct, avg, len(diffs)))

rankings.sort(key=lambda x: -x[2])
print(f'{"角色":<10} {"召唤物优先%":>10} {"角色优先%":>10} {"avg_diff":>10} {"样本":>6}  图示')
for name, ck, t3pct, plpct, avg, total in rankings:
    bar_len = max(int(t3pct/4), 1) if t3pct > 0 else 0
    bar = '#' * bar_len
    print(f'{name}({ck}):   {t3pct:6.1f}%    {plpct:6.1f}%    {avg:+9.4f}   {total:4d}   {bar}')

# ===== 2. T0(残血) vs T2(高威胁) 倾向 =====
print()
print('='*70)
print('T0(最低HP收割) vs T2(最高DEX威胁打击) 倾向')
print('='*70)
for ck in ['Y1','Y2','Y4','Y5','Y6','Y7','Y8','Y9','Y10','Y11','Y12']:
    entries = data['Q_team'].get(ck, {})
    state_pairs = defaultdict(dict)
    for key, val in entries.items():
        m = re.match(r'^([\d|]+)__(.+)$', key)
        if not m: continue
        st_str, ak_full = m.group(1), m.group(2)
        parts = ak_full.split('__')
        if len(parts) != 2: continue
        base_ak, target = parts[0], parts[1]
        if target in ('T0','T2') and base_ak.startswith(('SKILL_','BASIC_ATTACK')):
            state_pairs[(st_str, base_ak)][target] = val

    diffs = []
    for (st_str, base_ak), targets in state_pairs.items():
        if 'T0' in targets and 'T2' in targets:
            diffs.append(targets['T0'] - targets['T2'])

    if diffs:
        avg = sum(diffs)/len(diffs)
        t0pct = 100*sum(1 for d in diffs if d > 0.001)/len(diffs)
        t2pct = 100*sum(1 for d in diffs if d < -0.001)/len(diffs)
        bar = 'T0' * max(int(t0pct/10),1) if t0pct > t2pct else 'T2' * max(int(t2pct/10),1)
        print(f'  {char_names[ck]}({ck}): T0={t0pct:.0f}% T2={t2pct:.0f}% avg={avg:+.4f} n={len(diffs)}  [{bar}]')

# ===== 3. Y12 斯瑞提卡 detailed =====
print()
print('='*70)
print('斯瑞提卡(Y12): 各技能×各目标 Q值矩阵 (不可指定=1)')
print('='*70)
y12 = data['Q_team'].get('Y12', {})
skill_target_q = defaultdict(lambda: defaultdict(list))
for key, val in y12.items():
    m = re.match(r'^([\d|]+)__(.+)$', key)
    if not m: continue
    st_str, ak_full = m.group(1), m.group(2)
    parts = ak_full.split('__')
    if len(parts) != 2: continue
    base_ak, target = parts[0], parts[1]
    if target in ('T0','T1','T2','T3'):
        skill_target_q[base_ak][target].append(val)

print(f'{"技能":<15} {"T0(残血)":>10} {"T1(最近)":>10} {"T2(高威)":>10} {"T3(召唤)":>10}  倾向')
for sk in sorted(skill_target_q.keys()):
    avgs = {}
    for t in ['T0','T1','T2','T3']:
        vals = skill_target_q[sk].get(t, [])
        avgs[t] = sum(vals)/len(vals) if vals else 0
    best_t = max(avgs, key=avgs.get)
    vals_str = '  '.join(f'{avgs[t]:8.4f}' for t in ['T0','T1','T2','T3'])
    print(f'{sk:<15} {vals_str}  -> {best_t}')

# Best target per skill per state
y12_best = defaultdict(lambda: defaultdict(int))
for key, val in y12.items():
    m = re.match(r'^([\d|]+)__(.+)$', key)
    if not m: continue
    st_str, ak_full = m.group(1), m.group(2)
    parts = ak_full.split('__')
    if len(parts) != 2: continue
    base_ak, target = parts[0], parts[1]
    if target in ('T0','T1','T2','T3'):
        y12_best[(st_str, base_ak)][target] = val

best_count = defaultdict(lambda: defaultdict(int))
for (st_str, base_ak), targets in y12_best.items():
    if len(targets) >= 2:
        best_t = max(targets, key=targets.get)
        best_count[base_ak][best_t] += 1

print()
print('各技能最优目标次数:')
print(f'{"技能":<15} {"T0":>6} {"T1":>6} {"T2":>6} {"T3":>6}')
for sk in sorted(best_count.keys()):
    counts = '  '.join(f'{best_count[sk].get(t,0):4d}' for t in ['T0','T1','T2','T3'])
    print(f'{sk:<15} {counts}')

# ===== 4. Y5 血天使吸血策略 =====
print()
print('='*70)
print('血天使(Y5): 吸血(SKILL_2) — 偏好低风险目标?')
print('='*70)
y5 = data['Q_team'].get('Y5', {})
y5_sk2 = defaultdict(list)
for key, val in y5.items():
    m = re.match(r'^([\d|]+)__SKILL_2__(.+)$', key)
    if not m: continue
    st_str, target = m.group(1), m.group(2)
    if target in ('T0','T1','T2','T3'):
        y5_sk2[target].append(val)

for t in ['T0','T1','T2','T3']:
    vals = y5_sk2.get(t, [])
    if vals:
        print(f'  {t}: avg={sum(vals)/len(vals):.4f}  min={min(vals):.4f}  max={max(vals):.4f}  n={len(vals)}')

# ===== 5. Y6 雪人 =====
print()
print('='*70)
print('雪人(Y6): 发送雪球(SKILL_2) 目标策略')
print('='*70)
y6 = data['Q_team'].get('Y6', {})
y6_sk2 = defaultdict(list)
for key, val in y6.items():
    m = re.match(r'^([\d|]+)__SKILL_2__(.+)$', key)
    if not m: continue
    st_str, target = m.group(1), m.group(2)
    if target in ('T0','T1','T2','T3'):
        y6_sk2[target].append(val)

for t in ['T0','T1','T2','T3']:
    vals = y6_sk2.get(t, [])
    if vals:
        print(f'  {t}: avg={sum(vals)/len(vals):.4f}  n={len(vals)}')

# ===== 6. Y10 春晌 summon vs combat skills =====
print()
print('='*70)
print('春晌(Y10): 召唤技能 vs 战斗技能 目标倾向差异')
print('='*70)
y10 = data['Q_team'].get('Y10', {})
for sk in ['SKILL_1','SKILL_2','SKILL_3','SKILL_4']:
    sk_targets = defaultdict(list)
    for key, val in y10.items():
        m = re.match(r'^([\d|]+)__' + sk + '__(.+)$', key)
        if not m: continue
        st_str, target = m.group(1), m.group(2)
        if target in ('T0','T1','T2','T3'):
            sk_targets[target].append(val)
    if sk_targets:
        avgs = {t: sum(v)/len(v) for t,v in sk_targets.items() if v}
        best = max(avgs, key=avgs.get)
        print(f'  {sk}: best_target={best}  ' + '  '.join(f'{t}={avgs.get(t,0):.3f}' for t in ['T0','T1','T2','T3']))
