// ==UserScript==
// @name         LogUtil 日志录制与文件桥接
// @author       Air, Gemini, fanmm, chaye2333
// @version      4.5.3-logutil
// @description  LogUtil 日志录制与 NapCat 文件桥接插件，支持 .logutil 与 .bridge 命令。
// @timestamp    1781107200
// @license      Apache-2.0
// @homepageURL  https://github.com/fanmm01/logai/
// @updateURL    https://raw.githubusercontent.com/fanmm01/logai/refs/heads/main/instance/logutil/frontend.js
// ==/UserScript==

// LogUtil + Bridge 独立插件，仅包含日志录制与文件桥接功能。

let ext = seal.ext.find('log-analyzer');
if (!ext) {
    ext = seal.ext.new('log-analyzer', 'Air', '4.5.3-logutil');
    seal.ext.register(ext);
}

// 保留 logutil 与 bridge 相关配置项
seal.ext.registerStringConfig(ext, "Python后端服务地址", "http://127.0.0.1:8001", "Python 后端服务的地址，用于提交分析任务和获取结果。");
seal.ext.registerIntConfig(ext, "Python后端服务端口", 8001, "Python 后端服务的端口号（默认8001）。修改后前端所有请求将使用新端口。");
seal.ext.registerStringConfig(ext, "OneBot_API_地址", "http://127.0.0.1:34567", "Bot客户端的HTTP监听地址");
seal.ext.registerBoolConfig(ext, "启用HTTP文件桥接", true, "开启后通过HTTP中转服务读取最近群文件，不再依赖群内文本回传。");
seal.ext.registerStringConfig(ext, "HTTP文件桥接读取API", "http://127.0.0.1:8001/bridge/latest", "中转服务读取接口，默认使用本地服务。建议使用POST JSON。");
seal.ext.registerStringConfig(ext, "HTTP文件桥接Token", "", "可选，若中转服务启用鉴权则在此填写。");
seal.ext.registerBoolConfig(ext, "调试日志", true, "开启后在命令行输出桥接请求、解析与提交流程详细日志。");
seal.ext.registerBoolConfig(ext, "启用文本文件桥接", true, "开启后可通过纯文本桥接消息记录最新群文件，不依赖CQ:file解析。");
seal.ext.registerStringConfig(ext, "文本桥接前缀", "[LogFileBridge]", "仅处理以该前缀开头的纯文本桥接消息。建议与外部脚本保持一致。");
seal.ext.registerBoolConfig(ext, "文本桥接自动提示", true, "开启后，收到桥接消息会回一条记录成功提示。默认关闭以减少刷屏。");


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

function getDigits(str) {
    if (!str) return '';
    const m = String(str).match(/\d+/g);
    return m ? m.join('') : '';
}

function getBridgeTokenHeader() {
    let token = (seal.ext.getStringConfig(ext, "HTTP文件桥接Token") || '').trim();
    if (!token) return {};
    return { "Authorization": token };
}

// v4.4.0: 短别名展开 — F14→[file]-14, L0→[link]-0, H23→[history]-23
// v4.4.4: 也修复 SealDice 去括号导致的 file-0/link-0/history-0 格式
// v4.4.4.1: 跨群访问 F14-123456→[file]-14-123456
function expandShortAlias(raw) {
    let s = String(raw || '').trim();
    // 跨群短别名: F14-123456, L0-999888
    let cm = s.match(/^([FLH])(\d+)-(\d+)$/i);
    if (cm) {
        let prefix = cm[1].toUpperCase();
        let num = cm[2];
        let gid = cm[3];
        if (prefix === 'F') return `[file]-${num}-${gid}`;
        if (prefix === 'L') return `[link]-${num}-${gid}`;
        if (prefix === 'H') return `[history]-${num}-${gid}`;
    }
    // 短别名: F14, L0, H23
    let m = s.match(/^([FLH])(\d+)$/i);
    if (m) {
        let prefix = m[1].toUpperCase();
        let num = m[2];
        if (prefix === 'F') return `[file]-${num}`;
        if (prefix === 'L') return `[link]-${num}`;
        if (prefix === 'H') return `[history]-${num}`;
    }
    // v4.4.4: 修复 SealDice 去括号 — file-0→[file]-0, link-0→[link]-0, history-0→[history]-0
    let bm = s.match(/^(file|link|history)-(\d+)$/i);
    if (bm) {
        let type = bm[1].toLowerCase();
        return `[${type}]-${bm[2]}`;
    }
    return raw;
}

function getBackendBaseUrl() {
    let port = seal.ext.getIntConfig(ext, "Python后端服务端口");
    if (!port || port <= 0) port = 8001;
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
    // v4.4.5: also match optional cross-group suffix [file]-N-GID
    let fileIdxMatch = val.match(/^\[file\]-(\d+)(?:-(\d+))?(?:\s|$)/i);
    if (fileIdxMatch) {
        let r = { key: fileIdxMatch[1], source: 'bridge_file', password: '' };
        if (fileIdxMatch[2]) r.cross_group_id = fileIdxMatch[2];
        return r;
    }

    // v4.4.0: [link]-N pattern: reference bridge-cached link text by index
    let linkIdxMatch = val.match(/^\[link\]-(\d+)(?:-(\d+))?(?:\s|$)/i);
    if (linkIdxMatch) {
        let r = { key: linkIdxMatch[1], source: 'bridge_link', password: '' };
        if (linkIdxMatch[2]) r.cross_group_id = linkIdxMatch[2];
        return r;
    }

    // v4.4.3: [history]-N pattern: reference evicted bridge items by index
    let historyIdxMatch = val.match(/^\[history\]-(\d+)(?:-(\d+))?(?:\s|$)/i);
    if (historyIdxMatch) {
        let r = { key: historyIdxMatch[1], source: 'bridge_history', password: '' };
        if (historyIdxMatch[2]) r.cross_group_id = historyIdxMatch[2];
        return r;
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
        const preview = text.length > 300 ? (text.slice(0, 300) + "...") : text;
        throw new Error(`${tag} HTTP${resp.status} 返回非JSON: ${preview}`);
    }
}

// 通用 JSON fetch 封装，带完整调试信息
async function fetchJson(url, options) {
    let resp;
    try {
        resp = await fetch(url, options);
    } catch (e) {
        throw new Error(`无法连接后端(${url}): ${e.message}`);
    }
    const text = await resp.text();
    if (!resp.ok) {
        throw new Error(`后端HTTP${resp.status}(${url}): ${text.slice(0, 200)}`);
    }
    try {
        return JSON.parse(text);
    } catch (e) {
        throw new Error(`后端返回非JSON(${url}): ${text.slice(0, 200)}`);
    }
}

// --- 核心调度函数 ---
// --- 核心调度函数 ---

// v4.4.1: .halt 强制停止AI生成（所有用户可用）
const cmdHalt = seal.ext.newCmdItemInfo();
cmdHalt.name = 'halt';
cmdHalt.help = '强制停止当前群内所有进行中的AI生成任务。\n用法: .halt';
cmdHalt.solve = async (ctx, msg, cmdArgs) => {
    let groupId = ctx.group ? ctx.group.groupId : '';
    if (!groupId) {
        seal.replyToSender(ctx, msg, '❌ 此功能仅在群聊中可用。');
        return seal.ext.newCmdExecuteResult(true);
    }
    try {
        let url = `${getBackendBaseUrl()}/api/halt`;
        let data = await fetchJson(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({group_id:getPureGroupId(groupId)})});
        if (data.status === 'ok') {
            seal.replyToSender(ctx, msg, `🛑 已停止 ${data.count} 个进行中的任务。`);
        } else {
            seal.replyToSender(ctx, msg, `❌ 停止失败: ${JSON.stringify(data)}`);
        }
    } catch(e) {
        seal.replyToSender(ctx, msg, `❌ 停止请求失败: ${e.message}`);
    }
    return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['halt'] = cmdHalt;

// 新命令：.aiutil 快速AI分析（不保存配置）
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
    cmdArgs.args = (cmdArgs.args || []).map(expandShortAlias);
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
    let tokenRegex = /(?:\[file\]-\d+)|(?:\[link\]-\d+)|(?:\[history\]-\d+)|(?:https?:\/\/\S+)|(?:"[^"]*")|(?:\S+)/gi;
    let m;
    while ((m = tokenRegex.exec(restText)) !== null) {
        tokens.push(m[0]);
    }
    // v4.4.4: apply short alias expansion to re-tokenized tokens
    tokens = tokens.map(expandShortAlias);

    let op = (tokens[0] || '').toLowerCase();
    if (op === 'help') {
        seal.replyToSender(ctx, msg, fw('.logutil <子命令>\nnew [名称] [raw]        新建日志并开始录制\non [名称] [raw]         继续已有日志\noff                     暂停录制\nend [名称] [del_paren]  结束并导出\nlist                    列出本群日志\nget [名称]              导出日志\nclear [名称]            删除日志\nwsconfig                查看/配置WS监听\n复合: .logutil [new] <op...> [end]\n修饰符: raw=跳过消息头解析直接拼接原始文本 | del_paren=删除括号包裹内容\n修饰符可在任意位置使用。op支持: [file]-N/[link]-N/[history]-N、短别名、跨群、染色器链接、文本'));
        return seal.ext.newCmdExecuteResult(true);
    }
    let rawArgs = tokens.slice(1);
    // v4.4.4: raw 修饰符可在任意位置生效
    let raw_mode = tokens.some(t => (t || '').toLowerCase() === 'raw');
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
            return /^\[file\]-\d+$/i.test(s) || /^\[link\]-\d+$/i.test(s) || /^\[history\]-\d+$/i.test(s) || /^https?:\/\//i.test(s) || !!parseLogTargetEntry(s);
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
                               /^\[link\]-\d+$/i.test(cand) ||
                               /^\[history\]-\d+$/i.test(cand) ||
                               /^https?:\/\//i.test(cand) ||
                               (cand.toLowerCase() === 'end') ||
                               (cand.toLowerCase() === 'logai') ||
                               (cand.toLowerCase() === 'raw') ||
                               (cand.toLowerCase() === 'del_paren') ||
                               (cand.toLowerCase() === 'delparen') ||
                               (cand.toLowerCase() === 'del-paren') ||
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
                return s !== 'new' && s !== 'end' && s !== 'logai' && s !== 'raw' && s !== 'del_paren' && s !== 'delparen' && s !== 'del-paren' && !logaiModeLower.has(s);
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
            if (raw_mode) compoundPayload.raw = true;

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
            let data = await fetchJson(`${host}/api/logutil_list?group_id=${getPureGroupId(groupId)}`);
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
    '.bridge list [file|link|history]  // 列出桥接缓存（默认 file+link）',
    '.bridge master  // 查看 Web 管理界面地址',
    '.bridge get [file]-N/[link]-N/[history]-N  // 获取编号文件并发送到群',
    '.bridge del [file]-N [link]-N ...  // 删除指定编号的桥接项(v4.4)',
    '.bridge rate n   // 设置轮询间隔为 n 秒（n<=0 恢复默认）',
].join('\n'));
cmdBridge.solve = async (ctx, msg, cmdArgs) => {
    cmdArgs.args = (cmdArgs.args || []).map(expandShortAlias);
    let op = (cmdArgs.getArgN(1) || '').toLowerCase();
    if (op === 'help') {
        seal.replyToSender(ctx, msg, fw('.bridge <子命令>\non/off/status   控制桥接轮询\nrate <秒>         设置轮询间隔\nlist [file|link|history|all]  列出缓存 (无参数=file+link, all=全部)\nmaster            查看Web管理界面\nget <ref>         获取文件到群\ndel <ref>...      删除指定缓存项\n引用格式: [file]-N/[link]-N/[history]-N | 短别名 F14/L0/H23 | 跨群 F0-群号'));
        return seal.ext.newCmdExecuteResult(true);
    }
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
            // v4.4.0: 支持 filter 参数 (file/link/history/all)，默认 all 显示 file+link
            let filterArg = (cmdArgs.getArgN(2) || '').toLowerCase();
            let validFilters = ['file', 'link', 'history', 'all'];
            if (filterArg && !validFilters.includes(filterArg)) {
                filterArg = '';
            }
            payload.filter = filterArg;

            let data = await fetchJson(`${host}/api/bridge_list`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            if (data.status === 'ok') {
                let files = data.files || [];
                let links = data.links || [];
                let history = data.history || [];
                let showFiles = (!filterArg || filterArg === 'all' || filterArg === 'file');
                let showLinks = (!filterArg || filterArg === 'all' || filterArg === 'link');
                let showHistory = (filterArg === 'all' || filterArg === 'history');

                // v4.4.4.1: always use merged chat record format
                let msgNodes = [];
                if (showFiles) {
                    if (files.length > 0) {
                        let fileLines = [fw(`【文件】共 ${files.length} 个文件。`)];
                        for (let f of files) {
                            let chars = f.text_chars || 0;
                            let preview = (f.preview || f.name || '').slice(0, 12);
                            fileLines.push(`#${f.index} [file] ${f.name} (${chars}字) | ${preview}`);
                        }
                        fileLines.push(fw('提示: 使用 [file]-N 引用特定文件，编号从 0(最旧) 递增到最新。'));
                        msgNodes.push(fileLines.join('\n'));
                    } else {
                        msgNodes.push(fw('【文件】暂无缓存文件。'));
                    }
                }

                if (showLinks) {
                    if (links.length > 0) {
                        let linkLines = [fw(`【链接】共 ${links.length} 个链接。`)];
                        for (let l of links) {
                            let chars = l.text_chars || 0;
                            let preview = l.preview || (l.url || l.name || '').slice(0, 30);
                            linkLines.push(`#${l.index} [link] ${(l.url||'').slice(0,50)} (${chars}字) | ${preview}`);
                        }
                        linkLines.push(fw('提示: 使用 [link]-N 引用特定链接文本，编号从 0(最旧) 递增到最新。'));
                        msgNodes.push(linkLines.join('\n'));
                    } else {
                        msgNodes.push(fw('【链接】暂无缓存链接。'));
                    }
                }

                if (showHistory) {
                    if (history.length > 0) {
                        let histLines = [fw(`【历史记录】共 ${history.length} 条（0=最新）。`)];
                        for (let h of history) {
                            let chars = h.text_chars || 0;
                            let preview = (h.name || h.url || '').slice(0, 30);
                            histLines.push(`#${h.index} [${h._type || '?'}] ${preview} (${chars} 字)`);
                        }
                        histLines.push(fw('提示: 使用 [history]-N 引用历史记录。使用 .bridge del [history]-N 删除。'));
                        msgNodes.push(histLines.join('\n'));
                    } else {
                        msgNodes.push(fw('【历史记录】暂无历史记录。'));
                    }
                }

                // Always show master link
                msgNodes.push(fw('使用 .bridge master 查看 Web 管理界面。'));

                if (msgNodes.length === 0) {
                    seal.replyToSender(ctx, msg, fw('暂无桥接缓存数据。'));
                } else {
                    // Send as merged chat record (ref: getSong_v2.js sendForwardMessage)
                    let sentForward = false;
                    try {
                        if (typeof http !== 'undefined' && http && typeof http.getData === 'function') {
                            const epId = (ctx && ctx.endPoint && ctx.endPoint.userId) || '';
                            const userId = getDigits(epId) || '10000';
                            const nickname = seal.formatTmpl ? seal.formatTmpl(ctx, '核心:骰子名字') || '骰娘' : '骰娘';
                            const messages = msgNodes.map(t => ({
                                type: 'node',
                                data: {
                                    user_id: userId,
                                    nickname: nickname,
                                    content: { type: 'text', data: { text: String(t || '') } }
                                }
                            }));
                            const title = fw(`桥接列表 - ${showFiles ? '文件' : ''}${showFiles && showLinks ? ' + ' : ''}${showLinks ? '链接' : ''}`);
                            const forwardData = {
                                messages: messages,
                                news: [{ text: title }],
                                prompt: title,
                                summary: `查看${messages.length}条消息`,
                                source: fw(`${nickname}桥接列表`),
                            };
                            if (ctx.isPrivate) {
                                const uid = getDigits(ctx.player && ctx.player.userId);
                                if (uid) forwardData.user_id = uid;
                            } else {
                                const gid = getDigits(ctx.group && ctx.group.groupId);
                                if (gid) forwardData.group_id = gid;
                            }
                            http.getData(epId, 'send_forward_msg', forwardData);
                            sentForward = true;
                        }
                    } catch (e) {
                        // fallback to plain text below
                    }
                    if (!sentForward) {
                        seal.replyToSender(ctx, msg, msgNodes.join('\n\n'));
                    }
                }
            } else {
                seal.replyToSender(ctx, msg, fw(`list 失败: ${JSON.stringify(data)}`));
            }
        } else if (op === 'master') {
            let resp = await fetch(`${host}/api/bridge_master`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({group_id: pureId})
            });
            let data = await resp.json();
            if (data.status === 'ok' && data.gui_url) {
                seal.replyToSender(ctx, msg, fw(`Web 管理界面: ${data.gui_url}`));
            } else {
                seal.replyToSender(ctx, msg, fw(`获取失败: ${JSON.stringify(data)}`));
            }
        } else if (op === 'get') {
            // v4.4.0: 接受 [file]-N / [link]-N / [history]-N 格式
            let refArg = (cmdArgs.getArgN(2) || '').trim();
            if (!refArg) {
                seal.replyToSender(ctx, msg, fw('用法: .bridge get [file]-N / [link]-N / [history]-N\n请使用 .bridge list 查看可用编号。'));
                return seal.ext.newCmdExecuteResult(true);
            }
            let data = await fetchJson(`${host}/api/bridge_get`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({group_id: pureId, ref: refArg})
            });
            if (data.status === 'ok' && data.file_sent) {
                seal.replyToSender(ctx, msg, fw(`📄 已转为纯文本并发送：${data.filename}`));
            } else {
                seal.replyToSender(ctx, msg, fw(`❌ 获取失败: ${data.msg || JSON.stringify(data)}`));
            }
        } else if (op === 'del') {
            // v4.4.0: .bridge del [file]-N [link]-N [history]-N ...
            let targets = [];
            for (let i = 2; i <= cmdArgs.args.length; i++) {
                let arg = cmdArgs.getArgN(i);
                if (arg) targets.push(arg.trim());
            }
            if (targets.length === 0) {
                seal.replyToSender(ctx, msg, fw('用法: .bridge del [file]-N [link]-N [history]-N ...'));
                return seal.ext.newCmdExecuteResult(true);
            }
            let data = await fetchJson(`${host}/api/bridge_del`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({group_id: pureId, targets: targets})
            });
            if (data.status === 'ok') {
                let msgParts = [fw(`已删除: ${(data.deleted || []).join(', ')}`)];
                if (data.errors && data.errors.length > 0) {
                    msgParts.push(fw(`失败: ${data.errors.join('; ')}`));
                }
                seal.replyToSender(ctx, msg, msgParts.join('\n'));
            } else {
                seal.replyToSender(ctx, msg, fw(`删除失败: ${JSON.stringify(data)}`));
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

// v4.4.1: .translate 文件翻译（参数顺序: [goal-ALL] [lang] file/link...）

// 导出脚本完成
// 合并：来自 人工智障模组分析器2.js 的文件处理与命令

// .模组备团

// .模组完善

console.log('用户脚本：log-analyzer v4.5.3-logutil loaded (logutil + bridge only)');
try { console.log('[log-analyzer] 后端地址: ' + getBackendBaseUrl()); } catch(e) {}

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
