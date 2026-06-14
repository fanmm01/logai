# logutil & logai
**V4.4.3**  
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
- 可能有任何bug出现，届时请在github上进行反映。
- 修改时为vibe coding，可能含有ai创作的差代码。
- 本插件已知适用于napcat分离部署架构。确认了LLBot下基础功能的可用性。
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
   - 文件名；
   - 不引起歧义的文件名的一部分；
   - 文件编号。
   其中，文件编号指 `[file]-N` 格式，意思是调取编号为N的文件。文件编号可以通过 `.bridge list` 查看。

**示例**: `.logai https://weizaima.com/dice/api?key=xxx pro 温柔 提取NPC`

**修饰符 `get_text`**：适用于所有生成图片的指令（`.logai`、`.aiutil`、模组命令）。携带时，输出不再以图片方式给出，而是全部AI输出放入一个txt文件中，并发出该文件及其下载链接。

`.logai 配置 添加 <名称> <提示词内容>`  
创建自定义分析配置，之后可用 `.logai <名称>` 调用  
`.logai 配置 删除 / 列表 / 查看`  
管理配置

`.aiutil [file1] …… [fileN] prompt [pro] [get_text]`  
将桥接缓存中的文件提交给AI进行分析，提示词为输入的`prompt`。支持任何可展开为文本的文件格式（.py, .c, .js, .txt, .log 等）。  
file1-fileN 只能是 `[file]-N` 的格式，而不能是文件名及其部分。  
在不提供任何文件时，仅把prompt交给AI，不携带文件。  
等价于 `.logai 配置 新建 test prompt` + `.logai test [file1] …… [fileN]`，但该临时配置不会被保存。

`.模组分析 [file1] …… [fileN] [配置名] [pro] [ai] [主题] [get_text]`  
分析模组文件。支持 `[file]-N` 编号、文件名及其部分匹配。不指定文件时默认使用最新群文件。

`.模组备团`、`.模组完善` 同上，均完整支持多文件指定。

---

### 二、.logutil功能
以下全部logutil均可用fwlog代替。

`.logutil new [名称] [raw]`  开始新日志记录（raw: 跳过消息头解析，直接拼接原始文本）  
`.logutil on [名称] [raw]`   继续记录已有日志  
`.logutil off`         暂停记录  
`.logutil end [名称] [del_paren]`  结束并发送文件与染色器链接。del_paren参数可去除括号包裹的内容（见修饰符说明）。  
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
此外，`[file]-N` 命令可直接在群内发送，将桥接缓存文件内容追加到当前正在记录的 logutil 日志中。

logutil支持识别以下种类的消息头：
<角色名>: 发言内容；

角色名: 发言内容；

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
`.bridge list [file|link|history]`  以合并聊天记录格式返回桥接缓存列表（v4.4.1更新），文件和链接条目附带内容开头预览字符。支持分类筛选，默认同时显示文件和链接。  
`.bridge master`  (v4.4.1新增) 查看 Web 管理界面地址。  
`.bridge get [file]-N/[link]-N/[history]-N`  获取编号文件并直接发送到群（纯文本，v4.4.1起不再返回下载链接）。  
`.bridge del [file]-N [link]-N ...`  删除指定编号的桥接项，索引自动顺延。

通过输入 `[file]-N` 调取编号为N的文件，通过 `[link]-N` 调取编号为N的链接文本（v4.4.0新增）。  
也支持短别名 `F14`=`[file]-14`, `L0`=`[link]-0`, `H23`=`[history]-23`（v4.4.0新增）。  
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
`.translate [goal-ALL] [target_lang=中文] [file/link1] …… [file/linkN]`  调用翻译接口，对docx/pdf/txt等格式文件进行翻译，返回翻译后的txt文件。`[file]-N` 调取桥接缓存文件，`[link]-N` 调取桥接缓存链接文本。默认目标语言为中文。翻译结果同时上传到群并保留下载链接。  
v4.4.1更新：参数顺序调整为 `goal-ALL` 优先于目标语言。`goal-ALL` 模式在翻译开始时即发送 TextDB.online 在线查看链接，且不再有超时限制，将持续翻译直到完成或被 `.halt` 停止。

`.halt`  强制停止当前群内所有进行中的AI生成任务（v4.4.1起所有用户可用，原仅限骰主）。

---

### 五、复合命令
**示例**: `.logutil new [file]-0 [file]-1 链接 end logai 评分 pro`

将新建日志，追加缓存中索引 0、1 的文件内容，追加一个日志链接内容，结束记录，然后对生成文件执行 AI 分析，采用自定义配置：评分，pro模式。  
暂不支持在复合命令中提供log名称。复合命令的 end 阶段支持 `del_paren` 修饰符。

### 六、修饰符说明
**`del_paren`**：当 logai/logutil 带有标记 `del_paren` 时，将所有在调整后被中文括号（）或英文括号()括起来的"行"，以及以中文/英文左括号开头的段落（一次引导语的出现称为一段，一个换行符称为一句），从用于AI分析的纯文本/输出的着色器链接/输出的txt文件中删除。  
适用于：`.logutil end`、`.logutil get`、复合命令。

**`get_text`**：针对所有生成格式是一张图片的指令（如 `.logai`、`.aiutil`、模组命令）。在此修饰被带有时，输出不再以图片方式给出，而是把全部AI输出放在一个txt文件里，并发出该文件及其下载链接。

**`raw`**：对 logutil 生效。携带时（如 `.logutil new raw`、`.logutil on raw`），跳过判断消息头的步骤，直接把各源文本按原始内容并行拼接入日志中。适用于导入非标准格式的纯文本日志。

---

### 七、防刷屏机制 (v4.4.0新增)
海豹配置项 `刷屏警告时限(s)`（默认60）与 `处理上限`（默认6）。
在指定秒数内，若某群中 `.logai` + `.aiutil` 调用次数超过上限，则拒绝后续请求并提示等待时间。

### 八、Web管理界面
v4.4.1起，通过 `.bridge master` 查看网页版管理地址（使用内网IP而非 127.0.0.1），可在浏览器中查看所有文件、链接、历史记录的表格（含内容开头预览），并支持输入命令。

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
