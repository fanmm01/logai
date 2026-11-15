"""Build the delivery report directly from frozen-input acceptance artifacts."""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--after', default='level6_final_hp19_tournaments10.json')
    parser.add_argument('--before', default='level6_baseline5500_tournaments10.json')
    parser.add_argument('--team', default='level6_hp19_team_validation.json')
    args = parser.parse_args()
    after = read(args.after)
    before = read(args.before)
    verification = read('level6_verification.json')
    team = read(args.team)
    assert len(after['runs']) == len(before['runs']) == 10
    assert all(s['games'] == 400 for s in team['summary'].values())
    frozen_tag = Path(after['source_snapshot']).name
    before_by_seed = {r['seed']: r for r in before['runs']}
    rows = []
    same_status = True
    for index, row in enumerate(after['runs'], 1):
        baseline = before_by_seed[row['seed']]
        same_status &= row['season_status'] == baseline['season_status']
        rows.append(f"| {index} | {row['seed']} | {baseline['wins']} | {row['wins']} | {row['rank']} |")
    stats = after['effective_character']
    checks = '\n'.join(f"- `{ ' '.join(c['command']) }`：退出码 {c['returncode']}。" for c in verification['checks'])
    teams = '\n'.join(f"| {fmt} | {s['games']} | {s['wins']} | {s['wins']/s['games']:.1%} | {s.get('timeouts', 0)} |"
                      for fmt, s in team['summary'].items())
    audited_friendly = sum(s.get('friendly_damage', 0) for s in team['summary'].values())
    audited_games = sum(s['games'] for s in team['summary'].values())
    rejection_count = sum(r['counts'].get('command_rejects', 0) for r in after['runs'])
    report = f'''# Y17 夜诏明：六级补强实施与验收

日期：2026-11-15（Asia/Singapore）。

## 验收结论

最新目标是等级固定为 6，十次完整单挑锦标赛预赛平均每次 45–47 胜；每次每人 82 场，淘汰赛不计入。

最终实测 **{sum(r['wins'] for r in after['runs'])}/820，平均 {after['mean_wins']:.1f} 胜/次**；验收标记 `target_met={after['target_met']}`。
同口径旧实现为平均 **{before['mean_wins']:.1f} 胜/次**。一、二阶段的机制和 AI 补强已落实。

| 次数 | 种子 | 旧实现胜场 | 最终版胜场 | 最终版积分名次 |
|---|---|---:|---:|---:|
{chr(10).join(rows)}

采用正式 `Tournament.run_phases`、`BattleEngine` 和 `AIController`，每次包含全部角色预赛，十次合计 6,970 场；不是仅抽取 Y17 对位的筛查。保留当前 10×10 地图、射程覆盖开关、随机赛季状态及正式回合上限。对照角色、权重和配置冻结；每次赛季状态映射一致：`{same_status}`。

验收使用独立于调参筛查的种子。四个独立进程各自串行执行完整锦标赛，避免线程共享随机数导致结果随调度漂移；对手仍使用冻结的原权重。改动会改变后续掷骰序列，因此同种子不代表逐场掷骰完全相同。本批均值是验收结果，不是对任意未来十次赛季的保证。

## 最终角色与参数

- 等级 **{stats['level']}**；基础 HP **{stats['hp']}**，标准变身后 HP **{stats['hp'] * (5 + stats['level'])}**；基础 MP {stats['mp']}，变身后 MP {int(stats['mp'] * (2 + .5 * stats['level']))}。
- 自定义“弓术”现在获得并能正确撤销通用变身的 +20、上限 80 修正；没有提高行动数和大招倍率。
- 幻提供剑/弓两种形态：100ml、持续 10 回合；血弓 1d8+2、射程 8，使用真实弓术，避免飞行近战惩罚；剑型保留月斩前置。
- 守保留每 300ml→1 甲，AI 按威胁选择 300/600/900ml，保留进攻材料；同名不叠加。
- 存改为 500ml→4HP，仍需 2MP、1SAN，受轻伤、距离、昏迷/濒死限制；按缺血和库存选择份数。
- 月斩保留 500ml 基础费用与追加剑气投入；命中实际失血有限收血，25ml/HP，整招最多 200ml，并占用同一身体的 1L 来源额度。
- 血枪保留 1L、近战 1d8+6、投掷 1d10+6、三次耐久；投掷消耗整枪，成功反击与格挡损坏也计入磨损。
- 披风保留 1L→4MP；AI 仅在缺少攻击所需魔力时考虑换蓝。

在 6 级约束下，小幅生命、血枪耐久和换蓝倍率候选未稳定达到目标。HP 18 的五次筛查为 46、44、46、47、44 胜，但其独立十次验收平均 44.0 胜；HP 20 的另一批十次平均 49.4 胜，超过目标上沿。因此收敛到上述 HP，并更换种子进行本批验收。不同批次种子不同，不能把均值差当作单一 HP 参数的精确因果效果。未采用试验过的五次血枪耐久、1L→8MP 或双倍护甲；未重训或重写全局权重。

## 一、二阶段完成项

1. 费用、目标、距离和状态先验证再提交；随机费用只结算一次。非法施法不扣血、HP/MP/SAN 或动作，续唱和 PvP 反应续接不重扣。最终十次中 Y17 命令拒绝数为 {rejection_count}。
2. 吟唱按三/两次主动作推进，回合结束不自动推进；保留真实魔力投入、方向与原点，修复致命反噬离场后取不到坐标的问题。多人同序号吟唱相互独立。
3. 祭炼保留施法者最大 HP 的 90% 口径；目标离开范围或死亡时消散，成功后濒死，救援和死亡追加只结算一次。
4. 月相按真实失血吸取，护盾和过量伤害不造治疗；血液来源额度在活体、尸体和复活之间共用，复活不恢复死亡快照中的旧额度。
5. 污血连锁改为迭代执行，不重复启动费用，目标耗尽不空扣；超过递归深度的有限血池连锁测试可终止。
6. 血弓、血刃和血枪共用攻击配置；武器到期恢复原装备，最后一次使用保留正确伤害，被格挡损坏不转移到下一根血枪。
7. AI 共享只读投入计划，保护血液预算，避免补 1HP 或少量缺蓝就清空血池。启动吟唱时核对实际射线；友军进入已付费射线时可取消，已付费用不返还。
8. 防御按当前闪避/反击技能选择倾向；对已失败攻击优先利用免费反击机会。PvP 显式反应覆盖优先，人工选择保持可用。
9. PvP 角色合并补齐血液规则和扩展技能字段，Full/Fast/集成后端机制回归通过。血池、武器、吟唱和诊断字段随战斗状态保存。

## 团队与验证边界

| 赛制 | 场数 | 随队胜场 | 随队胜率 | 超时 |
|---|---:|---:|---:|---:|
{teams}

团队筛查使用轮换搭档和对手、交换阵营，覆盖治疗、无治疗和召唤组合。它验证可用性与副作用，不用于推导完整全员排名。
最初友伤诊断在目标死亡后使用了缺省阵营，误把部分击杀记为友伤；已改为受伤前捕获阵营。该修正仅影响统计。最终 {audited_games} 场中，记录友伤 {audited_friendly} HP；旧版过程文件中的友伤总数不作为判断依据。

相关战斗回归 **117 项通过**，生成集成后端后同一 Y17 套件 **37 项通过**。
{checks}

初轮全量测试另发现既有 UI 用例问题：启用测试假定 Y17 仍是不可启用草稿；另两处回放断言固定期望两行，而当前不含 Y17 的对局产生三行。本轮未修改这些 UI 用例或 UI 业务逻辑，不把相关战斗测试通过表述为全仓库零失败。

## 复跑与产物

- 正式结果：`{args.after}`；逐次原始结果在同名 `_parts/` 目录。
- 旧实现：`{args.before}`；团队：`{args.team}`。
- 指纹：`level6_source_manifest.json`；测试及安装源码一致性：`level6_verification.json`。
- 复跑入口：`run_acceptance.py`；团队入口：`run_team_validation.py`；集成验证：`verify_integrated.py`。
- 本机冻结源码与权重：`.ui-runtime/y17-level6/baseline/`、`.ui-runtime/y17-level6/{frozen_tag}/`。验收输入前后未变化：`{after['inputs_unchanged']}`；安装源码与最终冻结版本一致：`{all(verification['installed_matches_frozen'].values())}`。

```powershell
$env:PYTHONHASHSEED='17'
python artifacts/balance_y17/run_acceptance.py --source .ui-runtime/y17-level6/{frozen_tag} --settings .ui-runtime/y17-level6/{frozen_tag}/settings.json --runs 10 --seed {after['seed']} --workers 4 --output artifacts/balance_y17/recheck.json
```

任务期间已有的其他角色编辑已保留；受控对照固定同一份对手数据。源码与集成后端已同步，本轮没有重启服务。
'''
    (HERE / 'Y17六级补强实施与验收.md').write_text(report, encoding='utf-8')
    print('report written; target_met=', after['target_met'])


if __name__ == '__main__':
    main()
