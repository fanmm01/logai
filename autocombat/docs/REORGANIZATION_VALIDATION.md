# 目录整理验证记录 · 2026-11-16

目录整理保留了进入任务时的未提交修改。角色数据、训练权重、战斗引擎和插件逻辑与备份内容一致；运行模块仅调整资源和运行数据路径。全部备份文件中，除持续变化的运行数据外，迁移后未发现缺失文件。

## 已执行验证

| 验证 | 结果 |
| --- | --- |
| 项目外完整备份与 ZIP CRC | 通过；约 2.11 GB，排除可重建依赖与缓存 |
| Python unittest 完整回归 | 452 项，449 通过、3 项失败，见下表 |
| 前端 Node 单元测试 | 52 项通过；整理前同样为 52 项通过 |
| SealDice 插件纯函数回归 | 48 项通过 |
| TypeScript 检查与 Vite 生产构建 | 通过；存在超过 500 kB 的分包体积提示 |
| `scripts/merge_backend.py --check` | 通过 |
| 发布包暂存目录 API、页面与中文字体 | 通过 |
| 发布 ZIP 解压后独立启动 | 通过；从其他工作目录运行 `start_ui.py`，无需 Node 构建 |
| 发布包 HTTP 页面、角色目录 API 与 TCP 转发 | 通过 |
| 发布 ZIP CRC 与逐文件 SHA256 清单 | 通过 |

完整 Python 日志存于源码工作区 `archive/validation/reorganization-python.log`。发布包不包含这些开发日志。

## 整理前已存在的失败

三项均使用项目外备份还原的原始源码和测试单独复验，重现同样的断言失败；没有通过修改角色数值、战斗行为或测试预期来掩盖失败。

| 测试 | 当前失败 | 整理前复验 |
| --- | --- | --- |
| `test_five_balance.FiveBalanceTests.test_attributes_and_unselected_characters_unchanged` | Y2 当前卡片与早期平衡基线不同，包含伤害 `1d8+2` 与历史 `1d8` 等差异 | 同样失败 |
| `test_skill_mp_minimum.SkillMpTests.test_summon_skill_payment_and_ai_filtering` | 测试预期召唤物 MP 耗尽后没有动作，当前返回 8 个动作 | 同样失败 |
| `test_y12_summons.SummonRulesTests.test_y12_02_turtle_hp_and_block_pool_are_independent` | 测试预期 HP 120，当前数据为 101 | 同样失败 |

复验日志存于源码工作区 `archive/validation/original-baseline.log` 与 `archive/validation/original-y12-baseline.log`。
因此当前构建可启动、可打包分发，但完整回归未全绿。发布稳定版本前，应单独确认这三处当前规则与测试基线的预期。

本次未上传或公开发布，也未重启用户已有服务。项目当前未附统一许可证，资源分发授权需由维护者确认。
