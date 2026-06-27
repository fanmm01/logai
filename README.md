# logutil & logai
**V4.6.1**  
*A fork based on Air, Gemini: 人工智障系列*

---

@license: Apache-2.0  
logai功能原作者：Air, Gemini  
fwlog功能部分原作者：@chaye2333  
合并、修改、以及新建部分功能：@fanmm, GPT-5, DeepseekV4  

---

## 使用方法：
1. 完整地解压整个压缩包；
2. 将前端 `logai_logutil_frontend.js` 文件导入海豹插件列表中；
3. 找到后端的启动器：
   - linux/Mac: `run_logai.sh`
   - windows: `run_logai.bat`
   （安卓请在termux内执行sh文件）
   随后，如文件对应的注释般，编辑其配置部分；
   该脚本会自动执行缺失依赖的补齐并最终启动。
   推荐的启动脚本为（以linux为例，在cd到当前目录后）："bash ./run_logai.sh --api-key=sk-xxxxxxxxxxxxx"。这样会获得更高的安全性和分发的灵活性。

## 注意事项：
- 本插件缺乏足够且全面的测试，且未经检验其跨平台能力。
- 大部分测试中使用linux环境、napcat分离部署。故windows下以及LLBot下可能存在未经发现的漏洞。
- 可能有任何bug出现，如有发现，请在github：https://github.com/fanmm01/logai 上反应。
- 修改时为vibe coding，可能含有ai创作的差代码。
- 本插件已知适用于napcat分离部署架构。确认了LLBot下基础功能的可用性但是，在LLBot上，合并聊天记录转发功能无法实现：这会影响.bridge get指令的返回。
  对于其余架构，其可用性未知，可能需要自行修改插件源码。

---

## 功能列表：

### 一、logai功能
`.logai [链接1] [链接2] [...] [pro] [温柔] [ai] [主题标签] [自定义配置名] [get_text]`

其中，`[链接n]` 可为一个或多个。当不给出任何链接时，默认使用当前最新群文件。  
链接支持以下内容：
a) 染色器链接。目前，支持的包括：
   - `'https://s02.trpgbot.com/s/'`
   - `'https://s03.trpgbot.com/models/'`
   - `'https://api.dice.center/dicelogger/'`
   - `"https://dicelogger.s3-accelerate.amazonaws.com/"`
   - `"https://weizaima.com/"`
   - `"https://log.dice.zone/"`
b) 文件编号/名称。目前，支持的包括：
   - `[file]-N` / `[link]-N` / `[history]-N` 编号格式；
   - 文件名及其不引起歧义的部分；
   - 短别名 `F14`=`[file]-14`、`L0`=`[link]-0`、`H23`=`[history]-23`；
   - 跨群访问 `F0-群号` / `[file]-0-群号` 等。

**示例**: `.logai https://weizaima.com/dice/api?key=xxx pro 温柔 提取NPC`

**修饰符 `get_text`**：适用于所有生成图片的指令（`.logai`、`.aiutil`、模组命令）。携带时，输出不再以图片方式给出，而是全部AI输出放入一个txt文件中，并直接发送到群。

`.logai 配置 添加 <名称> <提示词内容>`  
创建自定义分析配置，之后可用 `.logai <名称>` 调用  
`.logai 配置 删除 / 列表 / 查看`  
管理配置

`.aiutil [file1] …… [fileN] prompt [pro] [get_text]`  
将桥接缓存中的文件提交给AI进行分析，提示词为输入的`prompt`。支持任何可展开为文本的文件格式（.py, .c, .js, .txt, .log 等）。  
file1-fileN 支持 `[file]-N` / `[link]-N` / `[history]-N` 格式及短别名（F14/L0/H23）、跨群访问（F0-群号）。  
在不提供任何文件时，仅把prompt交给AI，不携带文件。  
等价于 `.logai 配置 新建 test prompt` + `.logai test [file1] …… [fileN]`，但该临时配置不会被保存。

`.模组分析 [file1] …… [fileN] [配置名] [pro] [ai] [主题] [get_text]`  
分析模组文件。支持 `[file]-N` / `[link]-N` / `[history]-N` 编号、短别名、跨群访问、文件名及其部分匹配。不指定文件时默认使用最新群文件。

`.模组备团`、`.模组完善` 同上，均完整支持多文件指定与所有引用格式。

---

### 二、.logutil功能
以下全部logutil均可用fwlog代替。

`.logutil new [名称] [raw]`  开始新日志记录（raw: 跳过消息头解析，直接拼接原始文本，可在任意位置使用）  
`.logutil on [名称] [raw]`   继续记录已有日志  
`.logutil off`         暂停记录  
`.logutil end [名称] [del_paren]`  结束并发送文件与染色器链接。del_paren参数可去除括号包裹的内容（见修饰符说明），可在任意位置使用。  
`.logutil get [名称]`  获取当前日志文件及链接  
`.logutil list`        列出当前群的 logutil 日志  
`.logutil clear [名称]` 删除指定日志  
`.logutil wsconfig [url] [token] [on|off] [mode=0|1]`  配置 WS 监听参数

logutil功能可用于以下操作：
- 将多个log合并到一起；
- 将菠萝导出的log转为海豹染色器格式；
- 把已经由染色器导出的docx文档重新打回到染色器内以进行重新染色；
- etc.（其他功能待开发）

logutil on状态下：
可通过【上传群文件】【发送群文件标题】【发送合并转发记录】【发送零碎文字】【发送（一）中给出的格式的染色器链接】将其内容注入到logutil日志中。  
此外，`[file]-N` / `[link]-N` / `[history]-N` 及短别名（F14/L0/H23）、跨群访问（F0-群号）可直接在群内发送，将桥接缓存内容追加到当前正在记录的日志中。

logutil支持识别以下种类的消息头：
<角色名>: 发言内容；

<
角色名(可跨多行)\n>: 对白内容；

2024/01/15 14:30:22<角色名>: 内容；

2024/01/15 14:30:22角色名: 内容；

[2024-01-15 14: 30:22]<角色名|玩家昵称>内容；

（当玩家昵称为”游戏外”时，内容会自动用中文括号(内容)ゝ包裹。使用 `del_paren` 修饰符可在输出时移除此类括号。）

[2024-01-15 14: 30:22]*角色名|玩家昵称内容；

2024/01/15 14:30:22（即：仅有时间戳）

---

### 三、群文件桥接系统
`.bridge on/off/status/rate`  控制群文件桥接系统。  
`.bridge list [file|link|history|all]`  以合并聊天记录格式返回桥接缓存列表，条目附带内容开头预览字符。`all` 参数同时显示文件/链接/历史。  
`.bridge master`  查看 Web 管理界面地址（使用内网 IP）。  
`.bridge get [file]-N/[link]-N/[history]-N`  获取编号文件并直接发送纯文本到群。  
`.bridge del [file]-N [link]-N [history]-N ...`  删除指定编号的桥接项，索引自动顺延。

通过输入 `[file]-N` / `[link]-N` / `[history]-N` 调取编号为N的缓存项。  
支持短别名 `F14`=`[file]-14`、`L0`=`[link]-0`、`H23`=`[history]-23`。  
支持跨群访问语法 `F0-群号` / `[file]-0-群号` 等，可在任意命令中访问其他群的桥接缓存。  
编号从旧到新排列，以0开端，最多缓存20个文件与30个链接。溢出项移入【历史记录】（最多50条），可通过 `.bridge list history` 查看。关闭后端时自动保存历史记录至磁盘，下次启动恢复。

文件桥接系统支持提取文本的文件格式如下:
- `.txt`, `.log` (纯文本文件)
- `.json`, `.csv`, `.md`, `.xml`, `.yaml`, `.yml` (常见数据/标记格式)
- `.docx` (Word文档，使用 `python-docx` 库解析)
- `.pdf` (PDF文档，使用 `pymupdf` 库解析，可直接提取文本; 如果文件较大，LLM可原生阅读PDF图像/排版)
- `.doc` (旧版Word 文档，需依赖系统命令 `antiword` 或 `catdoc` 提取文本)
- 其他本质为文本的文件格式，如 `.py`, `.js` 等等。

另外，对于图片文件 (`'.png'`, `'.jpg'`, `'.jpeg'`, `'.webp'`)，桥接系统本身不提取文本，但在文件分析任务中会由 LLM 进行原生图片阅读 (当文件大小不超过20MB时)。  
整体文件大小上限为150MB。

---

### 四、其他
`.搜模组 <关键词>`  百度网盘搜索模组  
`.translate [goal-ALL|ALL] [target_lang=中文] [file/link1] …… [file/linkN]`  调用翻译接口，对docx/pdf/txt等格式文件进行翻译，返回翻译后的txt文件并上传到群。支持 `[file]-N` / `[link]-N` / `[history]-N`、短别名及跨群访问。默认目标语言为中文。`goal-ALL`（简写 `ALL`）模式在翻译开始时即发送 TextDB.online 在线查看链接，无超时限制，持续翻译直到完成或被 `.halt` 停止。

`.halt`  强制停止当前群内所有进行中的AI生成任务，所有用户可用。

---

### 五、复合命令
**示例**: `.logutil new [file]-0 [link]-1 [history]-2 end logai 评分 pro`

将新建日志，追加缓存文件/链接/历史内容，结束记录，然后对生成文件执行 AI 分析。  
复合命令支持所有修饰符（`raw`、`del_paren`）在任意位置使用，支持所有引用格式（短别名、跨群访问）。

### 六、修饰符说明
**`del_paren`**：当 logai/logutil 带有标记 `del_paren` 时，将所有在调整后被中文括号（）或英文括号()括起来的"行"，以及以中文/英文左括号开头的段落，从用于AI分析的纯文本/输出的着色器链接/输出的txt文件中删除。适用于 `.logutil end`、`.logutil get`、复合命令，可在任意位置使用。

**`get_text`**：针对所有生成格式是一张图片的指令（如 `.logai`、`.aiutil`、模组命令）。携带时输出不再以图片方式给出，而是把全部AI输出放在一个txt文件里并直接发送到群。

**`raw`**：对 logutil 生效。携带时跳过判断消息头的步骤，直接把各源文本按原始内容并行拼接入日志中。适用于导入非标准格式的纯文本日志，可在任意位置使用（含复合命令）。

---

### 七、防刷屏机制 (v4.4.0新增)
海豹配置项 `刷屏警告时限(s)`（默认60）与 `处理上限`（默认6）。
在指定秒数内，若某群中 `.logai` + `.aiutil` 调用次数超过上限，则拒绝后续请求并提示等待时间。

### 八、Web管理界面
通过 `.bridge master` 查看网页版管理地址（使用内网 IP 而非 127.0.0.1），可在浏览器中通过下拉框选择群组查看文件/链接/历史记录（含内容开头预览），并支持输入命令。通过 `/bridge/gui/<群号>` 直接访问时默认打开对应群组。

---

### Bridge API 文档

Bridge 系统负责群文件缓存、链接文本缓存、历史记录管理。以下为完整 API 参考。

#### `POST /napcat/event`
接收 NapCat (OneBot v11) 文件上传事件，自动提取文本并缓存。

| 参数 | 类型 | 说明 |
|------|------|------|
| `group_id` | int | 群号 |
| `file` | object | 文件信息 `{id, name, busid, size}` |

#### `POST/GET /bridge/latest`
获取指定群最新缓存的文件。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |

**响应** `{status:"ok", file:{name, content_key, content_url, text_chars, ...}, file_count:N, index:N}`

#### `POST/GET /bridge/list`
列出指定群桥接缓存（含文件、链接、历史）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |

**响应** `{status:"ok", files:[...], links:[...], history:[...], count:N}`

#### `GET /bridge/content/<content_key>`
获取缓存文件的原始文本内容。返回 `text/plain`。

#### `GET/POST /api/bridge_list`
列出缓存，含内容预览字符。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `group_id` | int | 是 | - | 群号 |
| `filter` | string | 否 | 空 | `file`/`link`/`history`/`all`。空=file+link，`all`=全部含历史 |

**响应** `{status:"ok", files:[{index,name,text_chars,preview,content_key,content_url,...}], links:[...], history:[...], history_count:N}`

#### `POST /api/bridge_get`
获取指定编号的缓存项并上传到群。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |
| `ref` | string | 是 | 引用，如 `[file]-0`、`[link]-1`、`[history]-2`、`[file]-0-123456`（跨群） |

**响应** `{status:"ok", file_sent:true, filename:"xxx.txt"}`

#### `POST /api/bridge_del`
删除指定编号的缓存项。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |
| `targets` | string[] | 是 | 引用数组，如 `["[file]-0","[link]-1","[history]-2"]` |

**响应** `{status:"ok", deleted:[...], errors:[...]}`

#### `POST /api/bridge_poll_on` / `POST /api/bridge_poll_off`
启用/停用本群的桥接轮询。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |

#### `GET/POST /api/bridge_poll_status`
查询轮询状态。参数同 `bridge_poll_on`。

**响应** `{status:"ok", poll_active:bool, poll_interval_sec:N, ...}`

#### `POST /api/bridge_rate`
设置轮询间隔。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |
| `rate` | int | 是 | 间隔秒数 |

#### `POST/GET /api/bridge_master`
获取 Web 管理界面地址（使用内网 IP）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |

**响应** `{status:"ok", gui_url:"http://内网IP:端口/bridge/gui/群号"}`

#### `GET /api/bridge_groups`
返回所有存在桥接数据的群号列表。

**响应** `{status:"ok", groups:["123456","789012",...]}`

#### `GET /api/bridge_gui_data`
Web GUI 数据接口。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |

**响应** `{status:"ok", files:[...], links:[...], history:[...]}`

#### `POST /api/bridge_gui_command`
Web GUI 命令输入接口（辅助性质，完整功能请通过 QQ 使用）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | int | 是 | 群号 |
| `command` | string | 是 | 命令文本 |

#### `GET /bridge/gui` / `GET /bridge/gui/<group_id>`
Web 管理界面 HTML 页面。带 group_id 时自动选中对应群组。

---

## 更新日志：

**napcat bridge v0.1**  
- 建立了一个可以被外部接口访问的小型文件管理器，功能是读取napcat接收到的群文件并将其转为文本形式存储。

**napcat bridge v0.2**  
- 增加缓存功能，使之可以同时存储多个文件，并可通过文件编号获取它们。
- 增加自动轮巡功能，使之可以主动地循环获取最新群文件。
- 将其内嵌至了logai中。下面将把其更新日志合并至logai内部。

**logai v2.5.1**  
- 追加对外部接口“napcat bridge”的兼容性。现在可以在napcatbridge运行中，通过该桥接系统监听并获取群文件列表，不再需要修改海豹源码。

**logai v3.0.0**  
1. 将logai与napcat bridge合并。现在，只需要开启logai的后端便可以获取群文件列表了。
2. 现在允许logai同时读入多个链接/文件，将其依照顺序拼接后交给ai分析。需要提供链接或完整的文件名。

**logai v3.1.0**  
1. 将logai与原作者的模组分析插件前端功能合并。
2. 对桥接系统，增加文件编号。该编号可以被用于替代文件名。
3. 染色器链接现在支持dicezone。
4. bug修复。

**fwlog_forked v1.0**  
1. 追加对外部接口“napcat bridge”的兼容性。
2. 增加把文件，染色器链接，单句对话接入至fwlog的能力。
3. 减少一些bug，增加另一些bug。

**logai v4.0.0**  
1. 合并fwlog与logai。为fwlog提供新名字为logutil。
2. 更改了ncbridge文件监听的逻辑。旧逻辑保留备用。
3. 追加del_paren参数，用于去括号。

**v4.1.0**  
1. 修复数个重要bug。
2. logutil增加对菠萝导出的log的支持。
3. 增加复合命令功能。
4. 扩大ncbridge编号系统的实用价值。将其反序。
5. 由于bug严重，暂时不实现del_paren参数的功能。
6. 创建win/linux系统下的启动脚本，并将api key改为在启动脚本内填写，以增大分发过程中的安全性与灵活性。

**v4.2.0**  
1. 新增 `.aiutil` 命令：快速AI分析，仅支持 `[file]-N` 格式文件，不保存配置。无文件时仅将提示词交给AI。
2. 新增 `.translate` 命令：调用AI翻译接口，对docx/pdf/txt格式文件进行翻译。
3. 新增 `get_text` 修饰符：图片输出指令可改为txt文件输出。
4. 完善 `del_paren` 修饰符：修复括号段落逻辑漏洞，现已完整奏效。追加到 `logutil get` 的支持。
5. 模组命令（分析/完善/备团）全面支持多文件与指定文件，机制与logai完全对齐。
6. 后端端口号改为海豹配置项，默认8000，可即时调整。
   
**v4.2.1**
1. 修复 `.aiutil` 无文件模式下混杂 `.logai` 评分prompt的bug，现使用中性AI助手系统提示。
   
**v4.3**
1. 文件输入扩展：`.aiutil` 及所有文件输入命令，提供文件允许为任何可展开成文本的文件（.py/.c/.js/.class 等），未知扩展名直接当作文本解码。
2. 文件大小上限从 50MB/40MB 统一提升至 150MB。
3. `.bridge get` 新增专用后端接口 `/api/bridge_get`，内部直接调用 NapCat 上传文件到群，解决大文件提取失败及只发链接未实际上传的问题。
4. `.aiutil get_text` 与 `.translate` 同样改为后端自动上传文件到群，翻译结果统一以 `.txt` 扩展名发送。
5. 修复 `FileBridgeMode=0`（WS实时推送模式）下仍启动HTTP轮询的问题，WS模式下彻底禁用HTTP轮询。
6. 新增 `raw` 修饰符（对 logutil 生效）：跳过消息头解析，直接拼接原始文本。
7. 移除 `.ai` 作为 `.aiutil` 的别名，避免与其他插件命名冲突。
8. Python 下载源新增 Gitee 镜像作为首选；修复 Windows 启动脚本编码、Python 路径嵌套、依赖安装失败等问题；确认 LLBot 兼容性。

——————————————

**v4.3.0**  
1. 文件输入扩展：`.aiutil` 及一切输入文件的命令，提供文件允许为任何可以被展开成文本的文件（.py, .c, .js, .class 等）。
2. 新增 `.bridge get N`：将该编号文件桥接为纯文本后的txt文档直接给出下载链接。
3. 文件大小上限从50MB/40MB统一提升至150MB。

**v4.3.1**  
1. 修复大文件（如59MB PDF）通过 `.bridge get` 和 `.aiutil` 提取失败的问题：`.bridge get` 新增专用后端接口 `/api/bridge_get`（内部直接调用 NapCat `upload_group_file`，与 `logutil end` 机制一致）；`.aiutil` 改走 `raw_url` 模式避免重复解析已提取的桥接文本。
2. 修复 `.py`、`.js`、`.c` 等非白名单扩展名文件报"不支持的文件格式"的问题：未知扩展名现在直接当作文本解码。
3. 修复 `FileBridgeMode=0`（WS实时推送模式）下仍启动HTTP轮询的问题：WS模式下彻底禁用HTTP轮询。

**v4.3.2**  
1. 修复 `.bridge get`、`.aiutil get_text`、`.translate` 三个功能只发送下载链接而未实际通过 NapCat 上传文件到群的问题：改为后端自动调用 `napcat_upload_group_file` 上传，前端不再参与文件发送逻辑。`.translate` 同时保留下载链接作为保险机制。
2. 新增 `raw` 修饰符（对 logutil 生效）：携带时跳过消息头解析步骤，直接把各源文本并行拼接入日志。

**v4.3.3**  
1. 修复 `raw` 修饰符无效的问题：`raw` 出现在 `logutil` 后第一或第二字段时正确识别为修饰符，不再被误当作日志名称或录入文本。
2. `.translate` 翻译结果统一以 `.txt` 扩展名发送（因桥接已将原文统一转为纯文本，保留原扩展名无意义）。

**v4.3.4**  
1. 移除 `.ai` 作为 `.aiutil` 的别名，避免与其他插件命名冲突。
2. 修复 Windows 启动脚本 `run_logai.bat` 的编码、Python 路径嵌套、依赖安装失败等问题。
3. Python 下载源新增 Gitee 镜像（`https://gitee.com/masx200/python-build-standalone`）作为首选。
4. 确认 LLBot 架构下的可用性。

**v4.4**
1. 新增防刷屏机制：基于滑动窗口限速 `.logai` 与 `.aiutil` 调用，海豹配置项可调。
2. 新增着色器链接文本缓存（`[link]-N`，上限30个）：功能中使用染色器链接时自动保存为桥接缓存；消息中出现的染色器链接也被自动监听并缓存。
3. 新增历史记录系统：文件/链接溢出项自动移入历史记录（上限50条，0=最新），关闭后端时自动持久化，下次启动恢复。`[history]-N` 引用全面支持所有命令，编号采用顺序计数器，按 `group_id` 过滤隔离。
4. 新增短别名（`F14`=`[file]-14`、`L0`=`[link]-0`、`H23`=`[history]-23`）与跨群访问语法（`F0-群号`/`[file]-0-群号` 等），所有命令及 WS 独立消息均可用。
5. 新增 `.bridge del` 命令：删除指定桥接项，索引自动顺延。
6. 新增 `.bridge list all` 参数，全部采用合并聊天记录格式呈现，条目附带内容开头预览字符。
7. 新增 `.bridge master` 命令：查看 Web 管理界面地址（使用内网 IP 而非 127.0.0.1），彻底移除输出中的 127.0.0.1。
8. 新增 Web 管理界面（`/bridge/gui`）：下拉框选择群组，以表格展示文件/链接/历史记录，含命令输入框；通过 `/bridge/gui/<群号>` 直接访问时默认选中对应群组。
9. 新增 `.halt` 命令（全员可用）：强制停止当前群内所有进行中的 AI 生成任务。
10. 新增 `goal-ALL`（简写 `ALL`）翻译模式：分句逐块翻译，每 10 秒上传进度至 TextDB.online，启动时即发在线查看链接，无超时限制。
11. 新增 URL 黑名单：QQ 多媒体 CDN（`multimedia.nt.qq.com.cn`）不再自动缓存。
12. `.bridge get` 直接发送纯文本文件到群，不再附带下载链接；所有用户侧的下载链接均已删除，文件直接通过群文件交付。
13. 修复 `bridge_link` 在复合命令中静默失效的问题（来源白名单遗漏）。
14. 修复 SealDice 去括号导致 `[link]-N`/`[history]-N` 无法识别的问题：`expandShortAlias` 新增去括号修复，复合命令重分词后补做别名展开，后端同步新增 `expand_short_alias`。
15. 修复 `raw` 修饰符三层失效问题：DB 行确保（`ensure_logutil_group_state`）、复合 op 解析（直接 `make_log_item`）、WS 消息处理均检查 raw 标记；`del_paren` 等修饰符不会被当作文本 op 发送。
16. 修复 `.aiutil get_text` 模式下 txt 文件可能因竞态条件而丢失的问题（`status='done'` 移至 `get_text` 块之后）。
17. 日志输出净化：NapCat 文件上传日志不再打印完整 base64 文件内容，改为截断标记。
18. 修复 TextDB 云数据库接口，仅用于 goal-ALL 翻译功能。

————————————————
**v4.4.0**
1. 新增防刷屏机制：海豹配置项 `刷屏警告时限(s)`（默认60）与 `处理上限`（默认6），基于滑动窗口限速 `.logai` 与 `.aiutil` 调用。
2. 新增着色器链接文本缓存：任何功能中使用着色器链接时，其纯文本内容自动保存到桥接缓存中，编号为 `[link]-N`（上限30个），可在任何使用链接处替代。支持单独发送 `[link]-N` 调取。
3. 新增 Web 管理界面：`/bridge/gui/<群号>` 以表格展示文件/链接/历史记录，含命令输入框。
4. 新增历史记录系统：文件超过 20 个或链接超过 30 个时，溢出项移入历史记录（上限50）。关闭后端时自动保存至 `instance/napcat_file_bridge/history.json`，下次启动恢复。
5. 新增 `.bridge del [file]-N [link]-N [history]-N ...` 命令：删除指定桥接项，索引自动顺延。
6. `.bridge get` 命令参数格式更新为 `[file]-N`/`[link]-N`/`[history]-N`。
7. 新增短别名：`F14` = `[file]-14`、`L0` = `[link]-0`、`H23` = `[history]-23`，可在所有命令中使用。
8. 新增 `.halt` 命令：强制停止当前群内所有进行中的 AI 生成任务（仅骰主可用）。
9. 修复 `logutil end` 时桥接文件列表出现重复条目的问题（`export_log_text` 不再注册到 `LATEST_FILES`）。
10. `.bridge list` 改为分列显示：文件和链接分两条消息发送，支持 `file`/`link`/`history` 筛选参数。
11. 所有"下载链接"后追加"（仅骰主可用）"标注。
12. 新增 `goal-ALL` 翻译模式：将全文分句后逐块翻译，每10秒将进度上传至 TextDB.online 云数据库（在线查看链接见翻译结果），支持 `.halt` 暂停。
13. 更新 README 与 ARCHITECTURE 文档至 v4.4.0。

**v4.4.1**
1. `.translate` 参数顺序调整：`[goal-ALL]` 优先于 `[lang]`，再跟文件/链接。goal-ALL 模式下翻译启动时即发送 TextDB 在线查看链接。
2. `.translate goal-ALL` 模式不再有超时限制，将持续执行直到完成或被 `.halt` 停止。
3. `.halt` 权限下放：所有群成员均可使用，不再仅限骰主。
4. `.bridge list` 文件/链接条目附带内容开头预览字符；以合并聊天记录格式呈现。
5. Web 管理界面地址改用内网 IP（而非 127.0.0.1），彻底移除输出中的所有 127.0.0.1。
6. 新增 `.bridge master` 命令：单独查看 Web 管理界面地址。`.bridge list` 不再附带 Web UI 链接。
7. 日志输出净化：NapCat 文件上传日志中不再打印完整 base64 文件内容，改为截断标记。
8. `.bridge get` 直接发送纯文本文件到群，不再附带下载链接。
9. 修复 TextDB 云数据库接口（改用 `/api/write` 端点），确认仅用于 goal-ALL 翻译功能。
10. 更新版本号至 v4.4.1。

**v4.4.2**
1. 删除所有用户侧的下载链接功能：AI 分析、翻译、logutil 等结果不再附带下载链接，文件直接通过群文件交付。
2. 消息中的染色器链接自动缓存：用户发送到群内的 log 链接（weizaima/dice.zone/kokona/trpgbot 等）自动拉取纯文本并加入桥接 `[link]` 列表，无需手动指令。
3. 更新版本号至 v4.4.2。

**v4.4.3**
1. `[history]-N` 全面支持：`.aiutil`、`.translate`、`.模组分析/备团/完善`、`.logutil` 复合命令及录音中独立发送，均支持 `[history]-N` 引用被淘汰的桥接历史项。
2. 修复 `bridge_link` 在复合命令中静默失效的问题：`.logutil` 复合命令现在正确解析 `[link]-N` 引用（此前因来源白名单遗漏，被当作原始文本处理）。
3. `.模组分析/备团/完善` 新增 `[link]-N` 和 `[history]-N` 引用支持。
4. 修复独立版前端（instance/logutil/frontend.js）在复合命令中未传递 `raw` 修饰符的问题。
5. `/api/bridge_list` 在默认 `all` 模式下同时返回历史记录（按群号过滤），前端无需额外请求。
6. 独立版后端的 `/bridge/list` 端点新增历史记录返回。
7. 更新版本号至 v4.4.3。

**v4.5.0**
1. 修复 `.bridge get [link]-N`/`[history]-N` 返回文件名含 URL 的问题，现使用干净文件名。
2. 修复跨群数据泄露：所有历史记录访问点增加 `group_id` 过滤隔离。
3. 修复历史记录列表中 `[link]` 字样重复显示的问题（name 字段移除 `[link] ` 前缀）。
4. 修复 SealDice 去括号导致 `.aiutil` 等命令中 `[link]-N`/`[history]-N` 无法识别的问题。
5. 修复 logutil 复合命令与独立发送中不识别短别名（`F14`/`L0`/`H23`）的问题：后端新增 `expand_short_alias`，前端重分词后补做别名展开。
6. 修复 `raw` 修饰符三层失效问题：DB 行确保（`ensure_logutil_group_state`）、复合 op 解析（直接 `make_log_item`）、WS 消息处理（检查 `raw_recording` 标记）。
7. 修复 `del_paren` 等修饰符在复合命令中被当作文本 op 发送给后端的问题。
8. 修复 `.aiutil get_text` 模式下 txt 文件可能因竞态条件而丢失的问题。
9. 修复 `.bridge get [history]-N` 与 `.bridge list history` 显示的编号不一致（全局索引→顺序计数器）。
10. 新增 URL 黑名单：QQ 多媒体 CDN（`multimedia.nt.qq.com.cn`）不再自动缓存。
11. 新增跨群访问语法：`L0-群号`/`[link]-0-群号` 等，可访问其他群的桥接缓存项。
12. `.bridge list` 全部采用合并聊天记录格式（含单条记录时），新增 `all` 参数同时显示文件/链接/历史。

—————————
**4.5.0.0**
1. 修复 `.bridge get [link]-N` / `[history]-N` 返回文件名含 URL 的问题，现使用干净文件名。
2. 修复跨群数据泄露：所有历史记录访问点增加 `group_id` 过滤隔离。
3. 修复历史记录列表中 `[link]` 字样重复显示的问题。
4. 修复 SealDice 去括号导致 `.aiutil` 等命令中 `[link]-N` / `[history]-N` 无法识别的问题。
5. 新增 URL 黑名单：QQ 多媒体 CDN（`multimedia.nt.qq.com.cn`）不再被自动缓存为 bridge link。
6. 修复 logutil 复合命令与独立发送中不识别短别名（`F14`/`L0`/`H23`）的问题：后端新增 `expand_short_alias`，前端重分词后补做别名展开。
7. 修复 logutil 复合命令中 `raw` 修饰符仅在开头两个位置生效的问题，现可在任意位置使用。
8. 更新版本号至 v4.4.4。

**4.5.0.1**
1. 修复 `raw` 修饰符在复合命令中仍可能被误判为日志标题的问题（`isOp` 检查显式列出修饰符）。
2. 修复 `.aiutil get_text` 模式下 txt 文件可能因竞态条件而丢失的问题（`status='done'` 移至 `get_text` 块之后）。
3. 修复 `.bridge get [history]-N` 与 `.bridge list history` 显示的编号不一致（因全局/过滤后索引错位）的问题。
4. 新增跨群访问语法：`L0-群号` / `[link]-0-群号` 等，可访问其他群的桥接缓存项。
5. `.bridge list` 全部采用合并聊天记录格式（含单条记录时），新增 `all` 参数同时显示文件/链接/历史。
6. 更新版本号至 4.5.0.1。

**v4.5.0.2**
1. 修复 `raw` 修饰符在复合命令中无效的根因：`api_logutil_compound` 未在调用 `parse_structured_text_to_items` 前检查 `raw_mode`；WS 独立消息（`[file]`/`[link]`/`[history]`）处理同样未检查 `raw_recording`；新增 `ensure_logutil_group_state` 确保 DB 行存在后再 UPDATE。
2. 修复 `del_paren` 等修饰符在复合命令中被当作文本 op 发送给后端的问题（ops filter 白名单补全）。
3. 更新版本号至 v4.5.0.2。

**v4.5.1**
1. 跨群访问语法全面生效：`H0-群号` / `[history]-0-群号` 等格式所有场景可用。
2. `.translate` 新增简写：`ALL` 等价于 `goal-ALL`。
3. Web 管理界面改造：群号输入框改为下拉框，自动列出全部有数据的群组，通过链接访问时默认打开对应群组。
4. 更新版本号至 v4.5.1。

**v4.5.1.1**
1. 修复 WebUI 路由冲突导致无法显示数据的问题（`/api/bridge_gui_data` 装饰器错误绑定到 `api_bridge_groups`）。
2. 修复 WebUI 与 `bridge list` 历史记录编号不一致的问题：WebUI 现在同样按 `group_id` 过滤并使用顺序编号。
3. 修复主后端 `/bridge/list` 未返回历史记录的问题（此前仅实例版修复），解决 `.translate` 等前端命令无法解析 `[history]-N` 的 bug。
4. 更新版本号至 v4.5.1.1。

**v4.5.3**
1. 修复着色器链接分词漏洞：`format_weizaima_text` 改用方括号管道格式 `[time] <nick|IMUserId> msg` 输出，保留 IMUserId 与 time 字段；格式 b 输出 `<玩家|游戏外>`；格式 c 星号动作不加冒号直接拼接。同步至主路径。
2. DOCX 文本提取：`extract_text_from_group_file` 改为按段落聚合文本 run，消除逐字换行；`extract_text_from_file` 段落 strip 并过滤空段。PDF 页文本同样 strip 过滤。
3. `.bridge list` 无参数时不再返回历史记录（仅 file+link），`all` 参数仍同时显示全部；文件/链接表均空时分别提示而非仅提示文件。
4. 新增 `.logai help`、`.logutil help`、`.aiutil help`、`.translate help`、`.bridge help` 指令，返回各命令用法及全部参数/修饰符说明。
5. 实例版默认端口改为 8001（与主版本 8000 分离）；前端错误输出优化（含 URL/HTTP 状态码/原始响应）；注册名改为 `logutil` 避免与主前端冲突。
6. 更新版本号至 4.5.3。

**v4.5.4**
1. 所有文件输出前自动截断超长文件名（>30字符保留扩展名）。
2. 更新版本号至 4.5.4。

**v4.6.1**
1. 修复 `.logutil end` 被误判为复合命令的问题：`isCompound` 不再仅因 `end` 存在而触发。
2. 移除纯冒号格式（`Name: text`）及含时间戳冒号格式（`time Name: text`）的发言识别，防止一般文本中冒号被误判为发言头。
3. 移除仅含标点文本被识别为发言者名称的漏洞（`looks_like_speaker_name` 要求至少一个字母/数字/中文）。
4. 更新版本号至 4.6.1。

**v4.6.0**
1. 新增倒数别名 `F-1`/`L-1`/`H-1` 等（`-1`=最新，`-2`=倒数第2），使用 `~` 内部标记为反向索引。支持跨群访问（`F-1-群号`）。全部功能（`.logai`、`.aiutil`、`.logutil`、`.bridge`、`.translate`、模组命令、复合命令、WS 独立消息）均支持。
2. 对 fwlog/logutil 新增 `-t` 修饰符：携带时生成的日志按原消息的发送时间戳排序（适用于 `.logutil end`、`.logutil get`、复合命令末尾）。
3. 桥接缓存保存原始下载链接（`download_url` 字段）。
4. 更新版本号至 4.6.0。

