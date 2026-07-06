# logai v5.0.0 代码审查 Bug 报告

> 审查日期：2026-07-05
> 审查范围：35 个文件，81,854 行新增，419 行删除（已提交），加 129 行未提交调试日志
> 审查方法：9 个发现视角 → 8 个验证 → 1 个遗漏扫描

---

## 🔴 安全严重性

### 1. Flask 调试模式开启导致远程代码执行 (RCE)

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 2432 |
| **严重度** | 🔴 安全严重 |

**问题描述：**
`app.run(host='0.0.0.0', port=8889, debug=True, threaded=True)` — Flask 在生产模式下开启了 Werkzeug 调试器，绑定在公网地址 `0.0.0.0:8889`。

**故障场景：**
攻击者通过网络访问 8889 端口，触发一个未处理异常后，访问 `/console` 路径进入 Werkzeug 交互式 Python 控制台。虽然该控制台有 PIN 码保护，但 PIN 码生成算法依赖机器属性（MAC 地址、机器 ID），有已知的破解技术。一旦获取 PIN，攻击者可在服务器上执行任意 Python 代码，实现完整的远程代码执行。同时 `debug=True` 还会启动自动重载器，导致进程 fork，破坏 `threading.Lock` 状态。

**修复建议：** 将 `debug=True` 改回 `debug=False`。

---

## 🟠 高严重度 — 游戏逻辑破坏

### 2. `_tick_down` 使用过期的效果快照覆盖阶段转换清理

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_engine.py` |
| **行号** | 1562-1692 |
| **严重度** | 🟠 高 |

**问题描述：**
`_tick_down()` 在第 1562 行捕获效果列表的快照 `effects = self._get_effects()`。随后 `_check_hp_triggers()` 触发 `_transition_phase()`，后者通过 `_set_effects()` 移除旧阶段的领域效果。但 `_tick_down` 的第二个循环遍历的是**过期的快照**，在 1692 行用从快照重建的 `new_effects` 覆盖存储，将已删除的领域重新写入。

**故障场景：**
角色在阶段 1 有一个活跃的领域效果（zone）。其 HP 降到阈值以下 → hpTrigger 触发 → `_transition_phase` 从存储中移除阶段 1 的领域。`_tick_down` 的快照中仍包含该领域。如果该领域的 `remainingRounds > 1`，它会被添加到 `new_effects` 中并写回存储，**静默复活已移除的领域**。hpTrigger 的 `triggered` 标记也会丢失。

---

### 3. 多人 PvP 模式中 AI 回合在玩家操作后停止

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 1908-1911 |
| **严重度** | 🟠 高 |

**问题描述：**
`/action` 端点中，`_run_ai_turns` 的继续执行被 `if human_uid`（第 1909 行）条件阻挡。`human_uid` 仅在 PvE 模式（第 1266 行）中被设置。在 `multi_pvp` 模式下，`human_uid` 保持为 `None`（第 1247 行），导致条件永远为 `False`，AI 自动回合永远不会执行。

**故障场景：**
多人 PvP 对战：玩家控制 Y1 角色，X1 角色未绑定（AI 控制）。玩家提交操作后，`_end_turn` 前进到 X1。由于 `human_uid` 为 `None`，`_run_ai_turns` 从不触发。X1 的回合被静默跳过。**所有位于人类玩家回合之间的 AI 角色在初始战斗创建后都被跳过。**

---

### 4. 濒死角色在显示中不可见但被计为存活，造成幽灵战局

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` (行 984) / `battle_engine.py` (行 841) |
| **行号** | 984 / 841 |
| **严重度** | 🟠 高 |

**问题描述：**
`_build_global_list`（`battle_http_server.py` 第 984-986 行）过滤掉所有 `hp <= 0` 的角色。但 `_check_battle_end`（`battle_engine.py` 第 841-846 行）将 HP=0 的濒死角色计为"存活"。下游影响：`_initiative_list_text`、`_enemy_list_text`、`_resolve_global_index` 都调用 `_build_global_list`，濒死角色在显示中完全不可见，但战斗结束判断却认为他们还活着。

**故障场景：**
角色 HP 降到 0，通过 CON 豁免进入濒死状态。先攻表、敌方列表、全局编号解析都隐藏了这名角色。其他玩家看到"无存活角色"或"敌方已全部阵亡"。但 `_check_battle_end` 仍将其计为存活，保持 `phase='active'`——战斗陷入无法结束的幽灵状态。

---

### 5. AI 攻击人类防守时双重扣除行动值

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 913, 2017 |
| **严重度** | 🟠 高 |

**问题描述：**
当 AI 攻击人类防守者并触发反应（ReactionNeeded）时，攻击者的 `主动` 行动被**扣除两次**：
1. `_run_ai_turns` 第 913 行：在捕获 ReactionNeeded 后扣除 1 次
2. `submit_reaction` 第 2017 行：在人类提交反应选择后再次扣除 1 次

**故障场景：**
AI 角色有 2 个主动行动，攻击人类玩家。`_run_ai_turns` 扣除主动到 1（第 913 行），存储待处理反应，然后 return。人类选择闪避。`submit_reaction` 再次扣除主动到 0（第 2017 行）。AI 的第二个行动完全丢失。

---

### 6. 多个目标选择路径缺少 `_is_untargetable` 过滤

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 1085-1109, 1681-1683 |
| **严重度** | 🟠 高 |

**问题描述：**
以下路径缺少 `_is_untargetable()` 检查，允许玩家攻击受 `不可指定` 保护的角色：
- `_resolve_target_by_serial()`（第 1085-1109 行）：按序列号/名称匹配目标，未过滤不可指定
- `submit_action` 的 `.s0` 路径（第 1681-1683 行）：敌方列表不包含 `not engine._is_untargetable(e['userId'])`
- `.sN` 技能路径（第 1773-1779 行）：通过 `_resolve_target_by_serial` 解析目标，同样缺少过滤

对比：AI 路径（第 892 行）正确包含了 `and not engine._is_untargetable(e['userId'])`。

**故障场景：**
玩家使用 `.s0 Y4` 攻击 Y4，Y4 有 `不可指定=1` 和一个活着的非三合一召唤物。`_resolve_target_by_serial` 返回 Y4 的 uid 不经过滤。攻击对应该免疫的角色执行。

---

### 7. PvP 反应处理从不传递 `source_dmg`，破坏同归于尽裁决

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 415-643 |
| **严重度** | 🟠 高 |

**问题描述：**
`resolve_reaction`（PvP 反应解决路径，复刻了父级的 `_coc7_attack`）在三处伤害应用点（第 531、574、643 行）都**未传递 `source_dmg`** 给 `_set_combat_hp`。而父级方法 `_coc7_attack`（`battle_engine.py` 第 2010 行）正确地传递了该参数。

这意味着所有 PvP 反应致死都注册为 0 溢出伤害。`_check_battle_end`（第 862-874 行）的溢出比较裁决机制完全失效。

**故障场景：**
两个角色在 PvP 中互相击杀（等级平局 → 互相命中，第 484 行）。双方都有实际的溢出伤害，但 `_set_combat_hp` 被调用时没有 `source_dmg`。`_last_death_overflow` 为空或两方都为 0 → 裁决回退到"Y 方获胜"默认值，结果可能完全错误。

---

### 8. AI 的 `.a eat`/`.a give` 命令被静默丢弃

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 948-951 |
| **严重度** | 🟠 高 |

**问题描述：**
`_run_ai_turns` 的命令分发链（第 878 行起）只处理 `.i end` 和 `.sN` 命令。当 Q-table 推荐 `EAT_CAKE` 或 `GIVE_CAKE` 动作时，`_ai_decide_action` 返回 `.a eat` 或 `.a give`，但没有任何处理器匹配。命令落入最终的 `else` 分支，仅调用 `_end_turn(uid)` 浪费整个回合。

**故障场景：**
Q-table 为低血量 AI 角色推荐吃蛋糕。`_ai_decide_action` 返回 `.a eat`。分发链既不匹配 `.i end` 也不匹配 `.sN`，落入 `else` → `engine._end_turn(uid)`。AI 既不治疗也不攻击，浪费整个回合。

---

### 9. `get_clist` 使用过滤后的索引访问未过滤的先攻表导致错位

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 2264, 2278 |
| **严重度** | 🟠 高 |

**问题描述：**
`get_clist` 使用 `_build_global_list` 返回的重编号过滤索引（`e['index']`）来索引原始的**未过滤**先攻表（`il[e['index']-1]`）。当有角色阵亡被过滤掉后，重编号的索引会错位。

**故障场景：**
先攻表：`[Alice(Y,存活), Bob(X,已阵亡), Charlie(Y,存活)]`。`_build_global_list` 返回：条目 index=1（Alice），index=2（Charlie）。对于 Charlie，`e['index']=2`，`il[1]` 返回 **Bob 的已阵亡条目** → `base_uid` 得到 Bob 的 userId 而非 Charlie 的 → `controller_ids` 和 `is_active` 引用了错误的角色。

---

## 🟡 中等严重度

### 10. `_get_active_buffs`/`_get_active_shields` 不解析多重行动 UID

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_engine.py` |
| **行号** | 559-572 |
| **严重度** | 🟡 中等 |

**问题描述：**
`_get_active_buffs()`（第 561 行）、`_get_active_shields()`（第 565 行）和 `_absorb_damage_with_shield()`（第 572 行）直接用 `targetUserId==uid` 进行比较，没有调用 `_resolve_uid()`。而 `_get_combat_hp`、`_set_combat_hp`、`get_char` 和 `_is_dying` 都正确使用了 `_resolve_uid()`。

当角色有多个行动（如 `Y1__act0`、`Y1__act1`）时，对基础 UID（`Y1`）施加的 buff/shield 在使用多重行动条目 ID 查询时不可见。

**故障场景：**
`setup_battle` 在第 1870 行对基础 UID `Y1` 施加被动 buff。当 `Y1__act1` 行动时，`_coc7_attack` 调用 `_get_active_buffs('Y1__act1')`，比较 `targetUserId=='Y1__act1'`——存储在 `Y1` 下的 buff 不可见。角色在战斗中缺少被动效果。

---

### 11. `_apply_item_heal` 用 `or 10` 将濒死角色的 HP=0 强转为 10

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_engine.py` |
| **行号** | 1748 |
| **严重度** | 🟡 中等 |

**问题描述：**
`_apply_item_heal` 使用 `chp = self._get_combat_hp(target_id) or 10`。由于 Python 中 `0` 是 falsy 值，濒死角色的 HP=0 被 `or 10` 强制转换为 10。引擎已有 `_hp_safe()`（第 391 行）来正确处理此情况（区分 `None` 和 `0`），但这里未使用。

**故障场景：**
角色在濒死状态 HP=0，使用 `.a eat` 吃蛋糕。`_get_combat_hp` 返回 `0`。`0 or 10` 求值为 `10`。角色从错误的 10-HP 基线治疗：`min(10 + 治疗量, 最大HP)` 而非 `min(0 + 治疗量, 最大HP)`。获得最多 10 点额外有效 HP。

---

### 12. `pvp_bind()` 端点无锁竞态条件

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_http_server.py` |
| **行号** | 2312 |
| **严重度** | 🟡 中等 |

**问题描述：**
`pvp_bind()` 端点访问 `_battles` 和修改 `engine._player_controllers` 时**未获取 `_lock`**。而 `create_battle` 等其他端点正确使用了锁。

**故障场景：**
两个玩家同时调用 `/api/pvp/<id>/bind` 绑定同一角色。两个线程都读取 `_player_controllers` 为空 `{}`，都追加自己的 `player_id`。由于 dict-of-list 竞态条件，一个追加可能丢失。其中一名玩家**静默丢失绑定关系**。

---

### 13. `process_command` 只处理 `.hs`，`.unh` 返回 None

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_engine.py` (行 700) / `battle_http_server.py` (行 2131) |
| **行号** | 700 / 2131 |
| **严重度** | 🟡 中等 |

**问题描述：**
`process_command` 只实现了 `.hs`（变身）的处理逻辑，没有 `.unh`（解除变身）的处理。HTTP 端点 `/api/pvp/<id>/unh` 调用 `process_command('.unh')` 返回 `None`，没有错误提示。

**故障场景：**
玩家通过 HTTP 端点调用 `.unh` 尝试恢复变身前的属性值。`process_command` 返回 `None`。`str(None) = "None"` 被返回为输出，没有错误提示。角色的 HP/MP/SAN **永久保持变身加成后的数值**。

---

## 🟢 低严重度 / 清理项

### 14. `_get_targets_display` 中存在不可达的死代码

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_engine.py` |
| **行号** | 981-985 |
| **严重度** | 🟢 低 |

**问题描述：**
`_get_targets_display()` 在第 980 行有 `return '\n'.join(lines)`。第 981-985 行（重新获取先攻列表、搜索非不可指定敌人）在无条件的 `return` 之后——永远无法执行。

**故障场景：**
死代码误导维护者，可能编辑无效代码。也暗示重构不完整——旧版 helper 应被删除但遗留在此。

---

### 15. `roll_dice` 中裸 `except: pass` 吞没系统信号

| 字段 | 内容 |
|------|------|
| **文件** | `autocombat/battle_engine.py` |
| **行号** | 127 |
| **严重度** | 🟢 低 |

**问题描述：**
`roll_dice` 函数中 `except: pass` 裸异常处理不仅捕获预期的 `ValueError`（来自 `int(clean)`），还会吞没 `KeyboardInterrupt` 和 `SystemExit`。

**故障场景：**
用户在 `int(clean)` 执行期间发送 SIGINT (Ctrl+C) 或 Python 运行时抛出 KeyboardInterrupt。信号被静默吞没，服务器持续运行。共享线程锁可能保持在已获取状态。

**修复建议：** 改为 `except (ValueError, TypeError): pass`。

---

## 🔵 附加发现（Angle B — 已删除行为审计）

### 16. `expand_short_alias` 正则表达式将 `file-N` 正向索引错误解析为反向索引

| 字段 | 内容 |
|------|------|
| **文件** | `logutil/backend.py` (行 436) / `logutil/frontend.js` (行 104) |
| **严重度** | 🟠 高 — 静默返回错误数据 |

**问题描述：**
v5.0.0 中，短别名展开的正则表达式从专用的 `(file|link|history)-(\d+)` 改为通用的 `^([A-Za-z]+)(-?\d+)`。新正则在向后兼容处理器之前匹配到 `file-14`（SealDice 去括号后的格式）。

**Python 代码追踪：**
1. 第 436 行：`s = "file-14"` 匹配 `^([A-Za-z]+)(-?\d+)` → 组1="file"，组2="-14"
2. 第 448 行：`cat_name = "file"`（"file" 在 `CATEGORY_META` 中）
3. 第 449 行：`num_str = "-14"` 以 `-` 开头 → True
4. 第 451 行：返回 `[file]~14`（**反向**索引，从最新开始数第 14 个）
5. 第 454 行的向后兼容 handler `([a-zA-Z_][a-zA-Z0-9_]*)-(\d+)$` **永远不会被到达**

**故障场景：**
SealDice 用户发送 `file-14`（去括号后，意为"第 14 号文件"）。旧代码匹配 `^(file|link|history)-(\d+)$` 返回 `[file]-14`（正向，从最旧开始数第 14 个）。新代码返回 `[file]~14`（**反向**，从最新开始数第 14 个），**静默返回了错误的文件**。

**JS 端同理：** 第 104 行 `^([A-Za-z]{2,})(-?\d+)` 同样匹配 `file-14`，返回 `[file]~14`。

---

## 📊 汇总

| 严重度 | 数量 | 关键问题 |
|--------|------|---------|
| 🔴 安全 | 1 | Flask debug 模式 RCE |
| 🟠 高 | 8 | 效果快照过期、多人 PvP AI 停滞、濒死幽灵战局、双重扣除行动、不可指定绕过、PvP 裁决破坏、AI 道具丢弃、get_clist 索引错位 |
| 🟡 中等 | 4 | 多重行动 UID 解析缺口、HP=0→10 强制转换、绑定竞态条件、.unh 无功能 |
| 🟢 低 | 2 | 死代码、裸异常吞没信号 |
| 🔵 回归 | 1 | `file-N` 正向索引被错误解析为反向索引 |
