"""Summarize measured results and the unchanged-opponent control."""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
import characters_data as cd


def main():
    before = json.loads((HERE/'before_verified.json').read_text(encoding='utf-8'))
    after = json.loads((HERE/'final.json').read_text(encoding='utf-8'))
    targets = after['targets']
    for filename in ('ai_weights.json', 'ai_battle.py', 'ai_trainer.py'):
        assert before['runs'][0]['fingerprints'][filename] == after['runs'][0]['fingerprints'][filename]
    names = {c['serial']: c['name'] for c in cd.ALL_CHARACTERS}
    def mean(data, serial, key='wins'):
        return sum(r['records'][serial][key] for r in data['runs'])/len(data['runs'])
    control_count = 0
    for a, b in zip(before['runs'], after['runs']):
        assert a['seed'] == b['seed']
        old = [g for g in a['games'] if g[0] not in targets and g[1] not in targets]
        new = [g for g in b['games'] if g[0] not in targets and g[1] not in targets]
        assert old == new, 'Unselected vs unselected battle changed'
        control_count += len(old)
    spec = importlib.util.spec_from_file_location('before_characters', HERE/'before_source/characters_data.py')
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    originals = {c['serial']: c for c in baseline.ALL_CHARACTERS}
    for c in cd.ALL_CHARACTERS:
        assert c['attrs'] == originals[c['serial']]['attrs']
        assert c['str_attrs'] == originals[c['serial']]['str_attrs']
        if c['serial'] not in targets:
            assert c == originals[c['serial']]
    assert cd.SUMMON_TEMPLATES == baseline.SUMMON_TEMPLATES
    assert after['target_met']
    import hashlib
    for filename, fingerprint in after['runs'][0]['fingerprints'].items():
        assert hashlib.sha256((ROOT/filename).read_bytes()).hexdigest() == fingerprint, filename
    lines = ['# 五角色平衡调整验收', '', '日期：2026-11-15（Asia/Singapore）。', '',
        '采用当前生产 `Tournament`、`BattleEngine`、AI 权重和用户赛制设置。'
        '五届固定种子 2026118500–2026118504；每人每届 82 场，'
        '每届 697 场，总计 3,485 场真实 1v1 预赛。无淘汰赛胜场、无改分。'
        '仅关闭显示延时，并将赛事进程数设为 1，五届在独立进程中同时运行。', '',
        '|角色|调整前均胜场|调整后均胜场|五届逐届胜场|目标|', '|---|---:|---:|---|---|']
    for s, (lo, hi) in targets.items():
        scores = ' / '.join(str(r['records'][s]['wins']) for r in after['runs'])
        lines.append(f'|{names[s]} {s}|{mean(before,s):.1f}|{mean(after,s):.1f}|{scores}|{lo}–{hi}|')
    lines += ['', '## 其余成员稳定性', '',
        f'未调整成员彼此之间的 {control_count:,} 场比赛，胜负、回合数及比赛记录与对照完全一致。'
        '下表总战绩的变化来自对阵五名调整角色的比赛。总胜场守恒，目标角色增加的胜场会由对手承担。', '',
        '|角色|原均胜场|现均胜场|胜场变化|原均积分|现均积分|', '|---|---:|---:|---:|---:|---:|']
    for s in sorted(names, key=lambda s: int(s[1:])):
        if s not in targets:
            b, a = mean(before,s), mean(after,s)
            lines.append(f'|{names[s]} {s}|{b:.1f}|{a:.1f}|{a-b:+.1f}|{mean(before,s,"points"):.1f}|{mean(after,s,"points"):.1f}|')
    lines += ['', '## 改动边界', '',
        '- 所有角色 attrs、str_attrs 完全保留，包括等级、生命、MP、基础攻击属性。',
        '- 未调整角色定义、所有召唤物模板、AI 权重、全局赛制和全局伤害/控制规则保持原值。',
        '- 蓝：镰刀伤害与吸血提升，增加被动镰刃余势，覆盖其常用普通攻击。',
        '- 春晌：提高屏障护盾、降低屏障费用，保留治疗、藤蔓及蜂鸟原机制。',
        '- 木落：矛术提高成功率及伤害，增加命中后的生命回流；阶段转换、召唤和领域不变。',
        '- 雪人：提高雪球伤害，取消雪球闪避反应，增加雪之法杖被动输出增益；雪带与雪幕不变。',
        '- 环花暖：主体战意独立参数；战意下降产生 p1/p2、造成伤害降低、沉默4级。'
        '主体归零不撤离、不扣血；友方不受影响，多个来源每回合只处理主体一次。'
        '来源离场、阵亡或隔离时压制停止；原召唤物战意与撤离规则不变。', '',
        '## 最终数值', '',
        '- 蓝：镰刀 `1d10+1d8+2 → 1d10+1d8+6`，吸血 `0.35 → 0.65`；镰刃余势被动伤害骰 +3。',
        '- 春晌：屏障 `2d6+3 → 3d6+4`，费用 `9 → 8 MP`。',
        '- 木落：生之矛 `4d6+3 → 4d6+7`，死之矛 `2d8 → 2d8+4`；两矛成功率 `70 → 85`、吸血 `0 → 0.25`；友方伤害及延迟治疗不变。',
        '- 雪人：雪球 `1d6+1d4+2 → 2d8+2`，取消闪避反应；雪之法杖被动伤害骰 +17。',
        '- 环花暖：主体初始4层、每轮65%削弱1层；4层起攻击p1、2层起p2、1层起造成伤害-30%、0层沉默4级。召唤物维持原5层/50%机制。', '',
        '## 验证', '',
        '- 77 项属性、战斗、领域、召唤物与战意回归通过。',
        '- 生成后的集成后端六项专项回归通过，并完成编译检查。',
        '- 全量 269 项测试有三处原有断言失败：角色启用成员校验一处、实时回放行数两处。'
        '均已在改动前快照复现，见 `test_results.log` 和 `baseline_known_failures.log`。',
        '- 正式五届使用冻结源码，五届源码指纹和设置一致，运行期间输入未变化；当前源码与正式验收指纹一致。', '',
        '完整逐场记录、设置和源码指纹见 `before_verified.json`、`final.json`。'
        '这是固定五届实测，随机比赛的其他五届均值仍会波动。', '',
        '复测：`python -B artifacts/balance_five/evaluate.py --output artifacts/balance_five/recheck.json`', '']
    (HERE/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    (HERE/'verification.json').write_text(json.dumps(dict(target_met=True,
        unchanged_control_games=control_count, all_attributes_unchanged=True,
        unselected_definitions_unchanged=True, summon_templates_unchanged=True,
        ai_weights_unchanged=True, accepted_source_matches_workspace=True,
        fingerprints=after['runs'][0]['fingerprints']), ensure_ascii=False, indent=2), encoding='utf-8')
    print('report.md written; unchanged control games:', control_count)


if __name__ == '__main__':
    main()
