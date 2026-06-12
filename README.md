# logutil & logai
**V4.3.2**  
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
   - windows: `run_logai.bat`（未经过测试）
   （安卓请在termux内执行sh文件）
   随后，如文件对应的注释般，编辑其配置部分；
   该脚本会自动执行缺失依赖的补齐并最终启动。

## 注意事项：
- 本插件缺乏足够的测试，且未经检验其跨平台能力。
- 测试中使用linux环境，故windows下可能存在未经发现的漏洞。
- 可能有任何bug出现，届时请在github上进行反应。
- 修改时为vibe coding，可能含有ai创作的差代码。
- 本插件只适用于napcat分离部署架构，其余架构大概率不可用，需要自行修改插件源码。

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
（`.ai` 作为别名保留，向下兼容。）

`.模组分析 [file1] …… [fileN] [配置名] [pro] [ai] [主题] [get_text]`  
分析模组文件。支持 `[file]-N` 编号、文件名及其部分匹配。不指定文件时默认使用最新群文件。

`.模组备团`、`.模组完善` 同上，均完整支持多文件指定。

---

### 二、.logutil功能
以下全部logutil均可用fwlog代替。

`.logutil new [名称]`  开始新日志记录  
`.logutil on [名称]`   继续记录已有日志  
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
`.bridge list`  返回群文件桥接系统所缓存的全部群文件及其编号列表。  
`.bridge get <N>`  返回编号为N的文件转纯文本后的txt文档下载链接。

可通过输入 `[file]-N` 来调取编号为N的文件。  
编号从旧到新排列，以0开端，最多缓存19个文件。当接收到的群文件总数目大于19时，则第0个文件将被第1个文件覆盖，以此类推，而 `[file]-19` 成为刚接收到的文件。

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
`.translate [target_lang=中文] [file1] …… [fileN]`  调用翻译接口，对docx/pdf/txt等格式文件进行翻译，返回翻译后的文件。file1-fileN 只能是 `[file]-N` 的格式。默认目标语言为中文。

---

### 五、复合命令
**示例**: `.logutil new [file]-0 [file]-1 链接 end logai 评分 pro`

将新建日志，追加缓存中索引 0、1 的文件内容，追加一个日志链接内容，结束记录，然后对生成文件执行 AI 分析，采用自定义配置：评分，pro模式。  
暂不支持在复合命令中提供log名称。复合命令的 end 阶段支持 `del_paren` 修饰符。

### 六、修饰符说明
**`del_paren`**：当 logai/logutil 带有标记 `del_paren` 时，将所有在调整后被中文括号（）或英文括号()括起来的"行"，以及以中文/英文左括号开头的段落（一次引导语的出现称为一段，一个换行符称为一句），从用于AI分析的纯文本/输出的着色器链接/输出的txt文件中删除。  
适用于：`.logutil end`、`.logutil get`、复合命令。

**`get_text`**：针对所有生成格式是一张图片的指令（如 `.logai`、`.aiutil`、模组命令）。在此修饰被带有时，输出不再以图片方式给出，而是把全部AI输出放在一个txt文件里，并发出该文件及其下载链接。

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

**v4.2.0**  
1. 新增 `.aiutil` 命令：快速AI分析，仅支持 `[file]-N` 格式文件，不保存配置。无文件时仅将提示词交给AI。
2. 新增 `.translate` 命令：调用AI翻译接口，对docx/pdf/txt格式文件进行翻译。
3. 新增 `get_text` 修饰符：图片输出指令可改为txt文件输出。
4. 完善 `del_paren` 修饰符：修复括号段落逻辑漏洞，现已完整奏效。追加到 `logutil get` 的支持。
5. 模组命令（分析/完善/备团）全面支持多文件与指定文件，机制与logai完全对齐。
6. 后端端口号改为海豹配置项，默认8000，可即时调整。

**v4.3.0**  
1. 文件输入扩展：`.aiutil` 及一切输入文件的命令，提供文件允许为任何可以被展开成文本的文件（.py, .c, .js, .class 等）。
2. 新增 `.bridge get N`：将该编号文件桥接为纯文本后的txt文档直接给出下载链接。
3. 文件大小上限从50MB/40MB统一提升至150MB。
4. 修复 `.aiutil` 无文件模式下混杂 `.logai` 评分prompt的bug，现使用中性AI助手系统提示。

**v4.3.1**  
1. 修复大文件（如59MB PDF）通过 `.bridge get` 和 `.aiutil` 提取失败的问题：`.bridge get` 使用正确的含 `content_url` 的接口；`.aiutil` 改走 `raw_url` 模式避免重复解析已提取的桥接文本。
2. 修复 `.py`、`.js`、`.c` 等非白名单扩展名文件报"不支持的文件格式"的问题：未知扩展名现在直接当作文本解码。
3. 修复 `FileBridgeMode=0`（WS实时推送模式）下仍启动HTTP轮询的问题：WS模式下彻底禁用HTTP轮询。

**v4.3.2**  
1. 修复 `.bridge get`、`.aiutil get_text`、`.translate` 三个功能只发送下载链接而未实际通过 NapCat 上传文件到群的问题。现已统一使用与 `.logutil end` 相同的 NapCat `/upload_group_file` 机制，参考该处实现新增 `/api/send_file_to_group` 通用上传接口。
