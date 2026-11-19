# 剧情运行与扩展接口

HTTP 前缀为 `/api/ui/story`，使用现有 HttpOnly 会话 cookie 和同源写请求检查。开发阶段目录与执行目标不使用白名单。

## 项目与运行

- GET projects：项目、章节、锁定原因与可用入口。
- POST workspaces/open `{path}`；GET workspaces/:id/tree、files?path=…。
- POST workspaces/:id/files `{changes:[{path,baseHash,content,op?}]}`。op 为 write、rename、delete；rename 还需 to。
- POST workspaces/:id/compile `{drafts?}`，返回图形和诊断。
- POST workspaces/:id/graph/patch `{path,source?,operation}`，操作可为 replace、delete、append、header。
- POST workspaces/:id/publish：不可变正式版本。
- POST workspaces/:id/preview `{drafts?,flow?}`：独立 preview 项目，正式进度不受影响。
- POST runs `{project,flow?,entry?,version?}`：entry 是 START 或具体接续记录 ID。
- GET runs/:id、runs/:id/events、runs/:id/slots。
- POST runs/:id/commands `{expectedRevision,commandId,op,payload}`。

命令包括 advance、checkpoint、choose、retry、startBattle、resumeBattle、save、load、executeExternal、cancelExternal。保存使用 `{slot,snapshot?}`，选择使用 `{id}`，播放器推进使用 `{token,snapshot,user_data}`。409 时刷新后用同一 commandId 重试。

项目版本固定在运行和存档中；媒体由该版本的 game 路径提供。文件写入检查内容哈希，发生冲突不会覆盖外部修改。多文件保存和引用同步有恢复日志。编辑器重命名保留 content_ids，权限记录不因名字变化丢失。

接续入口按 `(source, tag)` 唯一；重复完成来源章节时替换该组接续状态。读取旧档案中的重复记录时按完成时间选取最新记录，同一时间取后写入者。START、不同来源及不同标签互不合并；已建立运行与存档保留各自的状态副本。

## 桥接与检查点

主应用与 iframe 独立使用各自 React，不共享 DOM 或 Redux。消息包含 protocol=1、launch、requestId，校验来源窗口与同源。宿主边界同时阻塞点击、自动和快进，使用执行代次去重。

存档包含流程位置、分支、变量、计数、WebGAL 舞台/调用栈/媒体进度、战斗角色与引擎内部状态、待反应法术计划、召唤分组、随机状态、控制和请求去重。集合、元组键和共享引用使用显式 JSON 对象图，绝不载入 pickle。

AI 权重按内容哈希保存一次；战报按事件追加，检查点引用事件，避免每个行动重复写入整场历史。服务重启后恢复原有骰点和待反应状态，默认暂停。权限与完成记录在玩家项目档案中独立保存，不随旧档回滚。

历史回顾可阅读；跨剧情块回跳使用统一存档，避免越过已执行战斗或重复结算权限指令。

## 外部规则

`.rule` 为 JSON，例如 HTTP 规则：

```json
{
  "schema": 1,
  "syntax": "json-v1",
  "inputSchema": {
    "type": "object",
    "required": ["difficulty"],
    "properties": {"difficulty": {"type": "integer"}}
  },
  "executor": {"kind": "http", "url": "http://127.0.0.1:9000/resolve", "timeout": 30},
  "display": "NONE",
  "returns": ["w", "l"]
}
```

对应 `.gmf`：

```text
rule CHECK
{"difficulty": 2}
```

请求为 `{schema:1,execution_id,input}`，响应为 `{result:"w",data?:…}`。HTTP 使用 JSON POST，Idempotency-Key 为 execution_id。inputSchema 支持 type、enum、required、properties、items。

程序规则将 executor 改为 `{kind:"exec",argv:["程序路径","参数"],timeout:30}`；JSON 写入 stdin，stdout 只返回一个 JSON 对象，stderr 为错误日志。不使用 shell 拼接。取消会终止本地进程；已发送 HTTP 请求的远端执行无法撤回，其结果会被忽略。

普通外部规则在当前页面等待结果，不切换到战术室；AUTOCOMBAT 内置规则负责页面切换。重启时未完成的外部调用标为中断，必须由玩家明确重新执行。远端应按 execution_id 去重。
