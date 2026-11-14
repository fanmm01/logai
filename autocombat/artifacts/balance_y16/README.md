# Y16 加强实施与验证

日期：2026-11-14（Asia/Singapore）。角色编号 Y16；当前角色数据的显示名为“罗杰”，原设定名为林白。

## 已落地的行为

- 切换武器后立即刷新技能，AI 动作缓存区分武器；Y16 策略状态从 22 维扩展为 25 维，增加武器、月亮存活、月能分档。旧权重可回退读取。
- 重复切换、错误武器、召唤额度耗尽和月能不足不会提交施法。剑终结技要求月能超过 20，MP 校验成功后才消耗月亮。
- 灵感、月亮馈赠、理想城包含自身。馈赠提供全 D100 奖励骰。辉月祝福登记低血触发器，不再于入场直接提供免伤；实际授予时支付 5 月能。
- 月亮不享受系统每回合 2d6 回蓝，维持费为 1；零月能保留月亮，停止补满和付费保护，之后可重新充能。
- 具有有效馈赠链接的单位，所有 HP/MP/SAN 回复溢出均可充能，包括通用回蓝。月亮自身不产生溢出充能；失效链接与重复处理不会额外充能。
- 通用回蓝先于月能补满；变身状态下月亮展开使用 2d3 MP，初始月能等于实际费用。
- 灵感最多两层：有效耗蓝或写作后，自身回合结束每层回复 2d3 MP，每个单位每轮只结算一次；返蓝不递归触发。
- 法杖短句增加 1 充能；弓短句消耗 2 MP、三连发、增加弓本体伤害，共享一次写作且只充能一次。补齐弓术基础值 55。
- 理想城为 6 MP、2d6 护盾、持续三回合；实际吸收伤害按 1:1 转月能。过期不充能。
- 弓终极技保留 10 MP、2d12 基础伤害，改为一回合吟唱并享受一次写作增益。
- PvP 手动反应续接保留本次三连发计划及实际费用，不重复写作、扣费或充能。
- 基础 HP、MP、闪避、行动次数保持原值；剑终结技销毁月亮后，本场不可重召。

## 受控对照

使用真实锦标赛 BattleEngine/AIController，10×10 地图、赛季状态 50、单挑上限 20 回合。
每个对手 100 个种子，每个种子换边，共 3,200 场；辅助排除口径固定为 Y6/Y7/Y8。

| 版本 | 全部对手 | 排除三名辅助 | 超时 |
|---|---:|---:|---:|
| 改动前，冻结初始权重 | 929/3200，29.0% | 360/2600，13.8% | 15 |
| 加强后，仅替换 Y16 专项权重，其余使用同一冻结权重 | 1662/3200，51.9% | 1076/2600，41.4% | 4 |
| 最终安装组合，保留任务期间的外部全局权重更新 | 1649/3200，51.5% | 1061/2600，40.8% | 3 |

第二、三组均未发生 Y16 施法拒绝。受控数据分别见 `baseline_solo_100.json`、`candidate_solo_100.json`；最终组合见 `final_solo_100.json`。

第一层修复的小样本非辅助胜率为 34%～37%，因此启用第二层技能包。曾筛选更积极的弓切换先验，非辅助胜率达到 62.3%，超过目标而放弃；正式版本使用较温和的切换策略。

## 团队与回归

团队使用固定的四组 2v2、四组 3v3 队友/敌队配置，每组 20 个种子并换边。最终 Y16 共 320 场；这些是指定阵容样本，不是所有组合的总体估计。

| 角色替换项 | 2v2 | 3v3 | 合计 |
|---|---:|---:|---:|
| Y16 | 62/160，38.8% | 83/160，51.9% | 145/320，45.3% |
| Y1 | 62/160，38.8% | 48/160，30.0% | 110/320，34.4% |
| Y4 | 82/160，51.3% | 79/160，49.4% | 161/320，50.3% |
| Y11 | 67/160，41.9% | 67/160，41.9% | 134/320，41.9% |

替换对照使用冻结的其他角色权重，见 `candidate_team_20.json`。保留外部更新后的最终 Y16 团队样本结果相同，见 `final_team_20.json`。Y16 达到综合中游目标，但 2v2 仍弱于上述 Y4 对照，未继续叠加数值。

- 三组不含 Y16 的对局，共 60 场：改动前后胜负、回合与结果指纹完全一致，见 `control_before.json` / `control_after.json`。
- 13 项新增 Y16 回归通过，涵盖 Full/Fast、武器缓存、费用原子性、零月能、两队溢出充能、灵感、护盾、吟唱与 PvP 反应续接。
- 全套测试的最新结果见 `test_results.json`。其中角色管理用例 `test_invalid_enabled_membership_is_atomic_and_disabled_drafts_are_preserved` 在冻结的改动前角色数据上亦复现失败（预期 400，实际 200）；未改动该无关用例。
- 集成后端已重新生成并编译；从生成文件提取的 Full/Fast 类均通过月亮、武器与三连发运行验证，见 `verify_integrated.py`。

## 权重与复现

专项训练为 1,200 场（840 场单挑、360 场 3v3），只写回 Y16 的 5,730 条 solo 和 5,712 条 team 项。
任务期间其他训练更新了全局权重，已保存为 `latest_weights.json`；随后仅合并 Y16 项并重新评估。
`weight_installation.json` 记录安装前后哈希，并确认其他角色与全部召唤物表逐项相同。

```powershell
python -B artifacts/balance_y16/evaluate.py --source artifacts/balance_y16/candidate_source --weights artifacts/balance_y16/final_weights.json --seeds 100 --workers 2 --output artifacts/balance_y16/recheck_solo.json
python -B artifacts/balance_y16/evaluate.py --source artifacts/balance_y16/candidate_source --weights artifacts/balance_y16/final_weights.json --format team --roles Y16 --seeds 20 --workers 2 --output artifacts/balance_y16/recheck_team.json
python -B -m unittest tests.test_y16_balance -q
python -B artifacts/balance_y16/verify_integrated.py
```

数值与机制均以当前源码为准，角色编辑器仍使用统一数据源。运行中的服务需要重启以加载引擎与 AI 代码更新。

## 新增数据字段

召唤物模板的 `receives_round_mp_regen` 默认为 true，月亮显式设为 false；`keep_on_empty` 决定维持消耗耗尽后是否保留召唤物。
效果字段 `turn_mp_refund` 表示灵感回合结束返蓝骰，`staff_charge_bonus` / `bow_volley` 控制短句武器变体，`shield_absorb_moon_ratio` 控制护盾实际吸收伤害的月能转化比例。加载器与生成后端均已支持这些字段。
