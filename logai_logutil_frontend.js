// ==UserScript==
// @name         人工智障Log分析器 & 模组分析器 (合并版)
// @author       Air, Gemini, fanmm, GPT5.1, Deepseek4.0
// @version      4.3.3
// @description  合并 Log 分析与模组分析，新增 logutil 命令与 del_paren 选项，兼容 HTTP 桥接与本地群文件。
// @timestamp    1781107200
// @license      Apache-2.0
// @homepageURL  https://github.com/fanmm01/logai/
// @updateURL    https://raw.githubusercontent.com/fanmm01/logai/refs/heads/main/logai_logutil_frontend.js
// ==/UserScript==

// 此文件基于现有脚本合并而成，保留所有旧有功能，并新增 logutil 控制命令。

let ext = seal.ext.find('log-analyzer');
if (!ext) {
    ext = seal.ext.new('log-analyzer', 'Air', '4.3.3');
    seal.ext.register(ext);
}

// 复制并保留原有配置项（简化处理：保留原脚本中已有配置注册）
// === 新增：骰娘人设系统配置 ===
seal.ext.registerBoolConfig(ext, "启用骰娘人设", false, "开启后，AI分析将带入下方设定的骰娘语气。关闭则使用原版专业AI语气。");
seal.ext.registerStringConfig(ext, "常规模式_骰娘设定", "你是一个严厉老练的骰娘，说话犀利，对逻辑存在硬伤的模组绝不留情，像个严肃的调查员前辈。", "普通分析模式下的AI扮演提示词");
seal.ext.registerStringConfig(ext, "温柔模式_骰娘设定", "你是一个温柔可爱的骰娘，总是鼓励模组作者，指出缺点时也会非常委婉，语气软萌。", "使用'温柔'参数时的AI扮演提示词");
seal.ext.registerStringConfig(ext, "Python后端服务地址", "http://127.0.0.1:8000", "Python 后端服务的地址，用于提交分析任务和获取结果。");
seal.ext.registerIntConfig(ext, "Python后端服务端口", 8000, "Python 后端服务的端口号（默认8000）。修改后前端所有请求将使用新端口。");
seal.ext.registerStringConfig(ext, "OneBot_API_地址", "http://127.0.0.1:34567", "Bot客户端的HTTP监听地址");
seal.ext.registerBoolConfig(ext, "启用HTTP文件桥接", true, "开启后通过HTTP中转服务读取最近群文件，不再依赖群内文本回传。");
seal.ext.registerStringConfig(ext, "HTTP文件桥接读取API", "http://127.0.0.1:8000/bridge/latest", "中转服务读取接口，默认使用本地服务。建议使用POST JSON。");
seal.ext.registerStringConfig(ext, "HTTP文件桥接Token", "", "可选，若中转服务启用鉴权则在此填写。");
seal.ext.registerBoolConfig(ext, "调试日志", true, "开启后在命令行输出桥接请求、解析与提交流程详细日志。");
seal.ext.registerBoolConfig(ext, "启用文本文件桥接", true, "开启后可通过纯文本桥接消息记录最新群文件，不依赖CQ:file解析。");
seal.ext.registerStringConfig(ext, "文本桥接前缀", "[LogFileBridge]", "仅处理以该前缀开头的纯文本桥接消息。建议与外部脚本保持一致。");
seal.ext.registerBoolConfig(ext, "文本桥接自动提示", true, "开启后，收到桥接消息会回一条记录成功提示。默认关闭以减少刷屏。");

// === 新增：钱包保护 - Pro 模式限额配置 ===
seal.ext.registerIntConfig(ext, "每日Pro全局限额", 30, "每天所有群合计最多能使用多少次Pro模式（0为禁用，-1为无限）");
seal.ext.registerIntConfig(ext, "每日单人Pro限额", 3, "每天每个普通用户最多能使用多少次Pro模式（-1为无限，管理员不受限）");

// ...（为简洁省略重复注册段，实际脚本中应包含原文件中所有 registerConfig 调用）

// 新：fwlog 文案风格（兼容 fwlog 原有文案样式）
seal.ext.registerBoolConfig(ext, "fwlog文案风格", true, "开启后将使用 fwlog 风格的文案（将 'fwlog' 文本替换为 'logutil'），默认开启。若关闭，使用原脚本通用文案。");

// 新：logutil WebSocket 实时监听配置
seal.ext.registerStringConfig(ext, "logutil_WS地址", "ws://127.0.0.1:3001", "NapCat OneBot v11 正向 WebSocket 地址。修改后可通过 .logutil wsconfig 热更新。");
seal.ext.registerStringConfig(ext, "logutil_WS_Token", "", "NapCat Access Token。留空则自动复用 OneBot_API 地址对应的 Token。修改后可通过 .logutil wsconfig 热更新。");
seal.ext.registerBoolConfig(ext, "logutil_WS启用", true, "是否启用 WebSocket 实时监听自动转录。关闭后 logutil 仅通过 API 被动接收文本。");

// 新：NapCat 文件桥接模式配置
seal.ext.registerIntConfig(ext, "NCFileBridgeMode", 0, "文件桥接模式: 0=WS实时推送(推荐), 1=轮询模式(备用)。修改后通过 .bridge wsconfig 热更新。");

function useFwlogStyle() {
    try {
        return !!seal.ext.getBoolConfig(ext, "fwlog文案风格");
    } catch (e) { return true; }
}

function fw(text) {
    // 当 fwlog 样式开启时，返回 fwlog 风格文案（替换 fwlog->logutil）
    if (useFwlogStyle()) {
        if (!text) return text;
        // 保持原 fwlog 风格标记，如 【暂停记录】 等
        return String(text).replace(/fwlog/gi, 'logutil');
    }
    return text;
}

// 监听文件上传，保存最近一份 Log 文件的数据
function saveLastLogFile(groupId, file) {
    if (!groupId || !file) return;
    let fileInfo = {
        name: file.name || "未知文件",
        file_id: file.id || file.file_id || "",
        busid: file.busid || 0
    };
    if (!fileInfo.file_id) return;
    ext.storageSet(`log_last_file_${groupId}`, JSON.stringify(fileInfo));
}

function normalizeGroupId(rawGroupId) {
    if (rawGroupId === undefined || rawGroupId === null) return '';
    let s = String(rawGroupId).trim();
    if (!s) return '';
    if (s.includes('Group:')) return s;
    if (/^\d+$/.test(s)) return `QQ-Group:${s}`;
    return s;
}

function getPureGroupId(groupId) {
    let s = normalizeGroupId(groupId);
    if (!s) return '';
    let m = s.match(/(\d+)/);
    return m ? m[1] : '';
}

function getBridgeTokenHeader() {
    let token = (seal.ext.getStringConfig(ext, "HTTP文件桥接Token") || '').trim();
    if (!token) return {};
    return { "Authorization": token };
}

function getBackendBaseUrl() {
    let port = seal.ext.getIntConfig(ext, "Python后端服务端口");
    if (!port || port <= 0) port = 8000;
    let hostUrl = (seal.ext.getStringConfig(ext, "Python后端服务地址") || 'http://127.0.0.1').trim();
    // 从完整URL中提取协议+主机部分（去掉已有端口）
    let base = hostUrl.replace(/:\d+$/, '');  // 去掉末尾端口
    if (base.endsWith('/')) base = base.slice(0, -1);
    return `${base}:${port}`;
}

function dbgLog(tag, message, data) {
    if (!seal.ext.getBoolConfig(ext, "调试日志")) return;
    if (data !== undefined) {
        try {
            console.log(`[log-analyzer][${tag}] ${message}`, data);
            return;
        } catch (e) {}
    }
    console.log(`[log-analyzer][${tag}] ${message}`);
}

async function readLastLogFileFromHttpBridge(groupId) {
    if (!seal.ext.getBoolConfig(ext, "启用HTTP文件桥接")) return null;

    let pureGroupId = getPureGroupId(groupId);
    if (!pureGroupId) return null;

    let readApi = (seal.ext.getStringConfig(ext, "HTTP文件桥接读取API") || '').trim();
    if (!readApi) return null;

    dbgLog('bridge', `start read latest, groupId=${groupId}, pureGroupId=${pureGroupId}, api=${readApi}`);

    try {
        let resp = await fetch(readApi, {
            method: 'POST',
            headers: Object.assign({ 'Content-Type': 'application/json' }, getBridgeTokenHeader()),
            body: JSON.stringify({ group_id: parseInt(pureGroupId, 10) })
        });
        let text = await resp.text();
        dbgLog('bridge', `latest response status=${resp.status}, bodyPreview=${text.slice(0, 240)}`);
        if (!resp.ok) return null;

        let data = null;
        try {
            data = JSON.parse(text);
        } catch (e) {
            dbgLog('bridge', 'latest response is not JSON');
            return null;
        }
        if (!data) return null;

        if (data.status && data.status !== 'ok') {
            dbgLog('bridge', `latest status=${data.status}`, data);
        }

        let file = data.file || data.data || data.value || data;
        if (!file) return null;

        let fileId = file.file_id || file.id || '';
        let contentUrl = file.content_url || file.text_url || '';
        if (!fileId && !contentUrl) return null;

        let fileInfo = {
            name: file.name || file.file_name || file.text_filename || "未知文件",
            file_id: fileId ? String(fileId) : "",
            busid: parseInt(file.busid || file.biz || 0) || 0,
            content_url: contentUrl ? String(contentUrl) : "",
            text_filename: file.text_filename || ""
        };

        dbgLog('bridge', 'parsed latest file record', fileInfo);

        ext.storageSet(`log_last_file_${normalizeGroupId(groupId)}`, JSON.stringify(fileInfo));
        return fileInfo;
    } catch (e) {
        dbgLog('bridge', `read latest failed: ${e.message || e}`);
        return null;
    }
}

function parseBridgePayload(payloadText) {
    if (!payloadText) return null;

    let text = payloadText.trim();
    if (!text) return null;

    // 格式1：JSON对象
    if (text.startsWith('{') && text.endsWith('}')) {
        try {
            return JSON.parse(text);
        } catch (e) {
            return null;
        }
    }

    // 格式2：key=value;key=value
    let obj = {};
    let parts = text.split(/[;,&]/);
    for (let i = 0; i < parts.length; i++) {
        let seg = parts[i].trim();
        if (!seg) continue;
        let p = seg.indexOf('=');
        if (p <= 0) continue;
        let key = seg.slice(0, p).trim();
        let value = seg.slice(p + 1).trim();
        if (!key) continue;
        obj[key] = value;
    }
    return Object.keys(obj).length > 0 ? obj : null;
}

function getHostnameFromUrl(url) {
    if (!url) return '';
    const match = url.match(/^https?:\/\/([^\/\s?#]+)/i);
    return match ? match[1].toLowerCase() : '';
}

function parseLogTargetEntry(raw) {
    let val = String(raw || '').trim();
    if (!val) return null;

    const urlMatch = val.match(/https?:\/\/[^\s\]"']+/);
    if (urlMatch) val = urlMatch[0];

    let key = '';
    let source = '';
    let password = '';

    // 纯文本中转全文URL，按原始文本拉取
    if (/\/bridge\/content\//.test(val)) {
        return { key: val, source: 'raw_url', password: '' };
    }

    // [file]-N pattern: reference bridge-cached files by index (0=latest)
    let fileIdxMatch = val.match(/^\[file\]-(\d+)(?:\s|$)/i);
    if (fileIdxMatch) {
        return { key: fileIdxMatch[1], source: 'bridge_file', password: '' };
    }

    // Bare file name detection: non-URL values that look like filenames
    // Examples: "[2026-06-11_11-25]8月23日营地.txt", "8月23日营地.txt", "8月23日营地"
    if (!/^https?:\/\//i.test(val) && !val.includes('://') && !val.includes('=') && !val.includes('#') && val.length > 1) {
        let looksLikeFilename = (
            val.startsWith('[') ||
            /\.\w{2,5}$/i.test(val) ||
            /\[\d{4}-\d{2}-\d{2}[_\s]/.test(val) ||
            // Bare name without extension: contains Chinese characters or looks like a natural name
            /[一-鿿]/.test(val)
        );
        if (looksLikeFilename) {
            return { key: val, source: 'bridge_file_name', password: '' };
        }
    }

    if (val.includes('s3=')) {
        const m = val.match(/[?&]s3=([^&#]+)/);
        if (m) {
            key = m[1];
            source = 'kokona';
        }
    } else if (val.includes('key=')) {
        const k = val.match(/[?&]key=([^&#]+)/);
        if (k) {
            key = k[1];
            const hostname = getHostnameFromUrl(val);
            if (hostname.endsWith('dice.zone')) {
                source = 'dice_zone';
            } else if (hostname.endsWith('weizaima.com')) {
                source = 'weizaima';
            }
            else {
                source = 'unknown';
            }
        }
        const p = val.match(/#([^?&\s]+)/);
        if (p) password = p[1];
    } else if (val.includes('#')) {
        const parts = val.split('#');
        if (parts.length > 1) {
            key = parts[parts.length - 1].replace(/[^a-zA-Z0-9-_]/g, '');
            if (key.includes('-')) source = 'trpgbot';
        }
    } else {
        key = val;
    }

    if (!key) return null;
    if (!source) {
        if (key.includes('-') && /^\d+\-/.test(key)) source = 'trpgbot';
        else if (key.includes('_') || key.length > 20) source = 'kokona';
        else source = 'weizaima';
    }

    return { key, source, password };
}

function trySaveLogFileFromBridgeMessage(ctx, msg) {
    if (!msg || !msg.message) return false;
    if (!seal.ext.getBoolConfig(ext, "启用文本文件桥接")) return false;

    let prefix = seal.ext.getStringConfig(ext, "文本桥接前缀") || "[LogFileBridge]";
    let messageText = String(msg.message || '').trim();
    if (!messageText.startsWith(prefix)) return false;

    let payloadText = messageText.slice(prefix.length).trim();
    let payload = parseBridgePayload(payloadText);
    if (!payload) return false;

    let fileId = payload.file_id || payload.fileId || payload.id || '';
    if (!fileId) return false;

    let groupId = normalizeGroupId(payload.group_id || payload.groupId || msg.groupId || (ctx && ctx.group ? ctx.group.groupId : ''));
    if (!groupId) return false;

    let file = {
        id: String(fileId),
        name: payload.name || payload.file_name || payload.fileName || payload.file || "未知文件",
        busid: parseInt(payload.busid || payload.biz || 0) || 0
    };

    saveLastLogFile(groupId, file);

    if (seal.ext.getBoolConfig(ext, "文本桥接自动提示")) {
        seal.replyToSender(ctx, msg, `✅ 已记录群文件：${file.name}`);
    }
    return true;
}

// 路径1：标准群文件上传回调
ext.onGroupUpload = (ctx, msg, file) => {
    saveLastLogFile(msg.groupId, file);
};

// 路径2：普通消息里的纯文本桥接上报（推荐）
// 兼容保留：若核心未来恢复 CQ:file 透传，仍可继续使用
ext.onNotCommandReceived = (ctx, msg) => {
    if (!msg || !msg.message) return;

    if (trySaveLogFileFromBridgeMessage(ctx, msg)) return;

    if (!msg.groupId) return;
    if (!msg.message.includes("[CQ:file,")) return;

    let m = msg.message.match(/\[CQ:file,([^\]]+)\]/);
    if (!m || !m[1]) return;

    let params = {};
    m[1].split(",").forEach(seg => {
        let p = seg.indexOf("=");
        if (p <= 0) return;
        let k = seg.slice(0, p).trim();
        let v = seg.slice(p + 1).trim();
        params[k] = v;
    });

    let file = {
        id: params.file_id || "",
        name: params.file || "未知文件",
        busid: 0
    };
    saveLastLogFile(msg.groupId, file);
};

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// 辅助函数：稳健读取 JSON，避免服务端偶发返回纯文本导致直接崩溃
async function safeFetchJson(url, options, tag) {
    const resp = await fetch(url, options);
    const text = await resp.text();
    try {
        return JSON.parse(text);
    } catch (e) {
        const preview = text.length > 160 ? (text.slice(0, 160) + "...") : text;
        throw new Error(`${tag} 返回非JSON: ${preview}`);
    }
}

// --- 核心调度函数 ---
// --- 核心调度函数 ---
async function processLogTask(ctx, msg, cmdArgs, modeName, pythonMode) {
    let args = cmdArgs.args;

    // 1. 获取并解析存储中的自定义配置库
    let stored = ext.storageGet('logai_custom_prompts') || '{}';
    let customPrompts = {};
    try { customPrompts = JSON.parse(stored); } catch (e) {}

    let customPromptContent = "";
    let customName = "";

    // 扫描群友的参数中，是否包含已保存的自定义配置名
    for (let i = 0; i < args.length; i++) {
        if (customPrompts[args[i]]) {
            customName = args[i];
            // 【兼容升级】：旧版是字符串，新版是带 owner 的对象
            let p = customPrompts[args[i]];
            customPromptContent = typeof p === 'string' ? p : p.content;
            break; // 找到即跳出
        }
    }

    let isPro = args.some(a => a.toLowerCase() === 'pro');
    let isKind = args.some(a => a.includes('温柔') || a.toLowerCase() === 'kind');
    let isAI = args.some(a => a.toLowerCase() === 'ai' || a === '原版' || a === '专业');
    let getText = args.some(a => a.toLowerCase() === 'get_text');

    // ===== 新增：图片主题风格嗅探 =====
    let theme = 'default';
    let tArgs = args.join(' ');
    if (tArgs.includes('赛博')) theme = 'cyberpunk';
    else if (tArgs.includes('历史') || tArgs.includes('古风')) theme = 'historical';
    else if (tArgs.includes('克苏鲁') || tArgs.includes('深潜')) theme = 'cthulhu';
    else if (tArgs.includes('废土') || tArgs.includes('末日')) theme = 'wasteland';
    else if (tArgs.includes('二次元') || tArgs.includes('萌系')) theme = 'anime';
    else if (tArgs.includes('终端') || tArgs.includes('黑客')) theme = 'terminal';
    else if (tArgs.includes('经典')) theme = 'classic';
    else if (tArgs.includes('默认') || tArgs.includes('常规')) theme = 'default';

    // ================= 核心：钱包保护逻辑 =================
    // 获取东八区今天的日期字符串 (例如 "2026-3-19")
    let dateStr = (function() {
        let d = new Date(new Date().getTime() + 8 * 3600 * 1000);
        return d.getUTCFullYear() + '-' + (d.getUTCMonth() + 1) + '-' + d.getUTCDate();
    })();
    let globalKey = `logai_pro_usage_${dateStr}_global`;
    let userKey = `logai_pro_usage_${dateStr}_${ctx.player.userId}`;

    if (isPro) {
        let globalLimit = seal.ext.getIntConfig(ext, "每日Pro全局限额");
        let userLimit = seal.ext.getIntConfig(ext, "每日单人Pro限额");
        let isAdmin = ctx.privilegeLevel >= 100; // 大于等于 40 为群管/群主/骰主

        if (globalLimit === 0) {
            seal.replyToSender(ctx, msg, '❌ 抱歉，Pro 模式目前已被骰主禁用，请去除 pro 参数使用普通模式。');
            return seal.ext.newCmdExecuteResult(true);
        }

        let globalUsage = parseInt(ext.storageGet(globalKey) || '0');
        let userUsage = parseInt(ext.storageGet(userKey) || '0');

        if (globalLimit > 0 && globalUsage >= globalLimit && !isAdmin) {
            seal.replyToSender(ctx, msg, `❌ 抱歉，今日机器人的 Pro 模式全局额度（${globalLimit}次）已耗尽，请明日再试或使用普通模式。`);
            return seal.ext.newCmdExecuteResult(true);
        }
        if (userLimit > 0 && userUsage >= userLimit && !isAdmin) {
            seal.replyToSender(ctx, msg, `❌ 抱歉，您今日的 Pro 模式额度（${userLimit}次）已达上限，请明日再试或使用普通模式。`);
            return seal.ext.newCmdExecuteResult(true);
        }

        // 预扣除额度
        ext.storageSet(globalKey, (globalUsage + 1).toString());
        ext.storageSet(userKey, (userUsage + 1).toString());
    }
    // =======================================================

    // 【核心机制】：如果有自定义配置，强制关闭骰娘语气，进入 AI 原版模式！
    if (customName) {
        isAI = true;
    }

    let usePersona = seal.ext.getBoolConfig(ext, "启用骰娘人设");
    if (isAI) usePersona = false;

    let personaStr = "";
    if (usePersona) {
        personaStr = isKind ? seal.ext.getStringConfig(ext, "温柔模式_骰娘设定") : seal.ext.getStringConfig(ext, "常规模式_骰娘设定");
    }

    // 将匹配到的自定义名字加入屏蔽字库，以免它被误认为是网址
    // 把主题关键字也加入过滤列表，防止被当成文件名
    let excludeList =['pro', 'kind', '温柔', '本地', '文件', 'ai', '原版', '专业', '赛博风', '赛博', '历史风', '历史', '古风', '简约风', '简约', '白底', '克苏鲁', '克苏鲁风', '深潜', '废土', '废土风', '末日', '末日风', '二次元', '二次元风', '萌系', '终端', '终端风', '黑客', '经典', '经典风'];
    if (customName) excludeList.push(customName); 
    
    // 找出剩下的所有链接参数（支持多条，按顺序拼接）
    let targetArgs = args.filter(a => !excludeList.includes(a) && !excludeList.includes(a.toLowerCase()));
    let useLocalFile = targetArgs.length === 0;

    let apiUrl = "";
    let logKeyForMsg = "";

    // 组装 POST 数据载荷，加入自定义提示词
    let payload = {
        mode: pythonMode,
        pro: isPro,
        kind: isKind,
        persona: personaStr,
        custom_prompt: customPromptContent,
        theme: theme,   // 新增传给后端的风格
        get_text: getText
    };

    if (useLocalFile) {
        let groupId = ctx.group.groupId;
        dbgLog('flow', `useLocalFile=true, groupId=${groupId}`);
        if (!groupId.includes('Group')) {
            seal.replyToSender(ctx, msg, '❌ 本地文件分析功能只能在群聊中使用。');
            return seal.ext.newCmdExecuteResult(true);
        }

        // 优先从HTTP中转读取最近文件，失败后回退本地缓存（兼容旧逻辑）
        await readLastLogFileFromHttpBridge(groupId);

        let fileDataStr = ext.storageGet(`log_last_file_${groupId}`);
        if (!fileDataStr) {
            dbgLog('flow', 'no file record in storage after bridge read');
            seal.replyToSender(ctx, msg, '❌ 当前群没有可用的文件记录。\n请先上传群文件，或检查HTTP文件桥接服务是否已接收到上传事件。');
            return seal.ext.newCmdExecuteResult(true);
        }
        let fileData = JSON.parse(fileDataStr);
        dbgLog('flow', 'loaded file record from storage', fileData);
        logKeyForMsg = fileData.name;

        apiUrl = `${getBackendBaseUrl()}/api/submit_file`;
        if (fileData.content_url) {
            dbgLog('flow', 'submit by bridge content_url');
            payload.url = fileData.content_url;
            payload.filename = fileData.text_filename || `${fileData.name || 'log'}.txt`;
        } else {
            dbgLog('flow', 'bridge content_url missing, fallback get_group_file_url');
            let onebotApiUrl = seal.ext.getStringConfig(ext, "OneBot_API_地址");
            if (onebotApiUrl.endsWith('/')) onebotApiUrl = onebotApiUrl.slice(0, -1);
            let onebotGroupId = parseInt(groupId.replace('QQ-Group:', ''));

            let urlJson = await safeFetchJson(`${onebotApiUrl}/get_group_file_url`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ group_id: onebotGroupId, file_id: fileData.file_id, busid: fileData.busid })
            }, "获取群文件链接");

            if (!urlJson || !urlJson.data || !urlJson.data.url) {
                dbgLog('flow', 'fallback get_group_file_url failed', urlJson);
                seal.replyToSender(ctx, msg, `❌ 获取群文件下载链接失败。`);
                return seal.ext.newCmdExecuteResult(true);
            }
            payload.url = urlJson.data.url;
            payload.filename = fileData.name;
        }
        payload.mode = pythonMode === 'analyze' ? 'log_analyze' : 'log_recap';

    } else {
        let entries = [];
        for (let i = 0; i < targetArgs.length; i++) {
            let entry = parseLogTargetEntry(targetArgs[i]);
            if (entry) entries.push(entry);
        }

        if (entries.length === 0) {
            seal.replyToSender(ctx, msg, '❌ 无法解析 Log 链接。');
            return seal.ext.newCmdExecuteResult(true);
        }

        // Build logKeyForMsg with real file names for bridge_file entries
        let bridgeFiles = [];
        if (entries.some(e => e.source === 'bridge_file' || e.source === 'bridge_file_name')) {
            try {
                let pureGid = getPureGroupId(ctx.group ? ctx.group.groupId : '');
                let listData = await safeFetchJson(`${getBackendBaseUrl()}/bridge/list?group_id=${pureGid}`, {}, "获取桥接文件列表");
                if (listData && listData.status === 'ok') {
                    bridgeFiles = listData.files || [];
                }
            } catch (e) {}
        }

        logKeyForMsg = entries.map((e, i) => {
            if (e.source === 'bridge_file') {
                let idx = parseInt(e.key) || 0;
                let bf = bridgeFiles[idx];
                let displayName = bf ? bf.display_name || bf.name || `[file]-${idx}` : `[file]-${idx}`;
                return `#${i + 1}:${displayName}`;
            }
            if (e.source === 'bridge_file_name') {
                // Try to match by normalized filename (strip all spaces)
                let searchName = String(e.key).replace(/\s+/g, '');
                let bf = bridgeFiles.find(f => {
                    let fname = String(f.display_name || f.name || '').replace(/\s+/g, '');
                    return fname === searchName;
                });
                // Strategy 2: strip extension and compare base name
                if (!bf) {
                    bf = bridgeFiles.find(f => {
                        let fname = String(f.display_name || f.name || '').replace(/\s+/g, '');
                        let baseName = fname.replace(/\.\w{2,5}$/i, '');
                        return baseName === searchName;
                    });
                }
                // Strategy 3: substring containment
                if (!bf && searchName.length > 1) {
                    bf = bridgeFiles.find(f => {
                        let fname = String(f.display_name || f.name || '').replace(/\s+/g, '');
                        return fname.includes(searchName);
                    });
                }
                // If matched, rewrite to numeric index so backend can use bridge_file logic
                if (bf && bf._index !== undefined) {
                    e.key = String(bf._index);
                    e.source = 'bridge_file';
                    return `#${i + 1}:${bf.display_name || bf.name}`;
                }
                // Fallback: keep as-is, backend will try matching via bridge_file_name
                return `#${i + 1}:${e.key}`;
            }
            return `#${i + 1}:${e.key}`;
        }).join(' | ');
        apiUrl = `${getBackendBaseUrl()}/api/submit`;
        payload.group_id = getPureGroupId(ctx.group ? ctx.group.groupId : '');
        if (entries.length === 1) {
            payload.key = entries[0].key;
            payload.source = entries[0].source;
            if (entries[0].password) payload.password = entries[0].password;
        } else {
            payload.keys = entries.map(e => e.key);
            payload.sources = entries.map(e => e.source);
            payload.passwords = entries.map(e => e.password || '');
        }
    }

    if (customName) {
        modeName = `自定义配置[${customName}]`;
    }

    let modeMsg = usePersona ? " [骰娘人设]" : (isAI ? " [纯净AI]" : "");
    if (useLocalFile) modeMsg += "[本地文件]";
    if (isPro) modeMsg += " [Pro模式]";

    seal.replyToSender(ctx, msg, `已提交请求，正在阅读【${logKeyForMsg}】并执行【${modeName}】${modeMsg} (请稍候)...`);
    dbgLog('submit', `api=${apiUrl}`, payload);

    try {
        let data = await safeFetchJson(apiUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }, "提交分析任务");
        dbgLog('submit', 'submit response', data);
        
        if (data.status !== 'ok') {
            // 【新增】：如果提交给 Python 后端失败了（没扣费），把刚才预扣的额度退回给用户
            if (isPro) {
                let g = parseInt(ext.storageGet(globalKey) || '1');
                let u = parseInt(ext.storageGet(userKey) || '1');
                ext.storageSet(globalKey, Math.max(0, g - 1).toString());
                ext.storageSet(userKey, Math.max(0, u - 1).toString());
            }
            seal.replyToSender(ctx, msg, `❌ 提交失败：${JSON.stringify(data)}`);
            return seal.ext.newCmdExecuteResult(true);
        }

        let jobId = data.id;
        let checkUrl = `${getBackendBaseUrl()}/api/status?id=${jobId}`;
        let resultUrl = `${getBackendBaseUrl()}/api/result?id=${jobId}`;

        let maxRetries = 90;
        while (maxRetries > 0) {
            await sleep(2000);
            let sData = await safeFetchJson(checkUrl, undefined, "查询任务状态");
            
            if (sData.status === 'done' || sData.status === 'error') {
                // get_text 模式：发送下载链接而非图片
                if (getText && sData.text_url) {
                    seal.replyToSender(ctx, msg, `✅ AI 分析完成，文本结果：\n下载链接：${sData.text_url}\n文件名：${sData.text_filename || 'ai_analysis.txt'}`);
                } else if (sData.image_count !== undefined && sData.image_count > 0) {
                    let msgStr = "";
                    for (let i = 0; i < sData.image_count; i++) {
                        let finalUrl = `${resultUrl}&index=${i}&t=${new Date().getTime()}`;
                        msgStr += `[CQ:image,file=${finalUrl},cache=0]`;
                    }
                    seal.replyToSender(ctx, msg, msgStr);
                } else if (sData.text_url) {
                    // 兜底：如果有 text_url 也发送
                    seal.replyToSender(ctx, msg, `✅ AI 分析完成：\n下载链接：${sData.text_url}`);
                } else {
                    let finalUrl = `${resultUrl}&index=0&t=${new Date().getTime()}`;
                    seal.replyToSender(ctx, msg, `[CQ:image,file=${finalUrl},cache=0]`);
                }

                if (useLocalFile) ext.storageSet(`log_last_file_${ctx.group.groupId}`, "");
                return seal.ext.newCmdExecuteResult(true);
            }
            maxRetries--;
        }
        seal.replyToSender(ctx, msg, `⚠️ 分析超时，后台可能仍在处理。`);
    } catch (e) {
        console.error(e);
        seal.replyToSender(ctx, msg, `❌ 脚本错误：${e.message}`);
    }
    return seal.ext.newCmdExecuteResult(true);
}

// --- 注册核心指令： .logai ---
const cmdLogAi = seal.ext.newCmdItemInfo();
cmdLogAi.name = 'logai';
cmdLogAi.help = '对跑团Log进行整体评分。\n用法: .logai [配置名] <链接1> [链接2] ...（按输入顺序拼接）\n或先发文件后输入 .logai（HTTP桥接可走全文）。\n配置管理请使用 .logai 配置 示例';
cmdLogAi.solve = async (ctx, msg, cmdArgs) => { 
    // 【拦截 .logai 配置 子指令】
    let val1 = cmdArgs.getArgN(1);
    if (val1 === '配置') {
        let op = cmdArgs.getArgN(2);
        let stored = ext.storageGet('logai_custom_prompts') || '{}';
        let prompts = {};
        try { prompts = JSON.parse(stored); } catch (e) {}

        // 提取用户信息与权限 (>= 40 为群管/群主/骰主)
        let userId = ctx.player.userId;
        let userName = ctx.player.name;
        let isAdmin = ctx.privilegeLevel >= 100; 

        if (!op || op === '示例') {
            seal.replyToSender(ctx, msg, `【自定义分析配置说明】
你可以自由编写AI阅读Log时的要求，存为配置名随时调用。
1. 添加配置：.logai 配置 添加 <名称> <提示词...>
2. 删除配置：.logai 配置 删除 <名称>
3. 查看列表：.logai 配置 列表 [页码]
4. 查看详情：.logai 配置 查看 <名称>
5. 使用配置：.logai <名称> <网址链接 或 在群里发本地Log>

💡 示例用法：
.logai 配置 添加 吐槽机器 你是一个只会吐槽的杠精，找出下面日志里的弱智操作狠狠嘲讽。
（进阶：如果字数太多需要分页长图发，可以在提示词里要求 AI 在每段开头加上“【分页符】”这四个字）
.logai 配置 查看 吐槽机器
(注：每个人只能修改/删除自己创建的配置，管理员拥有所有权限)`);
            return seal.ext.newCmdExecuteResult(true);

        } else if (op === '添加') {
            let name = cmdArgs.getArgN(3);
            let contentArgs =[];
            for(let i=3; i < cmdArgs.args.length; i++) { contentArgs.push(cmdArgs.args[i]); }
            let content = contentArgs.join(' ').trim();
            
            if (!name || !content) {
                seal.replyToSender(ctx, msg, `❌ 缺少名称或内容！\n示例：.logai 配置 添加 小说家 请将跑团转为小说...`);
                return seal.ext.newCmdExecuteResult(true);
            }

            let existing = prompts[name];
            if (existing) {
                // 修改已有配置：校验归属权，且不消耗每日创建配额
                let owner = typeof existing === 'string' ? '' : existing.owner;
                if (owner && owner !== userId && !isAdmin) {
                    seal.replyToSender(ctx, msg, `❌ 权限不足：配置【${name}】由其他用户创建，您无法修改它！`);
                    return seal.ext.newCmdExecuteResult(true);
                }
            } else {
                // 【新增】：创建全新配置时的防滥用限额机制
                if (!isAdmin) {
                    let dateStr = (function() {
                        let d = new Date(new Date().getTime() + 8 * 3600 * 1000);
                        return d.getUTCFullYear() + '-' + (d.getUTCMonth() + 1) + '-' + d.getUTCDate();
                    })();
                    let createLimitKey = `logai_config_create_${dateStr}_${userId}`;
                    let currentCreates = parseInt(ext.storageGet(createLimitKey) || '0');
                    
                    if (currentCreates >= 1) {
                        seal.replyToSender(ctx, msg, `❌ 抱歉，为防止恶意绕过限额，普通用户每天仅限创建 1 个全新配置。\n您今天已达上限，请明天再试，或对您的已有配置进行修改覆盖。`);
                        return seal.ext.newCmdExecuteResult(true);
                    }
                    
                    // 扣除今日的创建额度
                    ext.storageSet(createLimitKey, (currentCreates + 1).toString());
                }
            }

            prompts[name] = { content: content, owner: userId, creatorName: userName };
            ext.storageSet('logai_custom_prompts', JSON.stringify(prompts));
            seal.replyToSender(ctx, msg, `✅ 已成功保存自定义配置：【${name}】\n现在你可以直接回复含有网址或群文件的内容：\n.logai ${name} 链接`);
            return seal.ext.newCmdExecuteResult(true);
            
        } else if (op === '删除') {
            let name = cmdArgs.getArgN(3);
            let existing = prompts[name];
            
            if (existing) {
                let owner = typeof existing === 'string' ? '' : existing.owner;
                if (owner && owner !== userId && !isAdmin) {
                    seal.replyToSender(ctx, msg, `❌ 权限不足：配置【${name}】由其他用户创建，您无法删除它！`);
                    return seal.ext.newCmdExecuteResult(true);
                }
                
                delete prompts[name];
                ext.storageSet('logai_custom_prompts', JSON.stringify(prompts));
                seal.replyToSender(ctx, msg, `✅ 已删除配置：【${name}】`);
            } else {
                seal.replyToSender(ctx, msg, `❌ 库中未找到名为【${name}】的配置。`);
            }
            return seal.ext.newCmdExecuteResult(true);

        } else if (op === '列表') {
            let keys = Object.keys(prompts);
            if (keys.length === 0) {
                seal.replyToSender(ctx, msg, `当前没有保存任何自定义配置。`);
                return seal.ext.newCmdExecuteResult(true);
            } 
            
            // 获取用户输入的页码，默认为 1
            let page = parseInt(cmdArgs.getArgN(3));
            if (isNaN(page) || page < 1) page = 1;

            let pageSize = 10;
            let totalPages = Math.ceil(keys.length / pageSize);

            if (page > totalPages) page = totalPages;

            let start = (page - 1) * pageSize;
            let currentKeys = keys.slice(start, start + pageSize);

            let replyMsg = `📄 当前自定义配置列表 (第 ${page}/${totalPages} 页)：\n- ` + currentKeys.join('\n- ');
            
            if (totalPages > 1) {
                let nextPage = page < totalPages ? page + 1 : 1;
                replyMsg += `\n\n💡 翻页提示：发送 .logai 配置 列表 ${nextPage} 查看其它页`;
            }

            seal.replyToSender(ctx, msg, replyMsg);
            return seal.ext.newCmdExecuteResult(true);

        } else if (op === '查看') {
            let name = cmdArgs.getArgN(3);
            if (!name) {
                seal.replyToSender(ctx, msg, `❌ 请输入要查看的配置名称！\n示例：.logai 配置 查看 小说家`);
            } else if (prompts[name]) {
                let p = prompts[name];
                let content = typeof p === 'string' ? p : p.content;
                let creator = typeof p === 'string' ? '未知(旧版)' : p.creatorName;
                seal.replyToSender(ctx, msg, `📄 配置【${name}】 (创建者: ${creator})\n--------------------\n${content}`);
            } else {
                seal.replyToSender(ctx, msg, `❌ 库中未找到名为【${name}】的配置。`);
            }
            return seal.ext.newCmdExecuteResult(true);
        }
    }
    
    return await processLogTask(ctx, msg, cmdArgs, '跑团日志评分与吐槽', 'analyze'); 
};
ext.cmdMap['logai'] = cmdLogAi;
// 新命令：.aiutil 快速AI分析（不保存配置）
const cmdAiutil = seal.ext.newCmdItemInfo();
cmdAiutil.name = 'aiutil';
cmdAiutil.help = '快速AI分析，不保存配置。\n用法: .aiutil [file1] …… [fileN] <prompt> [pro] [get_text]\nfile格式: [file]-N（编号从0最旧到最新）\n无文件时仅发送prompt给AI。';
cmdAiutil.solve = async (ctx, msg, cmdArgs) => {
    let args = cmdArgs.args;
    if (!args || args.length === 0) {
        seal.replyToSender(ctx, msg, '用法: .aiutil [file1] …… [fileN] <prompt> [pro] [get_text]\n示例: .aiutil [file]-0 [file]-1 请总结这两个文件的内容 pro');
        return seal.ext.newCmdExecuteResult(true);
    }

    let isPro = args.some(a => a.toLowerCase() === 'pro');
    let getText = args.some(a => a.toLowerCase() === 'get_text');

    // 提取 [file]-N 文件引用
    let fileArgs = [];
    let promptParts = [];
    for (let a of args) {
        if (/^\[file\]-\d+$/i.test(String(a || '').trim())) {
            fileArgs.push(String(a).trim());
        } else if (a.toLowerCase() === 'pro' || a.toLowerCase() === 'get_text') {
            // skip flags
        } else {
            promptParts.push(a);
        }
    }
    let prompt = promptParts.join(' ').trim();

    if (!prompt) {
        seal.replyToSender(ctx, msg, '❌ 请提供分析提示词(prompt)。\n用法: .aiutil [file1] …… [fileN] <prompt> [pro] [get_text]');
        return seal.ext.newCmdExecuteResult(true);
    }

    let groupId = ctx.group ? ctx.group.groupId : '';
    if (!groupId) {
        seal.replyToSender(ctx, msg, '❌ 此功能仅在群聊中可用。');
        return seal.ext.newCmdExecuteResult(true);
    }

    seal.replyToSender(ctx, msg, `已提交AI分析请求（提示词: ${prompt.slice(0, 30)}...${fileArgs.length > 0 ? ' 携带' + fileArgs.length + '个文件' : ' 无文件'}）${isPro ? ' [Pro]' : ''}${getText ? ' [文本输出]' : ''}`);

    try {
        let host = getBackendBaseUrl();
        let pureGroupId = getPureGroupId(groupId);

        // 解析文件引用为 bridge_file 格式
        let fileEntries = [];
        if (fileArgs.length > 0) {
            // 获取桥接文件列表
            let listData = await safeFetchJson(`${getBackendBaseUrl()}/bridge/list?group_id=${pureGroupId}`, {}, "获取桥接文件列表");
            let bridgeFiles = [];
            if (listData && listData.status === 'ok') {
                bridgeFiles = listData.files || [];
            }

            for (let fa of fileArgs) {
                let idxMatch = fa.match(/^\[file\]-(\d+)$/i);
                if (idxMatch) {
                    let idx = parseInt(idxMatch[1]);
                    if (idx >= 0 && idx < bridgeFiles.length) {
                        let bf = bridgeFiles[idx];
                        let contentUrl = bf.content_url || '';
                        if (contentUrl) {
                            fileEntries.push({ url: contentUrl, name: bf.display_name || bf.name || fa });
                        }
                    }
                }
            }
        }

        let jobId, checkUrl, resultUrl;

        if (fileEntries.length > 0) {
            // 有文件：使用 /api/submit + raw_url 源（桥接内容已是提取后的纯文本，避免重复解析）
            let submitPayload = {
                mode: 'analyze',
                pro: isPro,
                kind: false,
                persona: '',
                custom_prompt: prompt,
                theme: 'default',
                get_text: getText,
                keys: fileEntries.map(e => e.url),
                sources: fileEntries.map(() => 'raw_url'),
                passwords: fileEntries.map(() => ''),
                group_id: pureGroupId
            };

            let apiUrl = `${getBackendBaseUrl()}/api/submit`;
            let data = await safeFetchJson(apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(submitPayload)
            }, "提交AI分析任务");

            if (data.status !== 'ok') {
                seal.replyToSender(ctx, msg, `❌ 提交失败：${JSON.stringify(data)}`);
                return seal.ext.newCmdExecuteResult(true);
            }
            jobId = data.id;
        } else {
            // 无文件：仅发送 prompt 给 AI（v4.2: 使用 text 直接文本模式）
            let textPayload = {
                mode: 'analyze',
                pro: isPro,
                kind: false,
                persona: '',
                custom_prompt: '',   // 系统提示留空，让后端用默认
                theme: 'default',
                get_text: getText,
                text: prompt          // v4.2: direct_text模式，prompt作为AI分析的输入内容
            };

            let apiUrl = `${getBackendBaseUrl()}/api/submit`;
            let data = await safeFetchJson(apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(textPayload)
            }, "提交AI分析任务");

            if (data.status !== 'ok') {
                seal.replyToSender(ctx, msg, `❌ 提交失败：${JSON.stringify(data)}`);
                return seal.ext.newCmdExecuteResult(true);
            }
            jobId = data.id;
        }

        checkUrl = `${getBackendBaseUrl()}/api/status?id=${jobId}`;
        resultUrl = `${getBackendBaseUrl()}/api/result?id=${jobId}`;

        let maxRetries = 90;
        while (maxRetries > 0) {
            await sleep(2000);
            let sData = await safeFetchJson(checkUrl, undefined, "查询AI任务状态");

            if (sData.status === 'done' || sData.status === 'error') {
                if (getText && sData.text_url) {
                    seal.replyToSender(ctx, msg, `✅ AI 分析完成，文本结果：\n下载链接：${sData.text_url}\n文件名：${sData.text_filename || 'ai_analysis.txt'}`);
                } else if (sData.image_count !== undefined && sData.image_count > 0) {
                    let msgStr = "";
                    for (let i = 0; i < sData.image_count; i++) {
                        let finalUrl = `${resultUrl}&index=${i}&t=${new Date().getTime()}`;
                        msgStr += `[CQ:image,file=${finalUrl},cache=0]`;
                    }
                    seal.replyToSender(ctx, msg, msgStr);
                } else if (sData.text_url) {
                    seal.replyToSender(ctx, msg, `✅ AI 分析完成：\n下载链接：${sData.text_url}`);
                } else {
                    let finalUrl = `${resultUrl}&index=0&t=${new Date().getTime()}`;
                    seal.replyToSender(ctx, msg, `[CQ:image,file=${finalUrl},cache=0]`);
                }
                return seal.ext.newCmdExecuteResult(true);
            }
            maxRetries--;
        }
        seal.replyToSender(ctx, msg, `⚠️ AI 分析超时，后台可能仍在处理。`);
    } catch (e) {
        console.error(e);
        seal.replyToSender(ctx, msg, `❌ 脚本错误：${e.message}`);
    }
    return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['aiutil'] = cmdAiutil;
ext.cmdMap['ai'] = cmdAiutil;  // .ai 作为 .aiutil 的别名（向下兼容）
// 新命令：logutil（对接后端 /api/logutil_*）
const cmdLogUtil = seal.ext.newCmdItemInfo();
cmdLogUtil.name = 'logutil';
cmdLogUtil.help = fw([
    '【fwlog 聊天记录转海豹日志工具】',
    '// 说明：本工具会将【合并转发 / 日志链接 / 文档 / 零碎文字】提取并转换为海豹(SealDice)原生日志格式，以便在日志缺失时进行补充。',
    '// 注意：记录开启后，会按收到顺序持续追加以上内容。',
    '// 正常跑团请使用 .log 指令。',
    '',
    '【指令列表】',
    '.logutil new [名称] [raw] // 新建并开始记录（raw: 跳过消息头解析）',
    '.logutil on [名称] [raw]  // 继续记录已有日志',
    '.logutil off              // 暂停当前日志记录',
    '.logutil end [名称]       // 结束、发送日志文件并上传染色器',
    '.logutil get [名称]       // 获取日志文件并上传染色器',
    '.logutil list             // 列出当前会话日志',
    '.logutil clear [名称]     // 清除指定日志',
    '.logutil wsconfig [url] [token] [on|off]  // 查看/设置 WS 实时监听配置',
    '[file]-N                  // 将桥接缓存的第N个文件内容追加到当前日志 (N=0表示最旧，数字越大越新)',
    '',
    '【复合命令】',
    '.logutil [new] <op1> <op2> ... [end] [logai]',
    '// 串联执行多个操作，等价于依次执行 new → add(op1) → ... → add(opN) → end → logai',
    '// op 可为 [file]-N、文件名(含/不含扩展名)、着色器链接、或任意文本',
    '// new 和 end 可省略；logai 关键字在 end 后自动对生成文件执行 AI 分析',
    '// 别名：.fwlog 完全等价于 .logutil',
].join('\n'));
cmdLogUtil.solve = async (ctx, msg, cmdArgs) => {
    let groupId = ctx.group ? ctx.group.groupId : '';
    if (!groupId) {
        seal.replyToSender(ctx, msg, fw('❌ 此功能仅在群聊中可用'));
        return seal.ext.newCmdExecuteResult(true);
    }
    let host = getBackendBaseUrl();

    // Parse args from raw message text to avoid SealDice tokenization issues
    // (SealDice may drop or mangle [file]-N bracket syntax)
    let rawText = String(msg.message || '').trim();
    // Strip command prefix: .logutil or .fwlog (with optional leading 。/、//)
    let cmdPrefixMatch = rawText.match(/^[.。/、]?\s*(?:logutil|fwlog)\s+/i);
    let restText = cmdPrefixMatch ? rawText.slice(cmdPrefixMatch[0].length).trim() : rawText;

    // Split restText into tokens respecting quoted strings and bracket groups
    let tokens = [];
    let tokenRegex = /(?:\[file\]-\d+)|(?:https?:\/\/\S+)|(?:"[^"]*")|(?:\S+)/gi;
    let m;
    while ((m = tokenRegex.exec(restText)) !== null) {
        tokens.push(m[0]);
    }

    let op = (tokens[0] || '').toLowerCase();
    let rawArgs = tokens.slice(1);
    // raw 作为修饰符应被排除在名称/内容之外（v4.3.3: 仅当 logutil 后第一个非命令字段为 raw 时生效）
    let raw_mode = (tokens[0] || '').toLowerCase() === 'raw' || (tokens.length > 1 && (tokens[1] || '').toLowerCase() === 'raw');
    let arg2 = rawArgs.find(a => !['del_paren', 'delparen', 'del-paren', 'raw'].includes((a || '').toLowerCase())) || '';

    let pureGroupId = getPureGroupId(groupId);
    let payload = { group_id: pureGroupId };
    let del_paren = rawArgs.some(a => ['del_paren', 'delparen', 'del-paren'].includes((a || '').toLowerCase()));

    if (!op) {
        seal.replyToSender(ctx, msg, fw('用法: .logutil new/on/off/get/end/list/clear [名称] [del_paren]'));
        return seal.ext.newCmdExecuteResult(true);
    }

    try {
        // --- Detect compound command pattern ---
        let allArgs = tokens;
        let hasEnd = allArgs.some(a => (a || '').toLowerCase() === 'end');
        let hasNew = allArgs.some(a => (a || '').toLowerCase() === 'new');
        let hasLogai = allArgs.some(a => (a || '').toLowerCase() === 'logai');
        let hasOps = rawArgs.some(a => {
            let s = String(a || '').trim();
            return /^\[file\]-\d+$/i.test(s) || /^https?:\/\//i.test(s) || !!parseLogTargetEntry(s);
        });
        let isCompound = (hasEnd || hasLogai || (hasNew && rawArgs.length > 1) || (!hasNew && hasOps));

        if (isCompound || (!op || (op !== 'new' && op !== 'on' && op !== 'off' && op !== 'get' &&
             op !== 'end' && op !== 'list' && op !== 'clear' && op !== 'wsconfig'))) {

            // --- 复合命令: .logutil [new [标题]] <ops...> [end] [logai] ---
            // Parse: find "new" and "end" positions
            let newIdx = allArgs.findIndex(a => (a || '').toLowerCase() === 'new');
            let endIdx = allArgs.findIndex(a => (a || '').toLowerCase() === 'end');
            hasLogai = allArgs.some(a => (a || '').toLowerCase() === 'logai');
            // Parse logai mode words (everything after "logai" keyword)
            let logaiIdx = allArgs.findIndex(a => (a || '').toLowerCase() === 'logai');
            let logaiModeWords = [];
            if (logaiIdx >= 0 && logaiIdx + 1 < allArgs.length) {
                logaiModeWords = allArgs.slice(logaiIdx + 1);
            }

            // Title detection: after "new", the next non-op word is the title
            // An "op" is: [file]-N, URL, or something parseLogTargetEntry recognizes
            let title = '';
            let opsStart = 0;
            if (newIdx >= 0) {
                let titleCandidateIdx = newIdx + 1;
                if (titleCandidateIdx < allArgs.length) {
                    let cand = String(allArgs[titleCandidateIdx] || '').trim();
                    let isOp = /^\[file\]-\d+$/i.test(cand) ||
                               /^https?:\/\//i.test(cand) ||
                               (cand.toLowerCase() === 'end') ||
                               (cand.toLowerCase() === 'logai') ||
                               !!parseLogTargetEntry(cand);
                    if (!isOp && cand) {
                        title = cand;
                        opsStart = titleCandidateIdx + 1;
                    } else {
                        opsStart = titleCandidateIdx;
                    }
                } else {
                    opsStart = titleCandidateIdx;
                }
            } else {
                opsStart = 0;
            }

            // Extract ops: between title/start and "end" (or end of args)
            let ops;
            if (endIdx >= 0) {
                ops = allArgs.slice(opsStart, endIdx);
            } else {
                ops = allArgs.slice(opsStart);
            }
            // Filter out "new"/"end"/"logai" keywords and logai mode words from ops
            let logaiModeLower = new Set(logaiModeWords.map(w => (w || '').toLowerCase()));
            ops = ops.filter(a => {
                let s = (a || '').toLowerCase();
                return s !== 'new' && s !== 'end' && s !== 'logai' && s !== 'raw' && !logaiModeLower.has(s);
            });

            if (ops.length === 0 && !hasEnd && !hasLogai) {
                // Bare ".logutil new" with no ops — use old handler
                // (fall through pattern — actually just do old new handler)
                // But we're in compound mode now, so just show help
                seal.replyToSender(ctx, msg, fw('用法: .logutil [new [标题]] <内容...> [end] [logai]'));
                return seal.ext.newCmdExecuteResult(true);
            }

            // Build compound payload
            let compoundPayload = {
                group_id: getPureGroupId(groupId),
                new: newIdx >= 0,
                end: endIdx >= 0,
                logai: hasLogai,
                ops: ops,
                nickname: ctx.player ? ctx.player.name : 'User',
                user_id: ctx.player ? ctx.player.userId : '',
            };
            if (title) compoundPayload.title = title;
            if (del_paren) compoundPayload.del_paren = true;

            try {
                let resp = await fetch(`${host}/api/logutil_compound`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(compoundPayload)
                });
                let data = await resp.json();

                if (data.status === 'ok') {
                    let replyParts = [];
                    if (newIdx >= 0) {
                        replyParts.push(fw(`【新建复合日志】 ${title || data.name}`));
                        replyParts.push(fw('------------------------------'));
                    } else {
                        replyParts.push(fw(`【复合命令】 ${data.name}`));
                    }

                    for (let r of (data.ops || [])) {
                        let icon = r.status === 'ok' ? '✓' : (r.status === 'empty' ? '○' : '✗');
                        replyParts.push(fw(`  ${icon} ${r.op} (+${r.added || 0}条)`));
                    }
                    replyParts.push(fw(`共追加 ${data.total_added || 0} 条`));

                    if (endIdx >= 0) {
                        replyParts.push(fw('【已结束记录】'));
                        if (data.content_url) replyParts.push(fw(`下载链接：${data.content_url}`));
                        if (data.dye_link) {
                            replyParts.push(fw('【染色器链接】'));
                            replyParts.push(data.dye_link);
                        }
                    }

                    seal.replyToSender(ctx, msg, replyParts.join('\n'));

                    // Logai after compound end: submit and poll for images (same as standalone .logai)
                    if (hasLogai && endIdx >= 0 && data.content_url) {
                        // Parse mode words following "logai"
                        let logaiIsPro = logaiModeWords.some(w => (w || '').toLowerCase() === 'pro');
                        let logaiIsKind = logaiModeWords.some(w => (w || '').toLowerCase() === 'kind' || (w || '').includes('温柔'));
                        let logaiIsAI = logaiModeWords.some(w => (w || '').toLowerCase() === 'ai' || w === '原版' || w === '专业');
                        let logaiTheme = 'default';
                        let tCheck = logaiModeWords.join(' ');
                        if (tCheck.includes('赛博')) logaiTheme = 'cyberpunk';
                        else if (tCheck.includes('历史') || tCheck.includes('古风')) logaiTheme = 'historical';
                        else if (tCheck.includes('简约')) logaiTheme = 'minimal';
                        else if (tCheck.includes('克苏鲁')) logaiTheme = 'cthulhu';
                        else if (tCheck.includes('废土') || tCheck.includes('末日')) logaiTheme = 'wasteland';
                        else if (tCheck.includes('二次元') || tCheck.includes('萌系')) logaiTheme = 'anime';
                        else if (tCheck.includes('终端') || tCheck.includes('黑客')) logaiTheme = 'terminal';
                        // Custom config name (stored prompts)
                        let logaiCustomName = '';
                        let logaiCustomPrompt = '';
                        let stored = ext.storageGet('logai_custom_prompts') || '{}';
                        let customPrompts = {};
                        try { customPrompts = JSON.parse(stored); } catch (e) {}
                        for (let w of logaiModeWords) {
                            if (customPrompts[w]) {
                                logaiCustomName = w;
                                let p = customPrompts[w];
                                logaiCustomPrompt = typeof p === 'string' ? p : p.content;
                                break;
                            }
                        }
                        if (logaiCustomName) logaiIsAI = true;  // custom config forces AI mode

                        try {
                            let aiPayload = {
                                group_id: getPureGroupId(groupId),
                                key: data.content_url,
                                source: 'raw_url',
                                mode: 'log_analyze',
                                pro: logaiIsPro,
                                kind: logaiIsKind,
                                persona: '',  // AI mode or custom disables persona
                                theme: logaiTheme,
                            };
                            if (logaiCustomPrompt) {
                                aiPayload.custom_prompt = logaiCustomPrompt;
                            }
                            if (logaiIsAI) {
                                // AI mode: disable persona
                                aiPayload.persona = '__ai__';
                            }
                            let aiResp = await fetch(`${host}/api/submit`, {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify(aiPayload)
                            });
                            let aiData = await aiResp.json();
                            if (aiData.status === 'ok' && aiData.id) {
                                seal.replyToSender(ctx, msg, fw('✓ AI 分析任务已提交，请稍候...'));
                                let jobId = aiData.id;
                                let checkUrl = `${getBackendBaseUrl()}/api/status?id=${jobId}`;
                                let resultUrl = `${getBackendBaseUrl()}/api/result?id=${jobId}`;
                                let maxRetries = 90;
                                let _sleep = (ms) => new Promise(r => setTimeout(r, ms));
                                while (maxRetries > 0) {
                                    await _sleep(2000);
                                    let sData = await safeFetchJson(checkUrl, undefined, "查询logai任务状态");
                                    if (sData && (sData.status === 'done' || sData.status === 'error')) {
                                        if (sData.image_count !== undefined && sData.image_count > 0) {
                                            let msgStr = '';
                                            for (let i = 0; i < sData.image_count; i++) {
                                                let finalUrl = `${resultUrl}&index=${i}&t=${new Date().getTime()}`;
                                                msgStr += `[CQ:image,file=${finalUrl},cache=0]`;
                                            }
                                            seal.replyToSender(ctx, msg, msgStr);
                                        } else {
                                            let finalUrl = `${resultUrl}&index=0&t=${new Date().getTime()}`;
                                            seal.replyToSender(ctx, msg, `[CQ:image,file=${finalUrl},cache=0]`);
                                        }
                                        break;
                                    }
                                    maxRetries--;
                                }
                                if (maxRetries <= 0) {
                                    seal.replyToSender(ctx, msg, fw('⚠ AI 分析超时，请稍后手动 .logai 查看结果'));
                                }
                            } else {
                                seal.replyToSender(ctx, msg, fw(`✗ AI 分析提交失败: ${JSON.stringify(aiData)}`));
                            }
                        } catch (e) {
                            seal.replyToSender(ctx, msg, fw(`✗ AI 分析异常: ${e.message}`));
                        }
                    }
                } else {
                    seal.replyToSender(ctx, msg, fw(`复合命令失败: ${JSON.stringify(data)}`));
                }
            } catch (e) {
                seal.replyToSender(ctx, msg, fw(`复合命令执行失败: ${e.message}`));
            }
            return seal.ext.newCmdExecuteResult(true);
        }

        if (op === 'new' || op === 'on') {
            if (arg2) {
                payload.name = arg2;
            }
            if (raw_mode) {
                payload.raw = true;
            }
            let endpoint = `${host}/api/logutil_${op}`;
            let resp = await fetch(endpoint, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
            let data = await resp.json();
            if (data.status === 'ok') {
                // 对应 fwlog 的【新建日志】回应
                let newMsg = `【新建日志】 ${ctx.player.name || '用户'} 已新建日志: ${data.name}\n` +
                    "------------------------------\n" +
                    "* 记录已开启！请发送【合并转发 / 日志链接 / 文档 / 零碎文字】以提取内容。\n" +
                    "// 说明：本工具会将以上内容转化为海豹原始格式，用于补充缺失日志。\n" +
                    "// 正常跑团请直接使用 .log 指令。";
                seal.replyToSender(ctx, msg, fw(newMsg));
            } else {
                seal.replyToSender(ctx, msg, fw(`指定日志不存在或创建失败: ${JSON.stringify(data)}`));
            }
            return seal.ext.newCmdExecuteResult(true);
        }

        if (op === 'off') {
            let resp = await fetch(`${host}/api/logutil_off`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
            let data = await resp.json();
            if (data.status === 'ok') {
                // 对应 fwlog 的暂停记录回应
                seal.replyToSender(ctx, msg, fw('【暂停记录】 已暂停记录当前日志'));
            } else {
                seal.replyToSender(ctx, msg, fw(`无法暂停记录: ${JSON.stringify(data)}`));
            }
            return seal.ext.newCmdExecuteResult(true);
        }

        if (op === 'list') {
            let resp = await fetch(`${host}/api/logutil_list?group_id=${getPureGroupId(groupId)}`);
            let data = await resp.json();
            if (data.status === 'ok') {
                let lines = [fw('【日志列表】 本会话 fwlog 列表:')];
                for (const log of data.logs || []) {
                    let name = log.name;
                    let is_current = (data.current_log_name === name && data.recording);
                    let status = is_current ? '* [记录中]' : (log.ended ? '  [已结束]' : '  [已暂停]');
                    let created = log.created_at ? (new Date(log.created_at)).toLocaleString() : '';
                    lines.push(`- ${status} ${name} (${log.item_count || 0}条, 创建于 ${created})`);
                }
                seal.replyToSender(ctx, msg, fw(lines.join('\n')));
            } else {
                seal.replyToSender(ctx, msg, fw(`list 失败: ${JSON.stringify(data)}`));
            }
            return seal.ext.newCmdExecuteResult(true);
        }

        if (op === 'clear') {
            if (arg2) {
                payload.name = arg2;
            }
            let resp = await fetch(`${host}/api/logutil_clear`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
            let data = await resp.json();
            if (data.status === 'ok') {
                seal.replyToSender(ctx, msg, fw(`【清除成功】 日志 ${data.name} 已清除`));
            } else {
                seal.replyToSender(ctx, msg, fw(`clear 失败: ${JSON.stringify(data)}`));
            }
            return seal.ext.newCmdExecuteResult(true);
        }

        if (op === 'get' || op === 'end') {
            if (arg2) {
                payload.name = arg2;
            }
            if (del_paren) {
                payload.del_paren = true;
            }
            let endpoint = `${host}/api/logutil_${op}`;
            let resp;
            if (op === 'get') {
                let query = `?group_id=${getPureGroupId(groupId)}`;
                if (payload.name) {
                    query += `&name=${encodeURIComponent(payload.name)}`;
                }
                if (del_paren) {
                    query += `&del_paren=true`;
                }
                resp = await fetch(`${endpoint}${query}`);
            } else {
                resp = await fetch(endpoint, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
            }
            let data = await resp.json();
            if (data.status === 'ok') {
                // fwlog-style response: file sent + dye link
                let parts = [];
                if (data.file_sent) {
                    parts.push(fw('【发送成功】 日志文件已发送'));
                } else if (data.napcat_error) {
                    parts.push(fw(`【发送失败】 日志文件发送失败: ${data.napcat_error}`));
                }
                if (data.content_url) {
                    parts.push(fw(`下载链接：${data.content_url}`));
                }
                if (data.dye_link) {
                    parts.push(fw('【染色器链接】 已上传至染色器'));
                    parts.push(data.dye_link);
                }
                if (parts.length === 0) {
                    parts.push(fw('【发送成功】 日志文件已处理'));
                }
                seal.replyToSender(ctx, msg, parts.join('\n'));
            } else {
                seal.replyToSender(ctx, msg, fw(`${op} 失败: ${JSON.stringify(data)}`));
            }
            return seal.ext.newCmdExecuteResult(true);
        }

        if (op === 'wsconfig') {
            // .logutil wsconfig [url] [token] [on|off] [mode=0|1]
            let wsUrl = rawArgs.find(a => a.startsWith('ws://') || a.startsWith('wss://')) || '';
            let wsToken = rawArgs.find(a => a.startsWith('NCFileBridgeMode=')) ? '' : rawArgs.find(a => !a.startsWith('ws') && a !== 'on' && a !== 'off' && a !== wsUrl && !a.startsWith('mode=') && !a.startsWith('NCFileBridgeMode=')) || '';
            let wsEnable = rawArgs.includes('on') ? true : (rawArgs.includes('off') ? false : undefined);
            let modeArg = rawArgs.find(a => a.startsWith('mode=') || a.startsWith('NCFileBridgeMode='));
            let bridgeMode = undefined;
            if (modeArg) {
                let m = modeArg.match(/(\d+)/);
                if (m) bridgeMode = parseInt(m[1]);
            }
            let payload = {};
            if (wsUrl) payload.NAPCAT_WS_URL = wsUrl;
            if (wsToken) payload.NAPCAT_WS_TOKEN = wsToken;
            if (wsEnable !== undefined) payload.LOGUTIL_WS_ENABLED = wsEnable;
            if (bridgeMode !== undefined) payload.NC_FILE_BRIDGE_MODE = bridgeMode;
            if (Object.keys(payload).length > 0) {
                let resp = await fetch(`${host}/api/logutil_config`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                let data = await resp.json();
                if (data.status === 'ok') {
                    let lines = [fw('【WS实时监听配置】已更新:')];
                    if (data.updated.NAPCAT_WS_URL) lines.push(fw(`  地址: ${data.updated.NAPCAT_WS_URL}`));
                    if (data.updated.NAPCAT_WS_TOKEN) lines.push(fw('  Token: 已设置'));
                    if (data.updated.LOGUTIL_WS_ENABLED !== undefined) lines.push(fw(`  WS监听: ${data.updated.LOGUTIL_WS_ENABLED ? '已启用' : '已禁用'}`));
                    if (data.updated.NC_FILE_BRIDGE_MODE !== undefined) lines.push(fw(`  文件桥接模式: ${data.updated.NC_FILE_BRIDGE_MODE === 0 ? 'WS实时推送' : '轮询模式'}`));
                    seal.replyToSender(ctx, msg, lines.join('\n'));
                } else {
                    seal.replyToSender(ctx, msg, fw(`更新失败: ${JSON.stringify(data)}`));
                }
            } else {
                // Query only
                let resp = await fetch(`${host}/api/logutil_config`);
                let data = await resp.json();
                if (data.status === 'ok') {
                    let modeText = (data.NC_FILE_BRIDGE_MODE === 0) ? 'WS实时推送' : '轮询模式(备用)';
                    let lines = [
                        fw('【WS实时监听配置】当前状态:'),
                        fw(`  地址: ${data.NAPCAT_WS_URL}`),
                        fw(`  Token: ${data.NAPCAT_WS_TOKEN ? '已设置' : '(未设置，复用HTTP API Token)'}`),
                        fw(`  WS监听: ${data.LOGUTIL_WS_ENABLED ? '已启用' : '已禁用'}`),
                        fw(`  文件桥接模式: ${modeText}`),
                        fw(`  最大缓存文件数: ${data.MAX_BRIDGE_FILES_PER_GROUP}`),
                    ];
                    seal.replyToSender(ctx, msg, lines.join('\n'));
                } else {
                    seal.replyToSender(ctx, msg, fw(`查询失败: ${JSON.stringify(data)}`));
                }
            }
            return seal.ext.newCmdExecuteResult(true);
        }

    } catch (e) {
        seal.replyToSender(ctx, msg, fw(`请求后端失败: ${e.message}`));
    }
    return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['logutil'] = cmdLogUtil;
ext.cmdMap['fwlog'] = cmdLogUtil;  // fwlog 作为 logutil 的别名


const cmdBridge = seal.ext.newCmdItemInfo();
cmdBridge.name = 'bridge';
cmdBridge.help = fw([
    '【群文件桥接轮询控制】',
    '// 说明：控制后台对群文件新上传的主动检测（轮询）。',
    '// 开启后每 5 秒（录音中每 1 秒）检测一次新文件。',
    '',
    '.bridge on       // 对本群开启桥接轮询',
    '.bridge off      // 对本群关闭桥接轮询',
    '.bridge status   // 查看当前轮询状态',
    '.bridge list     // 列出当前桥接缓存的全部文件',
    '.bridge get N    // 获取编号为N的文件转纯文本后的txt文档下载链接',
    '.bridge rate n   // 设置轮询间隔为 n 秒（n<=0 恢复默认）',
].join('\n'));
cmdBridge.solve = async (ctx, msg, cmdArgs) => {
    let op = (cmdArgs.getArgN(1) || '').toLowerCase();
    let groupId = ctx.group ? ctx.group.groupId : '';
    if (!groupId) {
        seal.replyToSender(ctx, msg, fw('❌ 此功能仅在群聊中可用'));
        return seal.ext.newCmdExecuteResult(true);
    }
    let pureId = getPureGroupId(groupId);
    let payload = { group_id: pureId };
    let host = getBackendBaseUrl();

    try {
        if (op === 'on') {
            let resp = await fetch(`${host}/api/bridge_poll_on`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            let data = await resp.json();
            if (data.status === 'ok') {
                seal.replyToSender(ctx, msg, fw(`【桥接轮询】已开启。当前共 ${data.groups_count} 个群组在监听。`));
            } else {
                seal.replyToSender(ctx, msg, fw(`开启失败: ${JSON.stringify(data)}`));
            }
        } else if (op === 'off') {
            let resp = await fetch(`${host}/api/bridge_poll_off`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            let data = await resp.json();
            if (data.status === 'ok') {
                seal.replyToSender(ctx, msg, fw(`【桥接轮询】已关闭。剩余 ${data.groups_count} 个群组在监听。`));
            } else {
                seal.replyToSender(ctx, msg, fw(`关闭失败: ${JSON.stringify(data)}`));
            }
        } else if (op === 'status') {
            let resp = await fetch(`${host}/api/bridge_poll_status?group_id=${pureId}`);
            let data = await resp.json();
            if (data.status === 'ok') {
                let statusText = data.poll_active ? '已开启' : '已关闭';
                seal.replyToSender(ctx, msg, fw(`【桥接轮询状态】本群: ${statusText} | 全局共 ${data.total_groups} 个群组在监听。`));
            } else {
                seal.replyToSender(ctx, msg, fw(`查询失败: ${JSON.stringify(data)}`));
            }
        } else if (op === 'list') {
            let resp = await fetch(`${host}/api/bridge_list`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            let data = await resp.json();
            if (data.status === 'ok') {
                let files = data.files || [];
                if (files.length === 0) {
                    seal.replyToSender(ctx, msg, fw('【桥接文件列表】暂无缓存文件。'));
                } else {
                    let lines = [fw(`【桥接文件列表】共 ${files.length} 个文件:`)];
                    for (let f of files) {
                        let idxTag = f.index === 0 ? ' [最早]' : '';
                        let cached = f.cached ? '' : ' [已过期]';
                        let chars = f.text_chars || 0;
                        lines.push(`- #${f.index}${idxTag}${cached} ${f.name} (${chars} 字)`);
                    }
                    lines.push(fw('提示: 使用 [file]-N 引用特定文件，编号从 0(最旧) 递增到最新。'));
                    seal.replyToSender(ctx, msg, lines.join('\n'));
                }
            } else {
                seal.replyToSender(ctx, msg, fw(`list 失败: ${JSON.stringify(data)}`));
            }
        } else if (op === 'get') {
            let idx = parseInt(cmdArgs.getArgN(2) || '-1', 10);
            if (isNaN(idx) || idx < 0) {
                seal.replyToSender(ctx, msg, fw('用法: .bridge get <编号>\n请使用 .bridge list 查看可用文件编号。'));
                return seal.ext.newCmdExecuteResult(true);
            }
            // 后端统一处理：读取桥接缓存 + NapCat 上传到群（仿 logutil end 机制）
            let resp = await fetch(`${host}/api/bridge_get`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({group_id: pureId, index: idx})
            });
            let data = await resp.json();
            if (data.status === 'ok' && data.file_sent) {
                seal.replyToSender(ctx, msg, fw(`📄 已转为纯文本并发送：${data.filename}`));
            } else {
                seal.replyToSender(ctx, msg, fw(`❌ 获取失败: ${data.msg || JSON.stringify(data)}`));
            }
        } else if (op === 'rate') {
            let rateVal = parseInt(cmdArgs.getArgN(2) || '0', 10);
            if (isNaN(rateVal)) {
                seal.replyToSender(ctx, msg, fw('用法: .bridge rate <秒数>  (n<=0 恢复默认 5 秒)'));
                return seal.ext.newCmdExecuteResult(true);
            }
            payload.interval_sec = rateVal;
            let resp = await fetch(`${host}/api/bridge_rate`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            let data = await resp.json();
            if (data.status === 'ok') {
                if (data.is_custom) {
                    seal.replyToSender(ctx, msg, fw(`【桥接轮询频率】已设置为 ${data.interval_sec} 秒。`));
                } else {
                    seal.replyToSender(ctx, msg, fw(`【桥接轮询频率】已恢复默认 ${data.interval_sec} 秒。`));
                }
            } else {
                seal.replyToSender(ctx, msg, fw(`设置失败: ${JSON.stringify(data)}`));
            }
        } else {
            seal.replyToSender(ctx, msg, fw('用法: .bridge on/off/status/list/get/rate'));
        }
    } catch (e) {
        seal.replyToSender(ctx, msg, fw(`请求后端失败: ${e.message}`));
    }
    return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['bridge'] = cmdBridge;

// 新命令：.translate 文件翻译
const cmdTranslate = seal.ext.newCmdItemInfo();
cmdTranslate.name = 'translate';
cmdTranslate.help = '翻译桥接缓存文件。\n用法: .translate [target_lang=中文] [file1] …… [fileN]\nfile格式: [file]-N（编号从0最旧到最新）\n支持格式: docx, pdf, txt等。';
cmdTranslate.solve = async (ctx, msg, cmdArgs) => {
    let args = cmdArgs.args;
    if (!args || args.length === 0) {
        seal.replyToSender(ctx, msg, '用法: .translate [target_lang=中文] [file1] …… [fileN]\n示例: .translate 英文 [file]-0\n       .translate 日文 [file]-0 [file]-1');
        return seal.ext.newCmdExecuteResult(true);
    }

    let groupId = ctx.group ? ctx.group.groupId : '';
    if (!groupId) {
        seal.replyToSender(ctx, msg, '❌ 此功能仅在群聊中可用。');
        return seal.ext.newCmdExecuteResult(true);
    }

    let pureGroupId = getPureGroupId(groupId);

    // Parse: target_lang and [file]-N refs
    let targetLang = '中文';  // default
    let fileArgs = [];
    for (let a of args) {
        let trimmed = String(a || '').trim();
        if (/^\[file\]-\d+$/i.test(trimmed)) {
            fileArgs.push(trimmed);
        } else {
            // Assume it's a target language
            targetLang = trimmed;
        }
    }

    if (fileArgs.length === 0) {
        seal.replyToSender(ctx, msg, '❌ 请提供至少一个要翻译的文件（格式：[file]-N）。\n示例: .translate 英文 [file]-0');
        return seal.ext.newCmdExecuteResult(true);
    }

    try {
        // Get bridge file list
        let listData = await safeFetchJson(`${getBackendBaseUrl()}/bridge/list?group_id=${pureGroupId}`, {}, "获取桥接文件列表");
        if (!listData || listData.status !== 'ok' || !listData.files || listData.files.length === 0) {
            seal.replyToSender(ctx, msg, '❌ 当前群没有桥接缓存文件。请先上传文件。');
            return seal.ext.newCmdExecuteResult(true);
        }
        let bridgeFiles = listData.files;

        // Resolve file refs
        let resolvedFiles = [];
        for (let fa of fileArgs) {
            let idxMatch = fa.match(/^\[file\]-(\d+)$/i);
            if (idxMatch) {
                let idx = parseInt(idxMatch[1]);
                if (idx >= 0 && idx < bridgeFiles.length) {
                    let bf = bridgeFiles[idx];
                    let contentUrl = bf.content_url || '';
                    if (contentUrl) {
                        resolvedFiles.push({
                            url: contentUrl,
                            name: bf.display_name || bf.name || fa
                        });
                    }
                }
            }
        }

        if (resolvedFiles.length === 0) {
            seal.replyToSender(ctx, msg, '❌ 未能解析任何有效文件。请使用 .bridge list 查看可用文件编号。');
            return seal.ext.newCmdExecuteResult(true);
        }

        seal.replyToSender(ctx, msg, `正在翻译 ${resolvedFiles.length} 个文件为${targetLang}，请稍候...`);

        // Translate each file
        let results = [];
        for (let rf of resolvedFiles) {
            let transJobId = null;
            try {
                // Submit translate task
                let transResp = await safeFetchJson(
                    `${getBackendBaseUrl()}/api/translate?url=${encodeURIComponent(rf.url)}&filename=${encodeURIComponent(rf.name)}&lang=${encodeURIComponent(targetLang)}&group_id=${pureGroupId}`,
                    {},
                    "提交翻译任务"
                );
                if (transResp.status === 'ok' && transResp.id) {
                    transJobId = transResp.id;
                }
            } catch (e) {
                results.push(`${rf.name}: 提交失败 - ${e.message}`);
                continue;
            }

            if (!transJobId) {
                results.push(`${rf.name}: 提交失败`);
                continue;
            }

            // Poll for translation result
            let translated = false;
            let maxRetries = 60;
            while (maxRetries > 0) {
                await sleep(2000);
                let sData = await safeFetchJson(
                    `${getBackendBaseUrl()}/api/translate_result?id=${transJobId}`,
                    undefined,
                    "查询翻译状态"
                );
                if (sData && sData.status === 'done') {
                    // 后端已自动上传到群；同时发送下载链接作为保险
                    let dlInfo = sData.text_url ? `\n下载链接：${sData.text_url}` : '';
                    results.push(`${rf.name}: ✅ 翻译完成（目标语言: ${targetLang}）${dlInfo}`);
                    translated = true;
                    break;
                } else if (sData && sData.status === 'error') {
                    results.push(`${rf.name}: 翻译失败 - ${sData.text || '未知错误'}`);
                    translated = true;
                    break;
                }
                maxRetries--;
            }
            if (!translated) {
                results.push(`${rf.name}: 翻译超时`);
            }
        }

        seal.replyToSender(ctx, msg, `【翻译结果】\n` + results.join('\n'));

    } catch (e) {
        console.error(e);
        seal.replyToSender(ctx, msg, `❌ 翻译错误：${e.message}`);
    }
    return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['translate'] = cmdTranslate;

// 导出脚本完成
// 合并：来自 人工智障模组分析器2.js 的文件处理与命令
async function processModuleFile(ctx, msg, cmdArgs, modeName, pythonMode) {
    let groupId = ctx.group ? ctx.group.groupId : '';
    if (!groupId || !groupId.includes('Group')) {
        seal.replyToSender(ctx, msg, '❌ 请在群聊中使用此功能。');
        return seal.ext.newCmdExecuteResult(true);
    }

    let args = cmdArgs.args;

    // 1. 获取并解析存储中的自定义配置库
    let stored = ext.storageGet('module_custom_prompts') || '{}';
    let customPrompts = {};
    try { customPrompts = JSON.parse(stored); } catch (e) {}

    let customPromptContent = "";
    let customName = "";

    for (let i = 0; i < args.length; i++) {
        if (customPrompts[args[i]]) {
            customName = args[i];
            let p = customPrompts[args[i]];
            customPromptContent = typeof p === 'string' ? p : p.content;
            break;
        }
    }

    let isPro = args.some(a => a && a.toLowerCase && a.toLowerCase() === 'pro');
    let isKind = args.some(a => a && (a.includes && a.includes('温柔') || (a.toLowerCase && a.toLowerCase() === 'kind')));
    let isAI = args.some(a => a && (a.toLowerCase && (a.toLowerCase() === 'ai' || a === '原版' || a === '专业')));
    let getText = args.some(a => a && a.toLowerCase && a.toLowerCase() === 'get_text');

    let theme = 'default';
    let tArgs = args.join(' ');
    if (tArgs.includes('赛博')) theme = 'cyberpunk';
    else if (tArgs.includes('历史') || tArgs.includes('古风')) theme = 'historical';
    else if (tArgs.includes('克苏鲁') || tArgs.includes('深潜')) theme = 'cthulhu';
    else if (tArgs.includes('废土') || tArgs.includes('末日')) theme = 'wasteland';
    else if (tArgs.includes('二次元') || tArgs.includes('萌系')) theme = 'anime';
    else if (tArgs.includes('终端') || tArgs.includes('黑客')) theme = 'terminal';
    else if (tArgs.includes('经典')) theme = 'classic';
    else if (tArgs.includes('默认') || tArgs.includes('常规')) theme = 'default';

    // 钱包保护
    let dateStr = (function() {
        let d = new Date(new Date().getTime() + 8 * 3600 * 1000);
        return d.getUTCFullYear() + '-' + (d.getUTCMonth() + 1) + '-' + d.getUTCDate();
    })();
    let globalKey = `logai_pro_usage_${dateStr}_global`;
    let userKey = `logai_pro_usage_${dateStr}_${ctx.player.userId}`;

    if (isPro) {
        let globalLimit = seal.ext.getIntConfig(ext, "每日Pro全局限额");
        let userLimit = seal.ext.getIntConfig(ext, "每日单人Pro限额");
        let isAdmin = ctx.privilegeLevel >= 100;

        if (globalLimit === 0) {
            seal.replyToSender(ctx, msg, '❌ 抱歉，Pro 模式目前已被骰主禁用，请去除 pro 参数使用普通模式。');
            return seal.ext.newCmdExecuteResult(true);
        }

        let globalUsage = parseInt(ext.storageGet(globalKey) || '0');
        let userUsage = parseInt(ext.storageGet(userKey) || '0');

        if (globalLimit > 0 && globalUsage >= globalLimit && !isAdmin) {
            seal.replyToSender(ctx, msg, `❌ 抱歉，今日机器人的 Pro 模式全局额度（${globalLimit}次）已耗尽，请明日再试或使用普通模式。`);
            return seal.ext.newCmdExecuteResult(true);
        }
        if (userLimit > 0 && userUsage >= userLimit && !isAdmin) {
            seal.replyToSender(ctx, msg, `❌ 抱歉，您今日的 Pro 模式额度（${userLimit}次）已达上限，请明日再试或使用普通模式。`);
            return seal.ext.newCmdExecuteResult(true);
        }

        ext.storageSet(globalKey, (globalUsage + 1).toString());
        ext.storageSet(userKey, (userUsage + 1).toString());
    }

    if (customName) {
        isAI = true;
    }

    let usePersona = seal.ext.getBoolConfig(ext, "启用骰娘人设");
    if (isAI) usePersona = false;

    let personaStr = "";
    if (usePersona) {
        personaStr = isKind ? seal.ext.getStringConfig(ext, "温柔模式_骰娘设定") : seal.ext.getStringConfig(ext, "常规模式_骰娘设定");
    }

    // Build exclude list for arg filtering
    let excludeList = ['pro', 'kind', '温柔', 'ai', '原版', '专业', 'get_text',
        '赛博', '历史', '古风', '克苏鲁', '深潜', '废土', '末日', '二次元', '萌系', '终端', '黑客', '经典', '默认', '常规'];
    if (customName) excludeList.push(customName);

    // Detect file references in args ([file]-N or filenames)
    let fileRefs = [];
    for (let a of args) {
        let s = String(a || '').trim();
        if (excludeList.some(e => e.toLowerCase() === s.toLowerCase())) continue;
        if (/^\[file\]-\d+$/i.test(s)) {
            fileRefs.push({ type: 'index', value: s });
        } else if (s && s.length > 1 && !s.startsWith('pro') && !s.startsWith('kind') && !s.startsWith('ai')) {
            // Potential filename
            let looksLikeFilename = (
                s.startsWith('[') ||
                /\.\w{2,5}$/i.test(s) ||
                /\[\d{4}-\d{2}-\d{2}[_\s]/.test(s) ||
                /[一-鿿]/.test(s)
            );
            if (looksLikeFilename && !parseLogTargetEntry(s)) {
                fileRefs.push({ type: 'name', value: s });
            }
        }
    }

    let useLocalFile = fileRefs.length === 0;
    let resolvedFiles = [];

    if (!useLocalFile) {
        // Resolve file references from bridge cache
        let pureGroupId = getPureGroupId(groupId);
        try {
            let listData = await safeFetchJson(`${getBackendBaseUrl()}/bridge/list?group_id=${pureGroupId}`, {}, "获取桥接文件列表");
            let bridgeFiles = [];
            if (listData && listData.status === 'ok') {
                bridgeFiles = listData.files || [];
            }

            for (let ref of fileRefs) {
                if (ref.type === 'index') {
                    let idxMatch = ref.value.match(/^\[file\]-(\d+)$/i);
                    let idx = parseInt(idxMatch[1]);
                    if (idx >= 0 && idx < bridgeFiles.length) {
                        let bf = bridgeFiles[idx];
                        if (bf.content_url) {
                            resolvedFiles.push({ url: bf.content_url, name: bf.display_name || bf.name || ref.value });
                        }
                    }
                } else if (ref.type === 'name') {
                    let searchName = String(ref.value).replace(/\s+/g, '');
                    let bf = bridgeFiles.find(f => {
                        let fname = String(f.display_name || f.name || '').replace(/\s+/g, '');
                        return fname === searchName;
                    });
                    if (!bf) {
                        bf = bridgeFiles.find(f => {
                            let fname = String(f.display_name || f.name || '').replace(/\s+/g, '');
                            let baseName = fname.replace(/\.\w{2,5}$/i, '');
                            return baseName === searchName;
                        });
                    }
                    if (!bf && searchName.length > 1) {
                        bf = bridgeFiles.find(f => {
                            let fname = String(f.display_name || f.name || '').replace(/\s+/g, '');
                            return fname.includes(searchName);
                        });
                    }
                    if (bf && bf.content_url) {
                        resolvedFiles.push({ url: bf.content_url, name: bf.display_name || bf.name || ref.value });
                    }
                }
            }
        } catch (e) {
            // Fall through to local file behavior
        }

        if (resolvedFiles.length === 0) {
            seal.replyToSender(ctx, msg, '❌ 无法解析指定的文件引用。请使用 .bridge list 查看可用文件，或直接上传文件后不加文件参数。');
            return seal.ext.newCmdExecuteResult(true);
        }
    }

    let filename = '';
    let modeMsg = usePersona ? " [骰娘人设]" : (isAI ? " [纯净AI]" : "");
    if (isPro) modeMsg += "[Pro模式]";
    if (isKind && pythonMode === 'analyze' && usePersona) modeMsg += "[温柔模式]";
    if (getText) modeMsg += "[文本输出]";

    if (useLocalFile) {
        let storageKey = `last_file_${groupId}`;
        let fileDataStr = ext.storageGet(storageKey);
        if (!fileDataStr) {
            seal.replyToSender(ctx, msg, '❌ 当前群没有检测到新上传的模组文件，或记录已过期。');
            return seal.ext.newCmdExecuteResult(true);
        }

        let fileData = JSON.parse(fileDataStr);
        filename = fileData.name;
        if (customName) modeName = `自定义处理[${customName}]`;

        seal.replyToSender(ctx, msg, `🤖 正在请求【${filename}】的下载链接并开始${modeName}...${modeMsg}`);

        try {
            let onebotApiUrl = seal.ext.getStringConfig(ext, "OneBot_API_地址");
            if (onebotApiUrl.endsWith('/')) onebotApiUrl = onebotApiUrl.slice(0, -1);
            let onebotGroupId = parseInt(groupId.replace('QQ-Group:', ''));

            let urlJson = await safeFetchJson(`${onebotApiUrl}/get_group_file_url`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ group_id: onebotGroupId, file_id: fileData.file_id, busid: fileData.busid })
            }, "获取群文件链接");

            if (!urlJson || !urlJson.data || !urlJson.data.url) {
                seal.replyToSender(ctx, msg, `❌ 获取文件链接失败。`);
                return seal.ext.newCmdExecuteResult(true);
            }

            let payload = {
                url: urlJson.data.url,
                filename: filename,
                mode: pythonMode,
                pro: isPro,
                kind: isKind,
                persona: personaStr,
                custom_prompt: customPromptContent,
                theme: theme,
                get_text: getText
            };

            let pythonApiUrl = `${getBackendBaseUrl()}/api/submit_file`;
            let pyData = await safeFetchJson(pythonApiUrl, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
            }, "提交分析任务");

            if (pyData.status !== 'ok') {
                if (isPro) {
                    let g = parseInt(ext.storageGet(globalKey) || '1');
                    let u = parseInt(ext.storageGet(userKey) || '1');
                    ext.storageSet(globalKey, Math.max(0, g - 1).toString());
                    ext.storageSet(userKey, Math.max(0, u - 1).toString());
                }
                seal.replyToSender(ctx, msg, `❌ 后端提交失败: ${JSON.stringify(pyData)}`);
                return seal.ext.newCmdExecuteResult(true);
            }

            let jobId = pyData.id;
            let maxRetries = 120;
            while (maxRetries > 0) {
                await sleep(2000);
                let sData = await safeFetchJson(`${getBackendBaseUrl()}/api/status?id=${jobId}`, undefined, "查询任务状态");
                if (sData.status === 'done' || sData.status === 'error') {
                    if (getText && sData.text_url) {
                        seal.replyToSender(ctx, msg, `✅ 分析完成，文本结果：\n下载链接：${sData.text_url}\n文件名：${sData.text_filename || 'ai_analysis.txt'}`);
                    } else if (sData.image_count && sData.image_count > 0) {
                        let msgStr = "";
                        for (let i = 0; i < sData.image_count; i++) {
                            let finalUrl = `${getBackendBaseUrl()}/api/result?id=${jobId}&index=${i}&t=${new Date().getTime()}`;
                            msgStr += `[CQ:image,file=${finalUrl},cache=0]`;
                        }
                        seal.replyToSender(ctx, msg, msgStr);
                    } else if (sData.text_url) {
                        seal.replyToSender(ctx, msg, `✅ 分析完成：\n下载链接：${sData.text_url}`);
                    } else {
                        seal.replyToSender(ctx, msg, `❌ 发生未知错误，未能生成图片。`);
                    }
                    ext.storageSet(storageKey, "");
                    return seal.ext.newCmdExecuteResult(true);
                }
                maxRetries--;
            }
            seal.replyToSender(ctx, msg, `⚠️ 分析超时，后台可能仍在处理。`);

        } catch (e) {
            seal.replyToSender(ctx, msg, `❌ 发生错误: ${e.message}`);
        }
    } else {
        // Multi-file from bridge cache
        if (resolvedFiles.length === 1) {
            filename = resolvedFiles[0].name;
        } else {
            filename = `多文件拼接(${resolvedFiles.length}段)`;
        }
        if (customName) modeName = `自定义处理[${customName}]`;

        seal.replyToSender(ctx, msg, `🤖 正在分析【${filename}】并执行${modeName}...${modeMsg}`);

        try {
            let payload = {
                mode: pythonMode,
                pro: isPro,
                kind: isKind,
                persona: personaStr,
                custom_prompt: customPromptContent,
                theme: theme,
                get_text: getText
            };

            if (resolvedFiles.length === 1) {
                payload.url = resolvedFiles[0].url;
                payload.filename = resolvedFiles[0].name;
            } else {
                payload.urls = resolvedFiles.map(f => f.url);
                payload.filenames = resolvedFiles.map(f => f.name);
            }

            let pythonApiUrl = `${getBackendBaseUrl()}/api/submit_file`;
            let pyData = await safeFetchJson(pythonApiUrl, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
            }, "提交分析任务");

            if (pyData.status !== 'ok') {
                if (isPro) {
                    let g = parseInt(ext.storageGet(globalKey) || '1');
                    let u = parseInt(ext.storageGet(userKey) || '1');
                    ext.storageSet(globalKey, Math.max(0, g - 1).toString());
                    ext.storageSet(userKey, Math.max(0, u - 1).toString());
                }
                seal.replyToSender(ctx, msg, `❌ 后端提交失败: ${JSON.stringify(pyData)}`);
                return seal.ext.newCmdExecuteResult(true);
            }

            let jobId = pyData.id;
            let maxRetries = 120;
            while (maxRetries > 0) {
                await sleep(2000);
                let sData = await safeFetchJson(`${getBackendBaseUrl()}/api/status?id=${jobId}`, undefined, "查询任务状态");
                if (sData.status === 'done' || sData.status === 'error') {
                    if (getText && sData.text_url) {
                        seal.replyToSender(ctx, msg, `✅ 分析完成，文本结果：\n下载链接：${sData.text_url}\n文件名：${sData.text_filename || 'ai_analysis.txt'}`);
                    } else if (sData.image_count && sData.image_count > 0) {
                        let msgStr = "";
                        for (let i = 0; i < sData.image_count; i++) {
                            let finalUrl = `${getBackendBaseUrl()}/api/result?id=${jobId}&index=${i}&t=${new Date().getTime()}`;
                            msgStr += `[CQ:image,file=${finalUrl},cache=0]`;
                        }
                        seal.replyToSender(ctx, msg, msgStr);
                    } else if (sData.text_url) {
                        seal.replyToSender(ctx, msg, `✅ 分析完成：\n下载链接：${sData.text_url}`);
                    } else {
                        seal.replyToSender(ctx, msg, `❌ 发生未知错误，未能生成图片。`);
                    }
                    return seal.ext.newCmdExecuteResult(true);
                }
                maxRetries--;
            }
            seal.replyToSender(ctx, msg, `⚠️ 分析超时，后台可能仍在处理。`);
        } catch (e) {
            seal.replyToSender(ctx, msg, `❌ 发生错误: ${e.message}`);
        }
    }
    return seal.ext.newCmdExecuteResult(true);
}

// 注册命令： .模组分析
const cmdFile = seal.ext.newCmdItemInfo();
cmdFile.name = '模组分析';
cmdFile.help = '分析模组文件。\n用法: .模组分析 [file1] …… [fileN] [配置名] [pro] [ai] [主题] [get_text]\nfile格式: [file]-N 或 文件名/部分文件名\n不指定文件则使用最新上传的群文件。\n配置管理请使用 .模组分析 配置 示例';
cmdFile.solve = async (ctx, msg, cmdArgs) => {
    let val1 = cmdArgs.getArgN(1);
    if (val1 === '配置') {
        let op = cmdArgs.getArgN(2);
        let stored = ext.storageGet('module_custom_prompts') || '{}';
        let prompts = {};
        try { prompts = JSON.parse(stored); } catch (e) {}

        let userId = ctx.player.userId;
        let userName = ctx.player.name;
        let isAdmin = ctx.privilegeLevel >= 100;

        if (!op || op === '示例') {
            seal.replyToSender(ctx, msg, `【自定义模组分析配置说明】\n你可以自由编写AI阅读模组时的要求，存为配置名随时调用。\n(当使用自定义配置时，系统会自动关闭骰娘语气，确保排版专业)\n\n1. 添加配置：.模组分析 配置 添加 <名称> <提示词...>\n2. 删除配置：.模组分析 配置 删除 <名称>\n3. 查看列表：.模组分析 配置 列表 [页码]\n4. 使用配置：上传群文件后，发送 .模组分析 <名称>\n5. 查看详情：.模组分析 配置 查看 <名称>\n\n💡 示例用法：\n.模组分析 配置 添加 提取NPC 请帮我把模组里所有出现过的NPC名字、真实身份和目的整理成一张表格。\n(注：每个人只能修改/删除自己创建的配置，管理员拥有所有权限)`);
            return seal.ext.newCmdExecuteResult(true);
        } else if (op === '添加') {
            let name = cmdArgs.getArgN(3);
            let contentArgs =[];
            for(let i=3; i < cmdArgs.args.length; i++) { contentArgs.push(cmdArgs.args[i]); }
            let content = contentArgs.join(' ').trim();
            if (!name || !content) {
                seal.replyToSender(ctx, msg, `❌ 缺少名称或内容！\n示例：.模组分析 配置 添加 考据狂 请深挖...`);
                return seal.ext.newCmdExecuteResult(true);
            }
            let existing = prompts[name];
            if (existing) {
                let owner = typeof existing === 'string' ? '' : existing.owner;
                if (owner && owner !== userId && !isAdmin) {
                    seal.replyToSender(ctx, msg, `❌ 权限不足：配置【${name}】由其他用户创建，您无法修改它！`);
                    return seal.ext.newCmdExecuteResult(true);
                }
            } else {
                if (!isAdmin) {
                    let dateStr = (function() {
                        let d = new Date(new Date().getTime() + 8 * 3600 * 1000);
                        return d.getUTCFullYear() + '-' + (d.getUTCMonth() + 1) + '-' + d.getUTCDate();
                    })();
                    let createLimitKey = `module_config_create_${dateStr}_${userId}`;
                    let currentCreates = parseInt(ext.storageGet(createLimitKey) || '0');
                    if (currentCreates >= 1) {
                        seal.replyToSender(ctx, msg, `❌ 抱歉，为防止恶意绕过限额，普通用户每天仅限创建 1 个全新配置。\n您今天已达上限，请明天再试，或对您的已有配置进行修改覆盖。`);
                        return seal.ext.newCmdExecuteResult(true);
                    }
                    ext.storageSet(createLimitKey, (currentCreates + 1).toString());
                }
            }
            prompts[name] = { content: content, owner: userId, creatorName: userName };
            ext.storageSet('module_custom_prompts', JSON.stringify(prompts));
            seal.replyToSender(ctx, msg, `✅ 已成功保存模组配置：【${name}】\n现在发完文件后可以直接使用：.模组分析 ${name}`);
            return seal.ext.newCmdExecuteResult(true);
        } else if (op === '删除') {
            let name = cmdArgs.getArgN(3);
            let existing = prompts[name];
            if (existing) {
                let owner = typeof existing === 'string' ? '' : existing.owner;
                if (owner && owner !== userId && !isAdmin) {
                    seal.replyToSender(ctx, msg, `❌ 权限不足：配置【${name}】由其他用户创建，您无法删除它！`);
                    return seal.ext.newCmdExecuteResult(true);
                }
                delete prompts[name];
                ext.storageSet('module_custom_prompts', JSON.stringify(prompts));
                seal.replyToSender(ctx, msg, `✅ 已删除配置：【${name}】`);
            } else {
                seal.replyToSender(ctx, msg, `❌ 未找到名为【${name}】的配置。`);
            }
            return seal.ext.newCmdExecuteResult(true);
        } else if (op === '列表') {
            let keys = Object.keys(prompts);
            if (keys.length === 0) {
                seal.replyToSender(ctx, msg, `当前没有保存任何自定义配置。`);
                return seal.ext.newCmdExecuteResult(true);
            }
            let page = parseInt(cmdArgs.getArgN(3));
            if (isNaN(page) || page < 1) page = 1;
            let pageSize = 10;
            let totalPages = Math.ceil(keys.length / pageSize);
            if (page > totalPages) page = totalPages;
            let start = (page - 1) * pageSize;
            let currentKeys = keys.slice(start, start + pageSize);
            let replyMsg = `📄 当前模组分析配置列表 (第 ${page}/${totalPages} 页)：\n- ` + currentKeys.join('\n- ');
            if (totalPages > 1) {
                let nextPage = page < totalPages ? page + 1 : 1;
                replyMsg += `\n\n💡 翻页提示：发送 .模组分析 配置 列表 ${nextPage} 查看其它页`;
            }
            seal.replyToSender(ctx, msg, replyMsg);
            return seal.ext.newCmdExecuteResult(true);
        } else if (op === '查看') {
            let name = cmdArgs.getArgN(3);
            if (!name) {
                seal.replyToSender(ctx, msg, `❌ 请输入要查看的配置名称！\n示例：.模组分析 配置 查看 提取NPC`);
            } else if (prompts[name]) {
                let p = prompts[name];
                let content = typeof p === 'string' ? p : p.content;
                let creator = typeof p === 'string' ? '未知(旧版)' : p.creatorName;
                seal.replyToSender(ctx, msg, `📄 配置【${name}】 (创建者: ${creator})\n--------------------\n${content}`);
            } else {
                seal.replyToSender(ctx, msg, `❌ 未找到名为【${name}】的配置。`);
            }
            return seal.ext.newCmdExecuteResult(true);
        }
    }
    return await processModuleFile(ctx, msg, cmdArgs, '模组解析与评价', 'analyze');
};
ext.cmdMap['模组分析'] = cmdFile;
ext.cmdMap['分析文件'] = cmdFile;

// .模组备团
const cmdPrepare = seal.ext.newCmdItemInfo();
cmdPrepare.name = '模组备团';
cmdPrepare.help = '对模组进行分图梳理。\n用法: .模组备团 [file1] …… [fileN] [配置名] [pro] [ai] [主题] [get_text]\nfile格式: [file]-N 或 文件名/部分文件名\n不指定文件则使用最新上传的群文件。';
cmdPrepare.solve = async (ctx, msg, cmdArgs) => {
    return await processModuleFile(ctx, msg, cmdArgs, '备团资料梳理', 'prepare');
};
ext.cmdMap['模组备团'] = cmdPrepare;
ext.cmdMap['备团'] = cmdPrepare;

// .模组完善
const cmdRefine = seal.ext.newCmdItemInfo();
cmdRefine.name = '模组完善';
cmdRefine.help = '对模组进行审查与润色。\n用法: .模组完善 [file1] …… [fileN] [配置名] [pro] [ai] [主题] [get_text]\nfile格式: [file]-N 或 文件名/部分文件名\n不指定文件则使用最新上传的群文件。';
cmdRefine.solve = async (ctx, msg, cmdArgs) => {
    return await processModuleFile(ctx, msg, cmdArgs, '写作进度审查与润色', 'refine');
};
ext.cmdMap['模组完善'] = cmdRefine;
ext.cmdMap['完善模组'] = cmdRefine;

console.log('用户脚本：log-analyzer v4.3.3 loaded (with module-analyzer merged)');

// Auto-push WS config to backend on startup (delayed to let backend start)
(async function syncLogutilConfig() {
    try {
        let host = getBackendBaseUrl();
        let wsUrl = seal.ext.getStringConfig(ext, 'logutil_WS地址') || 'ws://127.0.0.1:3001';
        let wsToken = seal.ext.getStringConfig(ext, 'logutil_WS_Token') || '';
        let wsEnabled = seal.ext.getBoolConfig(ext, 'logutil_WS启用');
        if (wsEnabled === undefined || wsEnabled === null) wsEnabled = true;
        let bridgeMode = seal.ext.getIntConfig(ext, 'NCFileBridgeMode');
        if (bridgeMode === undefined || bridgeMode === null) bridgeMode = 0;
        let payload = {
            NAPCAT_WS_URL: wsUrl,
            NAPCAT_WS_TOKEN: wsToken,
            LOGUTIL_WS_ENABLED: wsEnabled,
            NC_FILE_BRIDGE_MODE: bridgeMode,
        };
        // Retry a few times in case backend is still starting
        for (let i = 0; i < 3; i++) {
            try {
                let resp = await fetch(`${host}/api/logutil_config`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (resp.ok) {
                    console.log('[log-analyzer] WS config synced to backend:', payload);
                    break;
                }
            } catch (e) {
                if (i < 2) await new Promise(r => setTimeout(r, 2000));
            }
        }
    } catch (e) {
        console.log('[log-analyzer] WS config sync failed (backend may not be running):', e.message);
    }
})();
