# v4.4.3 全覆盖测试指令列表

> 测试服务器: `http://127.0.0.1:18993`  
> 测试群号: 替换 `GROUP_ID` 为实际群号  
> 标记 ⭐ = 本次 v4.4.3 修复点，必须通过

---

## 一、Bridge 桥接系统

### 1.1 基础状态
```
.bridge status
.bridge on
.bridge off
.bridge rate 10
```

### 1.2 列表查看
```
.bridge list
.bridge list file
.bridge list link
.bridge list history
```

### 1.3 获取文件 ⭐
```
.bridge get [file]-0
.bridge get [link]-0
.bridge get [history]-0          ⭐ 新增
.bridge get F0                    # = [file]-0
.bridge get L0                    # = [link]-0
.bridge get H0                    ⭐ = [history]-0
```

### 1.4 删除项目 ⭐
```
.bridge del [file]-0
.bridge del [link]-0 [history]-1  ⭐ 混合删除
.bridge del [history]-0           ⭐ 单独删历史
```

### 1.5 Web 管理
```
.bridge master                    # 返回内网 IP，非 127.0.0.1
```

---

## 二、.logutil 日志录制（简单模式）

### 2.1 基本生命周期
```
.logutil new test_session
.logutil off
.logutil on test_session
.logutil list
.logutil end test_session
.logutil get test_session
.logutil clear test_session
```

### 2.2 raw 修饰符
```
.logutil new raw_session raw
（在录制中发几条任意消息）
.logutil end raw_session del_paren
```

### 2.3 del_paren 修饰符
```
.logutil new paren_test
（在录制中发几条消息）
.logutil end paren_test del_paren
```

### 2.4 录制中独立发送引用 ⭐
```
.logutil new standalone_test
[file]-0                          # 独立发送，追加桥接文件
[link]-0                          # 独立发送，追加链接文本
[history]-0                       ⭐ 独立发送，追加历史记录
F1                                # 短别名 = [file]-1
L1                                # 短别名 = [link]-1
H1                                ⭐ 短别名 = [history]-1
.logutil end standalone_test
```

### 2.5 录制中发送染色器链接
```
.logutil new link_test
（在群内发送一个 weizaima/dice.zone 染色器链接）
.logutil end link_test
```

---

## 三、.logutil 复合命令 ⭐

### 3.1 基础复合
```
.logutil new [file]-0 [file]-1 end
.logutil new [link]-0 end         ⭐ bridge_link 白名单修复
.logutil new [history]-0 end      ⭐ bridge_history 新增
```

### 3.2 混合引用复合
```
.logutil new [file]-0 [link]-0 [history]-0 end         ⭐ 三种引用混合
.logutil new F0 L0 H0 end                              ⭐ 短别名混合
```

### 3.3 带修饰符复合
```
.logutil new raw [file]-0 end                           ⭐ raw 传递修复
.logutil new [file]-0 [link]-0 end del_paren
.logutil new raw [file]-0 end logai pro
```

### 3.4 复合 + AI 分析
```
.logutil new [file]-0 [link]-0 end logai 评分 pro
.logutil new [history]-0 end logai 总结                ⭐
```

---

## 四、.aiutil 快速分析 ⭐

### 4.1 文件引用
```
.aiutil [file]-0 请总结这个文件
.aiutil [file]-0 [file]-1 请对比这两个文件
```

### 4.2 链接引用 ⭐（确认可用）
```
.aiutil [link]-0 请分析这个链接内容
```

### 4.3 历史引用 ⭐ 新增
```
.aiutil [history]-0 请总结这段历史记录
```

### 4.4 短别名
```
.aiutil F0 请总结
.aiutil L0 请分析
.aiutil H0 请总结                                       ⭐
```

### 4.5 混合引用
```
.aiutil [file]-0 [link]-0 [history]-0 请综合总结         ⭐
```

### 4.6 修饰符
```
.aiutil [file]-0 请总结 pro
.aiutil [file]-0 请分析 get_text
.aiutil 请回答这个问题                                    # 无文件，纯提示词
```

---

## 五、.logai 分析

### 5.1 引用类型
```
.logai [file]-0
.logai [file]-0 [file]-1                                # 多文件
.logai [link]-0
.logai [history]-0                                       ⭐
.logai [file]-0 [link]-0 [history]-0                     ⭐
```

### 5.2 修饰符组合
```
.logai [file]-0 pro
.logai [file]-0 pro 温柔
.logai [file]-0 ai 提取NPC
.logai [file]-0 get_text
```

### 5.3 配置管理
```
.logai 配置 列表
.logai 配置 添加 test_config 你是一个专业的TRPG分析助手
.logai 配置 查看 test_config
.logai test_config [file]-0
.logai 配置 删除 test_config
```

### 5.4 默认文件
```
.logai                                                    # 无参数，使用最新群文件
.logai pro 温柔
```

---

## 六、.translate 翻译 ⭐

### 6.1 引用类型
```
.translate [file]-0
.translate [link]-0
.translate [history]-0                                   ⭐
.translate [file]-0 [link]-0                             # 多文件
.translate [file]-0 [link]-0 [history]-0                 ⭐
```

### 6.2 目标语言
```
.translate 英文 [file]-0
.translate 日文 [file]-0
.translate 中文 [file]-0                                 # 默认
```

### 6.3 goal-ALL 模式
```
.translate goal-all 英文 [file]-0                        # 启动时即发 TextDB 链接
.translate goal-all 日文 [file]-0 [link]-0
```

### 6.4 短别名
```
.translate F0 英文
.translate goal-all L0
.translate H0                                            ⭐
```

---

## 七、.模组分析 / .模组备团 / .模组完善 ⭐

### 7.1 引用类型
```
.模组分析 [file]-0
.模组分析 [link]-0                                       ⭐ 新增
.模组分析 [history]-0                                    ⭐ 新增
.模组分析 [file]-0 [link]-0                              ⭐
.模组分析 [file]-0 [link]-0 [history]-0                  ⭐
```

### 7.2 短别名
```
.模组分析 F0
.模组分析 L0                                             ⭐
.模组分析 H0                                             ⭐
```

### 7.3 其他模组命令
```
.模组备团 [file]-0
.模组备团 [link]-0 [history]-0                           ⭐
.模组完善 [file]-0
.模组完善 [file]-0 [link]-0                              ⭐
```

### 7.4 带配置
```
.模组分析 [file]-0 默认配置 pro
.模组备团 [file]-0 温柔
.模组分析 [file]-0 get_text
```

---

## 八、.halt 停止任务

```
.halt                                                     # 所有用户可用
（启动一个长时间 AI 任务后执行 .halt 验证能停止）
```

---

## 九、防刷屏机制

```
.logai（连续快速发送 7 次）
# 第 7 次应被拦截，提示等待
```

---

## 十、自动链接缓存

```
（在群内发送染色器链接，如 https://log.weizaima.com/?key=xxx#123）
.bridge list link                                         # 验证链接出现在列表中
```

---

## 十一、WebSocket 配置

```
.logutil wsconfig
.logutil wsconfig ws://127.0.0.1:3001 token123 on
.logutil wsconfig ws://127.0.0.1:3001 token123 off
```

---

## 十二、回归验证（v4.4.1/v4.4.2 修复点）

| # | 测试 | 验证点 |
|---|------|--------|
| 1 | `.translate goal-all 英文 [file]-0` | 启动时立即收到 TextDB 在线查看链接 |
| 2 | `.halt` | 非骰主用户可执行 |
| 3 | `.bridge master` | 返回内网 IP，无 127.0.0.1 |
| 4 | `.bridge list` | 合并聊天记录格式，含内容预览 |
| 5 | `.bridge get [file]-0` | 直接发文件到群，无下载链接 |
| 6 | 所有 `.logai` / `.aiutil` / `.translate` 结果 | 无"下载链接"字样 |
| 7 | 后端日志 | 无完整 base64 内容 |

---

## 快速批量测试（curl）

```bash
HOST="http://127.0.0.1:18993"
GID="TEST_GROUP_ID"

# bridge list (all three types returned)
curl -s "$HOST/api/bridge_list?group_id=$GID" | python -m json.tool

# bridge list history only
curl -s "$HOST/api/bridge_list?group_id=$GID&filter=history" | python -m json.tool

# bridge master  
curl -s "$HOST/api/bridge_master?group_id=$GID"

# bridge get [history]-0
curl -s -X POST "$HOST/api/bridge_get" -H "Content-Type: application/json" \
  -d "{\"group_id\":\"$GID\",\"ref\":\"[history]-0\"}"

# bridge del [history]-0
curl -s -X POST "$HOST/api/bridge_del" -H "Content-Type: application/json" \
  -d "{\"group_id\":\"$GID\",\"targets\":[\"[history]-0\"]}"

# logutil compound with all three ref types
curl -s -X POST "$HOST/api/logutil_compound" -H "Content-Type: application/json" \
  -d "{\"group_id\":\"$GID\",\"new\":true,\"end\":true,\"ops\":[\"[file]-0\",\"[link]-0\",\"[history]-0\"],\"nickname\":\"Test\",\"user_id\":\"0\"}"

# halt
curl -s -X POST "$HOST/api/halt" -H "Content-Type: application/json" \
  -d "{\"group_id\":\"$GID\"}"
```
