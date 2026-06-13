# LogAI v4.3.4 — 全功能架构流程图

## 一、系统总览

```
用户群聊消息
     │
     ▼
┌──────────────────────────────────────┐
│  NapCat (OneBot v11) / SealDice      │
│  - HTTP API (port 8084)              │
│  - WebSocket (port 3001)             │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  logai_logutil_frontend.js v4.3.4    │
│  (海豹插件 — 命令解析与UI)            │
│                                      │
│  命令入口:                            │
│  ├─ .logai     日志分析              │
│  ├─ .aiutil    快速AI (v4.2新增)     │
│  ├─ .logutil   日志录制管理           │
│  ├─ .bridge    群文件桥接控制         │
│  │   └─ .bridge get N (v4.3新增)     │
│  ├─ .translate 文件翻译 (v4.2新增)    │
│  ├─ .模组分析  模组评价              │
│  ├─ .模组备团  备团梳理              │
│  ├─ .模组完善  模组润色              │
│  └─ .搜模组    网盘搜索              │
│                                      │
│  配置项 (海豹 Config):               │
│  ├─ Python后端服务端口 (IntConfig)    │
│  ├─ Python后端服务地址 (StringConfig) │
│  ├─ logutil_WS地址/Token/启用        │
│  └─ NCFileBridgeMode (0=WS, 1=轮询)  │
└──────────────┬───────────────────────┘
               │ HTTP REST
               ▼
┌──────────────────────────────────────┐
│  logai_server_release.py v4.3.4      │
│  (Flask 后端服务 — port 8000)         │
│                                      │
│  核心模块:                            │
│  ├─ AI分析 (DeepSeek API)            │
│  ├─ 图片渲染 (PIL/NovaAI)            │
│  ├─ 文件桥接 (NapCat File Bridge)     │
│  ├─ 日志管理 (logutil SQLite)         │
│  ├─ 翻译服务                          │
│  ├─ 百度网盘搜索                      │
│  └─ WebSocket实时监听                 │
│                                      │
│  关键参数:                            │
│  ├─ MAX_FILE_MB = 150                │
│  ├─ MAX_AI_CHARS = 3,000,000         │
│  ├─ MAX_LOG_ENTRIES = 20,000         │
│  └─ NC_FILE_BRIDGE_MODE (0/1)        │
└──────────────────────────────────────┘
```

## 二、命令流程图

### 2.1 `.logai` 命令流程

```
用户发送: .logai [链接/files] [pro] [温柔] [ai] [主题] [get_text] [配置名]
    │
    ├─ 检测到 "配置" 关键字 → 进入配置管理子流程
    │   ├─ 添加: 保存 {content, owner, creatorName}
    │   ├─ 删除: 校验权限 → delete
    │   ├─ 列表: 分页显示
    │   └─ 查看: 显示详情
    │
    └─ 否则 → processLogTask(ctx, msg, cmdArgs, '跑团日志评分与吐槽', 'analyze')
            │
            ├─ 解析参数
            │   ├─ isPro = 'pro' in args
            │   ├─ isKind = '温柔'/'kind' in args
            │   ├─ isAI = 'ai'/'原版'/'专业' in args
            │   ├─ getText = 'get_text' in args
            │   ├─ theme = 嗅探主题关键词
            │   └─ customPrompt = 匹配存储的自定义配置
            │
            ├─ 钱包保护检查 (Pro限额)
            │   ├─ 全局限额 check
            │   ├─ 个人限额 check
            │   └─ 预扣额度
            │
            ├─ 文件来源判断 (v4.3: 支持任意文本扩展名)
            │   ├─ useLocalFile (无链接参数)
            │   │   └─ 从 HTTP桥接/本地缓存获取最新群文件
            │   │       └─ POST /api/submit_file
            │   │
            │   └─ 有链接参数
            │       ├─ parseLogTargetEntry(link)
            │       │   ├─ URL (weizaima/dice.zone/trpgbot/kokona)
            │       │   ├─ [file]-N (桥接缓存文件编号)
            │       │   └─ 文件名/部分文件名
            │       │
            │       └─ POST /api/submit (支持多链接拼接)
            │
            ├─ 轮询等待结果
            │   ├─ GET /api/status?id=xxx
            │   ├─ status='done' →
            │   │   ├─ getText=true → 显示 text_url 下载链接
            │   │   │   (v4.3.2: 后端自动通过 NapCat 上传到群)
            │   │   └─ getText=false → 发送 CQ:image (GET /api/result)
            │   └─ status='error' → 退回额度 + 显示错误
            │
            └─ 错误处理: 退回Pro额度
```

### 2.2 `.aiutil` 命令流程 (v4.2新增, v4.3.4移除 `.ai` 别名)

```
用户发送: .aiutil [file1] … [fileN] prompt [pro] [get_text]
    │
    ├─ 解析参数
    │   ├─ fileArgs = 匹配 [file]-N 格式（拒绝文件名模糊匹配）
    │   ├─ prompt = 其余非flag文本拼接
    │   ├─ isPro = 'pro' in args
    │   └─ getText = 'get_text' in args
    │
    ├─ prompt 为空 → 报错提示
    │
    ├─ 有文件引用?
    │   ├─ YES → 从 /bridge/list 解析 content_url
    │   │        └─ POST /api/submit_file (多文件, custom_prompt=prompt, get_text=getText)
    │   │           (v4.3: 支持任意可展开为文本的文件格式 .py/.js/.c 等)
    │   │           (v4.3.1: 大文件走 raw_url 模式避免重复解析)
    │   │
    │   └─ NO  → 仅prompt无文件
    │            └─ POST /api/submit (custom_prompt=prompt, get_text=getText)
    │               └─ 后端调用 background_process_direct_text
    │                  (v4.3: 使用中性AI助手系统提示, 不混入TRPG评分模板)
    │
    └─ 轮询 → 同 .logai 的结果处理
              (getText → text_url下载链接 + NapCat自动上传,
               否则 → CQ:image图片)
    
    ⚠️ 等价于 .logai 配置 新建 test prompt + .logai test [files]
      但该临时配置不会被持久化保存。
    
    ⚠️ v4.3.4: .ai 别名已移除，仅保留 .aiutil 以避免与其他插件命名冲突。
```

### 2.3 `.logutil` 命令流程

```
用户发送: .logutil <op> [args...]
    │
    ├─ 修饰符解析 (v4.3.2/v4.3.3)
    │   ├─ raw: tokens[0] 或 tokens[1] 中出现 'raw' → raw_mode=true
    │   ├─ del_paren: args中含 'del_paren'/'delparen'/'del-paren'
    │   └─ 修饰符从 arg2 (名称) 和 compound ops 中排除
    │
    ├─ 复合命令检测 (包含 ops + end/logai)
    │   └─ POST /api/logutil_compound
    │       ├─ new → 创建日志 + clear items + 设baseline
    │       ├─ ops → 逐项解析为结构化item → add_logutil_items
    │       │   └─ ops.filter(o => o !== 'raw') 排除修饰符
    │       └─ end → 生成txt + NapCat上传 + 上传染色器
    │           └─ del_paren 从 payload 读取并传递
    │
    ├─ op='new'    → POST /api/logutil_new    (创建+开始+baseline, raw参数)
    ├─ op='on'     → POST /api/logutil_on     (继续+baseline, raw参数)
    ├─ op='off'    → POST /api/logutil_off    (暂停记录)
    ├─ op='list'   → GET  /api/logutil_list   (列出日志)
    ├─ op='clear'  → POST /api/logutil_clear  (删除日志)
    ├─ op='get'    → GET  /api/logutil_get    (获取txt+上传染色器)
    │                ↑ 支持 del_paren=true 查询参数
    ├─ op='end'    → POST /api/logutil_end    (结束+发文件+染色)
    │                ↑ 支持 del_paren=true
    └─ op='wsconfig' → POST/GET /api/logutil_config
```

### 2.4 `.translate` 命令流程 (v4.2新增, v4.3.x增强)

```
用户发送: .translate [target_lang=中文] [file1] … [fileN]
    │
    ├─ 解析: target_lang (默认"中文") + fileArgs (必须 [file]-N 格式)
    │   group_id 传递给后端
    │
    ├─ 从 /bridge/list 获取文件 content_url
    │
    ├─ 逐文件翻译:
    │   └─ GET /api/translate?url=...&filename=...&lang=...&group_id=...
    │       └─ executor.submit(background_translate_process)
    │           ├─ 下载文件
    │           ├─ 语言检测 (中文/日文/韩文)
    │           ├─ 调用AI翻译 (DeepSeek, temperature=0.5)
    │           ├─ 保存结果到桥接缓存 (v4.3.2: 同时保留bridge缓存)
    │           │   └─ text_key, text_url, text_filename 写入 JOB_CACHE
    │           └─ 自动上传到群 (v4.3.2: 后端 napcat_upload_group_file)
    │               └─ v4.3.3: 统一以 .txt 扩展名发送
    │
    └─ 轮询 GET /api/translate_result?id=xxx
        └─ 返回: status, text, filename, text_key, text_url, text_filename
```

### 2.5 模组分析/备团/完善命令流程

```
用户发送: .模组分析 [file...] [配置名] [pro] [ai] [主题] [get_text]
    │
    ├─ 检测到 "配置" → 配置管理子流程 (同logai)
    │
    └─ processModuleFile(ctx, msg, cmdArgs, modeName, pythonMode)
        │
        ├─ 解析参数 (pro/ai/主题/get_text/自定义配置)
        ├─ 钱包保护检查
        │
        ├─ 文件来源判断 (v4.2: 多文件+文件名模糊匹配)
        │   ├─ 有 [file]-N 或 文件名 → 从桥接缓存解析
        │   └─ 无文件参数 → 使用最新上传群文件 (旧行为)
        │
        ├─ POST /api/submit_file
        │   ├─ mode: analyze/prepare/refine
        │   └─ get_text, group_id 参数传递
        │
        └─ 轮询 + 显示结果 (图片 或 get_text文本+自动上传)
```

### 2.6 `.bridge` 命令流程

```
.bridge on     → POST /api/bridge_poll_on     (开启本群轮询)
.bridge off    → POST /api/bridge_poll_off    (关闭本群轮询)
.bridge status → GET  /api/bridge_poll_status (查询轮询状态)
.bridge list   → POST /api/bridge_list        (列出缓存文件+content_url)
.bridge rate n → POST /api/bridge_rate        (设置轮询间隔)
.bridge get N  → POST /api/bridge_get         (v4.3新增: 取编号N文件→NapCat上传txt)
    │
    └─ 后端 POST /api/bridge_get
        ├─ 接收 {group_id, index}
        ├─ 从 LATEST_FILES[group_id][index] 读取 content_key
        ├─ 从 CONTENT_INDEX 读取磁盘路径
        └─ napcat_upload_group_file(group_id, path, name.txt)
            └─ base64:// 协议上传 → 返回上传状态
```

## 三、后端核心处理流程

### 3.1 AI分析任务流程

```
POST /api/submit 或 /api/submit_file
    │
    ├─ JOB_CACHE[job_id] = {status: 'processing'}
    │
    └─ executor.submit(background_process / background_file_process /
                       background_process_direct_text)
        │
        ├─ 1. 省流缓存检查 (hash based, 同一天相同请求秒回)
        │
        ├─ 2. 获取日志/文件内容
        │   ├─ URL来源: fetch_log_text_by_source
        │   │   ├─ weizaima → zlib解压+base64解码
        │   │   ├─ dice_zone → zlib解压+base64解码
        │   │   ├─ trpgbot → 染色器raw text
        │   │   ├─ kokona → S3直读
        │   │   └─ raw_url → 直接HTTP读取
        │   │
        │   ├─ infer_source_by_key (v4.3增强)
        │   │   └─ 扩展名正则: \.(\w{1,10})$ (不再限于白名单)
        │   │
        │   ├─ 桥接文件: bridge_file/bridge_file_name → 本地缓存读取
        │   │
        │   └─ 上传文件: 下载 → extract_text_from_file
        │       ├─ .txt/.log/.json/.csv/.md/.xml/.yaml/.yml → 文本解码
        │       ├─ .docx → python-docx / XML提取
        │       ├─ .pdf → pymupdf / LLM原生阅读 (150MB上限)
        │       ├─ .doc → antiword/catdoc
        │       ├─ .png/.jpg/.jpeg/.webp → LLM原生图片阅读 (≤20MB)
        │       └─ 未知扩展名 → safe_decode 直接当作文本 (v4.3.1)
        │
        ├─ 3. 智能截断 (>MAX_AI_CHARS 时保留头尾+采样中间)
        │
        ├─ 4. AI请求 (OpenAI兼容API → DeepSeek)
        │   ├─ model: AI_MODEL_PRO (pro模式) / AI_MODEL (普通)
        │   ├─ system_prompt: 根据来源选择模板
        │   │   ├─ analyze → 评分分析 (DEFAULT/PRO/KIND_SYSTEM_PROMPT)
        │   │   ├─ recap → 前文回顾 (4板块)
        │   │   ├─ prepare → 备团梳理 (5板块)
        │   │   ├─ refine → 润色审查 (5板块)
        │   │   ├─ custom_prompt → 覆盖模板
        │   │   └─ 无prompt (.aiutil无文件) → 中性AI助手提示 (v4.3修复)
        │   ├─ persona注入 (骰娘人设)
        │   └─ theme标签 (AI自由选择或用户指定)
        │
        ├─ 5. 主题提取: extract_theme_from_text (嗅探【主题：xxx】)
        │
        ├─ 6. 文本渲染: text_to_images
        │   ├─ 解析Markdown → parse_markdown_layout
        │   ├─ 按【分页符】切页
        │   ├─ 多主题配色 (赛博/历史/克苏鲁/废土/二次元/终端/经典/默认)
        │   └─ PIL Image生成 → PNG bytes
        │
        ├─ 7. get_text模式 (v4.2新增, v4.3.2增强)
        │   ├─ 将raw text保存为桥接缓存文件 (CONTENT_INDEX)
        │   ├─ 构建 text_url (build_content_url)
        │   ├─ /api/status 返回 text_url + text_key + text_filename
        │   └─ 自动上传到群 (v4.3.2: napcat_upload_group_file)
        │       ├─ background_process: get_text → NapCat上传
        │       ├─ background_process_direct_text: get_text → NapCat上传
        │       └─ background_file_process: get_text → NapCat上传
        │
        └─ 8. 写入省流缓存 (普通模式)
```

### 3.2 文件桥接流程 (NapCat File Bridge)

```
文件上传事件来源:
    ├─ WS推送 (mode=0): group_upload事件 → process_ws_bridge_file
    ├─ HTTP回调 (mode=1): POST /napcat/event → enqueue_group_upload
    └─ 轮询: bridge_poll_worker_loop → pull_latest_from_napcat
        ↑ v4.3.1: mode=0时 ensure_poll_worker_started 直接return，彻底禁用轮询

处理流程:
    enqueue_group_upload(info)
        │
        └─ upload_worker_loop (后台线程)
            │
            ├─ process_group_upload(info)
            │   ├─ get_group_file_url (NapCat API)
            │   ├─ download_file_bytes (流式下载, 限150MB)
            │   ├─ extract_text_from_group_file (格式识别+文本提取)
            │   │   └─ v4.3: 未知扩展名 → safe_decode
            │   └─ write_text_cache (保存文件+注册到LATEST_FILES)
            │
            ├─ 去重检查: find_duplicate_by_name_and_chars
            ├─ 清理过期: cleanup_expired (BRIDGE_TTL_SEC)
            │
            └─ 自动导入logutil: auto_import_bridge_file_to_logutil
                └─ 如果群正在录制中 → 解析文本为结构化items → 添加到当前日志
```

### 3.3 logutil 日志录制与导出流程

```
录制状态机:
    new → recording=true, items=[], baseline=最新文件ID
    on  → recording=true, baseline=最新文件ID
    off → recording=false

记录状态 (SQLite groups表):
    ├─ current_log_name: 当前日志名
    ├─ recording: 0/1 (是否录制中)
    ├─ raw_recording: 0/1 (v4.3.2新增 — 是否跳过消息头解析)
    └─ baseline_file_id: 录制开始时的最新文件ID

消息摄入 (WS实时监听 + HTTP API):
    handle_ws_recording_event(event)
        │
        ├─ raw_recording=true → 跳过解析，直接拼接原始文本
        │   └─ segments_to_text → make_log_item → add_logutil_items
        │
        ├─ raw_recording=false → 正常解析流程:
        │   ├─ 文本消息 → extract_items_from_text_chunk
        │   │   ├─ URL链接 → fetch_log_text_by_source → 解析结构化items
        │   │   ├─ [CQ:forward] → fetch_forward_messages_napcat → 展开
        │   │   └─ 普通文本 → parse_structured_text_to_items
        │   │       ├─ 6种fwlog格式 + 3种bracket-pipe格式
        │   │       └─ 时间戳解析 (YYYY/MM/DD 和 YYYY-MM-DD)
        │   │
        │   ├─ 文件上传 → extract_items_from_file_payload_napcat
        │   │   └─ 下载 → 文本提取 → 结构化解析
        │   │
        │   └─ [file]-N命令 → 按需追加指定缓存文件
        │
        └─ Baseline检查: 跳过录制开始时已存在的文件

导出:
    logutil end / logutil get / logutil_compound end
        │
        ├─ generate_log_text (格式: Name(UID) YYYY/MM/DD HH:MM:SS\n content)
        │
        ├─ strip_paren_text (del_paren选项)
        │   ├─ 删除完全括号包裹的行 (中文/英文括号)
        │   ├─ 删除括号段落 (智能闭合检测 — 追踪匹配闭合括号)
        │   └─ v4.2修复: 多行括号块不再误删后续非括号内容
        │
        ├─ 保存为bridge文件 (可通过URL下载)
        │
        ├─ NapCat群文件上传 (send_log_via_napcat)
        │   └─ napcat_upload_group_file → base64:// 协议
        │
        └─ 上传染色器 (Story Painter API)
            ├─ zlib压缩payload
            └─ PUT weizaima API
```

### 3.4 翻译流程 (v4.3.2增强)

```
GET /api/translate
    │
    └─ executor.submit(background_translate_process)
        ├─ 下载文件
        ├─ 语言检测 (中文/日文/韩文)
        ├─ 截断 MAX_AI_CHARS (3,000,000)
        ├─ AI翻译 (DeepSeek, temperature=0.5)
        ├─ 保存结果 → JOB_CACHE
        │   ├─ text_key (桥接缓存键)
        │   ├─ text_url (下载链接 — 保险机制)
        │   └─ text_filename (v4.3.3: 统一.txt扩展名)
        └─ NapCat 自动上传到群 (v4.3.2)
            └─ napcat_upload_group_file(group_id, path, filename.txt)

GET /api/translate_result?id=xxx
    └─ 返回: status, text, filename, text_key, text_filename, text_url
```

### 3.5 百度网盘搜索流程

```
GET /api/search_module?keyword=xxx
    │
    └─ executor.submit(background_search_module)
        ├─ 1. get_valid_access_token (OAuth2.0自动刷新)
        ├─ 2. baidu_search_files (严格精准匹配+安全拦截)
        ├─ 3. 路径折叠去重 (只保留父文件夹)
        │
        ├─ 网盘分享模式 (is_local=false)
        │   ├─ 按父目录分组 (最多5组)
        │   ├─ baidu_create_share (合并分享链接+随机提取码)
        │   └─ 返回分享链接文本
        │
        └─ 本地打包模式 (is_local=true)
            ├─ 收集下载任务 (递归遍历文件夹, 限30文件)
            ├─ baidu_get_dlinks_batch (批量获取直链)
            ├─ 多线程下载 (ThreadPoolExecutor)
            ├─ ZIP打包
            └─ NapCat群文件上传
```

### 3.6 文件发送统一机制 (v4.3.2)

```
所有需要发送文件到群的操作统一使用:
    napcat_upload_group_file(group_id, file_path, name)
        │
        ├─ 读取文件内容 → base64编码
        ├─ napcat_json_post("/upload_group_file", {group_id, file, name})
        └─ 返回 (file_sent: bool, result: dict)

涉及的调用点 (全部在后端):
    ├─ send_log_via_napcat         → logutil end/get 发送日志txt
    ├─ background_process          → get_text 模式发送AI分析txt
    ├─ background_file_process     → get_text 模式发送模组分析txt
    ├─ background_process_direct_text → get_text 模式发送 .aiutil txt
    ├─ background_translate_process   → 翻译结果txt (v4.3.2)
    └─ api_bridge_get             → .bridge get N 发送桥接txt (v4.3.1)
```

## 四、修饰符系统

### 4.1 `del_paren` — 去括号

```
适用范围: .logutil get/end, 复合命令
处理位置: export_log_text → strip_paren_text

删除规则:
  1. 完全由()或（）包裹的单行 → 整行删除
  2. 以(或（开头的段落 → 追踪到闭合)或）或空行 → 全部删除
  3. 多行括号块正确追踪闭合括号，避免误删后续内容 (v4.2修复)

数据流:
  前端: payload.del_paren = true
  复合命令: api_logutil_compound 从 payload 读取 del_paren
  api_logutil_get: 支持 ?del_paren=true 查询参数
```

### 4.2 `get_text` — 文本输出 (v4.2新增, v4.3.2增强)

```
适用范围: .logai, .aiutil, .模组分析, .模组备团, .模组完善
实现:
  1. 前端检测 'get_text' in args → payload.get_text=true, group_id传递
  2. 后端分析完成后 → 将raw text保存为bridge缓存文件
  3. /api/status 返回 text_url + text_key + text_filename
  4. 后端自动调用 napcat_upload_group_file 上传到群 (v4.3.2)
  5. 前端发送下载链接作为保险机制

与图片输出的互斥: get_text=true 时优先发送文本
```

### 4.3 `raw` — 原始文本录制 (v4.3.2新增, v4.3.3修复)

```
适用范围: .logutil new, .logutil on
处理位置: 
  前端: 解析 tokens[0] 或 tokens[1] 中 'raw' → raw_mode=true
        ops.filter(o => o !== 'raw') 排除修饰符
  后端: api_logutil_new / api_logutil_on 接收 raw 参数
        → groups.raw_recording = 1
  WS监听: handle_ws_recording_event
        → raw_recording=true → 跳过 parse_structured_text_to_items
        → 直接 segments_to_text → 追加原始文本

效果:
  不解析消息头、时间戳、角色名
  各源文本按原始内容并行拼接入日志
  适用于非标准格式的纯文本日志导入
```

## 五、数据存储

```
┌─ JOB_CACHE (内存字典)
│   ├─ job_id → {status, images[], text, text_url, text_key,
│   │             text_filename, text_file_sent, created}
│   └─ 每日省流缓存: DAILY_CACHE[today][hash_key] → images[]
│
├─ LATEST_FILES (内存字典)
│   ├─ group_id → [{file_id, name, content_key, ...}]
│   └─ index 0=最旧, index -1=最新 (v4.1反序)
│
├─ CONTENT_INDEX (内存字典)
│   └─ content_key → 磁盘文件路径
│
├─ 磁盘: BRIDGE_CACHE_DIR
│   └─ {uuid}.txt (提取的文本文件)
│
├─ LOG_IMPORTED_FILES (内存字典)
│   └─ log_id_str → set() (已导入的文件ID集合, 防重复)
│
├─ logutil.db (SQLite)
│   ├─ groups: group_id, current_log_name, recording, 
│   │          raw_recording (v4.3.2), baseline_file_id
│   ├─ logs: group_id, name, ended, upload_url, upload_time
│   └─ items: log_id, nickname, im_userid, time, message, raw_msg_id
│
└─ 海豹存储 (前端)
    ├─ logai_custom_prompts: {name: {content, owner, creatorName}}
    ├─ module_custom_prompts: 同上
    ├─ logai_pro_usage_{date}_global: 全局Pro计数
    ├─ logai_pro_usage_{date}_{userId}: 个人Pro计数
    └─ log_last_file_{groupId}: 最后上传的群文件信息
```

## 六、外部依赖与数据流

```
AI服务:
  ├─ DeepSeek API (主要) — OpenAI兼容接口
  └─ NovelAI API (文生图) — nai-diffusion-4-5-full

染色器:
  ├─ weizaima.com (主要上传染色器 — Story Painter API)
  ├─ dice.zone (日志存储+染色)
  ├─ trpgbot.com (染色器s02/s03)
  └─ dicelogger (Kokona S3)

文件格式支持:
  文本: .txt .log .json .csv .md .xml .yaml .yml
  文档: .docx .doc .pdf
  代码: .py .js .c .h .cpp .java .ts .rs .go ... (v4.3: 所有纯文本扩展名)
  图片: .png .jpg .jpeg .webp (LLM原生阅读, ≤20MB)
  文件大小上限: 150MB (v4.3 从50MB提升)

NapCat通信:
  ├─ HTTP API: napcat_json_post("/upload_group_file", ...)
  ├─ HTTP API: napcat_json_post("/send_group_msg", ...)
  ├─ HTTP API: napcat_json_post("/get_group_file_url", ...)
  └─ WebSocket: ws_connect → 实时消息/文件事件

百度网盘: OAuth2.0 → 搜索/下载/分享/打包

Python便携环境 (python-build-standalone):
  ├─ 首选镜像: gitee.com/masx200/python-build-standalone (v4.3.4)
  ├─ 备用镜像: ghproxy.cc / mirror.ghproxy.com
  └─ 直连: github.com/astral-sh/python-build-standalone
```

## 七、部署架构

```
┌─────────────────────────────────────────────────┐
│  用户机器                                        │
│                                                  │
│  run_logai.sh / run_logai.bat (v4.3.4)          │
│    ├─ 自动下载便携Python (PBS, 含Gitee镜像)       │
│    ├─ ensurepip + pip install 依赖               │
│    └─ 启动 logai_server_release.py               │
│         │                                        │
│         ├─ Flask HTTP (port 8000)                │
│         └─ WS Client → NapCat (port 3001)        │
│                                                  │
│  SealDice 海豹骰娘                               │
│    └─ logai_logutil_frontend.js (插件)            │
│         └─ HTTP → Flask后端 (可配置端口)           │
│                                                  │
│  NapCat (OneBot v11)                             │
│    ├─ HTTP API (port 8084) ← 后端调用             │
│    └─ WebSocket (port 3001) → 后端监听            │
└──────────────────────────────────────────────────┘
```

## 八、v4.3.0 ~ v4.3.4 更新要点汇总

| 版本 | 序号 | 功能 | 说明 |
|------|------|------|------|
| 4.3.0 | 1 | 文件扩展名扩展 | 任意文本文件(.py/.js/.c等)均可作为输入 |
| 4.3.0 | 2 | `.bridge get N` | 编号N文件转纯文本txt→NapCat上传 |
| 4.3.0 | 3 | 文件大小上限 | 从40~50MB统一提升至150MB |
| 4.3.0 | 4 | `.ai` → `.aiutil` | 重命名命令，修复无文件模式prompt泄漏 |
| 4.3.1 | 5 | 大PDF修复 | `.bridge get` 新增专用后端接口 `/api/bridge_get` |
| 4.3.1 | 6 | 未知扩展名修复 | `.py`/`.js` 不再报"不支持格式"，直接safe_decode |
| 4.3.1 | 7 | WS轮询修复 | mode=0时彻底禁用HTTP轮询线程 |
| 4.3.2 | 8 | 文件发送统一 | get_text/translate/bridge_get 全部后端NapCat上传 |
| 4.3.2 | 9 | `raw` 修饰符 | logutil跳过消息头解析，直接拼接原始文本 |
| 4.3.3 | 10 | raw解析修复 | raw在logutil后第一/二字段时正确识别 |
| 4.3.3 | 11 | translate .txt | 翻译结果统一以.txt扩展名发送 |
| 4.3.4 | 12 | 移除 `.ai` 别名 | 避免与其他插件命名冲突 |
| 4.3.4 | 13 | .bat修复 | 编码/Python路径嵌套/依赖安装/注释编码 |
| 4.3.4 | 14 | Gitee镜像 | Python下载首选Gitee镜像 |
| 4.3.4 | 15 | LLBot兼容 | 确认LLBot架构下可用性 |
