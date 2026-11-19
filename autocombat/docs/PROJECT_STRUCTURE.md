# 目录与发布流程

剧情源码位于 `backend/story_*.py`、`backend/ui_story.py` 与 `frontend/src/story/`；引入的 WebGAL 固定源码位于 `webgal-adv/engine/`。默认创作示例位于本工作区 `story-projects/demo/`。运行、发布版本、战斗检查点和玩家权限保存在既有运行目录的 SQLite 与 `story/` 下。

新增验证：`python scripts/test_story.py --coverage`、`npm --prefix frontend run test:story:coverage`。完整构建同时生成主界面和 `frontend/dist/story-engine/`。发布包携带示例项目和修改后的引擎源码归档，无需 Node 即可运行。

```text
autocombat/
├── start_ui.py               # 源码和发布包的统一启动入口
├── restart_services.bat      # Windows 双实例重启入口
├── VERSION                  # 发布包版本
├── requirements.txt         # 运行依赖
├── backend/                 # 引擎、HTTP/UI、角色数据、权重、字体
├── frontend/                # React/TypeScript 源码与构建配置
├── plugins/                 # SealDice JS 插件
├── scripts/                 # 构建、重启、验证和分析工具
├── dev/
│   ├── tests/               # Python 与插件回归测试
│   └── frontend-tests/      # 前端单测、浏览器测试和批准夹具
├── docs/                    # 规则、审阅记录、目录和发布文档
├── archive/                 # 历史参考、旧备份、实验工具与验证产物
├── .ui-runtime/             # 本地运行数据，Git 忽略
└── releases/                # 发布 ZIP 与校验文件，Git 忽略
```

测试集中归档到 `dev/` 并继续由 Git 管理，发布包不包含测试。
`archive/` 保留既有资料，不参与程序导入和发布打包；旧实验脚本保留原状，不能假定可以直接运行。
既有运行数据继续使用根目录 `.ui-runtime/`，不搬动数据库。`backend/` 内的角色数据与权重仍可由管理页编辑。
根目录原有 `ai_battle.log` 已归档；`battle_debug.log` 仍被现有进程占用，已备份并暂留原位。新生成日志由 Git 忽略。

## 开发与验证

```powershell
python -m pip install -r requirements-dev.txt
python start_ui.py
# 已构建时，跳过构建直接运行：
python start_ui.py --skip-build

python scripts/merge_backend.py
python scripts/merge_backend.py --check
python scripts/run_tests.py
node dev/tests/js_rules_check.js
npm --prefix frontend test
npm --prefix frontend run build
```

Python 测试入口会为测试创建独立的运行目录，不读取或写入用户的 `.ui-runtime/`。
支持传入 unittest 参数，例如 `python scripts/run_tests.py tests.test_backend_contract -v`。
浏览器验证使用 `npm --prefix frontend run test:browser`，需要先启动测试服务，建议使用独立端口和运行目录。
可设置 `AUTOCOMBAT_RUNTIME_DIR` 指向独立运行数据目录；显式指定根目录的开发测试仍沿用临时目录。

## 构建发布包

```powershell
python scripts/build_release.py
# 已验证的前端构建可复用：
python scripts/build_release.py --skip-frontend-build
```

默认先构建前端，再从源码在临时目录生成整合后端，校验语法，并在包内执行 API、页面和字体加载检查。
输出 `releases/autocombat-<VERSION>.zip` 和对应 `.zip.sha256`，ZIP 内含逐文件 SHA256 清单。
打包使用运行文件白名单，排除 `dev/`、`archive/`、`.ui-runtime/`、依赖缓存、源码构建缓存和日志。
发布包携带前端静态文件和 Python 源模块，运行时无需 Node.js。生成后端和 ZIP 均不再作为手工维护源码。

当前尚无统一项目许可证；对外发布前需确认代码、角色设定及字体的分发授权。构建完成表示技术打包通过，尚未上传或公开发布。

本次验证结果及整理前已存在的三项失败见 [验证记录](REORGANIZATION_VALIDATION.md)。当前 ZIP 是可分发的候选构建，不代表完整回归全部通过。

已有服务若仍使用旧入口，可用根目录 `restart_services.bat` 切换到新路径；脚本兼容本项目原来的绝对入口路径。重启会清空普通内存对局。

## 本次整理的备份

原工作区备份：`D:\Datastruct\logai-backups\autocombat-20261116-074234/workspace.zip`。
包含当时的源码、测试、未提交修改、未跟踪文件、历史资料和运行数据；排除可重建的 `node_modules/`、`frontend/dist/` 和 Python/pytest 缓存。
同目录另存文件清单、Git 工作区差异、暂存区差异、HEAD 和索引快照。压缩包已通过 CRC 校验。
文件迁移清单见 [reorganization-20261116.json](../archive/reorganization-20261116.json)。
