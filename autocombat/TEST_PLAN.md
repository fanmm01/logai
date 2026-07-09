# autocombat v1.3.0 全功能测试计划

> 按顺序执行，每步标注预期结果。`[PASS]` = 通过，`[FAIL]` = 需修复。

---

## 一、环境准备

### 1.1 启动多文件版后端
```bash
cd d:\Datastruct\logai\autocombat
python battle_http_server.py
```
**预期**：输出 `Starting autocombat PvP battle server on http://0.0.0.0:8889`，端口 8889 无报错。

### 1.2 验证健康检查
```bash
curl http://127.0.0.1:8889/api/pvp/ping
```
**预期**：返回 JSON `{"ok": true}` 或类似成功响应。

### 1.3 停止多文件版 → 启动单文件版
```bash
# Ctrl+C 停止 battle_http_server.py
python autocombat_backend.py
```
**预期**：同样输出启动信息，端口 8889。

### 1.4 验证单文件版端点
```bash
curl http://127.0.0.1:8889/api/pvp/ping
curl -X POST http://127.0.0.1:8889/api/reload_chardata
```
**预期**：`ping` 返回成功，`reload_chardata` 返回角色数。

---

## 二、setab2 战斗核心流程

以下测试在 **SealDice 群聊** 中执行，后端任选一个运行。

### 2.1 启用规则
```
.set autocombat
```
**预期**：提示"已启用魔法少女半自动战斗规则 (autocombat v1.3.0)"

### 2.2 切换 setab2 模式
```
.setab 2
```
**预期**：提示"已切换至 Python后端模式"

### 2.3 创建队伍
```
.bteam new T1 Y1 Y2
.bteam new T2 Y3 Y4
.bteam list
```
**预期**：`list` 显示 T1(Y1,Y2) 和 T2(Y3,Y4)

### 2.4 绑定角色
```
.as Y1
.as Y2
```
**预期**：提示"已绑定【Y1】炎焱"和"已绑定【Y2】"

### 2.5 开始 PvP 战斗
```
.btast Y1 Y2 vs Y3 Y4
```
**预期**：显示 Y队/Y队阵容，连接后端成功，显示"玩家绑定"信息。

### 2.6 查看信息
```
.alist
.clist
```
**预期**：`alist` 显示当前可用操作，`clist` 显示战斗中人物。

### 2.7 执行攻击
```
.bta
```
**预期**：显示攻击检定结果（掷骰、成功等级、伤害）。

### 2.8 使用技能
```
.s1
```
**预期**：使用第1个魔法技能，显示效果。

### 2.9 附加动作
```
.a eat
```
**预期**：食用蛋糕，恢复HP/MP。

### 2.10 结束回合
```
.i end
```
**预期**：回合结束，推进至下一角色。

### 2.11 结束战斗
```
.btaend
```
**预期**：战斗结束，HP同步回卡片。

### 2.12 清理队伍
```
.bteam delete T1
.bteam delete T2
```
**预期**：删除成功。

---

## 三、代骰（代理操控）测试

需要另一个玩家的账号协助，或使用海豹的测试环境。

### 3.1 开始战斗
```
.as Y1
.btast Y1 Y2 vs Y3 Y4
```

### 3.2 代理攻击
```
.bta @另一玩家
```
**预期**：以后端记录的 `@另一玩家` 的绑定角色进行攻击（而非当前玩家）。

### 3.3 代理反应
```
.e d @另一玩家
```
**预期**：以代理玩家的角色闪避。

### 3.4 代理技能
```
.s2 @另一玩家
```
**预期**：以代理玩家角色使用技能2。

### 3.5 代理结束回合
```
.i end @另一玩家
```
**预期**：以代理玩家角色结束回合。

### 3.6 代理绑定
```
.as Y3 @另一玩家
```
**预期**：为另一玩家绑定Y3。

### 3.7 代理信息
```
.alist @另一玩家
```
**预期**：显示另一玩家角色的可用操作表。

### 3.8 代理解绑
```
.unbind Y3 @另一玩家
```
**预期**：解除另一玩家的Y3绑定。

### 3.9 清理
```
.btaend
```

---

## 四、.train 训练测试

### 4.1 启动训练
```
.train
```
**预期**：提示"训练已启动 (xxxxxxxx)，预计需数分钟..."

### 4.2 查询训练状态（后端）
```bash
curl http://127.0.0.1:8889/api/train/<job_id>/status
```
**预期**：返回 `{"status": "running", ...}`

### 4.3 等待完成
- 等待约 2-5 分钟（取决于机器性能）
- 聊天中应收到"训练完成！Q表已自动重载。"

### 4.4 验证权重文件更新
```bash
dir d:\Datastruct\logai\autocombat\ai_weights_pvp.json
```
**预期**：文件修改时间为当前时间。

---

## 五、.jour 锦标赛测试

### 5.1 仅 1v1 模式（最快）
```
.jour 1
```
**预期**：提示"锦标赛已启动 (1v1)"，然后陆续收到图片/文字输出。

### 5.2 输出验证
按顺序检查收到的消息：
1. **积分表图**（1v1 排名）—— 含角色名、积分、胜场
2. **签表图**（淘汰赛对阵图）
3. **淘汰赛赛果图**（每轮淘汰赛结果，含比分和回合数）
4. **SF+ 详程图**（半决赛起的详细回合日志）
5. **最终结果图**（冠亚季军 + 角色综合胜率）

### 5.3 pictmode=0 文本模式测试
```
.setab 2
```
修改配置将 `pictmode` 设为 0（或在 UI 中切换），然后：
```
.jour 1
```
**预期**：全部输出以文字形式发送（非图片）。

### 5.4 恢复 pictmode=1
```
# 在 UI 中将 pictmode 改回 1
```

---

## 六、.jour 停止测试

### 6.1 启动长时间锦标赛
```
.jour
```
（全部三种赛制，耗时较长）

### 6.2 立即停止
```
.jour stop
```
**预期**：提示"锦标赛已停止"。

---

## 七、.sim 战斗模拟测试

### 7.1 单场模式
```
.sim Y5 vs Y1 -n 10 -s 12345
```
**预期**：输出 `Y5 v.s. Y1 — 10场`，显示双方胜率统计和平均回合数。

### 7.2 多角色对战
```
.sim Y5+Y6 vs Y1+Y7 -n 20
```
**预期**：显示 2v2 胜率统计。

### 7.3 详细回放（verbose）
```
.sim Y5 vs Y1 verbose -s 12345
```
**预期**：输出单场详细回放，含每回合掷骰/技能/伤害/最终结果。

### 7.4 赛季状态参数
```
.sim Y5 vs Y1 -n 10 season 90
```
**预期**：输出含"赛季状态"信息。

### 7.5 胜率矩阵 — 1v1
```
.sim mode table format 1v1 -n 5
```
**预期**：输出 N×N ASCII 表格，含 CSV/JSON 文件路径，平均积分排名，团队贡献率。

### 7.6 胜率矩阵 — 2v2
```
.sim mode table format 2v2 teams "Y5+Y6,Y7+Y8,Y1+Y2" -n 5
```
**预期**：同上格式。

### 7.7 语法错误处理
```
.sim
.sim Y5
.sim Y5 vs
```
**预期**：三种情况均显示用法提示，不崩溃。

---

## 八、其他端点验证

### 8.1 重载角色数据
```bash
curl -X POST http://127.0.0.1:8889/api/reload_chardata
```
**预期**：返回角色数和召唤物模板数。

### 8.2 渲染端点
```bash
curl -X POST http://127.0.0.1:8889/api/render \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"测试文本\\n第二行\"}"
```
**预期**：若 PIL 可用，返回 `{"images": ["base64..."]}`；否则返回 `{"error": true, ...}`。

### 8.3 队伍 CRUD
```bash
curl -X POST http://127.0.0.1:8889/api/pvp/team/create \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"test\",\"members\":[\"Y1\",\"Y2\"]}"

curl http://127.0.0.1:8889/api/pvp/team/list

curl -X POST http://127.0.0.1:8889/api/pvp/team/delete \
  -H "Content-Type: application/json" \
  -d "{\"team_id\":\"T1\"}"
```
**预期**：创建、列出、删除均返回成功。

---

## 九、边界/错误处理

### 9.1 无效战斗ID
```bash
curl -X POST http://127.0.0.1:8889/api/pvp/nonexistent/action \
  -H "Content-Type: application/json" \
  -d "{}"
```
**预期**：返回 404 或 `{"error": true}`。

### 9.2 缺少参数
```
.as
.unbind
```
**预期**：提示用法，不崩溃。

### 9.3 无活跃战斗时操作
```
.bta 斗殴
.e d
.i end
```
（在未开始战斗时执行）
**预期**：提示"不在setab2战斗中"或类似信息。

---

## 十、单文件版独立部署验证

### 10.1 复制到临时目录测试
```bash
mkdir d:\tmp_test
copy d:\Datastruct\logai\autocombat\autocombat_backend.py d:\tmp_test\
cd d:\tmp_test
python autocombat_backend.py --port 8890
```
**预期**：独立运行成功，无 import 错误。

### 10.2 验证核心端点
```bash
curl http://127.0.0.1:8890/api/pvp/ping
curl -X POST http://127.0.0.1:8890/api/sim \
  -H "Content-Type: application/json" \
  -d "{\"team_a\":[\"Y5\"],\"team_b\":[\"Y1\"],\"n\":5}"
```
**预期**：ping 成功，sim 返回模拟结果。

### 10.3 清理
```bash
# Ctrl+C 停止
rmdir /s d:\tmp_test
```

---

## 通过标准

- 所有标注 `[PASS]` 的步骤无异常
- 无 Python traceback 泄漏到聊天中
- 单文件版与多文件版行为一致
- 代骰命令正确使用代理玩家身份
- `.jour` 输出完整且格式正确
- `.sim` 各模式输出与直接 `python sim.py` 一致
