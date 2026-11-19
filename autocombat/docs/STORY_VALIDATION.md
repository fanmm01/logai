# 剧情框架验证记录

首次验收：2026-11-16；响应式与交互修订验收：2026-11-17。实现包含请求文件及后续修订：本工作区演示目录、中英文逗号与最多五人立绘、多 START／CONTINUE 入口、UNLOCK／LOCK、前置 `__LOCK` 默认锁定。

## 自动验证

| 检查 | 结果 |
|---|---|
| `python scripts/test_story.py --coverage` | 新增后端 57 项通过 |
| 新增后端模块覆盖率 | 行 97.13%，分支 80.76%；检查入口强制要求至少 85%／80% |
| `npm --prefix frontend test` | 既有 Node 测试 54 项与新增 Vitest 测试 12 项通过，共 66 项 |
| 前端核心模块覆盖率 | 桥接、图模型、请求及资源引用：行 100%，分支 95.34%；React 界面另由浏览器验收 |
| `npm --prefix webgal-adv/engine run test:parser` | 上游解析测试 35 项通过 |
| `npm --prefix frontend run build` | 主应用及独立 WebGAL 均通过类型检查和生产构建 |
| `python scripts/merge_backend.py --check` | 通过 |
| `git diff --check` | 通过 |
| 发布包暂存目录 smoke test | 页面、三章、场景入口、播放器、字体及合并后端 Character 状态编码通过 |
| 发布 ZIP | CRC 校验及逐文件 SHA256 清单通过；附独立 `.zip.sha256` |

覆盖率不计未经修改的上游引擎；新增后端覆盖全部 story 模块和 ui_story，前端覆盖纯逻辑核心，界面使用真实浏览器验证。

## 浏览器验收

使用独立运行目录和端口 8895，未重启既有 8889／8890 实例。

1. `dev/frontend-tests/browser/story.cjs`：章节列表、第三章锁定、真实 WebGAL 舞台、五人立绘、初始锁定后的解锁、战前框、原有战术室、战斗存读档、手机宽度章节页面。
2. `dev/frontend-tests/browser/story-editor.cjs`：打开临时创作目录、图节点回写源码、Ctrl+S、新建文件、发布、独立预览。临时项目不会改写正式演示源文件。
3. `dev/frontend-tests/browser/story-continuation.cjs`：第二章真实两波 AI 战斗、暂停和单步、接续提示、进入第三章并验证 signal=2，使用接续入口而非 START。

4. `dev/frontend-tests/browser/story-responsive.cjs`：430×900 竖屏源码编辑器、折叠文件抽屉、并排单击和流程图双击打开 txt、切段点击响应、844×390 小横屏、自适应阅读设置与历史面板、浏览器全屏下选择和设置兼容、选项期间修改字号的持久化。

截图输出到 `.tmpwork/story-browser-artifacts/`。新增响应式截图在 `.tmpwork/story-responsive-artifacts/`，阵容弹层另有竖屏和小横屏截图。测试可设置 `UI_BASE_URL`、`UI_BROWSER_CHANNEL` 和 `UI_TEST_ARTIFACTS`。

另有独立进程恢复测试：在待处理防御反应处保存，用新的 Python 解释器读取同一隔离数据库；恢复房间暂停且反应仍在。再次作相同选择，得到相同的 HP／MP 结果，未重新掷已完成的骰。

## 完整回归与既有失败

2026-11-16 的 `python scripts/run_tests.py -q`：504 项，500 通过，4 项失败。新增剧情测试全部通过。

| 既有测试 | 当前差异 |
|---|---|
| `test_five_balance.FiveBalanceTests.test_attributes_and_unselected_characters_unchanged` | Y2 现有数据与早期基线不同 |
| `test_skill_mp_minimum.SkillMpTests.test_summon_skill_payment_and_ai_filtering` | 原测试期望无召唤动作，当前有 8 个动作 |
| `test_y12_summons.SummonRulesTests.test_y12_02_turtle_hp_and_block_pool_are_independent` | 原测试期望 HP 120，当前为 101 |
| `test_character_encyclopedia.Y5Y6StaticEncyclopediaTests.test_static_costs_targets_and_attack_values_match_live_rules` | 原测试期望百科列出全局射程覆盖说明，当前文章已不包含该条 |

前三项在既有 `REORGANIZATION_VALIDATION.md` 有记录。第四项已单独运行复现，相关百科实现、数据及测试文件本次均无修改。没有为使回归变绿而修改这些既有规则或预期。

## 运行和边界

从首页或侧栏进入“剧情模式”。默认创作项目位于 `story-projects/demo/`；开发阶段创作和执行目标对已建立会话的访问者开放。

完整存档覆盖剧情与 AUTOCOMBAT 战斗；外部 HTTP／程序在重启时标为中断，需明确重新执行。Live2D 运行代码保留，但 Cubism SDK 不随包提供。浏览器不提供 Steam 桌面宿主能力。详见 `STORY_RUNTIME.md` 与 `STORY_WEBGAL_COMPATIBILITY.md`。

本次发布候选输出在 `.tmpwork/story-release/`，未覆盖原有 releases 目录的候选包。

## 2026-11-17 修订

剧情舞台默认铺满视口，竖屏保持方向，不再放进固定比例外框。全屏目标包含 iframe、剧情选择和阵容弹层，确认阵容在舞台上方滚动显示。字体和间距已缩小；移除底部控制条，存读档、历史、暂停和设置统一收进右上角菜单。

手机创作室隐藏应用侧栏，文件树默认折叠；源码编辑器占据剩余视口。并排时单击文本节点会打开对应文件并保留流程图，其他模式双击打开。流程图从隐藏状态显示或屏幕尺寸明显变化时重新适配。

宿主直接解析已返回的场景脚本，存档列表只在初始化读取；切段期间保留一次输入，重复边界事件不会再次执行。流程变量只从当前场景快照同步，旧快照不能覆盖后续赋值。阅读设置可在剧情选择界面单独持久化。后两项均有独立后端回归测试。

本轮后端 54 项、前端 66 项、上游解析 35 项通过，四条真实浏览器流程通过，主应用与 WebGAL 生产构建通过。既有完整 Python 回归的四项失败仍保留上次记录，本轮未重跑完整套件。

## 接续入口重复修复（2026-11-17）

重复完成同一章节的同一 CONTINUE 标签时，替换该来源的接续状态，避免后续章节重复出现同名入口。读取历史档案时按来源章节与标签合并重复记录，选择完成时间最新者，同时间取后写入者。不同来源、不同标签和 START 仍分别显示。

新增三个回归测试：旧档案重复记录及时间排序、不同来源和标签保持独立、重复完成后只保留一个入口并继承最新变量与舞台状态，同时确保已开始的运行状态不被更新覆盖。后端全套剧情测试 57 项通过，行覆盖率 97.13%、分支覆盖率 80.76%。本次仅修改后端与说明文档，无需重新构建前端。
