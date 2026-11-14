# Y14（余烬）修复与强度验证

已实现 Y14 专属修复，三张卡带共享 3 回合冷却，保留当前卡面的伤害、HP、MP 和技能费用。没有采用额外资源或伤害加强。

## 结果

主对手池沿用实施前的 15 名其他角色，每名对手 100 场，50 个独立种子、双方各一次，共前后各 1500 场：

| 指标 | 基线 | 修复后 |
|---|---:|---:|
| 胜场／场次 | 594 / 1500 | 876 / 1500 |
| 总胜率 | 39.6% | 58.4% |
| 单个对手胜率中位数 | 24% | 50% |
| 空切次数 | 2306 | 0 |
| 拒绝施法次数 | 4011 | 0 |
| 卡带切换次数 | 6883 | 1187 |

不含 Y14 的全部 120 组配对、240 场控制对局，命令、行动、HP/MP、位置、效果和比赛结果的指纹前后一致。这验证了本次改动对该固定种子样本中的其他角色对局没有副作用。

Y17 在实施期间另行启用及修改，单独报告，不纳入原有对手池：含 Y17 的前后各 1600 场总胜率为 43.4% → 61.0%。这不是完整预赛或团战排名；结论限于 10×10、season=50、20 回合上限的 1v1 条件。

## 原因与实际修复

- 卡带切换的四个目标键重复计权，训练还给未学过的切换固定高分。Y14 纯自身动作仅保留 T0，移除其切换乐观加分；旧权重别名仅在内存兼容。
- 当前形态空切、已弹出卡带、冷却、MP／计数器不足、已使用的一次性技能，以及没有合法敌人的攻击，统一从候选和执行入口拦截。计数器延迟至最终 MP 检查通过后扣除，避免失败时清空极限槽。
- 成功切换任一卡带后，三张均进入 3 回合冷却；第 R 轮切换，经过三次全局轮末结算，第 R+3 轮可再切换。强制弹出卡带不会重置或绕过主动切换的锁定状态。
- 切入形态初始化其被动，计数器不重复创建。极限槽跨形态保留；金环进出音速时清零；残机替身在致命伤触发时支付 8MP，而非加载卡带时预付。支付不足不触发，成功后保留 1HP、弹出并锁定魂斗罗。
- 普攻按当前卡带及重剑模式读取既有伤害与射程。最终幻想默认迅捷，两种模式互斥，退出形态清理其自身模式效果。
- 金环由冲刺期间实际移动满 8 格或成功闪避获取，合计每轮一次；实际受伤掉一枚。极限槽的造成伤害、受到伤害、勇猛成功反击分别每轮最多一次；胜出的反击即使被护盾吸收，也独立触发反击充能。保留逐轮与队友首次濒死充能。
- Full、Fast 和 HTTP 手动反应路径同步处理 Y14 的射程、闪避、反击与资源事件。所有新增角色机制均由 Y14 身份条件启用。

## 各对手结果

同一组筛选种子、原有 15 名对手的消融探针结果如下。机制相互依赖，这些差异不能作为可相加的单项贡献：

| 筛选配置 | 胜场／场次 | 胜率 |
|---|---:|---:|
| 仅 AI 候选、去重与预检 | 90 / 300 | 30.0% |
| AI 修正＋共享冷却 | 88 / 300 | 29.3% |
| 完整机制修复 | 167 / 300 | 55.7% |

每格为 100 场胜率，双方各 50 场。

| 对手 | 基线 | 修复后 |
|---|---:|---:|
| Y1 | 28% | 42% |
| Y2 | 22% | 55% |
| Y3 | 30% | 50% |
| Y4 | 18% | 41% |
| Y5 | 20% | 61% |
| Y6 | 90% | 93% |
| Y7 | 100% | 100% |
| Y8 | 99% | 100% |
| Y9 | 24% | 50% |
| Y10 | 24% | 45% |
| Y11 | 22% | 39% |
| Y12 | 15% | 34% |
| Y13 | 16% | 41% |
| Y15 | 16% | 51% |
| Y16 | 70% | 74% |
| Y17 | 100% | 100% |

## 验证与保留事项

- 新增 `tests/test_y14_balance.py` 共 25 项测试；执行、辅助平衡、召唤、模拟及效果规则等相关测试共 140 项通过。
- 全量 206 项中 205 项通过，1 项既有 UI 测试失败：`test_invalid_enabled_membership_is_atomic_and_disabled_drafts_are_preserved` 假定启用 Y17 必须返回 400，实际返回 200。使用实施前保存的角色卡也能复现，未修改该无关断言。完整输出见 `tests_full.log`。
- `py_compile`、`git diff --check` 通过；`merge_backend.py` 已同步生成后端。生成后端的完整 AI 对局及 `/api/characters`、`/api/pvp/ping` 冒烟通过，结果见 `backend_smoke.json`。
- 保留工作区既有改名、等级调整及并行 Y17 修改。本次只为 Y14 的三张卡带增加数据字段，其余加强来自机制与 AI 修复。
- 本次没有重训或覆盖权重。实施期间另一项工作更新了根目录 `ai_weights.json`，该文件的修改保持原样。评估使用独立冻结副本，SHA256 为 `fcea158e03a2e44e1e94309acadbcfd1ff6cec641bc5d5c9a744a4f39841f7d3`；源码和输入校验值见 `summary.json`。
- 原有基础数值不追加加强。首次筛选已达到目标范围，因此没有启动 MP／费用配置搜索。伤害日志字段按带 `source_dmg` 的 HP 损失统计，历史互命中分支的记录口径不同，故不据此宣称精确伤害涨幅。

## 复现

`final_source` 保存验证源码，`comparison_inputs.json` 保存角色卡、召唤物和设置，`weights/ai_weights.json` 保存只读评估权重。基线在同一源码上关闭本次 Y14 分支，保留其他并行修改；控制对局验证这些分支的隔离性。

```powershell
$env:PYTHONHASHSEED = '0'
python -X utf8 artifacts/balance_y14/compare.py --source artifacts/balance_y14/final_source --disable-hooks --seeds 50 --seed 2026111500 --output artifacts/balance_y14/verified_before.json
python -X utf8 artifacts/balance_y14/compare.py --source artifacts/balance_y14/final_source --seeds 50 --seed 2026111500 --output artifacts/balance_y14/verified_after.json
python -X utf8 artifacts/balance_y14/compare.py --source artifacts/balance_y14/final_source --disable-hooks --mode control --seeds 1 --output artifacts/balance_y14/verified_control_before.json
python -X utf8 artifacts/balance_y14/compare.py --source artifacts/balance_y14/final_source --mode control --seeds 1 --output artifacts/balance_y14/verified_control_after.json
python -X utf8 artifacts/balance_y14/summarize.py
```

消融诊断使用 `--ablation ai` 或 `--ablation cooldown`，筛选种子为 2026111400–2026111409。独立验证种子为 2026111500–2026111549。结果保存在 `final_ai_only.json`、`final_ai_cooldown.json` 与 `final_screen.json`；冷却减少乱切，形态机制补全才是主要强度来源，不能把冷却本身当成数值加强。
