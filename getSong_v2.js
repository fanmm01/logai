// ==UserScript==
// @name         getSong
// @author       fanmm,Copilot
// @version      1.4.1.20260430
// @description  点歌插件：点歌/搜歌/查歌/歌手查询/随机音乐/随机收藏音乐/随机歌单音乐/查询歌词。统一双消息发送流程与参数控制。
// @timestamp    1777056000
// @license      MIT
// @homepageURL  https://github.com/fanyixu/sealdice_js/
// @updateUrl    https://github.com/fanyixu/sealdice_js/getsongs/getSong.js
// @apiProvider  https://api.vkeys.cn/ @Luoyue712
// ==/UserScript==

let ext = seal.ext.find("getSong");
if (!ext) {
	ext = seal.ext.new("getSong", "fanmm,Copilot", "1.4.1.20260430");
	seal.ext.register(ext);
	seal.ext.registerTemplateConfig(ext, "合并消息预览", ["{核心:骰子名字}: 歌词已生成"]);
	seal.ext.registerTemplateConfig(ext, "合并消息外部预览", ["{核心:骰子名字}为{$t玩家}整理了歌词"]);
	seal.ext.registerTemplateConfig(ext, "聊天记录来源", ["{核心:骰子名字}与{$t玩家_RAW}的聊天记录"]);

	const BASE_URL_DEFAULT = "https://api.vkeys.cn/v2/music/tencent";
	const NETEASE_URL_DEFAULT = "https://api.vkeys.cn/v2/music/netease";
	const NETEASE_LYRIC_URL_DEFAULT = "https://api.vkeys.cn/v2/music/netease/lyric";
	const SONG_LINK_URL_DEFAULT = "https://api.vkeys.cn/music/tencent/song/link";
	const DEFAULT_QUALITY = 14;
	const NOTEBOOK_URL_DEFAULT = "https://textdb.online/fanmm";
	const TEXTDB_READ_URL_DEFAULT = "https://textdb.online/api/read";
	const TEXTDB_WRITE_URL_DEFAULT = "https://textdb.online/api/write";
	const CACHE_KEY = "qqmusic_tuple_cache_v2";

	seal.ext.registerStringConfig(ext, "腾讯音乐API", BASE_URL_DEFAULT, "用于QQ音乐搜歌/查歌的API根地址");
	seal.ext.registerStringConfig(ext, "腾讯音乐直链API", SONG_LINK_URL_DEFAULT, "用于获取腾讯音乐播放链接");
	seal.ext.registerStringConfig(ext, "网易云API", NETEASE_URL_DEFAULT, "用于网易云搜歌的API根地址");
	seal.ext.registerStringConfig(ext, "网易云歌词API", NETEASE_LYRIC_URL_DEFAULT, "用于网易云歌词查询的API地址");
	seal.ext.registerStringConfig(ext, "TextDB笔记页", NOTEBOOK_URL_DEFAULT, "卡片URL默认跳转地址");
	seal.ext.registerStringConfig(ext, "TextDB读取API", TEXTDB_READ_URL_DEFAULT, "TextDB读取接口");
	seal.ext.registerStringConfig(ext, "TextDB写入API", TEXTDB_WRITE_URL_DEFAULT, "TextDB写入接口");
	seal.ext.registerStringConfig(ext, "文案-未找到歌曲", "未找到歌曲结果。请更换关键词。", "统一未找到提示");
	seal.ext.registerStringConfig(ext, "文案-无可用结果", "没有可用结果。请更换关键词。", "统一空结果提示");
	seal.ext.registerStringConfig(ext, "文案-音频失败", "获取音频失败。", "录音发送失败提示");

	let fs = null;
	let path = null;
	try {
		fs = require("fs");
		path = require("path");
	} catch (e) {
		fs = null;
		path = null;
	}

	const TEXT = {
		HELP_PICK: "用法：.点歌 歌名 [n] [rec=true|false] [aud=true|false] [record=0|1|2]",
		HELP_SEARCH: "用法：.搜歌 歌名 [n]，n 默认 10，范围 1-30",
		HELP_GET: "用法：.查歌 id [rec=true|false] [aud=true|false] [record=0|1|2]",
		HELP_SINGER: "用法：.歌手查询 name [mode] [n] [page]，mode=1歌曲/2专辑",
		HELP_RANDOM_SINGER: "用法：.随机音乐 [singer] [record=0|1|2]",
		HELP_RANDOM_FAV: "用法：.随机收藏音乐 [qqid] [record=0|1|2]，微信账号无效",
		HELP_RANDOM_DISS: "用法：.随机歌单音乐 [id] [record=0|1|2]",
		HELP_LYRIC: "用法：.查询歌词 [name] [id]（二者择一）",
		HELP_NETEASE: "用法：.网易云 [歌名] 或 .网易云 [id]（返回歌词+163卡片）",
		USAGE_PICK: "用法：.点歌 歌名 [n] [rec=true|false] [aud=true|false] [record=0|1|2]",
		USAGE_SEARCH: "用法：.搜歌 歌名 [n]",
		USAGE_GET: "用法：.查歌 id [rec=true|false] [aud=true|false] [record=0|1|2]",
		USAGE_SINGER: "用法：.歌手查询 name [mode] [n] [page]",
		USAGE_RANDOM_SINGER: "用法：.随机音乐 [singer] [record=0|1|2]",
		USAGE_RANDOM_FAV: "用法：.随机收藏音乐 [qqid] [record=0|1|2]",
		USAGE_RANDOM_DISS: "用法：.随机歌单音乐 [id] [record=0|1|2]",
		USAGE_LYRIC: "用法：.查询歌词 [name] [id]（二者择一）",
		USAGE_NETEASE: "用法：.网易云 [歌名] 或 .网易云 [id]",
		ERR_EMPTY_SONGNAME: "歌名不能为空。",
		ERR_NOT_FOUND: seal.ext.getStringConfig(ext, "文案-未找到歌曲") || "未找到歌曲结果。请更换关键词。",
		ERR_NO_RESULT: seal.ext.getStringConfig(ext, "文案-无可用结果") || "没有可用结果。请更换关键词。",
		ERR_WX_INVALID: "此命令对微信账号无效",
		ERR_AUDIO_FAILED: seal.ext.getStringConfig(ext, "文案-音频失败") || "获取音频失败。",
		TIP_PICK_FALLBACK: "系搜索窗口回退得到。",
		TIP_LYRIC_FALLBACK: "系搜索第1项查歌得到。",
		TIP_SOURCE_SWITCH_QQ: "QQ音乐源不可用，尝试切换至%s...",
	};

	function clampInt(v, def, min, max) {
		const n = parseInt(v, 10);
		if (Number.isNaN(n)) return def;
		if (n < min) return min;
		if (n > max) return max;
		return n;
	}

	function parseBool(v, defVal) {
		if (v === undefined || v === null || v === "") return !!defVal;
		const s = String(v).trim().toLowerCase();
		if (["true", "1", "yes", "y", "on", "url"].includes(s)) return true;
		if (["false", "0", "no", "n", "off"].includes(s)) return false;
		return !!defVal;
	}

	function getArgList(cmdArgs, start) {
		const arr = [];
		let i = start;
		while (true) {
			const v = cmdArgs.getArgN(i);
			if (v === undefined || v === null || v === "") break;
			arr.push(String(v));
			i += 1;
		}
		return arr;
	}

	function parseRecordMode(v) {
		const n = clampInt(v, 0, 0, 2);
		return n;
	}

	function parseSongOptions(argList, defaults) {
		const opt = {
			rec: defaults.rec,
			aud: defaults.aud,
			record: defaults.record,
			rest: [],
		};
		for (const raw of argList || []) {
			const token = String(raw || "").trim();
			const lower = token.toLowerCase();
			if (lower.startsWith("rec=")) {
				opt.rec = parseBool(lower.slice(4), opt.rec);
				continue;
			}
			if (lower.startsWith("aud=")) {
				opt.aud = parseBool(lower.slice(4), opt.aud);
				continue;
			}
			if (lower.startsWith("record=")) {
				opt.record = parseRecordMode(lower.slice(7));
				continue;
			}
			if (lower === "rec" || lower === "aud") {
				opt[lower] = true;
				continue;
			}
			if (lower === "norec") {
				opt.rec = false;
				continue;
			}
			if (lower === "noaud") {
				opt.aud = false;
				continue;
			}
			if (/^record[012]$/.test(lower)) {
				opt.record = parseRecordMode(lower.replace("record", ""));
				continue;
			}
			opt.rest.push(token);
		}
		return opt;
	}

	function toQuery(params) {
		return Object.keys(params || {})
			.filter((k) => params[k] !== undefined && params[k] !== null)
			.map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(params[k])}`)
			.join("&");
	}

	function getCfgString(key, fallback) {
		const v = seal.ext.getStringConfig(ext, key);
		const s = String(v || "").trim();
		return s || fallback;
	}

	function getQQApiBaseUrl() {
		return getCfgString("腾讯音乐API", BASE_URL_DEFAULT);
	}

	function getSongLinkApiUrl() {
		return getCfgString("腾讯音乐直链API", SONG_LINK_URL_DEFAULT);
	}

	function getNeteaseApiUrl() {
		return getCfgString("网易云API", NETEASE_URL_DEFAULT);
	}

	function getNeteaseLyricApiUrl() {
		return getCfgString("网易云歌词API", NETEASE_LYRIC_URL_DEFAULT);
	}

	function getNotebookUrl() {
		return getCfgString("TextDB笔记页", NOTEBOOK_URL_DEFAULT);
	}

	function getTextDbReadUrl() {
		return getCfgString("TextDB读取API", TEXTDB_READ_URL_DEFAULT);
	}

	function getTextDbWriteUrl() {
		return getCfgString("TextDB写入API", TEXTDB_WRITE_URL_DEFAULT);
	}


	async function fetchWithTimeout(url, options, timeoutMs) {
		// 允许长任务使用更长超时，避免被 10s 上限提前截断。
		const ms = clampInt(timeoutMs, 3500, 1000, 3600000);
		return await Promise.race([
			fetch(url, options),
			new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms)),
		]);
	}

	async function apiGet(pathname, params) {
		const base = getQQApiBaseUrl();
		const query = toQuery(params || {});
		const url = query ? `${base}${pathname}?${query}` : `${base}${pathname}`;
		const resp = await fetchWithTimeout(url, undefined, 5000);
		if (!resp.ok) {
			throw new Error(`HTTP ${resp.status}`);
		}
		return await resp.json();
	}

	async function apiGetNetease(params) {
		const base = getNeteaseApiUrl();
		const query = toQuery(params || {});
		const url = query ? `${base}?${query}` : base;
		const resp = await fetchWithTimeout(url, undefined, 5000);
		if (!resp.ok) {
			throw new Error(`HTTP ${resp.status}`);
		}
		return await resp.json();
	}

	function extractSongList(data) {
		if (!data) return [];
		const cands = [
			data.data,
			data.result,
			data.songlist,
			data.list,
			data.songs,
			data,
		];
		for (const c of cands) {
			if (Array.isArray(c)) return c;
			if (c && Array.isArray(c.list)) return c.list;
			if (c && Array.isArray(c.songlist)) return c.songlist;
			if (c && Array.isArray(c.songs)) return c.songs;
			if (c && Array.isArray(c.albumlist)) return c.albumlist;
		}
		return [];
	}

	function pickId(item) {
		if (!item) return "";
		return (
			item.id ||
			item.songid ||
			item.songId ||
			item.musicid ||
			item.mid ||
			item.albumID ||
			item.albumId ||
			item.albumMid ||
			item.albumMID ||
			""
		);
	}

	function pickName(item) {
		if (!item) return "";
		return item.name || item.songname || item.song || item.title || item.albumname || item.albumName || "";
	}

	function normalizeTitleForCompare(v) {
		return String(v || "")
			.toLowerCase()
			.replace(/[\s\u3000]/g, "")
			.replace(/[\-_.~!@#$%^&*()+={}\[\]|\\:;"'<>,.?/`~，。！？、；：‘’“”（）【】《》·]/g, "");
	}

	function isKeywordNameEquivalent(name, keyword) {
		const n1 = normalizeTitleForCompare(name);
		const n2 = normalizeTitleForCompare(keyword);
		if (!n1 || !n2) return false;
		return n1 === n2;
	}

	function selectPreferredSongCandidate(rows, keyword) {
		if (!Array.isArray(rows) || !rows.length) return null;
		const first = rows[0];
		if (isKeywordNameEquivalent(first && first.name, keyword)) {
			return first;
		}
		for (let i = 1; i <= 2; i++) {
			const cand = rows[i];
			if (!cand) continue;
			if (String(cand.name || "") === String(keyword || "") || isKeywordNameEquivalent(cand.name, keyword)) {
				return cand;
			}
		}
		return first;
	}

	function buildLyricSwapKeyword(keyword, firstSinger) {
		const raw = String(keyword || "").trim();
		const singer = String(firstSinger || "").trim();
		const m = raw.match(/^(.+)-(.+)$/);
		if (!m) return "";
		const left = String(m[1] || "").trim();
		const right = String(m[2] || "").trim();
		if (!left || !right || !singer) return "";
		const singerNorm = normalizeTitleForCompare(singer).replace(/\//g, "");
		const leftNorm = normalizeTitleForCompare(left);
		const rightNorm = normalizeTitleForCompare(right);
		if (leftNorm && singerNorm.includes(leftNorm) && rightNorm && !singerNorm.includes(rightNorm)) return right;
		if (rightNorm && singerNorm.includes(rightNorm) && leftNorm && !singerNorm.includes(leftNorm)) return left;
		return "";
	}

	function pickSingerName(item) {
		if (!item) return "";
		if (item.singername) return item.singername;
		if (item.singerName) return item.singerName;
		if (item.singer && typeof item.singer === "string") return item.singer;
		if (item.singer && Array.isArray(item.singer) && item.singer[0] && item.singer[0].name) {
			return item.singer[0].name;
		}
		return "";
	}

	function pickAlbumName(item) {
		if (!item) return "";
		return item.album || item.albumname || item.albumName || item.album_title || "";
	}

	function pickLink(data) {
		if (!data) return "";
		return data.link || data.url || (data.data && (data.data.link || data.data.url)) || "";
	}

	function pickUrl(data) {
		if (!data) return "";
		return data.url || (data.data && data.data.url) || "";
	}

	function pickCover(data) {
		if (!data) return "";
		return data.cover || data.coverUrl || data.cover_url || data.pic || data.picurl || data.image || data.img ||
			(data.data && (data.data.cover || data.data.coverUrl || data.data.cover_url || data.data.pic || data.data.picurl || data.data.image || data.data.img)) || "";
	}

	function containsTrialHint(data) {
		if (!data) return false;
		try {
			const s = JSON.stringify(data);
			if (!s) return false;
			return s.includes("音乐试听") || s.includes("试听");
		} catch (e) {
			return false;
		}
	}

	function isEmptyStreamUrl(v) {
		return /^https?:\/\/ws\.stream\.qqmusic\.qq\.com\/?$/i.test(String(v || "").trim());
	}

	function isPlayableAudioUrl(v) {
		if (!v) return false;
		const s = String(v).trim();
		if (!/^https?:\/\//i.test(s)) return false;
		if (isEmptyStreamUrl(s)) return false;
		return /(ws\.stream\.qqmusic\.qq\.com|\.(mp3|m4a|flac|ape)(\?|$)|\/(M800|C400|F000))/i.test(s);
	}

	function pickPlayableUrl(data) {
		if (!data) return "";
		const cands = [];
		if (typeof data === "string") cands.push(data);
		cands.push(
			pickUrl(data),
			data.link,
			data.playUrl,
			data.play_url,
			data.src,
			data.file,
			data.data && data.data.link,
			data.data && data.data.playUrl,
			data.data && data.data.play_url,
			data.data && data.data.src,
			data.data && data.data.file
		);
		for (const v of cands) {
			if (isPlayableAudioUrl(v)) return String(v);
		}
		return "";
	}

	async function queryGetUrlById(id, quality) {
		const q = clampInt(quality, DEFAULT_QUALITY, 1, 14);
		const songLinkApi = getSongLinkApiUrl();
		let directData = null;
		try {
			const resp = await fetchWithTimeout(`${songLinkApi}?id=${encodeURIComponent(id)}&quality=${encodeURIComponent(q)}`, undefined, 5000);
			if (resp.ok) {
				directData = await resp.json();
			}
		} catch (e) {
			// fallback below
		}

		const directLink = pickLink(directData);
		const directUrl = pickPlayableUrl(directData);
		if (directUrl && !isEmptyStreamUrl(directUrl) && !containsTrialHint(directData)) {
			return { link: directLink, url: directUrl, raw: directData };
		}

		let legacy = null;
		try {
			legacy = await apiGet("/geturl", { id: id, quality: q });
		} catch (e) {
			legacy = null;
		}

		const legacyUrl = legacy && !containsTrialHint(legacy) ? (pickPlayableUrl(legacy) || pickUrl(legacy)) : "";
		return {
			link: directLink || pickLink(legacy),
			url: legacyUrl,
			raw: legacy || directData,
		};
	}

	// Auto quality switching: descend from starting quality until a playable URL is found.
	// Returns { link, url, raw, finalQuality } or { url: "" } if no quality works down to minQuality.
	async function queryGetUrlWithQualityFallback(id, startQuality, minQuality) {
		const sq = clampInt(startQuality, DEFAULT_QUALITY, 1, 14);
		const mq = clampInt(minQuality, 7, 1, 14);
		for (let q = sq; q >= mq; q--) {
			try {
				const result = await queryGetUrlById(id, q);
				const url = result && result.url ? String(result.url).trim() : "";
				if (url && !isEmptyStreamUrl(url) && isPlayableAudioUrl(url)) {
					return { ...result, finalQuality: q };
				}
			} catch (e) {
				// try next quality
			}
		}
		return { link: "", url: "", raw: null, finalQuality: 0 };
	}

	function escapeCqParam(v) {
		return String(v || "")
			.replace(/\[/g, "&#91;")
			.replace(/\]/g, "&#93;")
			.replace(/,/g, "&#44;");
	}

	function getDigits(v) {
		const m = String(v || "").match(/(\d{5,})/);
		return m ? m[1] : "";
	}

	function parseSenderQQ(ctx) {
		const uid = (ctx.player && ctx.player.userId) || "";
		return getDigits(uid);
	}

	function extractLyricBundleQQ(data) {
		if (!data) return { lrc: "", trans: "" };
		const root = data.data && typeof data.data === "object" ? data.data : data;
		return {
			lrc: String(
				root.lyric || root.lrc ||
				(root.lrc && root.lrc.lyric) ||
				""
			),
			trans: String(
				root.trans || root.tlyric || root.translation ||
				(root.tlyric && root.tlyric.lyric) ||
				""
			),
		};
	}

	function extractLyricBundleNetease(data) {
		if (!data) return { lrc: "", trans: "" };
		const root = data.data && typeof data.data === "object" ? data.data : data;
		return {
			lrc: String(root.lrc || root.lyric || (root.lrc && root.lrc.lyric) || ""),
			trans: String(root.trans || root.tlyric || root.translation || (root.tlyric && root.tlyric.lyric) || ""),
		};
	}

	function normalizeLyricRaw(rawLyric) {
		return String(rawLyric || "")
			.replace(/\\r\\n/g, "\n")
			.replace(/\\n/g, "\n")
			.replace(/\r\n/g, "\n");
	}

	function splitLyricLines(rawLyric) {
		const normalized = normalizeLyricRaw(rawLyric);
		const lines = normalized.split("\n");
		return lines
			.map((line) => line
				.replace(/\[(ti|ar|al|by|offset|kana|re|ve):[^\]]*\]/gi, "")
				.replace(/\[[0-9:.]+\]/g, "")
				.trim())
			.filter((line) => line.length > 0);
	}

	function isForeignLyric(rawMainLyric) {
		const s = normalizeLyricRaw(rawMainLyric);
		return /[A-Za-z\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]/.test(s);
	}

	function mergeBilingualLyric(rawMainLyric, rawTransLyric) {
		const main = splitLyricLines(rawMainLyric);
		if (!main.length) return "未查询到歌词。";
		const trans = splitLyricLines(rawTransLyric);
		if (!trans.length || !isForeignLyric(rawMainLyric)) {
			return main.join("\n");
		}
		const n = Math.max(main.length, trans.length);
		const out = [];
		for (let i = 0; i < n; i++) {
			if (i < main.length && main[i]) out.push(main[i]);
			if (i < trans.length && trans[i]) out.push(trans[i]);
		}
		return out.length ? out.join("\n") : "未查询到可展示歌词。";
	}

	async function getLyricById(id) {
		if (!id) return "未查询到歌词。";
		try {
			const lyricData = await apiGet("/lyric", { id: id });
			const bundle = extractLyricBundleQQ(lyricData);
			return mergeBilingualLyric(bundle.lrc, bundle.trans);
		} catch (e) {
			return "歌词获取失败。";
		}
	}

	async function getNeteaseLyricById(id) {
		if (!id) return "未查询到歌词。";
		try {
			const lyricApi = getNeteaseLyricApiUrl();
			const url = `${lyricApi}?id=${encodeURIComponent(id)}`;
			const resp = await fetchWithTimeout(url, undefined, 5000);
			if (!resp.ok) return "歌词获取失败。";
			const rawText = await resp.text();
			let data = null;
			try {
				data = JSON.parse(rawText);
			} catch (e) {
				data = extractFirstJson(rawText);
			}
			if (!data) return "歌词获取失败。";
			const bundle = extractLyricBundleNetease(data);
			return mergeBilingualLyric(bundle.lrc, bundle.trans);
		} catch (e) {
			return "歌词获取失败。";
		}
	}

	function stripAnsi(s) {
		return String(s || "").replace(/\x1b\[[0-9;]*m/g, "");
	}

	function looksLikeHttpUrl(s) {
		return /^https?:\/\//i.test(String(s || "").trim());
	}

	function extractFirstHttpUrl(text) {
		const m = String(text || "").match(/https?:\/\/[^\s\]"'<>]+/i);
		return m ? m[0] : "";
	}

	function extractFirstJson(text) {
		const s = stripAnsi(text || "").trim();
		const starts = [];
		for (let i = 0; i < s.length; i++) {
			if (s[i] === "{" || s[i] === "[") starts.push(i);
		}
		for (const st of starts) {
			const candidate = s.slice(st).trim();
			try {
				return JSON.parse(candidate);
			} catch (e) {
				// continue
			}
		}
		return null;
	}

	function joinUrl(base, pathPart) {
		const b = String(base || "").replace(/\/+$/, "");
		const p = String(pathPart || "").trim();
		if (!p) return b;
		if (/^https?:\/\//i.test(p)) return p;
		const normalizedPath = `/${p.replace(/^\/+/, "")}`;
		if (!b) return normalizedPath;
		if (b.endsWith(normalizedPath)) return b;
		try {
			const u = new URL(b);
			const pathname = String(u.pathname || "").replace(/\/+$/, "") || "/";
			if (pathname === normalizedPath) return b;
		} catch (e) {
			// ignore URL parse errors and keep fallback join behavior
		}
		return `${b}/${p.replace(/^\/+/, "")}`;
	}



	function loadLocalCache() {
		try {
			const raw = ext.storageGet(CACHE_KEY);
			if (!raw) return {};
			const parsed = JSON.parse(raw);
			if (!parsed || typeof parsed !== "object") return {};
			return parsed;
		} catch (e) {
			return {};
		}
	}

	function saveLocalCache(cacheObj) {
		try {
			ext.storageSet(CACHE_KEY, JSON.stringify(cacheObj || {}));
		} catch (e) {
			// ignore
		}
	}

	function tupleKeyById(id) {
		return `id:${String(id || "").trim()}`;
	}

	function normalizeTuple(obj) {
		if (!obj) return null;
		const id = String(obj.id || "").trim();
		if (!id) return null;
		return {
			id: id,
			name: String(obj.name || "").trim(),
			singer: String(obj.singer || "").trim(),
			album: String(obj.album || "").trim(),
			rank: clampInt(obj.rank, 1, 1, 999),
			link: String(obj.link || "").trim(),
			url: String(obj.url || "").trim(),
			cover: String(obj.cover || "").trim(),
			ts: Date.now(),
		};
	}

	function cacheGetById(id) {
		const cache = loadLocalCache();
		return cache[tupleKeyById(id)] || null;
	}

	function cacheUpsertTuple(tupleLike) {
		const t = normalizeTuple(tupleLike);
		if (!t) return;
		const cache = loadLocalCache();
		cache[tupleKeyById(t.id)] = t;
		saveLocalCache(cache);
	}

	async function textDbReadTuple(id) {
		const sid = String(id || "").trim();
		if (!sid) return null;
		const readUrl = getTextDbReadUrl();
		const body = {
			notebook: "fanmm",
			table: "qqmusic_tuple",
			key: tupleKeyById(sid),
		};
		try {
			const resp = await fetchWithTimeout(readUrl, {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify(body),
			}, 2500);
			if (!resp.ok) return null;
			const data = await resp.json();
			const cand = data && (data.value || data.data || data.row || data.item);
			if (!cand) return null;
			if (typeof cand === "string") {
				try {
					return normalizeTuple(JSON.parse(cand));
				} catch (e) {
					return null;
				}
			}
			return normalizeTuple(cand);
		} catch (e) {
			return null;
		}
	}

	async function textDbWriteTuple(tupleLike) {
		const t = normalizeTuple(tupleLike);
		if (!t) return;
		const writeUrl = getTextDbWriteUrl();
		const body = {
			notebook: "fanmm",
			table: "qqmusic_tuple",
			key: tupleKeyById(t.id),
			value: t,
		};
		try {
			await fetchWithTimeout(writeUrl, {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify(body),
			}, 2500);
		} catch (e) {
			// ignore
		}
	}

	async function getTupleFromDbFirst(id) {
		const local = cacheGetById(id);
		if (local) return local;
		const remote = await textDbReadTuple(id);
		if (remote) {
			cacheUpsertTuple(remote);
			return remote;
		}
		return null;
	}

	function randomDelayMs() {
		return 500 + Math.floor(Math.random() * 1001);
	}

	function buildMusicCardCq(song) {
		const title = song.name || "(未知歌曲)";
		const singer = song.singer || "(未知歌手)";
		const album = song.album || "(未知专辑)";
		const content = `${singer} ${album}`;
		const url = song.link || getNotebookUrl();
		const audio = song.url || "1.mp3";
		const image = song.cover || "";
		return `[CQ:music,type=custom,url=${escapeCqParam(url)},audio=${escapeCqParam(audio)},title=${escapeCqParam(title)},content=${escapeCqParam(content)},image=${escapeCqParam(image)}]`;
	}

	function buildRecordCq(song) {
		if (!isPlayableAudioUrl(song.url)) return "";
		return `[CQ:record,file=${song.url}]`;
	}

	function buildSecondMessagePayload(song, recordMode) {
		const card = buildMusicCardCq(song);
		const rec = buildRecordCq(song);
		if (recordMode === 0) return card;
		if (recordMode === 1) return rec || TEXT.ERR_AUDIO_FAILED;
		if (rec) return `${card}\n${rec}`;
		return `${card}\n${TEXT.ERR_AUDIO_FAILED}`;
	}


function makeForwardNode(userId, nickname, text) {
		return {
			type: "node",
			data: {
				user_id: userId,
				nickname: nickname,
				content: {
					type: "text",
					data: {
						text: String(text || ""),
				},
				},
			},
		};
	}

	function sendForwardMessage(ctx, msg, texts, summaryTitle) {
		const epId = (ctx && ctx.endPoint && ctx.endPoint.userId) || "";
		const userId = getDigits(epId) || "10000";
		const nickname = seal.formatTmpl(ctx, "核心:骰子名字") || "骰娘";
		const messages = (texts || []).map((t) => makeForwardNode(userId, nickname, t));

		const newsTmpl = seal.ext.getTemplateConfig(ext, "合并消息预览") || ["QQ音乐结果"];
		const promptTmpl = seal.ext.getTemplateConfig(ext, "合并消息外部预览") || ["QQ音乐结果"];
		const sourceTmpl = seal.ext.getTemplateConfig(ext, "聊天记录来源") || [`${nickname}聊天记录`];
		const news = seal.format(ctx, newsTmpl[Math.floor(Math.random() * newsTmpl.length)]);
		const prompt = seal.format(ctx, promptTmpl[Math.floor(Math.random() * promptTmpl.length)]);
		const source = seal.format(ctx, sourceTmpl[Math.floor(Math.random() * sourceTmpl.length)]);

		const data = {
			messages: messages,
			news: [
				{
					text: summaryTitle || news,
				},
			],
			prompt: summaryTitle || prompt,
			summary: `查看${messages.length}条转发消息`,
			source: source,
		};

		if (ctx.isPrivate) {
			const uid = getDigits(ctx.player && ctx.player.userId);
			if (uid) data.user_id = uid;
		} else {
			const gid = getDigits(ctx.group && ctx.group.groupId);
			if (gid) data.group_id = gid;
		}

		try {
			if (typeof http !== "undefined" && http && typeof http.getData === "function") {
				http.getData(epId, "send_forward_msg", data);
				return true;
			}
		} catch (e) {
			// fallback below
		}
		const fallback = (texts || []).join("\n\n");
		seal.replyToSender(ctx, msg, fallback);
		return false;
	}

	async function sendSongInTwoMessages(ctx, msg, song, opts) {
		// If source-switched to NetEase, use NetEase-style send
		if (song.source === "netease" && song.neteaseId) {
			await sendNeteaseInTwoMessages(ctx, msg, {
				id: song.neteaseId || song.id,
				name: song.name,
				singer: song.singer,
				album: song.album,
			});
			return;
		}
		// Default: QQ music style
		const lyric = song.lyric || await getLyricById(song.id);
		const meta = [
			`歌名：${song.name || "(未知歌曲)"}`,
			`歌手：${song.singer || "(未知歌手)"}`,
			`专辑：${song.album || "(未知专辑)"}`,
			`id: ${song.id || ""}`,
			`link: ${song.link || ""}`,
			`url: ${song.url || ""}`,
		].join("\n");

		sendForwardMessage(ctx, msg, [meta, lyric || "未查询到歌词。"], `${song.name || "QQ音乐"} 歌词`);
		const delay = randomDelayMs();
		setTimeout(() => {
			const payload = buildSecondMessagePayload(song, opts.record);
			seal.replyToSender(ctx, msg, payload);
		}, delay);
	}


	async function searchSongsByWord(word, n, quality) {
		const num = clampInt(n, 10, 1, 30);
		const q = clampInt(quality, DEFAULT_QUALITY, 1, 14);
		const data = await apiGet("", { word: word, page: 1, num: num, quality: q });
		const list = extractSongList(data);
		return list.slice(0, num).map((item, idx) => ({
			name: pickName(item) || word,
			id: String(pickId(item) || ""),
			singer: pickSingerName(item),
			album: pickAlbumName(item),
			cover: pickCover(item),
			rank: idx + 1,
		})).filter((r) => r.id);
	}

	async function searchNeteaseByWord(word) {
		const data = await apiGetNetease({ word: word, page: 1, num: 3, quality: 8 });
		const list = extractSongList(data);
		if (!list.length) return null;
		const rows = list.slice(0, 3).map((item) => ({
			id: String(pickId(item) || ""),
			name: pickName(item) || word,
			singer: pickSingerName(item),
			album: pickAlbumName(item),
		})).filter((r) => r.id);
		if (!rows.length) return null;
		const selected = selectPreferredSongCandidate(rows, word) || rows[0];
		const id = String((selected && selected.id) || "");
		if (!id) return null;
		return {
			id: id,
			name: selected.name || word,
			singer: selected.singer || "",
			album: selected.album || "",
		};
	}

	async function sendNeteaseInTwoMessages(ctx, msg, song) {
		const lyric = await getNeteaseLyricById(song.id);
		const meta = [
			`平台：网易云`,
			`歌名：${song.name || "(未知歌曲)"}`,
			`歌手：${song.singer || "(未知歌手)"}`,
			`专辑：${song.album || "(未知专辑)"}`,
			`id: ${song.id || ""}`,
		].join("\n");

		sendForwardMessage(ctx, msg, [meta, lyric || "未查询到歌词。"], `${song.name || "网易云"} 歌词`);
		const delay = randomDelayMs();
		setTimeout(() => {
			seal.replyToSender(ctx, msg, `[CQ:music,type=163,id=${escapeCqParam(song.id)}]`);
		}, delay);
	}

	async function fetchSongMetaByHint(id, nameHint) {
		const tuple = {
			name: nameHint || "",
			singer: "",
			album: "",
			cover: "",
		};
		try {
			const word = nameHint || id;
			const data = await apiGet("", { word: word, page: 1, num: 8, quality: DEFAULT_QUALITY });
			const list = extractSongList(data);
			let item = null;
			for (const it of list) {
				if (String(pickId(it) || "") === String(id || "")) {
					item = it;
					break;
				}
			}
			if (!item && list.length) item = list[0];
			if (item) {
				tuple.name = pickName(item) || tuple.name;
				tuple.singer = pickSingerName(item) || tuple.singer;
				tuple.album = pickAlbumName(item) || tuple.album;
				tuple.cover = pickCover(item) || tuple.cover;
			}
			if (!tuple.cover) tuple.cover = pickCover(data) || tuple.cover;
		} catch (e) {
			// best effort
		}
		return tuple;
	}


	// Build a song with auto source switching: QQ → NetEase.
	// qualitySwitching: if true, auto-downgrade QQ quality from 14 to 7.
	// ctx, msg: optional — if provided, progress hints are sent to the user.
	async function buildSongWithAutoSwitch(id, hints, opts, qualitySwitching, ctx, msg) {
		const sid = String(id || "").trim();
		if (!sid) return null;

		const cached = await getTupleFromDbFirst(sid);
		const song = {
			id: sid,
			name: (hints && hints.name) || (cached && cached.name) || "",
			singer: (hints && hints.singer) || (cached && cached.singer) || "",
			album: (hints && hints.album) || (cached && cached.album) || "",
			cover: (hints && hints.cover) || (cached && cached.cover) || "",
			rank: (hints && hints.rank) || (cached && cached.rank) || 1,
			link: (hints && hints.link) || (cached && cached.link) || "",
			url: (hints && hints.url) || (cached && cached.url) || "",
			source: "qq",
		};

		const tip = (tpl, ...args) => {
			if (ctx && msg) {
				let s = tpl;
				for (let i = 0; i < args.length; i++) s = s.replace("%s", String(args[i]));
				seal.replyToSender(ctx, msg, s);
			}
		};

		// ── Step 2: QQ music with optional quality switching ──────
		let qqInfo = null;
		if (qualitySwitching) {
			qqInfo = await queryGetUrlWithQualityFallback(song.id, DEFAULT_QUALITY, 7);
		} else {
			try {
				qqInfo = await queryGetUrlById(song.id, DEFAULT_QUALITY);
			} catch (e) {
				qqInfo = null;
			}
		}
		const qqUrl = qqInfo && qqInfo.url ? String(qqInfo.url).trim() : "";
		if (qqUrl && isPlayableAudioUrl(qqUrl)) {
			song.url = qqUrl;
			song.link = qqInfo.link || song.link || "";
			song.source = "qq";
		} else {
			// ── Step 3: QQ failed → try NetEase ──────────────────
			tip(TEXT.TIP_SOURCE_SWITCH_QQ, "网易云音乐");
			song.source = "netease";
			let neteaseSong = null;
			try {
				const searchWord = song.name || hints.name || "";
				if (searchWord) {
					neteaseSong = await searchNeteaseByWord(searchWord);
			}
			} catch (e) {
				neteaseSong = null;
			}
			if (neteaseSong && neteaseSong.id) {
				song.id = neteaseSong.id;
				song.name = neteaseSong.name || song.name;
				song.singer = neteaseSong.singer || song.singer;
				song.album = neteaseSong.album || song.album;
				song.url = "";
				song.source = "netease";
				song.neteaseId = neteaseSong.id;
				}
		}

		// Fill missing metadata
		if (opts.rec && !song.link) {
			song.link = getNotebookUrl();
		}
		if (!song.name || !song.singer || !song.album || !song.cover) {
			try {
				const meta = await fetchSongMetaByHint(song.id, song.name);
				song.name = song.name || meta.name;
				song.singer = song.singer || meta.singer;
				song.album = song.album || meta.album;
				song.cover = song.cover || meta.cover;
			} catch (e) {
				// best effort
			}
		}

		// Cache the result for QQ/NetEase sources
		if (song.source === "qq" || song.source === "netease") {
			cacheUpsertTuple(song);
			textDbWriteTuple(song).catch(() => {});
		}

		return song;
	}

	async function buildSongById(id, hints, opts) {
		const sid = String(id || "").trim();
		if (!sid) return null;
		const cached = await getTupleFromDbFirst(sid);
		const song = {
			id: sid,
			name: (hints && hints.name) || (cached && cached.name) || "",
			singer: (hints && hints.singer) || (cached && cached.singer) || "",
			album: (hints && hints.album) || (cached && cached.album) || "",
			cover: (hints && hints.cover) || (cached && cached.cover) || "",
			rank: (hints && hints.rank) || (cached && cached.rank) || 1,
			link: (hints && hints.link) || (cached && cached.link) || "",
			url: (hints && hints.url) || (cached && cached.url) || "",
		};

		let info = null;
		if (opts.aud || !song.url || !song.link) {
			info = await queryGetUrlById(song.id, DEFAULT_QUALITY);
		}
		if (opts.rec) {
			song.link = song.link || (info && info.link) || "";
		} else {
			song.link = (info && info.link) || song.link || "";
		}
		if (opts.aud) {
			song.url = (info && info.url) || song.url || "";
		}

		if (!song.name || !song.singer || !song.album || !song.cover) {
			const meta = await fetchSongMetaByHint(song.id, song.name);
			song.name = song.name || meta.name;
			song.singer = song.singer || meta.singer;
			song.album = song.album || meta.album;
			song.cover = song.cover || meta.cover;
		}

		cacheUpsertTuple(song);
		textDbWriteTuple(song);
		return song;
	}

	async function chooseSongByWord(word, choose) {
		const rank = clampInt(choose, 1, 1, 5);
		const data = await apiGet("", {
			word: word,
			page: 1,
			num: Math.max(5, rank),
			quality: DEFAULT_QUALITY,
		});
		const list = extractSongList(data);
		const rows = list.slice(0, Math.max(5, rank)).map((item) => ({
			name: pickName(item) || word,
			id: String(pickId(item) || ""),
			link: String(pickLink(item) || ""),
			singer: pickSingerName(item),
			album: pickAlbumName(item),
			cover: pickCover(item),
		})).filter((r) => r.id);
		if (!rows.length) {
			const item = data.data || data;
			return {
				name: pickName(item) || word,
				id: String(data.id || pickId(item) || ""),
				link: String(data.link || pickLink(data) || ""),
				singer: pickSingerName(item),
				album: pickAlbumName(item),
				cover: pickCover(item) || pickCover(data),
			};
		}
		const picked = rank === 1 ? (selectPreferredSongCandidate(rows.slice(0, 3), word) || rows[0]) : (rows[rank - 1] || rows[0]);
		return {
			name: picked.name || word,
			id: String(picked.id || ""),
			link: String(picked.link || ""),
			singer: picked.singer || "",
			album: picked.album || "",
			cover: picked.cover || "",
		};
	}

	async function resolveSongByWordAndRank(word, rank, opts) {
		const n = clampInt(rank, 1, 1, 30);
		if (n > 5) {
			const rows = await searchSongsByWord(word, n, DEFAULT_QUALITY);
			return { mode: "list", rows: rows };
		}

		let base = null;
		try {
			base = await chooseSongByWord(word, n);
		} catch (e) {
			base = null;
		}

		if ((!base || !base.id) && n >= 1 && n <= 5) {
			const windowRows = await searchSongsByWord(word, 5, DEFAULT_QUALITY);
			if (windowRows.length >= n) {
				const fb = windowRows[n - 1];
				base = {
					name: fb.name,
					id: fb.id,
					link: "",
					singer: fb.singer,
					album: fb.album,
					cover: fb.cover,
					fallback: true,
				};
			}
		}

		if (!base || !base.id) {
			return { mode: "none", song: null };
		}

		const song = await buildSongById(base.id, {
			name: base.name,
			singer: base.singer,
			album: base.album,
			cover: base.cover,
			rank: n,
			link: opts.rec ? (base.link || "") : "",
		}, opts);
		if (!song) return { mode: "none", song: null };
		if (base.fallback) song.fallback = true;
		return { mode: "single", song: song };
	}

	async function fetchAllSingerSongs(mid) {
		const all = [];
		let page = 1;
		while (page <= 200) {
			const data = await apiGet("/singer/songlist", { mid: mid, page: page, num: 60 });
			const batch = extractSongList(data);
			if (!batch.length) break;
			all.push(...batch);
			page += 1;
		}
		return all;
	}

	async function fetchAllDissSongs(dissId) {
		const all = [];
		let page = 1;
		while (page <= 200) {
			const data = await apiGet("/dissinfo", { id: dissId, page: page, num: 60 });
			const batch = extractSongList(data);
			if (!batch.length) break;
			all.push(...batch);
			page += 1;
		}
		return all;
	}

	async function getSingerMidByName(name) {
		const data = await apiGet("/search/singer", { word: name });
		const list = extractSongList(data);
		if (!list.length) return { mid: "", singerName: name };
		const first = list[0] || {};
		return {
			mid: first.mid || first.singermid || first.singerMID || first.singerMid || "",
			singerName: first.singerName || first.singername || first.name || name,
		};
	}

	function buildRowsText(title, rows) {
		const lines = [title];
		rows.forEach((r, idx) => {
			lines.push(`${idx + 1}. ${r.name}-${r.id}`);
		});
		return lines.join("\n");
	}

	async function sendSongFlow(ctx, msg, song, opts) {
		if (!song || !song.id) {
			seal.replyToSender(ctx, msg, TEXT.ERR_NO_RESULT);
			return;
		}
		await sendSongInTwoMessages(ctx, msg, song, opts);
	}

	async function sendSongFlowAutoSwitch(ctx, msg, song, opts, qualitySwitching) {
		if (!song || !song.id) {
			seal.replyToSender(ctx, msg, TEXT.ERR_NO_RESULT);
			return;
		}
		// Build song with auto source switching (sends progress hints to ctx)
		const builtSong = await buildSongWithAutoSwitch(song.id, {
			name: song.name,
			singer: song.singer,
			album: song.album,
			cover: song.cover,
			rank: song.rank || 1,
			link: opts.rec ? (song.link || "") : "",
		}, opts, qualitySwitching, ctx, msg);
		if (!builtSong) {
			seal.replyToSender(ctx, msg, TEXT.ERR_NO_RESULT);
			return;
		}
		await sendSongInTwoMessages(ctx, msg, builtSong, opts);
	}

	const cmdPick = seal.ext.newCmdItemInfo();
	cmdPick.name = "点歌";
	cmdPick.help = TEXT.HELP_PICK;
	cmdPick.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const parsed = parseSongOptions(getArgList(cmdArgs, 1), { rec: true, aud: true, record: 0 });
		const args = parsed.rest.slice();
		if (!args.length) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_PICK);
			return seal.ext.newCmdExecuteResult(true);
		}

		let n = 1;
		if (args.length >= 2 && /^\d+$/.test(args[args.length - 1])) {
			n = clampInt(args.pop(), 1, 1, 30);
		}
		const word = args.join(" ").trim();
		if (!word) {
			seal.replyToSender(ctx, msg, `${TEXT.ERR_EMPTY_SONGNAME}${TEXT.USAGE_PICK}`);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const resolved = await resolveSongByWordAndRank(word, n, parsed);
				if (resolved.mode === "none") {
					seal.replyToSender(ctx, msg, TEXT.ERR_NOT_FOUND);
					return;
				}
				if (resolved.mode === "list") {
					if (!resolved.rows || !resolved.rows.length) {
						seal.replyToSender(ctx, msg, TEXT.ERR_NO_RESULT);
						return;
				}
					seal.replyToSender(ctx, msg, buildRowsText(`关键词：${word}\n结果数量：${resolved.rows.length}\n仅返回id（n > 5）`, resolved.rows));
					return;
				}
				if (resolved.song && resolved.song.fallback) {
					seal.replyToSender(ctx, msg, TEXT.TIP_PICK_FALLBACK);
				}
				// Auto source switching with quality auto-downgrade
				await sendSongFlowAutoSwitch(ctx, msg, resolved.song, parsed, true);
			} catch (e) {
				seal.replyToSender(ctx, msg, `点歌失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	// .qq点歌 — alias for QQ music only (no source switching, original behavior)
	const cmdQQPick = seal.ext.newCmdItemInfo();
	cmdQQPick.name = "qq点歌";
	cmdQQPick.help = "用法：.qq点歌 歌名 [n] [rec=true|false] [aud=true|false] [record=0|1|2]（QQ音乐源，不自动换源）";
	cmdQQPick.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const parsed = parseSongOptions(getArgList(cmdArgs, 1), { rec: true, aud: true, record: 0 });
		const args = parsed.rest.slice();
		if (!args.length) {
			seal.replyToSender(ctx, msg, "用法：.qq点歌 歌名 [n] [rec=true|false] [aud=true|false] [record=0|1|2]");
			return seal.ext.newCmdExecuteResult(true);
		}

		let n = 1;
		if (args.length >= 2 && /^\d+$/.test(args[args.length - 1])) {
			n = clampInt(args.pop(), 1, 1, 30);
		}
		const word = args.join(" ").trim();
		if (!word) {
			seal.replyToSender(ctx, msg, `歌名不能为空。用法：.qq点歌 歌名 [n] [rec=true|false] [aud=true|false] [record=0|1|2]`);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const resolved = await resolveSongByWordAndRank(word, n, parsed);
				if (resolved.mode === "none") {
					seal.replyToSender(ctx, msg, TEXT.ERR_NOT_FOUND);
					return;
				}
				if (resolved.mode === "list") {
					if (!resolved.rows || !resolved.rows.length) {
						seal.replyToSender(ctx, msg, TEXT.ERR_NO_RESULT);
						return;
				}
					seal.replyToSender(ctx, msg, buildRowsText(`关键词：${word}\n结果数量：${resolved.rows.length}\n仅返回id（n > 5）`, resolved.rows));
					return;
				}
				if (resolved.song && resolved.song.fallback) {
					seal.replyToSender(ctx, msg, TEXT.TIP_PICK_FALLBACK);
				}
				// QQ music only with auto quality downgrade (14→7), no source switching
				const song = await buildSongById(resolved.song.id, resolved.song, parsed);
				if (!song || !song.id) {
					seal.replyToSender(ctx, msg, TEXT.ERR_NO_RESULT);
					return;
				}
				// Apply quality auto-downgrade for QQ music URL
				if (parsed.aud) {
					const qqResult = await queryGetUrlWithQualityFallback(song.id, DEFAULT_QUALITY, 7);
					song.url = qqResult.url || song.url || "";
					song.link = qqResult.link || song.link || "";
				}
				await sendSongInTwoMessages(ctx, msg, song, parsed);
			} catch (e) {
				seal.replyToSender(ctx, msg, `qq点歌失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdSearchSong = seal.ext.newCmdItemInfo();
	cmdSearchSong.name = "搜歌";
	cmdSearchSong.help = TEXT.HELP_SEARCH;
	cmdSearchSong.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const args = getArgList(cmdArgs, 1);
		if (!args.length) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_SEARCH);
			return seal.ext.newCmdExecuteResult(true);
		}

		let n = 10;
		if (args.length >= 2 && /^\d+$/.test(args[args.length - 1])) {
			n = clampInt(args.pop(), 10, 1, 30);
		}
		const word = args.join(" ").trim();
		if (!word) {
			seal.replyToSender(ctx, msg, `${TEXT.ERR_EMPTY_SONGNAME}${TEXT.USAGE_SEARCH}`);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const rows = await searchSongsByWord(word, n, DEFAULT_QUALITY);
				if (!rows.length) {
					seal.replyToSender(ctx, msg, TEXT.ERR_NOT_FOUND);
					return;
				}
				rows.forEach((r) => {
					cacheUpsertTuple({ id: r.id, name: r.name, singer: r.singer, album: r.album, cover: r.cover, rank: r.rank });
				});
				seal.replyToSender(ctx, msg, buildRowsText(`关键词：${word}\n结果数量：${rows.length}`, rows));
			} catch (e) {
				seal.replyToSender(ctx, msg, `搜歌失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdGet = seal.ext.newCmdItemInfo();
	cmdGet.name = "查歌";
	cmdGet.help = TEXT.HELP_GET;
	cmdGet.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const rawArgs = getArgList(cmdArgs, 1);
		if (!rawArgs.length) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_GET);
			return seal.ext.newCmdExecuteResult(true);
		}
		const id = String(rawArgs[0] || "").trim();
		const parsed = parseSongOptions(rawArgs.slice(1), { rec: true, aud: true, record: 0 });
		if (!id) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_GET);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const song = await buildSongById(id, { name: "查歌结果", rank: 1 }, parsed);
				await sendSongFlow(ctx, msg, song, parsed);
			} catch (e) {
				seal.replyToSender(ctx, msg, `查歌失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdSinger = seal.ext.newCmdItemInfo();
	cmdSinger.name = "歌手查询";
	cmdSinger.help = TEXT.HELP_SINGER;
	cmdSinger.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const args = getArgList(cmdArgs, 1);
		if (!args.length) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_SINGER);
			return seal.ext.newCmdExecuteResult(true);
		}

		let page = 1;
		let n = 10;
		let mode = 1;
		if (args.length >= 2 && /^\d+$/.test(args[args.length - 1])) page = clampInt(args.pop(), 1, 1, 9999);
		if (args.length >= 2 && /^\d+$/.test(args[args.length - 1])) n = clampInt(args.pop(), 10, 1, 60);
		if (args.length >= 2 && /^\d+$/.test(args[args.length - 1])) mode = clampInt(args.pop(), 1, 1, 2);
		const name = args.join(" ").trim();
		if (!name) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_SINGER);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const singer = await getSingerMidByName(name);
				if (!singer.mid) {
					seal.replyToSender(ctx, msg, "未找到该歌手。");
					return;
				}

				if (mode === 2) {
					const data = await apiGet("/singer/albumlist", { mid: singer.mid, page: page, num: n });
					const list = extractSongList(data);
					if (!list.length) {
						seal.replyToSender(ctx, msg, `歌手名：${singer.singerName}\n专辑列表为空。`);
						return;
				}
					const rows = list.map((item) => ({
						name: pickName(item) || "(未知专辑)",
						id: String(pickId(item) || item.albumMID || item.albummid || ""),
				}));
					seal.replyToSender(ctx, msg, buildRowsText(`歌手名：${singer.singerName}\n专辑列表：`, rows));
					return;
				}

				const data = await apiGet("/singer/songlist", { mid: singer.mid, page: page, num: n });
				const list = extractSongList(data);
				if (!list.length) {
					seal.replyToSender(ctx, msg, `歌手名：${singer.singerName}\n歌曲列表为空。`);
					return;
				}
				const rows = list.map((item) => ({
					name: pickName(item) || "(未知歌曲)",
					id: String(pickId(item) || ""),
				}));
				rows.forEach((r) => cacheUpsertTuple({ id: r.id, name: r.name, singer: singer.singerName }));
				seal.replyToSender(ctx, msg, buildRowsText(`歌手名：${singer.singerName}\n歌曲列表：`, rows));
			} catch (e) {
				seal.replyToSender(ctx, msg, `歌手查询失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdRandomSinger = seal.ext.newCmdItemInfo();
	cmdRandomSinger.name = "随机音乐";
	cmdRandomSinger.help = TEXT.HELP_RANDOM_SINGER;
	cmdRandomSinger.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const parsed = parseSongOptions(getArgList(cmdArgs, 1), { rec: true, aud: true, record: 0 });
		const singerName = parsed.rest.join(" ").trim();
		if (!singerName) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_RANDOM_SINGER);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const singer = await getSingerMidByName(singerName);
				if (!singer.mid) {
					seal.replyToSender(ctx, msg, "未找到该歌手。");
					return;
				}
				const allSongs = await fetchAllSingerSongs(singer.mid);
				const rows = allSongs.map((item) => ({
					name: pickName(item),
					id: String(pickId(item) || ""),
					singer: pickSingerName(item) || singer.singerName,
					album: pickAlbumName(item),
					cover: pickCover(item),
				})).filter((r) => r.name && r.id);
				if (!rows.length) {
					seal.replyToSender(ctx, msg, "该歌手无可用歌曲。");
					return;
				}
				const random = rows[Math.floor(Math.random() * rows.length)];
				const song = await buildSongById(random.id, random, parsed);
				await sendSongFlow(ctx, msg, song, parsed);
			} catch (e) {
				seal.replyToSender(ctx, msg, `随机音乐失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdRandomFav = seal.ext.newCmdItemInfo();
	cmdRandomFav.name = "随机收藏音乐";
	cmdRandomFav.help = TEXT.HELP_RANDOM_FAV;
	cmdRandomFav.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const parsed = parseSongOptions(getArgList(cmdArgs, 1), { rec: true, aud: true, record: 0 });
		const maybeQq = parsed.rest[0] || "";
		const qqid = /^\d{5,}$/.test(String(maybeQq || "")) ? String(maybeQq) : parseSenderQQ(ctx);
		const uidRaw = (ctx.player && ctx.player.userId) || "";
		if (!qqid || String(uidRaw).toLowerCase().includes("wx")) {
			seal.replyToSender(ctx, msg, TEXT.ERR_WX_INVALID);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const info = await apiGet("/info", { uin: qqid });
				const likesongRaw =
					(info.data && (info.data.likesong || info.data.likesongs || info.data.likeSong)) ||
					info.likesong || info.likesongs || "";
				const likesongId = String(
					(info.data && info.data.liked_dissid) ||
					(info.data && info.data.likesong && info.data.likesong.id) ||
					(info.data && info.data.likesongs && info.data.likesongs.id) ||
					(typeof likesongRaw === "object" && likesongRaw ? (likesongRaw.id || likesongRaw.dissid || "") : likesongRaw) ||
					""
				);
				if (!likesongId) {
					seal.replyToSender(ctx, msg, "未找到该QQ账号的likesongs歌单id。\n此命令对微信账号无效");
					return;
				}

				const all = await fetchAllDissSongs(likesongId);
				const rows = all.map((item) => ({
					name: pickName(item),
					id: String(pickId(item) || ""),
					singer: pickSingerName(item),
					album: pickAlbumName(item),
					cover: pickCover(item),
				})).filter((r) => r.name && r.id);
				if (!rows.length) {
					seal.replyToSender(ctx, msg, "收藏歌单为空或读取失败。\n此命令对微信账号无效");
					return;
				}
				const random = rows[Math.floor(Math.random() * rows.length)];
				const song = await buildSongById(random.id, random, parsed);
				await sendSongFlow(ctx, msg, song, parsed);
			} catch (e) {
				seal.replyToSender(ctx, msg, `随机收藏音乐失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdRandomDiss = seal.ext.newCmdItemInfo();
	cmdRandomDiss.name = "随机歌单音乐";
	cmdRandomDiss.help = TEXT.HELP_RANDOM_DISS;
	cmdRandomDiss.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const parsed = parseSongOptions(getArgList(cmdArgs, 1), { rec: true, aud: true, record: 0 });
		const id = parsed.rest[0] || "";
		if (!id) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_RANDOM_DISS);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				const allSongs = await fetchAllDissSongs(id);
				const rows = allSongs.map((item) => ({
					name: pickName(item),
					id: String(pickId(item) || ""),
					singer: pickSingerName(item),
					album: pickAlbumName(item),
					cover: pickCover(item),
				})).filter((r) => r.name && r.id);
				if (!rows.length) {
					seal.replyToSender(ctx, msg, "歌单为空或读取失败。");
					return;
				}
				const random = rows[Math.floor(Math.random() * rows.length)];
				const song = await buildSongById(random.id, random, parsed);
				await sendSongFlow(ctx, msg, song, parsed);
			} catch (e) {
				seal.replyToSender(ctx, msg, `随机歌单音乐失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdLyric = seal.ext.newCmdItemInfo();
	cmdLyric.name = "查询歌词";
	cmdLyric.help = TEXT.HELP_LYRIC;
	cmdLyric.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const args = getArgList(cmdArgs, 1);
		if (!args.length) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_LYRIC);
			return seal.ext.newCmdExecuteResult(true);
		}

		let idInput = "";
		let nameInput = "";
		if (args.length === 1) {
			if (/^\d{5,}$/.test(args[0])) idInput = String(args[0]);
			else nameInput = String(args[0]);
		} else {
			const maybeId = String(args[args.length - 1] || "");
			if (/^\d{5,}$/.test(maybeId)) {
				idInput = maybeId;
				nameInput = args.slice(0, -1).join(" ").trim();
			} else {
				nameInput = args.join(" ").trim();
			}
		}

		if (!idInput && !nameInput) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_LYRIC);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				let id = idInput;
				let songName = nameInput || "(按id查询)";
				let usedSearchFallback = false;

				if (!id) {
					let base = null;
					let firstSinger = "";
					try {
						const topRows = await searchSongsByWord(songName, 3, DEFAULT_QUALITY);
						if (topRows.length) {
							firstSinger = String(topRows[0].singer || "");
							base = selectPreferredSongCandidate(topRows, songName) || topRows[0];
					}
				} catch (e) {
						base = null;
				}
					if ((!base || !base.id) && songName) {
						const swapped = buildLyricSwapKeyword(songName, firstSinger);
						if (swapped) {
							try {
								const swappedRows = await searchSongsByWord(swapped, 3, DEFAULT_QUALITY);
								if (swappedRows.length) {
									base = selectPreferredSongCandidate(swappedRows, swapped) || swappedRows[0];
									songName = swapped;
									usedSearchFallback = true;
							}
						} catch (e) {
								// ignore
						}
					}
				}
					id = String((base && base.id) || "");
					if (!id) {
						const fallbackRows = await searchSongsByWord(songName, 1, DEFAULT_QUALITY);
						if (fallbackRows.length) {
							id = String(fallbackRows[0].id || "");
							songName = fallbackRows[0].name || songName;
							usedSearchFallback = !!id;
					}
				}
					if (!id) {
						seal.replyToSender(ctx, msg, "未找到该歌曲，无法查询歌词。");
						return;
				}
				}
				const lyric = await getLyricById(id);
				const lines = [`歌名：${songName}`, `id: ${id}`];
				if (usedSearchFallback) lines.push(TEXT.TIP_LYRIC_FALLBACK);
				lines.push("歌词：");
				lines.push(lyric || "未查询到歌词。");
				seal.replyToSender(ctx, msg, lines.join("\n"));
			} catch (e) {
				seal.replyToSender(ctx, msg, `查询歌词失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};

	const cmdNetease = seal.ext.newCmdItemInfo();
	cmdNetease.name = "网易云";
	cmdNetease.help = TEXT.HELP_NETEASE;
	cmdNetease.solve = (ctx, msg, cmdArgs) => {
		const first = cmdArgs.getArgN(1);
		if (first === "help") {
			const ret = seal.ext.newCmdExecuteResult(true);
			ret.showHelp = true;
			return ret;
		}

		const args = getArgList(cmdArgs, 1);
		if (!args.length) {
			seal.replyToSender(ctx, msg, TEXT.USAGE_NETEASE);
			return seal.ext.newCmdExecuteResult(true);
		}

		(async () => {
			try {
				let song = null;
				if (args.length === 1 && /^\d{5,}$/.test(args[0])) {
					song = { id: String(args[0]), name: "(按id点歌)", singer: "", album: "" };
				} else {
					const word = args.join(" ").trim();
					if (!word) {
						seal.replyToSender(ctx, msg, TEXT.USAGE_NETEASE);
						return;
				}
					song = await searchNeteaseByWord(word);
				}

				if (!song || !song.id) {
					seal.replyToSender(ctx, msg, TEXT.ERR_NOT_FOUND);
					return;
				}

				await sendNeteaseInTwoMessages(ctx, msg, song);
			} catch (e) {
				seal.replyToSender(ctx, msg, `网易云点歌失败：${e.message || e}`);
			}
		})();

		return seal.ext.newCmdExecuteResult(true);
	};


	ext.cmdMap["点歌"] = cmdPick;
	ext.cmdMap["qq点歌"] = cmdQQPick;
	ext.cmdMap["搜歌"] = cmdSearchSong;
	ext.cmdMap["查歌"] = cmdGet;
	ext.cmdMap["歌手查询"] = cmdSinger;
	ext.cmdMap["随机音乐"] = cmdRandomSinger;
	ext.cmdMap["随机收藏音乐"] = cmdRandomFav;
	ext.cmdMap["随机歌单音乐"] = cmdRandomDiss;
	ext.cmdMap["查询歌词"] = cmdLyric;
	ext.cmdMap["网易云"] = cmdNetease;
}
