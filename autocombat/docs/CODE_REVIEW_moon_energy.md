# 月能系统崩溃代码审查与修复报告

日期：2026-10-28 · 审查对象：`autocombat/`（battle_engine / characters_data / ai_trainer / autocombat_backend）

---

## 1. 概要与结论

训练 `python ai_trainer.py` 时反复出现 `[WARN] char battle worker failed`：

```
AttributeError: 'FastBattleEngine' object has no attribute '_has_moon_buff'
  ai_trainer.execute_action (1046) → battle_engine._execute_spell (2677)
```

**根因**：重构提交 `d9e4216 (2026-07-30)` 把硬编码召唤物名的辅助函数改成数据驱动版本，但 `_execute_spell` 治疗溢出分支里的 2 处 `self._has_moon_buff(tid)` 调用点没有同步修改，且该方法在新版本中从未定义——训练、表演赛（`ai_battle.py`）、HTTP 后端（`autocombat_backend.py`）三条路径同时中招。

**修复后状态**：崩溃已修；月能系统（充能/补满/维持消耗/低血授予免疫）全链路跑通；骰子解析器支持 `/2` 等算术表达式；训练 1 代冒烟通过（`[3/3] char_battles=2`，无 WARN）。Y14/Y15/Y16 法术回归 50/50 PASS。

| 项目 | 状态 |
|---|---|
| P0 崩溃修复（`_has_moon_buff`） | ✅ 已修 |
| P1 月能系统全链路（模板查找 / linked_summon 落盘 / buff 持续回合 / hpt 去重 / 骰串成本） | ✅ 已修 |
| P2 骰子 `/` 支持、攻击技能选择统一 | ✅ 已修 |
| P3 `_find_enemy` 死引用、TODO 注释 | ✅ 已修 |
| 切换法术 timing `'123'` → `'23'` | ✅ 已改（用户决定，两个数据文件） |
| **第二轮** `suffix_map` 角色化（Alt A） | ✅ 已实施（2026-10-28） |
| **第二轮** 缺陷9：月光王座月能×5 + 销毁月亮 | ✅ 已实施（2026-10-28） |
| **第二轮** 缺陷9：`damage_table='moon_phase'` 8 月相表 | ✅ 已实施（2026-10-28） |

---

## 2. 崩溃根因（git 历史 + 运行时双重证据）

`d9e4216` 的改动（`git show d9e4216 -- autocombat/battle_engine.py`）：

| 旧函数（`d9e4216^` 存在） | 重构后 | 结果 |
|---|---|---|
| `_find_moon_for_team(team)` | `_find_summons_by_field(team, 'replenish_allies')` | 查找永远返回空（见缺陷 1） |
| `_has_moon_buff(uid)` | `_has_linked_buff(uid, template_name)` | 函数被删，但 `_execute_spell` 中 2 处调用点未改 |

`git rev-list --all` 全历史扫描确认：`def _has_moon_buff` 只存在于 `64ebfec` 及其之前，`d9e4216` 删除后**没有任何提交重新定义过它**。

调用点在 `CombatEngine._execute_spell`（`battle_engine.py:2677 / 2691`），`FastBattleEngine` / `FullBattleEngine` / `BattleEngine` 全部继承该方法，因此触发条件是**任意角色满血时被治疗（type=3 治疗溢出）**，与林白无关——任何带治疗技能的对局都可能崩。

### 修复

在 `CombatEngine` 补回数据驱动版本（不硬编码 `'虚假之月'`）：

```python
def _has_moon_buff(self, uid):
    """事象的馈赠: uid 是否持有与月能召唤物(replenish_allies)链接的 buff。"""
    for e in self._get_effects():
        if e.get('type') == 'buff' and e.get('targetUserId') == uid \
           and e.get('linked_summon') \
           and e.get('remainingRounds', 0) > 0:
            return True
    return False
```

---

## 3. 缺陷清单（同域，均有代码/运行时证据）

### 缺陷 1 — `_find_summons_by_field` 永远返回空【已修】
`_summon_members[sid]` 从不保存模板字段（三处构造只写 userId/name/display_name/team/coord/skills/dex/...），但 `_find_summons_by_field`、`_tick_down` 的补满/维持、`protect_allies_on_hit` 都从它读 `replenish_allies` / `maintenance_mp` 等字段。
实测：有存活的虚假之月时 `_charge_moon_energy('Y', 10)` 恒返回 `0` → 月能充能/补满/维持消耗全部是死代码。
**修法**：新增模块级 `_get_summon_template(name)`（`_SUMMON_TEMPLATES` 注册表 + 懒加载回退），三处调用点统一改为按 `entry['name']`（恒为模板名）解析模板字段。

### 缺陷 2 — buff 条目丢失 `linked_summon` / `hp_threshold_grant`【已修】
`_execute_spell` ct==4 分支只拷贝白名单字段 → `_has_linked_buff` 永远 False（`_tick_down` 的「召唤物死亡则 buff 过期 + 存活时续 999 回合」同样失效），`hp_threshold_grant`（辉月女神的祝福：5 月能换 AUX30）永远不触发。
**修法**：`buff_entry` 补拷贝两个字段。

### 缺陷 3 — `持续回合` 缺失时被写成 0【已修】
`load_spells` 用 `char.get_attr(...)` 取数（缺省 0），`if v is not None` 让 0 也写进效果字典，覆盖 `eff.get('持续回合', 1)` 默认值 → 虚假之月挂的事象的馈赠 buff 出生即 `remainingRounds=0`。
**修法**：`dur = eff.get('持续回合') or 1`；两个数据文件的 s2 linked buff 效果补 `'持续回合': 99`。

### 缺陷 4 — hp_threshold_grant 去重的自由变量 bug【已修】
`any(e.get('auxCode') == ... for e2 in [e for e in effects])` 中 `e` 是自由变量（绑定到 countdown 循环遗留的末元素），去重完全失效：末元素非 AUX30 时每 tick 重复授予并白扣月能。
**修法**：`for e2 in effects` 并使用 `e2`。另把 hpt 块移到 `replenish_allies` 之前——原顺序下补满先把 HP 拉回上限，`<50%` 条件永远不成立。

### 缺陷 5 — 骰串成本被静默归零【已修】
spell 级 `if v is None:` 使 `get_str` 回退永不生效（缺失键 get_attr 返回 0）；effect 级 `持续回合`/`吟唱回合` 不在 str 键列表。
影响：Y16 灵感大爆发 3d3 / 虚假之月 3d4 / 月中的倒影 2d3、Y17 血月斩击 1d8 的 MP 成本；Y9 阶段转换 2d6、Y17 污血战争 1d8 的 SAN 成本；灵感大爆发 持续回合 2d3 —— 全部静默归零。虚假之月初始 MP=0，直接堵死月能池。
**修法**：spell 级改 `if not v:`；effect 级对这两个键读 str 并 `roll_dice` 成 int。

### 缺陷 6 — `db/2` 语法不被解析器支持【已修】
`_resolve_db('1d6+db/2','0') → '1d6/2'`，旧 findall 分词器把 `/` 丢弃：`1d6/2` 被当成 `1d6+2`、`1d6-2/2` 被当成 `1d6`（实测 Y16 斗殴期望 5.50、Y17 斗殴 3.50 即 `+2` 假象）。
**修法**：`roll_dice` / `roll_dice_detailed` / `avg_damage` / `max_damage` 统一走 `_eval_dice_expr(expr, mode)`（roll/avg/max）：`NdM` 先展开为数值，再对受限字符集 `^[0-9+\-*/(). ]+$` 求值，`int()` 截断（对齐 JS `parseInt`）；非法表达式回退旧版分词求和。`_resolve_db` 的 db=0 分支删除正则改为 `[+-]?\s*\bdb\b(?:\s*/\s*\d+)?`。
行为变化（bugfix 本意）：Y16 弓术由假的 `1d6+2` 变为 `floor(1d6/2)`；Y17 弓术由 `1d6` 变为 `1d6-1`。

### 缺陷 7 — 训练路径与对局路径的武器感知普攻分叉【已修】
武器感知选择只写在 `FullBattleEngine._basic_attack`，训练走 `execute_action → _fast_coc7_attack`（`get_best_melee()`），威胁/状态编码/射程过滤同样。
**修法**：抽出 `CombatEngine._get_attack_skill(uid, char)`，五处统一复用（`_basic_attack`、`execute_action`、`get_available_actions` 射程、`_compute_threat`、`encode_state`/`get_character_combat_value`）。

### 缺陷 8 — `engine._find_enemy` 死引用【已修】
`ai_trainer.py:1026` 调用了从未在任何引擎类上定义的方法（与 `_has_moon_buff` 同类陈旧 API）。原分支只在无敌人时走到 → 必抛 AttributeError。
**修法**：`if not tid: return ""`。

### 缺陷 9 — 数据声明但引擎无实现【仅记录 + TODO 注释】
- `damage_table: 'moon_phase'`（Y17 血月斩击）：引擎只解析不消费（已加 TODO 注释）。
- 月光王座全额率输出：设计文档为「月能×5 动态伤害 + 消耗全部月能」，数据只是普通 `1d1`（pvp 为 `2d12`，两文件本身不一致）——已加 TODO 注释。

---

## 4. 复现命令与实测输出

### 复现 1：月能崩溃（修前 → 修后）

```bash
cd /d/Datastruct/logai/autocombat
PYTHONIOENCODING=utf-8 python -B - <<'EOF'
import battle_engine as be
from characters_data import ALL_CHARACTERS, load_character_to_engine, SUMMON_TEMPLATES
be._SUMMON_TEMPLATES = SUMMON_TEMPLATES
eng = be.FastBattleEngine()
linbai = next(c for c in ALL_CHARACTERS if c['serial']=='Y16')
y17    = next(c for c in ALL_CHARACTERS if c['serial']=='Y17')
load_character_to_engine(eng, linbai, 'Y16'); load_character_to_engine(eng, y17, 'Y17')
eng.process_command('Y16', '.hs'); eng.process_command('Y17', '.hs')
eng.setup_battle(['Y16','Y17'], ['X1'], '10x10')
eng.get_char('Y17').set_attr('魔力', 99)
sp = next(s for s in eng.load_spells('Y17') if s['name']=='凝血式-存')
eng._set_combat_hp('Y17', eng.get_char('Y17').get_attr('体力上限'))
r = eng._execute_spell('Y17', 'Y17', sp)   # 修前此处抛 AttributeError
print(r)
EOF
```

- **修前**：`RAISED: AttributeError : 'FastBattleEngine' object has no attribute '_has_moon_buff'`
- **修后**：`夜诏明 释放【凝血式-存】→ 自身 | 消耗 2 MP | 消耗 1 SAN | 回复 HP +1 (1)`（无月能链接 buff 时正确跳过充能）

### 复现 2：木落 Y9 伤害骰回归（仅记录，未改）

```bash
cd /d/Datastruct/logai/autocombat
PYTHONIOENCODING=utf-8 python -B -c "
import battle_engine as be, characters_data as cd
be._SUMMON_TEMPLATES = cd.SUMMON_TEMPLATES
eng = be.FastBattleEngine()
for c in cd.ALL_CHARACTERS:
    if c['serial'] in ('Y9','Y16','Y17'): cd.load_character_to_engine(eng, c, c['serial'])
for s in ('Y9','Y16','Y17'):
    print(s, 'best_melee=', eng.get_char(s).get_best_melee()[0],
          '| 斗殴=', repr(eng._get_damage_dice(s,'斗殴')),
          '| 剑=', repr(eng._get_damage_dice(s,'剑')),
          '| 弓术=', repr(eng._get_damage_dice(s,'弓术')))
"
```

实测输出（修前与修后一致，映射未动）：

```
Y9  best_melee= 斗殴 | 斗殴= '1d4' | 剑= '1d6+1d6' | 弓术= '1d8+1d6+4'
Y16 best_melee= 斗殴 | 斗殴= '1d6' | 剑= '1d8'    | 弓术= '1d6'
Y17 best_melee= 斗殴 | 斗殴= '1d6-2' | 剑= '1d6-2' | 弓术= '1d6-2/2'
```

> `Y9 斗殴 = '1d4'`：木落只声明了 `伤害值a/b`（`1d6+1d6` / `1d8+1d6+4`）而没有 `伤害值`。工作区未提交改动把 `suffix_map` 从 `{'剑':'a','斗殴':'b'}` 改成 `{'剑':'a','弓术':'b'}` 后，斗殴普攻落到兜底 `1d4`（HEAD 下是 `1d8+1d6+4`）。**按用户决定：本次不动代码。**

---

## 5. 木落伤害骰：备选修复对照表（Alt A 已按用户决定实施，见 §10.1）

| 方案 | 改法 | Y9 斗殴 | Y16 斗殴 | Y16 剑 | Y16 弓术 | Y17 斗殴 | 评价 |
|---|---|---|---|---|---|---|---|
| 现状 | `{'剑':'a','弓术':'b'}` | `1d4` ❌ | `1d6` | `1d8` | `1d6` | `1d6-2` | 木落回归 |
| HEAD | `{'剑':'a','斗殴':'b'}` | `1d8+1d6+4` | `1d6/2` | `1d8` | `1d6` | `1d6-2/2` | 弓术拿不到 b |
| Alt A（推荐） | 默认 HEAD 映射，角色级覆盖（Y16 声明 `{'剑':'a','弓术':'b'}`，str_attrs JSON 键） | `1d8+1d6+4` ✅ | `1d6` ✅ | `1d8` ✅ | `1d6/2` ✅ | `1d6-2/2` | 语义清晰，需改引擎+两数据文件+重新生成后端 |
| Alt B | 全局 `{'剑':'a','斗殴':'b','弓术':'b'}` | `1d8+1d6+4` ✅ | `1d6/2` ⚠️ | `1d8` | `1d6/2` | `1d6-2/2` | 一行改完，但 Y16 空手普攻套上弓的 `伤害值b` |
| Alt C | 映射不动，给 Y9 补 `'伤害值': '1d8+1d6+4'` | `1d8+1d6+4` ✅ | 不变 | 不变 | 不变 | 不变 | 侵入最小，把变体固化为静态值 |

> 深层原因：`README.md:318-319` 把 `伤害值a/b` 定义为「变身前后变体」，引擎却当「武器/技能后缀」用（JS `getWeaponSuffix` 按技能名尾字母取后缀）——同一组字段两套语义。选定方案前请知悉。

---

## 6. 仅记录项（不改代码，待确认）

1. **max_simultaneous / max_total_spawned 从不校验**：可召唤第二个虚假之月并隐身入组合并组。
2. ~~**月光王座未实现**~~：**已实施**（§10.2：月能×5 + 消耗全部月能 + 销毁月亮 + 无月禁止施放）。
3. **Y17 三条 `phase: 99` 法术**（猩红眼眸 / 凝血式-浮 / 等价交换）战斗内不可达：load_spells 按 phase 过滤，无入口设 phase 99（Y15 同款有「非战斗」注释而 Y17 没有）。
4. **文档 vs 数据**：虚假之月文档 HP50/MP99，数据 HP9999/MP0 + unlimited_mp。
5. **边缘项**：`_tick_down` linked 分支 source 不在先攻时 team 默认 'Y'（X 队误判）；`_charge_moon_energy` 只充 `moons[0]`；weapon_required 只在 load_spells 过滤，type-17 切换后 `char.spells` 缓存不失效（`battle_http_server.py:2215` 等缓存路径读不到切换后的终极技）；FastBattleEngine._create_summon 主分支缺 `无主动行动`/`unlimited_mp`（训练循环直接驱动召唤物，当前无功能影响）。
6. **数据漂移**：pvp 的 虚假之月 timing `'23'` vs 训练数据 `'2'`（pvp 开局会自动施放月亮）；月光王座 pvp `2d12` vs 训练 `1d1`。
7. **训练/对局武器可达性**（随 timing `'23'` 改动部分缓解）：切换法术已进入 AI 动作空间，`'切换·'` 乐观偏置恢复有效；但 `_weapon_name` 仍不在 `encode_state` 里，策略无法观测武器状态。

---

## 7. 验证记录（全部通过）

1. **崩溃修复**：满血治疗不再抛异常（见 §4）。
2. **月能链路**：`_find_summons_by_field('Y','replenish_allies')` 返回 `['Y16_sum_1']` → `_charge_moon_energy('Y',10)` 返回 10 → 月 MP 增长；虚假之月初始 MP = 掷出的 3d4（成本修复生效）。
3. **补满/维持**：`虚假之月·补满 → 林白: HP+8 MP+0 (剩余:0)` + `虚假之月 月能耗尽，消逝...`。
4. **低血授予免疫**：`hp_threshold_grant: 消耗5MP，林白 获得 AUX30！(剩余:84)`；下一 tick 去重正确跳过，不再扣月能。
5. **骰子语义**：`roll_dice('1d6/2')∈[0,3]`、`avg_damage('1d6/2')=1.75`、`avg_damage('2d6+4')=11.0`、`max_damage('1d6-10')=0`、`roll_dice_detailed('2d3+1d4')` 协议不变、`_resolve_db('1d6+db/2','0')='1d6'`。
6. **Y14/Y15/Y16 回归**：`python reference/test_spells_y14_y15_y16.py` → **Total 50, PASS 50, FAIL 0**。
7. **训练冒烟**：`GENERATIONS=1, BATTLES_PER_GEN=2`（真实脚本文件 + `__main__` 保护）→ `[3/3] char_battles=2 avg_reward~0.466`，无 `[WARN] ... worker failed`，196 Q-entries。
8. **后端一致性**：`python scripts/merge_backend.py` 重新生成 `autocombat_backend.py`；重新生成前已确认与工作区字节级一致（零 churn）；生成后后端内的月能链路同样验证通过。

---

## 8. 变更文件清单

| 文件 | 变更 |
|---|---|
| `autocombat/battle_engine.py` | `_get_summon_template`、`_has_moon_buff`、`_find_summons_by_field` 模板解析、`_tick_down`（hpt 前置+去重修复+模板查找）、`buff_entry` 补字段、`dur` 兜底、`load_spells` 骰串成本修复、`_eval_dice_expr` 四个骰子函数重写、`_resolve_db` db/2、`_get_attack_skill`、`_basic_attack`/`protect_allies_on_hit` 改用统一辅助 |
| `autocombat/characters_data.py` | s2 linked buff 补 `持续回合: 99`；切换×3 timing `'123'`→`'23'`；TODO 注释×2 |
| `autocombat/characters_data_pvp.py` | 同上 |
| `autocombat/ai_trainer.py` | `_get_attack_skill` 统一五处；`_find_enemy` 死引用移除 |
| `autocombat/autocombat_backend.py` | 由 `merge_backend.py` 重新生成 |

---

## 9. 待办（供后续决策）

- [x] ~~选定木落伤害骰修复方案~~ → **Alt A 已实施**（见 §10）。
- [x] ~~决定是否实现月光王座「月能×5」与 `damage_table` 月相表~~ → **已实施**（见 §10）。
- [ ] 确认 Y17 `phase: 99` 法术意图（§6-3）。
- [ ] 确认 `hp_to_blood_ml` 漂移：characters_data.py=200 vs characters_data_pvp.py=250（§10 新发现）。
- [ ] 修复后建议重训 `ai_weights.json`（治疗成本、骰子语义、切换动作空间、月相伤害均有变化）。

---

## 10. 第二轮记录（2026-10-28）— 缺陷9 实现 + suffix_map 角色化

用户决定：实现缺陷9（月光王座 + moon_phase 月相表）；suffix_map 按角色放进 chData（Alt A）；月光王座无月时禁止施放；Y16/Y17 均声明角色级映射。

### 10.1 suffix_map 角色化（Alt A）【已实施】

- 引擎 `_get_damage_dice`：默认映射恢复 HEAD 的 `{'剑':'a','斗殴':'b'}`；角色可在 `str_attrs` 里用 `damage_suffix_map`（dict 或 JSON 字符串）整体覆盖。
- 数据：Y16、Y17 均声明 `'damage_suffix_map': {'剑': 'a', '弓术': 'b'}`（两个 data 文件）。

实测矩阵（修复后）：

```
Y9  斗殴 = '1d8+1d6+4'   ← 木落回归修复 ✅（剑 '1d6+1d6' 不变）
Y16 斗殴 '1d6' / 剑 '1d8' / 弓术 '1d6'   ← 与改动前一致
Y17 斗殴 '1d6-2' / 剑 '1d6-2' / 弓术 '1d6-1'(roll)   ← 与改动前一致
```

### 10.2 缺陷9a：月光王座全额率输出【已实施】

规格（reference/autocombat-reference-md-flickering-eich.md:209,225）：消耗全部月能 → 伤害 = 月能×5 → 销毁虚假之月。

- 数据：s8c effect 增 `'damage_moon_mult': 5, 'consume_moon': True`；两文件伤害骰统一 `1d1`（pvp 原为 `2d12`）。
- 引擎：
  - `load_spells` 数值键白名单补 `damage_moon_mult` / `consume_moon`（否则字段不落盘——首轮实测 None 后修复）。
  - `_execute_spell` 射程检查后、MP 扣除前：无月亮或月能≤0 → `无虚假之月，无法释放！`（不扣 MP）；有月亮 → `spell['_moon_dmg'] = max(1, 月能×5)`，月能归零 + 销毁月亮（一次性，多效果/满值穿透不重复扣）。
  - `FullBattleEngine._handle_spell_damage_effect` 公式链：`damage_moon_mult` 分支读 `spell['_moon_dmg']`（继承自首轮验证的 `_actual_mp_cost` 模式）。

实测：29 月能 → `消耗全部月能 29 → 月光王座伤害 145！虚假之月消逝...`，月亮 HP=0、月能=0、目标 -139（含困难成功加骰）。

### 10.3 缺陷9b：damage_table='moon_phase' 8 月相表【已实施】

规格（reference/各魔法少女魔法列表.md:22，8 月相由斩击方向决定，引擎自动掷骰）：

| 月相 | 伤害 | 吸取 |
|---|---|---|
| 满月 | `1d12+3` | ✅ |
| 亏凸月 | `1d8` | ✅ |
| 弦月 | `1d12` | — |
| 残月 | `1d6+4` | — |
| 新月 | `1d16+12` | — |
| 蛾眉月 | 固定 8 | — |
| 上弦月 | 固定 5 | ✅ |
| 盈凸月 | 固定 10 | ✅ |

- 引擎：模块常量 `_MOON_PHASE_TABLE`；公式链 `damage_table == 'moon_phase'` 分支掷 1d8 选月相；吸取月相在结算后等量治疗施法者（上限 体力上限），输出附月相名。

实测：120 次采样 8 月相均匀分布（13~21 次）；吸血样本（满月 9HP / 亏凸月 1~8 / 上弦月 5 / 盈凸月 10）全部正确。

### 10.4 第二轮附带发现与处理

- **`hp_to_blood_ml` 数据漂移**（新发现）：characters_data.py=200 vs characters_data_pvp.py=250（"1 HP = 250ml blood" 注释支持 250）。`reference/test_spells_y17.py` 从 pvp 导入却断言 200 → 该用例长期失败（与本次改动无关）。**测试期望改为 250 并加注释**；数据本身未动，待用户确认以哪边为准。
- 验证记录（全过）：Y14/Y15/Y16 套件 50/50；Y17 套件 62/62；backend 重新生成后月光王座/Y9 修复在 backend 内同样验证通过；训练冒烟 `[3/3] char_battles=2` 无 WARN。

### 10.5 验证 agent 复核后的精化（2026-10-28）

后台 Plan agent 对落盘实现做了独立复核，结论：consume_moon 一次性结算的挂点正确（法术路径无多目标循环，贯穿只是满值加骰）；PvP 子类继承公式链，`_moon_dmg` 在 ReactionNeeded 抛出前已落盘，PvP 伤害值正确。按复核意见又做了三处精化：

1. **月相吸血统一进 `_apply_spell_damage`**（新 kwarg `moon_drain=0`，在 `eff_dmg > 0` 块内、与既有吸血并列）：
   - 修复：AI 路径原来在 handler 调用后治疗 → 会被 AUX30 免疫早退/闪避误触发；现在只有目标实际受伤才吸血。
   - 修复 PvP 缺口：人类玩家防御时 `ReactionNeeded` 提前返回，旧位置吸不到血；吸取量改存 `eff['_moon_drain']`（随 PvP 的 `effect` 往返存活），`resolve_spell_reaction` 的结算点传入。
   - 基类 `CombatEngine._handle_spell_damage_effect` 公式链同步补 `damage_moon_mult` / `moon_phase` 分支（消除 legacy 路径分叉）。
2. **修复 PvP 双结算旧 bug**：`.e` 反应路由在 `resolve_spell_reaction`（内部已结算伤害）之后又 `_apply_spell_damage` 一次 → 同一法术双倍伤害/吸血/DOT（且不检查 atk_rank，失败法术也会出伤）。已删除路由内的重复结算块。
3. 数据里过期的"尚未实现"TODO 注释已更新为实际机制说明。

复核后验证：Y14/Y15/Y16 套件 50/50、Y17 套件 62/62、backend 再生成 + 月相吸血/月光王座/Y9 全部通过、训练冒烟无 WARN。
