## v4.2.0 更新公告

**LogAI v4.2.0** 现已发布！本次更新聚焦于易用性增强、命令扩展与关键Bug修复。

---

### 🆕 新增命令

#### `.ai` — 快速AI分析
```
.ai [file1] …… [fileN] prompt [pro] [get_text]
```
- 将桥接缓存文件提交给AI进行自定义分析，无需预先保存配置。
- 文件仅支持 `[file]-N` 编号格式（`.bridge list` 查看编号）。
- 不提供文件时，仅将提示词交给AI，不携带任何文件。
- 等价于 `.logai 配置 新建 test prompt` → `.logai test [files]`，但该临时配置**不会被保存**。
- 示例：`.ai [file]-0 [file]-1 请总结这两个日志的关键情节 pro`

#### `.translate` — 文件翻译
```
.translate [target_lang=中文] [file1] …… [fileN]
```
- 调用AI翻译接口，对 docx/pdf/txt 等格式文件进行翻译。
- 文件仅支持 `[file]-N` 编号格式。
- 默认翻译目标语言为**中文**。
- 示例：`.translate 英文 [file]-0`  `.translate 日文 [file]-1 [file]-2`

---

### ✨ 新增修饰符

#### `get_text` — 文本输出模式
- 针对所有生成图片的指令：`.logai`、`.ai`、`.模组分析/备团/完善`。
- 携带 `get_text` 时，AI分析结果**不再以图片方式给出**，而是将全部输出放在一个txt文件中，并发送该文件的下载链接。
- 示例：`.logai pro get_text`  `.ai [file]-0 总结 pro get_text`

---

### 🔧 功能增强

#### 模组命令多文件支持
`.模组分析`、`.模组完善`、`.模组备团` 现在支持指定文件：
```
.模组分析 [file1] …… [fileN] [配置名] [pro] [ai] [主题] [get_text]
```
- 支持 `[file]-N` 编号格式
- 支持文件名及其部分匹配
- 不指定文件时回退到最新上传群文件（保持向后兼容）
- 机制完全与 `.logai` 对齐

#### 后端端口可配置
- 新增海豹配置项 **"Python后端服务端口"**（`registerIntConfig`，默认8000）
- 前端所有请求地址不再硬编码端口号，修改端口配置后即时生效

---

### 🐛 Bug修复

#### `del_paren` 去括号逻辑完善
- 修复了方括号管道格式 `[2024-01-15 14:30:22]<角色名|玩家昵称>内容` 产生的括号内容未能正确删除的漏洞。
- 完善了多行括号块的闭合检测：追踪到匹配的右括号后才停止删除，不再误删后续非括号内容。
- 修复了 `logutil get` 操作未正确传递 `del_paren` 参数的问题——现在 `get` 也能正确处理去括号。

---

### 📋 升级指南

1. 将前端 `logai_logutil_frontend_4_1_0.js` 替换为 v4.2.0 版本。
2. 将后端 `logai_server_release.py` 替换为新版本。
3. 重启海豹与后端服务即可。
4. （可选）在海豹配置中调整 "Python后端服务端口" 以匹配你的后端端口。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
