# LogUtil + Bridge 4.4.5.1-logutil

独立 LogUtil 日志录制与 NapCat 文件桥接插件。

## 功能概述

| 功能 | 说明 |
|------|------|
| **LogUtil (.logutil)** | TRPG 群聊日志实时录制，支持 SQLite 持久化、结构化解析、导出为 txt、上传至 Story Painter 染色 |
| **Bridge (.bridge)** | QQ 群文件自动缓存，支持文本提取（PDF/DOCX/TXT 等）、轮询/WebSocket 双模式、Web 管理界面 |

> **注意**：此独立插件**不含** AI 分析（.logai/.aiutil）、模组分析、翻译、百度网盘搜索等功能。如需完整版请使用 LogAI 主项目。

---

## 文件结构

```
instance/logutil/
├── backend.py          # Flask 后端服务 (5,504 行)
├── frontend.js         # SealDice 海豹插件 (1,213 行)
├── run_logutil.bat     # Windows 启动脚本
├── run_logutil.sh      # Linux/macOS 启动脚本
└── README.md           # 本文件
```

依赖的外部文件（不在此文件夹内）：
- `../napcat_file_bridge/` — 桥接文件缓存目录
- `../../logutil.db` — SQLite 日志数据库

---

## 快速开始

### 1. 启动后端

**Windows**：
```bat
cd instance\logutil
run_logutil.bat
```

**Linux/macOS**：
```bash
cd instance/logutil
bash run_logutil.sh
```

脚本会自动下载便携 Python 3.11 并安装依赖（`flask`, `requests`, `PyPDF2`, `pymupdf`, `websockets`）。

### 2. 配置前端

将 `frontend.js` 导入 SealDice 海豹骰的插件系统。前端会自动同步 WebSocket 配置到后端。

### 3. 配置 NapCat

确保 NapCat (OneBot v11) 已启动，HTTP API 默认在 `http://127.0.0.1:8084`，WebSocket 在 `ws://127.0.0.1:3001`。

---

## 启动参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 服务器监听地址 |
| `--port` | `8001` | 服务器端口 |
| `--napcat-url` | `http://127.0.0.1:8084` | NapCat HTTP API 地址 |
| `--napcat-token` | `1` | NapCat 访问令牌 |
| `--ws-url` | `ws://127.0.0.1:3001` | NapCat WebSocket 地址 |
| `--ws-token` | (空) | WebSocket 令牌（默认复用 napcat-token） |
| `--bridge-token` | (空) | 桥接 HTTP API 鉴权令牌 |
| `--bridge-public-base` | (自动检测) | 桥接服务公网地址 |
| `--ws-enabled` | `1` | 启用 WebSocket 实时监听 |
| `--bridge-mode` | `0` | 桥接模式: 0=WS推送, 1=轮询 |
| `--story-painter-url` | `https://weizaima.com/dice/api/log` | Story Painter 上传地址 |
| `--story-painter-token` | (空) | Story Painter API 令牌 |

---

## 命令参考

### .logutil（别名 .fwlog）

| 子命令 | 用法 | 说明 |
|--------|------|------|
| `new [名称] [raw]` | `.logutil new 周六团 raw` | 新建日志并开始录制。`raw` 表示跳过消息头解析 |
| `on [名称] [raw]` | `.logutil on 周六团` | 恢复已有日志录制 |
| `off` | `.logutil off` | 暂停当前录制 |
| `end [名称]` | `.logutil end` | 结束录制，导出 txt，上传到群和 Story Painter |
| `get [名称]` | `.logutil get 周六团` | 导出日志 txt 并上传至 Story Painter（不结束录制） |
| `list` | `.logutil list` | 列出当前群所有日志 |
| `clear [名称]` | `.logutil clear 周六团` | 删除指定日志 |
| `wsconfig` | `.logutil wsconfig` | 查询/修改 WebSocket 配置 |

**复合命令**：
```
.logutil new [file]-0 [link]-1 [history]-2 end
```
原子执行：新建 → 导入桥接文件 → 导入链接 → 导入历史 → 结束导出。

**修饰符**：
- `raw` — 跳过消息格式解析，记录原始文本
- `del_paren` — 导出时删除括号内的纯英文行（KP 旁白等）

### .bridge

| 子命令 | 用法 | 说明 |
|--------|------|------|
| `on` | `.bridge on` | 为本群启用桥接轮询 |
| `off` | `.bridge off` | 停用桥接轮询 |
| `status` | `.bridge status` | 查看桥接状态 |
| `list [file\|link\|history]` | `.bridge list` | 列出缓存文件/链接/历史 |
| `get [file]-N / [link]-N / [history]-N` | `.bridge get [file]-0` | 获取指定文件/链接/历史记录并上传到群 |
| `del [file]-N [link]-N [history]-N` | `.bridge del [file]-0 [link]-1 [history]-2` | 删除指定缓存项 |
| `rate N` | `.bridge rate 10` | 设置轮询间隔（秒） |
| `master` | `.bridge master` | 获取 Web 管理界面链接 |

**快捷别名**：`F14` = `[file]-14`, `L0` = `[link]-0`, `H23` = `[history]-23`

### .halt

停止当前群所有进行中的任务。
```
.halt
```

---

## API 端点

### LogUtil 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/logutil_new` | 新建日志录制 |
| POST | `/api/logutil_on` | 恢复录制 |
| POST | `/api/logutil_off` | 暂停录制 |
| GET | `/api/logutil_list` | 列出日志 |
| POST | `/api/logutil_clear` | 删除日志 |
| POST | `/api/logutil_add` | 手动添加消息条目 |
| GET | `/api/logutil_get` | 导出日志（不结束） |
| POST | `/api/logutil_end` | 结束并导出日志 |
| POST | `/api/logutil_compound` | 复合命令 |
| POST/GET | `/api/logutil_config` | 配置管理 |
| POST | `/api/logutil_toggle` | 快速开关录制 |
| GET | `/api/logutil_status` | 录制状态 |
| POST | `/api/logutil_record_outgoing` | 记录发出消息 |

### Bridge 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/napcat/event` | 接收 NapCat 文件上传事件 |
| POST/GET | `/bridge/latest` | 获取最新缓存文件 |
| POST/GET | `/bridge/list` | 列出缓存 |
| GET | `/bridge/content/<key>` | 获取缓存内容 |
| POST | `/api/bridge_get` | 获取文件并上传到群 |
| POST | `/api/bridge_del` | 删除缓存项 |
| POST | `/api/bridge_poll_on` | 启用轮询 |
| POST | `/api/bridge_poll_off` | 停用轮询 |
| GET/POST | `/api/bridge_poll_status` | 轮询状态 |
| GET/POST | `/api/bridge_list` | 缓存列表（含预览） |
| POST | `/api/bridge_rate` | 设置轮询间隔 |
| POST/GET | `/api/bridge_master` | Web GUI 地址 |
| GET | `/bridge/gui` | Web 管理界面 |
| GET | `/bridge/gui/<group_id>` | 指定群 Web 界面 |
| GET | `/api/bridge_gui_data` | GUI 数据接口 |
| POST | `/api/bridge_gui_command` | GUI 命令接口 |

### 通用端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/halt` | 停止任务 |

---

## 配置项（前端）

| 配置名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| Python后端服务地址 | String | `http://127.0.0.1:8001` | 后端地址 |
| Python后端服务端口 | Int | `8001` | 后端端口 |
| OneBot_API_地址 | String | `http://127.0.0.1:34567` | Bot HTTP API |
| 启用HTTP文件桥接 | Bool | true | 启用桥接 |
| HTTP文件桥接读取API | String | `…/bridge/latest` | 桥接读取接口 |
| HTTP文件桥接Token | String | (空) | 桥接鉴权 |
| 启用文本文件桥接 | Bool | true | 文本桥接 |
| 文本桥接前缀 | String | `[LogFileBridge]` | 桥接消息前缀 |
| 文本桥接自动提示 | Bool | true | 自动回复提示 |
| 调试日志 | Bool | true | 调试日志 |
| fwlog文案风格 | Bool | true | fwlog → logutil 文案替换 |
| logutil_WS地址 | String | `ws://127.0.0.1:3001` | WS 地址 |
| logutil_WS_Token | String | (空) | WS 令牌 |
| logutil_WS启用 | Bool | true | WS 启用 |
| NCFileBridgeMode | Int | 0 | 桥接模式 |

---

## 环境变量

所有启动参数均支持通过环境变量配置：

| 变量 | 对应参数 |
|------|----------|
| `LOGAI_HOST` | `--host` |
| `LOGAI_PORT` | `--port` |
| `NAPCAT_API_BASE` | `--napcat-url` |
| `NAPCAT_TOKEN` | `--napcat-token` |
| `NAPCAT_WS_URL` | `--ws-url` |
| `NAPCAT_WS_TOKEN` | `--ws-token` |
| `BRIDGE_TOKEN` | `--bridge-token` |
| `BRIDGE_PUBLIC_BASE` | `--bridge-public-base` |
| `LOGUTIL_WS_ENABLED` | `--ws-enabled` |
| `NC_FILE_BRIDGE_MODE` | `--bridge-mode` |
| `BRIDGE_TTL_SEC` | 缓存过期秒数（默认86400） |
| `MAX_BRIDGE_FILES_PER_GROUP` | 每组最大缓存文件数（默认20） |
| `LOGAI_STORY_PAINTER_UPLOAD_URL` | Story Painter 地址 |
| `LOGAI_STORY_PAINTER_TOKEN` | Story Painter 令牌 |

---

## 致谢

- 原作者: Air, Gemini
- 改编: fanmm (@fanmm01)
- logutil 段参考: @chaye2333 的 fwlog 项目

---

## 更新日志

**v4.4.3-logutil**
1. `[history]-N` 全面支持：复合命令及录音中独立发送均支持 `[history]-N` 引用被淘汰的桥接历史项。
2. 修复 `bridge_link` 在复合命令中静默失效的问题（来源白名单遗漏，导致 `[link]-N` 被当作原始文本处理）。
3. 修复复合命令中 `raw` 修饰符未传递到后端的问题。
4. `/api/bridge_list` 在默认模式下同时返回历史记录（按群号过滤）。
5. `/bridge/list` 端点新增历史记录返回。
6. 同步 LogAI v4.4.3 全部修复。

**v4.4.4-logutil** （含 v4.4.4.0 + v4.4.4.1）

**v4.4.4.0-logutil**
1. 修复 `.bridge get [link]-N` / `[history]-N` 返回文件名含 URL 的问题。
2. 修复跨群数据泄露：所有历史记录访问点增加 `group_id` 过滤隔离。
3. 修复历史记录列表中 `[link]` 字样重复显示（name 字段移除 `[link] ` 前缀）。
4. 修复 SealDice 去括号导致 `[link]-N` / `[history]-N` 无法识别：`expandShortAlias` 新增去括号修复。
5. 新增 URL 黑名单：QQ 多媒体 CDN 链接不再自动缓存。
6. 修复复合命令与独立发送中不识别短别名（`F14`/`L0`/`H23`）：后端新增 `expand_short_alias`，前端重分词后补做别名展开。
7. 修复复合命令中 `raw` 修饰符仅在前两个位置生效的问题。
8. 同步 LogAI v4.4.4 全部修复。

**v4.4.4.1-logutil**
1. 修复 `raw` 修饰符在复合命令中仍可能被误判为标题的问题。
2. 修复历史记录编号错位（全局索引→顺序计数器）。
3. 新增跨群访问语法：`L0-群号` / `[link]-0-群号` 等。
4. `.bridge list` 全部采用合并聊天记录格式，新增 `all` 参数。
5. 同步 LogAI v4.4.4.1 全部修复。

**v4.4.4.2-logutil**
1. 修复 `raw` 修饰符在复合命令与独立消息中无效的根因（三层修复：DB行确保、复合op解析、WS消息处理均检查 raw 标记）。
2. 同步 LogAI v4.4.4.2 全部修复。

**v4.4.5-logutil**
1. 跨群访问语法全面生效：`H0-群号` 等格式在所有场景可用。
2. 同步 LogAI v4.4.5 全部修复。
