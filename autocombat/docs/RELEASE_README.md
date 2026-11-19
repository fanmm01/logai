# autocombat · 星序浏览器战术室

CoC 魔法少女半自动战斗系统，提供浏览器对战、角色图鉴、AI 模拟、训练和锦标赛，以及 SealDice 插件。

当前包为候选构建。剧情框架的测试、覆盖率和完整回归结果见 [剧情验证记录](docs/STORY_VALIDATION.md)；历史目录整理记录另见 [验证记录](docs/REORGANIZATION_VALIDATION.md)。

## 运行发布包

需要 Python 3.10+。在解压后的目录打开终端：

```powershell
python -m pip install -r requirements.txt
python start_ui.py
```

浏览器访问 `http://127.0.0.1:8889/ui/`。发布包已包含前端构建和生成后端，无须安装 Node.js。
HTTP 监听 `0.0.0.0:8889`，TCP 转发入口为 `0.0.0.0:8892`；局域网玩家使用主机 IP 访问。
修改端口可运行 `python start_ui.py --port 8890 --tcp-port 8893`。按 Ctrl+C 停止服务。

## SealDice 插件

将 `plugins/autocombat.js` 放入 SealDice 插件目录并重载，启用 `.set autocombat`。
需要 SealDice 1.4.0+、coc7 扩展及 loganalyser/logutil v5.0+。后端服务启动后即可使用 `.setab 2`。

## 文件和数据

- `backend/`：Python 模块、角色数据、AI 权重和中文字体。生成文件 `autocombat_backend.py` 不应手动修改。
- `frontend/dist/`：构建后的网页、图标和静态资源。
- `plugins/`：SealDice 插件。
- `docs/`：公共游戏机制与 MOV 规则。
- `.ui-runtime/`：首次运行时创建的设置、备份、任务和赛事数据库。
- `MANIFEST.sha256.json`：包内文件的 SHA256 校验清单。

升级前备份 `.ui-runtime/`、`backend/characters_data.py`、`backend/team_tables.py` 及 `backend/ai_weights*.json`。
角色编辑、训练和配置需要目录可写。此包不包含原工作区的房间、身份、白名单、数据库或历史任务。
请从项目维护者处确认代码及角色、字体等资源的分发授权；当前源码未附统一项目许可证。
# 剧情模式

“剧情模式”提供三章示例，第三章接续第二章。包内 `story-projects/demo/` 可直接作为创作工作区；在页面中打开目录即可编辑和预览，发布后运行使用固定版本。

角色立绘最多五人，支持中英文逗号；选项前置 `__LOCK` 初始锁定，LOCK／UNLOCK 保存到玩家项目档案。多入口文件头使用 `||`。详细使用见 `docs/STORY_AUTHORING.md`。

剧情战斗复用战术室并支持连续波次及完整检查点；服务重启后恢复为暂停状态。修改后的 WebGAL 源码位于 `webgal-adv/engine-source.zip`，第三方声明见 `docs/THIRD_PARTY_NOTICES.md`。
